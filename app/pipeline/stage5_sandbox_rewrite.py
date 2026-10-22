# ============================================================
# app/pipeline/stage5_sandbox_rewrite.py
# [AR Step 5 + 6 + 7] Sandbox ép tham số -> JSON chuẩn hóa -> Pareto export.
# Ánh xạ sang Stage 6 + Stage 7 của UniversalCoScientistPipeline [DT v2.0].
# ============================================================
from typing import Any, Dict, List, Optional

from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline
from app.schemas.universal_orchestrator_schema import (
    DualCritiqueReport, IngestedClusterSchema, MetricBoundSpec,
    FinalOrchestratorOutput,
)


async def run_stage5(
    pipeline: UniversalCoScientistPipeline,
    task_id: str,
    winner: Dict[str, Any],
    report: Optional[DualCritiqueReport],
    cluster: IngestedClusterSchema,
    parameter_bounds: Optional[List[MetricBoundSpec]] = None,
) -> Dict[str, Any]:
    """Đưa winner vào UniversalParameterForcingSandbox và nhận chứng nhận HMAC."""
    return await pipeline.stage6_sandbox_rewrite(
        task_id, winner, report, cluster, parameter_bounds
    )


async def export_stage7(
    pipeline: UniversalCoScientistPipeline,
    task_id: str,
    domain_tag: str,
    sandbox_results: List[Dict[str, Any]],
) -> FinalOrchestratorOutput:
    """Đóng gói Pareto frontier + dead branches + luật phạt đã biên dịch."""
    viable = [r for r in sandbox_results if r.get("status") == "VIABLE"]
    dead = [r for r in sandbox_results if r.get("status") != "VIABLE"]
    return await pipeline.stage7_export(task_id, domain_tag, viable, dead)
