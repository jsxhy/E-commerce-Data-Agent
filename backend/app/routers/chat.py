"""数据分析对话路由：SSE 流式分析、人工确认、历史回显。

契约：docs/api-contract.md §4
- POST /api/chat/stream    SSE：meta → step* → token* → [interrupt] → done/error
- POST /api/chat/confirm   approve / reject 恢复被 interrupt() 暂停的图
- GET  /api/chat/history?limit=20

几个关键取舍（都写进代码注释，避免后来人踩回去）：
1. ``agent_runs`` 行在**推送 interrupt 之前**就 commit：前端收到 interrupt 后可能立刻
   刷新运行列表/详情页，这时必须能查到这条 run。
2. SSE 生成器内部**自己开 DB 会话**：FastAPI 的 ``Depends(get_db)`` 会话在响应开始
   发送前就被关闭了，不能在流式体里继续用。
3. 图暂停后不阻塞 SSE 连接等用户点确认（那会占住连接最多 5 分钟）：流在 ``done``
   处正常结束（``awaiting_confirm=true``），确认由独立请求触发 ``Command(resume=...)``。
4. 每 15 秒无事件发一行 SSE 注释 ``: ping``，防止反向代理掐掉长连接。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from backend.app import constants
from backend.app.agent.confirm_store import get_confirm_store
from backend.app.agent.graph import resume_run, stream_run
from backend.app.deps import CurrentUser, DbSession
from backend.app.models import AgentRun, RunStep
from backend.app.schemas import ChatRequest, ConfirmRequest, ok
from backend.app.utils import fmt_dt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

HEARTBEAT_SECONDS = 15.0
HISTORY_LIMIT_MAX = 100

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # 关掉 nginx 一类代理的响应缓冲，否则 token 会被攒到最后一起发
    "X-Accel-Buffering": "no",
}


# ------------------------------------------------------------------ 工具
def sse_event(event: str, data: dict[str, Any]) -> str:
    """拼一帧 SSE。JSON 用 ensure_ascii=False，中文直接可读。"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _new_session_id() -> str:
    return f"s-{uuid.uuid4().hex[:12]}"


def _db_session():
    from backend.app.db import SessionLocal

    return SessionLocal()


def _step_count(db, run_id: int) -> int:  # noqa: ANN001
    return int(db.execute(
        select(func.count(RunStep.id)).where(RunStep.run_id == int(run_id))
    ).scalar() or 0)


def _elapsed_ms(created_at) -> int:  # noqa: ANN001
    from datetime import datetime

    if created_at is None:
        return 0
    return max(0, int((datetime.now().replace(microsecond=0) - created_at).total_seconds() * 1000))


def persist_step(run_id: int, step: dict[str, Any], seen: set[int]) -> None:
    """把一条 step 落库到 run_steps（seq 重复则跳过，避免唯一约束冲突）。"""
    seq = int(step.get("seq") or 0)
    if seq <= 0 or seq in seen:
        return
    db = _db_session()
    try:
        db.add(RunStep(
            run_id=int(run_id),
            seq=seq,
            stage=str(step.get("stage") or ""),
            actor=str(step.get("actor") or ""),
            detail=str(step.get("detail") or ""),
            duration_ms=int(step.get("duration_ms") or 0),
        ))
        db.commit()
        seen.add(seq)
    except Exception:  # noqa: BLE001 - 步骤落库失败不该中断整个分析
        logger.exception("run=%s 步骤落库失败：seq=%s", run_id, seq)
        db.rollback()
    finally:
        db.close()


def save_run(run_id: int, state: dict[str, Any], status: str) -> dict[str, Any]:  # noqa: ANN001
    """把图状态回写 agent_runs（answer/route/duration_ms/step_count/status）。"""
    db = _db_session()
    try:
        run = db.get(AgentRun, int(run_id))
        if run is None:
            return {}
        run.answer = str(state.get("answer") or "")
        route = state.get("route")
        if route:
            run.route = str(route)
        run.status = status
        run.duration_ms = _elapsed_ms(run.created_at)
        run.step_count = _step_count(db, run_id)
        db.commit()
        return {
            "run_id": run.id,
            "answer": run.answer,
            "route": run.route,
            "status": run.status,
            "duration_ms": int(run.duration_ms or 0),
            "step_count": int(run.step_count or 0),
        }
    except Exception:  # noqa: BLE001
        logger.exception("run=%s 运行结果回写失败", run_id)
        db.rollback()
        return {}
    finally:
        db.close()


async def _producer(run_id: int, question: str, session_id: str,
                    queue: "asyncio.Queue[dict[str, Any]]") -> None:
    """后台消费图事件，塞进队列（主循环负责心跳与转发）。"""
    try:
        async for chunk in stream_run(run_id, question, session_id):
            await queue.put(chunk)
    except Exception as exc:  # noqa: BLE001 - 图内异常统一转成 error 事件
        logger.exception("run=%s 图执行失败", run_id)
        await queue.put({"type": "__error__", "message": f"分析流程异常：{exc}"})
    finally:
        await queue.put({"type": "__end__"})


# ------------------------------------------------------------------ 流式分析
@router.post("/stream")
async def chat_stream(payload: ChatRequest, db: DbSession, user: CurrentUser):
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入分析问题")

    session_id = (payload.session_id or "").strip() or _new_session_id()

    run = AgentRun(question=question, route=constants.ROUTE_UNDETERMINED,
                   status=constants.STATUS_PENDING, answer="")
    db.add(run)
    db.commit()
    run_id = int(run.id)
    created_at = fmt_dt(run.created_at)

    return StreamingResponse(
        _stream_body(run_id, question, session_id, created_at),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _stream_body(run_id: int, question: str, session_id: str,
                       created_at: str) -> AsyncIterator[str]:
    queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    task = asyncio.create_task(_producer(run_id, question, session_id, queue))
    seen: set[int] = set()
    store = get_confirm_store()
    started = time.monotonic()
    try:
        yield sse_event("meta", {
            "run_id": run_id,
            "question": question,
            "created_at": created_at,
            "session_id": session_id,
        })

        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # 15 秒没有任何事件：发注释行保活（SSE 注释会被客户端忽略）
                yield ": ping\n\n"
                continue

            kind = chunk.get("type")

            if kind == "step":
                step = chunk.get("step") or {}
                persist_step(run_id, step, seen)
                yield sse_event("step", step)

            elif kind == "token":
                yield sse_event("token", {"delta": chunk.get("delta") or ""})

            elif kind == "paused":
                state = chunk.get("state") or {}
                interrupt_payload = chunk.get("interrupt") or {}
                # 先登记等待确认，再落库运行结果，最后才推 interrupt / done：
                # 保证前端收到 interrupt 时 agent_runs 与 confirm_store 都已就绪
                await store.put_pending(
                    run_id,
                    state.get("actions") or [],
                    state.get("risk") or constants.RISK_LOW,
                    interrupt_payload.get("message") or "",
                )
                saved = save_run(run_id, state, constants.STATUS_PENDING)
                yield sse_event("interrupt", interrupt_payload)
                yield sse_event("done", {
                    "run_id": run_id,
                    "answer": saved.get("answer") or state.get("answer") or "",
                    "route": saved.get("route") or state.get("route"),
                    "status": constants.STATUS_PENDING,
                    "duration_ms": saved.get("duration_ms") or int((time.monotonic() - started) * 1000),
                    "awaiting_confirm": True,
                })
                return

            elif kind == "final":
                state = chunk.get("state") or {}
                saved = save_run(run_id, state, constants.STATUS_SUCCESS)
                yield sse_event("done", {
                    "run_id": run_id,
                    "answer": saved.get("answer") or state.get("answer") or "",
                    "route": saved.get("route") or state.get("route"),
                    "status": constants.STATUS_SUCCESS,
                    "duration_ms": saved.get("duration_ms") or int((time.monotonic() - started) * 1000),
                    "awaiting_confirm": False,
                })
                return

            elif kind == "__error__":
                message = str(chunk.get("message") or "分析流程异常")
                _mark_failed(run_id, message)
                yield sse_event("error", {"message": message, "run_id": run_id})
                return

            elif kind == "__end__":
                # 没收到 final/paused 就结束了：按异常处理，避免前端一直转圈
                message = "分析流程未正常结束"
                _mark_failed(run_id, message)
                yield sse_event("error", {"message": message, "run_id": run_id})
                return

    except asyncio.CancelledError:  # 客户端断开
        logger.info("run=%s 客户端断开，终止流", run_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("run=%s 流式响应异常", run_id)
        _mark_failed(run_id, str(exc))
        yield sse_event("error", {"message": f"服务端异常：{exc}", "run_id": run_id})
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def _mark_failed(run_id: int, message: str) -> None:
    db = _db_session()
    try:
        run = db.get(AgentRun, int(run_id))
        if run is None:
            return
        run.status = constants.STATUS_FAILED
        run.duration_ms = _elapsed_ms(run.created_at)
        run.step_count = _step_count(db, run_id)
        if message and not run.answer:
            run.answer = f"分析失败：{message}"
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("run=%s 失败状态回写失败", run_id)
        db.rollback()
    finally:
        db.close()


# ------------------------------------------------------------------ 人工确认
@router.post("/confirm")
async def chat_confirm(payload: ConfirmRequest, db: DbSession, user: CurrentUser):
    run_id = int(payload.run_id)
    decision = payload.decision

    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    store = get_confirm_store()
    entry = await store.get(run_id)
    if entry is None:
        # 进程内没有这条 run 的等待记录：
        # - 库里也不是 pending → 本来就不在等确认（409）
        # - 库是 pending 但没有检查点 → 进程重启过，检查点失效（410）
        if run.status != constants.STATUS_PENDING:
            raise HTTPException(status_code=409, detail="该运行不在等待确认状态")
        raise HTTPException(status_code=410, detail="确认检查点已失效，请重新发起分析")
    if entry.get("status") != "pending":
        raise HTTPException(status_code=409, detail="该运行已处理过确认")
    if run.status != constants.STATUS_PENDING:
        raise HTTPException(status_code=409, detail="该运行不在等待确认状态")

    taken = await store.take_decision(run_id, decision)
    if not taken.get("ok"):
        # 并发下的第二次点击
        raise HTTPException(status_code=409, detail="该运行已处理过确认")

    seen: set[int] = set()
    final_state: dict[str, Any] = {}
    try:
        async for chunk in resume_run(run_id, decision):
            kind = chunk.get("type")
            if kind == "step":
                persist_step(run_id, chunk.get("step") or {}, seen)
            elif kind == "final":
                final_state = chunk.get("state") or {}
    except Exception as exc:  # noqa: BLE001 - 恢复失败：检查点可能已被丢弃
        logger.exception("run=%s 恢复执行失败", run_id)
        await store.mark_processed(run_id)
        raise HTTPException(status_code=410,
                            detail="确认检查点已失效，请重新发起分析") from exc

    save_run(run_id, final_state, constants.STATUS_SUCCESS)
    await store.mark_processed(run_id)

    executed = final_state.get("executed") or []
    skipped = final_state.get("skipped") or []
    if decision == "approve":
        message = f"已执行 {len(executed)} 个动作"
        if not executed:
            message = "没有可执行的动作"
    else:
        message = f"已跳过 {len(skipped)} 个动作" if skipped else "已拒绝执行，未做任何变更"

    logger.info("run=%s 确认完成：%s（executed=%s skipped=%s）",
                run_id, decision, len(executed), len(skipped))
    # 严格按契约 §4 只回这 5 个键：回答已经通过 SSE token 流过，这里不重复下发
    return ok({
        "run_id": run_id,
        "decision": decision,
        "executed": executed,
        "skipped": skipped,
        "message": message,
    })


# ------------------------------------------------------------------ 历史
@router.get("/history")
def chat_history(db: DbSession, user: CurrentUser, limit: int = 20):
    try:
        size = int(limit)
    except (TypeError, ValueError):
        size = 20
    size = max(1, min(size, HISTORY_LIMIT_MAX))

    rows = db.execute(
        select(AgentRun)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(size)
    ).scalars().all()

    items = [{
        "run_id": run.id,
        "question": run.question,
        "answer": run.answer or "",
        "route": run.route,
        "created_at": fmt_dt(run.created_at),
    } for run in rows]
    return ok({"items": items})
