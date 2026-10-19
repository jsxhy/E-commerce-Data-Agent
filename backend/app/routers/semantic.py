"""语义检索路由。

契约：docs/api-contract.md §8
- GET /api/semantic/search?q=跑鞋脱胶&top_k=5

降级约定：embedding 模型或向量库不可用时**不报错**，返回 HTTP 200 + ``code=0``，
``embedding_mode="unavailable"``、``hit_count=0``、``collection_size=0``、``items=[]``，
由前端提示「向量检索不可用」。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.app.deps import CurrentUser
from backend.app.rag.store import (EMBEDDING_MODE_UNAVAILABLE, MAX_TOP_K,
                                   EmbeddingUnavailable, get_store)
from backend.app.schemas import ok
from backend.config import get_config

router = APIRouter(prefix="/api/semantic", tags=["semantic"])

DEFAULT_TOP_K = 5


def _rag_cfg() -> dict:
    return dict(get_config().get("rag") or {})


def _resolve_top_k(top_k: int | None) -> int:
    """归一化 top_k：缺省取配置，夹在 1..MAX_TOP_K。"""
    raw = top_k if top_k is not None else int(_rag_cfg().get("top_k") or DEFAULT_TOP_K)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_TOP_K
    return max(1, min(value, MAX_TOP_K))


@router.get("/search")
def semantic_search(user: CurrentUser, q: str = "", top_k: int | None = None):
    query = (q or "").strip()
    if not query:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "请输入检索内容", "data": None},
        )

    cfg = _rag_cfg()
    threshold = float(cfg.get("relevant_threshold") or 0.5)
    k = _resolve_top_k(top_k)
    store = get_store()

    payload = {
        "query": query,
        "top_k": k,
        "hit_count": 0,
        "collection_size": 0,
        "embedding_mode": store.embedding_mode,
        "threshold": threshold,
        "items": [],
    }

    try:
        collection_size = store.count()
        raw_items = store.search(query, top_k=k)
    except EmbeddingUnavailable:
        payload["embedding_mode"] = EMBEDDING_MODE_UNAVAILABLE
        return ok(payload)
    except Exception:  # noqa: BLE001 - 向量库任何异常都按降级态返回
        payload["embedding_mode"] = EMBEDDING_MODE_UNAVAILABLE
        return ok(payload)

    items = []
    for rank, item in enumerate(raw_items, start=1):
        distance = item.get("distance")
        relevant = bool(isinstance(distance, (int, float)) and float(distance) <= threshold)
        items.append({
            "rank": rank,
            "source": item.get("source") or "review",
            "ref_id": item.get("ref_id"),
            "product_id": item.get("product_id"),
            "product_name": item.get("product_name") or "",
            "rating": item.get("rating"),
            "snippet": item.get("snippet") or "",
            "distance": distance,
            "relevant": relevant,
        })

    payload.update({
        "hit_count": len(items),
        "collection_size": int(collection_size or 0),
        "embedding_mode": store.embedding_mode,
        "items": items,
    })
    return ok(payload)
