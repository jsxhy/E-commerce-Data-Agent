"""接口 CRUD / 契约一致性测试。

覆盖：
- 登录成功与失败、未登录 401、非法 token 401；
- dashboard overview 的 8 个 KPI 数值与契约样例一致；
- products / orders / reviews / users 的分页与筛选；
- 金额出口「元」、时间出口 "YYYY-MM-DD HH:MM:SS"。
"""
from __future__ import annotations

import re

import pytest

# 契约 §2 的 KPI 期望值（由种子数据推导，见 test_seed_invariants.py）
EXPECT_KPIS = {
    "product_count": 20,
    "order_count": 49,
    "review_count": 46,
    "avg_rating": 3.02,
    "paid_amount": 7081.8,
    "high_risk_reports": 4,
    "unread_alerts": 2,
    "pending_review": 1,
}
EXPECT_KPI_LABELS = {
    "product_count": "全部商品",
    "order_count": "累计订单",
    "review_count": "累计评价",
    "avg_rating": "满分 5 分",
    "paid_amount": "订单实付合计（不含已取消）",
    "high_risk_reports": "Agent 判定需人工确认",
    "unread_alerts": "站内通知",
    "pending_review": "人工复核状态",
}

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _data(resp) -> dict:
    """断言统一包裹并返回 data。"""
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    assert body["message"] == "ok", body
    return body["data"]


# ------------------------------------------------------------------ 鉴权
def test_login_success(client):
    resp = client.post("/api/auth/login",
                       json={"username": "admin", "password": "admin123"})
    data = _data(resp)

    assert isinstance(data["token"], str) and data["token"]
    user = data["user"]
    assert user["username"] == "admin"
    assert user["role"] == "admin"
    assert user["display_name"] == "管理员"
    assert isinstance(user["id"], int)
    # 契约 §1：user 结构固定 4 个字段，不能泄漏 password_hash
    assert set(user) == {"id", "username", "display_name", "role"}


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login",
                       json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == 401
    assert body["message"] == "用户名或密码错误"
    assert body["data"] is None


def test_login_unknown_user(client):
    resp = client.post("/api/auth/login",
                       json={"username": "nobody", "password": "whatever"})
    assert resp.status_code == 401
    assert resp.json()["message"] == "用户名或密码错误"


def test_me_returns_current_user(client, auth):
    data = _data(client.get("/api/auth/me", headers=auth))
    assert data["username"] == "admin"
    assert data["display_name"] == "管理员"
    assert set(data) == {"id", "username", "display_name", "role"}


PROTECTED_GETS = [
    "/api/auth/me",
    "/api/dashboard/overview",
    "/api/products",
    "/api/orders",
    "/api/reviews",
    "/api/users",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_requires_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body["code"] == 401
    assert body["message"] == "未登录或登录已过期"
    assert body["data"] is None


def test_requires_login_for_inspect(client):
    resp = client.post("/api/dashboard/inspect")
    assert resp.status_code == 401
    assert resp.json()["message"] == "未登录或登录已过期"


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_invalid_token_rejected(client, path):
    resp = client.get(path, headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_malformed_authorization_header(client):
    resp = client.get("/api/products", headers={"Authorization": "Basic abc"})
    assert resp.status_code == 401


# ------------------------------------------------------------------ Dashboard
def test_overview_kpis(client, auth):
    data = _data(client.get("/api/dashboard/overview", headers=auth))

    kpis = data["kpis"]
    assert set(kpis) == set(EXPECT_KPIS), "kpis 的 8 个键名固定"
    for key, expect in EXPECT_KPIS.items():
        assert kpis[key]["value"] == pytest.approx(expect), f"KPI {key} 数值不符"
        assert kpis[key]["label"] == EXPECT_KPI_LABELS[key], f"KPI {key} 文案不符"


def test_overview_header_fields(client, auth):
    data = _data(client.get("/api/dashboard/overview", headers=auth))
    assert data["greeting"] == "你好，管理员"
    assert data["subtitle"] == (
        "这是你的电商数据与 Agent 运行概览，可一键巡检商品、订单、评价的健康度。")
    assert DATETIME_RE.match(data["updated_at"]), data["updated_at"]


def test_overview_order_trend(client, auth):
    data = _data(client.get("/api/dashboard/overview", headers=auth))
    trend = data["order_trend"]

    points = trend["points"]
    assert len(points) == 30, "order_trend.points 固定 30 项"
    for point in points:
        assert DATE_RE.match(point["date"]), point["date"]
        assert isinstance(point["count"], int) and point["count"] >= 0
        assert point["amount"] >= 0

    # 日期连续且升序，最后一天是今天
    from datetime import date, datetime, timedelta

    days = [datetime.strptime(p["date"], "%Y-%m-%d").date() for p in points]
    assert days == sorted(days)
    assert days[-1] == date.today()
    assert (days[-1] - days[0]).days == 29
    for earlier, later in zip(days, days[1:]):
        assert later - earlier == timedelta(days=1)

    # 30 天内合计包含已取消订单
    assert trend["total_orders"] == sum(p["count"] for p in points)
    assert trend["total_amount"] == pytest.approx(sum(p["amount"] for p in points), abs=0.01)
    assert trend["total_orders"] == 49
    assert trend["total_amount"] == pytest.approx(7170.8)


def test_overview_rating_dist(client, auth):
    data = _data(client.get("/api/dashboard/overview", headers=auth))
    dist = data["rating_dist"]

    assert dist["avg"] == pytest.approx(3.02)
    buckets = dist["buckets"]
    assert len(buckets) == 5
    assert [b["star"] for b in buckets] == [1, 2, 3, 4, 5]
    assert [b["count"] for b in buckets] == [10, 8, 9, 9, 10]


def test_overview_attention_products(client, auth):
    data = _data(client.get("/api/dashboard/overview", headers=auth))
    items = data["attention_products"]

    assert 1 <= len(items) <= 5, "最多 5 条"
    counts = [item["bad_review_count"] for item in items]
    assert counts == sorted(counts, reverse=True), "必须按差评数降序"

    for item in items:
        assert set(item) == {"product_id", "name", "category",
                             "bad_review_count", "avg_rating"}
        assert isinstance(item["product_id"], int)
        assert item["name"]
        assert item["bad_review_count"] >= 1
        assert 0 <= item["avg_rating"] <= 5

    top = items[0]
    assert top["name"] == "静音蓝牙耳机"
    assert top["bad_review_count"] == 3
    # 契约 §3 样例里的 avg_rating=2.1 与 review_count=5 数学上不自洽，
    # 说明那只是示意值；这里只断言合理区间，真实值由种子数据决定。
    assert 1.0 <= top["avg_rating"] <= 5.0


def test_overview_kpis_are_live(client, auth, session):
    """KPI 必须实时统计，而不是写死：直接改库后数值要跟着变。"""
    from backend.app.models import Notification

    before = _data(client.get("/api/dashboard/overview", headers=auth))["kpis"]
    unread_before = before["unread_alerts"]["value"]

    session.add(Notification(title="测试预警", content="临时插入", level="info", read=False))
    session.commit()
    try:
        after = _data(client.get("/api/dashboard/overview", headers=auth))["kpis"]
        assert after["unread_alerts"]["value"] == unread_before + 1
    finally:
        session.delete(session.query(Notification)
                       .filter(Notification.title == "测试预警").one())
        session.commit()

    restored = _data(client.get("/api/dashboard/overview", headers=auth))["kpis"]
    assert restored["unread_alerts"]["value"] == unread_before


# ------------------------------------------------------------------ 分页基类
def _assert_page(data, *, total: int | None = None, page: int = 1, size: int = 20):
    assert set(data) == {"items", "total", "page", "size"}
    assert data["page"] == page
    assert data["size"] == size
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= size
    if total is not None:
        assert data["total"] == total


# ------------------------------------------------------------------ 商品
def test_products_pagination(client, auth):
    data = _data(client.get("/api/products", headers=auth, params={"page": 1, "size": 20}))
    _assert_page(data, total=20, page=1, size=20)
    assert len(data["items"]) == 20

    # 分页断言必须用同一个 size，否则 offset 口径不同、两页会重叠
    first = _data(client.get("/api/products", headers=auth, params={"page": 1, "size": 15}))
    _assert_page(first, total=20, page=1, size=15)
    assert len(first["items"]) == 15

    second = _data(client.get("/api/products", headers=auth, params={"page": 2, "size": 15}))
    _assert_page(second, total=20, page=2, size=15)
    assert len(second["items"]) == 5

    # 两页之间不重复
    first_ids = {i["id"] for i in first["items"]}
    assert first_ids.isdisjoint({i["id"] for i in second["items"]})


def test_products_item_contract(client, auth, session):
    from backend.app.models import Product

    data = _data(client.get("/api/products", headers=auth, params={"q": "静音蓝牙耳机"}))
    assert data["total"] == 1
    item = data["items"][0]

    assert set(item) == {"id", "name", "category", "price", "stock", "status",
                         "review_count", "avg_rating", "bad_review_count",
                         "pending_review", "created_at"}
    assert item["name"] == "静音蓝牙耳机"
    row = session.get(Product, item["id"])
    assert item["price"] == pytest.approx(round(row.price_cents / 100, 2)), "金额出口必须是元"
    assert isinstance(item["price"], float)
    assert item["status"] in ("on_sale", "off_sale")
    # 差评数是种子数据的硬约束（3 条），条数/均分则与评价表实时一致
    assert item["bad_review_count"] == 3
    assert isinstance(item["pending_review"], bool)
    assert DATETIME_RE.match(item["created_at"])


def test_products_review_stats_match_db(client, auth, session):
    """review_count / avg_rating / bad_review_count 必须由评价表实时聚合而来。"""
    from sqlalchemy import case, func, select

    from backend.app.models import Review

    bad_count = func.sum(case((Review.rating <= 2, 1), else_=0))
    rows = session.execute(
        select(Review.product_id, func.count(Review.id), func.avg(Review.rating), bad_count)
        .group_by(Review.product_id)
    ).all()
    expected = {
        pid: {
            "review_count": int(cnt),
            "avg_rating": round(float(avg), 2),
            "bad_review_count": int(bad or 0),
        }
        for pid, cnt, avg, bad in rows
    }

    data = _data(client.get("/api/products", headers=auth, params={"size": 100}))
    for item in data["items"]:
        stats = expected.get(item["id"])
        if stats is None:  # 没有评价的商品三项都是 0
            assert item["review_count"] == 0
            assert item["avg_rating"] == 0.0
            assert item["bad_review_count"] == 0
            continue
        assert item["review_count"] == stats["review_count"]
        assert item["avg_rating"] == pytest.approx(stats["avg_rating"])
        assert item["bad_review_count"] == stats["bad_review_count"]


def test_products_price_is_yuan(client, auth, session):
    """逐个核对：接口 price == price_cents / 100（两位小数）。"""
    from backend.app.models import Product

    rows = session.query(Product).order_by(Product.id).all()
    expected = {p.id: round(p.price_cents / 100, 2) for p in rows}

    data = _data(client.get("/api/products", headers=auth, params={"size": 100}))
    assert len(data["items"]) == len(expected)
    for item in data["items"]:
        assert item["price"] == pytest.approx(expected[item["id"]])


def test_products_filter_by_category(client, auth):
    all_data = _data(client.get("/api/products", headers=auth, params={"size": 100}))
    category = all_data["items"][0]["category"]

    data = _data(client.get("/api/products", headers=auth,
                            params={"category": category, "size": 100}))
    assert data["total"] >= 1
    assert all(item["category"] == category for item in data["items"])


def test_products_search_q(client, auth):
    data = _data(client.get("/api/products", headers=auth, params={"q": "耳机"}))
    assert data["total"] >= 1
    assert all("耳机" in item["name"] for item in data["items"])

    empty = _data(client.get("/api/products", headers=auth, params={"q": "不存在的商品XYZ"}))
    assert empty["total"] == 0
    assert empty["items"] == []


def test_products_size_capped_at_100(client, auth):
    data = _data(client.get("/api/products", headers=auth, params={"size": 1000}))
    assert data["size"] == 100


# ------------------------------------------------------------------ 订单
def test_orders_pagination(client, auth):
    data = _data(client.get("/api/orders", headers=auth, params={"page": 1, "size": 20}))
    _assert_page(data, total=49, page=1, size=20)
    assert len(data["items"]) == 20

    last = _data(client.get("/api/orders", headers=auth, params={"page": 3, "size": 20}))
    assert len(last["items"]) == 9


def test_orders_item_contract(client, auth):
    data = _data(client.get("/api/orders", headers=auth, params={"size": 1}))
    item = data["items"][0]

    assert set(item) == {"id", "order_no", "product_id", "product_name", "quantity",
                         "amount", "status", "buyer", "created_at"}
    assert item["status"] in ("paid", "cancelled", "refunded")
    assert isinstance(item["amount"], float), "金额出口必须是元"
    assert item["quantity"] >= 1
    assert item["product_name"], "product_name 由 join 商品表得到"
    assert DATETIME_RE.match(item["created_at"])


def test_orders_amount_is_yuan(client, auth, session):
    from backend.app.models import Order

    expected = {o.id: round(o.amount_cents / 100, 2)
                for o in session.query(Order).all()}
    data = _data(client.get("/api/orders", headers=auth, params={"size": 100}))
    for item in data["items"]:
        assert item["amount"] == pytest.approx(expected[item["id"]])


def test_orders_filter_by_status(client, auth):
    paid = _data(client.get("/api/orders", headers=auth,
                            params={"status": "paid", "size": 100}))
    assert paid["total"] == 48
    assert all(item["status"] == "paid" for item in paid["items"])

    cancelled = _data(client.get("/api/orders", headers=auth,
                                 params={"status": "cancelled", "size": 100}))
    assert cancelled["total"] == 1
    assert cancelled["items"][0]["amount"] == 89.0, "8900 分 = 89.0 元"


def test_orders_search_by_order_no(client, auth):
    first = _data(client.get("/api/orders", headers=auth, params={"size": 1}))["items"][0]
    order_no = first["order_no"]

    data = _data(client.get("/api/orders", headers=auth, params={"q": order_no}))
    assert data["total"] >= 1
    assert any(item["order_no"] == order_no for item in data["items"])


def test_orders_size_capped_at_100(client, auth):
    data = _data(client.get("/api/orders", headers=auth, params={"size": 999}))
    assert data["size"] == 100


# ------------------------------------------------------------------ 评价
def test_reviews_pagination(client, auth):
    data = _data(client.get("/api/reviews", headers=auth, params={"page": 1, "size": 20}))
    _assert_page(data, total=46, page=1, size=20)

    last = _data(client.get("/api/reviews", headers=auth, params={"page": 3, "size": 20}))
    assert len(last["items"]) == 6


def test_reviews_item_contract(client, auth):
    data = _data(client.get("/api/reviews", headers=auth, params={"size": 1}))
    item = data["items"][0]

    assert set(item) == {"id", "product_id", "product_name", "rating", "content",
                         "buyer", "has_image", "image_desc", "created_at"}
    assert 1 <= item["rating"] <= 5
    assert item["content"]
    assert item["product_name"]
    assert isinstance(item["has_image"], bool)
    assert DATETIME_RE.match(item["created_at"])


def test_reviews_filter_by_rating(client, auth):
    expected = {5: 10, 4: 9, 3: 9, 2: 8, 1: 10}
    for rating, count in expected.items():
        data = _data(client.get("/api/reviews", headers=auth,
                                params={"rating": rating, "size": 100}))
        assert data["total"] == count, f"{rating} 星应有 {count} 条"
        assert all(item["rating"] == rating for item in data["items"])

    bad = _data(client.get("/api/reviews", headers=auth, params={"rating": 2, "size": 100}))
    assert bad["total"] == 8


def test_reviews_search_content(client, auth):
    data = _data(client.get("/api/reviews", headers=auth, params={"q": "降噪"}))
    assert data["total"] >= 1
    assert all("降噪" in item["content"] for item in data["items"])


def test_reviews_size_capped_at_100(client, auth):
    data = _data(client.get("/api/reviews", headers=auth, params={"size": 500}))
    assert data["size"] == 100


# ------------------------------------------------------------------ 用户
def test_users_requires_admin(client, auth):
    data = _data(client.get("/api/users", headers=auth, params={"page": 1, "size": 20}))
    _assert_page(data, page=1, size=20)
    assert data["total"] >= 1

    usernames = [item["username"] for item in data["items"]]
    assert "admin" in usernames


def test_users_item_contract(client, auth):
    data = _data(client.get("/api/users", headers=auth))
    item = data["items"][0]

    assert set(item) == {"id", "username", "display_name", "role", "created_at"}
    assert "password_hash" not in item, "绝不能泄漏密码哈希"
    assert item["role"], "role 不能为空"
    assert DATETIME_RE.match(item["created_at"])


def test_users_requires_admin_role(client, auth):
    """非管理员访问 /api/users 必须 403（契约：该接口仅管理员）。"""
    login = client.post("/api/auth/login",
                        json={"username": "analyst", "password": "analyst123"})
    if login.status_code != 200:
        pytest.skip("种子数据里没有 analyst 账号，跳过非管理员权限校验")
    analyst_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    # 普通业务接口照样能访问
    assert client.get("/api/products", headers=analyst_headers).status_code == 200

    resp = client.get("/api/users", headers=analyst_headers)
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == 403
    assert body["message"] == "需要管理员权限"
    assert body["data"] is None


def test_users_pagination(client, auth):
    first = _data(client.get("/api/users", headers=auth, params={"page": 1, "size": 1}))
    assert first["size"] == 1
    assert len(first["items"]) == 1

    if first["total"] > 1:
        second = _data(client.get("/api/users", headers=auth,
                                  params={"page": 2, "size": 1}))
        assert second["items"][0]["id"] != first["items"][0]["id"]


# ------------------------------------------------------------------ 巡检
def test_inspect_writes_reports_and_alerts(client, auth, session):
    """一键巡检：只落诊断报告 / 通知两张表，不改商品与订单。

    注意：测试库是整个会话共享的，巡检会真的写库，因此本用例必须在 finally 里
    把自己造的 run / 报告 / 通知全部删掉，否则会污染 test_seed_invariants 的计数断言。
    """
    from sqlalchemy import func

    from backend.app.models import (AgentRun, DiagnosisReport, Notification, Order,
                                    Product, RunStep)

    products_before = {
        p.id: (p.price_cents, p.stock, p.status, p.pending_review)
        for p in session.query(Product).all()
    }
    orders_before = {o.id: o.status for o in session.query(Order).all()}
    runs_before = session.query(AgentRun).count()
    reports_before = session.query(DiagnosisReport).count()
    notes_before = session.query(Notification).count()
    max_run_id = session.query(func.max(AgentRun.id)).scalar() or 0
    max_report_id = session.query(func.max(DiagnosisReport.id)).scalar() or 0
    max_note_id = session.query(func.max(Notification.id)).scalar() or 0

    try:
        data = _data(client.post("/api/dashboard/inspect", headers=auth))
        assert set(data) == {"run_id", "report_count", "alert_count", "message"}
        assert data["message"] == "巡检完成"
        assert isinstance(data["run_id"], int)
        assert data["report_count"] >= 1
        assert data["alert_count"] <= data["report_count"]

        session.expire_all()
        assert session.query(AgentRun).count() == runs_before + 1
        assert session.query(DiagnosisReport).count() == reports_before + data["report_count"]
        assert session.query(Notification).count() == notes_before + data["alert_count"]

        run = session.get(AgentRun, data["run_id"])
        assert run is not None
        assert run.question == "系统一键巡检"
        assert run.route == "undetermined"
        assert run.status == "success"
        assert session.query(RunStep).filter(RunStep.run_id == run.id).count() == run.step_count
        assert run.step_count >= 1

        # 只读边界：商品价格/库存/上下架/待复核 与 订单状态一律不变
        for product in session.query(Product).all():
            assert (product.price_cents, product.stock, product.status,
                    product.pending_review) == products_before[product.id]
        for order in session.query(Order).all():
            assert order.status == orders_before[order.id]
    finally:
        # 回滚巡检写入（RunStep 由 cascade 一并删除）
        for model, max_id in ((RunStep, None), (AgentRun, max_run_id),
                              (DiagnosisReport, max_report_id),
                              (Notification, max_note_id)):
            if max_id is None:
                continue
            session.query(model).filter(model.id > max_id).delete(synchronize_session=False)
        session.query(RunStep).filter(
            RunStep.run_id > max_run_id
        ).delete(synchronize_session=False)
        session.commit()
        session.expire_all()
