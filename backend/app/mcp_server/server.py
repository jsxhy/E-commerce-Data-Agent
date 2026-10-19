"""FastMCP 服务：3 个只读工具，streamable-HTTP 挂载到 FastAPI 的 ``/mcp``。

**为什么必须是 streamable-HTTP 而不是 stdio**：本沙箱下 asyncio 创建子进程管道会报
``PermissionError: [WinError 5]``，stdio 传输必挂。streamable-HTTP 走的是同进程
ASGI 挂载，没有子进程。

接入方式（main.py 里两件事缺一不可，否则客户端报 ``Session terminated`` 或 307）：

    from backend.app.mcp_server.server import get_mcp_app, mcp_lifespan

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_lifespan():        # 1) 启动 session_manager
            yield
    app.mount(get_config()["mcp"]["mount_path"], get_mcp_app())   # 2) 挂载

客户端连 ``http://127.0.0.1:<app.port>/mcp/``（端口从 ``get_config()["app"]["port"]`` 取）。

**降级**：``call_tool()`` 先试 MCP over HTTP，连不上（测试环境没挂载、或独立进程未启动）
就直接调同一批 Python 函数。工具实现只有一份，所以两条路径结果完全一致。
"""
from __future__ import annotations

import contextlib
import json
import logging
from typing import Any, Callable

from backend.config import get_config

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "ecom-tools"
MCP_CLIENT_TIMEOUT = 20.0

# 工具名常量（graph 与测试都引用，避免写错字符串）
TOOL_QUERY_MYSQL = "query_mysql"
TOOL_SEARCH_REVIEWS = "search_reviews"
TOOL_LIST_RECENT_RUNS = "list_recent_runs"
ALL_TOOLS: tuple[str, ...] = (TOOL_QUERY_MYSQL, TOOL_SEARCH_REVIEWS, TOOL_LIST_RECENT_RUNS)


def _db_session():
    """工具内部自建会话（不依赖 FastAPI 请求上下文）。"""
    from backend.app.db import SessionLocal

    return SessionLocal()


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


# ------------------------------------------------------------------ 工具实现（唯一一份）
def tool_query_mysql(sql: str, max_rows: int | None = None) -> dict[str, Any]:
    """校验并执行只读 SQL。"""
    from backend.app.agent.sql_guard import execute_sql, validate_sql

    cfg = get_config()["agent"]
    ok, normalized = validate_sql(sql, max_rows=max_rows)
    if not ok:
        return {"ok": False, "error": normalized}
    db = _db_session()
    try:
        result = execute_sql(db, normalized, timeout_seconds=cfg.get("sql_timeout_seconds"),
                             max_rows=max_rows)
    finally:
        db.close()
    if result.get("ok"):
        return {"ok": True, "columns": result["columns"], "rows": result["rows"],
                "row_count": result["row_count"]}
    return {"ok": False, "error": result.get("error", "执行失败")}


def tool_search_reviews(query: str, top_k: int = 5) -> dict[str, Any]:
    """语义检索评价/商品。模型不可用时降级为空结果。"""
    from backend.app.rag.embedding import EmbeddingUnavailable
    from backend.app.rag.store import EMBEDDING_MODE_UNAVAILABLE, get_store

    text = (query or "").strip()
    if not text:
        return {"ok": False, "error": "请输入检索内容"}
    store = get_store()
    try:
        items = store.search(text, top_k=top_k)
    except EmbeddingUnavailable as exc:
        return {"ok": False, "error": f"向量检索不可用：{exc}",
                "embedding_mode": EMBEDDING_MODE_UNAVAILABLE, "items": [], "hit_count": 0}
    return {"ok": True, "query": text, "hit_count": len(items), "items": items,
            "embedding_mode": store.embedding_mode,
            "collection_size": store.count()}


def tool_list_recent_runs(limit: int = 10) -> dict[str, Any]:
    """最近运行记录（只读）。"""
    from backend.app.models import AgentRun

    count = max(1, min(int(limit or 10), 100))
    db = _db_session()
    try:
        rows = (db.query(AgentRun).order_by(AgentRun.id.desc()).limit(count).all())
        items = [{
            "id": int(run.id),
            "question": run.question or "",
            "route": run.route or "",
            "status": run.status or "",
            "duration_ms": int(run.duration_ms or 0),
            "step_count": int(run.step_count or 0),
            "created_at": run.created_at.strftime("%Y-%m-%d %H:%M:%S") if run.created_at else "",
        } for run in rows]
    finally:
        db.close()
    return {"ok": True, "items": items, "count": len(items)}


# ------------------------------------------------------------------ FastMCP 服务
def _build_server():
    from mcp.server.fastmcp import FastMCP

    # streamable_http_path="/" → 挂到 /mcp 后完整 URL 就是 /mcp/
    server = FastMCP(MCP_SERVER_NAME, streamable_http_path="/")

    @server.tool(name=TOOL_QUERY_MYSQL)
    def query_mysql(sql: str) -> str:
        """执行只读 SQL 查询（仅 SELECT，自动限行）。返回 JSON 字符串。"""
        try:
            return _json(tool_query_mysql(sql))
        except Exception as exc:  # noqa: BLE001 - 工具层绝不抛，返回错误 JSON
            logger.exception("query_mysql 失败")
            return _json({"ok": False, "error": f"工具执行失败：{exc}"})

    @server.tool(name=TOOL_SEARCH_REVIEWS)
    def search_reviews(query: str, top_k: int = 5) -> str:
        """语义检索商品评价（中文向量检索）。返回 JSON 字符串。"""
        try:
            return _json(tool_search_reviews(query, top_k))
        except Exception as exc:  # noqa: BLE001
            logger.exception("search_reviews 失败")
            return _json({"ok": False, "error": f"工具执行失败：{exc}", "items": [], "hit_count": 0})

    @server.tool(name=TOOL_LIST_RECENT_RUNS)
    def list_recent_runs(limit: int = 10) -> str:
        """列出最近的 Agent 运行记录。返回 JSON 字符串。"""
        try:
            return _json(tool_list_recent_runs(limit))
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_recent_runs 失败")
            return _json({"ok": False, "error": f"工具执行失败：{exc}", "items": []})

    return server


# import 时不建表、不连库、不加载模型；只创建 FastMCP 实例（纯内存对象）
mcp = _build_server()

_mcp_app = None


def get_mcp_app():
    """进程内缓存的 streamable-HTTP ASGI 应用。"""
    global _mcp_app
    if _mcp_app is None:
        _mcp_app = mcp.streamable_http_app()
    return _mcp_app


def _reset_session_manager_started() -> bool:
    """允许同一进程内重复启停 app（热重载、测试里多次启动 uvicorn）。

    mcp 1.30 的 ``StreamableHTTPSessionManager.run()`` 有一个 ``_has_started`` 闸门，
    第二次 ``run()`` 直接抛::

        RuntimeError: StreamableHTTPSessionManager .run() can only be called once
        per instance. Create a new instance if you need to run again.

    但 ``run()`` 在退出时已经把自己收拾干净了（取消 task group、``_task_group=None``、
    清空 ``_server_instances`` / ``_session_owners``），所以真正拦住重启的只有那个
    布尔标志。**不能**重建 manager：mount 保存的是 ``StreamableHTTPASGIApp`` 引用，
    换 manager 会让已挂载的路由指向旧实例（实测报 "Task group is not initialized"）。

    因此这里只把标志复位，让同一个 manager 重新 ``run()``。
    """
    manager = mcp.session_manager
    if not getattr(manager, "_has_started", False):
        return False
    manager._has_started = False  # noqa: SLF001 - 官方未提供重启入口
    logger.info("MCP session manager 复位重启标志（同进程重复启动）")
    return True


@contextlib.asynccontextmanager
async def mcp_lifespan():
    """FastAPI lifespan 里包一层：启动/关闭 MCP session_manager。"""
    _reset_session_manager_started()
    async with mcp.session_manager.run():
        yield


def mount_path() -> str:
    return str(get_config()["mcp"].get("mount_path") or "/mcp")


def base_url() -> str:
    """按运行时配置拼 MCP 端点（端口绝不写死）。"""
    cfg = get_config()
    app_cfg = cfg.get("app") or {}
    host = str(app_cfg.get("host") or "127.0.0.1")
    port = int(app_cfg.get("port") or 8000)
    path = mount_path().rstrip("/") or "/mcp"
    return f"http://{host}:{port}{path}/"


# ------------------------------------------------------------------ 客户端（带降级）
_INPROCESS: dict[str, Callable[..., dict[str, Any]]] = {
    TOOL_QUERY_MYSQL: lambda sql="", **kw: tool_query_mysql(sql, **kw),
    TOOL_SEARCH_REVIEWS: lambda query="", top_k=5, **kw: tool_search_reviews(query, top_k),
    TOOL_LIST_RECENT_RUNS: lambda limit=10, **kw: tool_list_recent_runs(limit),
}


def call_tool_inprocess(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """进程内直调（MCP 不可用时的降级路径，与工具实现同一份代码）。"""
    handler = _INPROCESS.get(name)
    if handler is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    try:
        return handler(**(arguments or {}))
    except TypeError as exc:
        return {"ok": False, "error": f"参数错误：{exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("进程内调用工具 %s 失败", name)
        return {"ok": False, "error": f"工具执行失败：{exc}"}


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """把 MCP CallToolResult 转成 dict。"""
    content = getattr(result, "content", None) or []
    text_parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(str(text))
    raw = "\n".join(text_parts).strip()
    if not raw:
        return {"ok": False, "error": "MCP 工具返回为空"}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"MCP 工具返回非 JSON：{raw[:200]}"}
    return parsed if isinstance(parsed, dict) else {"ok": True, "data": parsed}


async def call_tool(name: str, arguments: dict[str, Any] | None = None,
                    timeout: float = MCP_CLIENT_TIMEOUT,
                    prefer_http: bool = True) -> dict[str, Any]:
    """调 MCP 工具。先走 streamable-HTTP，失败自动降级为进程内直调。

    返回的 dict 额外带 ``transport``: ``"mcp"`` / ``"inprocess"``，供证据来源与调试使用。
    """
    args = dict(arguments or {})
    if not prefer_http:
        payload = call_tool_inprocess(name, args)
        payload.setdefault("transport", "inprocess")
        return payload

    url = base_url()
    try:
        import asyncio

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def _call() -> dict[str, Any]:
            async with streamablehttp_client(url, timeout=timeout) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, args)
                    return _parse_tool_result(result)

        payload = await asyncio.wait_for(_call(), timeout=timeout)
        if not payload.get("ok") and payload.get("error") == "MCP 工具返回为空":
            raise RuntimeError("MCP 返回为空")
        payload["transport"] = "mcp"
        return payload
    except Exception as exc:  # noqa: BLE001 - 任何 MCP 侧问题都降级
        logger.info("MCP 调用 %s 失败（%s），降级为进程内直调", name, exc)
        payload = call_tool_inprocess(name, args)
        payload.setdefault("transport", "inprocess")
        payload.setdefault("mcp_error", str(exc))
        return payload


async def health() -> dict[str, Any]:
    """给 /api/health 用：MCP 是否可达（不抛异常）。"""
    payload = await call_tool(TOOL_LIST_RECENT_RUNS, {"limit": 1}, timeout=3.0)
    return {"status": "ok" if payload.get("ok") else "unavailable",
            "transport": payload.get("transport", "unknown"),
            "url": base_url()}


__all__ = [
    "MCP_SERVER_NAME", "TOOL_QUERY_MYSQL", "TOOL_SEARCH_REVIEWS", "TOOL_LIST_RECENT_RUNS",
    "ALL_TOOLS", "mcp", "get_mcp_app", "mcp_lifespan", "mount_path", "base_url",
    "call_tool", "call_tool_inprocess", "health",
    "tool_query_mysql", "tool_search_reviews", "tool_list_recent_runs",
]
