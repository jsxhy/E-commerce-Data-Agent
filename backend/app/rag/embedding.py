"""中文向量化：bge-small-zh-v1.5 的 ONNX 推理（CLS pooling + L2 归一化）。

为什么不用 Chroma 自带的 ONNXMiniLM_L6_V2：MiniLM 是英文模型，对中文语义几乎无效
（实测「鞋子开胶」与「跑鞋脱胶」余弦距离 0.981，比无关文档还远）。bge-small-zh 是
中文小模型，94MB，纯 CPU 推理单条约 10ms，实测区分度良好。
"""
from __future__ import annotations

import pathlib
import threading
from typing import Sequence

import numpy as np

from backend.config import get_config, resolve_path

MODEL_FILENAME = "model.onnx"
TOKENIZER_FILENAME = "tokenizer.json"
MAX_LENGTH = 128


class EmbeddingUnavailable(RuntimeError):
    """模型文件缺失或 onnxruntime 不可用。"""


class BgeSmallZhEmbedding:
    """本地中文 embedding。首次调用时懒加载，进程内单例。"""

    def __init__(self, model_dir: pathlib.Path | None = None):
        cfg = get_config()["rag"]
        self.model_dir = pathlib.Path(model_dir or resolve_path(cfg["model_dir"]))
        self._session = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._dim: int | None = None

    # ------------------------------------------------------------ 加载
    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            model_path = self.model_dir / MODEL_FILENAME
            tok_path = self.model_dir / TOKENIZER_FILENAME
            if not model_path.exists() or not tok_path.exists():
                raise EmbeddingUnavailable(
                    f"缺少中文 embedding 模型：{model_path}（可执行 "
                    f"python backend/scripts/fetch_model.py 下载）")
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as exc:  # pragma: no cover
                raise EmbeddingUnavailable(f"缺少 onnxruntime/tokenizers：{exc}") from exc
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.log_severity_level = 3
            self._session = ort.InferenceSession(str(model_path), sess_options=opts,
                                                 providers=["CPUExecutionProvider"])
            self._tokenizer = Tokenizer.from_file(str(tok_path))
            self._tokenizer.enable_truncation(max_length=MAX_LENGTH)
            self._tokenizer.enable_padding(length=None)
            inputs = {i.name for i in self._session.get_inputs()}
            self._input_names = inputs
            self._dim = int(self._session.get_outputs()[0].shape[-1])

    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        return self._dim or 512

    # ------------------------------------------------------------ 推理
    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        encodings = self._tokenizer.encode_batch(list(texts))
        max_len = max(len(e.ids) for e in encodings) or 1
        input_ids = np.zeros((len(encodings), max_len), dtype=np.int64)
        attention = np.zeros((len(encodings), max_len), dtype=np.int64)
        token_types = np.zeros((len(encodings), max_len), dtype=np.int64)
        for row, enc in enumerate(encodings):
            ids = enc.ids[:max_len]
            input_ids[row, :len(ids)] = ids
            attention[row, :len(ids)] = 1
        feed: dict[str, np.ndarray] = {"input_ids": input_ids,
                                       "attention_mask": attention}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = token_types
        hidden = self._session.run(None, feed)[0]
        # CLS pooling（bge 系列用首 token 作为句向量）+ L2 归一化
        vectors = hidden[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms
        return vectors.tolist()


class _ChromaEmbeddingFunction:
    """Chroma 1.x 的 EmbeddingFunction 协议实现（name/__call__/get_config）。"""

    def __init__(self, inner: BgeSmallZhEmbedding):
        self._inner = inner

    def name(self) -> str:  # chroma 1.x 要求
        return "bge-small-zh-onnx"

    def __call__(self, input: Sequence[str]) -> list[list[float]]:  # noqa: A002
        return self._inner.encode(input)

    def get_config(self) -> dict:
        return {"model": "bge-small-zh-v1.5", "dim": 512}


_embedder: BgeSmallZhEmbedding | None = None


def get_embedder() -> BgeSmallZhEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = BgeSmallZhEmbedding()
    return _embedder


def chroma_embedding_function() -> _ChromaEmbeddingFunction:
    return _ChromaEmbeddingFunction(get_embedder())


def reset_embedder() -> None:
    global _embedder
    _embedder = None
