#!/usr/bin/env python3
"""Stress-test CLIO-CUSP 8192 v4.0 in orchestra with macro-stress v5.7.

Flow per scenario:
    synthetic 36-series panel -> v5.7 macro artifact
    -> CLIO driver bridge -> 128-day Xi_t run
    -> CLIO feedback -> v5.7 rerun with the feedback QPM anchor

The script checks bitwise CLIO determinism, probability/finite invariants,
and that the macro rerun consumes the feedback anchor.  It writes a JSON report
and prints one comparison row per scenario.

Usage:
    .venv/bin/python scripts/stress_clio_cusp_orchestra.py
    .venv/bin/python scripts/stress_clio_cusp_orchestra.py --quick
    .venv/bin/python scripts/stress_clio_cusp_orchestra.py --scenarios baseline,severe --repeat 1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from app.math_engine.clio_cusp import (
    CLIOExogenous,
    CLIORequest,
    EventInput,
    InterestGroupInput,
    RemediationInput,
    apply_feedback_to_macro_request,
    run_clio,
)
from app.math_engine.clio_cusp.contracts import (
    EventInputs,
    MilitaryInputs,
    PowerInputs,
    PsychologyInputs,
    SignedNetworkInput,
    StructuralInputs,
    WeatherInputs,
)
from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
    SeriesPanel,
)
from app.math_engine.macro_stress_v57.engine import MacroStressEngineV57
from app.services.macro_stress import PANEL_SERIES


SCENARIOS = {
    "baseline": 0.15,
    "adverse": 0.90,
    "severe": 1.80,
}


def _synthetic_panel(n_dates: int = 40, shock: float = 0.0) -> SeriesPanel:
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

    # Scenario shift on the nine core workflow series.  A constant shift is
    # intentional: it tests the mapping without pretending to know the exact
    # timing of a future shock.
    values[:, 0] -= 4.0 * shock  # GDP_YOY
    values[:, 1] += 6.0 * shock  # CPI_YOY
    values[:, 2] += 10.0 * shock  # FX_DEPREC
    values[:, 3] += 3.0 * shock  # IB_RATE
    values[:, 4] -= 10.0 * shock  # FX_RESERVES
    values[:, 5] += 4.0 * shock  # NPL_RATIO
    values[:, 6] -= 2.0 * shock  # CREDIT_GROWTH
    values[:, 7] -= 1.5 * shock  # CURRENT_ACCOUNT
    values[:, 8] -= 2.0 * shock  # FINANCIAL_ACCOUNT

    return SeriesPanel(
        series_codes=[spec["code"] for spec in PANEL_SERIES],
        series_names_vi=[spec["name_vi"] for spec in PANEL_SERIES],
        units=[spec["unit"] for spec in PANEL_SERIES],
        dates=[f"{1980 + index}-12-31" for index in range(n_dates)],
        values=np.round(values, 8).tolist(),
        frequency="annual",
        source="synthetic_stress_orchestra",
        source_url="https://example.test/synthetic",
    )


def _broadcast_tree(nodes: int) -> List[int]:
    return [-1] + [(index - 1) // 2 for index in range(1, nodes)]


# Event magnitude by scenario.  The mapping and macro channel are identical
# across scenarios, so the only difference in the CLIO pulse is the size of
# the observed protest wave.
_SCENARIO_EVENTS = {
    "baseline": {
        "policy_headcount": 3_000.0,
        "policy_damage": 0.20,
        "policy_shares": 80.0,
        "policy_tree": 15,
        "agitated_headcount": 500.0,
        "agitated_damage": 0.30,
        "agitated_shares": 120.0,
        "agitated_tree": 15,
        "remediation": 0.020,
    },
    "adverse": {
        "policy_headcount": 15_000.0,
        "policy_damage": 0.40,
        "policy_shares": 400.0,
        "policy_tree": 31,
        "agitated_headcount": 800.0,
        "agitated_damage": 0.50,
        "agitated_shares": 250.0,
        "agitated_tree": 31,
        "remediation": 0.010,
    },
    "severe": {
        "policy_headcount": 60_000.0,
        "policy_damage": 1.50,
        "policy_shares": 1_500.0,
        "policy_tree": 63,
        "agitated_headcount": 8_000.0,
        "agitated_damage": 4.00,
        "agitated_shares": 2_000.0,
        "agitated_tree": 31,
        "remediation": 0.008,
    },
}


def _scenario_exogenous(scenario: str = "baseline") -> CLIOExogenous:
    pulse = _SCENARIO_EVENTS.get(scenario)
    if pulse is None:
        raise ValueError(f"unknown scenario {scenario!r}")
    groups = [
        InterestGroupInput(
            group_id="state",
            label="incumbent state coalition",
            node_indices=[0],
            assets=100.0,
            media_virality=10.0,
            state_affiliated=True,
        ),
        InterestGroupInput(
            group_id="state_media",
            label="state media apparatus",
            node_indices=[1],
            assets=70.0,
            media_virality=35.0,
            state_affiliated=True,
        ),
        InterestGroupInput(
            group_id="oligarchs",
            label="oligarch network",
            node_indices=[2],
            assets=120.0,
            media_virality=50.0,
        ),
        InterestGroupInput(
            group_id="unions",
            label="labor unions",
            node_indices=[3],
            assets=80.0,
            media_virality=40.0,
        ),
    ]
    network = SignedNetworkInput(
        n_nodes=4,
        state_nodes=[0, 1],
        positive_edges=[
            {"source": 0, "target": 1, "weight": 1.0},
            {"source": 1, "target": 0, "weight": 1.0},
            {"source": 2, "target": 3, "weight": 0.5},
        ],
        negative_edges=[
            {"source": 2, "target": 0, "weight": 2.0},
            {"source": 3, "target": 0, "weight": 1.0},
            {"source": 2, "target": 1, "weight": 1.5},
        ],
    )
    events = [
        EventInput(
            event_id="policy_protest_day3",
            day=3,
            source="acled_like",
            target="government",
            event_type="PROTEST",
            mode="demonstrate",
            context="policy",
            headcount=pulse["policy_headcount"],
            damage=pulse["policy_damage"],
            new_shares_per_day=pulse["policy_shares"],
            parent_indices=_broadcast_tree(pulse["policy_tree"]),
            is_agitated=False,
        ),
        EventInput(
            event_id="agitated_protest_day10",
            day=10,
            source="acled_like",
            target="government",
            event_type="PROTEST",
            mode="riot_violent",
            context="regime_change",
            headcount=pulse["agitated_headcount"],
            damage=pulse["agitated_damage"],
            new_shares_per_day=pulse["agitated_shares"],
            parent_indices=_broadcast_tree(pulse["agitated_tree"]),
            is_agitated=True,
        ),
        EventInput(
            event_id="aid_positive_day5",
            day=5,
            source="acled_like",
            target="citizens",
            event_type="PROVIDE_AID",
            mode="aid",
            context="welfare",
            headcount=1_000,
            goldstein_expectation=4.0,
            is_agitated=False,
        ),
    ]
    return CLIOExogenous(
        structural=StructuralInputs(
            gdp_pc=3_500.0,
            median_wage=950.0,
            urban_share=0.42,
            youth_share=0.21,
            top10_share=0.46,
            bottom50_share=0.14,
            top1_share=0.21,
            labor_share=0.50,
            elite_share=0.10,
            public_debt_gdp=0.45,
            interest_plus_deficit_tax=0.65,
            reform=0.06,
            expected_value=100.0,
            capability_value=70.0,
            productivity_growth=0.025,
            median_wage_growth=0.012,
        ),
        power=PowerInputs(
            faction_shares=[0.45, 0.35, 0.20],
            selectorate=100.0,
            coalition=25.0,
            lambda_olson=0.030,
            stability_duration=25.0,
            veto_players=3.0,
            rent_gdp=0.08,
            interest_groups=groups,
            network=network,
        ),
        military=MilitaryInputs(
            military_shares=[0.45, 0.35, 0.20],
            active_military=320_000.0,
            paramilitary=60_000.0,
            reserve=2_100_000.0,
            military_expenditure=5.5e9,
            gdp=4.0e11,
            population=100_000_000.0,
            baseline_pay=2_100.0,
            eta_coup=0.50,
        ),
        events=EventInputs(
            events=events,
            remediation=[RemediationInput(amount=pulse["remediation"], age_days=3.0)],
            police_count=25_000.0,
            active_protesters=35_000.0,
            population_pool=10_000_000.0,
            coercive_intensity=0.25,
            eta_select=0.55,
        ),
        weather=WeatherInputs(
            z_temp=0.8,
            z_pm25=0.4,
            rain_mm=1.2,
            daytime_temp_c=33.0,
        ),
        psychology=PsychologyInputs(mu_theta=0.25, sigma_theta=0.10),
    )


def _macro_summary(artifact: MacroStressArtifact) -> Dict[str, Any]:
    active = artifact.branches.get("active")
    passive = artifact.branches.get("passive")
    return {
        "status": artifact.status,
        "request_id": artifact.request_id,
        "execution_time_seconds": artifact.execution_time_seconds,
        "nowcast_core": (artifact.nowcast or {}).get("core_series", {}),
        "regime_stress_probability": (artifact.nowcast or {})
        .get("regime", {})
        .get("stress_probability_last"),
        "gatekeeper": {
            key: (artifact.gatekeeper or {}).get(key)
            for key in (
                "acceptance_rate",
                "joint_valid_particles",
                "fallback_used",
            )
        },
        "qpm_anchor": (artifact.gatekeeper or {})
        .get("entropy_pooling", {})
        .get("target_gdp_yoy_pct"),
        "entropy_pooling": (artifact.gatekeeper or {}).get("entropy_pooling", {}),
        "baseline": (artifact.branches or {}).get("baseline").model_dump(mode="json")
        if (artifact.branches or {}).get("baseline") is not None
        else None,
        "active": active.model_dump(mode="json") if active else None,
        "passive": passive.model_dump(mode="json") if passive else None,
        "structural_satellites": artifact.structural_satellites,
        "micro_downscaling": artifact.micro_downscaling,
        "adversarial_objective": (artifact.adversarial_shock or {}).get("best_objective"),
        "tail_risk": [row.model_dump(mode="json") for row in artifact.tail_risk[:9]],
    }


def _clio_invariants(artifact: Any, horizon: int) -> Dict[str, bool]:
    days = artifact.days
    checks: Dict[str, bool] = {
        "status_ok": artifact.status == "OK",
        "horizon_complete": len(days) == horizon,
        "hash_present": bool(artifact.artifact_sha256),
        "days_finite": True,
        "probabilities_bounded": True,
        "stability_bounded": True,
        "event_records_valid": True,
    }
    for day in days:
        values = [
            day.psi,
            day.mmp_aug,
            day.emp,
            day.sfd,
            day.rdi,
            day.hhi_pol,
            day.pci,
            day.enp,
            day.sclerosis,
            day.omega_total,
            day.smr,
            day.f_mil,
            day.asi_outcome,
            day.penalty,
            day.bias,
            day.k_exo,
            day.spi,
            day.hawkes,
            day.latent_discontent,
            day.a_t,
            day.b_t,
            day.sigma_t,
            day.x_mean,
            day.stability,
            day.p_collapse,
            day.p_hysteresis,
            day.stress_index,
        ]
        if not all(math.isfinite(float(value)) for value in values):
            checks["days_finite"] = False
        for probability in (
            day.p_attempt,
            day.p_success,
            day.p_regime_change,
            day.p_bloody_failed,
            day.p_collapse,
            day.p_hysteresis,
            day.stress_index,
            day.epstein_activation_rate,
        ):
            if not 0.0 <= float(probability) <= 1.0:
                checks["probabilities_bounded"] = False
        if not 0.0 < float(day.stability) < 1.0:
            checks["stability_bounded"] = False
        for event in day.events:
            if not (0.0 <= event.goldstein_plover <= 8.3 or -10.0 <= event.goldstein_plover < 0.0):
                checks["event_records_valid"] = False
            if not 0.0 <= event.severity <= 1.0:
                checks["event_records_valid"] = False
    checks["macro_feedback_present"] = artifact.macro_feedback is not None
    return checks


def run_scenario(
    name: str,
    shock: float,
    *,
    horizon: int,
    macro_particles: int,
    cma_generations: int,
    repeat: int,
    run_feedback: bool,
) -> Dict[str, Any]:
    engine = MacroStressEngineV57()
    exogenous = _scenario_exogenous(name)
    timings: Dict[str, float] = {}

    with tempfile.TemporaryDirectory(prefix=f"clio_orchestra_{name}_") as temporary:
        macro_request = MacroStressRequest(
            panel=_synthetic_panel(shock=shock),
            horizon=1,
            n_particles=macro_particles,
            k_rep=8,
            m_cand=8,
            cma_population=8,
            cma_generations=cma_generations,
            enable_micro_satellite=True,
            firm_count=64,
            enable_structural_satellites=True,
            stress_artifact_dir=temporary,
            seed=20261102,
            article_title_vi=f"CLIO orchestra stress scenario: {name}",
            article_context={"scenario": name, "shock": shock},
        )

        macro_clock = time.perf_counter()
        macro_before = engine.run(macro_request)
        timings["macro_before_seconds"] = round(time.perf_counter() - macro_clock, 3)

        clio_request = CLIORequest(
            macro_artifact=macro_before,
            horizon=horizon,
            seed=20261102,
            exogenous=exogenous,
            backend="numpy",
            enable_macro_events=True,
        )
        clio_clock = time.perf_counter()
        clio_artifact = run_clio(clio_request)
        timings["clio_seconds"] = round(time.perf_counter() - clio_clock, 3)

        repeat_hashes: List[str] = []
        for _ in range(max(0, repeat - 1)):
            repeat_clock = time.perf_counter()
            repeated = run_clio(clio_request)
            repeat_hashes.append(repeated.artifact_sha256)
            timings["repeat_seconds"] = round(
                timings.get("repeat_seconds", 0.0)
                + time.perf_counter()
                - repeat_clock,
                3,
            )

        feedback = clio_artifact.macro_feedback
        macro_after: Optional[MacroStressArtifact] = None
        if run_feedback and feedback is not None and macro_before.status == "OK":
            rerun_request = apply_feedback_to_macro_request(macro_request, feedback)
            rerun_clock = time.perf_counter()
            macro_after = engine.run(rerun_request)
            timings["macro_after_seconds"] = round(
                time.perf_counter() - rerun_clock, 3
            )

    invariants = _clio_invariants(clio_artifact, horizon)
    invariants["deterministic_repeat"] = (
        repeat <= 1 or all(value == clio_artifact.artifact_sha256 for value in repeat_hashes)
    )
    if macro_after is not None and feedback is not None:
        anchored = (
            abs(
                float(
                    (macro_after.gatekeeper or {})
                    .get("entropy_pooling", {})
                    .get("target_gdp_yoy_pct", float("nan"))
                )
                - feedback.gdp_yoy_stressed_pct
            )
            <= 1e-6
        )
        invariants["macro_rerun_consumed_anchor"] = bool(anchored)
        before_gdp = float(
            ((macro_before.nowcast or {}).get("core_series") or {}).get("GDP_YOY", 0.0)
        )
        after_gdp = float(
            ((macro_after.nowcast or {}).get("core_series") or {}).get("GDP_YOY", 0.0)
        )
        # The gatekeeper ESS floor can neutralise an extreme QPM anchor, so the
        # bridge also applies a panel overlay.  Require a material GDP response.
        invariants["macro_feedback_material"] = abs(after_gdp - before_gdp) >= 1e-3
    else:
        invariants["macro_rerun_consumed_anchor"] = not run_feedback
        invariants["macro_feedback_material"] = not run_feedback

    return {
        "scenario": name,
        "shock": shock,
        "timings": timings,
        "invariants": invariants,
        "macro_before": _macro_summary(macro_before),
        "macro_after": _macro_summary(macro_after) if macro_after is not None else None,
        "feedback": feedback.model_dump(mode="json") if feedback is not None else None,
        "clio": clio_artifact.model_dump(mode="json"),
        "repeat_hashes": repeat_hashes,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", type=int, default=128, choices=range(1, 129))
    parser.add_argument("--macro-particles", type=int, default=64)
    parser.add_argument("--cma-generations", type=int, default=5)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--scenarios", default="baseline,adverse,severe")
    parser.add_argument("--no-rerun", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--output", default="/tmp/opencode/clio_cusp_orchestra_stress.json"
    )
    args = parser.parse_args()

    if args.quick:
        args.horizon = min(args.horizon, 32)
        args.macro_particles = min(args.macro_particles, 64)
        args.repeat = 1

    names = [name.strip() for name in args.scenarios.split(",") if name.strip()]
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenarios: {unknown}; choose from {sorted(SCENARIOS)}")

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "horizon": args.horizon,
            "macro_particles": args.macro_particles,
            "cma_generations": args.cma_generations,
            "repeat": args.repeat,
            "run_feedback": not args.no_rerun,
            "scenarios": names,
        },
        "scenarios": {},
    }

    for name in names:
        print(f"[stress] running {name} ...", flush=True)
        result = run_scenario(
            name,
            SCENARIOS[name],
            horizon=args.horizon,
            macro_particles=args.macro_particles,
            cma_generations=args.cma_generations,
            repeat=args.repeat,
            run_feedback=not args.no_rerun,
        )
        report["scenarios"][name] = result
        failed = [key for key, value in result["invariants"].items() if not value]
        print(
            f"[stress] {name}: {'PASS' if not failed else 'FAIL ' + ','.join(failed)} "
            f"in {result['timings'].get('clio_seconds', 0):.2f}s",
            flush=True,
        )

    all_ok = all(
        all(result["invariants"].values())
        for result in report["scenarios"].values()
    )
    report["overall_pass"] = all_ok
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    header = (
        f"{'scenario':<10} {'macroGDP':>9} {'macroStress':>11} {'clioPcoll':>10} "
        f"{'clioCoup':>9} {'stability':>9} {'stressIdx':>9} {'feedback':>9} "
        f"{'rerunGDP':>9} {'macroDelta':>10} {'seconds':>8}"
    )
    print()
    print(header)
    print("-" * len(header))
    for name, result in report["scenarios"].items():
        before = result["macro_before"]
        after = result["macro_after"] or {}
        clio = result["clio"]
        summary = clio.get("summary", {})
        terminal = clio.get("terminal", {})
        feedback = result["feedback"] or {}
        gdp_before = (before.get("nowcast_core") or {}).get("GDP_YOY")
        gdp_after = (after.get("nowcast_core") or {}).get("GDP_YOY")
        delta = (
            _fmt(float(gdp_after) - float(gdp_before), 3)
            if gdp_before is not None and gdp_after is not None
            else "-"
        )
        seconds = sum(
            float(value)
            for value in result["timings"].values()
            if isinstance(value, (int, float))
        )
        print(
            f"{name:<10} {_fmt(gdp_before, 3):>9} "
            f"{_fmt(before.get('regime_stress_probability'), 3):>11} "
            f"{_fmt(summary.get('terminal_p_collapse'), 3):>10} "
            f"{_fmt(terminal.get('p_regime_change'), 3):>9} "
            f"{_fmt(terminal.get('stability'), 3):>9} "
            f"{_fmt(summary.get('terminal_stress_index'), 3):>9} "
            f"{_fmt(feedback.get('gdp_yoy_adjustment_pct'), 3):>9} "
            f"{_fmt(feedback.get('gdp_yoy_stressed_pct'), 3):>9} "
            f"{delta:>10} {seconds:>8.2f}"
        )

    print()
    print(f"[stress] report: {output}")
    print(f"[stress] overall: {'PASS' if all_ok else 'FAIL'}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
