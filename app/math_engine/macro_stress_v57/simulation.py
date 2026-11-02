"""Layer 5: three-branch common-random-number Monte Carlo propagation.

The tensor shapes are the v5.7 online shapes::

    Z_sobol_is : (N_particles, H, r)
    Y_base/pass/act : (N_particles, H, N_series)

All branches share the same Sobol draws, regime draws and Haar rotation index,
which is what makes the passive/active comparison causal rather than a
comparison of unrelated samples.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import qmc

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle
from app.math_engine.macro_stress_v57.markov import (
    step1_and_step3_endogenous_markov_propagation,
)


def branchless_pos(x: np.ndarray | float) -> np.ndarray | float:
    return 0.5 * (x + np.abs(x))


def branchless_clamp(x: np.ndarray | float, low: float, high: float) -> np.ndarray | float:
    return np.minimum(np.maximum(x, low), high)


def allocate_shock_blocks(
    weights: np.ndarray, n_particles: int, n_shocks: int
) -> np.ndarray:
    weights = np.asarray(weights, dtype=float)
    if weights.size == 0 or not np.isfinite(weights).all() or weights.sum() <= 0:
        weights = np.ones(n_shocks, dtype=float)
    weights = weights[:n_shocks] / np.sum(weights[:n_shocks])
    cumulative = np.cumsum(weights * n_particles).astype(int)
    cumulative[-1] = n_particles
    indices = np.zeros(n_particles, dtype=np.int32)
    start = 0
    for index, end in enumerate(cumulative):
        end = max(start, min(int(end), n_particles))
        indices[start:end] = index
        start = end
    if start < n_particles:
        indices[start:] = n_shocks - 1
    return indices


def defensive_mixture_shocks(
    *,
    n_particles: int,
    horizon: int,
    n_factors: int,
    seed: int,
    alpha_center: float = 0.50,
    mu_twist: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, int]:
    if abs(float(alpha_center) - 0.50) > 1e-12:
        raise ValueError("alpha_center must be exactly 0.50 for the two Sobol strata")
    n_particles = int(n_particles)
    n_center = int(round(alpha_center * n_particles))
    sampler = qmc.Sobol(d=n_factors * horizon, scramble=True, seed=int(seed))
    uniforms = np.clip(
        sampler.random_base2(m=int(round(np.log2(max(64, n_particles))))),
        1e-12,
        1.0 - 1e-12,
    )
    if uniforms.shape[0] < n_particles:
        uniforms = np.vstack(
            [uniforms, uniforms[: n_particles - uniforms.shape[0]]]
        )
    uniforms = uniforms[:n_particles]
    from scipy.stats import norm

    z = norm.ppf(uniforms).reshape(n_particles, horizon, n_factors)
    if mu_twist is None:
        mu_twist = np.array([-0.85, -0.60, 0.75, -0.90, 0.80, 0.60][:n_factors])
    mu_twist = np.asarray(mu_twist, dtype=float)[:n_factors]
    z[n_center:, :, :] += mu_twist[None, None, :]
    # Raw Radon-Nikodym derivative in (0, 2]; normalization is performed at
    # Layer 5.5 only, never inside this generator.
    dot = np.einsum("nhr,r->nh", z, mu_twist)
    log_ratio = np.clip(
        np.sum(dot - 0.5 * float(np.dot(mu_twist, mu_twist)), axis=1), -50.0, 50.0
    )
    weights = 1.0 / (
        alpha_center + (1.0 - alpha_center) * np.exp(log_ratio)
    )
    return z, weights, n_center


def _doran_projection(
    delta_res: np.ndarray,
    ca: np.ndarray,
    fa: np.ndarray,
    eps_sfc: float = 4.5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    discrepancy = delta_res - ca - fa
    excess = np.sign(discrepancy) * np.maximum(
        np.abs(discrepancy) - eps_sfc, 0.0
    )
    weights = np.array([0.4, 0.3, 0.3], dtype=float)
    delta_res = delta_res - weights[0] * excess
    ca = ca + weights[1] * excess
    fa = fa + weights[2] * excess
    return delta_res, ca, fa


def _policy_step(
    *,
    y_prev: np.ndarray,
    calendar: Dict[str, int],
    shock: np.ndarray,
    reserves_min: float,
    archetype: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    i_ib = y_prev[:, calendar["ib"]]
    fx = y_prev[:, calendar["fx"]]
    reserves = y_prev[:, calendar["reserves"]]
    sofr = np.full_like(i_ib, 5.0)
    anchor_ib = 4.5
    anchor_fx = 1.5
    if archetype == "FREE_FLOATING_RESERVE":
        bills = branchless_clamp(1.2 * branchless_pos(sofr - i_ib), 0.0, 30.0)
        omo = branchless_clamp(5.0 * branchless_pos(i_ib - 5.0), 0.0, 60.0)
        fx_sold = np.zeros_like(i_ib)
        delta_res = np.zeros_like(i_ib)
    elif archetype == "HARD_PEG_CURRENCY_BOARD":
        bills = np.zeros_like(i_ib)
        fx_breach = branchless_pos(fx - 0.1)
        avail = branchless_pos(reserves - 0.5)
        fx_sold = np.minimum(2.5 * fx_breach, avail)
        delta_res = -fx_sold
        omo = np.zeros_like(i_ib)
    else:
        fx_breach = branchless_pos(fx - anchor_fx - 0.8)
        bills = branchless_clamp(
            1.0 * branchless_pos(sofr - i_ib) + 2.0 * fx_breach,
            0.0,
            30.0,
        )
        avail = branchless_pos(reserves - reserves_min - 0.5)
        fx_sold = np.minimum(
            1.5 * fx_breach
            + 0.5 * branchless_pos(shock[:, min(0, shock.shape[1] - 1)]),
            avail,
        )
        delta_res = -fx_sold
        omo = branchless_clamp(
            8.0 * branchless_pos(i_ib - anchor_ib + 0.3)
            + 10.0 * branchless_pos(shock[:, min(1, shock.shape[1] - 1)])
            + 8.0 * branchless_pos(shock[:, min(2, shock.shape[1] - 1)]),
            0.0,
            50.0,
        )
    drain = np.maximum(shock[:, min(2, shock.shape[1] - 1)], -5.0) * 10.0
    i_next = branchless_clamp(
        i_ib
        - 0.35 * (i_ib - anchor_ib)
        + 0.01 * bills
        - 0.04 * omo
        - 0.06 * np.abs(delta_res)
        + 0.01 * drain,
        1.0,
        25.0,
    )
    u = np.column_stack([bills, delta_res, omo]).astype(float)
    return u, i_next, delta_res


def run_counterfactual_simulation(
    calib: CalibrationBundle,
    *,
    initial_state: np.ndarray,
    adversarial: Dict[str, Any],
    n_particles: int = 8192,
    horizon: int = 4,
    seed: int = 20261026,
    shock_dimension: int = 4,
    reserves_min: float = 10.0,
    sfc_tolerance: float = 4.5,
    archetype: str = "EMDE_IPF_CORRIDOR",
    mu_twist: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    n_particles = int(n_particles)
    horizon = int(horizon)
    r = calib.n_factors
    dim = calib.state_dim
    n_series = calib.n_series
    shock_dimension = int(min(max(3, shock_dimension), r))
    n_factors = calib.n_factors

    z, raw_weights, n_center = defensive_mixture_shocks(
        n_particles=n_particles,
        horizon=horizon,
        n_factors=n_factors,
        seed=seed,
        mu_twist=mu_twist,
    )
    rng = np.random.default_rng(seed + 17)
    regime_uniform = rng.random((n_particles, horizon))
    elite_physical = np.asarray(adversarial.get("elite_physical", []), dtype=float)
    narrative_weights = np.asarray(
        adversarial.get("narrative_weights", []), dtype=float
    )
    if elite_physical.size == 0:
        elite_physical = np.zeros((1, shock_dimension))
        narrative_weights = np.ones(1)
    shock_indices = allocate_shock_blocks(
        narrative_weights, n_particles, elite_physical.shape[0]
    )
    shock_particle = elite_physical[shock_indices, :shock_dimension]

    calendar = {
        "gdp": calib.core_index.get("GDP_YOY", 0),
        "cpi": calib.core_index.get("CPI_YOY", 1),
        "fx": calib.core_index.get("FX_DEPREC", 2),
        "ib": calib.core_index.get("IB_RATE", 3),
        "reserves": calib.core_index.get("FX_RESERVES", 4),
        "npl": calib.core_index.get("NPL_RATIO", 5),
        "credit": calib.core_index.get("CREDIT_GROWTH", 6),
        "ca": calib.core_index.get("CURRENT_ACCOUNT", 7),
        "fa": calib.core_index.get("FINANCIAL_ACCOUNT", 8),
    }
    nowcast = calib.nowcast_observables()
    initial_reserves = float(nowcast[calendar["reserves"]])
    if not np.isfinite(initial_reserves) or initial_reserves <= 0:
        initial_reserves = 88.5
    initial_reserves = max(initial_reserves, reserves_min)

    branch_paths: Dict[str, np.ndarray] = {}
    branch_factors: Dict[str, np.ndarray] = {}
    branch_regimes: Dict[str, np.ndarray] = {}
    branch_u: Dict[str, np.ndarray] = {}

    for branch in ("baseline", "passive", "active"):
        state = np.repeat(
            np.asarray(initial_state, dtype=float)[None, :], n_particles, axis=0
        )
        regimes = np.zeros(n_particles, dtype=np.int8)
        y_prev = np.repeat(nowcast[None, :], n_particles, axis=0)
        reserves_prev = np.full(n_particles, initial_reserves, dtype=float)
        y_path = np.empty((n_particles, horizon, n_series), dtype=float)
        f_path = np.empty((n_particles, horizon, r), dtype=float)
        regime_path = np.empty((n_particles, horizon), dtype=np.int8)
        u_path = np.zeros((n_particles, horizon, 3), dtype=float)

        for h in range(horizon):
            transition = calib.p_trans[regimes, 0]
            regimes = (regime_uniform[:, h] >= transition).astype(np.int8)
            a = (
                np.broadcast_to(calib.a_comp, (n_particles,) + calib.a_comp.shape)
                if calib.a_comp.ndim == 2
                else calib.a_comp[regimes]
            )
            state = np.einsum("pij,pj->pi", a, state)
            innovation = np.einsum(
                "pij,pj->pi", calib.p_chol[regimes], z[:, h, :]
            )
            state[:, :r] += innovation

            if branch == "baseline":
                shock_h = np.zeros((n_particles, shock_dimension), dtype=float)
                policy = np.zeros((n_particles, 3), dtype=float)
                i_next = y_prev[:, calendar["ib"]]
                delta_res_policy = np.zeros(n_particles, dtype=float)
            else:
                decay = np.power(calib.d_rho[:shock_dimension], h + 1)
                shock_h = shock_particle * decay[None, :]
                if branch == "active":
                    policy, i_next, delta_res_policy = _policy_step(
                        y_prev=y_prev,
                        calendar=calendar,
                        shock=shock_particle,
                        reserves_min=reserves_min,
                        archetype=archetype,
                    )
                else:
                    policy = np.zeros((n_particles, 3), dtype=float)
                    i_next = branchless_clamp(
                        y_prev[:, calendar["ib"]]
                        - 0.35 * (y_prev[:, calendar["ib"]] - 4.5),
                        1.0,
                        25.0,
                    )
                    delta_res_policy = np.zeros(n_particles, dtype=float)

            state[:, :r] += np.einsum(
                "ij,pj->pi", calib.b_eps, shock_h
            )
            state[:, :r] += np.einsum("ij,pj->pi", calib.b_u, policy)
            y = calib.y_mean + calib.y_std * np.einsum(
                "ij,pj->pi", calib.lambda_tilde, state
            )

            # Stock-flow integration is applied to total reserve variation
            # (factor-driven plus active intervention) before clipping at the
            # absorbing floor.  See v5.7 Patch #10.
            delta_res_endo = y[:, calendar["reserves"]] - reserves_prev
            total_delta = delta_res_endo + delta_res_policy
            ca_raw = y[:, calendar["ca"]]
            fa_raw = y[:, calendar["fa"]]
            total_delta, ca_sfc, fa_sfc = _doran_projection(
                total_delta, ca_raw, fa_raw, eps_sfc=sfc_tolerance
            )
            unclipped = reserves_prev + total_delta
            new_reserves = np.maximum(reserves_min, unclipped)
            actual_delta = new_reserves - reserves_prev
            clip_diff = actual_delta - total_delta
            y[:, calendar["reserves"]] = new_reserves
            y[:, calendar["ca"]] = ca_sfc + 0.5 * clip_diff
            y[:, calendar["fa"]] = fa_sfc + 0.5 * clip_diff
            y[:, calendar["ib"]] = i_next
            if branch == "passive":
                shortfall = np.maximum(0.0, reserves_min - unclipped)
                y[:, calendar["fx"]] += 0.45 * shortfall
            reserves_prev = new_reserves

            y_path[:, h, :] = y
            f_path[:, h, :] = state[:, :r]
            regime_path[:, h] = regimes
            u_path[:, h, :] = policy
            y_prev = y

        branch_paths[branch] = y_path
        branch_factors[branch] = f_path
        branch_regimes[branch] = regime_path
        branch_u[branch] = u_path

    return {
        "branch_paths": branch_paths,
        "branch_factors": branch_factors,
        "branch_regimes": branch_regimes,
        "branch_policy": branch_u,
        "weights_raw": raw_weights,
        "n_center": n_center,
        "n_tail": n_particles - n_center,
        "alpha_center": 0.5,
        "shock_indices": shock_indices,
        "shock_particle": shock_particle,
        "calendar": calendar,
        "initial_reserves": float(initial_reserves),
        "sobol_dimension": int(n_factors * horizon),
        "sobol_particles": n_particles,
        "sfc_tolerance": float(sfc_tolerance),
        "max_raw_weight": float(np.max(raw_weights)),
        "prior_kish_ess": float(
            np.sum(raw_weights) ** 2 / np.sum(raw_weights**2)
        ),
    }


__all__ = [
    "allocate_shock_blocks",
    "branchless_clamp",
    "branchless_pos",
    "defensive_mixture_shocks",
    "run_counterfactual_simulation",
]
