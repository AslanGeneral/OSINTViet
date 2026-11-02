#!/usr/bin/env python3
"""Deterministic tests for the CLIO-CUSP 8192 v4.0 module.

Run:
    .venv/bin/python tests/test_clio_cusp_module.py
    .venv/bin/python tests/test_clio_cusp_module.py --quick

The suite uses the real v5.7 engine for one small integration run and otherwise
uses a hand-built signed artifact so failures are attributable to the bridge or
to CLIO rather than to macro calibration.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from pydantic import ValidationError

from app.math_engine.clio_cusp import (
    CLIOExogenous,
    CLIORequest,
    EventInput,
    InterestGroupInput,
    MacroFeedback,
    apply_feedback_to_macro_request,
    clio_to_macro_feedback,
    map_macro_to_drivers,
    run_clio,
)
from app.math_engine.clio_cusp.contracts import (
    CoupCoefficients,
    CuspWeights,
    MilitaryInputs,
    PowerInputs,
    SignedNetworkInput,
    sha256_value,
)
from app.math_engine.clio_cusp.cusp import (
    control_particles,
    draw_sobol_nets_numpy,
    initial_particles,
    numpy_cusp_step,
)
from app.math_engine.clio_cusp.kernel import (
    M_PARTICLES,
    R_REPLICATES,
    penalty_increment,
    compute_structural_virality_o_n,
)
from app.math_engine.clio_cusp.state import update_protester_pool
from app.math_engine.clio_cusp.subsystems import (
    classify_agitated,
    esim_conversion,
    epstein_activation_rate,
    interest_group_magnitudes,
    plover_goldstein,
    power_layer,
)
from app.math_engine.macro_stress_v57.contracts import (
    BranchMetrics,
    MacroStressArtifact,
    MacroStressRequest,
    SeriesPanel,
)


def _synthetic_artifact() -> MacroStressArtifact:
    active = BranchMetrics(
        gdp_yoy_mean_pct=3.1,
        gdp_yoy_trough_mean_pct=-1.2,
        gdp_yoy_std_pct=1.8,
        gdp_tail_loss_var_pct=3.4,
        gdp_tail_loss_es_pct=4.1,
        fx_deprec_var_pct=8.0,
        fx_reserves_drain_billion_usd=4.0,
        interbank_peak_p95_pct=7.8,
        npl_breach_probability=0.22,
        npl_terminal_mean_pct=4.5,
        valid_particle_count=128,
        acceptance_rate=0.9,
    )
    passive = BranchMetrics(
        gdp_yoy_mean_pct=2.4,
        gdp_yoy_trough_mean_pct=-2.0,
        gdp_yoy_std_pct=2.1,
        gdp_tail_loss_var_pct=4.5,
        gdp_tail_loss_es_pct=5.4,
    )
    return MacroStressArtifact(
        status="OK",
        engine_version="5.7.0",
        request_id="macro_synthetic_001",
        artifact_sha256="a" * 64,
        nowcast={
            "core_series": {
                "GDP_YOY": 4.0,
                "CPI_YOY": 3.6,
                "FX_DEPREC": 2.2,
                "IB_RATE": 5.1,
                "FX_RESERVES": 85.0,
                "NPL_RATIO": 2.8,
                "CREDIT_GROWTH": 11.0,
                "CURRENT_ACCOUNT": 0.8,
                "FISCAL_BALANCE": -3.2,
            },
            "regime": {"stress_probability_last": 0.31},
        },
        branches={"active": active, "passive": passive},
        gatekeeper={"acceptance_rate": 0.88},
        structural_satellites={
            "enabled": True,
            "bohn_sovereign_debt": {"debt_to_gdp_pct": {"mean": 55.0}},
            "adrian_shin_bank_car": {"car_pct": {"mean": 10.5}},
            "nelson_siegel": {"yields_pct": {"10Y": {"mean": 6.2}}},
        },
        micro_downscaling={
            "enabled": True,
            "expected_stress_next_q": {"mean": 0.42},
            "var99_stress_next_q": {"mean": 0.71},
            "share_expected_stress_above_080": 0.18,
        },
        adversarial_shock={
            "best_physical": [1.2, -0.4, 0.5, 0.2],
            "best_objective": 2.7,
        },
        deterministic_briefing=["synthetic fixture"],
    )


def _small_macro_request() -> MacroStressRequest:
    codes = [
        "GDP_YOY",
        "CPI_YOY",
        "FX_DEPREC",
        "IB_RATE",
        "FX_RESERVES",
        "NPL_RATIO",
        "CREDIT_GROWTH",
        "CURRENT_ACCOUNT",
        "FINANCIAL_ACCOUNT",
    ]
    dates = [f"{2000 + index}-12-31" for index in range(20)]
    values = []
    for index in range(20):
        values.append(
            [
                5.5 + 0.1 * math.sin(index),
                3.2 + 0.05 * math.cos(index),
                1.5 + 0.2 * math.sin(index / 3.0),
                5.0,
                85.0,
                2.5,
                10.0,
                0.8,
                -0.4,
            ]
        )
    panel = SeriesPanel(
        series_codes=codes,
        dates=dates,
        values=values,
        frequency="annual",
        source="synthetic_test",
    )
    return MacroStressRequest(
        panel=panel,
        horizon=1,
        n_particles=64,
        k_rep=8,
        m_cand=8,
        cma_population=8,
        cma_generations=5,
        enable_micro_satellite=False,
        enable_structural_satellites=True,
        seed=11,
    )


def test_contracts_and_event_taxonomy() -> None:
    request = CLIORequest()
    assert 1 <= request.horizon <= 128
    dumped = request.model_dump(mode="json")
    assert dumped["engine_version"] == "4.0.0"
    try:
        CLIORequest(horizon=129)
    except ValidationError:
        pass
    else:
        raise AssertionError("horizon > 128 must be rejected")
    try:
        PowerInputs(faction_shares=[0.9, 0.2])
    except ValidationError:
        pass
    else:
        raise AssertionError("non-normalized faction shares must be rejected")

    event = EventInput(
        event_type="PROTEST",
        mode="riot_violent",
        context="regime_change",
        probabilities={"PROTEST": 0.6, "COERCE": 0.4},
        headcount=12_000,
        damage=3.0,
        parent_indices=[-1, 0, 0, 2, 2],
        new_shares_per_day=500.0,
    )
    score = plover_goldstein(event)
    assert -10.0 <= score <= 8.3
    assert classify_agitated(event)
    assert compute_structural_virality_o_n([-1, 0, 0, 2, 2]) > 0.0
    for impact in (0.03, 0.2, 0.7, 1.5):
        increment = penalty_increment(impact)
        assert 0.0 < increment <= 0.99
    print("[test] contracts, PLOVER taxonomy and penalty ladder: OK")


def test_cusp_backend() -> None:
    v1, u1 = draw_sobol_nets_numpy(123)
    v2, u2 = draw_sobol_nets_numpy(123)
    assert v1.shape == (R_REPLICATES, M_PARTICLES)
    assert np.array_equal(v1, v2) and np.array_equal(u1, u2)
    assert float(v1.min()) >= 0.0 and float(v1.max()) < 1.0

    X = initial_particles()
    a, b = control_particles(0.1, 0.4, 0.02, 0.02, seed=7, day=3)
    sigma = np.full((R_REPLICATES, M_PARTICLES), 0.04)
    first = numpy_cusp_step(X, a, b, sigma, v1, u1)
    second = numpy_cusp_step(X, a, b, sigma, v1, u1)
    assert np.array_equal(first["X_next"], second["X_next"])
    assert np.all(np.isfinite(first["X_next"]))
    for key in ("S_next_mRQMC", "P_collapse_mRQMC", "P_hysteresis_mRQMC"):
        assert 0.0 <= first[key] <= 1.0
    assert first["S_next"].min() > 0.0 and first["S_next"].max() < 1.0
    print("[test] NumPy Array-mRQMC step, determinism and probability bounds: OK")


def test_interest_group_and_epstein() -> None:
    groups = [
        InterestGroupInput(
            group_id="state",
            node_indices=[0],
            assets=100.0,
            media_virality=10.0,
            state_affiliated=True,
        ),
        InterestGroupInput(
            group_id="oligarchs",
            node_indices=[1],
            assets=60.0,
            media_virality=30.0,
        ),
        InterestGroupInput(
            group_id="unions",
            node_indices=[2],
            assets=40.0,
            media_virality=20.0,
        ),
    ]
    params = CLIOExogenous(
        power=PowerInputs(
            interest_groups=groups,
            network=SignedNetworkInput(
                n_nodes=3,
                state_nodes=[0],
                positive_edges=[],
                negative_edges=[
                    {"source": 1, "target": 0, "weight": 2.0},
                    {"source": 2, "target": 0, "weight": 1.0},
                ],
            ),
        )
    )
    layer = power_layer(params, previous_psi=0.0)
    assert 0.0 <= layer["sclerosis"] < 1.0
    assert layer["omega_total"] > 0.0
    assert all(value >= 0.0 for value in layer["omega_groups"])

    rate_low = epstein_activation_rate(
        penalty=0.05, police_count=10_000, active_protesters=10_000, agents=512
    )
    rate_high = epstein_activation_rate(
        penalty=0.85, police_count=10_000, active_protesters=10_000, agents=512
    )
    assert rate_high >= rate_low
    normalized, raw = esim_conversion(params)
    assert 0.0 <= normalized <= 1.0
    assert raw >= 0.0
    print("[test] signed graph Omega, Epstein activation and ESIM conversion: OK")


def test_standalone_orchestrator_determinism() -> None:
    request = CLIORequest(horizon=16, seed=2026)
    first = run_clio(request)
    second = run_clio(request)
    assert first.status == "OK", first.error
    assert second.status == "OK", second.error
    assert first.artifact_sha256 == second.artifact_sha256
    assert len(first.days) == 16
    assert first.dimensions["particle_steps"] == 16 * 9 * 8192
    assert first.summary["max_p_collapse"] <= 1.0
    assert first.terminal["stability"] > 0.0
    print("[test] standalone 16-day orchestrator and bitwise determinism: OK")


def test_macro_bridge_and_feedback() -> None:
    artifact = _synthetic_artifact()
    exo, mapping, warnings = map_macro_to_drivers(
        artifact, CLIOExogenous(), enable_macro_events=True
    )
    assert mapping["raw"]["gdp_yoy_pct"] == 4.0
    assert mapping["raw"]["regime_stress_probability"] == 0.31
    assert exo.rdi_override is not None and exo.rdi_override > 0.0
    assert exo.foreign_pressure > 0.0
    assert any(event.event_id.startswith("macro_") for event in exo.events.events)
    assert 0.0 <= exo.psychology.sigma_theta < 0.1223

    clio_request = CLIORequest(macro_artifact=artifact, horizon=12, seed=99)
    expected_input_hash = sha256_value(clio_request.model_dump(mode="json"))
    run = run_clio(clio_request)
    assert run.status == "OK", run.error
    assert run.request_sha256 == expected_input_hash
    assert run.audit["mapped_request_sha256"] not in (None, "", expected_input_hash)
    assert run.driver_source == "macro_stress_v57"
    assert run.macro_request_id == artifact.request_id
    feedback = clio_to_macro_feedback(run, artifact)
    assert isinstance(feedback, MacroFeedback)
    assert feedback.gdp_yoy_stressed_pct <= 4.0
    assert feedback.npl_uplift_pct >= 0.0
    assert feedback.fx_pressure_uplift_pct >= 0.0

    macro_request = _small_macro_request()
    rerun_request = apply_feedback_to_macro_request(macro_request, feedback)
    assert rerun_request.qpm_anchor_gdp_yoy_pct == feedback.gdp_yoy_stressed_pct
    assert rerun_request.article_context["clio_feedback"]["stress_index"] == feedback.stress_index
    overlay = rerun_request.article_context["macro_panel_overlay"]
    assert overlay["shifts"]["GDP_YOY"] == round(feedback.gdp_yoy_adjustment_pct, 6)
    assert "NPL_RATIO" in overlay["shifts"] and "FX_DEPREC" in overlay["shifts"]
    gdp_index = rerun_request.panel.series_codes.index("GDP_YOY")
    original_gdp = macro_request.panel.values[-1][gdp_index]
    shifted_gdp = rerun_request.panel.values[-1][gdp_index]
    assert math.isclose(
        shifted_gdp - original_gdp,
        feedback.gdp_yoy_adjustment_pct,
        rel_tol=1e-9,
        abs_tol=1e-6,
    )
    # The caller's panel must not be mutated by the bridge.
    assert macro_request.panel.values[-1][gdp_index] == original_gdp

    # World Bank alias codes must resolve to the same overlay channel.
    alias_request = macro_request.model_copy(deep=True)
    alias_request.panel.series_codes[gdp_index] = "NY.GDP.MKTP.KD.ZG"
    alias_rerun = apply_feedback_to_macro_request(alias_request, feedback)
    assert alias_rerun.article_context["macro_panel_overlay"]["shifts"]["GDP_YOY"] == round(
        feedback.gdp_yoy_adjustment_pct, 6
    )
    print("[test] macro -> CLIO driver mapping and CLIO -> macro feedback: OK")


def test_protester_pool_ignores_future_events() -> None:
    params = CLIOExogenous()
    params.events.events = [
        EventInput(day=1, headcount=1_000.0),
        EventInput(day=5, headcount=500_000.0),
    ]
    params.events.active_protesters = 10_000.0
    updated = update_protester_pool(params, params.events.events, turnout=1.0, day=1)
    # 0.70 * 10,000 + 0.30 * 1,000 = 7,300.  A future-event leak would raise
    # this to 0.70 * 10,000 + 0.30 * 501,000 = 157,300.
    assert math.isclose(updated, 7_300.0, rel_tol=1e-12, abs_tol=1e-9)
    print("[test] protester pool uses only same-day events: OK")


def test_real_macro_engine_bridge() -> None:
    from app.math_engine.macro_stress_v57.engine import MacroStressEngineV57

    request = _small_macro_request()
    macro = MacroStressEngineV57().run(request)
    assert macro.status == "OK", macro.error
    run = run_clio(CLIORequest(macro_artifact=macro, horizon=8, seed=5))
    assert run.status == "OK", run.error
    assert run.macro_feedback is not None
    assert run.macro_feedback.source_macro_request_id == macro.request_id
    assert math.isfinite(run.macro_feedback.gdp_yoy_stressed_pct)
    print("[test] real v5.7 artifact -> 8-day CLIO -> feedback: OK")


TESTS = [
    test_contracts_and_event_taxonomy,
    test_cusp_backend,
    test_interest_group_and_epstein,
    test_standalone_orchestrator_determinism,
    test_macro_bridge_and_feedback,
    test_protester_pool_ignores_future_events,
    test_real_macro_engine_bridge,
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="skip the real macro integration")
    args = parser.parse_args()
    selected = [test for test in TESTS if not (args.quick and test is test_real_macro_engine_bridge)]
    for test in selected:
        test()
    print(f"[test] ALL OK ({len(selected)} tests)")


if __name__ == "__main__":
    main()
