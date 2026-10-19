"""商品数据路由。

契约：docs/api-contract.md §3
- GET /api/products?page=1&size=20&q=&category=
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import case, func, select

from backend.app.deps import CurrentUser, DbSession
from backend.app.models import Product, Review
from backend.app.schemas import ok, page_payload
from backend.app.utils import fmt_dt, like_pattern, norm_page, yuan
from backend.config import get_config

router = APIRouter(prefix="/api/products", tags=["products"])


def _bad_rating_max() -> int:
    rules = get_config().get("agent", {}).get("risk", {})
    return int(rules.get("bad_rating_max", 2))


def _review_stats(db) -> dict[int, dict]:
    """一次性取全部商品的评价聚合：条数、均分、差评数。"""
    bad_max = _bad_rating_max()
    bad_count = func.sum(case((Review.rating <= bad_max, 1), else_=0))
    rows = db.execute(
        select(Review.product_id,
               func.count(Review.id),
               func.avg(Review.rating),
               bad_count)
        .group_by(Review.product_id)
    ).all()
    return {
        pid: {
            "review_count": int(cnt or 0),
            "avg_rating": round(float(avg), 2) if avg is not None else 0.0,
            "bad_review_count": int(bad or 0),
        }
        for pid, cnt, avg, bad in rows
    }


def product_item(product: Product, stats: dict | None = None) -> dict:
    """契约 §3 的 products item 结构（金额出口转元）。"""
    stats = stats or {}
    return {
        "id": product.id,
        "name": product.name,
        "category": product.category,
        "price": yuan(product.price_cents),
        "stock": product.stock,
        "status": product.status,
        "review_count": stats.get("review_count", 0),
        "avg_rating": stats.get("avg_rating", 0.0),
        "bad_review_count": stats.get("bad_review_count", 0),
        "pending_review": bool(product.pending_review),
        "created_at": fmt_dt(product.created_at),
    }


@router.get("")
def list_products(db: DbSession, user: CurrentUser,
                  page: int = 1, size: int = 20,
                  q: str = "", category: str = ""):
    page, size = norm_page(page, size)

    conditions = []
    pattern = like_pattern(q)
    if pattern:
        conditions.append(Product.name.like(pattern))
    if category:
        conditions.append(Product.category == category)

    total = db.execute(
        select(func.count(Product.id)).where(*conditions)
    ).scalar() or 0

    rows = db.execute(
        select(Product)
        .where(*conditions)
        .order_by(Product.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).scalars().all()

    stats = _review_stats(db)
    items = [product_item(row, stats.get(row.id)) for row in rows]
    return ok(page_payload(items, int(total), page, size))
