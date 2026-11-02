"""Layer 1: Rao-Blackwellized particle filter and RTS smoother.

The conditional-linear part of the v5.7 state space is a 5-lag companion
factor state.  Each particle carries a full Gaussian state and covariance;
regime switching is sampled, while the linear Kalman update is exact for each
particle.  The backward RTS pass uses the particle's realized regime path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle


def _select_transition(a: np.ndarray, regimes: np.ndarray) -> np.ndarray:
    """Return per-particle (P, dim, dim) transitions for 2D or 3D A."""
    a = np.asarray(a, dtype=float)
    if a.ndim == 2:
        return np.broadcast_to(a, (int(regimes.size),) + a.shape)
    return a[regimes]


def _regime_covariances(calib: CalibrationBundle) -> np.ndarray:
    dim = calib.state_dim
    r = calib.n_factors
    q = np.zeros((2, dim, dim), dtype=float)
    for s in (0, 1):
        q[s, :r, :r] = calib.omega_regimes[s]
        q[s] += np.eye(dim) * 1e-7
    return q


def run_rbpf_rts(
    calib: CalibrationBundle,
    *,
    n_particles: int = 256,
    seed: int = 20261026,
    jump_probability: float = 0.02,
) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    y_std = calib.x_std
    t_count, n_series = y_std.shape
    dim = calib.state_dim
    r = calib.n_factors
    n_particles = max(16, int(n_particles))
    q = _regime_covariances(calib)

    # Forward filter storage.  Resampling is performed after each measurement
    # update; the stored arrays therefore represent the current particle set.
    x_filt = np.zeros((n_particles, t_count, dim), dtype=float)
    p_filt = np.zeros((n_particles, t_count, dim, dim), dtype=float)
    x_pred_hist = np.zeros_like(x_filt)
    p_pred_hist = np.zeros_like(p_filt)
    regimes_hist = np.zeros((n_particles, t_count), dtype=np.int8)
    weights_hist = np.zeros((n_particles, t_count), dtype=float)

    x = np.zeros((n_particles, dim), dtype=float)
    p = np.repeat((np.eye(dim) * 0.6)[None, :, :], n_particles, axis=0)
    regimes = rng.integers(0, 2, size=n_particles, dtype=np.int8)
    weights = np.full(n_particles, 1.0 / n_particles, dtype=float)
    log_likelihood = 0.0
    ess_values: list[float] = []

    for t in range(t_count):
        # Regime transition, shared across the particle's linear state only
        # through the realized discrete draw.
        transition = calib.p_trans[regimes, 1]
        regimes = (rng.random(n_particles) < transition).astype(np.int8)

        a = _select_transition(calib.a_comp, regimes)  # (P, dim, dim)
        x_pred = np.einsum("pij,pj->pi", a, x)
        p_pred = np.einsum("pij,pjk,plk->pil", a, p, a) + q[regimes]
        p_pred = 0.5 * (p_pred + p_pred.transpose(0, 2, 1))

        # Bernoulli-Gaussian jumps in the latent factor block.
        jump_mask = rng.random((n_particles, r)) < jump_probability
        if np.any(jump_mask):
            jump_scale = np.sqrt(np.maximum(np.diag(calib.omega), 1e-8))
            jump = jump_mask * rng.normal(
                loc=0.0, scale=jump_scale[None, :] * 1.5, size=(n_particles, r)
            )
            x_pred[:, :r] += jump

        observed = np.isfinite(y_std[t])
        if not np.any(observed):
            x = x_pred
            p = p_pred
            x_pred_hist[:, t] = x_pred
            p_pred_hist[:, t] = p_pred
            x_filt[:, t] = x
            p_filt[:, t] = p
            regimes_hist[:, t] = regimes
            weights_hist[:, t] = weights
            continue

        h = calib.lambda_tilde[observed]  # (N_t, dim)
        r_diag = np.maximum(calib.r_diag[observed], 1e-8)
        residual = y_std[t, observed][None, :] - x_pred @ h.T
        s = np.einsum("ni,pij,mj->pnm", h, p_pred, h) + np.diag(r_diag)[None, :, :]
        s = 0.5 * (s + s.transpose(0, 2, 1))
        s += np.eye(s.shape[1])[None, :, :] * 1e-9

        try:
            s_inv_res = np.linalg.solve(s, residual[..., None])[..., 0]
            log_det = np.linalg.slogdet(s)[1]
            k = np.linalg.solve(s, np.einsum("pij,nj->pni", p_pred, h))
            k = k.transpose(0, 2, 1)  # (P, dim, N_t)
        except np.linalg.LinAlgError:
            # Extremely defensive fallback: use a diagonal innovation
            # covariance rather than corrupting the whole particle bank.
            s_diag = np.maximum(np.diagonal(s, axis1=1, axis2=2), 1e-8)
            s_inv_res = residual / s_diag
            log_det = np.log(s_diag).sum(axis=1)
            k = np.einsum("pij,pnj->pdi", p_pred, h) / s_diag[:, None, :]

        log_weights = (
            -0.5
            * (
                observed.sum() * np.log(2.0 * np.pi)
                + log_det
                + np.einsum("pn,pn->p", residual, s_inv_res)
            )
        )
        log_weights -= np.max(log_weights)
        weights = weights * np.exp(log_weights)
        weights = np.maximum(weights, 1e-300)
        weights /= np.sum(weights)
        log_likelihood += float(
            np.log(np.sum(np.exp(log_weights - np.max(log_weights))))
            + np.max(log_weights)
        )

        x = x_pred + np.einsum("pdn,pn->pd", k, residual)
        identity = np.eye(dim)[None, :, :]
        i_kh = identity - np.einsum("pdn,nm->pdm", k, h)
        p = (
            np.einsum("pij,pjk,plk->pil", i_kh, p_pred, i_kh)
            + np.einsum("pdn,n,pem->pde", k, r_diag, k)
        )
        p = 0.5 * (p + p.transpose(0, 2, 1))

        x_pred_hist[:, t] = x_pred
        p_pred_hist[:, t] = p_pred
        x_filt[:, t] = x
        p_filt[:, t] = p
        regimes_hist[:, t] = regimes
        weights_hist[:, t] = weights

        ess = float(1.0 / np.sum(weights**2))
        ess_values.append(ess)
        if ess < 0.5 * n_particles:
            positions = rng.choice(
                n_particles, size=n_particles, replace=True, p=weights
            )
            x = x[positions]
            p = p[positions]
            regimes = regimes[positions]
            weights = np.full(n_particles, 1.0 / n_particles, dtype=float)
            # The stored filtered set now refers to the resampled particles.
            x_filt[:, t] = x
            p_filt[:, t] = p
            regimes_hist[:, t] = regimes

    # RTS backward smoother for each particle path.  The transition matrix is
    # the realized regime at t+1 (Patch #2b / 3D transition).
    x_smooth = x_filt.copy()
    p_smooth = p_filt.copy()
    for t in range(t_count - 2, -1, -1):
        a_next = _select_transition(calib.a_comp, regimes_hist[:, t + 1])
        p_next_pred = p_pred_hist[:, t + 1]
        b = np.einsum("pij,pkj->pik", a_next, p_filt[:, t])
        try:
            gain = np.linalg.solve(p_next_pred, b).transpose(0, 2, 1)
        except np.linalg.LinAlgError:
            gain = np.zeros_like(b).transpose(0, 2, 1)
        diff = x_smooth[:, t + 1] - x_pred_hist[:, t + 1]
        x_smooth[:, t] = x_filt[:, t] + np.einsum("pij,pj->pi", gain, diff)
        p_smooth[:, t] = p_filt[:, t] + np.einsum(
            "pij,pjk,plk->pil",
            gain,
            p_smooth[:, t + 1] - p_next_pred,
            gain,
        )
        p_smooth[:, t] = 0.5 * (
            p_smooth[:, t] + p_smooth[:, t].transpose(0, 2, 1)
        )

    final_weights = weights_hist[:, -1]
    final_weights = np.maximum(final_weights, 1e-300)
    final_weights /= np.sum(final_weights)
    smoothed_mean = np.einsum("p,pi->i", final_weights, x_smooth[:, -1])
    smoothed_cov = np.einsum(
        "p,pi,pj->ij",
        final_weights,
        x_smooth[:, -1] - smoothed_mean,
        x_smooth[:, -1] - smoothed_mean,
    )
    nowcast_standardized = calib.lambda_tilde @ smoothed_mean
    nowcast_original = calib.y_mean + calib.y_std * nowcast_standardized
    factor_path = x_smooth[:, :, :r]
    factor_mean_path = np.einsum("pt,pfi->tfi", weights_hist, factor_path)

    return {
        "n_particles": n_particles,
        "state_dim": dim,
        "filter_mode": "rbpf_conditional_gaussian_rts",
        "log_likelihood": round(log_likelihood, 6),
        "mean_ess": round(float(np.mean(ess_values)) if ess_values else n_particles, 3),
        "min_ess": round(float(np.min(ess_values)) if ess_values else n_particles, 3),
        "smoothed_state": smoothed_mean.tolist(),
        "smoothed_covariance": smoothed_cov.tolist(),
        "smoothed_factor_path": factor_mean_path.tolist(),
        "nowcast_standardized": nowcast_standardized.tolist(),
        "nowcast_original": nowcast_original.tolist(),
        "final_regime_probability_stress": round(
            float(np.mean(regimes_hist[:, -1] == 1)), 6
        ),
    }


__all__ = ["run_rbpf_rts"]
