"""意图路由单元测试（纯规则，不依赖 LLM）。

这些用例锁住的是「真实数据教出来的经验」：一旦有人把优先级改回去，
「跑鞋脱胶」会去查错商品的差评，语义检索被跳过，整条链路结论都会错。
"""
from __future__ import annotations

import pytest

from backend.app.agent import intent
from backend.app.constants import (ROUTE_SEMANTIC, ROUTE_STRUCTURED,
                                   ROUTE_UNDETERMINED)

#: 与 seed.py 商品名保持一致（顺序无关，路由只做包含匹配）
PRODUCT_NAMES = [
    "静音蓝牙耳机", "轻量跑步鞋", "纯棉基础款T恤", "不锈钢保温杯", "无线降噪头戴耳机",
    "天然乳胶枕", "快干运动短裤", "陶瓷马克杯", "加厚法兰绒睡衣", "便携榨汁杯",
    "真丝眼罩", "帆布托特包", "竹纤维洗碗巾", "智能手环", "越野跑鞋",
    "磨砂玻璃水壶", "硅胶折叠水杯", "记忆棉坐垫", "蓝牙音箱", "针织开衫",
]


# ------------------------------------------------------------------ 路由
@pytest.mark.parametrize("question", [
    "静音蓝牙耳机最近差评集中在哪些问题？",
    "轻量跑步鞋的评分怎么样",
    "越野跑鞋还有库存吗",
])
def test_exact_product_name_routes_structured(question):
    assert intent.route_question(question, PRODUCT_NAMES) == ROUTE_STRUCTURED


@pytest.mark.parametrize("question", [
    "这个月销售额是多少",
    "一共有多少个订单",
    "销量排名前五的商品",
    "各品类的平均客单价",
    "差评占比是多少",
    "帮我统计一下订单总数",
])
def test_stat_words_route_structured(question):
    assert intent.route_question(question, PRODUCT_NAMES) == ROUTE_STRUCTURED


@pytest.mark.parametrize("question", [
    "SO123456 这单到哪了",
    "订单 SO202410190001 是什么状态",
])
def test_order_no_routes_structured(question):
    assert intent.route_question(question, PRODUCT_NAMES) == ROUTE_STRUCTURED


@pytest.mark.parametrize("question", [
    "跑鞋脱胶",
    "耳机有杂音",
    "洗了缩水",
    "包装破损",
    "用户吐槽质量差",
    "有没有异味",
    "戴着不舒服",
])
def test_semantic_topics_route_semantic(question):
    assert intent.route_question(question, PRODUCT_NAMES) == ROUTE_SEMANTIC


def test_semantic_topic_wins_over_category_suffix_guess():
    """「跑鞋脱胶」里的「跑鞋」会被粗切成「越野跑鞋」，但语义词优先。"""
    assert intent.route_question("跑鞋脱胶", PRODUCT_NAMES) == ROUTE_SEMANTIC
    # 对照：没有语义词时，粗切才升格为 structured
    assert intent.route_question("跑鞋怎么样", PRODUCT_NAMES) == ROUTE_STRUCTURED


def test_stat_word_beats_semantic_word():
    """「差评占比是多少」既有语义词又有统计口径 → 结构化。"""
    assert intent.route_question("差评占比是多少", PRODUCT_NAMES) == ROUTE_STRUCTURED


@pytest.mark.parametrize("question", [
    "",
    "   ",
    "你好",
    "今天天气不错",
    "帮我看看",
])
def test_unmatched_routes_undetermined(question):
    assert intent.route_question(question, PRODUCT_NAMES) == ROUTE_UNDETERMINED


def test_route_question_works_without_product_names():
    assert intent.route_question("这个月销售额是多少") == ROUTE_STRUCTURED
    assert intent.route_question("耳机有杂音") == ROUTE_SEMANTIC


def test_route_question_none_input_is_safe():
    assert intent.route_question(None) == ROUTE_UNDETERMINED  # type: ignore[arg-type]


# ------------------------------------------------------------------ 商品名提取
def test_exact_product_name_requires_verbatim_match():
    assert intent.exact_product_name("静音蓝牙耳机最近怎么样", PRODUCT_NAMES) == "静音蓝牙耳机"
    # 只说品类不说全名 → 精确匹配为 None
    assert intent.exact_product_name("耳机怎么样", PRODUCT_NAMES) is None


def test_exact_product_name_prefers_longest():
    assert intent.exact_product_name(
        "无线降噪头戴耳机和静音蓝牙耳机比", PRODUCT_NAMES) in PRODUCT_NAMES


def test_extract_product_name_falls_back_to_category_suffix():
    assert intent.extract_product_name("跑鞋脱胶", PRODUCT_NAMES) in ("越野跑鞋", "轻量跑步鞋")


def test_extract_product_name_returns_none_for_unrelated_question():
    assert intent.extract_product_name("这个月销售额是多少", PRODUCT_NAMES) is None


def test_extract_product_name_handles_empty_input():
    assert intent.extract_product_name("", PRODUCT_NAMES) is None
    assert intent.extract_product_name("耳机", []) is None


def test_match_product_name_is_alias_of_extract():
    assert intent.match_product_name("静音蓝牙耳机", PRODUCT_NAMES) == "静音蓝牙耳机"


# ------------------------------------------------------------------ 差评信号
def test_has_bad_signal_by_rating():
    assert intent.has_bad_signal([{"rating": 1, "snippet": "还行"}]) is True
    assert intent.has_bad_signal([{"rating": 2, "snippet": "一般"}]) is True
    assert intent.has_bad_signal([{"rating": 5, "snippet": "很好"}]) is False


def test_has_bad_signal_by_keyword_even_without_rating():
    assert intent.has_bad_signal([{"snippet": "鞋底开胶了"}]) is True
    assert intent.has_bad_signal([{"snippet": "音质很棒"}]) is False


def test_has_bad_signal_accepts_text_and_single_dict():
    assert intent.has_bad_signal("耳机有杂音") is True
    assert intent.has_bad_signal("物流很快") is False
    assert intent.has_bad_signal({"rating": 1}) is True
    assert intent.has_bad_signal(None) is False
    assert intent.has_bad_signal([]) is False


def test_has_bad_signal_accepts_objects_with_attributes():
    class _Review:
        rating = 1
        snippet = ""

    assert intent.has_bad_signal([_Review()]) is True


def test_count_bad_evidence():
    evidence = [{"rating": 1}, {"rating": 2}, {"rating": 5}, {"rating": None}, {"snippet": "x"}]
    assert intent.count_bad_evidence(evidence) == 2
    assert intent.count_bad_evidence([]) == 0
    assert intent.count_bad_evidence(None) == 0


# ------------------------------------------------------------------ 词表与正则
def test_stat_words_cover_contract_terms():
    for word in ("多少", "几个", "总数", "合计", "销量", "销售额", "排名",
                 "最高", "最低", "平均", "占比", "统计", "汇总"):
        assert word in intent.STAT_WORDS, word


def test_semantic_words_cover_contract_terms():
    for word in ("脱胶", "开胶", "杂音", "噪音", "缩水", "掉色", "破损", "坏了",
                 "异味", "质量", "不舒服", "疼", "漏", "卡顿", "差评", "吐槽",
                 "反馈", "问题"):
        assert word in intent.SEMANTIC_WORDS, word


def test_match_helpers_return_hit_lists():
    assert intent.match_stat_words("有多少订单") == ["多少"]
    assert intent.match_semantic_words("耳机有杂音") == ["杂音"]
    assert intent.match_stat_words("耳机有杂音") == []


def test_has_order_no():
    assert intent.has_order_no("SO123456") is True
    assert intent.has_order_no("订单号 SO202410190001 查一下") is True
    assert intent.has_order_no("订单 SO12") is False
    assert intent.has_order_no("没有订单号") is False
