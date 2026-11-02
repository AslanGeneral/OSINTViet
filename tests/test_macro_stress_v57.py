#!/usr/bin/env python3
"""Deterministic tests for SPEC-MACRO-QUANT v5.7.

Run:
    .venv/bin/python tests/test_macro_stress_v57.py
    .venv/bin/python tests/test_macro_stress_v57.py --live

The default suite is offline and uses a synthetic 36-series panel only to test
the mathematical contracts.  ``--live`` additionally fetches (or reads the
disk cache) the real World Bank panel and checks its shape/source metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from pydantic import ValidationError

from app.core.config import settings
from app.core.plugin_registry import hub
from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
    SeriesPanel,
)
from app.math_engine.macro_stress_v57.engine import MacroStressEngineV57
from app.math_engine.macro_stress_v57.jcs import sha256_value
from app.plugins import load_plugins
from app.services.macro_stress import PANEL_SERIES, fetch_world_bank_panel


def _synthetic_panel(n_dates: int = 40) -> SeriesPanel:
    rng = np.random.default_rng(7)
    n_series = len(PANEL_SERIES)
    factors = rng.normal(size=(n_dates, 5)).cumsum(axis=0) * 0.2
    loadings = rng.normal(0.0, 0.3, size=(n_series, 5))
    core = np.array(
        [
            [0.80, 0.20, 0.10, 0.10, 0.00],
            [0.10, 0.50, 0.40, 0.00, 0.20],
            [0.00, 0.20, 0.80, 0.00, 0.10],
            [0.10, 0.70, 0.10, 0.00, 0.00],
            [0.00, 0.10, 0.80, 0.00, 0.00],
            [0.10, -0.40, 0.20, 0.00, 0.10],
            [0.30, 0.10, -0.20, 0.00, 0.00],
            [0.10, 0.20, -0.30, 0.00, 0.00],
            [0.10, 0.20, -0.30, 0.00, 0.00],
        ]
    )
    loadings[:9] = core
    values = factors @ loadings.T + rng.normal(0.0, 0.15, size=(n_dates, n_series))
    means = np.zeros(n_series)
    means[:9] = [6.5, 3.5, 2.0, 4.5, 88.5, 2.2, 10.0, 1.0, 0.5]
    values += means
    return SeriesPanel(
        series_codes=[spec["code"] for spec in PANEL_SERIES],
        series_names_vi=[spec["name_vi"] for spec in PANEL_SERIES],
        units=[spec["unit"] for spec in PANEL_SERIES],
        dates=[f"{1990 + index}-12-31" for index in range(n_dates)],
        values=np.round(values, 8).tolist(),
        frequency="annual",
        source="synthetic_test",
        source_url="https://example.test/synthetic",
    )


def test_jcs_and_contracts() -> None:
    assert sha256_value({"b": 1, "a": [2.0, -0.0]}) == sha256_value(
        {"a": [2, 0], "b": 1}
    )
    panel = _synthetic_panel(36)
    request = MacroStressRequest(
        panel=panel,
        n_particles=64,
        k_rep=8,
        m_cand=8,
        cma_population=8,
        cma_generations=5,
        horizon=1,
        enable_micro_satellite=False,
    )
    assert request.panel.n_series == 36
    assert request.panel.n_dates == 36
    try:
        MacroStressRequest(
            panel=panel,
            n_particles=64,
            k_rep=65,
            enable_micro_satellite=False,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("k_rep > n_particles must be rejected")
    broken = panel.model_dump(mode="json")
    broken["values"] = broken["values"][:-1]
    try:
        SeriesPanel.model_validate(broken)
    except ValidationError:
        pass
    else:
        raise AssertionError("date/value length mismatch must be rejected")
    print("[test] RFC 8785-compatible JCS + strict request contract: OK")


def test_engine_and_micro() -> None:
    panel = _synthetic_panel(40)
    with tempfile.TemporaryDirectory(prefix="macro_v57_test_") as temporary:
        request = MacroStressRequest(
            panel=panel,
            horizon=2,
            n_particles=256,
            k_rep=8,
            m_cand=32,
            cma_population=8,
            cma_generations=5,
            enable_micro_satellite=True,
            firm_count=32,
            stress_artifact_dir=temporary,
        )
        artifact = MacroStressEngineV57().run(request)
        assert artifact.status == "OK", artifact.error
        assert artifact.engine_version == "5.7.0"
        assert artifact.dimensions["N_series"] == 36
        assert artifact.dimensions["r_factors"] == 5
        assert artifact.dimensions["companion_dim_tilde_r"] == 25
        assert artifact.dimensions["regimes_K"] == 2
        assert artifact.dimensions["haar_rotation_draws_MQ"] == 64
        assert artifact.dimensions["sobol_particles_Np"] == 256
        assert artifact.dimensions["wasserstein_archetypes_Krep"] == 8
        assert artifact.gatekeeper["joint_valid_particles"] >= 50
        assert artifact.gatekeeper["acceptance_rate"] > 0.0
        entropy = artifact.gatekeeper["entropy_pooling"]
        assert entropy["kish_ess"] >= min(200.0, 0.95 * request.n_particles)
        assert entropy["kish_ess_before_floor"] > 0.0
        assert math.isclose(
            sum(artifact.wasserstein["cluster_weights"]), 1.0, rel_tol=1e-7
        )
        assert artifact.wasserstein["k_rep"] == 8
        assert len(artifact.gfevd_matrix) == 36
        assert abs(sum(artifact.gfevd_matrix[0]) - 100.0) < 1e-5
        assert len(artifact.deterministic_briefing) >= 3
        assert all(
            branch.valid_particle_count == request.n_particles
            for branch in artifact.branches.values()
        )
        assert artifact.micro_downscaling["enabled"] is True
        assert artifact.micro_downscaling["n_firms"] == 32
        assert artifact.micro_downscaling["zero_copy"]["raw_columns_zero_copy"] is True
        output = Path(artifact.micro_downscaling["output_arrow_uri"])
        assert output.exists() and output.stat().st_size > 0

        artifact_path = Path(temporary) / "macro_stress_artifact.json"
        assert artifact_path.exists()
        payload = artifact.model_dump(mode="json")
        digest = payload.pop("artifact_sha256")
        assert sha256_value(payload) == digest
        request_digest = sha256_value(request.model_dump(mode="json"))
        assert artifact.request_sha256 == request_digest

        # Plugin bridge: artifact -> compact evidence -> invocation receipt.
        os.environ["MACRO_STRESS_ARTIFACT_PATH"] = str(artifact_path)
        os.environ["MACRO_STRESS_INVOCATION_DIR"] = str(
            Path(temporary) / "invocations"
        )
        load_plugins()
        assert "macro_stress_v57" in hub.theory_algorithms
        assert "arima_garch" not in hub.theory_algorithms
        assert "arima_garch_legacy" not in hub.theory_algorithms
        plugin = hub.theory_algorithms["macro_stress_v57"]
        evidence = asyncio.run(
            plugin({"hypothesis_id": "test_hypothesis", "mechanism_vi": "kiểm thử"})
        )
        assert evidence["status"] == "OK", evidence
        assert evidence["artifact_sha256"] == digest
        assert evidence["dimensions"]["N_series"] == 36
        receipts = list((Path(temporary) / "invocations").glob("invocation_*.json"))
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        assert receipt["plugin"] == "macro_stress_v57"
        assert receipt["artifact_sha256"] == digest

        # A modified artifact must be rejected before it can reach the critic.
        tampered = dict(payload)
        tampered["artifact_sha256"] = "0" * 64
        artifact_path.write_text(json.dumps(tampered), encoding="utf-8")
        rejected = asyncio.run(plugin({}))
        assert rejected["status"] == "ERROR", rejected
        artifact_path.write_text(
            json.dumps(artifact.model_dump(mode="json")), encoding="utf-8"
        )
    print("[test] v5.7 engine, gatekeeper, micro Arrow bridge, plugin: OK")


async def test_live_panel() -> None:
    panel, metadata = await fetch_world_bank_panel("VNM")
    assert panel.n_series == 36
    assert panel.n_dates >= 20
    assert panel.source.startswith("world_bank")
    assert panel.source_url.startswith("https://")
    assert metadata["n_series"] == 36
    assert len(metadata["sources"]) >= 30
    print(
        "[test] live World Bank panel: "
        f"{panel.n_series} series x {panel.n_dates} dates, "
        f"{panel.missing_cells} missing cells, source={panel.source}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also test the live panel")
    args = parser.parse_args()
    test_jcs_and_contracts()
    test_engine_and_micro()
    if args.live:
        asyncio.run(test_live_panel())
    else:
        print("[test] live World Bank panel: SKIP (use --live)")
    print("[test] ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
