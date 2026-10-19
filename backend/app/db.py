"""数据库引擎与会话。默认 SQLite；可经配置切到 MySQL 8。"""
from __future__ import annotations

import pathlib

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_config, resolve_path


def _prepare_url(url: str) -> str:
    """SQLite 相对路径转绝对路径；Windows 反斜杠统一成 /。"""
    if not url.startswith("sqlite"):
        return url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw = url[len(prefix):]
    if raw.startswith(":memory:"):
        return url
    path = resolve_path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return prefix + str(path).replace("\\", "/")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    cfg = get_config()
    url = _prepare_url(cfg["database"]["url"])
    echo = bool(cfg["database"].get("echo"))
    if url.startswith("sqlite"):
        engine = create_engine(url, echo=echo, future=True,
                               connect_args={"check_same_thread": False})
        _install_sqlite_pragmas(engine)
        return engine
    return create_engine(url, echo=echo, future=True, pool_pre_ping=True, pool_recycle=1800)


def _install_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False,
                                     autocommit=False, expire_on_commit=False,
                                     class_=Session)
    return _SessionLocal


def SessionLocal() -> Session:  # noqa: N802 - 兼作工厂使用
    return get_session_factory()()


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def init_db(drop: bool = False) -> None:
    from backend.app import models  # noqa: F401  确保模型已注册

    engine = get_engine()
    if drop:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def reset_engine() -> None:
    """测试用：丢弃缓存引擎（切换数据库后需调用）。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def db_path() -> pathlib.Path:
    url = _prepare_url(get_config()["database"]["url"])
    return pathlib.Path(url[len("sqlite:///"):])
