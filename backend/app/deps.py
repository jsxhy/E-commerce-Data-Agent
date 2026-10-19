"""FastAPI 依赖：数据库会话、当前用户。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import User
from backend.app.security import decode_access_token

DbSession = Annotated[Session, Depends(get_db)]


def _extract_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip() or None


def get_current_user(
    db: DbSession,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    token = _extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    user = db.query(User).filter(User.username == payload["sub"]).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
