"""探测脚本：验证中文 embedding 的区分度（开发期自检用，不参与运行）。"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from backend.app.rag.embedding import get_embedder  # noqa: E402

QUERY = "鞋子开胶"
DOCS = [
    "跑了两次鞋底就开胶了，质量太差",
    "音质不错，佩戴舒服，续航也够用",
    "今天股价多少",
]


def main() -> None:
    emb = get_embedder()
    vectors = emb.encode([QUERY, *DOCS])
    print("dim =", len(vectors[0]))
    base = np.array(vectors[0])
    for doc, vec in zip(DOCS, vectors[1:]):
        dist = 1.0 - float(base @ np.array(vec))
        print(f"  distance {dist:.3f}  <- {doc}")


if __name__ == "__main__":
    main()
