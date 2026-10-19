"""ORM 模型：所有金额字段以「分」存储，接口出口再转元。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db import Base


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # on_sale | off_sale
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="on_sale")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_desc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pending_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    reviews: Mapped[list["Review"]] = relationship(back_populates="product")
    orders: Mapped[list["Order"]] = relationship(back_populates="product")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # paid | cancelled | refunded
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="paid")
    buyer: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    product: Mapped[Product] = relationship(back_populates="orders")

    __table_args__ = (Index("ix_orders_created_at", "created_at"),)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    buyer: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_desc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    product: Mapped[Product] = relationship(back_populates="reviews")

    __table_args__ = (Index("ix_reviews_product_rating", "product_id", "rating"),)


class DiagnosisReport(Base):
    __tablename__ = "diagnosis_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # high | low
    risk: Mapped[str] = mapped_column(String(8), nullable=False, default="low")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON 文本（SQLite/MySQL 通用）
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    actions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    __table_args__ = (Index("ix_reports_created_at", "created_at"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # high | info
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="info")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # structured | semantic | undetermined
    route: Mapped[str] = mapped_column(String(16), nullable=False, default="undetermined")
    # success | failed | pending | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # up | down | None
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    steps: Mapped[list["RunStep"]] = relationship(
        back_populates="run", order_by="RunStep.seq", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_runs_created_at", "created_at"),)


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"),
                                        nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    run: Mapped[AgentRun] = relationship(back_populates="steps")

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_steps_seq"),)
