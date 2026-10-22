# ============================================================
# app/pipeline/stage2_workers_gen.py
# [AR Step 2] >= 4 Worker khác model sinh hypothesis + Proximity check.
# Khung chính là Stage 2 + Stage 3 của UniversalCoScientistPipeline [DT v2.0].
# ============================================================
from typing import Any, Dict, List
from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline
from app.schemas.universal_orchestrator_schema import IngestedClusterSchema


async def run_stage2(
    pipeline: UniversalCoScientistPipeline,
    user_requirement: str,
    cluster: IngestedClusterSchema,
    num_workers: int = 4,
) -> List[Dict[str, Any]]:
    """Sinh >= 4 hypothesis độc lập và gộp các cặp trùng lặp qua Embedding Proximity."""
    return await pipeline.stage2_workers_gen(user_requirement, cluster, num_workers)
