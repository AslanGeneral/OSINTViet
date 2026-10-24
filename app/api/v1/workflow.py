"""API surface for the forced full-circle workflow."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_workflow_run
from app.services.full_circle_workflow import new_run_id

router = APIRouter(prefix="/workflow", tags=["Full-Circle Workflow"])


class WorkflowRunRequest(BaseModel):
    query: Optional[str] = None
    domain_tag: str = "macro_econ"
    reset_memory: bool = False
    fetch_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    model: Optional[str] = None


async def _run_in_background(
    run_id: str,
    request: WorkflowRunRequest,
) -> None:
    try:
        from app.services.full_circle_workflow import FullCircleWorkflow

        workflow = FullCircleWorkflow(model=request.model)
        await workflow.run(
            query=request.query,
            domain_tag=request.domain_tag,
            reset_memory=request.reset_memory,
            fetch_attempts=request.fetch_attempts,
            run_id=run_id,
        )
    except Exception:
        # The workflow persists its own error envelope.  This catch prevents an
        # unhandled background-task exception from poisoning the API process.
        return None


@router.post("/run")
async def start_workflow(
    request: WorkflowRunRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    run_id = new_run_id()
    background_tasks.add_task(_run_in_background, run_id, request)
    return {
        "run_id": run_id,
        "status": "queued",
        # The workflow always performs the live headless-browser fetch.  The
        # optional query only overrides the orchestration question.
        "fetch_live_article": True,
        "reset_memory": request.reset_memory,
    }


@router.get("/{run_id}")
async def get_workflow(run_id: str) -> dict:
    row = await get_workflow_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    try:
        return json.loads(row.payload_json)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"stored workflow payload is not valid JSON: {exc}",
        ) from exc


__all__ = ["router"]
