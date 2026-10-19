"""订单数据路由。

契约：docs/api-contract.md §3
- GET /api/orders?page=1&size=20&q=&status=
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.app.deps import CurrentUser, DbSession
from backend.app.models import Order, Product
from backend.app.schemas import ok, page_payload
from backend.app.utils import fmt_dt, like_pattern, norm_page, yuan

router = APIRouter(prefix="/api/orders", tags=["orders"])


def order_item(order: Order, product_name: str) -> dict:
    """契约 §3 的 orders item 结构（金额出口转元）。"""
    return {
        "id": order.id,
        "order_no": order.order_no,
        "product_id": order.product_id,
        "product_name": product_name,
        "quantity": order.quantity,
        "amount": yuan(order.amount_cents),
        "status": order.status,
        "buyer": order.buyer,
        "created_at": fmt_dt(order.created_at),
    }


@router.get("")
def list_orders(db: DbSession, user: CurrentUser,
                page: int = 1, size: int = 20,
                q: str = "", status: str = ""):
    page, size = norm_page(page, size)

    conditions = []
    pattern = like_pattern(q)
    if pattern:
        conditions.append(Order.order_no.like(pattern))
    if status:
        conditions.append(Order.status == status)

    total = db.execute(
        select(func.count(Order.id)).where(*conditions)
    ).scalar() or 0

    rows = db.execute(
        select(Order, Product.name)
        .join(Product, Product.id == Order.product_id)
        .where(*conditions)
        .order_by(Order.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    items = [order_item(order, name) for order, name in rows]
    return ok(page_payload(items, int(total), page, size))
