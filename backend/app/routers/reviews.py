"""评价数据路由。

契约：docs/api-contract.md §3
- GET /api/reviews?page=1&size=20&q=&rating=
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.app.deps import CurrentUser, DbSession
from backend.app.models import Product, Review
from backend.app.schemas import ok, page_payload
from backend.app.utils import fmt_dt, like_pattern, norm_page

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def review_item(review: Review, product_name: str) -> dict:
    """契约 §3 的 reviews item 结构。"""
    return {
        "id": review.id,
        "product_id": review.product_id,
        "product_name": product_name,
        "rating": review.rating,
        "content": review.content,
        "buyer": review.buyer,
        "has_image": bool(review.has_image),
        "image_desc": review.image_desc,
        "created_at": fmt_dt(review.created_at),
    }


@router.get("")
def list_reviews(db: DbSession, user: CurrentUser,
                 page: int = 1, size: int = 20,
                 q: str = "", rating: int | None = None):
    page, size = norm_page(page, size)

    conditions = []
    pattern = like_pattern(q)
    if pattern:
        conditions.append(Review.content.like(pattern))
    if rating is not None:
        conditions.append(Review.rating == rating)

    total = db.execute(
        select(func.count(Review.id)).where(*conditions)
    ).scalar() or 0

    rows = db.execute(
        select(Review, Product.name)
        .join(Product, Product.id == Review.product_id)
        .where(*conditions)
        .order_by(Review.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    items = [review_item(review, name) for review, name in rows]
    return ok(page_payload(items, int(total), page, size))
