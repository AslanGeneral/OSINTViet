# ============================================================
# app/pipeline/stage1_ingest_embed.py
# [AR Step 1] Load data -> Embedding -> Raw JSON cluster.
# Khung chính là Stage 1 của UniversalCoScientistPipeline [DT v2.0].
# ============================================================
from typing import Any, Dict, List
from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline
from app.schemas.universal_orchestrator_schema import IngestedClusterSchema


async def run_stage1(
    pipeline: UniversalCoScientistPipeline,
    user_requirement: str,
    raw_inputs: List[Dict[str, Any]],
    domain_tag: str,
) -> IngestedClusterSchema:
    """Chạy Stage 1: Ingestor plugins + Embed v5 Pro + Search-error checking + Ontology."""
    return await pipeline.stage1_ingest_and_structure(user_requirement, raw_inputs, domain_tag)
