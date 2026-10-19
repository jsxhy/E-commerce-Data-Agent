"""阈值校准：在真实语料上观察正/负样本的余弦距离分布。"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from backend.app.rag.embedding import get_embedder  # noqa: E402

QUERIES = ["跑鞋脱胶", "耳机有杂音", "洗了缩水", "包装破损"]
DOCS = [
    ("跑鞋", "跑了两次鞋底就开胶了，质量太差"),
    ("跑鞋", "缓震不错，日常慢跑很舒服，鞋面透气"),
    ("耳机", "戴半小时耳朵疼，降噪也一般，还总有杂音"),
    ("耳机", "音质不错，佩戴舒服，续航也够用"),
    ("衣服", "第一次洗就缩水了，袖子短了一截"),
    ("衣服", "面料舒服，版型也好看，回购了"),
    ("包装", "外包装被压扁了，盒子破损，里面东西没事"),
    ("包装", "发货很快，包装完好，物流给力"),
    ("无关", "今天股价多少"),
    ("无关", "明天天气怎么样"),
]


def main() -> None:
    emb = get_embedder()
    qv = emb.encode(QUERIES)
    dv = emb.encode([d for _, d in DOCS])
    print(f"{'query':10s} " + " ".join(f"{d[:6]:>8s}" for _, d in DOCS))
    for q, vec in zip(QUERIES, qv):
        base = np.array(vec)
        row = [1.0 - float(base @ np.array(v)) for v in dv]
        print(f"{q:10s} " + " ".join(f"{x:8.3f}" for x in row))
    print()
    # 正样本（同品类）与负样本的距离分布
    pos, neg = [], []
    for qi, q in enumerate(QUERIES):
        for di, (cat, _) in enumerate(DOCS):
            dist = 1.0 - float(np.array(qv[qi]) @ np.array(dv[di]))
            expect = (q[:2] in cat) or (cat in ("跑鞋",) and q.startswith("跑鞋"))
            (pos if expect else neg).append(dist)
    print(f"pos min/mean/max = {min(pos):.3f}/{np.mean(pos):.3f}/{max(pos):.3f}")
    print(f"neg min/mean/max = {min(neg):.3f}/{np.mean(neg):.3f}/{max(neg):.3f}")


if __name__ == "__main__":
    main()
