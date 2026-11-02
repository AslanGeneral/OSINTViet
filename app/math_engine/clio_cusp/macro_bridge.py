"""Two-way bridge between SPEC-MACRO-QUANT v5.7 and CLIO-CUSP 8192 v4.0.

Channel 1 (macro -> CLIO) maps the signed macro artifact into the structural,
power, military, event, weather and psychology drivers of Xi_t.  Channel 2
(CLIO -> macro) produces a conservative GDP/NPL/FX feedback object.  The
feedback is written to ``qpm_anchor_gdp_yoy_pct`` and, because the v5.7
gatekeeper's Kish ESS floor can neutralise an extreme anchor, it is also
overlaid on the core macro panel series before the rerun.  Neither engine's
internals are modified.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.math_engine.clio_cusp.contracts import (
    CLIOArtifact,
    CLIOExogenous,
    CLIORequest,
    EventInput,
    MacroFeedback,
    sha256_value,
)
from app.math_engine.clio_cusp.kernel import SIGMA_C_GRAN
from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
)


def _nested(mapping: Any, path: str, default: Any = None) -> Any:
    value: Any = mapping
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part, default)
        else:
            value = getattr(value, part, default)
        if value is None:
            return default
    return value


def _branch_metrics(artifact: MacroStressArtifact, name: str) -> Dict[str, Any]:
    branch = artifact.branches.get(name)
    if branch is None:
        return {}
    return branch.model_dump(mode="json")


def _clip(value: float, low: float, high: float) -> float:
    return float(np.clip(float(value), low, high))


def synthesize_macro_events(artifact: MacroStressArtifact) -> List[EventInput]:
    """Translate the v5.7 adversarial scenario into a PLOVER event record.

    The macro artifact does not emit news text, so this is explicitly a
    scenario-to-event projection, not a claim about a real article.  It gives
    Subsystem 4 a deterministic, auditable pulse on day one.
    """
    active = _branch_metrics(artifact, "active")
    tail_var = float(active.get("gdp_tail_loss_var_pct", 0.0) or 0.0)
    regime = (artifact.nowcast or {}).get("regime") or {}
    stress = _clip(float(regime.get("stress_probability_last", 0.0) or 0.0), 0.0, 1.0)
    adversarial = artifact.adversarial_shock or {}
    objective = abs(float(adversarial.get("best_objective", 0.0) or 0.0))

    goldstein = -_clip(1.8 + 0.35 * tail_var + 3.5 * stress + 0.15 * objective, 0.0, 10.0)
    headcount = float(20_000.0 + 250_000.0 * stress + 2_000.0 * max(0.0, tail_var))
    damage = float(max(0.5, 0.5 * tail_var + 8.0 * stress))
    shares = float(100.0 + 12_000.0 * stress + 50.0 * max(0.0, tail_var))
    parents = [-1] + [(index - 1) // 2 for index in range(1, 127)]
    return [
        EventInput(
            event_id=f"macro_{artifact.request_id or 'stress'}",
            day=1,
            time=artifact.started_at,
            source="macro_stress_v57",
            target="domestic_polity",
            event_type="PROTEST",
            mode="riot_violent",
            context="regime_change",
            headcount=headcount,
            damage=damage,
            new_shares_per_day=shares,
            goldstein_expectation=goldstein,
            parent_indices=parents,
            negative_event_score=_clip(0.45 + 0.55 * stress, 0.0, 1.0),
            is_agitated=True,
        )
    ]


def map_macro_to_drivers(
    artifact: MacroStressArtifact,
    exogenous: CLIOExogenous,
    *,
    enable_macro_events: bool = True,
) -> Tuple[CLIOExogenous, Dict[str, Any], List[str]]:
    """Map a v5.7 artifact into a deep-copied CLIO driver set."""
    if artifact.status != "OK":
        raise ValueError(
            f"macro artifact status is {artifact.status!r}; CLIO requires an OK run"
        )
    exo = exogenous.model_copy(deep=True)
    warnings: List[str] = []

    core = (artifact.nowcast or {}).get("core_series") or {}
    active = _branch_metrics(artifact, "active")
    passive = _branch_metrics(artifact, "passive")
    regime = (artifact.nowcast or {}).get("regime") or {}
    stress = _clip(float(regime.get("stress_probability_last", 0.0) or 0.0), 0.0, 1.0)
    acceptance = _clip(
        float((artifact.gatekeeper or {}).get("acceptance_rate", 0.0) or 0.0),
        0.0,
        1.0,
    )

    gdp_yoy = float(
        core.get("GDP_YOY")
        if core.get("GDP_YOY") is not None
        else active.get("gdp_yoy_mean_pct", passive.get("gdp_yoy_mean_pct", 5.0))
    )
    cpi_yoy = float(core.get("CPI_YOY", 3.5) or 3.5)
    fx_deprec = float(core.get("FX_DEPREC", 2.0) or 2.0)
    ib_rate = float(core.get("IB_RATE", 5.0) or 5.0)
    npl_ratio = float(core.get("NPL_RATIO", 2.5) or 2.5)
    credit_growth = float(core.get("CREDIT_GROWTH", 10.0) or 10.0)
    fiscal_balance = float(core.get("FISCAL_BALANCE", -3.0) or -3.0)
    current_account = float(core.get("CURRENT_ACCOUNT", 1.0) or 1.0)
    fx_reserves = float(core.get("FX_RESERVES", 80.0) or 80.0)

    debt_pct = _nested(
        artifact.structural_satellites, "bohn_sovereign_debt.debt_to_gdp_pct.mean", None
    )
    car_pct = _nested(
        artifact.structural_satellites, "adrian_shin_bank_car.car_pct.mean", None
    )
    yield10 = _nested(
        artifact.structural_satellites, "nelson_siegel.yields_pct.10Y.mean", None
    )
    micro_mean = _nested(artifact.micro_downscaling, "expected_stress_next_q.mean", None)
    micro_tail = _nested(artifact.micro_downscaling, "var99_stress_next_q.mean", None)
    micro_share = _nested(
        artifact.micro_downscaling, "share_expected_stress_above_080", None
    )
    tail_var = float(active.get("gdp_tail_loss_var_pct", 0.0) or 0.0)
    tail_es = float(active.get("gdp_tail_loss_es_pct", 0.0) or 0.0)
    adversarial = artifact.adversarial_shock or {}
    best_physical = adversarial.get("best_physical") or []
    shock_intensity = float(
        np.mean([abs(float(value)) for value in best_physical])
        if best_physical
        else abs(float(adversarial.get("best_objective", 0.0) or 0.0))
    )

    # ------------------------------------------------------------------
    # Subsystem 1: growth, real wages, debt and relative deprivation
    # ------------------------------------------------------------------
    s = exo.structural
    s.gdp_pc_growth_daily = (1.0 + gdp_yoy / 100.0) ** (1.0 / 365.0) - 1.0
    s.wage_growth_daily = (
        1.0 + _clip((gdp_yoy - cpi_yoy) / 100.0, -0.20, 0.20)
    ) ** (1.0 / 365.0) - 1.0
    s.inflation_daily = (1.0 + cpi_yoy / 100.0) ** (1.0 / 365.0) - 1.0
    if debt_pct is not None and float(debt_pct) > 0:
        s.public_debt_gdp = float(debt_pct) / 100.0
    effective_rate = max(0.0, float(yield10 if yield10 is not None else ib_rate))
    s.interest_plus_deficit_tax = _clip(
        0.02
        + (effective_rate / 100.0) * s.public_debt_gdp
        + max(0.0, -fiscal_balance) / 100.0
        + 0.02 * stress,
        0.0,
        1.0,
    )
    s.reform = _clip(s.reform * (1.0 - 0.5 * stress) + 0.05 * (1.0 - stress), 0.0, 1.0)
    rdi_proxy = _clip(
        0.03
        + 0.35 * stress
        + 0.010 * max(0.0, cpi_yoy)
        + 0.020 * max(0.0, npl_ratio - 3.0)
        + 0.015 * max(0.0, fx_deprec)
        + 0.10 * max(0.0, -gdp_yoy) / 10.0,
        0.0,
        0.85,
    )
    exo.rdi_override = rdi_proxy
    s.capability_value = max(1e-9, s.expected_value * (1.0 - rdi_proxy))

    # ------------------------------------------------------------------
    # Subsystem 2: rents and institutional sclerosis
    # ------------------------------------------------------------------
    p = exo.power
    p.rent_gdp = _clip(
        p.rent_gdp + 0.010 * stress + 0.0005 * max(0.0, fx_deprec), 0.0, 0.50
    )
    p.stability_duration = max(0.0, p.stability_duration * (1.0 - 0.10 * stress))

    # ------------------------------------------------------------------
    # Subsystem 3: defense burden and coup-proofing
    # ------------------------------------------------------------------
    m = exo.military
    m.military_expenditure = max(0.0, m.military_expenditure * (1.0 + 0.10 * stress))
    exo.foreign_pressure = max(
        exo.foreign_pressure,
        _clip(
            0.05
            + 0.60 * stress
            + 0.010 * max(0.0, fx_deprec)
            + 0.03 * min(1.0, shock_intensity),
            0.0,
            2.0,
        ),
    )

    # ------------------------------------------------------------------
    # Subsystem 4: protest size, coercion and harbinger rails
    # ------------------------------------------------------------------
    e = exo.events
    e.harbingers = [
        max(float(e.harbingers[0]), stress),
        max(float(e.harbingers[1]), _clip(max(0.0, -gdp_yoy) / 5.0, 0.0, 1.0)),
        max(float(e.harbingers[2]), stress),
        max(float(e.harbingers[3]), _clip(tail_var / 10.0 + stress, 0.0, 1.0)),
    ]
    e.coercive_intensity = _clip(
        max(e.coercive_intensity, 0.15 + 0.60 * stress + npl_ratio / 50.0), 0.0, 1.0
    )
    e.eta_select = _clip(min(e.eta_select, 0.85 - 0.45 * stress), 0.05, 0.95)
    e.active_protesters = max(e.active_protesters, 5_000.0 * (1.0 + 4.0 * stress))
    e.police_count = max(e.police_count, e.active_protesters * (0.6 + 0.8 * stress))
    if e.spi_override is None:
        # The micro output is a DVI-scale stress level, not a probability.
        # Divide by ten before using it as a pressure contribution, and keep
        # the macro structural channel (rdi_proxy) explicit.
        micro_pressure = _clip(float(micro_mean or 0.0) / 10.0, 0.0, 1.0)
        e.spi_override = _clip(
            0.08
            + 0.30 * stress
            + 0.30 * rdi_proxy
            + 0.015 * max(0.0, tail_var)
            + 0.10 * micro_pressure,
            0.0,
            1.0,
        )

    # ------------------------------------------------------------------
    # Subsystem 5: proximity to the Granovetter fold
    # ------------------------------------------------------------------
    ps = exo.psychology
    ps.sigma_theta = _clip(
        SIGMA_C_GRAN
        * (0.55 + 0.40 * stress + 0.010 * max(0.0, cpi_yoy) + 0.010 * max(0.0, npl_ratio - 3.0)),
        0.02,
        SIGMA_C_GRAN * 0.99,
    )
    ps.gamma_econ = _clip(ps.gamma_econ + 0.10 * stress, 0.0, 1.0)
    ps.latent_initial = max(ps.latent_initial, 0.50 * stress)

    macro_events: List[EventInput] = []
    if enable_macro_events:
        macro_events = synthesize_macro_events(artifact)
        existing = {event.event_id for event in e.events}
        e.events = [
            event for event in e.events if not event.event_id.startswith("macro_")
        ] + [event for event in macro_events if event.event_id not in existing]

    mapping = {
        "raw": {
            "gdp_yoy_pct": round(gdp_yoy, 6),
            "cpi_yoy_pct": round(cpi_yoy, 6),
            "fx_deprec_pct": round(fx_deprec, 6),
            "interbank_rate_pct": round(ib_rate, 6),
            "npl_ratio_pct": round(npl_ratio, 6),
            "credit_growth_pct": round(credit_growth, 6),
            "fiscal_balance_pct_gdp": round(fiscal_balance, 6),
            "current_account_pct_gdp": round(current_account, 6),
            "fx_reserves_billion_usd": round(fx_reserves, 6),
            "regime_stress_probability": round(stress, 6),
            "gatekeeper_acceptance_rate": round(acceptance, 6),
            "sovereign_debt_pct_gdp": round(float(debt_pct), 6) if debt_pct is not None else None,
            "bank_car_pct": round(float(car_pct), 6) if car_pct is not None else None,
            "yield_10y_pct": round(float(yield10), 6) if yield10 is not None else None,
            "micro_expected_stress": round(float(micro_mean), 6) if micro_mean is not None else None,
            "micro_var99_stress": round(float(micro_tail), 6) if micro_tail is not None else None,
            "micro_share_above_080": round(float(micro_share), 6) if micro_share is not None else None,
            "gdp_tail_loss_var_pct": round(tail_var, 6),
            "gdp_tail_loss_es_pct": round(tail_es, 6),
            "adversarial_shock_intensity": round(shock_intensity, 6),
        },
        "derived": {
            "rdi_override": round(rdi_proxy, 6),
            "foreign_pressure": round(exo.foreign_pressure, 6),
            "spi_override": round(float(e.spi_override), 6),
            "harbingers": [round(float(v), 6) for v in e.harbingers],
            "coercive_intensity": round(float(e.coercive_intensity), 6),
            "eta_select": round(float(e.eta_select), 6),
            "sigma_theta": round(float(ps.sigma_theta), 6),
            "gdp_pc_growth_daily": round(float(s.gdp_pc_growth_daily), 10),
            "wage_growth_daily": round(float(s.wage_growth_daily), 10),
            "public_debt_gdp": round(float(s.public_debt_gdp), 6),
            "interest_plus_deficit_tax": round(float(s.interest_plus_deficit_tax), 6),
            "macro_event_ids": [event.event_id for event in macro_events],
        },
        "policy": {
            "engine": "macro_stress_v57",
            "macro_request_id": artifact.request_id,
            "macro_artifact_sha256": artifact.artifact_sha256,
            "macro_engine_version": artifact.engine_version,
            "mapping": "artifact fields -> CLIO drivers; no macro internals modified",
        },
    }
    if not active:
        warnings.append("macro artifact has no active branch; branch metrics default to nowcast/zero")
    if artifact.structural_satellites.get("enabled") is False:
        warnings.append("structural satellites disabled; debt/CAR/yield mappings use driver defaults")
    if micro_mean is None:
        warnings.append("micro downscaling disabled or missing; micro stress mapped to zero")
    return exo, mapping, warnings


def apply_macro_artifact(
    request: CLIORequest,
) -> Tuple[CLIORequest, Dict[str, Any], List[str]]:
    """Return a copy of ``request`` with macro-derived drivers."""
    if request.macro_artifact is None:
        return request, {}, []
    exo, mapping, warnings = map_macro_to_drivers(
        request.macro_artifact,
        request.exogenous,
        enable_macro_events=request.enable_macro_events,
    )
    updated = request.model_copy(update={"exogenous": exo})
    return updated, mapping, warnings


def clio_to_macro_feedback(
    artifact: CLIOArtifact,
    macro_artifact: Optional[MacroStressArtifact],
) -> Optional[MacroFeedback]:
    """Build the CLIO -> macro channel from terminal and last-week metrics."""
    if macro_artifact is None:
        return None
    terminal = artifact.terminal or {}
    summary = artifact.summary or {}
    p_regime = _clip(
        float(terminal.get("p_regime_change", summary.get("terminal_p_regime_change", 0.0))),
        0.0,
        1.0,
    )
    p_collapse = _clip(
        float(terminal.get("p_collapse", summary.get("terminal_p_collapse", 0.0))),
        0.0,
        1.0,
    )
    stability = _clip(
        float(terminal.get("stability", summary.get("terminal_stability", 1.0))),
        0.0,
        1.0,
    )
    stress_index = _clip(
        float(summary.get("terminal_stress_index", summary.get("mean_stress_index", 0.0))),
        0.0,
        1.0,
    )
    gdp_adjustment = _clip(
        float(summary.get("gdp_feedback_pct", 0.0)),
        -15.0,
        0.0,
    )

    core = (macro_artifact.nowcast or {}).get("core_series") or {}
    base_gdp = float(core.get("GDP_YOY", 5.0) or 5.0)
    if base_gdp == 0.0:
        base_gdp = float(_branch_metrics(macro_artifact, "active").get("gdp_yoy_mean_pct", 5.0))

    npl_uplift = _clip(12.0 * p_collapse + 6.0 * (1.0 - stability), 0.0, 20.0)
    fx_uplift = _clip(15.0 * p_regime + 8.0 * (1.0 - stability), 0.0, 30.0)
    investment_haircut = _clip(100.0 * (1.0 - math.exp(-3.0 * stress_index)), 0.0, 100.0)

    return MacroFeedback(
        source_request_id=artifact.request_id,
        source_macro_request_id=macro_artifact.request_id,
        stress_index=round(stress_index, 6),
        gdp_yoy_adjustment_pct=round(gdp_adjustment, 6),
        gdp_yoy_stressed_pct=round(base_gdp + gdp_adjustment, 6),
        npl_uplift_pct=round(npl_uplift, 6),
        fx_pressure_uplift_pct=round(fx_uplift, 6),
        investment_haircut_pct=round(investment_haircut, 6),
        regime_change_probability=round(p_regime, 6),
        collapse_probability=round(p_collapse, 6),
        terminal_stability=round(stability, 6),
        rationale=(
            "CLIO terminal probability of regime change, cusp collapse probability, "
            "terminal stability and the last-seven-day GDP feedback are mapped into "
            "a conservative GDP/NPL/FX adjustment. The adjustment is carried by the "
            "macro engine's existing QPM anchor and by a transparent scenario overlay "
            "on the core macro panel; no macro-internal equations are changed."
        ),
    )


_FEEDBACK_PANEL_SHIFTS: Dict[str, Any] = {
    "GDP_YOY": lambda feedback: float(feedback.gdp_yoy_adjustment_pct),
    "NPL_RATIO": lambda feedback: float(feedback.npl_uplift_pct),
    "FX_DEPREC": lambda feedback: float(feedback.fx_pressure_uplift_pct),
    # Imported inflation and a credit-supply haircut are the two conservative
    # secondary channels; both are derived from the same feedback object.
    "CPI_YOY": lambda feedback: 0.25 * float(feedback.fx_pressure_uplift_pct),
    "CREDIT_GROWTH": lambda feedback: -0.02 * float(feedback.investment_haircut_pct),
}


def _feedback_series_position(series_codes: List[str], code: str) -> Optional[int]:
    """Resolve a canonical core code against canonical or World Bank aliases."""
    from app.math_engine.macro_stress_v57.calibration import CORE_ALIASES

    aliases = set(CORE_ALIASES.get(code, (code,)))
    for position, series_code in enumerate(series_codes):
        if series_code in aliases:
            return position
    return None


def apply_feedback_to_macro_request(
    request: MacroStressRequest,
    feedback: MacroFeedback,
) -> MacroStressRequest:
    """Return a v5.7 request that consumes the CLIO feedback.

    The QPM anchor is kept as the named scalar channel, but the v5.7
    gatekeeper's Kish ESS floor can neutralise an extreme anchor.  To make the
    feedback materially effective without touching macro internals, the same
    feedback is also applied as a transparent scenario overlay on the core
    panel series: GDP growth, NPL, FX depreciation, imported CPI and credit
    growth.  The overlay is recorded in ``article_context`` for audit.
    """
    panel = request.panel.model_copy(deep=True)
    applied: Dict[str, float] = {}
    for code, shift_fn in _FEEDBACK_PANEL_SHIFTS.items():
        position = _feedback_series_position(panel.series_codes, code)
        if position is None:
            continue
        shift = float(shift_fn(feedback))
        if not math.isfinite(shift):
            continue
        applied[code] = round(shift, 6)
        for row in panel.values:
            value = row[position]
            if value is not None:
                row[position] = float(value) + shift

    context = dict(request.article_context or {})
    context["clio_feedback"] = feedback.model_dump(mode="json")
    context["macro_panel_overlay"] = {
        "reason": "CLIO feedback applied to macro core series; QPM anchor retained",
        "shifts": applied,
    }
    return request.model_copy(
        update={
            "request_id": f"{request.request_id}_clio_feedback",
            "panel": panel,
            "qpm_anchor_gdp_yoy_pct": feedback.gdp_yoy_stressed_pct,
            "article_context": context,
        }
    )


def run_orchestra(
    macro_request: MacroStressRequest,
    clio_request: Optional[CLIORequest] = None,
    *,
    run_feedback: bool = True,
) -> Dict[str, Any]:
    """Run macro -> CLIO -> macro in one auditable call."""
    from app.math_engine.clio_cusp.orchestrator import run_clio
    from app.math_engine.macro_stress_v57.engine import MacroStressEngineV57

    engine = MacroStressEngineV57()
    macro_clock = time.perf_counter()
    macro_artifact = engine.run(macro_request)
    macro_seconds = time.perf_counter() - macro_clock

    if clio_request is None:
        clio_request = CLIORequest(
            macro_artifact=macro_artifact,
            horizon=128,
            seed=20261102,
        )
    else:
        clio_request = clio_request.model_copy(update={"macro_artifact": macro_artifact})

    clio_clock = time.perf_counter()
    clio_artifact = run_clio(clio_request)
    clio_seconds = time.perf_counter() - clio_clock

    feedback = clio_to_macro_feedback(clio_artifact, macro_artifact)
    rerun = None
    rerun_seconds = 0.0
    if run_feedback and feedback is not None:
        rerun_clock = time.perf_counter()
        rerun = engine.run(apply_feedback_to_macro_request(macro_request, feedback))
        rerun_seconds = time.perf_counter() - rerun_clock

    return {
        "macro": macro_artifact,
        "clio": clio_artifact,
        "feedback": feedback,
        "macro_rerun": rerun,
        "timings": {
            "macro_seconds": round(macro_seconds, 3),
            "clio_seconds": round(clio_seconds, 3),
            "macro_rerun_seconds": round(rerun_seconds, 3),
        },
        "request_hash": sha256_value(macro_request.model_dump(mode="json")),
    }


__all__ = [
    "apply_feedback_to_macro_request",
    "apply_macro_artifact",
    "clio_to_macro_feedback",
    "map_macro_to_drivers",
    "run_orchestra",
    "synthesize_macro_events",
]
