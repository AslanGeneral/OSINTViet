#!/usr/bin/env python3
"""Run SPEC-MACRO-QUANT v5.7 against the live World Bank panel.

Examples:
    .venv/bin/python run_macro_stress.py --fast
    .venv/bin/python run_macro_stress.py --artifact-dir /tmp/opencode/stress-full

This is the standalone operator CLI.  The full-circle workflow calls the same
request builder and engine through ``app.services.macro_stress``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.math_engine.macro_stress_v57.contracts import MacroStressRequest
from app.services.macro_stress import (
    execute_macro_stress,
    fetch_world_bank_panel,
    macro_stress_summary,
)


async def _run(args: argparse.Namespace) -> int:
    panel, metadata = await fetch_world_bank_panel(country_code=args.country)
    output_dir = Path(
        args.artifact_dir
        or f"/tmp/opencode/macro_stress_v57_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    ).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    request = MacroStressRequest(
        panel=panel,
        country_code=args.country.upper(),
        horizon=3 if args.fast else settings.MACRO_STRESS_HORIZON,
        n_particles=512 if args.fast else settings.MACRO_STRESS_N_PARTICLES,
        k_rep=12 if args.fast else settings.MACRO_STRESS_K_REP,
        m_cand=64 if args.fast else settings.MACRO_STRESS_M_CAND,
        cma_population=(
            12 if args.fast else settings.MACRO_STRESS_CMA_POPULATION
        ),
        cma_generations=(
            5 if args.fast else settings.MACRO_STRESS_CMA_GENERATIONS
        ),
        enable_micro_satellite=(
            False if args.no_micro else settings.MACRO_STRESS_ENABLE_MICRO
        ),
        firm_count=100 if args.fast else settings.MACRO_STRESS_FIRM_COUNT,
        firm_data_path=settings.MACRO_STRESS_FIRM_DATA_PATH or None,
        stress_artifact_dir=str(output_dir),
        article_title_vi="Kiểm thử vận hành độc lập SPEC-MACRO-QUANT v5.7",
        article_context={"panel_metadata": metadata, "operator_cli": True},
    )
    (output_dir / "macro_stress_request.json").write_text(
        json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    started = time.time()
    artifact = execute_macro_stress(request, artifact_dir=str(output_dir))
    summary = macro_stress_summary(artifact)
    summary["wall_seconds"] = round(time.time() - started, 3)
    summary["request_path"] = str(output_dir / "macro_stress_request.json")
    summary["artifact_path"] = str(output_dir / "macro_stress_artifact.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if artifact.status == "OK" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="VNM", help="World Bank country code")
    parser.add_argument("--artifact-dir", default="", help="Output directory")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="512 particles / 12 scenarios for a quick operational check",
    )
    parser.add_argument(
        "--no-micro",
        action="store_true",
        help="Disable the Arrow micro downscaling satellite",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
