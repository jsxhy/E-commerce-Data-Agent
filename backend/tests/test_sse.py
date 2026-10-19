"""SSE 流式分析 / 人工确认 / 历史回显测试（契约 §4）。

设计要点：
- **LLM 一律注入 stub**：``config.local.yaml`` 里填了真实 api_key，而 conftest 的
  ``os.environ.setdefault("ECOM_LLM_API_KEY", "")`` 不会覆盖配置里的非空值
  （``config._apply_env`` 遇到空环境变量直接跳过），所以测试环境其实**是**配置好 LLM 的。
  不注入 stub 就会真的打 DeepSeek 烧配额、且回答不稳定。见 ``stub_llm`` 夹具。
- MCP 关掉 HTTP 优先（``set_mcp_prefer_http_for_test(False)``），全部走进程内直调，
  避免测试去连一个不存在的开发服务器。
- conftest 的 ``client`` 不带 ``with``，lifespan 不跑 → MCP session_manager 不启动，
  正好覆盖「MCP 未挂载时降级为进程内调用」这条必须能跑的路径。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Sequence

import pytest
from sqlalchemy import delete, func, select, update

from backend.app import constants
from backend.app.agent import graph as graph_mod
from backend.app.agent import llm as llm_mod
from backend.app.models import (AgentRun, DiagnosisReport, Notification, Product,
                                RunStep)
from backend.app.routers import chat as chat_mod

# 契约 §4 规定的 step.stage 固定集合（不含 constants 里的 action_decision）
CONTRACT_STAGES = {
    "starting", "scheduling", "routing", "agent.analysis", "querying",
    "deepening", "preparing_evidence", "agent.report", "deciding",
    "action_pending", "executing", "finished",
}
CONTRACT_ACTORS = {"", "查询分析 Agent", "报告决策 Agent", "调度"}

STUB_ANSWER = (
    "**结论**：该商品近期差评集中在做工与售后两类问题。\n\n"
    "- 做工：多条评价提到开胶、杂音等硬件问题。\n"
    "- 售后：客服响应慢，问题未及时解决。\n\n"
    "### 结构化查询\n- 本次执行 1 条 SELECT，命中差评若干条。"
)


class _StubLLM:
    """确定性假 LLM：不联网、不烧配额，输出固定 markdown 与固定 SQL。"""

    def __init__(self, answer: str = STUB_ANSWER,
                 sql: str = "SELECT id, name FROM products LIMIT 5"):
        self.answer = answer
        self.sql = sql
        self.stream_calls = 0
        self.complete_calls = 0

    async def stream(self, messages: Sequence[dict[str, Any]],
                     **kwargs: Any) -> AsyncIterator[str]:
        self.stream_calls += 1
        for i in range(0, len(self.answer), 24):
            yield self.answer[i:i + 24]

    async def complete(self, messages: Sequence[dict[str, Any]], **kwargs: Any) -> str:
        self.complete_calls += 1
        # 结构化路由下 complete 被用来生成 SQL
        return self.sql

    async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001
        return {"content": self.answer, "tool_calls": []}

    def generate_sql(self, question: str, error: str | None = None,
                     schema: str | None = None) -> str:
        return self.sql


class _BrokenLLM:
    """模拟 LLM 不可用：所有调用都抛异常 → 图必须走模板兜底而不是整个 run 失败。"""

    async def stream(self, messages, **kwargs):  # noqa: ANN001
        raise llm_mod.LLMUnavailable("stub：LLM 不可用")
        yield ""  # pragma: no cover - 让函数成为 async generator

    async def complete(self, messages, **kwargs):  # noqa: ANN001
        raise llm_mod.LLMUnavailable("stub：LLM 不可用")

    async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001
        raise llm_mod.LLMUnavailable("stub：LLM 不可用")

    def generate_sql(self, question, error=None, schema=None):  # noqa: ANN001
        raise llm_mod.LLMUnavailable("stub：LLM 不可用")



# ------------------------------------------------------------------ 工具
def parse_sse(text: str) -> list[tuple[str, object]]:
    """把 SSE 原始文本解析成 [(event|'__comment__', data|raw), ...]。"""
    events: list[tuple[str, object]] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        if block.startswith(":"):
            events.append(("__comment__", block))
            continue
        name = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if name:
            raw = "".join(data_lines)
            events.append((name, json.loads(raw) if raw else None))
    return events


def events_of(events, name):
    return [data for kind, data in events if kind == name]


def stream(client, auth, question: str, session_id: str | None = None) -> list[tuple[str, object]]:
    payload: dict[str, object] = {"question": question}
    if session_id is not None:
        payload["session_id"] = session_id
    resp = client.post("/api/chat/stream", json=payload, headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


@pytest.fixture(autouse=True)
def _inprocess_mcp():
    graph_mod.set_mcp_prefer_http_for_test(False)
    yield
    graph_mod.set_mcp_prefer_http_for_test(True)


@pytest.fixture(autouse=True)
def stub_llm():
    """默认注入确定性 stub，杜绝真实 API 调用（配置里 api_key 是填好的）。"""
    stub = _StubLLM()
    llm_mod.set_llm_client_for_test(stub)
    yield stub
    llm_mod.reset_llm_client()


@pytest.fixture(autouse=True)
def _restore_shared_db(session):
    """每个用例前记下水线，结束后清掉本用例新增的 run / report / 通知 / 待复核标记。

    ``prepared_db`` 是 session 级共享库（一次 seed 供全部测试），而本文件会真的
    跑完整图（写 agent_runs、run_steps、diagnosis_reports、notifications、
    products.pending_review）。不还原会污染 test_seed_invariants 与 dashboard KPI
    的计数断言（实测不还原时 reports 10→16、notifications 3→8、runs 20→27）。
    """
    watermarks = {
        "run": session.execute(select(func.max(AgentRun.id))).scalar() or 0,
        "report": session.execute(select(func.max(DiagnosisReport.id))).scalar() or 0,
        "notif": session.execute(select(func.max(Notification.id))).scalar() or 0,
    }
    pending_before = set(session.execute(
        select(Product.id).where(Product.pending_review.is_(True))
    ).scalars().all())

    yield

    session.rollback()
    new_runs = select(AgentRun.id).where(AgentRun.id > watermarks["run"])
    session.execute(delete(RunStep).where(RunStep.run_id.in_(new_runs)))
    session.execute(delete(AgentRun).where(AgentRun.id > watermarks["run"]))
    session.execute(delete(DiagnosisReport).where(
        DiagnosisReport.id > watermarks["report"]))
    session.execute(delete(Notification).where(Notification.id > watermarks["notif"]))
    session.execute(
        update(Product)
        .where(Product.pending_review.is_(True), Product.id.notin_(pending_before))
        .values(pending_review=False)
    )
    session.commit()
    # 进程内的确认状态与图检查点也要清，避免跨用例串台
    from backend.app.agent.confirm_store import reset_confirm_store

    reset_confirm_store()
    graph_mod.reset_graph()


# ------------------------------------------------------------------ 流式：低风险
def test_stream_low_risk_emits_meta_steps_tokens_done(client, auth, session):
    events = stream(client, auth, "这个月销售额是多少")
    names = [kind for kind, _ in events]

    # 顺序契约：meta 首帧、done 末帧
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert names.count("meta") == 1
    assert names.count("done") == 1
    assert "interrupt" not in names, "低风险不应暂停等确认"

    meta = events_of(events, "meta")[0]
    assert isinstance(meta["run_id"], int)
    assert meta["question"] == "这个月销售额是多少"
    assert meta["created_at"]
    assert meta["session_id"]

    steps = events_of(events, "step")
    assert steps, "至少要有一步"
    seqs = [s["seq"] for s in steps]
    assert seqs == list(range(1, len(steps) + 1)), "seq 必须从 1 连续递增"
    for step in steps:
        assert step["stage"] in CONTRACT_STAGES, step["stage"]
        assert step["actor"] in CONTRACT_ACTORS, step["actor"]
        assert isinstance(step["duration_ms"], int)
        assert step["detail"]

    tokens = events_of(events, "token")
    assert tokens, "回答必须以 token 事件流式下发"
    assert all("delta" in t for t in tokens)

    done = events_of(events, "done")[0]
    assert done["run_id"] == meta["run_id"]
    assert done["awaiting_confirm"] is False
    assert done["status"] == constants.STATUS_SUCCESS
    assert done["answer"] and "".join(t["delta"] for t in tokens) == done["answer"]
    assert done["route"] in (constants.ROUTE_STRUCTURED, constants.ROUTE_SEMANTIC,
                              constants.ROUTE_UNDETERMINED)

    # 运行结果与步骤都落库了
    run = session.get(AgentRun, meta["run_id"])
    assert run is not None
    assert run.status == constants.STATUS_SUCCESS
    assert run.answer == done["answer"]
    assert run.step_count == len(steps)
    assert run.duration_ms >= 0
    rows = session.execute(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.seq)
    ).scalars().all()
    assert [r.seq for r in rows] == seqs
    assert {r.stage for r in rows} <= CONTRACT_STAGES


def test_stream_echoes_client_session_id(client, auth):
    events = stream(client, auth, "这个月销售额是多少", session_id="s-fixed-001")
    assert events_of(events, "meta")[0]["session_id"] == "s-fixed-001"


def test_stream_generates_session_id_when_missing(client, auth):
    events = stream(client, auth, "这个月销售额是多少")
    session_id = events_of(events, "meta")[0]["session_id"]
    assert isinstance(session_id, str) and session_id.startswith("s-")


def test_stream_rejects_blank_question(client, auth):
    for payload in ({"question": ""}, {"question": "   "}):
        resp = client.post("/api/chat/stream", json=payload, headers=auth)
        assert resp.status_code == 400
        assert resp.json()["message"] == "请输入分析问题"


def test_stream_requires_auth(client):
    resp = client.post("/api/chat/stream", json={"question": "销售额是多少"})
    assert resp.status_code == 401


def test_stream_uses_llm_answer_when_available(client, auth, stub_llm):
    """LLM 可用时：回答来自 LLM，token 拼起来等于 done.answer，且命中一次流式调用。"""
    events = stream(client, auth, "这个月销售额是多少")
    tokens = events_of(events, "token")
    done = events_of(events, "done")[0]
    assert "".join(t["delta"] for t in tokens) == done["answer"]
    assert done["answer"] == STUB_ANSWER
    assert stub_llm.stream_calls >= 1


def test_stream_falls_back_to_template_when_llm_broken(client, auth, session):
    """LLM 不可用：run 不能失败，必须用模板兜底回答并照常走完事件流。"""
    llm_mod.set_llm_client_for_test(_BrokenLLM())
    events = stream(client, auth, "跑鞋脱胶")
    names = [kind for kind, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "error" not in names, "LLM 失败不应让整个 run 报错"

    done = events_of(events, "done")[0]
    assert done["status"] == constants.STATUS_SUCCESS
    answer = done["answer"]
    assert answer, "必须有兜底回答"
    assert "**结论**" in answer and "### 结构化查询" in answer
    assert "".join(t["delta"] for t in events_of(events, "token")) == answer

    # 兜底回答也要落库
    run = session.get(AgentRun, done["run_id"])
    assert run is not None and run.status == constants.STATUS_SUCCESS
    assert run.answer == answer
    # 步骤详情里要能看出降级过
    steps = events_of(events, "step")
    assert any("模板兜底" in s["detail"] for s in steps), [s["detail"] for s in steps]


# ------------------------------------------------------------------ 流式：高风险 + 确认
def test_stream_high_risk_interrupts_and_persists_run(client, auth, session):
    events = stream(client, auth, "静音蓝牙耳机最近差评集中在哪些问题？")
    names = [kind for kind, _ in events]

    assert names.count("interrupt") == 1
    assert names.index("interrupt") < names.index("done")
    # token 必须全部早于 interrupt（契约事件顺序）
    last_token = max(i for i, n in enumerate(names) if n == "token")
    assert last_token < names.index("interrupt")

    interrupt = events_of(events, "interrupt")[0]
    assert interrupt["risk"] == constants.RISK_HIGH
    assert interrupt["message"]
    assert interrupt["actions"], "高风险必须给出待确认动作"
    for action in interrupt["actions"]:
        assert set(action) == {"type", "label", "summary"}
        assert action["type"] in constants.ALLOWED_ACTIONS

    done = events_of(events, "done")[0]
    assert done["awaiting_confirm"] is True
    assert done["status"] == constants.STATUS_PENDING
    assert done["answer"], "暂停时也要先给出回答（前端已渲染）"

    # 关键：interrupt 之前 agent_runs 必须已 commit，前端一刷新就能查到
    run = session.get(AgentRun, done["run_id"])
    assert run is not None
    assert run.status == constants.STATUS_PENDING
    assert run.answer == done["answer"]
    assert run.step_count >= len(events_of(events, "step"))

    # 详情接口此刻可用（不需要额外刷新）
    detail = client.get(f"/api/runs/{done['run_id']}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == constants.STATUS_PENDING


def test_confirm_approve_executes_actions(client, auth, session):
    events = stream(client, auth, "静音蓝牙耳机最近差评集中在哪些问题？")
    run_id = events_of(events, "done")[0]["run_id"]
    planned = events_of(events, "interrupt")[0]["actions"]

    resp = client.post("/api/chat/confirm",
                       json={"run_id": run_id, "decision": "approve"},
                       headers=auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["run_id"] == run_id
    assert data["decision"] == "approve"
    assert data["skipped"] == []
    assert len(data["executed"]) == len(planned)
    assert all(item["ok"] for item in data["executed"]), data["executed"]
    assert {item["type"] for item in data["executed"]} == {a["type"] for a in planned}
    assert "已执行" in data["message"]

    session.expire_all()
    run = session.get(AgentRun, run_id)
    assert run.status == constants.STATUS_SUCCESS


def test_confirm_reject_skips_everything(client, auth, session):
    events = stream(client, auth, "耳机有杂音")
    run_id = events_of(events, "done")[0]["run_id"]
    planned = events_of(events, "interrupt")[0]["actions"]

    resp = client.post("/api/chat/confirm",
                       json={"run_id": run_id, "decision": "reject"},
                       headers=auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["executed"] == []
    assert len(data["skipped"]) == len(planned)
    assert all(item["ok"] is False for item in data["skipped"])
    assert "已跳过" in data["message"]

    session.expire_all()
    assert session.get(AgentRun, run_id).status == constants.STATUS_SUCCESS


def test_confirm_twice_returns_409(client, auth):
    events = stream(client, auth, "静音蓝牙耳机最近差评集中在哪些问题？")
    run_id = events_of(events, "done")[0]["run_id"]
    first = client.post("/api/chat/confirm",
                        json={"run_id": run_id, "decision": "approve"}, headers=auth)
    assert first.status_code == 200

    second = client.post("/api/chat/confirm",
                         json={"run_id": run_id, "decision": "approve"}, headers=auth)
    assert second.status_code == 409
    assert second.json()["message"] in ("该运行已处理过确认", "该运行不在等待确认状态")


def test_confirm_on_finished_run_returns_409(client, auth):
    events = stream(client, auth, "这个月销售额是多少")
    run_id = events_of(events, "done")[0]["run_id"]
    resp = client.post("/api/chat/confirm",
                       json={"run_id": run_id, "decision": "approve"}, headers=auth)
    assert resp.status_code == 409
    assert resp.json()["message"] == "该运行不在等待确认状态"


def test_confirm_without_checkpoint_returns_410(client, auth, session):
    """库里 pending 但进程内没有检查点（模拟重启）→ 410。"""
    pending = session.execute(
        select(AgentRun).where(AgentRun.status == constants.STATUS_PENDING)
    ).scalars().first()
    assert pending is not None, "种子数据应包含 pending 运行"
    resp = client.post("/api/chat/confirm",
                       json={"run_id": pending.id, "decision": "approve"}, headers=auth)
    assert resp.status_code == 410
    assert resp.json()["message"] == "确认检查点已失效，请重新发起分析"


def test_confirm_unknown_run_returns_404(client, auth):
    resp = client.post("/api/chat/confirm",
                       json={"run_id": 999999, "decision": "approve"}, headers=auth)
    assert resp.status_code == 404


def test_confirm_rejects_bad_decision(client, auth):
    resp = client.post("/api/chat/confirm",
                       json={"run_id": 1, "decision": "maybe"}, headers=auth)
    assert resp.status_code == 422


def test_confirm_requires_auth(client):
    resp = client.post("/api/chat/confirm", json={"run_id": 1, "decision": "approve"})
    assert resp.status_code == 401


# ------------------------------------------------------------------ 心跳
def test_sse_heartbeat_ping_keeps_connection_alive(client, auth, monkeypatch):
    """图长时间无事件时，必须每 15 秒发一行 ``: ping`` 注释保活。

    这里把间隔压到 50ms、用慢速假图（250ms）稳定触发，不依赖真实耗时。
    """
    async def slow_stream(run_id, question, session_id=None):
        import asyncio

        await asyncio.sleep(0.25)
        yield {"type": "final", "state": {"answer": "慢速假回答", "route": "semantic"}}

    monkeypatch.setattr(chat_mod, "stream_run", slow_stream)
    monkeypatch.setattr(chat_mod, "HEARTBEAT_SECONDS", 0.05)

    events = stream(client, auth, "慢一点的问题")
    comments = [data for kind, data in events if kind == "__comment__"]
    assert comments, "应至少发出一次心跳注释"
    assert all(line == ": ping" for line in comments)

    # 心跳不破坏事件序列
    names = [kind for kind, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert events_of(events, "done")[0]["answer"] == "慢速假回答"


def test_sse_event_format():
    frame = chat_mod.sse_event("token", {"delta": "你好"})
    assert frame == 'event: token\ndata: {"delta": "你好"}\n\n'
    assert frame.endswith("\n\n")


# ------------------------------------------------------------------ 历史
def test_history_returns_recent_runs(client, auth):
    stream(client, auth, "这个月销售额是多少")
    resp = client.get("/api/chat/history?limit=5", headers=auth)
    assert resp.status_code == 200
    data = resp.json()["data"]
    items = data["items"]
    assert 1 <= len(items) <= 5
    for item in items:
        assert set(item) == {"run_id", "question", "answer", "route", "created_at"}
        assert item["route"] in (constants.ROUTE_STRUCTURED, constants.ROUTE_SEMANTIC,
                                  constants.ROUTE_UNDETERMINED)
        assert item["created_at"]
    created = [item["created_at"] for item in items]
    assert created == sorted(created, reverse=True), "按时间倒序"
    # 刚发起的这次分析必须排在最前（它是最新的）
    assert items[0]["question"] == "这个月销售额是多少"


def test_history_clamps_limit(client, auth):
    resp = client.get("/api/chat/history?limit=0", headers=auth)
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) >= 1
    resp = client.get("/api/chat/history?limit=100000", headers=auth)
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) <= 100


def test_history_requires_auth(client):
    assert client.get("/api/chat/history").status_code == 401
