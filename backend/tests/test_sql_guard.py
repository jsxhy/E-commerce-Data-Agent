"""sql_guard 单元测试：只读白名单、注入防护、行数截断、失败重试。

这里**不碰真实 LLM**：`run_sql_with_retry` 的 llm 参数接受任意带
``generate_sql()`` 的对象，用本地 stub 就能覆盖「重试/放弃」全部分支。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.app.agent import sql_guard as sg

ALLOWED = ("products", "orders", "reviews", "diagnosis_reports")


# ------------------------------------------------------------------ 注释与表名
def test_strip_comments_removes_line_and_block_comments():
    sql = "SELECT id FROM products -- 只看 id\n/* 块注释 */ WHERE id > 1"
    cleaned = sg.strip_comments(sql)
    assert "--" not in cleaned
    assert "/*" not in cleaned
    assert "FROM products" in cleaned


def test_strip_comments_keeps_comment_like_text_inside_string_literal():
    sql = "SELECT id FROM products WHERE name = 'a--b'"
    assert "a--b" in sg.strip_comments(sql)


def test_extract_tables_from_join_and_comma_list():
    sql = ("SELECT p.id, r.rating FROM products p "
           "JOIN reviews r ON r.product_id = p.id, orders o WHERE o.id = 1")
    tables = sg.extract_tables(sql)
    assert "products" in tables
    assert "reviews" in tables
    assert "orders" in tables


def test_extract_tables_handles_schema_prefix_and_quotes():
    sql = 'SELECT * FROM "main".products'
    assert "products" in sg.extract_tables(sql)


def test_extract_tables_stops_at_subquery_paren():
    sql = "SELECT * FROM (SELECT id FROM products) t"
    # 括号里的子查询不应被当作主表名解析出奇怪的值
    tables = sg.extract_tables(sql)
    assert all(t for t in tables)


# ------------------------------------------------------------------ 合法查询
def test_validate_accepts_plain_select_and_appends_limit():
    ok, normalized = sg.validate_sql("SELECT id, name FROM products",
                                     allowed_tables=ALLOWED, max_rows=50)
    assert ok is True
    assert normalized.endswith("LIMIT 50")
    assert normalized.startswith("SELECT")


def test_validate_keeps_existing_limit():
    ok, normalized = sg.validate_sql("SELECT id FROM products LIMIT 5",
                                     allowed_tables=ALLOWED, max_rows=50)
    assert ok is True
    assert normalized.count("LIMIT") == 1
    assert normalized.endswith("LIMIT 5")


def test_validate_does_not_confuse_subquery_limit_with_top_level_limit():
    sql = "SELECT * FROM products WHERE id IN (SELECT product_id FROM reviews LIMIT 3)"
    ok, normalized = sg.validate_sql(sql, allowed_tables=ALLOWED, max_rows=50)
    assert ok is True
    assert normalized.endswith("LIMIT 50")


def test_validate_tolerates_trailing_semicolon():
    ok, normalized = sg.validate_sql("SELECT id FROM products;",
                                     allowed_tables=ALLOWED, max_rows=20)
    assert ok is True
    assert normalized.endswith("LIMIT 20")


# ------------------------------------------------------------------ 拒绝场景
@pytest.mark.parametrize("sql", [
    "",
    "   ",
    "-- 只有注释",
])
def test_validate_rejects_empty(sql):
    ok, error = sg.validate_sql(sql, allowed_tables=ALLOWED)
    assert ok is False
    assert error


@pytest.mark.parametrize("keyword", [
    "INSERT INTO products (name) VALUES ('x')",
    "UPDATE products SET price_cents = 1",
    "DELETE FROM reviews",
    "DROP TABLE products",
    "ALTER TABLE products ADD COLUMN x INT",
    "CREATE TABLE evil (id INT)",
    "PRAGMA table_info(products)",
    "ATTACH DATABASE 'x.db' AS x",
    "VACUUM",
    "REPLACE INTO products (id) VALUES (1)",
])
def test_validate_rejects_write_statements(keyword):
    ok, error = sg.validate_sql(keyword, allowed_tables=ALLOWED)
    assert ok is False, keyword
    assert error


def test_validate_rejects_multi_statement_injection():
    ok, error = sg.validate_sql("SELECT id FROM products; DROP TABLE products",
                                allowed_tables=ALLOWED)
    assert ok is False
    assert "单条" in error or "分号" in error


def test_validate_rejects_select_with_into_outfile():
    ok, error = sg.validate_sql("SELECT id INTO OUTFILE '/tmp/x' FROM products",
                                allowed_tables=ALLOWED)
    assert ok is False


def test_validate_rejects_table_outside_whitelist():
    ok, error = sg.validate_sql("SELECT * FROM users", allowed_tables=ALLOWED)
    assert ok is False
    assert "users" in error


def test_validate_rejects_query_without_table():
    ok, error = sg.validate_sql("SELECT 1", allowed_tables=ALLOWED)
    assert ok is False


def test_validate_ignores_keywords_inside_string_literal():
    """字面量里的 update/drop 不该触发关键字拦截。"""
    sql = "SELECT id FROM products WHERE name = 'update drop delete'"
    ok, normalized = sg.validate_sql(sql, allowed_tables=ALLOWED, max_rows=10)
    assert ok is True
    assert normalized.endswith("LIMIT 10")


# ------------------------------------------------------------------ 执行
def test_execute_sql_returns_columns_and_rows(session):
    result = sg.execute_sql(session, "SELECT id, name FROM products ORDER BY id LIMIT 3",
                            max_rows=10)
    assert result["ok"] is True
    assert result["row_count"] == 3
    assert "id" in result["columns"]
    assert "name" in result["columns"]
    assert isinstance(result["rows"][0], dict)


def test_execute_sql_truncates_rows_to_max_rows(session):
    result = sg.execute_sql(session, "SELECT id FROM products", max_rows=5)
    assert result["ok"] is True
    assert result["row_count"] == 5


def test_execute_sql_returns_error_instead_of_raising(session):
    result = sg.execute_sql(session, "SELECT no_such_column FROM products")
    assert result["ok"] is False
    assert result["error"]
    # 失败后会话仍可用
    assert session.execute(text("SELECT 1")).scalar() == 1


def test_execute_sql_timeout_message_is_explicit(session):
    """超时错误文本要能被上层直接透传给用户。"""
    result = sg.execute_sql(
        session,
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
        "SELECT count(*) FROM c",
        timeout_seconds=0.2,
    )
    assert result["ok"] is False
    assert "超时" in result["error"] or "interrupt" in result["error"].lower()


# ------------------------------------------------------------------ 重试
class _StubLLM:
    """按预设脚本依次返回 SQL 文本。"""

    def __init__(self, scripts: list[str]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    def generate_sql(self, question, error=None, schema=None):  # noqa: ANN001
        self.calls.append({"question": question, "error": error})
        return self.scripts.pop(0) if self.scripts else ""


def test_run_sql_with_retry_succeeds_first_try(session):
    llm = _StubLLM(["SELECT id FROM products LIMIT 2"])
    result = sg.run_sql_with_retry(session, llm, "有哪些商品", allowed_tables=ALLOWED)
    assert result["ok"] is True
    assert result["attempts"] == 1
    assert len(llm.calls) == 1
    assert result["row_count"] == 2


def test_run_sql_with_retry_feeds_error_back_and_recovers(session):
    """第一次给了写语句 → 守卫拒绝 → 错误回喂 → 第二次改对。"""
    llm = _StubLLM(["DELETE FROM products", "SELECT id FROM products LIMIT 1"])
    result = sg.run_sql_with_retry(session, llm, "有哪些商品", allowed_tables=ALLOWED)
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert llm.calls[1]["error"], "第二次生成必须带上一次的错误说明"


def test_run_sql_with_retry_gives_up_after_max_retries(session):
    llm = _StubLLM(["DROP TABLE products"] * 5)
    result = sg.run_sql_with_retry(session, llm, "有哪些商品",
                                   allowed_tables=ALLOWED, max_retries=2)
    assert result["ok"] is False
    assert result["give_up"] is True
    assert result["attempts"] == 3  # 首次 + 2 次重试
    assert result["error"]


def test_run_sql_with_retry_statistical_failure_is_explicit(session):
    """统计类问题失败时，错误文本必须能支撑「无法得出统计结论」。"""
    llm = _StubLLM(["SELECT sum(amount_cents) FROM no_such_table"] * 5)
    result = sg.run_sql_with_retry(session, llm, "这个月销售额是多少",
                                   allowed_tables=ALLOWED, max_retries=1)
    assert result["ok"] is False
    assert result["statistical"] is True
    assert result["error"].startswith("无法得出统计结论")


def test_run_sql_with_retry_handles_llm_exception(session):
    class _Boom:
        def generate_sql(self, question, error=None, schema=None):  # noqa: ANN001
            raise RuntimeError("LLM 挂了")

    result = sg.run_sql_with_retry(session, _Boom(), "商品数量",
                                   allowed_tables=ALLOWED, max_retries=0)
    assert result["ok"] is False
    assert result["give_up"] is True


def test_is_statistical_question():
    assert sg.is_statistical_question("这个月销售额是多少") is True
    assert sg.is_statistical_question("帮我 count 一下订单") is True
    assert sg.is_statistical_question("耳机有杂音吗") is False


def test_result_to_json_is_valid_json():
    import json

    ok_json = json.loads(sg.result_to_json(
        {"ok": True, "columns": ["a"], "rows": [{"a": 1}], "row_count": 1}))
    assert ok_json["ok"] is True and ok_json["row_count"] == 1

    bad_json = json.loads(sg.result_to_json({"ok": False, "error": "炸了", "give_up": True}))
    assert bad_json["ok"] is False and bad_json["give_up"] is True
