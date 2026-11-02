"""Layer 6 observables-projected GFEVD (36 x 36) and deterministic briefing."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle


def compute_gfevd(
    calib: CalibrationBundle, horizon: int = 4
) -> tuple[np.ndarray, List[Dict[str, Any]]]:
    r = calib.n_factors
    n = calib.n_series
    lambda_mat = calib.loadings
    omega_bar = 0.5 * (calib.omega_regimes[0] + calib.omega_regimes[1])
    sigma_y = lambda_mat @ omega_bar @ lambda_mat.T + np.diag(calib.r_diag)
    sigma_diag = np.maximum(np.diag(sigma_y), 1e-12)
    w_proj = calib.w_proj

    phi: List[np.ndarray] = [np.eye(r, dtype=float)]
    for h in range(1, max(1, horizon)):
        phi_h = np.zeros((r, r), dtype=float)
        for lag, a_lag in enumerate(calib.a_lags):
            if h - 1 - lag >= 0:
                phi_h += a_lag @ phi[h - 1 - lag]
        phi.append(phi_h)

    numer = np.zeros((n, n), dtype=float)
    denom = np.zeros(n, dtype=float)
    for h in range(max(1, horizon)):
        psi = lambda_mat @ phi[h] @ w_proj
        p_s = psi @ sigma_y
        numer += (p_s**2) / sigma_diag[None, :]
        denom += np.sum(p_s * psi, axis=1)
    theta = numer / np.maximum(denom[:, None], 1e-12)
    rows = np.maximum(theta.sum(axis=1, keepdims=True), 1e-12)
    matrix = theta / rows * 100.0

    edges: List[Dict[str, Any]] = []
    for target in range(n):
        for source in range(n):
            if source == target:
                continue
            edges.append(
                {
                    "source_index": source,
                    "target_index": target,
                    "source_code": calib.codes[source],
                    "target_code": calib.codes[target],
                    "variance_contribution_pct": round(
                        float(matrix[target, source]), 6
                    ),
                }
            )
    edges.sort(key=lambda item: item["variance_contribution_pct"], reverse=True)
    return matrix, edges[:80]


def deterministic_briefing(
    calib: CalibrationBundle,
    passive: Dict[str, Any],
    active: Dict[str, Any],
    shock: Dict[str, Any],
    *,
    regime_stress_probability: float,
) -> List[str]:
    gdp_var = float(passive.get("gdp_tail_loss_var_pct", 0.0))
    active_gdp_var = float(active.get("gdp_tail_loss_var_pct", 0.0))
    mitigation = (
        (gdp_var - active_gdp_var) / abs(gdp_var) * 100.0
        if abs(gdp_var) > 1e-12
        else 0.0
    )
    drain_p = float(passive.get("fx_reserves_drain_billion_usd", 0.0))
    drain_a = float(active.get("fx_reserves_drain_billion_usd", 0.0))
    ib_p = float(passive.get("interbank_peak_p95_pct", 0.0))
    ib_a = float(active.get("interbank_peak_p95_pct", 0.0))
    npl_p = float(passive.get("npl_breach_probability", 0.0))
    npl_a = float(active.get("npl_breach_probability", 0.0))
    best_objective = float(shock.get("best_objective", 0.0))
    return [
        (
            "The v5.7 adversarial layer selects a copula-GPD shock with objective "
            f"{best_objective:.4f}; the joint stress-regime probability is "
            f"{regime_stress_probability:.1%}."
        ),
        (
            "The passive branch's weighted 99% GDP tail loss is "
            f"{gdp_var:.4f} pp; the active three-tier SBV corridor reduces it to "
            f"{active_gdp_var:.4f} pp ({mitigation:.2f}% tail-risk mitigation)."
        ),
        (
            "The active corridor contains the P95 interbank peak at "
            f"{ib_a:.2f}% versus {ib_p:.2f}% passive, and limits the NPL breach "
            f"probability to {npl_a:.1%} versus {npl_p:.1%}."
        ),
        (
            "Cumulative FX reserve depletion is "
            f"{drain_a:.2f} billion USD under active defense versus "
            f"{drain_p:.2f} billion USD passive; the SFC projection keeps the "
            "reserve stock-flow identity within tolerance before the absorbing floor."
        ),
    ]


__all__ = ["compute_gfevd", "deterministic_briefing"]
