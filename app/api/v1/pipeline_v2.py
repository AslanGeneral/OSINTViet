"""HTTP surface for pipeline v2.

The endpoint is synchronous for the caller (the pipeline must finish before a
``PipelineOutput`` exists).  It never accepts an API key in the request body:
BYOK is supplied to the server process through the environment documented in
``.env.example`` or through the CLI's explicit stdin flag.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.pipeline_v2.contracts import (
    HouseholdProfile,
    PersonalContext,
    RequestedMode,
)
from app.pipeline_v2.errors import PipelineError, error_from_exception
from app.pipeline_v2.orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/pipeline-v2", tags=["Pipeline v2"])

_HTTP_BY_EXIT = {
    1: 500,
    2: 400,
    3: 502,
    4: 500,
    5: 422,
    6: 409,
}


class PipelineRunRequest(BaseModel):
    query: str = Field(..., min_length=1)
    mode: RequestedMode = "auto"
    personal_context: Optional[PersonalContext] = None
    household_profile: Optional[HouseholdProfile] = None
    allow_dual: bool = False
    dry_run: bool = False
    use_live: bool = False
    enable_orchestrator: bool = True
    model: Optional[str] = None


@router.post("/run")
async def run_pipeline(request: PipelineRunRequest):
    orchestrator = PipelineOrchestrator(
        model=request.model, include_orchestration_in_output=False
    )
    try:
        result = await orchestrator.run(
            query=request.query,
            requested_mode=request.mode,
            personal_context=request.personal_context,
            household_profile=request.household_profile,
            allow_dual=request.allow_dual,
            dry_run=request.dry_run,
            use_live=request.use_live,
            enable_orchestrator=request.enable_orchestrator,
        )
    except PipelineError as exc:
        contract, exit_code = error_from_exception(exc)
        raise HTTPException(
            status_code=_HTTP_BY_EXIT.get(exit_code, 500),
            detail=contract.model_dump(mode="json"),
        ) from exc
    except Exception as exc:
        contract, exit_code = error_from_exception(exc)
        raise HTTPException(
            status_code=_HTTP_BY_EXIT.get(exit_code, 500),
            detail=contract.model_dump(mode="json"),
        ) from exc

    return JSONResponse(
        content=result.output.model_dump(mode="json"),
        status_code=_HTTP_BY_EXIT.get(result.exit_code, 200)
        if result.exit_code
        else 200,
    )


__all__ = ["PipelineRunRequest", "router"]
