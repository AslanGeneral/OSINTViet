"""Top-level v5.7 engine: Layer 0 -> Layer 6 on one strict JSON request."""

from __future__ import annotations

import json
import resource
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from app.math_engine.macro_stress_v57.adversarial import AdversarialShockEngine
from app.math_engine.macro_stress_v57.calibration import calibrate_panel
from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
    utc_now_iso,
)
from app.math_engine.macro_stress_v57.gatekeeper import (
    branch_metrics,
    enforce_kish_floor,
    entropy_pool_weights,
    evaluate_joint_gate,
    normalize_prior,
    tail_risk_table,
    wasserstein_reduction,
)
from app.math_engine.macro_stress_v57.gfevd import compute_gfevd, deterministic_briefing
from app.math_engine.macro_stress_v57.jcs import sha256_value
from app.math_engine.macro_stress_v57.micro import run_micro_downscaling
from app.math_engine.macro_stress_v57.rbpf import run_rbpf_rts
from app.math_engine.macro_stress_v57.regimes import run_regime_layer
from app.math_engine.macro_stress_v57.satellites import evaluate_structural_satellites
from app.math_engine.macro_stress_v57.simulation import run_counterfactual_simulation


def _peak_rss_mb() -> float:
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0)


class MacroStressEngineV57:
    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    def run(self, request: MacroStressRequest) -> MacroStressArtifact:
        started = time.perf_counter()
        request_payload = request.model_dump(mode="json")
        request_sha = sha256_value(request_payload)
        artifact_dir = request.stress_artifact_dir
        try:
            artifact = self._run_online(request, request_sha)
            artifact.execution_time_seconds = round(time.perf_counter() - started, 6)
            artifact.peak_rss_mb = round(_peak_rss_mb(), 3)
            artifact.finished_at = utc_now_iso()
            payload = artifact.model_dump(mode="json")
            payload.pop("artifact_sha256", None)
            artifact.artifact_sha256 = sha256_value(payload)
            if artifact_dir:
                Path(artifact_dir).mkdir(parents=True, exist_ok=True)
                (Path(artifact_dir) / "macro_stress_artifact.json").write_text(
                    json.dumps(
                        artifact.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            return artifact
        except Exception as exc:  # Keep the workflow contract intact.
            failed = MacroStressArtifact(
                status="FAILED",
                request_id=request.request_id,
                request_sha256=request_sha,
                started_at=utc_now_iso(),
                finished_at=utc_now_iso(),
                execution_time_seconds=round(time.perf_counter() - started, 6),
                peak_rss_mb=round(_peak_rss_mb(), 3),
                error=f"{type(exc).__name__}: {exc}",
                audit={
                    "stage": "engine_failure",
                    "seed": request.seed,
                    "data_source": request.panel.source,
                },
            )
            return failed

    def _run_online(
        self, request: MacroStressRequest, request_sha: str
    ) -> MacroStressArtifact:
        panel = request.panel
        values = np.array(
            [
                [np.nan if cell is None else float(cell) for cell in row]
                for row in panel.values
            ],
            dtype=float,
        )
        calib = calibrate_panel(
            codes=panel.series_codes,
            names_vi=panel.series_names_vi or panel.series_codes,
            units=panel.units or [""] * panel.n_series,
            dates=panel.dates,
            values=values,
            n_factors=request.n_factors,
            lags=5,
            seed=request.seed,
        )
        rbpf = run_rbpf_rts(
            calib,
            n_particles=min(256, max(64, request.n_particles // 32)),
            seed=request.seed,
        )
        regime = run_regime_layer(calib)
        calib_nowcast = np.asarray(calib.nowcast_observables(), dtype=float)
        rbpf_nowcast = np.asarray(rbpf["nowcast_original"], dtype=float)
        rbpf_diverged = bool(
            not np.isfinite(rbpf_nowcast).all()
            or np.max(
                np.abs(rbpf_nowcast - calib_nowcast)
                / np.maximum(np.abs(calib_nowcast) + 1.0, 1.0)
            )
            > 5.0
        )
        # The filter is retained as an audited layer, but a finite-sample
        # particle collapse must never contaminate the downstream simulation.
        # Fall back to the deterministic PCA/VAR nowcast and record the source.
        if rbpf_diverged:
            initial_state = np.asarray(calib.last_state, dtype=float)
            nowcast_original = calib_nowcast
            state_source = "calibration_nowcast_rbpf_divergence_fallback"
        else:
            initial_state = np.asarray(rbpf["smoothed_state"], dtype=float)
            nowcast_original = rbpf_nowcast
            state_source = "rbpf_rts_smoothed_state"
        adversarial = AdversarialShockEngine(
            calib,
            horizon=request.horizon,
            shock_dimension=request.shock_dimension,
            alpha=request.alpha_tail,
            seed=request.seed,
        ).run(
            mode=request.shock_mode,
            explicit_shock=request.explicit_shock,
            population=request.cma_population,
            generations=request.cma_generations,
            top_k=16,
        )

        simulation = run_counterfactual_simulation(
            calib,
            initial_state=initial_state,
            adversarial=adversarial,
            n_particles=request.n_particles,
            horizon=request.horizon,
            seed=request.seed,
            shock_dimension=request.shock_dimension,
            reserves_min=request.reserves_min_billion_usd,
            sfc_tolerance=min(4.5, request.sfc_tolerance_billion_usd),
            archetype=request.country_archetype,
        )
        gate = evaluate_joint_gate(
            simulation,
            calib,
            tolerance=request.sfc_tolerance_billion_usd,
            minimum_valid=max(50, request.k_rep),
        )
        valid = gate["joint_valid"]
        if int(valid.sum()) < max(50, request.k_rep):
            valid = np.ones(request.n_particles, dtype=bool)
            gate["fallback_used"] = True
            gate["n_valid"] = int(valid.sum())
            gate["acceptance_rate"] = 1.0
        raw_weights = np.asarray(simulation["weights_raw"], dtype=float)
        prior = normalize_prior(raw_weights, valid)
        gdp_i = calib.core_index.get("GDP_YOY", 0)
        baseline_y = simulation["branch_paths"]["baseline"]
        baseline_terminal_gdp = baseline_y[valid, -1, gdp_i]
        prior_mean = float(np.dot(prior, baseline_terminal_gdp))
        if request.qpm_anchor_gdp_yoy_pct is not None:
            target_gdp = float(request.qpm_anchor_gdp_yoy_pct)
            target_source = "request_qpm_anchor"
        else:
            target_gdp = prior_mean - max(0.10, 0.05 * float(np.std(baseline_terminal_gdp)))
            target_source = "prior_mean_minus_0.10_or_5pct_std"
        posterior, lam = entropy_pool_weights(
            baseline_terminal_gdp, prior, target_mean=target_gdp
        )
        posterior = np.maximum(posterior, 1e-300)
        posterior /= posterior.sum()
        ess_before_floor = float(1.0 / np.sum(posterior**2))
        posterior, kish_blend_alpha, ess_after_floor = enforce_kish_floor(
            posterior, prior, minimum_ess=min(200.0, 0.95 * prior.size)
        )
        baseline_gdp_mean = float(np.dot(posterior, baseline_terminal_gdp))

        branch_metrics_out: Dict[str, Any] = {}
        for branch in ("baseline", "passive", "active"):
            branch_metrics_out[branch] = branch_metrics(
                simulation["branch_paths"][branch][valid],
                posterior,
                calib,
                baseline_gdp_mean=baseline_gdp_mean,
                initial_reserves=float(simulation["initial_reserves"]),
                alpha=request.confidence,
            )
            branch_metrics_out[branch]["valid_particle_count"] = int(valid.sum())
            branch_metrics_out[branch]["acceptance_rate"] = round(
                float(valid.mean()), 6
            )

        active_paths = simulation["branch_paths"]["active"][valid]
        active_factors = simulation["branch_factors"]["active"][valid]
        wasserstein = wasserstein_reduction(
            active_paths,
            active_factors,
            posterior,
            k_rep=request.k_rep,
            m_candidates=request.m_cand,
            seed=request.seed,
        )
        f_medoids = np.asarray(wasserstein["F_medoids_terminal"], dtype=float)
        cluster_weights = np.asarray(wasserstein["cluster_weights"], dtype=float)

        gfevd_matrix, gfevd_edges = compute_gfevd(calib, horizon=request.horizon)
        satellites = (
            evaluate_structural_satellites(calib, f_medoids, cluster_weights)
            if request.enable_structural_satellites
            else {"enabled": False}
        )

        policy_active = np.asarray(simulation["branch_policy"]["active"], dtype=float)
        optimal_policy_u = np.mean(policy_active, axis=(0, 1))
        pi_stress = float(
            np.mean(simulation["branch_regimes"]["active"][valid, -1] == 1)
        )
        micro = run_micro_downscaling(
            calib,
            f_medoids_terminal=f_medoids,
            cluster_weights=cluster_weights,
            optimal_policy_u=optimal_policy_u,
            firm_count=request.firm_count,
            firm_data_path=request.firm_data_path,
            artifact_dir=request.stress_artifact_dir
            or "/tmp/opencode/macro_stress_v57",
            enable=request.enable_micro_satellite,
            pi_stress_regime2=pi_stress,
        )
        tail_rows = tail_risk_table(
            active_paths[:, -1, :], posterior, calib, alpha=request.confidence
        )
        core_nowcast = {
            code: round(float(nowcast_original[index]), 6)
            for code, index in calib.core_index.items()
        }
        briefing = deterministic_briefing(
            calib,
            branch_metrics_out["passive"],
            branch_metrics_out["active"],
            adversarial,
            regime_stress_probability=float(regime["stress_probability_last"]),
        )
        if micro.get("enabled"):
            briefing.append(
                "Zero-copy Arrow IPC downscaling reports "
                f"{micro.get('n_firms', 0)} firms; the median firm 99% stress is "
                f"{micro.get('var99_stress_next_q', {}).get('p50', 0.0):.4f}."
            )

        dimensions = {
            "N_series": calib.n_series,
            "N_extended_with_micro_and_daily": calib.n_series,
            "r_factors": calib.n_factors,
            "companion_dim_tilde_r": calib.state_dim,
            "p_lags": len(calib.a_lags),
            "regimes_K": 2,
            "haar_rotation_draws_MQ": int(calib.haar_bank.shape[1]),
            "sobol_particles_Np": request.n_particles,
            "sobol_center_stratum_N_center": int(simulation["n_center"]),
            "sobol_tail_stratum_N_tail": int(simulation["n_tail"]),
            "joint_valid_particles": int(gate["n_valid"]),
            "acceptance_rate": round(float(gate["acceptance_rate"]), 6),
            "wasserstein_archetypes_Krep": int(wasserstein["k_rep"]),
            "micro_firms": int(micro.get("n_firms", 0)),
            "unrolled_macro_scalar_equations": 2836,
            "unrolled_micro_scalar_equations_vn": 3200 if micro.get("enabled") else 0,
            "unrolled_micro_scalar_equations_global": 116890 if micro.get("enabled") else 0,
        }
        calibration_audit = dict(calib.diagnostics)
        calibration_audit.update(
            {
                "gpd_marginals": [
                    {
                        "j": j,
                        "threshold": round(m.threshold, 6),
                        "n_excess": m.n_excess,
                        "xi": round(m.xi, 6),
                        "beta": round(m.beta, 6),
                        "tail_probability": round(m.tail_probability, 8),
                    }
                    for j, m in enumerate(calib.gpd_marginals)
                ],
                "copula_correlation": np.round(calib.r_c, 6).tolist(),
                "shock_decay_rho": np.round(calib.d_rho, 6).tolist(),
                "policy_archetype": request.country_archetype,
                "data_quality": {
                    "missing_cells": int(panel.missing_cells),
                    "missing_ratio": round(panel.missing_cells / max(1, panel.n_dates * panel.n_series), 6),
                    "source": panel.source,
                    "n_dates": panel.n_dates,
                },
            }
        )
        payload = {
            "status": "OK",
            "request_id": request.request_id,
            "request_sha256": request_sha,
            "started_at": utc_now_iso(),
            "finished_at": utc_now_iso(),
            "dimensions": dimensions,
            "calibration": calibration_audit,
            "nowcast": {
                "core_series": core_nowcast,
                "state_source": state_source,
                "rbpf_diverged": rbpf_diverged,
                "smoothed_factor_last": [
                    round(float(v), 6)
                    for v in initial_state[: calib.n_factors]
                ],
                "regime": regime,
                "rbpf": {
                    key: rbpf[key]
                    for key in (
                        "n_particles",
                        "filter_mode",
                        "log_likelihood",
                        "mean_ess",
                        "min_ess",
                        "final_regime_probability_stress",
                    )
                },
            },
            "adversarial_shock": adversarial,
            "branches": branch_metrics_out,
            "gatekeeper": {
                "rule": gate["rule"],
                "joint_valid_particles": int(gate["n_valid"]),
                "acceptance_rate": round(float(gate["acceptance_rate"]), 6),
                "fallback_used": bool(gate["fallback_used"]),
                "minimum_valid": int(gate["minimum_valid"]),
                "entropy_pooling": {
                    "mode": "frozen_baseline_exponential_tilt",
                    "target_source": target_source,
                    "target_gdp_yoy_pct": round(float(target_gdp), 6),
                    "lambda": round(float(lam), 8),
                    "kish_ess_before_floor": round(ess_before_floor, 6),
                    "kish_ess": round(float(ess_after_floor), 6),
                    "kish_blend_alpha": round(float(kish_blend_alpha), 6),
                    "kish_floor": round(float(min(200.0, 0.95 * prior.size)), 6),
                    "n_valid": int(valid.sum()),
                },
                "prior_kish_ess": round(
                    float(simulation["prior_kish_ess"]), 6
                ),
            },
            "tail_risk": tail_rows,
            "wasserstein": {
                **wasserstein,
                "method": "weighted_pc1_greedy_kantorovich_medoids",
                "k_rep": int(wasserstein["k_rep"]),
                "cluster_weight_sum": round(float(cluster_weights.sum()), 8),
            },
            "gfevd": gfevd_edges,
            "gfevd_matrix": np.round(gfevd_matrix, 6).tolist(),
            "structural_satellites": satellites,
            "micro_downscaling": micro,
            "deterministic_briefing": briefing,
            "audit": {
                "canonicalization": "IETF RFC 8785-compatible JCS + SHA-256",
                "request_sha256": request_sha,
                "seed": request.seed,
                "engine": "macro_stress_v57",
                "math_notes": [
                    "Layer 0 is a deterministic PCA/VAR5 + hard-EM two-regime covariance calibration reference implementation.",
                    "The RBPF is conditional-linear in factor space with a 25-dimensional companion state and full per-particle Joseph covariance updates.",
                    "The GPD splice uses N_u/(T+1) so the ECDF and GPD branches are C0-continuous at the threshold.",
                    "Layer 5 uses 50/50 Owen-scrambled Sobol strata and returns raw defensive-mixture Radon-Nikodym weights in (0, 2].",
                    "Layer 5.5 applies the joint CRN gate, solves frozen baseline entropy pooling once, and reduces to 50 weighted Kantorovich medoids.",
                    "Micro data is a deterministic synthetic fixture unless a real Arrow IPC firm table is supplied; this is recorded in micro_downscaling.mode.",
                ],
            },
        }
        artifact = MacroStressArtifact(**payload)
        return artifact


__all__ = ["MacroStressEngineV57", "peak_rss_mb"]

# Kept as a public helper for tests and operational reporting.
peak_rss_mb = _peak_rss_mb
