"""用户管理路由（仅管理员）。

契约：docs/api-contract.md §3
- GET /api/users?page=1&size=20
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.app.deps import AdminUser, DbSession
from backend.app.models import User
from backend.app.schemas import ok, page_payload
from backend.app.utils import fmt_dt, norm_page

router = APIRouter(prefix="/api/users", tags=["users"])


def user_item(user: User) -> dict:
    """契约 §3 的 users item 结构（不含 password_hash）。"""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "created_at": fmt_dt(user.created_at),
    }


@router.get("")
def list_users(db: DbSession, admin: AdminUser, page: int = 1, size: int = 20):
    page, size = norm_page(page, size)

    total = db.execute(select(func.count(User.id))).scalar() or 0
    rows = db.execute(
        select(User).order_by(User.id.asc()).offset((page - 1) * size).limit(size)
    ).scalars().all()

    return ok(page_payload([user_item(row) for row in rows], int(total), page, size))
