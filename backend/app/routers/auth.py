"""鉴权路由：登录、当前用户信息。

契约：docs/api-contract.md §1
- POST /api/auth/login  请求 {username, password}，失败 401 "用户名或密码错误"
- GET  /api/auth/me     返回当前登录用户
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from backend.app.deps import CurrentUser, DbSession
from backend.app.models import User
from backend.app.schemas import LoginRequest, ok
from backend.app.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_brief(user: User) -> dict:
    """契约 §1 的 user 结构：id / username / display_name / role。"""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


@router.post("/login")
def login(payload: LoginRequest, db: DbSession):
    user = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 契约要求 HTTP 401 + 统一包裹体；这里直接返回，不依赖全局异常处理器
        return JSONResponse(
            status_code=401,
            content={"code": 401, "message": "用户名或密码错误", "data": None},
        )
    token = create_access_token(user.username, extra={"role": user.role})
    return ok({"token": token, "user": user_brief(user)})


@router.get("/me")
def me(user: CurrentUser):
    return ok(user_brief(user))
