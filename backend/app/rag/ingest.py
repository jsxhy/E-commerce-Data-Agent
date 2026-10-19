"""把商品/评价写入向量库。

文档数固定 66 = 20 商品 + 46 评价（种子数据规模），测试据此断言。
- 商品文档文本：``名称 + 分类 + 描述``
- 评价文档文本：``content``，``has_image`` 时拼 ``image_desc``
  （``describe_image()`` 是预留钩子：默认透传库里已有的图片描述，接多模态模型时替换它；
  任何异常都被吞掉，降级为只用评价正文，不会因为图片描述缺失而丢文档）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from backend.app.models import Product, Review
from backend.app.rag.store import VectorStore, get_store

logger = logging.getLogger(__name__)

EXPECTED_PRODUCT_DOCS = 20
EXPECTED_REVIEW_DOCS = 46
EXPECTED_TOTAL_DOCS = EXPECTED_PRODUCT_DOCS + EXPECTED_REVIEW_DOCS


def describe_image(image_desc: str | None) -> str | None:
    """图片描述钩子。

    课程要求「图片描述 → 与正文合并 → 向量入库」：评价表里已经存好了 ``image_desc``
    （由运营/审核侧写入的自然语言描述），因此默认实现直接透传它，让带图评价的
    语义信息进入向量文本。接真实多模态模型时可以在这里替换成「对图片 URL 调视觉
    模型生成描述」，签名与返回值保持不变即可；任何异常都被吞掉并降级为 None
    （只用评价正文，不会因为图片描述缺失而丢文档）。
    """
    if image_desc is None:
        return None
    text = str(image_desc).strip()
    return text or None


def _safe_describe(image_desc: str | None) -> str | None:
    try:
        text = describe_image(image_desc)
    except Exception as exc:  # pragma: no cover - 钩子实现异常时才触发
        logger.warning("describe_image 失败，降级为只用评价正文：%s", exc)
        return None
    if text is None:
        return None
    text = str(text).strip()
    return text or None


def product_document(product: Product) -> dict[str, Any]:
    """商品 → 向量库文档。"""
    parts = [product.name or "", product.category or "", product.description or ""]
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return {
        "id": f"product-{product.id}",
        "text": text or (product.name or f"商品 {product.id}"),
        "metadata": {
            "source": "product",
            "ref_id": int(product.id),
            "product_id": int(product.id),
            "product_name": product.name or "",
        },
    }


def review_document(review: Review, product: Product | None = None) -> dict[str, Any]:
    """评价 → 向量库文档（有图时拼图片描述）。"""
    text = (review.content or "").strip()
    if review.has_image:
        described = _safe_describe(review.image_desc)
        if described:
            text = f"{text} {described}".strip()
    product_name = (product.name if product is not None else "") or ""
    metadata = {
        "source": "review",
        "ref_id": int(review.id),
        "product_id": int(review.product_id),
        "rating": int(review.rating),
        "product_name": product_name,
    }
    return {
        "id": f"review-{review.id}",
        "text": text or f"评价 {review.id}",
        "metadata": metadata,
    }


def build_documents(db: Session) -> list[dict[str, Any]]:
    """从数据库读出全部商品与评价，转成向量库文档列表。"""
    products = db.query(Product).order_by(Product.id).all()
    by_id = {int(p.id): p for p in products}
    docs: list[dict[str, Any]] = [product_document(p) for p in products]
    reviews = db.query(Review).order_by(Review.id).all()
    docs.extend(review_document(r, by_id.get(int(r.product_id))) for r in reviews)
    return docs


def ingest_all(db: Session, store: VectorStore | None = None,
               reset: bool = False,
               on_progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """把全部商品/评价写入向量库。幂等（upsert），可重复执行。

    返回 ``{"total": 66, "products": 20, "reviews": 46, "collection_size": 66}``。
    模型不可用时抛 ``EmbeddingUnavailable``，由调用方降级。
    """
    target = store or get_store()
    if reset:
        target.reset()
    docs = build_documents(db)
    product_count = sum(1 for d in docs if d["metadata"]["source"] == "product")
    review_count = len(docs) - product_count
    if on_progress is not None:
        on_progress(0, len(docs))
    written = target.add_documents(docs)
    if on_progress is not None:
        on_progress(written, len(docs))
    size = target.count()
    result = {
        "total": written,
        "products": product_count,
        "reviews": review_count,
        "collection_size": size,
    }
    logger.info("向量库写入完成：%s", json.dumps(result, ensure_ascii=False))
    return result


__all__ = [
    "EXPECTED_PRODUCT_DOCS", "EXPECTED_REVIEW_DOCS", "EXPECTED_TOTAL_DOCS",
    "describe_image", "product_document", "review_document", "build_documents", "ingest_all",
]
