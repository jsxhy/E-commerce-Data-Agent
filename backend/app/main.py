"""FastAPI 应用装配入口。

装配顺序（顺序很重要）：

1. ``/api/*`` 业务路由（各 router 自带 ``/api`` 前缀，这里不再加 prefix）
2. ``/mcp`` —— MCP 服务（streamable-http），必须在静态托管之前 mount
3. ``/assets`` 静态资源 + SPA catch-all（前端 Rollup 产物）

lifespan 内依次做四件事：

* ``init_db()`` —— 建表（幂等）
* 种子数据 —— 库为空时自动灌 ``seed_all()``，保证首次启动即可看到完整演示数据
* 向量入库 —— Chroma 集合为空时把 20 商品 + 46 评价灌进去（幂等）
* ``mcp_lifespan()`` —— 启动 MCP session manager（缺了它客户端会拿到 Session terminated）

任何一步失败都不阻断启动：降级为「能力缺失」并在 ``/api/health`` 里如实报告，
避免一个向量库问题把整个后台打死。
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.db import get_engine, init_db
from backend.config import get_config, resolve_path

logger = logging.getLogger("ecom.app")

VERSION = "1.0.0"

#: 前端 SPA 的入口文件名
INDEX_FILE = "index.html"

#: 需要注册的业务路由模块（顺序即 OpenAPI 分组顺序）
ROUTER_MODULES = (
    "auth", "dashboard", "products", "orders", "reviews", "users",
    "chat", "runs", "reports", "observability", "semantic",
)


# ------------------------------------------------------------------ 启动钩子

def _bootstrap_database() -> None:
    init_db(drop=False)
    from sqlalchemy import func, select

    from backend.app.db import SessionLocal
    from backend.app.models import User

    db = SessionLocal()
    try:
        user_count = db.execute(select(func.count(User.id))).scalar() or 0
    finally:
        db.close()
    if user_count:
        logger.info("数据库已有 %s 个用户，跳过种子数据", user_count)
        return

    from backend.app.seed import seed_all

    summary = seed_all(drop=False)
    logger.info("种子数据已写入：%s", summary)


def _bootstrap_vectors() -> None:
    """把商品与评价灌进 Chroma；集合已有数据就跳过（幂等）。"""
    try:
        from backend.app.rag.ingest import ingest_all
        from backend.app.rag.store import get_store
    except Exception as exc:  # pragma: no cover - 依赖缺失
        logger.warning("向量模块不可用，跳过入库：%s", exc)
        return

    store = get_store()
    try:
        existing = store.count()
    except Exception as exc:  # pragma: no cover - 模型/磁盘异常
        logger.warning("向量库不可用，跳过入库：%s", exc)
        return
    if existing:
        logger.info("向量库已有 %s 条文档，跳过入库", existing)
        return

    from backend.app.db import SessionLocal

    db = SessionLocal()
    try:
        summary = ingest_all(db, store=store)
    finally:
        db.close()
    logger.info("向量入库完成：%s", summary)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """应用生命周期：建库 → 种子 → 向量入库 → 启动 MCP session manager。"""
    import os

    if os.environ.get("ECOM_SKIP_BOOTSTRAP") != "1":
        _bootstrap_database()
        _bootstrap_vectors()
    else:
        init_db(drop=False)
        logger.info("ECOM_SKIP_BOOTSTRAP=1，跳过种子数据与向量入库")

    mcp_lifespan = None
    try:
        from backend.app.mcp_server.server import mcp_lifespan as _mcp_lifespan

        mcp_lifespan = _mcp_lifespan
    except Exception as exc:  # pragma: no cover - MCP 依赖缺失时降级
        logger.warning("MCP 模块不可用，工具调用将退化为进程内直调：%s", exc)

    if mcp_lifespan is None:
        yield
        return
    async with mcp_lifespan():
        yield


# ------------------------------------------------------------------ 路由装配

def _register_routers(app: FastAPI) -> list[str]:
    """注册全部业务路由；单个模块导入失败不阻断其他模块（启动期可观测）。"""
    import importlib

    registered: list[str] = []
    for name in ROUTER_MODULES:
        try:
            module = importlib.import_module(f"backend.app.routers.{name}")
        except Exception as exc:  # pragma: no cover - 模块缺失/语法错误
            logger.error("路由模块 %s 加载失败：%s", name, exc)
            continue
        router = getattr(module, "router", None)
        if router is None:
            logger.error("路由模块 %s 没有导出 router", name)
            continue
        app.include_router(router)
        registered.append(name)
    return registered


def _register_error_handlers(app: FastAPI) -> None:
    """统一错误包裹体：{code, message, data}。"""

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_request: Request, exc: HTTPException):
        detail = exc.detail
        message = detail if isinstance(detail, str) else str(detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": message, "data": None},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "请求体"
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": f"参数校验失败：{field}",
                     "data": first.get("msg")},
        )


# ------------------------------------------------------------------ 静态托管

def _mount_static(app: FastAPI) -> None:
    """托管前端产物；目录不存在时只注册一个提示用的根路由。"""
    static_dir = resolve_path(get_config()["app"]["static_dir"])
    index_file = static_dir / INDEX_FILE

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/", include_in_schema=False)
    async def _index():
        if index_file.is_file():
            return FileResponse(str(index_file))
        return JSONResponse(
            status_code=503,
            content={"code": 503,
                     "message": "前端产物不存在，请先执行 `pnpm run build`（见 README）",
                     "data": {"expected": str(index_file)}},
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """静态资源直接返回，其余路径交给 SPA（history 路由）。"""
        if full_path.startswith(("api/", "mcp")):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = (static_dir / full_path).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="资源不存在") from None
        if candidate.is_file():
            return FileResponse(str(candidate))
        if index_file.is_file():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="资源不存在")


# ------------------------------------------------------------------ 健康检查

def _db_health() -> str:
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # pragma: no cover - 磁盘/权限异常
        logger.warning("数据库健康检查失败：%s", exc)
        return "error"


def _vector_health() -> str:
    try:
        from backend.app.rag.store import get_store

        return "ok" if get_store().count() > 0 else "empty"
    except Exception as exc:  # pragma: no cover - 模型/磁盘异常
        logger.warning("向量库健康检查失败：%s", exc)
        return "unavailable"


# ------------------------------------------------------------------ 应用工厂

def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(
        title=cfg["app"]["name"],
        version=VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    _register_error_handlers(app)
    app.state.routers = _register_routers(app)

    # MCP 必须在静态托管之前 mount，否则 /mcp 会被 catch-all 吃掉
    try:
        from backend.app.mcp_server.server import get_mcp_app, mount_path

        app.mount(mount_path(), get_mcp_app())
        app.state.mcp_mounted = True
    except Exception as exc:  # pragma: no cover - MCP 依赖缺失
        logger.warning("MCP 挂载失败：%s", exc)
        app.state.mcp_mounted = False

    @app.get("/api/health", tags=["health"])
    def health():
        llm_configured = bool(cfg["llm"].get("api_key"))
        return {
            "code": 0,
            "message": "ok",
            "data": {
                "status": "ok",
                "db": _db_health(),
                "vector": _vector_health(),
                "llm": "configured" if llm_configured else "missing",
                "version": VERSION,
            },
        }

    _mount_static(app)
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - 手工启动入口
    import uvicorn

    cfg = get_config()
    uvicorn.run(app, host=cfg["app"]["host"], port=int(cfg["app"]["port"]))
