"""128-day CLIO-CUSP v4.0 orchestrator.

The loop follows the Xi_t dependency order fixed by Sections 3-8:

    power graph -> structural stress -> events/penalty -> military/coup
    -> micro-psychology -> cusp controls -> Array-mRQMC step

The orchestrator never mutates the caller's request; macro mapping happens on
a deep copy of the exogenous drivers.
"""

from __future__ import annotations

import time
import traceback
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.math_engine.clio_cusp.contracts import (
    CLIOArtifact,
    CLIODayRecord,
    CLIORequest,
    sha256_value,
    utc_now_iso,
)
from app.math_engine.clio_cusp.cusp import (
    control_particles,
    draw_sobol_nets_numpy,
    draw_sobol_nets_torch,
    numpy_cusp_step,
    resolve_backend,
    torch_cusp_step,
)
from app.math_engine.clio_cusp.kernel import (
    H_HORIZON,
    M_PARTICLES,
    R_REPLICATES,
    compute_cusp_parameters,
    diffusion_volatility,
    vulnerability_amplifier,
)
from app.math_engine.clio_cusp.macro_bridge import apply_macro_artifact
from app.math_engine.clio_cusp.state import (
    CLIOState,
    advance_slow_drivers,
    update_protester_pool,
)
from app.math_engine.clio_cusp.subsystems import (
    esim_conversion,
    event_layer,
    granovetter_constants,
    military_layer,
    power_layer,
    psychology_layer,
    structural_layer,
)


def _step_seed(seed: int, day: int) -> int:
    return int((int(seed) * 1_000 + int(day)) % (2**31 - 1))


def _day_date(start: str, day: int) -> str:
    if not start:
        return f"T+{day:03d}"
    return (date.fromisoformat(start) + timedelta(days=day - 1)).isoformat()


def _stress_index(
    p_collapse: float,
    p_regime_change: float,
    stability: float,
    psi: float,
) -> float:
    return float(
        np.clip(
            0.40 * p_collapse
            + 0.25 * p_regime_change
            + 0.20 * (1.0 - stability)
            + 0.15 * min(1.0, max(0.0, psi) / 2.0),
            0.0,
            1.0,
        )
    )


def _gdp_feedback_pct(
    p_collapse: float,
    p_regime_change: float,
    stability: float,
    psi: float,
) -> float:
    value = -(
        8.0 * p_regime_change
        + 4.0 * p_collapse
        + 1.5 * (1.0 - stability)
        + 2.0 * max(0.0, psi - 1.0)
    )
    return float(np.clip(value, -20.0, 0.0))


def _harbingers_with_event_rail(
    request: CLIORequest,
    records: List[Dict[str, Any]],
) -> List[float]:
    harbingers = [float(value) for value in request.exogenous.events.harbingers]
    # O_{4,t}: capital protest-military nexus.  We can only observe the
    # protest-size half without a capital-city flag, so the refusal signal is
    # proxied by (1 - eta_select) and the result is capped at one.
    size = sum(
        float(record["size_weight"])
        for record in records
        if record["agitated"] or str(record["context"]).lower() in {"regime_change", "leadership_ouster"}
    )
    observed = float(np.clip(size / 10.0, 0.0, 1.0)) * (
        1.0 - request.exogenous.events.eta_select
    )
    harbingers[3] = max(harbingers[3], observed)
    return harbingers


def run_clio(request: CLIORequest) -> CLIOArtifact:
    """Run one deterministic 128-day state-space integration."""
    started_at = utc_now_iso()
    clock = time.perf_counter()
    warnings: List[str] = []
    mapping: Dict[str, Any] = {}
    driver_source = "standalone"
    input_sha256 = ""
    mapped_sha256 = ""

    try:
        # Hash the caller's input before macro mapping so the artifact remains
        # traceable to the request the caller actually submitted.
        input_sha256 = sha256_value(request.model_dump(mode="json"))
        # The orchestrator advances mutable driver levels; never mutate the
        # caller's contract, and make repeated runs bitwise reproducible.
        request = request.model_copy(deep=True)
        if request.macro_artifact is not None:
            request, mapping, bridge_warnings = apply_macro_artifact(request)
            warnings.extend(bridge_warnings)
            driver_source = "macro_stress_v57"
            mapped_sha256 = sha256_value(request.model_dump(mode="json"))
        params = request.exogenous
        backend = resolve_backend(request.backend)
        state = CLIOState.initialize(params)
        z_star, sigma_c, r_star = granovetter_constants(params.psychology.mu_theta)
        days: List[CLIODayRecord] = []

        for day in range(1, request.horizon + 1):
            power = power_layer(params, state.psi)
            structural = structural_layer(
                params,
                sclerosis=float(power["sclerosis"]),
                previous_psi=state.psi,
                rdi_override=params.rdi_override,
            )
            event_result = event_layer(
                events=params.events.events,
                day=day,
                params=params,
                sclerosis=float(power["sclerosis"]),
                omega_total=float(power["omega_total"]),
                previous_penalty=state.penalty,
                previous_bias=state.bias,
                penalty_cpt=2.25,
            )
            records = event_result["events"]

            esim_normalized, esim_raw = esim_conversion(params)
            agitation_weights = [
                float(record["size_weight"])
                for record in records
                if record["agitated"]
            ]
            harbingers = _harbingers_with_event_rail(request, records)

            current_f = float(
                np.reciprocal(
                    np.sum(np.square(np.asarray(params.military.military_shares, dtype=float)))
                )
            )
            delta_f = state.delta_f_mil_365(current_f)
            military = military_layer(
                params=params,
                emp=float(structural["emp"]),
                penalty=float(event_result["penalty"]),
                delta_f_mil_365=delta_f,
                harbingers=harbingers,
                coeffs=request.coup,
            )
            state.push_military(float(military["f_mil"]))

            psychology = psychology_layer(
                params=params,
                penalty=float(event_result["penalty"]),
                rdi=float(structural["rdi"]),
                delta_penalty=float(event_result["event_penalty_total"]),
                agitation_events=agitation_weights,
                previous_hawkes=state.hawkes,
                previous_latent=state.latent,
                z_star=z_star,
                sigma_c=sigma_c,
                r_star=r_star,
            )
            k_exo = min(20.0, vulnerability_amplifier(float(event_result["penalty"])))
            a_base, b_base = compute_cusp_parameters(
                psi=float(structural["psi"]),
                spi=float(event_result["spi"]),
                omega_total=float(power["omega_total"]),
                regime_change_probability=float(military["p_regime_change"]),
                foreign_pressure=float(params.foreign_pressure),
                penalty=float(event_result["penalty"]),
                latent_discontent=float(psychology["latent_discontent"]),
                sclerosis=float(power["sclerosis"]),
                esim_conversion=esim_normalized,
                asi=float(military["asi_outcome"]),
                weights_a=request.weights.weights_a(),
                weights_b=request.weights.weights_b(),
                a0=request.weights.a0,
            )
            sigma_base = diffusion_volatility(
                sigma0=params.psychology.sigma0,
                penalty=float(event_result["penalty"]),
                aggression=float(psychology["aggression"]),
                turnout=float(psychology["turnout"]),
                sigma_theta=params.psychology.sigma_theta,
                sigma_c=sigma_c,
            )
            a_t, b_t = control_particles(
                a_base=float(a_base),
                b_base=float(b_base),
                sigma_a=request.weights.sigma_a,
                sigma_b=request.weights.sigma_b,
                seed=request.seed,
                day=day,
            )
            Sigma_t = np.full((R_REPLICATES, M_PARTICLES), float(sigma_base))
            seed_day = _step_seed(request.seed, day)

            if backend == "torch":
                import torch

                v_t, u_t = draw_sobol_nets_torch(seed_day)
                result = torch_cusp_step(
                    torch.as_tensor(state.X, dtype=torch.float64),
                    torch.as_tensor(a_t, dtype=torch.float64),
                    torch.as_tensor(b_t, dtype=torch.float64),
                    torch.as_tensor(Sigma_t, dtype=torch.float64),
                    v_t,
                    u_t,
                    sigma_a=request.weights.sigma_a,
                    sigma_b=request.weights.sigma_b,
                )
                X_next = result["X_next"].detach().cpu().numpy()
                S_next = result["S_next"].detach().cpu().numpy()
                result["X_mean"] = float(np.median(X_next.mean(axis=-1)))
                result["X_q05"] = float(np.quantile(X_next, 0.05))
                result["X_q50"] = float(np.quantile(X_next, 0.50))
                result["X_q95"] = float(np.quantile(X_next, 0.95))
            else:
                v_np, u_np = draw_sobol_nets_numpy(seed_day)
                result = numpy_cusp_step(
                    state.X,
                    a_t,
                    b_t,
                    Sigma_t,
                    v_np,
                    u_np,
                    sigma_a=request.weights.sigma_a,
                    sigma_b=request.weights.sigma_b,
                )
                X_next = result["X_next"]
                S_next = result["S_next"]

            if not np.all(np.isfinite(X_next)):
                raise FloatingPointError("cusp particle state produced a non-finite value")

            stability = float(result["S_next_mRQMC"])
            p_collapse = float(result["P_collapse_mRQMC"])
            p_hysteresis = float(result["P_hysteresis_mRQMC"])
            stress_index = _stress_index(
                p_collapse,
                float(military["p_regime_change"]),
                stability,
                float(structural["psi"]),
            )
            gdp_feedback = _gdp_feedback_pct(
                p_collapse,
                float(military["p_regime_change"]),
                stability,
                float(structural["psi"]),
            )

            days.append(
                CLIODayRecord(
                    day=day,
                    date=_day_date(request.horizon_start_date, day),
                    mmp_aug=round(float(structural["mmp_aug"]), 8),
                    auto_ineq=round(float(structural["auto_ineq"]), 8),
                    emp=round(float(structural["emp"]), 8),
                    sfd=round(float(structural["sfd"]), 8),
                    rdi=round(float(structural["rdi"]), 8),
                    psi=round(float(structural["psi"]), 8),
                    hhi_pol=round(float(power["HHI_pol"]), 8),
                    pci=round(float(power["PCI"]), 8),
                    enp=round(float(power["ENP"]), 8),
                    sclerosis=round(float(power["sclerosis"]), 8),
                    omega_total=round(float(power["omega_total"]), 8),
                    omega_groups=[round(float(v), 8) for v in power["omega_groups"]],
                    delta_p_ig=round(float(power["omega_total"]) * 1e-2, 10),
                    smr=round(float(military["smr"]), 8),
                    f_mil=round(float(military["f_mil"]), 8),
                    delta_f_mil_365=round(float(delta_f), 8),
                    asi_outcome=round(float(military["asi_outcome"]), 8),
                    p_attempt=round(float(military["p_attempt"]), 8),
                    p_success=round(float(military["p_success"]), 8),
                    p_regime_change=round(float(military["p_regime_change"]), 8),
                    p_bloody_failed=round(float(military["p_bloody_failed"]), 8),
                    penalty=round(float(event_result["penalty"]), 8),
                    bias=round(float(event_result["bias"]), 8),
                    k_exo=round(float(k_exo), 8),
                    spi=round(float(event_result["spi"]), 8),
                    esim_conversion=round(float(esim_normalized), 8),
                    esim_agents=round(float(esim_raw), 4),
                    event_count=int(event_result["event_count"]),
                    event_penalty_total=round(float(event_result["event_penalty_total"]), 10),
                    events=records,
                    aggression=round(float(psychology["aggression"]), 8),
                    turnout=round(float(psychology["turnout"]), 8),
                    hawkes=round(float(psychology["hawkes"]), 8),
                    latent_discontent=round(float(psychology["latent_discontent"]), 8),
                    epstein_activation_rate=round(
                        float(psychology["epstein_activation_rate"]), 8
                    ),
                    granovetter_z_star=round(z_star, 8),
                    granovetter_sigma_c=round(sigma_c, 8),
                    granovetter_r_star=round(r_star, 8),
                    a_t=round(float(a_base), 8),
                    b_t=round(float(b_base), 8),
                    sigma_t=round(float(sigma_base), 8),
                    x_mean=round(float(result["X_mean"]), 8),
                    x_q05=round(float(result["X_q05"]), 8),
                    x_q50=round(float(result["X_q50"]), 8),
                    x_q95=round(float(result["X_q95"]), 8),
                    stability=round(stability, 8),
                    stability_median=round(float(np.median(S_next)), 8),
                    p_collapse=round(p_collapse, 8),
                    p_hysteresis=round(p_hysteresis, 8),
                    stress_index=round(stress_index, 8),
                    gdp_feedback_pct=round(gdp_feedback, 8),
                )
            )

            state.X = X_next
            state.psi = float(structural["psi"])
            state.penalty = float(event_result["penalty"])
            state.bias = float(event_result["bias"])
            state.hawkes = float(psychology["hawkes"])
            state.latent = float(psychology["latent_discontent"])
            state.day = day

            update_protester_pool(
                params,
                params.events.events,
                float(psychology["turnout"]),
                day=day,
            )
            advance_slow_drivers(params)

        terminal = days[-1].model_dump(mode="json") if days else {}
        terminal = {
            key: terminal.get(key)
            for key in (
                "day",
                "psi",
                "penalty",
                "bias",
                "hawkes",
                "latent_discontent",
                "stability",
                "p_collapse",
                "p_hysteresis",
                "p_regime_change",
                "p_attempt",
                "x_mean",
                "x_q05",
                "x_q50",
                "x_q95",
                "stress_index",
            )
        }
        summary = {
            "horizon": request.horizon,
            "days_recorded": len(days),
            "terminal_psi": float(np.mean([d.psi for d in days[-7:]])),
            "mean_psi": float(np.mean([d.psi for d in days])),
            "max_psi": float(np.max([d.psi for d in days])),
            "terminal_penalty": float(np.mean([d.penalty for d in days[-7:]])),
            "mean_penalty": float(np.mean([d.penalty for d in days])),
            "terminal_stability": float(np.mean([d.stability for d in days[-7:]])),
            "mean_stability": float(np.mean([d.stability for d in days])),
            "terminal_p_collapse": float(np.mean([d.p_collapse for d in days[-7:]])),
            "max_p_collapse": float(np.max([d.p_collapse for d in days])),
            "mean_p_collapse": float(np.mean([d.p_collapse for d in days])),
            "terminal_p_regime_change": float(
                np.mean([d.p_regime_change for d in days[-7:]])
            ),
            "max_p_regime_change": float(np.max([d.p_regime_change for d in days])),
            "terminal_p_hysteresis": float(
                np.mean([d.p_hysteresis for d in days[-7:]])
            ),
            "max_p_hysteresis": float(np.max([d.p_hysteresis for d in days])),
            "terminal_latent_discontent": float(
                np.mean([d.latent_discontent for d in days[-7:]])
            ),
            "mean_latent_discontent": float(
                np.mean([d.latent_discontent for d in days])
            ),
            "terminal_stress_index": float(
                np.mean([d.stress_index for d in days[-7:]])
            ),
            "max_stress_index": float(np.max([d.stress_index for d in days])),
            "mean_stress_index": float(np.mean([d.stress_index for d in days])),
            "days_p_collapse_gt_0_5": int(sum(d.p_collapse > 0.5 for d in days)),
            "days_stress_index_gt_0_5": int(sum(d.stress_index > 0.5 for d in days)),
            "total_events": int(sum(d.event_count for d in days)),
            "total_event_penalty": float(sum(d.event_penalty_total for d in days)),
            "gdp_feedback_pct": float(np.mean([d.gdp_feedback_pct for d in days[-7:]])),
            "granovetter_z_star": round(z_star, 8),
            "granovetter_sigma_c": round(sigma_c, 8),
            "granovetter_r_star": round(r_star, 8),
        }

        artifact = CLIOArtifact(
            status="OK",
            engine_version=request.engine_version,
            request_id=request.request_id,
            request_sha256=input_sha256,
            started_at=started_at,
            finished_at=utc_now_iso(),
            execution_time_seconds=round(time.perf_counter() - clock, 3),
            dimensions={
                "R_replicates": R_REPLICATES,
                "M_particles": M_PARTICLES,
                "M_bits": 13,
                "H_horizon": request.horizon,
                "kernel_horizon": H_HORIZON,
                "particle_steps": request.horizon * R_REPLICATES * M_PARTICLES,
                "event_types": 18,
                "interest_groups": len(params.power.interest_groups),
                "network_nodes": int(power["network_nodes"])
                if days
                else 0,
            },
            backend=backend,
            driver_source=driver_source,
            macro_request_id=request.macro_artifact.request_id
            if request.macro_artifact is not None
            else "",
            macro_mapping=mapping,
            days=days,
            terminal=terminal,
            summary=summary,
            macro_feedback=None,
            warnings=warnings,
            audit={
                "kernel": "clio_cusp_8192_v4_kernel.py",
                "sobol_backend": (
                    "torch_owen_scrambled" if backend == "torch" else "scipy_linear_scrambled"
                ),
                "control_uncertainty": "independent seeded Gaussian per day; dW uses the mRQMC net",
                "date_index": request.horizon_start_date or "relative T+day",
                "dependency_order": [
                    "power",
                    "structural",
                    "events_penalty",
                    "military_coup",
                    "psychology",
                    "cusp",
                ],
                "macro_bridge": "macro_stress_v57.artifact -> drivers" if driver_source != "standalone" else "none",
                "mapped_request_sha256": mapped_sha256 or None,
            },
        )
        # The macro feedback channel is completed by macro_bridge so a pure
        # CLIO caller still gets a fully self-describing artifact.
        try:
            from app.math_engine.clio_cusp.macro_bridge import clio_to_macro_feedback

            artifact.macro_feedback = clio_to_macro_feedback(artifact, request.macro_artifact)
        except Exception as exc:  # pragma: no cover - defensive boundary
            warnings.append(f"macro feedback unavailable: {type(exc).__name__}: {exc}")
            artifact.warnings = warnings

        payload = artifact.model_dump(mode="json")
        for volatile in (
            "artifact_sha256",
            "started_at",
            "finished_at",
            "execution_time_seconds",
        ):
            payload.pop(volatile, None)
        artifact.artifact_sha256 = sha256_value(payload)
        return artifact
    except Exception as exc:
        artifact = CLIOArtifact(
            status="FAILED",
            engine_version=request.engine_version,
            request_id=request.request_id,
            request_sha256=input_sha256,
            started_at=started_at,
            finished_at=utc_now_iso(),
            execution_time_seconds=round(time.perf_counter() - clock, 3),
            backend=request.backend,
            driver_source=driver_source,
            macro_request_id=request.macro_artifact.request_id
            if request.macro_artifact is not None
            else "",
            macro_mapping=mapping,
            warnings=warnings,
            error=f"{type(exc).__name__}: {exc}",
            audit={"traceback": traceback.format_exc(limit=8)},
        )
        return artifact


__all__ = ["run_clio"]
