"""种子数据不变量测试。

这些数字是「前端演示效果」的硬约束（docs/api-contract.md 的样例值全部由它们推导），
所以任何改动种子数据的行为都必须让本文件保持绿灯，或同步改契约。

注意：本文件依赖 conftest.py 里的 `prepared_db`（会话级临时 SQLite + seed_all()）。
"""
from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import func, select

from backend.app.models import (AgentRun, DiagnosisReport, Notification, Order, Product,
                                Review, User)

# ------------------------------------------------------------------ 期望值常量
EXPECT_PRODUCTS = 20
EXPECT_ORDERS = 49
EXPECT_PAID_ORDERS = 48
EXPECT_CANCELLED_ORDERS = 1
EXPECT_CANCELLED_AMOUNT_CENTS = 8900
EXPECT_PAID_AMOUNT_CENTS = 708180
EXPECT_ALL_AMOUNT_CENTS = 717080

EXPECT_REVIEWS = 46
EXPECT_RATING_DIST = {5: 10, 4: 9, 3: 9, 2: 8, 1: 10}
EXPECT_AVG_RATING = 3.02
EXPECT_BAD_REVIEWS = 18          # 星级 <= 2
EXPECT_NOISY_EARPHONE = "静音蓝牙耳机"
EXPECT_NOISY_EARPHONE_BAD = 3

EXPECT_REPORTS = 10
EXPECT_HIGH_RISK_REPORTS = 4
EXPECT_NOTIFICATIONS = 3
EXPECT_UNREAD_NOTIFICATIONS = 2
EXPECT_PENDING_REVIEW_PRODUCTS = 1

EXPECT_RUNS = 20
EXPECT_RUN_STATUS = {"success": 12, "failed": 5, "pending": 3, "cancelled": 0}
EXPECT_ROUTE_DIST = {"structured": 9, "semantic": 6, "undetermined": 5}
EXPECT_SUCCESS_AVG_DURATION_MS = 21800
EXPECT_FEEDBACK_UP = 1
EXPECT_FEEDBACK_DOWN = 0


def _count(session, model) -> int:
    return int(session.execute(select(func.count(model.id))).scalar() or 0)


# ------------------------------------------------------------------ 用户
def test_seed_has_admin_user(session):
    users = session.execute(select(User)).scalars().all()
    assert len(users) >= 1
    admin = session.execute(
        select(User).where(User.username == "admin")
    ).scalar_one_or_none()
    assert admin is not None, "种子数据必须包含 admin 账号"
    assert admin.role == "admin"
    assert admin.display_name, "display_name 不能为空（首页 greeting 要用）"
    # 密码必须是 bcrypt 哈希，绝不能是明文
    assert admin.password_hash.startswith("$2"), "password_hash 应为 bcrypt 哈希"


# ------------------------------------------------------------------ 商品
def test_seed_product_count(session):
    assert _count(session, Product) == EXPECT_PRODUCTS


def test_seed_product_fields_valid(session):
    products = session.execute(select(Product)).scalars().all()
    for product in products:
        assert product.name, "商品名不能为空"
        assert product.category, "分类不能为空"
        assert product.price_cents > 0, f"商品 {product.name} 价格必须为正"
        assert product.stock >= 0
        assert product.status in ("on_sale", "off_sale")
        assert product.description, "描述不能为空（详情页要用）"


def test_seed_pending_review_products(session):
    count = int(session.execute(
        select(func.count(Product.id)).where(Product.pending_review.is_(True))
    ).scalar() or 0)
    assert count == EXPECT_PENDING_REVIEW_PRODUCTS


# ------------------------------------------------------------------ 订单
def test_seed_order_count_and_status(session):
    assert _count(session, Order) == EXPECT_ORDERS

    by_status = dict(session.execute(
        select(Order.status, func.count(Order.id)).group_by(Order.status)
    ).all())
    assert by_status.get("paid", 0) == EXPECT_PAID_ORDERS
    assert by_status.get("cancelled", 0) == EXPECT_CANCELLED_ORDERS
    assert by_status.get("refunded", 0) == 0


def test_seed_cancelled_order_amount(session):
    cancelled = session.execute(
        select(Order).where(Order.status == "cancelled")
    ).scalars().all()
    assert len(cancelled) == 1
    assert cancelled[0].amount_cents == EXPECT_CANCELLED_AMOUNT_CENTS


def test_seed_order_amount_sums(session):
    paid = int(session.execute(
        select(func.coalesce(func.sum(Order.amount_cents), 0))
        .where(Order.status == "paid")
    ).scalar() or 0)
    total = int(session.execute(
        select(func.coalesce(func.sum(Order.amount_cents), 0))
    ).scalar() or 0)

    assert paid == EXPECT_PAID_AMOUNT_CENTS, f"paid 合计应为 {EXPECT_PAID_AMOUNT_CENTS} 分"
    assert total == EXPECT_ALL_AMOUNT_CENTS, f"全部合计应为 {EXPECT_ALL_AMOUNT_CENTS} 分"
    # 两者差额必须正好是那一笔已取消订单
    assert total - paid == EXPECT_CANCELLED_AMOUNT_CENTS


def test_seed_order_no_unique_and_positive(session):
    orders = session.execute(select(Order)).scalars().all()
    order_nos = [o.order_no for o in orders]
    assert len(set(order_nos)) == len(order_nos), "order_no 必须唯一"
    for order in orders:
        assert order.quantity >= 1
        assert order.amount_cents > 0
        assert order.buyer, "买家不能为空"


# ------------------------------------------------------------------ 评价
def test_seed_review_count(session):
    assert _count(session, Review) == EXPECT_REVIEWS


def test_seed_rating_distribution(session):
    rows = session.execute(
        select(Review.rating, func.count(Review.id)).group_by(Review.rating)
    ).all()
    dist = {int(rating): int(count) for rating, count in rows}
    assert dist == EXPECT_RATING_DIST


def test_seed_avg_rating(session):
    avg = float(session.execute(select(func.avg(Review.rating))).scalar() or 0)
    assert round(avg, 2) == EXPECT_AVG_RATING


def test_seed_bad_review_count(session):
    bad = int(session.execute(
        select(func.count(Review.id)).where(Review.rating <= 2)
    ).scalar() or 0)
    assert bad == EXPECT_BAD_REVIEWS


def test_seed_noisy_earphone_bad_reviews(session):
    product = session.execute(
        select(Product).where(Product.name == EXPECT_NOISY_EARPHONE)
    ).scalar_one_or_none()
    assert product is not None, f"种子数据必须包含商品「{EXPECT_NOISY_EARPHONE}」"

    bad = int(session.execute(
        select(func.count(Review.id))
        .where(Review.product_id == product.id, Review.rating <= 2)
    ).scalar() or 0)
    assert bad == EXPECT_NOISY_EARPHONE_BAD


def test_seed_review_content_not_empty(session):
    reviews = session.execute(select(Review)).scalars().all()
    for review in reviews:
        assert review.content, "评价内容不能为空（检索/展示都要用）"
        assert 1 <= review.rating <= 5


# ------------------------------------------------------------------ 诊断报告 / 通知
def test_seed_diagnosis_reports(session):
    assert _count(session, DiagnosisReport) == EXPECT_REPORTS

    high = int(session.execute(
        select(func.count(DiagnosisReport.id)).where(DiagnosisReport.risk == "high")
    ).scalar() or 0)
    assert high == EXPECT_HIGH_RISK_REPORTS

    risks = set(session.execute(select(DiagnosisReport.risk)).scalars().all())
    assert risks <= {"high", "low"}, f"risk 只能是 high/low，实际 {risks}"


def test_seed_notifications(session):
    assert _count(session, Notification) == EXPECT_NOTIFICATIONS

    unread = int(session.execute(
        select(func.count(Notification.id)).where(Notification.read.is_(False))
    ).scalar() or 0)
    assert unread == EXPECT_UNREAD_NOTIFICATIONS

    levels = set(session.execute(select(Notification.level)).scalars().all())
    assert levels <= {"high", "info"}, f"level 只能是 high/info，实际 {levels}"


# ------------------------------------------------------------------ Agent 运行
def test_seed_agent_run_count(session):
    assert _count(session, AgentRun) == EXPECT_RUNS


def test_seed_agent_run_status_distribution(session):
    rows = session.execute(
        select(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status)
    ).all()
    dist = {status: int(count) for status, count in rows}
    for status, expect in EXPECT_RUN_STATUS.items():
        assert dist.get(status, 0) == expect, f"{status} 应有 {expect} 条，实际 {dist.get(status, 0)}"


def test_seed_agent_run_route_distribution(session):
    rows = session.execute(
        select(AgentRun.route, func.count(AgentRun.id)).group_by(AgentRun.route)
    ).all()
    dist = {route: int(count) for route, count in rows}
    assert dist == EXPECT_ROUTE_DIST


def test_seed_success_run_avg_duration(session):
    avg = session.execute(
        select(func.avg(AgentRun.duration_ms)).where(AgentRun.status == "success")
    ).scalar()
    assert avg is not None
    assert round(float(avg)) == EXPECT_SUCCESS_AVG_DURATION_MS


def test_seed_run_feedback(session):
    rows = session.execute(
        select(AgentRun.feedback, func.count(AgentRun.id)).group_by(AgentRun.feedback)
    ).all()
    dist = {feedback: int(count) for feedback, count in rows}
    assert dist.get("up", 0) == EXPECT_FEEDBACK_UP
    assert dist.get("down", 0) == EXPECT_FEEDBACK_DOWN


def test_seed_runs_have_steps(session):
    """每个运行都要有步骤，否则运行详情页是空的。"""
    runs = session.execute(select(AgentRun)).scalars().all()
    for run in runs:
        assert run.step_count == len(run.steps), (
            f"run {run.id} 的 step_count({run.step_count}) 与真实步骤数({len(run.steps)}) 不一致")
        assert run.question, "question 不能为空"


def test_seed_run_questions_are_chinese(session):
    """问题文本用于演示，必须是中文且不重复得离谱。"""
    questions = [run.question for run in session.execute(select(AgentRun)).scalars().all()]
    assert all(questions), "question 不能为空"
    # 至少一半的问题文本带中文字符
    chinese = sum(1 for q in questions if any("\u4e00" <= ch <= "\u9fff" for ch in q))
    assert chinese >= len(questions) // 2
