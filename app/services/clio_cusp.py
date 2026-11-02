"""Service facade for the CLIO-CUSP module.

Mirrors ``app.services.macro_stress``: build a contract, run one engine, return
an artifact, and expose a compact summary for API/plugin consumers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.math_engine.clio_cusp.contracts import (
    CLIOArtifact,
    CLIOExogenous,
    CLIORequest,
    MacroFeedback,
)
from app.math_engine.clio_cusp.macro_bridge import (
    apply_feedback_to_macro_request,
    clio_to_macro_feedback,
    run_orchestra,
)
from app.math_engine.clio_cusp.orchestrator import run_clio
from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
)


def execute_clio(request: CLIORequest) -> CLIOArtifact:
    """Run the CLIO-CUSP orchestrator on a validated request."""
    return run_clio(request)


def execute_clio_from_macro(
    macro_artifact: MacroStressArtifact,
    *,
    exogenous: Optional[CLIOExogenous] = None,
    horizon: int = 128,
    seed: int = 20261102,
    enable_macro_events: bool = True,
) -> CLIOArtifact:
    """Convenience entry point for a macro artifact already in hand."""
    request = CLIORequest(
        macro_artifact=macro_artifact,
        exogenous=exogenous or CLIOExogenous(),
        horizon=horizon,
        seed=seed,
        enable_macro_events=enable_macro_events,
    )
    return run_clio(request)


def execute_orchestra(
    macro_request: MacroStressRequest,
    clio_request: Optional[CLIORequest] = None,
    *,
    run_feedback: bool = True,
) -> Dict[str, Any]:
    """Run macro -> CLIO -> macro and return every boundary object."""
    return run_orchestra(
        macro_request,
        clio_request=clio_request,
        run_feedback=run_feedback,
    )


def apply_clio_feedback(
    macro_request: MacroStressRequest,
    feedback: MacroFeedback,
) -> MacroStressRequest:
    """Expose the CLIO -> macro request transform."""
    return apply_feedback_to_macro_request(macro_request, feedback)


def clio_summary(artifact: CLIOArtifact) -> Dict[str, Any]:
    """Compact, JSON-native summary for workflow and API layers."""
    if artifact.status != "OK":
        return {
            "status": artifact.status,
            "request_id": artifact.request_id,
            "error": artifact.error,
            "warnings": artifact.warnings,
        }
    return {
        "status": artifact.status,
        "engine_version": artifact.engine_version,
        "request_id": artifact.request_id,
        "backend": artifact.backend,
        "driver_source": artifact.driver_source,
        "horizon": artifact.summary.get("horizon"),
        "terminal": artifact.terminal,
        "summary": artifact.summary,
        "macro_feedback": artifact.macro_feedback.model_dump(mode="json")
        if artifact.macro_feedback is not None
        else None,
        "warnings": artifact.warnings,
        "artifact_sha256": artifact.artifact_sha256,
    }


__all__ = [
    "apply_clio_feedback",
    "clio_summary",
    "clio_to_macro_feedback",
    "execute_clio",
    "execute_clio_from_macro",
    "execute_orchestra",
]
