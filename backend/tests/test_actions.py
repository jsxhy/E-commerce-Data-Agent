"""动作白名单与执行测试。

本文件最重要的两组断言：
1. **白名单只有 3 类**，任何第 4 类动作必须抛 ``ValueError``；
2. 动作执行**绝不**触碰只读资源（价格/库存/上下架/订单状态）——这是契约里
   ``READ_ONLY_RESOURCES`` 的硬约束，用真实数据库断言前后值不变。
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select, update

from backend.app.agent import actions as act
from backend.app.constants import (ACTION_LABELS, ACTION_MARK_PENDING,
                                   ACTION_SAVE_REPORT, ACTION_SEND_ALERT,
                                   ALLOWED_ACTIONS, READ_ONLY_RESOURCES, RISK_HIGH,
                                   RISK_LOW)
from backend.app.models import DiagnosisReport, Notification, Product


@pytest.fixture(autouse=True)
def _restore_shared_db(session):
    """动作执行会写 diagnosis_reports / notifications / products.pending_review。

    本仓库的 ``prepared_db`` 是 **session 级**共享库（一次 seed 供所有测试用），
    所以这里必须把副作用还原，否则会污染种子不变量与 dashboard KPI 测试
    （实测：不还原时 reports 从 10 涨到 16、notifications 从 3 涨到 8）。
    """
    report_max = session.execute(select(func.max(DiagnosisReport.id))).scalar() or 0
    notif_max = session.execute(select(func.max(Notification.id))).scalar() or 0
    pending_before = set(session.execute(
        select(Product.id).where(Product.pending_review.is_(True))
    ).scalars().all())

    yield

    session.rollback()
    session.execute(delete(DiagnosisReport).where(DiagnosisReport.id > report_max))
    session.execute(delete(Notification).where(Notification.id > notif_max))
    session.execute(
        update(Product)
        .where(Product.pending_review.is_(True), Product.id.notin_(pending_before))
        .values(pending_review=False)
    )
    session.commit()


def _evidence(*ratings: int, product_id: int = 1, product_name: str = "静音蓝牙耳机"):
    return [{
        "source": "review",
        "ref_id": idx + 1,
        "product_id": product_id,
        "product_name": product_name,
        "rating": rating,
        "snippet": f"第 {idx + 1} 条评价正文",
        "distance": 0.2,
    } for idx, rating in enumerate(ratings)]


def _analysis(ratings=(1, 1), risk: str = RISK_HIGH, **extra):
    evidence = _evidence(*ratings)
    payload = {
        "question": "静音蓝牙耳机最近差评集中在哪些问题？",
        "route": "structured",
        "risk": risk,
        "evidence": evidence,
        "product_id": 1,
        "product_name": "静音蓝牙耳机",
        "bad_review_count": sum(1 for r in ratings if r <= 2),
        "run_id": 1,
    }
    payload.update(extra)
    return payload


# ------------------------------------------------------------------ 白名单
def test_allowed_actions_are_exactly_three():
    assert tuple(ALLOWED_ACTIONS) == (ACTION_SAVE_REPORT, ACTION_SEND_ALERT,
                                      ACTION_MARK_PENDING)
    assert len(ALLOWED_ACTIONS) == 3


def test_action_labels_cover_all_allowed():
    for action_type in ALLOWED_ACTIONS:
        assert ACTION_LABELS.get(action_type), action_type


@pytest.mark.parametrize("bad_type", [
    "update_product_price", "set_stock", "delete_order", "update_order_status",
    "toggle_on_sale", "refund_order", "", None, "SAVE_DIAGNOSIS_REPORT",
])
def test_assert_allowed_rejects_non_whitelisted(bad_type):
    with pytest.raises(ValueError):
        act.assert_allowed(bad_type)


def test_assert_allowed_accepts_whitelisted():
    for action_type in ALLOWED_ACTIONS:
        assert act.assert_allowed(action_type) == action_type


def test_execute_actions_raises_on_non_whitelisted():
    """非白名单动作必须直接抛 ValueError，而不是静默跳过。"""
    with pytest.raises(ValueError):
        act.execute_actions(None, [{"type": "update_product_price",
                                    "payload": {"product_id": 1}}])


# ------------------------------------------------------------------ 规划
def test_plan_actions_returns_empty_without_evidence():
    analysis = {"question": "随便问问", "risk": RISK_HIGH, "evidence": []}
    assert act.plan_actions(None, analysis) == []


def test_plan_actions_low_risk_only_saves_report():
    planned = act.plan_actions(None, _analysis(ratings=(5, 4), risk=RISK_LOW))
    types = [a["type"] for a in planned]
    assert types == [ACTION_SAVE_REPORT]


def test_plan_actions_high_risk_adds_alert_and_pending():
    planned = act.plan_actions(None, _analysis(ratings=(1, 1), risk=RISK_HIGH))
    types = [a["type"] for a in planned]
    assert ACTION_SAVE_REPORT in types
    assert ACTION_SEND_ALERT in types
    assert ACTION_MARK_PENDING in types
    assert set(types) <= set(ALLOWED_ACTIONS)


def test_plan_actions_high_risk_without_product_skips_pending():
    """完全没有商品对象（analysis 与证据里都没有 product_id）→ 不规划待复核。

    注意：只要证据里带了 product_id，`_pick_product_id` 就会回填它——这是有意的，
    因为语义检索返回的每条证据都自带 product_id。
    """
    analysis = _analysis(ratings=(1, 1), risk=RISK_HIGH, product_id=None)
    for item in analysis["evidence"]:
        item["product_id"] = None
    types = [a["type"] for a in act.plan_actions(None, analysis)]
    assert ACTION_MARK_PENDING not in types
    assert ACTION_SEND_ALERT in types


def test_planned_actions_carry_label_and_summary():
    for action in act.plan_actions(None, _analysis()):
        assert action["label"]
        assert action["summary"]
        assert action["payload"]["evidence"]
        assert action["payload"]["risk"] == RISK_HIGH


def test_public_actions_strips_payload():
    planned = act.plan_actions(None, _analysis())
    public = act.public_actions(planned)
    assert len(public) == len(planned)
    for item in public:
        assert set(item) == {"type", "label", "summary"}


# ------------------------------------------------------------------ 执行
def test_execute_actions_saves_report(session):
    before = len(session.execute(select(DiagnosisReport)).scalars().all())
    planned = act.plan_actions(session, _analysis(ratings=(5, 4), risk=RISK_LOW))
    results = act.execute_actions(session, planned)

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert ACTION_SAVE_REPORT == results[0]["type"]
    after = len(session.execute(select(DiagnosisReport)).scalars().all())
    assert after == before + 1


def test_execute_actions_high_risk_writes_all_three(session):
    notif_before = len(session.execute(select(Notification)).scalars().all())
    planned = act.plan_actions(session, _analysis(ratings=(1, 1), risk=RISK_HIGH))
    results = act.execute_actions(session, planned)

    assert len(results) == len(planned)
    assert all(item["ok"] for item in results), results
    assert len(session.execute(select(Notification)).scalars().all()) == notif_before + 1

    product = session.get(Product, 1)
    assert product is not None
    assert product.pending_review is True


def test_execute_actions_does_not_touch_read_only_resources(session):
    """核心安全断言：价格/库存/上下架/订单状态在动作执行前后完全一致。"""
    product = session.get(Product, 1)
    before = (product.price_cents, product.stock, product.status)

    planned = act.plan_actions(session, _analysis(ratings=(1, 1), risk=RISK_HIGH))
    act.execute_actions(session, planned)

    session.expire_all()
    product = session.get(Product, 1)
    assert (product.price_cents, product.stock, product.status) == before
    # 只读资源清单本身也是契约的一部分
    assert "products.price" in READ_ONLY_RESOURCES
    assert "orders.status" in READ_ONLY_RESOURCES


def test_execute_actions_is_idempotent_for_pending_flag(session):
    planned = act.plan_actions(session, _analysis(ratings=(1, 1), risk=RISK_HIGH))
    act.execute_actions(session, planned)
    # 第二次执行不该报错（商品已是待复核）
    results = act.execute_actions(session, planned)
    pending = [r for r in results if r["type"] == ACTION_MARK_PENDING]
    assert pending and pending[0]["ok"] is True


def test_execute_actions_missing_product_degrades_to_failed_item(session):
    analysis = _analysis(ratings=(1, 1), risk=RISK_HIGH, product_id=999999)
    analysis["evidence"] = _evidence(1, 1, product_id=999999)
    planned = act.plan_actions(session, analysis)
    results = act.execute_actions(session, planned)
    marked = [r for r in results if r["type"] == ACTION_MARK_PENDING]
    assert marked and marked[0]["ok"] is False
    # 其它动作仍然成功，不因单个失败而整体回滚
    others = [r for r in results if r["type"] != ACTION_MARK_PENDING]
    assert all(r["ok"] for r in others)


def test_execute_actions_empty_list(session):
    assert act.execute_actions(session, []) == []


def test_skip_actions_marks_all_skipped():
    planned = act.plan_actions(None, _analysis())
    skipped = act.skip_actions(planned)
    assert len(skipped) == len(planned)
    for item in skipped:
        assert item["ok"] is False
        assert "用户拒绝执行" in item["detail"]


def test_guard_read_only_blocks_price_write_intent(session):
    """描述里出现价格/库存写意图的动作必须被拦下（防御性检查）。"""
    evil = {
        "type": ACTION_SAVE_REPORT,
        "label": "伪装成保存报告",
        "summary": "顺便 set_price 到 1 元",
        "payload": {"question": "x", "evidence": []},
    }
    with pytest.raises(ValueError):
        act.execute_actions(session, [evil])


def test_build_summary_mentions_product_and_snippets():
    analysis = _analysis(ratings=(1, 2))
    summary = act.build_summary(analysis, analysis["evidence"])
    assert "静音蓝牙耳机" in summary
    assert "差评" in summary


def test_plan_actions_does_not_mutate_analysis():
    analysis = _analysis()
    snapshot = str(analysis)
    act.plan_actions(None, analysis)
    assert str(analysis) == snapshot
