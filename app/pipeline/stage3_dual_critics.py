# ============================================================
# app/pipeline/stage3_dual_critics.py
# [AR Step 3] Dual-Critic + Fuzzy Prolog.
# Ánh xạ sang Stage 4 của UniversalCoScientistPipeline [DT v2.0].
# ============================================================
import asyncio
from typing import Any, Dict, List
from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline
from app.schemas.universal_orchestrator_schema import (
    IngestedClusterSchema, DualCritiqueReport,
)


async def run_stage3(
    pipeline: UniversalCoScientistPipeline,
    hypotheses: List[Dict[str, Any]],
    cluster: IngestedClusterSchema,
) -> Dict[str, DualCritiqueReport]:
    """Chạy Practical + Theoretical Critic song song và Fuzzy Prolog cho từng hypothesis."""
    reports = await asyncio.gather(
        *[pipeline.stage4_run_dual_critics_and_prolog(h, cluster) for h in hypotheses],
        return_exceptions=True,
    )
    return {
        r.hypothesis_id: r for r in reports if isinstance(r, DualCritiqueReport)
    }
