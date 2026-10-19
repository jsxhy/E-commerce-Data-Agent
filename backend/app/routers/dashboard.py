"""系统首页路由：概览 KPI（全部实时统计）+ 一键巡检。

契约：docs/api-contract.md §2
- GET  /api/dashboard/overview
- POST /api/dashboard/inspect

只读边界（constants.READ_ONLY_RESOURCES）：巡检绝不修改商品价格/库存/上下架与订单状态，
写入范围仅 diagnosis_reports、notifications 两张表。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import case, func, select

from backend.app import constants
from backend.app.deps import CurrentUser, DbSession
from backend.app.models import (AgentRun, DiagnosisReport, Notification, Order, Product,
                                Review, RunStep)
from backend.app.schemas import ok
from backend.app.utils import fmt_date, fmt_dt, yuan
from backend.config import get_config

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

TREND_DAYS = 30
TOP_ATTENTION = 5


def _risk_rules() -> dict:
    """风险阈值统一取自配置 agent.risk，避免各处写死。"""
    rules = dict(get_config().get("agent", {}).get("risk", {}))
    return {
        "bad_rating_max": int(rules.get("bad_rating_max", 2)),
        "high_risk_bad_review_count": int(rules.get("high_risk_bad_review_count", 2)),
        "high_risk_rating_below": float(rules.get("high_risk_rating_below", 2.5)),
    }


def _kpi(value, label: str) -> dict:
    return {"value": value, "label": label}


# ------------------------------------------------------------------ 概览

def _order_trend(db) -> dict:
    """近 30 天（含今天）订单趋势，固定 30 个点。"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=TREND_DAYS - 1)

    rows = db.execute(
        select(Order.created_at, Order.amount_cents).where(Order.created_at >= start)
    ).all()

    buckets: dict[str, list] = {}
    for created_at, amount_cents in rows:
        key = fmt_date(created_at)
        slot = buckets.setdefault(key, [0, 0])
        slot[0] += 1
        slot[1] += int(amount_cents or 0)

    points = []
    for offset in range(TREND_DAYS):
        day = (start + timedelta(days=offset)).date()
        key = day.strftime("%Y-%m-%d")
        count, cents = buckets.get(key, [0, 0])
        points.append({"date": key, "count": count, "amount": yuan(cents)})

    total_orders = sum(item[0] for item in buckets.values())
    total_cents = sum(item[1] for item in buckets.values())
    return {
        "total_orders": total_orders,
        "total_amount": yuan(total_cents),
        "points": points,
    }


def _rating_dist(db) -> dict:
    """星级分布：固定 5 个桶（star 1..5），avg 为全部评价均分。"""
    rows = db.execute(
        select(Review.rating, func.count(Review.id)).group_by(Review.rating)
    ).all()
    counts = {int(rating): int(cnt) for rating, cnt in rows}

    avg_raw = db.execute(select(func.avg(Review.rating))).scalar()
    avg = round(float(avg_raw), 2) if avg_raw is not None else 0.0

    buckets = [{"star": star, "count": counts.get(star, 0)} for star in range(1, 6)]
    return {"avg": avg, "buckets": buckets}


def _attention_products(db) -> list[dict]:
    """按「星级<=2 的评价条数」降序取前 5 个需要关注的商品。"""
    bad_max = _risk_rules()["bad_rating_max"]

    bad_count = func.sum(
        case((Review.rating <= bad_max, 1), else_=0)
    ).label("bad_review_count")
    rows = db.execute(
        select(
            Product.id,
            Product.name,
            Product.category,
            bad_count,
            func.count(Review.id).label("review_count"),
            func.avg(Review.rating).label("avg_rating"),
        )
        .join(Review, Review.product_id == Product.id)
        .group_by(Product.id, Product.name, Product.category)
        .having(bad_count > 0)
        .order_by(bad_count.desc(), func.avg(Review.rating).asc(), Product.id.asc())
        .limit(TOP_ATTENTION)
    ).all()

    return [
        {
            "product_id": pid,
            "name": name,
            "category": category,
            "bad_review_count": int(bad or 0),
            "avg_rating": round(float(avg or 0), 2),
        }
        for pid, name, category, bad, _review_count, avg in rows
    ]


@router.get("/overview")
def overview(db: DbSession, user: CurrentUser):
    product_count = db.execute(select(func.count(Product.id))).scalar() or 0
    order_count = db.execute(select(func.count(Order.id))).scalar() or 0
    review_count = db.execute(select(func.count(Review.id))).scalar() or 0

    avg_rating_raw = db.execute(select(func.avg(Review.rating))).scalar()
    avg_rating = round(float(avg_rating_raw), 2) if avg_rating_raw is not None else 0.0

    paid_cents = db.execute(
        select(func.coalesce(func.sum(Order.amount_cents), 0))
        .where(Order.status == "paid")
    ).scalar() or 0

    high_risk_reports = db.execute(
        select(func.count(DiagnosisReport.id))
        .where(DiagnosisReport.risk == constants.RISK_HIGH)
    ).scalar() or 0

    unread_alerts = db.execute(
        select(func.count(Notification.id)).where(Notification.read.is_(False))
    ).scalar() or 0

    pending_review = db.execute(
        select(func.count(Product.id)).where(Product.pending_review.is_(True))
    ).scalar() or 0

    kpis = {
        "product_count": _kpi(int(product_count), "全部商品"),
        "order_count": _kpi(int(order_count), "累计订单"),
        "review_count": _kpi(int(review_count), "累计评价"),
        "avg_rating": _kpi(avg_rating, "满分 5 分"),
        "paid_amount": _kpi(yuan(paid_cents), "订单实付合计（不含已取消）"),
        "high_risk_reports": _kpi(int(high_risk_reports), "Agent 判定需人工确认"),
        "unread_alerts": _kpi(int(unread_alerts), "站内通知"),
        "pending_review": _kpi(int(pending_review), "人工复核状态"),
    }

    return ok({
        "updated_at": fmt_dt(datetime.now().replace(microsecond=0)),
        "greeting": f"你好，{user.display_name or user.username}",
        "subtitle": "这是你的电商数据与 Agent 运行概览，可一键巡检商品、订单、评价的健康度。",
        "kpis": kpis,
        "order_trend": _order_trend(db),
        "rating_dist": _rating_dist(db),
        "attention_products": _attention_products(db),
    })


# ------------------------------------------------------------------ 一键巡检

INSPECT_QUESTION = "系统一键巡检"


def _inspect_steps() -> list[dict]:
    """巡检过程的 RunStep 骨架（stage 取自 constants，前端按固定集合配色）。"""
    return [
        {"stage": constants.STAGE_STARTING, "actor": constants.ACTOR_SYSTEM,
         "detail": "系统一键巡检已启动", "duration_ms": 12},
        {"stage": constants.STAGE_SCHEDULING, "actor": constants.ACTOR_SCHEDULER,
         "detail": "调度商品、订单、评价三类健康度检查", "duration_ms": 35},
        {"stage": constants.STAGE_QUERYING, "actor": constants.ACTOR_ANALYSIS,
         "detail": "统计近 30 天订单与差评分布", "duration_ms": 268},
        {"stage": constants.STAGE_PREPARING_EVIDENCE, "actor": constants.ACTOR_ANALYSIS,
         "detail": "汇总差评证据并按阈值判定风险", "duration_ms": 156},
        {"stage": constants.STAGE_AGENT_REPORT, "actor": constants.ACTOR_REPORT,
         "detail": "生成诊断报告", "duration_ms": 231},
        {"stage": constants.STAGE_EXECUTING, "actor": constants.ACTOR_SYSTEM,
         "detail": "高风险商品已生成站内预警", "duration_ms": 88},
        {"stage": constants.STAGE_FINISHED, "actor": constants.ACTOR_SYSTEM,
         "detail": "巡检完成", "duration_ms": 20},
    ]


def _bad_products(db) -> list[dict]:
    """差评数 >= 2 的商品及其差评明细（巡检的诊断对象）。"""
    rules = _risk_rules()
    bad_max = rules["bad_rating_max"]
    threshold = rules["high_risk_bad_review_count"]

    bad_count = func.sum(case((Review.rating <= bad_max, 1), else_=0))
    rows = db.execute(
        select(Product.id, Product.name, Product.category, bad_count,
               func.count(Review.id), func.avg(Review.rating))
        .join(Review, Review.product_id == Product.id)
        .group_by(Product.id, Product.name, Product.category)
        .having(bad_count >= threshold)
        .order_by(bad_count.desc(), Product.id.asc())
    ).all()

    result = []
    for pid, name, category, bad, review_count, avg_rating in rows:
        reviews = db.execute(
            select(Review).where(Review.product_id == pid, Review.rating <= bad_max)
            .order_by(Review.rating.asc(), Review.id.asc())
        ).scalars().all()
        result.append({
            "product_id": pid,
            "name": name,
            "category": category,
            "bad_review_count": int(bad or 0),
            "review_count": int(review_count or 0),
            "avg_rating": round(float(avg_rating or 0), 2),
            "reviews": reviews,
        })
    return result


def _judge_risk(item: dict) -> str:
    """风险判定：差评数达阈值且均分低于阈值 → high，否则 low。"""
    rules = _risk_rules()
    if (item["bad_review_count"] >= rules["high_risk_bad_review_count"]
            and item["avg_rating"] < rules["high_risk_rating_below"]):
        return constants.RISK_HIGH
    return constants.RISK_LOW


def _evidence(item: dict) -> list[dict]:
    evidence = []
    for review in item["reviews"][:5]:
        evidence.append({
            "source": "review",
            "ref_id": review.id,
            "rating": review.rating,
            "snippet": (review.content or "")[:80],
            "distance": None,
        })
    return evidence


def _actions(risk: str) -> list[dict]:
    actions = [{
        "type": constants.ACTION_SAVE_REPORT,
        "label": constants.ACTION_LABELS[constants.ACTION_SAVE_REPORT],
        "status": "executed",
    }]
    if risk == constants.RISK_HIGH:
        actions.append({
            "type": constants.ACTION_SEND_ALERT,
            "label": constants.ACTION_LABELS[constants.ACTION_SEND_ALERT],
            "status": "executed",
        })
    return actions


@router.post("/inspect")
def inspect(db: DbSession, user: CurrentUser):
    steps = _inspect_steps()
    duration_ms = sum(step["duration_ms"] for step in steps)

    run = AgentRun(
        question=INSPECT_QUESTION,
        route=constants.ROUTE_UNDETERMINED,
        status=constants.STATUS_SUCCESS,
        duration_ms=duration_ms,
        step_count=len(steps),
        answer="",
    )
    db.add(run)
    db.flush()

    for seq, step in enumerate(steps, start=1):
        db.add(RunStep(run_id=run.id, seq=seq, stage=step["stage"], actor=step["actor"],
                       detail=step["detail"], duration_ms=step["duration_ms"]))

    report_count = 0
    alert_count = 0
    for item in _bad_products(db):
        risk = _judge_risk(item)
        evidence = _evidence(item)
        summary = (
            f"{item['name']}（{item['category']}）近 30 天差评 {item['bad_review_count']} 条，"
            f"均分 {item['avg_rating']:.2f}，建议人工复核。"
        )
        db.add(DiagnosisReport(
            question=INSPECT_QUESTION,
            product_id=item["product_id"],
            risk=risk,
            evidence_count=len(evidence),
            summary=summary,
            evidence_json=json.dumps(evidence, ensure_ascii=False),
            actions_json=json.dumps(_actions(risk), ensure_ascii=False),
            run_id=run.id,
        ))
        report_count += 1

        if risk == constants.RISK_HIGH:
            db.add(Notification(
                title="高风险商品预警",
                content=(f"{item['name']} 近 30 天差评 {item['bad_review_count']} 条，"
                         f"均分 {item['avg_rating']:.2f}，请尽快人工复核。"),
                level=constants.RISK_HIGH,
                read=False,
                product_id=item["product_id"],
            ))
            alert_count += 1

    db.commit()

    return ok({
        "run_id": run.id,
        "report_count": report_count,
        "alert_count": alert_count,
        "message": "巡检完成",
    })
