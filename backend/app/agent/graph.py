"""LangGraph 分析图：一次「问题 → 证据 → 报告 → 动作」的完整编排。

图结构（与任务书一致）::

    START → starting → scheduling → routing
          → (analysis | semantic_deepen) → evidence_merge → report_decision
          → (confirm_gate | action_execute | finish) → finish → END

三条设计约束（都是踩过的坑）：

1. **``interrupt()`` 所在节点必须无副作用**：LangGraph 恢复时会把整个节点从头重跑一遍，
   节点在 ``interrupt()`` 之前产生的 state 更新与 writer 事件都会丢失/重复。因此把
   「暂停」单独放进 :func:`_confirm_gate`——它只调 ``interrupt()``、不写库、不发 step。
2. **token 必须早于 interrupt**：契约与前端要求 ``step* → token* → interrupt → done``，
   所以回答在 :func:`_report_decision` 里就流式发完，之后才进确认门。
3. **MCP 优先、进程内兜底**：``analysis`` 节点通过 ``call_tool("query_mysql")`` 走
   streamable-HTTP；测试环境没挂载 MCP 时 ``call_tool`` 自动降级为同进程直调。

LLM 全程可注入（``backend.app.agent.llm.set_llm_client_for_test``），失败一律降级为
模板回答，绝不让整个 run 失败。
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import func

from backend.app.agent.actions import (
    execute_actions,
    plan_actions,
    public_actions,
    skip_actions,
)
from backend.app.agent.intent import (
    count_bad_evidence,
    exact_product_name,
    extract_product_name,
    has_bad_signal,
    route_question,
)
from backend.app.agent.llm import build_messages, get_llm_client
from backend.app.agent.sql_guard import (
    SQL_SCHEMA_HINT,
    _build_sql_prompt,
    _extract_sql_from_text,
    is_statistical_question,
    validate_sql,
)
from backend.app.agent.state import (
    AgentState,
    initial_state,
    make_step,
    ms_between,
    next_seq,
)
from backend.app.constants import (
    ACTOR_ANALYSIS,
    ACTOR_REPORT,
    ACTOR_SCHEDULER,
    ACTOR_SYSTEM,
    RISK_HIGH,
    RISK_LOW,
    ROUTE_LABELS,
    ROUTE_SEMANTIC,
    ROUTE_STRUCTURED,
    STATUS_SUCCESS,
    STAGE_ACTION_PENDING,
    STAGE_AGENT_ANALYSIS,
    STAGE_AGENT_REPORT,
    STAGE_DECIDING,
    STAGE_DEEPENING,
    STAGE_EXECUTING,
    STAGE_FINISHED,
    STAGE_PREPARING_EVIDENCE,
    STAGE_QUERYING,
    STAGE_ROUTING,
    STAGE_SCHEDULING,
    STAGE_STARTING,
)
from backend.app.mcp_server.server import TOOL_QUERY_MYSQL, TOOL_SEARCH_REVIEWS, call_tool
from backend.app.models import Product, Review
from backend.config import get_config

logger = logging.getLogger(__name__)

GRAPH_NAME = "ecom-agent"

#: 证据条数上限（报告里展示得下，也让 LLM prompt 不至于爆）
MAX_EVIDENCE = 10
#: 模板兜底回答里最多列几条要点
TEMPLATE_BULLETS = 4
#: 模板回答的流式分片大小（让前端打字机效果不至于一次蹦出一大段）
TEMPLATE_CHUNK = 48
#: 判定「均分」至少需要几条带评分证据（1 条 1 星不等于均分 1.00）
MIN_RATED_FOR_AVG = 3

#: 是否优先走 MCP over HTTP（测试里可关掉，避免连到开发服务器的库）
_PREFER_HTTP = True


def set_mcp_prefer_http_for_test(value: bool) -> None:
    """测试钩子：False 时 analysis 节点直接走进程内调用。"""
    global _PREFER_HTTP
    _PREFER_HTTP = bool(value)


# ------------------------------------------------------------------ 小工具
class _NoopWriter:
    """没有 graph 运行时上下文时的空 writer（直接 ainvoke 也能跑）。"""

    def __call__(self, _payload: Any) -> None:  # noqa: D102
        return None


def _writer():
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001 - 不在 astream 上下文里
        return _NoopWriter()


def _emit(writer, payload: dict[str, Any]) -> None:
    try:
        writer(payload)
    except Exception:  # noqa: BLE001 - 前端断开时不该影响图执行
        logger.debug("SSE writer 失效，丢弃事件：%s", payload.get("type"))


def _db_session():
    from backend.app.db import SessionLocal

    return SessionLocal()


def _load_products(db) -> list[tuple[int, str]]:  # noqa: ANN001
    rows = db.query(Product.id, Product.name).order_by(Product.id.asc()).all()
    return [(int(pid), str(name or "")) for pid, name in rows]


def _product_avg_rating(db, product_id: int) -> float | None:  # noqa: ANN001
    value = (db.query(func.avg(Review.rating))
             .filter(Review.product_id == int(product_id)).scalar())
    return float(value) if value is not None else None


def _collect_product_evidence(db, product_id: int, limit: int = 8) -> list[dict[str, Any]]:  # noqa: ANN001
    """取该商品的差评作为结构化证据（不依赖 LLM 生成的 SQL 质量）。"""
    product = db.get(Product, int(product_id))
    if product is None:
        return []
    bad_max = int(get_config()["agent"]["risk"].get("bad_rating_max", 2))
    rows = (db.query(Review)
            .filter(Review.product_id == int(product_id), Review.rating <= bad_max)
            .order_by(Review.rating.asc(), Review.created_at.desc())
            .limit(limit)
            .all())
    evidence: list[dict[str, Any]] = []
    for review in rows:
        text = (review.content or "").strip()
        if review.has_image and review.image_desc:
            text = f"{text} {review.image_desc}".strip()
        evidence.append({
            "source": "review",
            "ref_id": int(review.id),
            "product_id": int(review.product_id),
            "product_name": product.name,
            "rating": int(review.rating),
            "snippet": text[:200],
            "distance": None,
        })
    return evidence


def _merge_evidence(analysis_evidence: list[dict[str, Any]],
                    semantic_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """合并两路证据、去重、排序，并给出 evidence_source。"""
    merged: dict[tuple[Any, Any], dict[str, Any]] = {}
    sources: set[str] = set()
    for item in list(analysis_evidence or []) + list(semantic_items or []):
        if not isinstance(item, dict):
            continue
        key = (item.get("source"), item.get("ref_id"))
        if key in merged:
            continue
        merged[key] = item
        sources.add(str(item.get("source") or "review"))

    def _sort_key(item: dict[str, Any]) -> tuple[float, float]:
        rating = item.get("rating")
        distance = item.get("distance")
        rating_key = float(rating) if isinstance(rating, (int, float)) else 9.0
        distance_key = float(distance) if isinstance(distance, (int, float)) else 9.9
        return (rating_key, distance_key)

    items = sorted(merged.values(), key=_sort_key)[:MAX_EVIDENCE]
    if len(sources) > 1:
        source = "mixed"
    elif sources:
        source = sorted(sources)[0]
    else:
        source = "none"
    return items, source


def _split_chunks(text: str, size: int = TEMPLATE_CHUNK) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


# ------------------------------------------------------------------ SQL（经 MCP）
async def _generate_sql(llm, question: str, error: str | None = None) -> str:  # noqa: ANN001
    """让 LLM 写一条 SELECT（异步，避免在事件循环里再开线程）。"""
    prompt = _build_sql_prompt(question, SQL_SCHEMA_HINT, error)
    messages = [
        {"role": "system", "content": "你是 SQLite 专家，只输出一条 SELECT 语句，不要解释。"},
        {"role": "user", "content": prompt},
    ]
    raw = await llm.complete(messages, temperature=0.0)
    return _extract_sql_from_text(str(raw or ""))


async def _run_sql_via_mcp(question: str) -> dict[str, Any]:
    """生成 SQL → 守卫校验 → 经 MCP ``query_mysql`` 执行，失败回喂错误重试。"""
    cfg = get_config()["agent"]
    retries = int(cfg.get("sql_max_retries") or 2)
    max_rows = int(cfg.get("sql_max_rows") or 200)
    tables = tuple(cfg.get("allowed_tables") or ())
    llm = get_llm_client()

    last_error = "未能生成 SQL"
    attempts = 0
    for attempt in range(retries + 1):
        attempts += 1
        try:
            candidate = await _generate_sql(llm, question, last_error if attempt else None)
        except Exception as exc:  # noqa: BLE001 - LLM 不可用也计入重试
            last_error = f"LLM 生成 SQL 失败：{exc}"
            continue
        if not candidate:
            last_error = "LLM 未返回 SQL"
            continue
        ok, normalized = validate_sql(candidate, allowed_tables=tables, max_rows=max_rows)
        if not ok:
            last_error = normalized
            logger.info("SQL 被守卫拒绝（第 %s 次）：%s", attempt + 1, normalized)
            continue
        result = await call_tool(TOOL_QUERY_MYSQL, {"sql": normalized}, prefer_http=_PREFER_HTTP)
        if result.get("ok"):
            result.update({"sql": normalized, "attempts": attempts, "source": "sql"})
            return result
        last_error = str(result.get("error") or "执行失败")

    statistical = is_statistical_question(question)
    message = f"无法得出统计结论：{last_error}" if statistical else last_error
    return {"ok": False, "error": message, "give_up": True, "attempts": attempts,
            "statistical": statistical, "source": "sql"}


# ------------------------------------------------------------------ 回答生成
def template_answer(state: AgentState) -> str:
    """LLM 不可用时的兜底回答：结构、字段、口径与正常回答保持一致。"""
    question = state.get("question") or ""
    evidence = state.get("evidence") or []
    analysis = state.get("analysis") or {}
    risk = state.get("risk") or RISK_LOW
    product_name = state.get("product_name") or analysis.get("product_name") or ""
    bad_count = state.get("bad_review_count") or count_bad_evidence(evidence)
    avg_rating = state.get("avg_rating")
    sql_error = analysis.get("error") or ""
    row_count = analysis.get("row_count") or 0

    lines: list[str] = []
    if not evidence:
        lines.append("**结论**：证据不足，暂时无法给出确定结论。"
                     "建议补充具体商品名、订单号，或换一个更具体的评价关键词后重试。")
    else:
        subject = f"「{product_name}」" if product_name else "本次检索范围"
        if bad_count >= 2:
            verdict = f"{subject}共命中 {len(evidence)} 条证据，其中 {bad_count} 条差评为集中问题，风险等级：高。"
        elif bad_count == 1:
            verdict = f"{subject}共命中 {len(evidence)} 条证据，其中 1 条差评需要关注，风险等级：低。"
        else:
            verdict = f"{subject}共命中 {len(evidence)} 条证据，暂未发现明显差评信号，风险等级：低。"
        if avg_rating is not None:
            verdict += f"该商品平均评分 {float(avg_rating):.2f}。"
        lines.append(f"**结论**：{verdict}")

    lines.append("")
    lines.append("**要点**")
    if evidence:
        for item in evidence[:TEMPLATE_BULLETS]:
            rating = item.get("rating")
            rating_text = f"{int(rating)} 星" if isinstance(rating, (int, float)) else "无评分"
            snippet = str(item.get("snippet") or "").strip().replace("\n", " ")
            if len(snippet) > 90:
                snippet = snippet[:90] + "…"
            source_text = "评价" if item.get("source") == "review" else "商品"
            lines.append(f"- [{source_text} #{item.get('ref_id')}｜{rating_text}] {snippet}")
    else:
        lines.append("- 未检索到可引用的证据条目。")

    if risk == RISK_HIGH:
        lines.append(f"- 风险判定：高（差评 {bad_count} 条，达到预警阈值），建议人工复核商品详情与评价。")
    else:
        lines.append("- 风险判定：低，暂不需要触发预警。")

    lines.append("")
    lines.append("### 结构化查询")
    if analysis.get("sql"):
        lines.append(f"- 执行 SQL：`{analysis['sql']}`")
        lines.append(f"- 返回 {row_count} 行；语义检索命中 {len(state.get('semantic_items') or [])} 条。")
    elif sql_error:
        lines.append(f"- 本次结构化查询未成功：{sql_error}")
        lines.append(f"- 语义检索命中 {len(state.get('semantic_items') or [])} 条，结论仅基于语义证据。")
    else:
        lines.append("- 本次未执行结构化查询（路由为语义检索）。")
        lines.append(f"- 语义检索命中 {len(state.get('semantic_items') or [])} 条。")
    lines.append("")
    lines.append(f"> 回答由规则模板生成（LLM 暂不可用），问题：{question}")
    return "\n".join(lines)


async def _generate_answer(state: AgentState, writer) -> tuple[str, bool]:  # noqa: ANN001
    """流式生成回答；失败则模板兜底（同样以 token 事件发出去，前端拼接逻辑不变）。"""
    messages = build_messages(
        state.get("question") or "",
        state.get("evidence") or [],
        {**(state.get("analysis") or {}),
         "route": state.get("route"),
         "risk": state.get("risk"),
         "bad_review_count": state.get("bad_review_count"),
         "avg_rating": state.get("avg_rating"),
         "product_name": state.get("product_name"),
         "product_id": state.get("product_id")},
    )
    client = get_llm_client()
    parts: list[str] = []
    try:
        async for delta in client.stream(messages):
            if not delta:
                continue
            parts.append(delta)
            _emit(writer, {"type": "token", "delta": delta})
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("LLM 返回空内容")
        return text, False
    except Exception as exc:  # noqa: BLE001 - LLM 任何问题都降级为模板
        logger.warning("LLM 生成回答失败，降级为模板：%s", exc)
        text = template_answer(state)
        for piece in _split_chunks(text):
            _emit(writer, {"type": "token", "delta": piece})
        return text, True


# ------------------------------------------------------------------ 节点
async def _starting(state: AgentState) -> dict[str, Any]:
    t0 = time.monotonic()
    writer = _writer()
    step = make_step(next_seq(state), STAGE_STARTING, "已接入 LangGraph 分析流程",
                     ACTOR_SYSTEM, ms_between(t0))
    _emit(writer, {"type": "step", "step": step})
    return {"steps": [step]}


async def _scheduling(state: AgentState) -> dict[str, Any]:
    t0 = time.monotonic()
    writer = _writer()
    step = make_step(next_seq(state), STAGE_SCHEDULING, "调度完成：单 Agent 顺序执行，按需深挖语义证据",
                     ACTOR_SCHEDULER, ms_between(t0))
    _emit(writer, {"type": "step", "step": step})
    return {"steps": [step]}


async def _routing(state: AgentState) -> dict[str, Any]:
    t0 = time.monotonic()
    writer = _writer()
    question = state.get("question") or ""
    db = _db_session()
    try:
        products = _load_products(db)
    finally:
        db.close()
    names = [name for _, name in products]
    route = route_question(question, names)
    # 商品识别：优先原样出现的商品名；只有结构化路由时才允许品类后缀粗切
    # （否则「跑鞋脱胶」会被粗切成「越野跑鞋」，把证据引到错商品上）
    product_name = exact_product_name(question, names)
    if product_name is None and route == ROUTE_STRUCTURED:
        product_name = extract_product_name(question, names)
    product_id = next((pid for pid, name in products if name == product_name), None)
    detail = f"意图路由：{ROUTE_LABELS.get(route, route)}"
    if product_name:
        detail += f"（识别商品：{product_name}）"
    step = make_step(next_seq(state), STAGE_ROUTING, detail, ACTOR_SCHEDULER, ms_between(t0))
    _emit(writer, {"type": "step", "step": step})
    return {
        "route": route,
        "product_names": names,
        "product_name": product_name,
        "product_id": product_id,
        "steps": [step],
    }


async def _analysis(state: AgentState) -> dict[str, Any]:
    """结构化分析：MCP 查库 + 该商品差评明细（不依赖 LLM 的 SQL 质量）。"""
    t0 = time.monotonic()
    writer = _writer()
    question = state.get("question") or ""
    route = state.get("route") or ROUTE_STRUCTURED
    product_name = state.get("product_name")
    product_id = state.get("product_id")

    base = next_seq(state)
    step_a = make_step(base, STAGE_AGENT_ANALYSIS,
                       f"查询分析 Agent 接管：{ROUTE_LABELS.get(route, route)}",
                       ACTOR_ANALYSIS, ms_between(t0))
    _emit(writer, {"type": "step", "step": step_a})

    sql_result: dict[str, Any] = {}
    if route == ROUTE_STRUCTURED:
        sql_result = await _run_sql_via_mcp(question)

    evidence: list[dict[str, Any]] = []
    avg_rating: float | None = None
    if product_id is not None:
        db = _db_session()
        try:
            evidence = _collect_product_evidence(db, int(product_id))
            avg_rating = _product_avg_rating(db, int(product_id))
        finally:
            db.close()

    bad_count = count_bad_evidence(evidence)
    row_count = int(sql_result.get("row_count") or 0)
    if sql_result.get("ok"):
        detail_q = f"结构化查询返回 {row_count} 行"
    elif sql_result.get("error"):
        detail_q = f"结构化查询未成功：{sql_result['error']}"
    else:
        detail_q = "语义路由，跳过结构化查询"
    if product_name:
        detail_q += f"；商品「{product_name}」差评 {bad_count} 条"
    step_q = make_step(base + 1, STAGE_QUERYING, detail_q, ACTOR_ANALYSIS, ms_between(t0))
    _emit(writer, {"type": "step", "step": step_q})

    analysis: dict[str, Any] = {
        "route": route,
        "sql": sql_result.get("sql"),
        "columns": sql_result.get("columns") or [],
        "rows": (sql_result.get("rows") or [])[:20],
        "row_count": row_count,
        "sql_ok": bool(sql_result.get("ok")),
        "product_id": product_id,
        "product_name": product_name,
        "bad_review_count": bad_count,
        "avg_rating": round(avg_rating, 2) if avg_rating is not None else None,
        "error": None if sql_result.get("ok") else sql_result.get("error"),
        "transport": sql_result.get("transport", "inprocess"),
    }
    return {
        "analysis": analysis,
        "sql_result": sql_result,
        "sql": sql_result.get("sql"),
        "analysis_evidence": evidence,
        "bad_review_count": bad_count,
        "avg_rating": analysis["avg_rating"],
        "mcp_transport": analysis["transport"],
        "steps": [step_a, step_q],
    }


async def _semantic_deepen(state: AgentState) -> dict[str, Any]:
    """语义深挖：MCP ``search_reviews`` 补评价证据。

    query 直接用用户原问题：拼上商品名反而会把向量带偏——「跑鞋脱胶」这类词面本身
    就是最强的检索信号，而粗切出来的商品名可能是错的。
    """
    t0 = time.monotonic()
    writer = _writer()
    question = state.get("question") or ""
    query = question.strip()
    top_k = int(get_config()["rag"].get("top_k") or 5)
    threshold = float(get_config()["rag"].get("relevant_threshold") or 0.5)

    result = await call_tool(TOOL_SEARCH_REVIEWS, {"query": query, "top_k": top_k},
                             prefer_http=_PREFER_HTTP)
    raw_items = result.get("items") or []
    items = [item for item in raw_items
             if isinstance(item, dict) and item.get("distance") is not None
             and float(item["distance"]) <= threshold]
    if not items and raw_items:
        # 没有一条过阈值时取最相近的 2 条，至少让回答有依据（并如实标注）
        items = [item for item in raw_items if isinstance(item, dict)][:2]
    mode = result.get("embedding_mode") or ""
    if result.get("ok"):
        detail = f"语义检索命中 {len(items)} 条（集合 {result.get('collection_size', 0)} 条文档）"
    else:
        detail = f"语义检索不可用：{result.get('error', '未知错误')}"
    step = make_step(next_seq(state), STAGE_DEEPENING, detail, ACTOR_ANALYSIS, ms_between(t0))
    _emit(writer, {"type": "step", "step": step})
    return {
        "semantic_items": items,
        "semantic_query": query,
        "steps": [step],
        "mcp_transport": result.get("transport", state.get("mcp_transport", "inprocess")),
        "analysis": {**(state.get("analysis") or {}), "embedding_mode": mode},
    }


async def _evidence_merge(state: AgentState) -> dict[str, Any]:
    t0 = time.monotonic()
    writer = _writer()
    items, source = _merge_evidence(state.get("analysis_evidence") or [],
                                    state.get("semantic_items") or [])
    bad_count = count_bad_evidence(items)
    detail = f"证据整合完成：共 {len(items)} 条（来源：{source}），其中差评 {bad_count} 条"
    step = make_step(next_seq(state), STAGE_PREPARING_EVIDENCE, detail, ACTOR_REPORT,
                     ms_between(t0))
    _emit(writer, {"type": "step", "step": step})
    return {"evidence": items, "evidence_source": source,
            "bad_review_count": bad_count, "steps": [step]}


def _judge_risk(state: AgentState) -> tuple[str, str]:
    """风险判定：差评 ≥ N 条 或 均分 < 阈值 → high；证据不足一律 low。

    均分有**最小样本量**要求（``MIN_RATED_FOR_AVG``）：语义检索常常只捞回 1 条带评分的
    证据，拿它当「均分」会把一条 1 星直接升级成高风险（实测踩到过）。样本不足时改用
    该商品在库里的整体均分；两者都没有就不据均分判定。
    """
    evidence = state.get("evidence") or []
    if not evidence:
        return RISK_LOW, "证据不足，不做风险升级"
    rules = get_config()["agent"]["risk"]
    bad_threshold = int(rules.get("high_risk_bad_review_count", 2))
    rating_floor = float(rules.get("high_risk_rating_below", 2.5))
    bad_count = count_bad_evidence(evidence)
    if bad_count >= bad_threshold:
        return RISK_HIGH, f"差评 {bad_count} 条，达到高风险阈值（≥{bad_threshold}）"

    ratings = [float(item["rating"]) for item in evidence
               if isinstance(item, dict) and isinstance(item.get("rating"), (int, float))]
    if len(ratings) >= MIN_RATED_FOR_AVG:
        avg = sum(ratings) / len(ratings)
        if avg < rating_floor:
            return RISK_HIGH, f"证据均分 {avg:.2f}，低于阈值 {rating_floor:g}"
        return RISK_LOW, f"差评 {bad_count} 条、证据均分 {avg:.2f} 正常，未达高风险阈值"

    product_avg = state.get("avg_rating")
    if isinstance(product_avg, (int, float)) and float(product_avg) < rating_floor:
        return RISK_HIGH, f"商品整体均分 {float(product_avg):.2f}，低于阈值 {rating_floor:g}"
    return RISK_LOW, f"差评 {bad_count} 条，未达高风险阈值（带评分证据仅 {len(ratings)} 条）"


async def _report_decision(state: AgentState) -> dict[str, Any]:
    """报告决策：判定风险 → 规划动作 → 流式产出回答 → 决定是否需要人工确认。"""
    t0 = time.monotonic()
    writer = _writer()
    base = next_seq(state)

    risk, risk_reason = _judge_risk(state)
    evidence = state.get("evidence") or []
    bad_count = count_bad_evidence(evidence)
    ratings = [float(item["rating"]) for item in evidence
               if isinstance(item, dict) and isinstance(item.get("rating"), (int, float))]
    # 均分优先用商品整体均分（样本足），证据太少时不要拿 1~2 条算「均分」
    if len(ratings) >= MIN_RATED_FOR_AVG:
        avg_rating = round(sum(ratings) / len(ratings), 2)
    else:
        avg_rating = state.get("avg_rating")
        if avg_rating is None and ratings:
            avg_rating = round(sum(ratings) / len(ratings), 2)

    analysis_payload = {
        "question": state.get("question") or "",
        "run_id": state.get("run_id"),
        "route": state.get("route"),
        "risk": risk,
        "risk_reason": risk_reason,
        "product_id": state.get("product_id"),
        "product_name": state.get("product_name"),
        "evidence": evidence,
        "bad_review_count": bad_count,
        "avg_rating": avg_rating,
    }
    db = _db_session()
    try:
        actions = plan_actions(db, analysis_payload)
    except Exception as exc:  # noqa: BLE001 - 规划失败不应让 run 挂掉
        logger.exception("动作规划失败")
        actions = []
        analysis_payload["plan_error"] = str(exc)
    finally:
        db.close()

    state_for_answer: AgentState = {  # type: ignore[assignment]
        **state, "risk": risk, "bad_review_count": bad_count, "avg_rating": avg_rating,
        "actions": actions,
    }
    answer, llm_failed = await _generate_answer(state_for_answer, writer)

    needs_confirm = bool(actions) and (risk == RISK_HIGH or any(
        action.get("type") in ("send_alert_notification", "mark_product_pending_review")
        for action in actions))

    step_report = make_step(
        base, STAGE_AGENT_REPORT,
        f"报告决策 Agent 生成回答（{len(answer)} 字）" + ("，LLM 不可用已用模板兜底" if llm_failed else ""),
        ACTOR_REPORT, ms_between(t0))
    _emit(writer, {"type": "step", "step": step_report})

    step_decide = make_step(
        base + 1, STAGE_DECIDING,
        f"风险判定：{'高' if risk == RISK_HIGH else '低'}（{risk_reason}）；规划动作 {len(actions)} 个",
        ACTOR_REPORT, ms_between(t0))
    _emit(writer, {"type": "step", "step": step_decide})

    steps = [step_report, step_decide]
    if needs_confirm:
        step_pending = make_step(
            base + 2, STAGE_ACTION_PENDING,
            f"等待人工确认 {len(actions)} 个动作：" +
            "、".join(str(a.get("label") or a.get("type")) for a in actions),
            ACTOR_REPORT, ms_between(t0))
        _emit(writer, {"type": "step", "step": step_pending})
        steps.append(step_pending)

    return {
        "risk": risk,
        "risk_reason": risk_reason,
        "bad_review_count": bad_count,
        "avg_rating": avg_rating,
        "actions": actions,
        "answer": answer,
        "llm_failed": llm_failed,
        "awaiting_confirm": needs_confirm,
        "analysis": {**(state.get("analysis") or {}), **analysis_payload,
                     "risk_reason": risk_reason},
        "steps": steps,
    }


def build_interrupt_payload(state: AgentState) -> dict[str, Any]:
    """SSE ``interrupt`` 事件与 ``interrupt()`` 共用的载荷（契约 §4）。"""
    risk = state.get("risk") or RISK_LOW
    actions = public_actions(state.get("actions") or [])
    message = ("检测到高风险，请确认是否执行以下动作" if risk == RISK_HIGH
               else "本次分析涉及写操作，请确认是否执行以下动作")
    return {"actions": actions, "risk": risk, "message": message}


async def _confirm_gate(state: AgentState) -> dict[str, Any]:
    """确认门：**唯一**调用 ``interrupt()`` 的节点，无副作用、不发 step。

    恢复时 LangGraph 会重跑本节点，``interrupt()`` 直接返回 ``Command(resume=...)`` 的值。
    """
    payload = build_interrupt_payload(state)
    decision = interrupt(payload)
    if isinstance(decision, dict):
        raw = str(decision.get("decision") or "")
    else:
        raw = str(decision or "")
    normalized = "approve" if raw == "approve" else "reject"
    logger.info("run=%s 确认结果：%s", state.get("run_id"), normalized)
    return {"confirm_decision": normalized, "awaiting_confirm": False}


async def _action_execute(state: AgentState) -> dict[str, Any]:
    t0 = time.monotonic()
    writer = _writer()
    actions = state.get("actions") or []
    decision = state.get("confirm_decision")

    if not actions:
        step = make_step(next_seq(state), STAGE_EXECUTING, "无待执行动作（证据不足或无需写操作）",
                         ACTOR_REPORT, ms_between(t0))
        _emit(writer, {"type": "step", "step": step})
        return {"executed": [], "skipped": [], "steps": [step]}

    if decision == "reject":
        skipped = skip_actions(actions)
        step = make_step(next_seq(state), STAGE_EXECUTING,
                         f"用户拒绝执行，已跳过 {len(actions)} 个动作", ACTOR_REPORT,
                         ms_between(t0))
        _emit(writer, {"type": "step", "step": step})
        return {"executed": [], "skipped": skipped, "steps": [step]}

    db = _db_session()
    try:
        executed = execute_actions(db, actions)
    finally:
        db.close()
    ok_count = sum(1 for item in executed if item.get("ok"))
    detail = f"动作执行完成：成功 {ok_count}/{len(executed)}"
    failures = [item for item in executed if not item.get("ok")]
    if failures:
        detail += "；失败：" + "、".join(str(item.get("type")) for item in failures)
    step = make_step(next_seq(state), STAGE_EXECUTING, detail, ACTOR_REPORT, ms_between(t0))
    _emit(writer, {"type": "step", "step": step})
    return {"executed": executed, "skipped": [], "steps": [step]}


async def _finish(state: AgentState) -> dict[str, Any]:
    writer = _writer()
    total_ms = ms_between(state.get("started_at") or time.monotonic())
    executed = state.get("executed") or []
    skipped = state.get("skipped") or []
    detail = f"分析结束：共 {len(state.get('steps') or []) + 1} 步，耗时 {total_ms / 1000:.1f} 秒"
    if executed or skipped:
        detail += f"；执行 {sum(1 for i in executed if i.get('ok'))} 个动作、跳过 {len(skipped)} 个"
    step = make_step(next_seq(state), STAGE_FINISHED, detail, ACTOR_SYSTEM, 0)
    _emit(writer, {"type": "step", "step": step})
    return {"status": STATUS_SUCCESS, "awaiting_confirm": False, "steps": [step]}


# ------------------------------------------------------------------ 条件边
def _after_routing(state: AgentState) -> str:
    return "analysis" if state.get("route") == ROUTE_STRUCTURED else "semantic_deepen"


def _after_analysis(state: AgentState) -> str:
    """决定是否继续语义深挖。

    纯统计问题（「这个月销售额是多少」）**不深挖**：它没有商品对象，语义检索只会
    捞回几条无关评价当噪声证据，反而污染结论。这类问题靠 SQL 结果说话。
    """
    if state.get("route") == ROUTE_SEMANTIC:
        return "semantic_deepen"
    if state.get("product_id") is None:
        return "evidence_merge"
    evidence = state.get("analysis_evidence") or []
    if has_bad_signal(evidence) or not evidence:
        return "semantic_deepen"
    return "evidence_merge"


def _after_report(state: AgentState) -> str:
    if state.get("awaiting_confirm"):
        return "confirm_gate"
    if state.get("actions"):
        return "action_execute"
    return "finish"


# ------------------------------------------------------------------ 图
def build_graph(checkpointer=None):  # noqa: ANN001
    graph = StateGraph(AgentState)
    graph.add_node("starting", _starting)
    graph.add_node("scheduling", _scheduling)
    graph.add_node("routing", _routing)
    graph.add_node("analysis", _analysis)
    graph.add_node("semantic_deepen", _semantic_deepen)
    graph.add_node("evidence_merge", _evidence_merge)
    graph.add_node("report_decision", _report_decision)
    graph.add_node("confirm_gate", _confirm_gate)
    graph.add_node("action_execute", _action_execute)
    graph.add_node("finish", _finish)

    graph.add_edge(START, "starting")
    graph.add_edge("starting", "scheduling")
    graph.add_edge("scheduling", "routing")
    graph.add_conditional_edges("routing", _after_routing,
                                {"analysis": "analysis",
                                 "semantic_deepen": "semantic_deepen"})
    graph.add_conditional_edges("analysis", _after_analysis,
                                {"semantic_deepen": "semantic_deepen",
                                 "evidence_merge": "evidence_merge"})
    graph.add_edge("semantic_deepen", "evidence_merge")
    graph.add_edge("evidence_merge", "report_decision")
    graph.add_conditional_edges("report_decision", _after_report,
                                {"confirm_gate": "confirm_gate",
                                 "action_execute": "action_execute",
                                 "finish": "finish"})
    graph.add_edge("confirm_gate", "action_execute")
    graph.add_edge("action_execute", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


_compiled = None


def get_graph():
    """进程内单例（MemorySaver 检查点必须与 confirm 请求同进程，所以不能重建）。"""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def reset_graph() -> None:
    """测试用：丢弃已编译图与全部内存检查点（等价于进程重启）。"""
    global _compiled
    _compiled = None


def thread_config(run_id: int) -> dict[str, Any]:
    return {"configurable": {"thread_id": str(int(run_id))}}


# ------------------------------------------------------------------ 流式入口
async def stream_run(run_id: int, question: str,
                     session_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
    """跑一次完整分析，产出 ``step`` / ``token`` 事件，最后产出 ``paused`` 或 ``final``。"""
    graph = get_graph()
    config = thread_config(run_id)
    state_in = initial_state(run_id, question, session_id)
    async for chunk in graph.astream(state_in, config, stream_mode="custom"):
        if isinstance(chunk, dict) and chunk.get("type"):
            yield chunk
    snapshot = await graph.aget_state(config)
    values = dict(snapshot.values or {})
    if snapshot.next:
        yield {"type": "paused", "interrupt": build_interrupt_payload(values), "state": values}
    else:
        yield {"type": "final", "state": values}


async def resume_run(run_id: int, decision: str) -> AsyncIterator[dict[str, Any]]:
    """按用户决策恢复被 ``interrupt()`` 暂停的图，产出后续 step 事件。"""
    graph = get_graph()
    config = thread_config(run_id)
    async for chunk in graph.astream(Command(resume={"decision": decision}), config,
                                     stream_mode="custom"):
        if isinstance(chunk, dict) and chunk.get("type"):
            yield chunk
    snapshot = await graph.aget_state(config)
    yield {"type": "final", "state": dict(snapshot.values or {})}


async def get_snapshot(run_id: int) -> dict[str, Any]:
    """读取某 run 的检查点状态（confirm 路由用它判断是否真的在等待确认）。"""
    graph = get_graph()
    snapshot = await graph.aget_state(thread_config(run_id))
    return {"values": dict(snapshot.values or {}), "next": tuple(snapshot.next or ())}


__all__ = [
    "GRAPH_NAME", "MAX_EVIDENCE",
    "build_graph", "get_graph", "reset_graph", "thread_config",
    "stream_run", "resume_run", "get_snapshot",
    "build_interrupt_payload", "template_answer",
    "set_mcp_prefer_http_for_test",
]
