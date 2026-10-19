"""观测看板路由。

契约：docs/api-contract.md §7
- GET /api/observability/overview?only_mine=false

8 个 KPI 全部从 ``agent_runs`` 实时统计，不做缓存：
- 成功率 = success / (success + failed + cancelled)
- 平均耗时只算 success（秒，1 位小数）
- 点赞率 = up / (up + down)
``only_mine`` 仅为契约兼容保留：``agent_runs`` 没有 user_id 字段，无法按人过滤，
接受参数但不改变结果。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.app import constants
from backend.app.deps import CurrentUser, DbSession
from backend.app.models import AgentRun, RunStep
from backend.app.schemas import ok
from backend.app.utils import fmt_date, fmt_dt

router = APIRouter(prefix="/api/observability", tags=["observability"])

DAILY_DAYS = 14
STAGE_TOP_N = 8

# KPI 文案（契约 §7 逐字）
KPI_LABELS: dict[str, tuple[str, str | None]] = {
    "total_runs": ("含进行中与已取消", None),
    "success_runs": ("正常跑完", None),
    "failed_runs": ("异常终止", None),
    "cancelled_runs": ("用户放弃或中断", None),
    "unfinished_runs": ("进行中/待确认", None),
    "success_rate": ("已结束的运行里成功的比例", "%"),
    "avg_duration": ("仅统计成功运行", "s"),
    "like_rate": ("赞1 / 踩0", "%"),
}


def _kpi(name: str, value: float | int) -> dict:
    label, unit = KPI_LABELS[name]
    item: dict = {"value": value, "label": label}
    if unit:
        item["unit"] = unit
    return item


def _ratio(num: float, den: float) -> float:
    """百分比，保留 1 位；分母为 0 返回 0.0。"""
    if not den:
        return 0.0
    return round(num / den * 100, 1)


def _status_counts(db) -> dict[str, int]:  # noqa: ANN001
    rows = db.execute(
        select(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status)
    ).all()
    return {str(status): int(count or 0) for status, count in rows}


def _avg_success_duration(db) -> float:  # noqa: ANN001
    value = db.execute(
        select(func.avg(AgentRun.duration_ms)).where(AgentRun.status == constants.STATUS_SUCCESS)
    ).scalar()
    return round(float(value or 0) / 1000, 1)


def _feedback_counts(db) -> tuple[int, int]:  # noqa: ANN001
    rows = db.execute(
        select(AgentRun.feedback, func.count(AgentRun.id))
        .where(AgentRun.feedback.is_not(None))
        .group_by(AgentRun.feedback)
    ).all()
    counts = {str(fb): int(cnt or 0) for fb, cnt in rows}
    return counts.get("up", 0), counts.get("down", 0)


def _route_dist(db) -> list[dict]:  # noqa: ANN001
    rows = db.execute(
        select(AgentRun.route, func.count(AgentRun.id)).group_by(AgentRun.route)
    ).all()
    counts = {str(route): int(cnt or 0) for route, cnt in rows}
    return [
        {"route": route,
         "name": constants.ROUTE_LABELS.get(route, route),
         "count": counts.get(route, 0)}
        for route in (constants.ROUTE_STRUCTURED,
                      constants.ROUTE_SEMANTIC,
                      constants.ROUTE_UNDETERMINED)
    ]


def _stage_avg(db) -> list[dict]:  # noqa: ANN001
    rows = db.execute(
        select(RunStep.stage, func.avg(RunStep.duration_ms), func.count(RunStep.id))
        .group_by(RunStep.stage)
    ).all()
    items = []
    for stage, avg_ms, _count in rows:
        key = str(stage or "")
        items.append({
            "stage": constants.STAGE_LABELS.get(key, key),
            "avg_ms": int(round(float(avg_ms or 0))),
            "_order": float(avg_ms or 0),
        })
    items.sort(key=lambda item: item["_order"], reverse=True)
    return [{k: v for k, v in item.items() if k != "_order"} for item in items[:STAGE_TOP_N]]


def _daily_runs(db) -> dict:  # noqa: ANN001
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=DAILY_DAYS - 1)

    rows = db.execute(
        select(AgentRun.created_at).where(AgentRun.created_at >= start)
    ).all()
    buckets: dict[str, int] = {}
    for (created_at,) in rows:
        if created_at is None:
            continue
        key = fmt_date(created_at)
        buckets[key] = buckets.get(key, 0) + 1

    points = []
    for offset in range(DAILY_DAYS):
        day = start + timedelta(days=offset)
        key = fmt_date(day)
        points.append({"date": key, "count": buckets.get(key, 0)})
    return {"total": sum(point["count"] for point in points), "points": points}


@router.get("/overview")
def overview(db: DbSession, user: CurrentUser, only_mine: bool = False):
    # only_mine 仅契约兼容：agent_runs 无 user_id，无法按人过滤
    counts = _status_counts(db)
    total = sum(counts.values())
    success = counts.get(constants.STATUS_SUCCESS, 0)
    failed = counts.get(constants.STATUS_FAILED, 0)
    cancelled = counts.get(constants.STATUS_CANCELLED, 0)
    pending = counts.get(constants.STATUS_PENDING, 0)
    up, down = _feedback_counts(db)

    kpis = {
        "total_runs": _kpi("total_runs", total),
        "success_runs": _kpi("success_runs", success),
        "failed_runs": _kpi("failed_runs", failed),
        "cancelled_runs": _kpi("cancelled_runs", cancelled),
        "unfinished_runs": _kpi("unfinished_runs", pending),
        "success_rate": _kpi("success_rate", _ratio(success, success + failed + cancelled)),
        "avg_duration": _kpi("avg_duration", _avg_success_duration(db)),
        "like_rate": _kpi("like_rate", _ratio(up, up + down)),
    }

    return ok({
        "updated_at": fmt_dt(datetime.now().replace(microsecond=0)),
        "kpis": kpis,
        "route_dist": _route_dist(db),
        "stage_avg": _stage_avg(db),
        "daily_runs": _daily_runs(db),
    })
