"""运行历史路由。

契约：docs/api-contract.md §5
- GET  /api/runs?page=1&size=20&route=&status=
- GET  /api/runs/{id}                （附 answer / feedback / steps）
- POST /api/runs/{id}/feedback       （up | down | null）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from backend.app.deps import CurrentUser, DbSession
from backend.app.models import AgentRun, RunStep
from backend.app.schemas import FeedbackRequest, ok, page_payload
from backend.app.utils import fmt_dt, norm_page

router = APIRouter(prefix="/api/runs", tags=["runs"])


def run_brief(run: AgentRun) -> dict:
    """契约 §5 列表项结构。"""
    return {
        "id": run.id,
        "question": run.question,
        "route": run.route,
        "status": run.status,
        "duration_ms": int(run.duration_ms or 0),
        "step_count": int(run.step_count or 0),
        "created_at": fmt_dt(run.created_at),
    }


def step_item(step: RunStep) -> dict:
    """契约 §5 步骤结构。"""
    return {
        "seq": int(step.seq or 0),
        "stage": step.stage,
        "actor": step.actor,
        "detail": step.detail,
        "duration_ms": int(step.duration_ms or 0),
    }


def get_run_or_404(db, run_id: int) -> AgentRun:  # noqa: ANN001
    run = db.get(AgentRun, int(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


@router.get("")
def list_runs(db: DbSession, user: CurrentUser,
              page: int = 1, size: int = 20,
              route: str = "", status: str = ""):
    page, size = norm_page(page, size)

    conditions = []
    if route:
        conditions.append(AgentRun.route == route.strip())
    if status:
        conditions.append(AgentRun.status == status.strip())

    total = db.execute(
        select(func.count(AgentRun.id)).where(*conditions)
    ).scalar() or 0

    rows = db.execute(
        select(AgentRun)
        .where(*conditions)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).scalars().all()

    return ok(page_payload([run_brief(r) for r in rows], int(total), page, size))


@router.get("/{run_id}")
def get_run(run_id: int, db: DbSession, user: CurrentUser):
    run = get_run_or_404(db, run_id)
    steps = db.execute(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.seq.asc())
    ).scalars().all()

    data = run_brief(run)
    data.update({
        "answer": run.answer or "",
        "feedback": run.feedback,
        "steps": [step_item(s) for s in steps],
    })
    return ok(data)


@router.post("/{run_id}/feedback")
def submit_feedback(run_id: int, payload: FeedbackRequest,
                    db: DbSession, user: CurrentUser):
    run = get_run_or_404(db, run_id)
    run.feedback = payload.value
    db.commit()
    return ok({"run_id": run.id, "feedback": run.feedback})
