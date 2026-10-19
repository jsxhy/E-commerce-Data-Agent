"""pytest 全局夹具：独立临时 SQLite + 独立临时向量库 + 种子数据 + TestClient。

设计要点：
1. 在任何 backend 模块被导入之前就把 `ECOM_DB_URL` 指向临时目录，避免污染开发库；
2. `get_config()` 带 lru_cache，因此设置环境变量后必须 `reset_config_cache()`；
3. 引擎也是模块级缓存，切换数据库后必须 `reset_engine()`；
4. 种子数据由 `backend.app.seed.seed_all()` 落库（Lead 负责该模块）；
5. 临时目录**不能**用 `tempfile.mkdtemp()`：本机沙箱下 mkdtemp 建的目录不可写
   （sqlite 会报 "unable to open database file"），必须用 mkdir 建固定子目录。
6. **向量库同样必须隔离**：`ECOM_RAG_PERSIST_DIR` 指向临时目录，并在播种后
   灌入 66 条文档。否则依赖语义检索的用例（route=semantic 的问题拿不到证据）
   会隐式依赖开发目录 `backend/data/chroma` 里的数据，既不可重复也污染开发库。
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import tempfile

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 环境准备
# 必须在 import backend.* 之前完成：backend.config.load_config() 会读这些环境变量。
_TMP_DIR = pathlib.Path(tempfile.gettempdir()) / "ecom-test-db"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
_DB_FILE = _TMP_DIR / "test.db"
_CHROMA_DIR = _TMP_DIR / "chroma"
# 清掉上一次会话残留（WAL 模式会留下 -wal / -shm）
for _stale in _TMP_DIR.glob("test.db*"):
    try:
        _stale.unlink()
    except OSError:
        pass
shutil.rmtree(_CHROMA_DIR, ignore_errors=True)

os.environ["ECOM_DB_URL"] = "sqlite:///" + _DB_FILE.as_posix()
os.environ["ECOM_RAG_PERSIST_DIR"] = _CHROMA_DIR.as_posix()
os.environ.setdefault("ECOM_JWT_SECRET", "test-only-secret")
os.environ.setdefault("ECOM_LLM_API_KEY", "")

import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.config import reset_config_cache  # noqa: E402

reset_config_cache()

from backend.app import db as app_db  # noqa: E402

# 契约 §0：统一响应包裹；未登录固定 401 + 该文案
UNAUTHORIZED_MESSAGE = "未登录或登录已过期"


# ------------------------------------------------------------------ 应用装配
def _fallback_app() -> FastAPI:
    """main.py 尚不可用时的兜底应用（只挂本模块负责的路由）。

    正常路径永远是 `backend.app.main:app`；兜底只为让基础模块的测试能独立跑通。
    """
    from backend.app.routers import auth, dashboard, orders, products, reviews, users

    app = FastAPI(title="backend-core fallback")

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(_request, exc: HTTPException):  # noqa: ANN001
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": exc.detail, "data": None},
        )

    for module in (auth, dashboard, products, orders, reviews, users):
        app.include_router(module.router)
    return app


def build_app() -> FastAPI:
    """优先使用 Lead 装配的正式应用；不可用时退回兜底应用。"""
    try:
        from backend.app.main import app as main_app  # noqa: PLC0415
    except Exception:  # pragma: no cover - 仅在 main.py 未就绪时命中
        return _fallback_app()
    return main_app


# ------------------------------------------------------------------ 数据库
@pytest.fixture(scope="session", autouse=True)
def prepared_db():
    """整个测试会话共用一份已建表、灌好种子数据与向量文档的临时库。"""
    app_db.reset_engine()
    app_db.init_db(drop=True)

    from backend.app.seed import seed_all  # 延迟导入：Lead 并行编写

    seed_all()
    _ingest_test_vectors()
    yield
    app_db.reset_engine()
    from backend.app.rag.store import reset_store

    reset_store()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


def _ingest_test_vectors() -> None:
    """把种子数据灌进**测试自己的**向量库（66 条文档）。

    不做这一步时，route=semantic 的问题在测试里拿不到任何证据，会走「证据不足 →
    不规划动作」，依赖 interrupt 的用例就会失败；而如果共享开发目录的向量库，
    用例又会随开发数据漂移。所以这里显式自建。
    """
    from backend.app.rag.store import EmbeddingUnavailable, get_store, reset_store

    reset_store()
    try:
        store = get_store()
        with app_db.SessionLocal() as db:
            from backend.app.rag.ingest import ingest_all

            result = ingest_all(db, store=store, reset=True)
        logger.info("测试向量库就绪：%s", result)
    except EmbeddingUnavailable as exc:  # 模型缺失时降级，不阻断非语义用例
        logger.warning("测试向量库不可用（%s），依赖语义检索的用例可能失败", exc)


@pytest.fixture(scope="session")
def app(prepared_db) -> FastAPI:  # noqa: ARG001
    return build_app()


@pytest.fixture(scope="session")
def client(app) -> TestClient:  # noqa: ARG001
    # 不用 with 上下文，避免触发 main.py 的 lifespan（那会重复建表/灌种子）
    return TestClient(app)


@pytest.fixture(scope="session")
def token(client: TestClient) -> str:
    resp = client.post("/api/auth/login",
                       json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


@pytest.fixture(scope="session")
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def session():
    """直连数据库的会话（断言种子数据用）。"""
    db = app_db.SessionLocal()
    try:
        yield db
    finally:
        db.close()
