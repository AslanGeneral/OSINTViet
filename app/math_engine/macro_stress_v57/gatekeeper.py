"""Layer 5.5: joint CRN gatekeeper, entropy pooling and Wasserstein reduction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.spatial.distance import cdist

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle


def _weighted_quantile(
    values: np.ndarray, weights: np.ndarray, q: float
) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    order = np.argsort(values, kind="mergesort")
    values = values[order]
    weights = weights[order]
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0
    index = int(np.searchsorted(cumulative, q, side="left"))
    return float(values[min(index, values.size - 1)])


def weighted_var_es(
    values: np.ndarray,
    weights: np.ndarray,
    alpha: float = 0.99,
    tail_sign: int = 1,
) -> Tuple[float, float]:
    """Rockafellar-Uryasev boundary-split weighted VaR/ES."""
    losses = tail_sign * np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    weights = weights / max(float(np.sum(weights)), 1e-300)
    order = np.argsort(losses, kind="mergesort")
    losses = losses[order]
    weights = weights[order]
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0
    index = int(np.searchsorted(cumulative, alpha, side="left"))
    index = min(index, losses.size - 1)
    var_loss = float(losses[index])
    atom = max(0.0, float(cumulative[index] - alpha))
    tail = atom * var_loss
    if index + 1 < losses.size:
        tail += float(np.dot(weights[index + 1 :], losses[index + 1 :]))
    return float(tail_sign * var_loss), float(tail_sign * tail / (1.0 - alpha))


def _physical_mask(y: np.ndarray, calib: CalibrationBundle) -> np.ndarray:
    n_particles = y.shape[0]
    reserves_idx = calib.core_index.get("FX_RESERVES", 4)
    npl_idx = calib.core_index.get("NPL_RATIO", 5)
    ib_idx = calib.core_index.get("IB_RATE", 3)
    reserves = y[:, :, reserves_idx]
    npl = y[:, :, npl_idx]
    ib = y[:, :, ib_idx]
    mask = (
        np.all(np.isfinite(y), axis=(1, 2))
        & np.all(reserves >= 5.0, axis=1)
        & np.all((npl >= 0.2) & (npl <= 35.0), axis=1)
        & np.all((ib >= 0.1) & (ib <= 40.0), axis=1)
    )
    return mask.astype(bool)


def _sfc_mask(
    y: np.ndarray,
    calib: CalibrationBundle,
    initial_reserves: float,
    tolerance: float,
) -> Tuple[np.ndarray, np.ndarray]:
    n_particles, horizon, _ = y.shape
    reserves_idx = calib.core_index.get("FX_RESERVES", 4)
    ca_idx = calib.core_index.get("CURRENT_ACCOUNT", 7)
    fa_idx = calib.core_index.get("FINANCIAL_ACCOUNT", 8)
    previous = np.full((n_particles, 1), initial_reserves, dtype=float)
    delta = np.diff(
        np.concatenate([previous, y[:, :, reserves_idx]], axis=1), axis=1
    )
    discrepancy = np.abs(delta - (y[:, :, ca_idx] + y[:, :, fa_idx]))
    margin = np.min(tolerance - discrepancy, axis=1)
    return margin >= 0.0, margin


def _sign_mask(y: np.ndarray, calib: CalibrationBundle) -> np.ndarray:
    ib_idx = calib.core_index.get("IB_RATE", 3)
    fx_idx = calib.core_index.get("FX_DEPREC", 2)
    npl_idx = calib.core_index.get("NPL_RATIO", 5)
    mean_ib = np.mean(y[:, :, ib_idx], axis=1)
    max_fx = np.max(y[:, :, fx_idx], axis=1)
    terminal_npl = y[:, -1, npl_idx]
    return ~((mean_ib > 10.0) & (max_fx > 5.0) & (terminal_npl < 1.5))


def evaluate_joint_gate(
    sim: Dict[str, Any],
    calib: CalibrationBundle,
    *,
    tolerance: float = 5.0,
    minimum_valid: int = 50,
) -> Dict[str, Any]:
    paths = sim["branch_paths"]
    initial_reserves = float(sim["initial_reserves"])
    valid_by_branch: Dict[str, np.ndarray] = {}
    margins: Dict[str, np.ndarray] = {}
    for branch, y in paths.items():
        physical = _physical_mask(y, calib)
        sfc, sfc_margin = _sfc_mask(y, calib, initial_reserves, tolerance)
        sign = _sign_mask(y, calib)
        valid = physical & sfc & sign
        valid_by_branch[branch] = valid
        margins[branch] = np.minimum.reduce(
            [
                np.where(physical, 1.0, -1.0),
                np.where(sfc, 1.0, -1.0),
                sfc_margin,
                np.where(sign, 1.0, -1.0),
            ]
        )
    joint = valid_by_branch["baseline"] & valid_by_branch["passive"] & valid_by_branch["active"]
    if int(joint.sum()) < minimum_valid:
        # A strict intersection is preferred, but never silently discard the
        # entire tail.  The fallback is recorded explicitly in the artifact.
        fallback = valid_by_branch["active"]
        if int(fallback.sum()) < minimum_valid:
            fallback = np.ones(joint.shape, dtype=bool)
        joint = fallback
        fallback_used = True
    else:
        fallback_used = False
    return {
        "valid_by_branch": valid_by_branch,
        "joint_valid": joint,
        "fallback_used": fallback_used,
        "n_valid": int(joint.sum()),
        "acceptance_rate": float(joint.mean()),
        "minimum_valid": int(minimum_valid),
        "physical_acceptance": {
            k: float(v.mean()) for k, v in valid_by_branch.items()
        },
        "logical_margin": {
            k: float(np.min(v)) for k, v in margins.items()
        },
        "rule": "physical_domain AND SFC_BOP_tolerance AND economic_transmission_sign",
    }


def entropy_pool_weights(
    gdp_terminal: np.ndarray,
    prior: np.ndarray,
    *,
    target_mean: float,
    max_iterations: int = 160,
) -> Tuple[np.ndarray, float]:
    """Solve the one-constraint exponential tilt with a stable bisection.

    This is the saturated one-constraint special case of the dual Lagrangian
    in Eq. (42).  The posterior weights are solved once on the baseline branch
    and then frozen across all branches.
    """
    gdp = np.asarray(gdp_terminal, dtype=float)
    prior = np.asarray(prior, dtype=float)
    prior = prior / max(float(prior.sum()), 1e-300)
    low, high = float(gdp.min()), float(gdp.max())
    target = float(np.clip(target_mean, low + 1e-9, high - 1e-9))
    if abs(target - float(np.dot(prior, gdp))) < 1e-9:
        return prior.copy(), 0.0

    def tilted(lam: float) -> np.ndarray:
        logits = np.log(np.maximum(prior, 1e-300)) + lam * (gdp - target)
        logits -= np.max(logits)
        weights = np.exp(logits)
        return weights / weights.sum()

    def mean_gap(lam: float) -> float:
        return float(np.dot(tilted(lam), gdp) - target)

    lo, hi = -100.0, 100.0
    for _ in range(int(max_iterations)):
        mid = 0.5 * (lo + hi)
        if mean_gap(mid) > 0:
            hi = mid
        else:
            lo = mid
    posterior = tilted(0.5 * (lo + hi))
    return posterior, 0.5 * (lo + hi)


def tail_risk_table(
    terminal: np.ndarray,
    weights: np.ndarray,
    calib: CalibrationBundle,
    alpha: float = 0.99,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for j, code in enumerate(calib.codes):
        sign = int(calib.tail_signs.get(code, 1))
        var, es = weighted_var_es(
            terminal[:, j], weights, alpha=alpha, tail_sign=sign
        )
        mean = float(np.dot(weights, terminal[:, j]))
        std = float(
            np.sqrt(np.dot(weights, (terminal[:, j] - mean) ** 2))
        )
        rows.append(
            {
                "series_code": code,
                "series_name_vi": (
                    calib.names_vi[j] if j < len(calib.names_vi) else code
                ),
                "mean": round(mean, 6),
                "std": round(std, 6),
                "var_99": round(float(var), 6),
                "es_99": round(float(es), 6),
                "tail_sign": sign,
                "unit": calib.units[j] if j < len(calib.units) else "",
            }
        )
    return rows


def branch_metrics(
    y: np.ndarray,
    weights: np.ndarray,
    calib: CalibrationBundle,
    *,
    baseline_gdp_mean: float,
    initial_reserves: float,
    alpha: float = 0.99,
) -> Dict[str, Any]:
    gdp_idx = calib.core_index.get("GDP_YOY", 0)
    fx_idx = calib.core_index.get("FX_DEPREC", 2)
    ib_idx = calib.core_index.get("IB_RATE", 3)
    npl_idx = calib.core_index.get("NPL_RATIO", 5)
    terminal_gdp = y[:, -1, gdp_idx]
    gdp_trough = np.min(y[:, :, gdp_idx], axis=1)
    mean_gdp = float(np.dot(weights, terminal_gdp))
    std_gdp = float(np.sqrt(np.dot(weights, (terminal_gdp - mean_gdp) ** 2)))
    loss = baseline_gdp_mean - gdp_trough
    var_loss, es_loss = weighted_var_es(loss, weights, alpha=alpha, tail_sign=1)
    fx_var, _ = weighted_var_es(
        y[:, -1, fx_idx], weights, alpha=alpha, tail_sign=1
    )
    peak_ib = np.max(y[:, :, ib_idx], axis=1)
    peak_95 = _weighted_quantile(peak_ib, weights, 0.95)
    terminal_reserves = float(np.dot(weights, y[:, -1, calib.core_index.get("FX_RESERVES", 4)]))
    npl_peak = np.max(y[:, :, npl_idx], axis=1)
    breach = float(np.dot(weights, (npl_peak > 3.5).astype(float)))
    return {
        "gdp_yoy_mean_pct": round(mean_gdp, 6),
        "gdp_yoy_trough_mean_pct": round(float(np.dot(weights, gdp_trough)), 6),
        "gdp_yoy_std_pct": round(std_gdp, 6),
        "gdp_tail_loss_var_pct": round(float(var_loss), 6),
        "gdp_tail_loss_es_pct": round(float(es_loss), 6),
        "fx_deprec_var_pct": round(float(fx_var), 6),
        "fx_reserves_drain_billion_usd": round(
            max(0.0, initial_reserves - terminal_reserves), 6
        ),
        "interbank_peak_p95_pct": round(float(peak_95), 6),
        "npl_breach_probability": round(float(breach), 6),
        "npl_terminal_mean_pct": round(
            float(np.dot(weights, y[:, -1, npl_idx])), 6
        ),
    }


def wasserstein_reduction(
    y_valid: np.ndarray,
    factor_valid: np.ndarray,
    weights: np.ndarray,
    *,
    k_rep: int = 50,
    m_candidates: int = 250,
    seed: int = 20261026,
    time_discount: float = 0.95,
) -> Dict[str, Any]:
    n_valid, horizon, n_series = y_valid.shape
    k_rep = min(int(k_rep), n_valid)
    if k_rep <= 0:
        raise ValueError("k_rep must be positive")
    if n_valid <= k_rep:
        selected = np.arange(n_valid)
        return {
            "medoid_local_indices": selected.tolist(),
            "medoid_global_indices": selected.tolist(),
            "cluster_weights": (weights / weights.sum()).tolist(),
            "F_medoids_terminal": factor_valid[:, -1, :].tolist(),
            "k_rep": int(n_valid),
            "degenerate": True,
            "pc1_norm": 0.0,
        }

    w = np.asarray(weights, dtype=float)
    w = w / max(float(w.sum()), 1e-300)
    mean = np.sum(y_valid * w[:, None, None], axis=0, keepdims=True)
    variance = np.sum(
        w[:, None, None] * (y_valid - mean) ** 2, axis=0, keepdims=True
    )
    std = np.sqrt(np.maximum(variance, 1e-8))
    discount = np.power(time_discount, np.arange(horizon, dtype=float))[None, :, None]
    flat = ((y_valid - mean) / std * np.sqrt(discount)).reshape(
        n_valid, horizon * n_series
    )

    v = np.ones(flat.shape[1], dtype=float) / np.sqrt(flat.shape[1])
    for _ in range(8):
        candidate = flat.T @ (w * (flat @ v))
        norm = float(np.linalg.norm(candidate))
        if norm > 1e-12:
            v = candidate / norm
    scores = flat @ v
    order = np.argsort(scores, kind="mergesort")
    cumulative = np.cumsum(w[order])
    cumulative[-1] = 1.0
    m_candidates = max(8, min(int(m_candidates), n_valid))
    grid = np.linspace(0.5 / m_candidates, 1.0 - 0.5 / m_candidates, m_candidates)
    candidate_indices = order[
        np.unique(np.searchsorted(cumulative, grid, side="left"))
    ]
    if candidate_indices.size < k_rep:
        used = set(candidate_indices.tolist())
        extra = [int(i) for i in np.argsort(-w) if int(i) not in used]
        candidate_indices = np.concatenate(
            [candidate_indices, np.asarray(extra[: k_rep - candidate_indices.size], dtype=int)]
        )
    if candidate_indices.size < k_rep:
        candidate_indices = np.arange(n_valid, dtype=int)

    distances = cdist(flat, flat[candidate_indices], metric="euclidean")
    selected_local: List[int] = []
    current = np.full(n_valid, np.inf, dtype=float)
    trial = np.empty_like(distances)
    for _ in range(k_rep):
        np.minimum(current[:, None], distances, out=trial)
        costs = w @ trial
        costs[selected_local] = np.inf
        best = int(np.argmin(costs))
        selected_local.append(best)
        np.minimum(current, distances[:, best], out=current)
    selected_local_arr = np.asarray(selected_local, dtype=int)
    medoid_global = candidate_indices[selected_local_arr]
    nearest = np.argmin(distances[:, selected_local_arr], axis=1)
    cluster_weights = np.bincount(
        nearest, weights=w, minlength=k_rep
    ).astype(float)
    cluster_weights = cluster_weights / max(cluster_weights.sum(), 1e-300)
    f_medoid = factor_valid[medoid_global, -1, :]
    return {
        "medoid_local_indices": selected_local_arr.tolist(),
        "medoid_global_indices": medoid_global.tolist(),
        "cluster_weights": cluster_weights.tolist(),
        "F_medoids_terminal": f_medoid.tolist(),
        "k_rep": int(k_rep),
        "degenerate": False,
        "candidate_count": int(candidate_indices.size),
        "pc1_norm": round(float(np.linalg.norm(v)), 8),
        "kish_ess": round(float(1.0 / np.sum(w**2)), 6),
    }


def normalize_prior(weights: np.ndarray, valid: np.ndarray) -> np.ndarray:
    p = np.asarray(weights, dtype=float)[valid]
    p = np.maximum(p, 1e-300)
    return p / p.sum()


def enforce_kish_floor(
    posterior: np.ndarray,
    prior: np.ndarray,
    *,
    minimum_ess: float = 200.0,
) -> tuple[np.ndarray, float, float]:
    """Back off toward the prior until the Kish ESS floor is satisfied."""
    posterior = np.asarray(posterior, dtype=float)
    prior = np.asarray(prior, dtype=float)
    posterior = posterior / max(float(posterior.sum()), 1e-300)
    prior = prior / max(float(prior.sum()), 1e-300)
    for alpha in (1.0, 0.9, 0.75, 0.6, 0.5, 0.35, 0.2, 0.0):
        blended = alpha * posterior + (1.0 - alpha) * prior
        blended = blended / max(float(blended.sum()), 1e-300)
        ess = float(1.0 / np.sum(blended**2))
        if ess >= minimum_ess:
            return blended, float(alpha), ess
    # If the prior itself is below the finite-particle floor, a uniform
    # defensive-mixture fallback is the only auditable way to satisfy it.  The
    # artifact's blend_alpha=0 and kish_ess=N make this case explicit.
    uniform = np.full_like(prior, 1.0 / max(prior.size, 1))
    return uniform, 0.0, float(uniform.size)


__all__ = [
    "branch_metrics",
    "enforce_kish_floor",
    "entropy_pool_weights",
    "evaluate_joint_gate",
    "normalize_prior",
    "tail_risk_table",
    "wasserstein_reduction",
    "weighted_var_es",
]
