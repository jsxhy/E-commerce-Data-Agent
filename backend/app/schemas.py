"""Pydantic 请求模型与统一响应工具。"""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def page_payload(items: list[Any], total: int, page: int, size: int) -> dict[str, Any]:
    return {"items": items, "total": total, "page": page, "size": size}


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    size: int = 20


# ---------------------------------------------------------------- 鉴权

class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------- 对话

class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None


class ConfirmRequest(BaseModel):
    run_id: int
    decision: Literal["approve", "reject"]


class FeedbackRequest(BaseModel):
    value: Literal["up", "down"] | None = None
