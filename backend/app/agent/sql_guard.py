"""SQL 守卫：只放行单条只读 SELECT，限定表白名单与行数。

这是 Agent 唯一能碰到数据库的地方，所以规则写成「默认拒绝」：

- 去注释（``--`` / ``#`` / ``/* */``，字符串字面量内的不算）后必须以 ``SELECT`` 开头
- 不允许 ``;`` 拼接多条语句（只容忍结尾一个分号）
- 不允许任何写/管理关键字（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA/ATTACH/
  VACUUM/REPLACE INTO/TRUNCATE/GRANT/REVOKE/CALL/EXEC/SELECT ... INTO）
- ``FROM`` / ``JOIN`` 后的表名（含 ``FROM a, b`` 逗号列表、``schema.table``）必须全在白名单
- 无 ``LIMIT`` 自动补 ``LIMIT max_rows``；结果集再按 max_rows 截断
- SQLite 用 ``set_progress_handler`` 实现真超时（超时即中断语句）
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import get_config

logger = logging.getLogger(__name__)

# 默认白名单（配置 agent.allowed_tables 可覆盖）
DEFAULT_ALLOWED_TABLES: tuple[str, ...] = ("products", "orders", "reviews", "diagnosis_reports")

FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "PRAGMA", "ATTACH",
    "DETACH", "VACUUM", "TRUNCATE", "GRANT", "REVOKE", "MERGE", "CALL", "EXEC",
    "EXECUTE", "INTO", "LOAD_EXTENSION", "WRITEFILE", "READFILE",
)

# 这些词出现在问题里说明用户在问「统计口径」，失败时必须明确说无法得出统计结论
STAT_QUESTION_WORDS: tuple[str, ...] = (
    "多少", "几个", "几笔", "总数", "合计", "销量", "销售额", "排名", "最高", "最低",
    "平均", "占比", "统计", "汇总", "总量", "总金额", "总额", "数量", "count", "sum",
    "avg", "max", "min", "top",
)

# 给 LLM 的 schema 提示（也用于提示词，字段名与 models.py 一致）
SQL_SCHEMA_HINT = """可用表（只读）：
- products(id, name, category, price_cents, stock, status, description, image_desc, pending_review, created_at)
- orders(id, order_no, product_id, quantity, amount_cents, status, buyer, created_at)
- reviews(id, product_id, rating, content, buyer, has_image, image_desc, created_at)
- diagnosis_reports(id, question, product_id, risk, evidence_count, summary, evidence_json, actions_json, run_id, created_at)
说明：金额以「分」存储（amount_cents / price_cents），status: products.status ∈ on_sale|off_sale，
orders.status ∈ paid|cancelled|refunded，reviews.rating 为 1~5 星，差评指 rating <= 2。"""

_STAT_RE = re.compile(r"\b(?:count|sum|avg|min|max)\s*\(", re.I)
_COMMENT_OR_STRING = re.compile(r"--[^\n]*|#[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`",
                                re.S)
_IDENT_RE = re.compile(r"[`\"\[]?([A-Za-z_][A-Za-z0-9_$]*)[`\"\]]?")
_TABLE_TOKEN_RE = re.compile(r"\b(FROM|JOIN)\b", re.I)
# 只抹掉单引号字符串字面量；双引号/反引号是标识符（SQLite 允许 "products"），
# 交给 _IDENT_RE 剥壳，否则 `FROM "products"` 会被误判成没有表名。
_STRING_RE = re.compile(r"'(?:[^']|'')*'", re.S)


class SqlGuardError(ValueError):
    """守卫拒绝。"""


# ------------------------------------------------------------------ 文本预处理
def strip_comments(sql: str) -> str:
    """去掉 SQL 注释，保留字符串字面量与引号标识符。"""
    if not sql:
        return ""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            quote = ch
            j = i + 1
            while j < n:
                if sql[j] == "\\" and quote != "`":
                    j += 2
                    continue
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(sql[i:j])
            i = j
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            if j < 0:
                break
            out.append(" ")
            i = j + 1
            continue
        if ch == "#":
            j = sql.find("\n", i)
            if j < 0:
                break
            out.append(" ")
            i = j + 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            if j < 0:
                break
            out.append(" ")
            i = j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _blank_literals(sql: str) -> str:
    """把字符串字面量换成空串，供结构检查（避免 'JOIN x' 之类的误判）。"""
    return _STRING_RE.sub("''", sql)


def _skip_ws(sql: str, pos: int) -> int:
    n = len(sql)
    while pos < n and sql[pos].isspace():
        pos += 1
    return pos


_CLAUSE_END_RE = re.compile(
    r"(?i)\b(?:WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|EXCEPT|INTERSECT|WINDOW)\b")
_JOIN_COND_RE = re.compile(r"(?i)\b(?:ON|USING)\b")
#: 出现在表名之后、但**不是**别名的关键字（``FROM products WHERE ...``）
_RESERVED_AFTER_TABLE = frozenset({
    "where", "group", "order", "having", "limit", "union", "except", "intersect",
    "window", "join", "left", "right", "inner", "outer", "full", "cross", "natural",
    "on", "using", "offset", "as", "and", "or", "not", "set", "values",
})


def _skip_join_condition(scan: str, pos: int) -> tuple[int, bool]:
    """跳过 ``ON ...`` / ``USING (...)`` 条件，返回 ``(新位置, 是否遇到顶层逗号)``。

    存在的理由：``FROM a JOIN b ON b.id = a.id, c`` 这种写法里，逗号在 ON 条件之后，
    只按「表名后面紧跟逗号」判断会漏掉 ``c``。这里在遇到子句关键字（WHERE/GROUP BY…）
    或语句结尾时停下，避免把整个 WHERE 条件当成表名列表继续解析。
    """
    depth = 0
    n = len(scan)
    while pos < n:
        ch = scan[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
            if depth == 0:
                # USING (...) 的右括号之后可能才是逗号
                nxt = _skip_ws(scan, pos + 1)
                if nxt < n and scan[nxt] == ",":
                    return nxt + 1, True
                return nxt, False
        elif depth == 0:
            if ch == ",":
                return pos + 1, True
            if ch == ";":
                return pos, False
            clause = _CLAUSE_END_RE.match(scan, pos)
            if clause:
                return pos, False
        pos += 1
    return n, False


def extract_tables(sql: str) -> list[str]:
    """提取 FROM/JOIN 后出现的表名（小写，去重保序）。"""
    scan = _blank_literals(strip_comments(sql))
    tables: list[str] = []
    for match in _TABLE_TOKEN_RE.finditer(scan):
        pos = _skip_ws(scan, match.end())
        while pos < len(scan):
            if scan[pos] == "(":      # 子查询：内层 FROM 会被外层循环再次匹配
                break
            ident = _IDENT_RE.match(scan, pos)
            if not ident:
                break
            first = ident.group(1)
            pos = _skip_ws(scan, ident.end())
            table = first
            if pos < len(scan) and scan[pos] == ".":
                pos = _skip_ws(scan, pos + 1)
                second = _IDENT_RE.match(scan, pos)
                if second:
                    table = second.group(1)
                    pos = _skip_ws(scan, second.end())
            if table.lower() not in tables:
                tables.append(table.lower())
            # 跳过可选别名（``FROM products p`` / ``FROM products AS p``）
            alias = _IDENT_RE.match(scan, pos)
            if alias and alias.group(1).lower() not in _RESERVED_AFTER_TABLE:
                pos = _skip_ws(scan, alias.end())
                if alias.group(1).lower() == "as":
                    real = _IDENT_RE.match(scan, pos)
                    if real:
                        pos = _skip_ws(scan, real.end())
            # FROM a, b 的逗号列表
            if pos < len(scan) and scan[pos] == ",":
                pos = _skip_ws(scan, pos + 1)
                continue
            # FROM a JOIN b ON ..., c —— 逗号在 ON 条件之后
            if _JOIN_COND_RE.match(scan, pos):
                pos, found_comma = _skip_join_condition(scan, pos)
                if found_comma:
                    pos = _skip_ws(scan, pos)
                    continue
            break
    return tables


def _has_top_level_limit(scan: str) -> bool:
    depth = 0
    for match in re.finditer(r"[()]|\bLIMIT\b", scan, re.I):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            return True
    return False


def _has_multi_statement(scan: str) -> bool:
    stripped = scan.strip()
    # 容忍结尾一个分号
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    return ";" in stripped


# ------------------------------------------------------------------ 校验
def validate_sql(sql: str, allowed_tables: Sequence[str] | None = None,
                 max_rows: int | None = None) -> tuple[bool, str]:
    """校验并规范化 SQL。返回 ``(True, 规范化SQL)`` 或 ``(False, 错误说明)``。"""
    cfg = get_config()["agent"]
    tables = tuple(t.lower() for t in (allowed_tables or cfg.get("allowed_tables")
                                       or DEFAULT_ALLOWED_TABLES))
    limit = int(max_rows or cfg.get("sql_max_rows") or 200)

    raw = (sql or "").strip()
    if not raw:
        return False, "SQL 为空"

    cleaned = strip_comments(raw).strip()
    if not cleaned:
        return False, "SQL 去掉注释后为空"

    scan = _blank_literals(cleaned)

    if _has_multi_statement(scan):
        return False, "只允许单条 SQL 语句，禁止用分号拼接多条语句"

    head = scan.lstrip()
    if not re.match(r"(?is)^SELECT\b", head):
        return False, "只允许 SELECT 查询"

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", scan, re.I):
            return False, f"禁止的关键字：{keyword}"

    if re.search(r"\bREPLACE\s+INTO\b", scan, re.I):  # 已被 INTO 覆盖，双保险
        return False, "禁止的关键字：REPLACE INTO"

    used_tables = extract_tables(cleaned)
    if not used_tables:
        return False, "无法识别查询的数据表，请使用 FROM <表名> 写法"
    illegal = [t for t in used_tables if t not in tables]
    if illegal:
        return False, (f"表 {', '.join(illegal)} 不在允许范围内（"
                       f"可用：{', '.join(tables)}）")

    normalized = cleaned.rstrip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if not _has_top_level_limit(scan):
        normalized = f"{normalized} LIMIT {limit}"
    return True, normalized


# ------------------------------------------------------------------ 执行
def _apply_sqlite_deadline(db: Session, timeout_seconds: float):
    """给 SQLite 装 progress handler 实现语句级超时。返回清理函数。"""
    if timeout_seconds <= 0:
        return None
    try:
        raw = db.connection().connection  # type: ignore[union-attr]
        dbapi = getattr(raw, "driver_connection", None) or getattr(raw, "dbapi_connection", None)
        if dbapi is None or not hasattr(dbapi, "set_progress_handler"):
            return None
        deadline = time.monotonic() + float(timeout_seconds)

        def _progress() -> int:
            return 1 if time.monotonic() > deadline else 0

        dbapi.set_progress_handler(_progress, 2000)

        def _clear() -> None:
            try:
                dbapi.set_progress_handler(None, 0)
            except Exception:  # pragma: no cover
                pass

        return _clear
    except Exception:  # pragma: no cover - 非 SQLite 或连接已关闭
        return None


def _row_to_dict(row) -> dict[str, Any]:  # noqa: ANN001
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return {str(k): _jsonable(v) for k, v in mapping.items()}
    if isinstance(row, (tuple, list)):
        return {str(i): _jsonable(v) for i, v in enumerate(row)}
    return {"value": _jsonable(row)}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def execute_sql(db: Session, sql: str, timeout_seconds: float | None = None,
                max_rows: int | None = None) -> dict[str, Any]:
    """执行已校验的 SQL。成功返回 {ok, columns, rows, row_count}，失败返回 {ok:False, error}。"""
    cfg = get_config()["agent"]
    timeout = float(timeout_seconds if timeout_seconds is not None
                    else cfg.get("sql_timeout_seconds") or 5)
    limit = int(max_rows or cfg.get("sql_max_rows") or 200)
    cleanup = None
    try:
        cleanup = _apply_sqlite_deadline(db, timeout)
        result = db.execute(text(sql))
        columns = list(result.keys())
        rows: list[dict[str, Any]] = []
        for row in result:
            if len(rows) >= limit:
                break
            rows.append(_row_to_dict(row))
        return {"ok": True, "columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001 - 任何执行错误都转成错误文本回喂 LLM
        message = str(getattr(exc, "orig", exc)) or exc.__class__.__name__
        if "interrupted" in message.lower() or "timeout" in message.lower():
            message = f"查询超时（超过 {timeout:g} 秒）：{message}"
        logger.warning("SQL 执行失败：%s | sql=%s", message, sql)
        try:
            db.rollback()
        except Exception:  # pragma: no cover
            pass
        return {"ok": False, "error": message, "sql": sql}
    finally:
        if cleanup is not None:
            cleanup()


def is_statistical_question(question: str) -> bool:
    text_q = question or ""
    if _STAT_RE.search(text_q):
        return True
    return any(word in text_q.lower() for word in STAT_QUESTION_WORDS)


def _extract_sql_from_text(raw: str) -> str:
    """从 LLM 回复里抠出 SQL（容忍 ```sql 代码块与前后解释）。"""
    if not raw:
        return ""
    match = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.S | re.I)
    if match:
        return match.group(1).strip()
    # 没有代码块：取第一个 SELECT 起到结尾
    idx = re.search(r"(?i)\bselect\b", raw)
    if idx:
        return raw[idx.start():].strip()
    return raw.strip()


def run_sql_with_retry(db: Session, llm, question: str,  # noqa: ANN001
                       allowed_tables: Sequence[str] | None = None,
                       max_retries: int | None = None,
                       max_rows: int | None = None,
                       schema_hint: str | None = None) -> dict[str, Any]:
    """让 LLM 生成 SQL → 守卫校验 → 执行；失败把错误回喂 LLM 重写，最多 max_retries 次。

    ``llm`` 需要提供 ``generate_sql(question, error=None, schema=None) -> str``；
    也可传普通 callable（收到 prompt 字符串，返回 SQL 文本）。

    返回（成功）::

        {"ok": True, "sql": ..., "columns": [...], "rows": [...], "row_count": n,
         "attempts": n, "source": "sql"}

    返回（放弃）::

        {"ok": False, "error": "...", "give_up": True, "attempts": n,
         "statistical": bool}

    统计类问题失败时 ``error`` 以「无法得出统计结论」开头，上层可以直接透传。
    """
    cfg = get_config()["agent"]
    retries = int(max_retries if max_retries is not None else cfg.get("sql_max_retries") or 2)
    limit = int(max_rows or cfg.get("sql_max_rows") or 200)
    tables = tuple(allowed_tables or cfg.get("allowed_tables") or DEFAULT_ALLOWED_TABLES)
    schema = schema_hint or SQL_SCHEMA_HINT
    statistical = is_statistical_question(question)
    last_error = "未能生成 SQL"
    attempts = 0

    def _ask(error: str | None) -> str:
        nonlocal attempts
        attempts += 1
        if hasattr(llm, "generate_sql"):
            return _extract_sql_from_text(llm.generate_sql(question, error=error, schema=schema))
        prompt = _build_sql_prompt(question, schema, error)
        raw = llm(prompt) if callable(llm) else ""
        if hasattr(raw, "__await__"):  # pragma: no cover - 不支持异步 llm
            raise TypeError("run_sql_with_retry 需要同步 LLM")
        return _extract_sql_from_text(str(raw))

    for attempt in range(retries + 1):
        try:
            candidate = _ask(last_error if attempt else None)
        except Exception as exc:  # noqa: BLE001 - LLM 异常同样计入重试
            last_error = f"LLM 生成 SQL 失败：{exc}"
            logger.warning(last_error)
            continue
        if not candidate:
            last_error = "LLM 未返回 SQL"
            continue
        ok, normalized = validate_sql(candidate, allowed_tables=tables, max_rows=limit)
        if not ok:
            last_error = normalized
            logger.info("SQL 被守卫拒绝（第 %s 次）：%s", attempt + 1, normalized)
            continue
        result = execute_sql(db, normalized, timeout_seconds=cfg.get("sql_timeout_seconds"),
                             max_rows=limit)
        if result.get("ok"):
            result.update({"attempts": attempts, "source": "sql", "statistical": statistical})
            return result
        last_error = str(result.get("error") or "执行失败")

    message = f"无法得出统计结论：{last_error}" if statistical else last_error
    return {"ok": False, "error": message, "give_up": True, "attempts": attempts,
            "statistical": statistical, "source": "sql"}


def _build_sql_prompt(question: str, schema: str, error: str | None) -> str:
    parts = [
        "你是只读数据分析助手。根据用户问题写一条 SQLite 兼容的 SELECT 语句。",
        schema,
        "要求：只输出一条 SELECT；不要写 INSERT/UPDATE/DELETE；金额字段是分，输出时请 /100.0；"
        "金额别名用 amount_yuan；结果最多 200 行；只输出 SQL，不要解释。",
        f"用户问题：{question}",
    ]
    if error:
        parts.append(f"上一次失败原因：{error}\n请修正后重新输出 SQL。")
    return "\n\n".join(parts)


def result_to_json(result: dict[str, Any]) -> str:
    """MCP 工具返回用的 JSON 字符串。"""
    if result.get("ok"):
        payload = {"ok": True, "columns": result.get("columns", []),
                   "rows": result.get("rows", []), "row_count": result.get("row_count", 0)}
    else:
        payload = {"ok": False, "error": result.get("error", "未知错误")}
        if result.get("give_up"):
            payload["give_up"] = True
    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = [
    "DEFAULT_ALLOWED_TABLES", "FORBIDDEN_KEYWORDS", "SQL_SCHEMA_HINT", "SqlGuardError",
    "strip_comments", "extract_tables", "validate_sql", "execute_sql",
    "run_sql_with_retry", "is_statistical_question", "result_to_json",
]
