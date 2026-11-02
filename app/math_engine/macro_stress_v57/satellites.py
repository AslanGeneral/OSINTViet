"""Layer 6 structural satellites evaluated on the 50 Wasserstein scenarios."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle


def _quantiles(values: np.ndarray, weights: np.ndarray) -> Dict[str, float]:
    order = np.argsort(values, kind="mergesort")
    values = values[order]
    weights = weights[order]
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0

    def q(level: float) -> float:
        idx = int(np.searchsorted(cumulative, level, side="left"))
        return float(values[min(idx, values.size - 1)])

    return {
        "p05": round(q(0.05), 6),
        "p50": round(q(0.50), 6),
        "p95": round(q(0.95), 6),
        "mean": round(float(np.dot(weights, values)), 6),
    }


def evaluate_structural_satellites(
    calib: CalibrationBundle,
    f_medoids: np.ndarray,
    cluster_weights: np.ndarray,
) -> Dict[str, Any]:
    f = np.asarray(f_medoids, dtype=float)
    w = np.asarray(cluster_weights, dtype=float)
    w = w / max(float(w.sum()), 1e-300)
    if f.ndim != 2 or f.shape[0] != w.size:
        raise ValueError("F_medoids_terminal and cluster weights are misaligned")
    r = f.shape[1]
    gdp_i = calib.core_index.get("GDP_YOY", 0)
    cpi_i = calib.core_index.get("CPI_YOY", 1)
    fx_i = calib.core_index.get("FX_DEPREC", 2)
    npl_i = calib.core_index.get("NPL_RATIO", 5)

    def factor(index: int) -> np.ndarray:
        if index < r:
            return f[:, index]
        return np.zeros(f.shape[0], dtype=float)

    # Dynamic Nelson-Siegel (Diebold, Rudebusch & Aruoba, 2006).
    level = 3.20 + 0.16 * factor(0) + 0.08 * factor(4 if r > 4 else 0)
    slope = 0.95 + 0.22 * factor(1) - 0.12 * factor(0)
    curvature = 0.40 + 0.10 * factor(2)
    tau = np.array([2.0, 5.0, 10.0], dtype=float)
    lam = 0.65
    x = lam * tau
    factor_slope = (1.0 - np.exp(-x)) / x
    factor_curv = factor_slope - np.exp(-x)
    yields = (
        level[:, None]
        + slope[:, None] * factor_slope[None, :]
        + curvature[:, None] * factor_curv[None, :]
    )

    # Bohn (1998) fiscal snowball with an FX stock-flow adjustment.
    gdp = calib.y_mean[gdp_i] + calib.y_std[gdp_i] * (
        calib.loadings[gdp_i, :r] @ f.T
    )
    cpi = calib.y_mean[cpi_i] + calib.y_std[cpi_i] * (
        calib.loadings[cpi_i, :r] @ f.T
    )
    fx = calib.y_mean[fx_i] + calib.y_std[fx_i] * (
        calib.loadings[fx_i, :r] @ f.T
    )
    r_eff = 6.0 + 0.10 * factor(1) + 0.05 * factor(2)
    debt_prev = np.full(f.shape[0], 35.0, dtype=float)
    primary_balance = 1.0 + 0.05 * factor(0)
    sfa = 0.20 + 0.05 * fx
    debt_next = (
        (1.0 + r_eff / 100.0)
        / np.maximum(1.0 + (gdp + cpi) / 100.0, 0.5)
        * debt_prev
        - primary_balance
        + sfa
    )

    # Adrian-Shin (2010) bank capital channel and Basel credit ceiling.
    delta_npl = 0.10 * factor(0) + 0.05 * factor(2) + 0.02 * factor(3)
    car_prev = np.full(f.shape[0], 11.5, dtype=float)
    nim = 2.5 + 0.05 * factor(1)
    car = car_prev + 0.25 * nim - 0.65 * np.maximum(delta_npl, 0.0)
    credit_ceiling = 3.5 * np.maximum(car - 8.0, 0.0)

    return {
        "method": "reference_structural_satellites_v57",
        "nelson_siegel": {
            "maturities_years": tau.tolist(),
            "yields_pct": {
                f"{int(t)}Y": _quantiles(yields[:, i], w)
                for i, t in enumerate(tau)
            },
            "scenario_matrix": np.round(yields, 6).tolist(),
        },
        "bohn_sovereign_debt": {
            "debt_to_gdp_pct": _quantiles(debt_next, w),
            "primary_balance_pct_gdp": _quantiles(primary_balance, w),
            "effective_rate_pct": _quantiles(r_eff, w),
        },
        "adrian_shin_bank_car": {
            "car_pct": _quantiles(car, w),
            "credit_growth_ceiling_pct": _quantiles(credit_ceiling, w),
            "basel_min_car_pct": 8.0,
            "fraction_below_basel": round(float(np.dot(w, (car < 8.0).astype(float))), 6),
        },
        "scenario_weights": [round(float(v), 10) for v in w],
    }


__all__ = ["evaluate_structural_satellites"]
