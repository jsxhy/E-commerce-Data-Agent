"""意图路由：纯规则，**不调用 LLM**。

为什么要纯规则：路由必须在 1ms 内给出稳定答案，且可被单元测试逐条钉住；
LLM 路由既慢又会抖动，还会在测试里烧配额。

优先级（从高到低）：
1. 商品名 / 订单号（``SO\\d+``） / 统计口径词 → ``structured``
2. 模糊评价主题词（脱胶、杂音、缩水…）且不含统计口径 → ``semantic``
3. 都不命中 → ``undetermined``

注意「静音蓝牙耳机最近差评集中在哪些问题？」同时含商品名与「哪些」，仍走 structured：
有结构化对象（商品/订单）时，先查库拿到差评明细，再由图里的 semantic_deepen 节点补语义证据。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from backend.app.constants import (
    ROUTE_SEMANTIC,
    ROUTE_STRUCTURED,
    ROUTE_UNDETERMINED,
)

# 统计口径词：出现即说明用户在要「数字结论」
STAT_WORDS: tuple[str, ...] = (
    "多少", "几个", "几笔", "几条", "几家", "几天", "几次",
    "总数", "总量", "合计", "总计", "销量", "销售额", "销售额度", "营业额", "客单价",
    "排名", "排行", "最高", "最低", "最多", "最少", "平均", "均值", "占比", "比例",
    "统计", "汇总", "分布", "趋势", "环比", "同比", "增长率", "数量",
    "how many", "count", "total", "average", "sum of", "top ",
)

# 模糊评价主题词：出现即说明用户在问「评价里提到的问题」
SEMANTIC_WORDS: tuple[str, ...] = (
    "脱胶", "开胶", "裂开", "断裂", "掉底",
    "杂音", "噪音", "电流声", "底噪", "破音",
    "缩水", "掉色", "褪色", "起球", "变形",
    "破损", "坏了", "坏掉", "异味", "味道大", "发霉",
    "质量", "做工", "瑕疵", "次品", "假货",
    "不舒服", "疼", "勒", "闷", "磨脚", "夹脚", "偏大", "偏小", "不合脚",
    "漏", "漏水", "漏电", "卡顿", "死机", "闪退", "断连", "连不上",
    "差评", "吐槽", "反馈", "问题", "缺点", "毛病", "抱怨", "不满",
    "退货", "退换", "售后", "客服", "物流慢", "发货慢",
)

# 明确的差评/负面信号（用于 has_bad_signal）
BAD_SIGNAL_WORDS: tuple[str, ...] = (
    "脱胶", "开胶", "裂开", "断裂", "杂音", "噪音", "缩水", "掉色", "褪色",
    "破损", "坏了", "坏掉", "异味", "发霉", "质量差", "太差", "很差", "垃圾",
    "不舒服", "疼", "漏", "卡顿", "死机", "闪退", "断连", "差评", "吐槽",
    "退货", "退款", "投诉", "不满", "踩雷", "翻车", "失望", "劝退", "问题",
    "划痕", "掉漆", "生锈", "虚标", "假货", "次品",
)

ORDER_NO_RE = re.compile(r"\bSO\d{6,}\b", re.I)
# 商品名末尾常见品类后缀，用于从自然语言里抠商品名
CATEGORY_SUFFIXES: tuple[str, ...] = (
    "耳机", "跑鞋", "运动鞋", "鞋", "T恤", "衬衫", "外套", "卫衣", "裤",
    "杯", "壶", "键盘", "鼠标", "充电宝", "数据线", "支架", "灯", "包",
    "手机壳", "贴膜", "音箱", "手表", "手环", "风扇", "锅", "刀",
)


def _contains_any(text: str, words: Iterable[str]) -> list[str]:
    lowered = (text or "").lower()
    return [w for w in words if w.lower() in lowered]


def match_stat_words(question: str) -> list[str]:
    return _contains_any(question, STAT_WORDS)


def match_semantic_words(question: str) -> list[str]:
    return _contains_any(question, SEMANTIC_WORDS)


def match_product_name(question: str, product_names: Sequence[str] | None = None) -> str | None:
    """返回问题里出现的商品名（最长匹配优先），没有则 None。"""
    return extract_product_name(question, product_names or ())


def has_order_no(question: str) -> bool:
    return bool(ORDER_NO_RE.search(question or ""))


def exact_product_name(question: str, product_names: Sequence[str]) -> str | None:
    """只在问题里**原样出现**商品名时返回（不做品类后缀猜测）。"""
    text = (question or "").strip()
    names = [str(n) for n in (product_names or ()) if n]
    matched = [n for n in names if n in text]
    return max(matched, key=len) if matched else None


def route_question(question: str, product_names: Sequence[str] | None = None) -> str:
    """规则路由：structured / semantic / undetermined。

    「精确商品名」与「品类后缀猜出来的商品名」区别对待，这是被真实数据教育出来的：

    - 「跑鞋脱胶」里的「跑鞋」会被后缀粗切匹配到商品「越野跑鞋」，但用户问的其实是
      评价里的脱胶问题（真正的证据属于商品「轻量跑步鞋」）。若因粗切就走结构化，
      会去查错商品的差评、并跳过语义检索 → 证据与结论都错。
    - 因此：粗切命中**只在没有语义主题词时**才升格为 structured；
      有语义主题词且无统计口径词时一律走 semantic（语义检索本来就能覆盖商品维度）。
    """
    text = (question or "").strip()
    if not text:
        return ROUTE_UNDETERMINED

    stat_hits = match_stat_words(text)
    exact = exact_product_name(text, product_names or ())
    semantic_hits = match_semantic_words(text)

    # 1) 结构化（强信号）：精确商品名 / 订单号 / 统计口径词
    if exact or has_order_no(text) or stat_hits:
        return ROUTE_STRUCTURED

    # 2) 语义（中信号）：模糊评价主题词，且不含统计口径
    if semantic_hits:
        return ROUTE_SEMANTIC

    # 3) 结构化（弱信号）：只有品类后缀猜出来的商品名，例如「耳机怎么样」
    if extract_product_name(text, product_names or ()):
        return ROUTE_STRUCTURED

    # 4) 未判定
    return ROUTE_UNDETERMINED


def extract_product_name(question: str, product_names: Sequence[str]) -> str | None:
    """从问题里抠出商品名。

    两级策略：
    1. 与已知商品名做包含匹配，取最长者（「静音蓝牙耳机」优先于「耳机」）；
    2. 没匹配上时按品类后缀做一次粗略切分（「跑鞋脱胶」→ 尝试「跑鞋」），
       仍匹配不到已知商品名就返回 None（宁可漏，不可错）。
    """
    text = (question or "").strip()
    if not text:
        return None
    names = [str(n) for n in (product_names or ()) if n]
    matched = [n for n in names if n in text]
    if matched:
        return max(matched, key=len)

    # 粗略切分：取品类后缀前 2~6 个字符，看是否等于/包含于某个商品名
    for suffix in CATEGORY_SUFFIXES:
        idx = text.find(suffix)
        if idx < 0:
            continue
        for start in range(max(0, idx - 4), idx + 1):
            candidate = text[start:idx + len(suffix)].strip("，。？！,?! 的")
            if not candidate:
                continue
            for name in names:
                if candidate and (candidate in name or name in candidate):
                    return name
    return None


def has_bad_signal(evidence: Any) -> bool:
    """判断证据里是否有差评信号。

    支持三种输入：
    - 证据列表（dict 或对象），看 ``rating`` <= 2 或 ``snippet/content`` 命中负面词
    - 单个 dict
    - 纯文本
    """
    if evidence is None:
        return False
    if isinstance(evidence, str):
        return bool(_contains_any(evidence, BAD_SIGNAL_WORDS))
    items = evidence if isinstance(evidence, (list, tuple, set)) else [evidence]
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            if _contains_any(item, BAD_SIGNAL_WORDS):
                return True
            continue
        getter = item.get if isinstance(item, dict) else (lambda k, d=None, _i=item: getattr(_i, k, d))
        rating = getter("rating")
        try:
            if rating is not None and int(rating) <= 2:
                return True
        except (TypeError, ValueError):
            pass
        snippet = getter("snippet") or getter("content") or getter("detail") or ""
        if isinstance(snippet, str) and _contains_any(snippet, BAD_SIGNAL_WORDS):
            return True
        # 语义检索的相关性：距离越近越像差评
        distance = getter("distance")
        if isinstance(distance, (int, float)) and distance is not None:
            try:
                if float(distance) <= 0.3 and rating is not None and int(rating) <= 2:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def count_bad_evidence(evidence: Any) -> int:
    """统计差评证据条数（rating <= 2）。"""
    items = evidence if isinstance(evidence, (list, tuple, set)) else [evidence]
    count = 0
    for item in items:
        if item is None:
            continue
        getter = item.get if isinstance(item, dict) else (lambda k, d=None, _i=item: getattr(_i, k, d))
        try:
            rating = getter("rating")
            if rating is not None and int(rating) <= 2:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


__all__ = [
    "STAT_WORDS", "SEMANTIC_WORDS", "BAD_SIGNAL_WORDS", "ORDER_NO_RE",
    "route_question", "extract_product_name", "exact_product_name",
    "has_bad_signal", "count_bad_evidence",
    "match_stat_words", "match_semantic_words", "match_product_name", "has_order_no",
]
