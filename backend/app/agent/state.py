"""LangGraph 图的共享状态与通用工具。

``steps`` 用 ``operator.add`` 累加：每个节点只往自己的返回值里塞新增的 step，
LangGraph 负责拼接，这样并行/多轮（interrupt 恢复）都不会覆盖历史。
"""
from __future__ import annotations

import operator
import time
from datetime import date, datetime, timedelta
from typing import Annotated, Any, TypedDict

from backend.app.constants import (
    ACTOR_ANALYSIS,
    ACTOR_REPORT,
    ACTOR_SCHEDULER,
    ACTOR_SYSTEM,
    RISK_LOW,
    ROUTE_UNDETERMINED,
    STATUS_PENDING,
    STATUS_SUCCESS,
)

# ------------------------------------------------------------------ 时间格式（契约 §0）
DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def fmt_dt(value: datetime | None) -> str:
    """datetime → ``2026-10-19 11:38:26``（契约要求精确到秒的本地时间字符串）。"""
    if value is None:
        return ""
    return value.strftime(DT_FORMAT)


def fmt_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime(DT_FORMAT)


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in (DT_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def ms_between(start: float, end: float | None = None) -> int:
    return max(0, int(round(((end if end is not None else time.monotonic()) - start) * 1000)))


def day_range(days: int = 14, today: date | None = None) -> list[str]:
    """近 N 天日期字符串（含今天，升序），用于 daily_runs 固定点数。"""
    anchor = today or date.today()
    return [fmt_date(anchor - timedelta(days=offset)) for offset in range(days - 1, -1, -1)]


# ------------------------------------------------------------------ 状态
class AgentState(TypedDict, total=False):
    """图状态。除 steps 外都是「最后写入者胜」。"""

    # 标识
    run_id: int
    question: str
    session_id: str | None

    # 路由
    route: str
    product_names: list[str]
    product_name: str | None
    product_id: int | None

    # 分析
    sql: str | None
    sql_result: dict[str, Any]
    analysis: dict[str, Any]
    mcp_transport: str
    analysis_evidence: list[dict[str, Any]]

    # 语义
    semantic_items: list[dict[str, Any]]
    semantic_query: str | None

    # 证据
    evidence: list[dict[str, Any]]
    evidence_source: str          # review | product | mixed | none

    # 决策
    risk: str
    risk_reason: str
    bad_review_count: int
    avg_rating: float | None
    actions: list[dict[str, Any]]
    executed: list[dict[str, Any]]
    skipped: list[dict[str, Any]]

    # 输出
    answer: str
    status: str
    error: str | None
    awaiting_confirm: bool
    confirm_decision: str | None
    llm_failed: bool

    # 过程
    steps: Annotated[list[dict[str, Any]], operator.add]
    started_at: float


def initial_state(run_id: int, question: str, session_id: str | None = None) -> AgentState:
    return {
        "run_id": int(run_id),
        "question": question or "",
        "session_id": session_id,
        "route": ROUTE_UNDETERMINED,
        "product_names": [],
        "product_name": None,
        "product_id": None,
        "sql": None,
        "sql_result": {},
        "analysis": {},
        "mcp_transport": "unknown",
        "analysis_evidence": [],
        "semantic_items": [],
        "semantic_query": None,
        "evidence": [],
        "evidence_source": "none",
        "risk": RISK_LOW,
        "risk_reason": "",
        "bad_review_count": 0,
        "avg_rating": None,
        "actions": [],
        "executed": [],
        "skipped": [],
        "answer": "",
        "status": STATUS_PENDING,
        "error": None,
        "awaiting_confirm": False,
        "confirm_decision": None,
        "llm_failed": False,
        "steps": [],
        "started_at": time.monotonic(),
    }


def make_step(seq: int, stage: str, detail: str, actor: str = ACTOR_SYSTEM,
              duration_ms: int = 0) -> dict[str, Any]:
    """构造一条 step 记录（字段与 ``run_steps`` 表、SSE step 事件完全一致）。"""
    return {
        "seq": int(seq),
        "stage": stage,
        "actor": actor,
        "detail": detail or "",
        "duration_ms": max(0, int(duration_ms or 0)),
    }


def next_seq(state: AgentState) -> int:
    return len(state.get("steps") or []) + 1


__all__ = [
    "DT_FORMAT", "fmt_dt", "fmt_date", "now_str", "parse_dt", "ms_between", "day_range",
    "AgentState", "initial_state", "make_step", "next_seq",
    "ACTOR_SYSTEM", "ACTOR_SCHEDULER", "ACTOR_ANALYSIS", "ACTOR_REPORT",
    "STATUS_SUCCESS", "STATUS_PENDING", "RISK_LOW", "ROUTE_UNDETERMINED",
]
