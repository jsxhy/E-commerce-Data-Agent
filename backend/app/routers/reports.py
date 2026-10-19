"""诊断报告与站内预警通知路由。

契约：docs/api-contract.md §6
- GET  /api/reports?page=1&size=10&q=
- GET  /api/reports/{id}                     （附 evidence / actions）
- GET  /api/reports/notifications?unread_only=false
- POST /api/reports/notifications/{id}/read

``evidence_json`` / ``actions_json`` 是入库时序列化的 JSON 文本，出口统一解析成结构体；
解析失败（历史脏数据）时降级为空数组，不让整个接口 500。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from backend.app.deps import CurrentUser, DbSession
from backend.app.models import DiagnosisReport, Notification, Product
from backend.app.schemas import ok, page_payload
from backend.app.utils import fmt_dt, like_pattern, norm_page

router = APIRouter(prefix="/api/reports", tags=["reports"])

DEFAULT_REPORT_PAGE_SIZE = 10


def _loads(raw: str | None) -> list[Any]:
    """把 JSON 文本解析成 list；空/坏数据返回 []。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def report_brief(report: DiagnosisReport, product_name: str | None) -> dict:
    """契约 §6 列表项结构。"""
    return {
        "id": report.id,
        "question": report.question,
        "product_id": report.product_id,
        "product_name": product_name or "",
        "risk": report.risk,
        "evidence_count": int(report.evidence_count or 0),
        "summary": report.summary,
        "created_at": fmt_dt(report.created_at),
    }


def notification_item(notice: Notification) -> dict:
    """契约 §6 通知结构。"""
    return {
        "id": notice.id,
        "title": notice.title,
        "content": notice.content,
        "level": notice.level,
        "read": bool(notice.read),
        "product_id": notice.product_id,
        "created_at": fmt_dt(notice.created_at),
    }


def _unread_count(db) -> int:  # noqa: ANN001
    return int(db.execute(
        select(func.count(Notification.id)).where(Notification.read.is_(False))
    ).scalar() or 0)


# ---------------------------------------------------------------- 通知
# 注意：这两条必须写在 /{report_id} 之前，否则 "notifications" 会被当成 report_id
@router.get("/notifications")
def list_notifications(db: DbSession, user: CurrentUser, unread_only: bool = False):
    conditions = []
    if unread_only:
        conditions.append(Notification.read.is_(False))

    rows = db.execute(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    ).scalars().all()

    return ok({
        "items": [notification_item(n) for n in rows],
        "unread_count": _unread_count(db),
    })


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: DbSession, user: CurrentUser):
    notice = db.get(Notification, int(notification_id))
    if notice is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    notice.read = True
    db.commit()
    return ok({"id": notice.id, "read": True, "unread_count": _unread_count(db)})


# ---------------------------------------------------------------- 报告
@router.get("")
def list_reports(db: DbSession, user: CurrentUser,
                 page: int = 1, size: int = DEFAULT_REPORT_PAGE_SIZE, q: str = ""):
    page, size = norm_page(page, size, default_size=DEFAULT_REPORT_PAGE_SIZE)

    conditions = []
    pattern = like_pattern(q)
    if pattern:
        conditions.append(DiagnosisReport.question.like(pattern))

    total = db.execute(
        select(func.count(DiagnosisReport.id)).where(*conditions)
    ).scalar() or 0

    rows = db.execute(
        select(DiagnosisReport, Product.name)
        .outerjoin(Product, Product.id == DiagnosisReport.product_id)
        .where(*conditions)
        .order_by(DiagnosisReport.created_at.desc(), DiagnosisReport.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    items = [report_brief(report, name) for report, name in rows]
    return ok(page_payload(items, int(total), page, size))


@router.get("/{report_id}")
def get_report(report_id: int, db: DbSession, user: CurrentUser):
    row = db.execute(
        select(DiagnosisReport, Product.name)
        .outerjoin(Product, Product.id == DiagnosisReport.product_id)
        .where(DiagnosisReport.id == int(report_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="诊断报告不存在")
    report, product_name = row

    data = report_brief(report, product_name)
    data.update({
        "evidence": _loads(report.evidence_json),
        "actions": _loads(report.actions_json),
    })
    return ok(data)
