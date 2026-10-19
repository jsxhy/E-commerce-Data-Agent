"""密码哈希与 JWT。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from backend.config import get_config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    cfg = get_config()["auth"]
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=int(cfg["access_token_expire_minutes"]))
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, cfg["jwt_secret"], algorithm=cfg["jwt_algorithm"])


def decode_access_token(token: str) -> dict[str, Any] | None:
    cfg = get_config()["auth"]
    try:
        return jwt.decode(token, cfg["jwt_secret"], algorithms=[cfg["jwt_algorithm"]])
    except JWTError:
        return None
