"""Chroma 持久化向量库封装。

设计要点（都是本机踩过的坑）：

1. Chroma 1.x 的集合名只允许 ``[a-zA-Z0-9._-]{3,512}``，默认取配置里的 ``reviews_demo``。
2. ``backend.app.rag.embedding.chroma_embedding_function()`` 返回的 EF 只实现了
   ``__call__``，而 Chroma 1.5 的 ``collection.query(query_texts=...)`` 会调用
   ``embed_query``，直接用它查会抛 ``AttributeError``。该文件不允许改，所以这里包一层
   同时实现 ``__call__`` / ``embed_query`` 的子类（并补上 ``name`` / ``get_config`` /
   ``build_from_config``，否则集合元数据序列化会告警）。
3. 查询统一走 ``query_embeddings``（自己 encode），避免依赖 EF 协议细节。
4. 模型文件缺失时抛 :class:`EmbeddingUnavailable`，上层降级成空结果并标
   ``embedding_mode="unavailable"``，不能让整个接口 500。
5. 幂等：``collection.upsert``，id 由上层给（``product-{id}`` / ``review-{id}``）。
"""
from __future__ import annotations

import pathlib
import re
import threading
from typing import Any, Iterable, Sequence

from backend.app.rag.embedding import (
    EmbeddingUnavailable,
    chroma_embedding_function,
    get_embedder,
)
from backend.config import ensure_writable_tempdir, get_config, resolve_path

# embedding_mode 枚举（契约 §8；unavailable 为任务书要求的降级态）
EMBEDDING_MODE_ONNX = "bge-small-zh-onnx"
EMBEDDING_MODE_CHROMA = "chroma-default"
EMBEDDING_MODE_UNAVAILABLE = "unavailable"

COLLECTION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,512}$")
SNIPPET_MAX = 200
BATCH_SIZE = 64
MAX_TOP_K = 50


def _build_embedding_function():
    """包一层同时满足 Chroma 1.5 文档/查询两条路径的 EmbeddingFunction。"""
    from chromadb.api.types import Documents, EmbeddingFunction

    inner = chroma_embedding_function()

    class _BgeEmbeddingFunction(EmbeddingFunction[Documents]):
        def __init__(self, inner_fn=inner):
            self._inner = inner_fn

        def __call__(self, input):  # noqa: A002 - Chroma 协议签名
            return self._inner(input)

        def embed_query(self, input):  # noqa: A002 - Chroma 1.5 查询路径
            return self._inner(input)

        @staticmethod
        def name() -> str:
            return EMBEDDING_MODE_ONNX

        def get_config(self) -> dict[str, Any]:
            return self._inner.get_config()

        @staticmethod
        def build_from_config(config: dict[str, Any]):  # noqa: ARG004
            return _build_embedding_function()

    return _BgeEmbeddingFunction()


def _clean_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma 的 metadata 只接受 str/int/float/bool，None 必须剔除。"""
    out: dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            out[str(key)] = value
        else:
            out[str(key)] = str(value)
    return out


def _snippet(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= SNIPPET_MAX:
        return text
    return text[: SNIPPET_MAX - 1] + "…"


def _normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("doc 必须是 dict")
    doc_id = doc.get("id") or doc.get("doc_id")
    if not doc_id:
        raise ValueError("doc 缺少 id")
    text = doc.get("text") or doc.get("document") or ""
    if not str(text).strip():
        raise ValueError(f"doc {doc_id} 缺少正文")
    meta = _clean_metadata(dict(doc.get("metadata") or {}))
    # 便捷写法：顶层字段也接受（ingest 用 metadata 传，这里做兼容）
    for key in ("source", "ref_id", "product_id", "product_name", "rating"):
        if key not in meta and doc.get(key) is not None:
            meta[key] = _clean_metadata({key: doc[key]})[key]
    return {"id": str(doc_id), "text": str(text), "metadata": meta}


class VectorStore:
    """Chroma 向量库门面：懒加载、幂等写入、带元数据的相似度检索。"""

    def __init__(
        self,
        persist_dir: str | pathlib.Path | None = None,
        collection_name: str | None = None,
        embedding_mode: str = EMBEDDING_MODE_ONNX,
    ):
        cfg = get_config()["rag"]
        self.persist_dir = pathlib.Path(persist_dir) if persist_dir else resolve_path(cfg["persist_dir"])
        self.collection_name = collection_name or cfg.get("collection") or "reviews_demo"
        self.embedding_mode = embedding_mode
        self.default_top_k = int(cfg.get("top_k") or 5)
        self.threshold = float(cfg.get("relevant_threshold") or 0.5)
        self._client = None
        self._collection = None
        self._lock = threading.RLock()
        self._last_error: str | None = None

        if not COLLECTION_NAME_PATTERN.match(self.collection_name):
            raise ValueError(
                f"Chroma 集合名不合法：{self.collection_name!r}"
                "（只允许 [a-zA-Z0-9._-]，长度 3~512）")

    # ------------------------------------------------------------ 懒加载
    def _ensure_collection(self):
        if self._collection is not None:
            return self._collection
        with self._lock:
            if self._collection is not None:
                return self._collection
            try:
                # Chroma 的 Rust 绑定用 std::env::temp_dir() 建库；TMP/TEMP 若指向
                # 存在但不可写的目录会报「(code: 14) unable to open database file」。
                # 必须在 import chromadb 之前把临时目录修好（详见 config.py）。
                ensure_writable_tempdir()
                import chromadb
            except ImportError as exc:  # pragma: no cover - 依赖缺失才会走到
                raise EmbeddingUnavailable(f"chromadb 未安装：{exc}") from exc

            # 先确认模型可用：模型缺失时直接抛 EmbeddingUnavailable，
            # 避免集合建到一半、上层还要区分错误类型。
            try:
                get_embedder().dimension
            except EmbeddingUnavailable:
                raise
            except Exception as exc:  # pragma: no cover - onnxruntime 初始化异常
                raise EmbeddingUnavailable(f"中文 embedding 初始化失败：{exc}") from exc

            self.persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.persist_dir))
            collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=_build_embedding_function(),
            )
            self._client = client
            self._collection = collection
            return collection

    # ------------------------------------------------------------ 写入
    def add_documents(self, docs: Iterable[dict[str, Any]]) -> int:
        """幂等写入（upsert）。docs: [{id, text|document, metadata}]，返回写入条数。"""
        normalized = [_normalize_doc(doc) for doc in docs]
        if not normalized:
            return 0
        collection = self._ensure_collection()
        with self._lock:
            for start in range(0, len(normalized), BATCH_SIZE):
                batch = normalized[start:start + BATCH_SIZE]
                collection.upsert(
                    ids=[d["id"] for d in batch],
                    documents=[d["text"] for d in batch],
                    metadatas=[d["metadata"] for d in batch],
                )
        return len(normalized)

    # ------------------------------------------------------------ 查询
    def search(self, query: str, top_k: int | None = None,
               where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """相似度检索。返回 [{source, ref_id, product_id, product_name, rating, snippet, distance}]。"""
        text = (query or "").strip()
        if not text:
            return []
        collection = self._ensure_collection()
        n_results = max(1, min(int(top_k or self.default_top_k), MAX_TOP_K))
        try:
            vector = get_embedder().encode([text])[0]
            result = collection.query(
                query_embeddings=[vector],
                n_results=n_results,
                where=where or None,
                include=["documents", "metadatas", "distances"],
            )
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            message = str(exc)
            if "dimension" in message.lower() or "embedding" in message.lower():
                raise EmbeddingUnavailable(f"向量检索失败（embedding 不可用）：{message}") from exc
            raise

        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        items: list[dict[str, Any]] = []
        for idx, doc_id in enumerate(ids):
            meta = metas[idx] if idx < len(metas) and metas[idx] else {}
            distance = dists[idx] if idx < len(dists) else None
            items.append({
                "source": str(meta.get("source") or self._source_from_id(doc_id)),
                "ref_id": self._as_int(meta.get("ref_id"), doc_id),
                "product_id": self._as_int(meta.get("product_id"), None),
                "product_name": str(meta.get("product_name") or ""),
                "rating": self._as_int(meta.get("rating"), None),
                "snippet": _snippet(docs[idx] if idx < len(docs) else ""),
                "distance": round(float(distance), 3) if distance is not None else None,
            })
        return items

    @staticmethod
    def _source_from_id(doc_id: str) -> str:
        return "product" if str(doc_id).startswith("product-") else "review"

    @staticmethod
    def _as_int(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    # ------------------------------------------------------------ 运维
    def count(self) -> int:
        try:
            return int(self._ensure_collection().count())
        except EmbeddingUnavailable:
            return 0

    def reset(self) -> None:
        """删除并重建集合（下次访问时重建）。"""
        with self._lock:
            if self._collection is None:
                # 没初始化过也要把磁盘上的同名集合删掉
                try:
                    self._ensure_collection()
                except EmbeddingUnavailable:
                    raise
            try:
                self._client.delete_collection(self.collection_name)
            except Exception:
                # 集合不存在时 delete 会抛，忽略即可
                pass
            self._collection = None

    def health(self) -> dict[str, Any]:
        """给 /api/health 用的轻量探针，不抛异常。"""
        try:
            count = self.count()
        except EmbeddingUnavailable as exc:
            self._last_error = str(exc)
            return {"status": "unavailable", "mode": EMBEDDING_MODE_UNAVAILABLE,
                    "collection": self.collection_name, "count": 0, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - 磁盘/权限异常
            self._last_error = str(exc)
            return {"status": "unavailable", "mode": EMBEDDING_MODE_UNAVAILABLE,
                    "collection": self.collection_name, "count": 0, "error": str(exc)}
        return {"status": "ok", "mode": self.embedding_mode,
                "collection": self.collection_name, "count": count}

    def collection_size(self) -> int:
        return self.count()

    # 便于测试替换
    @property
    def last_error(self) -> str | None:
        return self._last_error


_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = VectorStore()
    return _store


def set_store_for_test(store: VectorStore | None) -> None:
    global _store
    _store = store


def reset_store() -> None:
    global _store
    _store = None


__all__ = [
    "EMBEDDING_MODE_ONNX", "EMBEDDING_MODE_CHROMA", "EMBEDDING_MODE_UNAVAILABLE",
    "EmbeddingUnavailable", "VectorStore", "get_store", "set_store_for_test", "reset_store",
]
