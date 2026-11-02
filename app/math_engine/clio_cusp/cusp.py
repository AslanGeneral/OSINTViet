"""Array-mRQMC cusp backend with a NumPy/SciPy reference implementation.

The locked production kernel in ``clio_cusp_8192_v4_kernel.py`` is PyTorch
first.  This module implements the identical equations with NumPy/SciPy so the
orchestrator also runs in environments where torch is not installed.  The
Hilbert bijection and rank permutation are imported from the locked kernel, so
there is only one implementation of the spatial coupling.

Backend selection is explicit in the artifact: ``numpy`` for this module and
``torch`` when the caller supplies torch tensors to the locked kernel.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
from scipy.special import expit, ndtr, ndtri
from scipy.stats import qmc

from app.math_engine.clio_cusp.kernel import (
    M_PARTICLES,
    R_REPLICATES,
    U_CLIP_EPS,
    compute_true_2d_hilbert_rank_permutation,
)


def phi1_numpy(z: np.ndarray) -> np.ndarray:
    """Stable phi_1(z) = (exp(z)-1)/z, including z near zero."""
    z = np.asarray(z, dtype=np.float64)
    small = np.abs(z) < 1e-4
    taylor = 1.0 + z * (0.5 + z * (1.0 / 6.0 + z * (1.0 / 24.0)))
    safe = np.where(small, 1.0, z)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        exact = np.expm1(safe) / safe
    return np.where(small, taylor, exact)


def cusp_barrier_and_crit(a_t: np.ndarray, b_t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Viète-Cardano unstable barrier and fold threshold."""
    a_t = np.asarray(a_t, dtype=np.float64)
    b_t = np.asarray(b_t, dtype=np.float64)
    b_pos = np.clip(b_t, 1e-7, None)
    a_crit = np.sqrt((4.0 / 27.0) * b_pos**3)
    ratio = np.clip(a_t / a_crit, -1.0, 1.0)
    x_saddle = -2.0 * np.sqrt(b_pos / 3.0) * np.sin(np.arcsin(ratio) / 3.0)
    barrier = np.where(b_t > 0.0, x_saddle, 0.0)
    return barrier, a_crit


def draw_sobol_nets_numpy(
    step_seed: int,
    R: int = R_REPLICATES,
    M: int = M_PARTICLES,
) -> tuple[np.ndarray, np.ndarray]:
    """Draw R scrambled (0, 13, 2)-nets with SciPy's Sobol engine.

    SciPy's ``scramble=True`` performs a linear matrix scrambling, not Owen
    scrambling.  The locked PyTorch path remains the Owen-scrambled reference;
    this path is deterministic, low-discrepancy and dependency-light.
    """
    if R != R_REPLICATES or M != M_PARTICLES:
        raise ValueError("production dimensions are locked to R=9 and M=8192")
    v_rows: list[np.ndarray] = []
    u_rows: list[np.ndarray] = []
    for replicate in range(R):
        seed = (int(step_seed) * 1009 + replicate * 9176 + 17) % (2**31 - 1)
        engine = qmc.Sobol(d=2, scramble=True, seed=seed)
        points = engine.random(M)
        v_rows.append(points[:, 0])
        u_rows.append(points[:, 1])
    return np.stack(v_rows, axis=0), np.stack(u_rows, axis=0)


def draw_sobol_nets_torch(step_seed: int) -> tuple[Any, Any]:
    """Delegate to the locked Owen-scrambled torch generator."""
    from app.math_engine.clio_cusp.kernel import draw_owen_scrambled_0_13_2_nets

    return draw_owen_scrambled_0_13_2_nets(step_seed)


def numpy_cusp_step(
    X_t: np.ndarray,
    a_t: np.ndarray,
    b_t: np.ndarray,
    Sigma_t: np.ndarray,
    v_sort_sobol: np.ndarray,
    u_step_sobol: np.ndarray,
    sigma_a: float = 0.02,
    sigma_b: float = 0.02,
    dt: float = 1.0,
) -> dict[str, Any]:
    """Advance the [9, 8192] particle array with the Section 8 equations."""
    arrays = (X_t, a_t, b_t, Sigma_t, v_sort_sobol, u_step_sobol)
    if any(np.asarray(t).shape != (R_REPLICATES, M_PARTICLES) for t in arrays):
        raise ValueError("all particle and Sobol tensors must have shape [9, 8192]")
    if dt <= 0 or sigma_a < 0 or sigma_b < 0:
        raise ValueError("dt must be positive and forecast standard deviations nonnegative")

    X_t = np.asarray(X_t, dtype=np.float64)
    a_t = np.asarray(a_t, dtype=np.float64)
    b_t = np.asarray(b_t, dtype=np.float64)
    Sigma_t = np.asarray(Sigma_t, dtype=np.float64)

    hilbert_rank = compute_true_2d_hilbert_rank_permutation(X_t, b_t)
    v_order = np.argsort(v_sort_sobol, axis=-1)
    u_stratified = np.take_along_axis(u_step_sobol, v_order, axis=-1)
    u_particle = np.take_along_axis(u_stratified, hilbert_rank, axis=-1)
    u_safe = np.clip(u_particle, U_CLIP_EPS, 1.0 - U_CLIP_EPS)
    dW = ndtri(u_safe) * math.sqrt(dt)

    z = -2.0 * b_t * dt
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        x_tilde = X_t / np.sqrt(np.exp(z) + 2.0 * dt * phi1_numpy(z) * X_t**2)
    x_tilde = np.nan_to_num(x_tilde, nan=0.0, posinf=0.0, neginf=0.0)

    drift_target = x_tilde + a_t * dt
    diffusion_std = np.clip(Sigma_t * math.sqrt(dt), 1e-7, None)
    barrier, a_crit = cusp_barrier_and_crit(a_t, b_t)

    p_kramers = ndtr((drift_target - barrier) / diffusion_std)
    p_fold = ndtr((a_t - a_crit) / max(sigma_a, 1e-4)) * ndtr(
        b_t / max(sigma_b, 1e-4)
    )
    p_collapse = np.clip(p_kramers + p_fold - p_kramers * p_fold, 0.0, 1.0)

    sigma_delta = np.sqrt(
        (54.0 * a_t * sigma_a) ** 2
        + (12.0 * b_t**2 * sigma_b) ** 2
        + 1e-6
    )
    p_hysteresis = np.clip(
        ndtr((4.0 * b_t**3 - 27.0 * a_t**2) / sigma_delta)
        * ndtr(b_t / max(sigma_b, 1e-4)),
        0.0,
        1.0,
    )

    X_next = drift_target + Sigma_t * dW
    S_next = expit(-X_next)

    collapse_replicate = p_collapse.mean(axis=-1)
    hysteresis_replicate = p_hysteresis.mean(axis=-1)
    stability_replicate = S_next.mean(axis=-1)

    return {
        "X_next": X_next,
        "S_next": S_next,
        "S_next_mRQMC": float(np.median(stability_replicate)),
        "P_hysteresis_mRQMC": float(np.median(hysteresis_replicate)),
        "P_collapse_mRQMC": float(np.median(collapse_replicate)),
        "Volatility_mean": float(np.mean(Sigma_t)),
        "X_mean": float(np.median(X_next.mean(axis=-1))),
        "X_q05": float(np.quantile(X_next, 0.05)),
        "X_q50": float(np.quantile(X_next, 0.50)),
        "X_q95": float(np.quantile(X_next, 0.95)),
    }


def torch_cusp_step(
    X_t: Any,
    a_t: Any,
    b_t: Any,
    Sigma_t: Any,
    v_sort_sobol: Any,
    u_step_sobol: Any,
    sigma_a: float = 0.02,
    sigma_b: float = 0.02,
    dt: float = 1.0,
) -> dict[str, Any]:
    """Run the locked PyTorch kernel and return float scalars plus tensors."""
    from app.math_engine.clio_cusp.kernel import clio_cusp_v4_array_mrqmc_step

    result = clio_cusp_v4_array_mrqmc_step(
        X_t,
        a_t,
        b_t,
        Sigma_t,
        v_sort_sobol,
        u_step_sobol,
        sigma_a=sigma_a,
        sigma_b=sigma_b,
        dt=dt,
    )
    return {
        **result,
        "S_next_mRQMC": float(result["S_next_mRQMC"].detach().cpu()),
        "P_hysteresis_mRQMC": float(result["P_hysteresis_mRQMC"].detach().cpu()),
        "P_collapse_mRQMC": float(result["P_collapse_mRQMC"].detach().cpu()),
        "Volatility_mean": float(result["Volatility_mean"].detach().cpu()),
    }


def initial_particles(
    x0: float = 0.0,
    spread: float = 0.10,
    R: int = R_REPLICATES,
    M: int = M_PARTICLES,
) -> np.ndarray:
    """Deterministic stratified initial condition for all replicates."""
    if R != R_REPLICATES or M != M_PARTICLES:
        raise ValueError("production dimensions are locked to R=9 and M=8192")
    quantiles = (np.arange(M, dtype=np.float64) + 0.5) / M
    base = x0 + spread * ndtri(quantiles)
    return np.tile(base, (R, 1))


def control_particles(
    a_base: float,
    b_base: float,
    sigma_a: float,
    sigma_b: float,
    seed: int,
    day: int,
    R: int = R_REPLICATES,
    M: int = M_PARTICLES,
) -> tuple[np.ndarray, np.ndarray]:
    """Map forecast control uncertainty into per-particle a_t and b_t.

    TimesFM supplies ``sigma_a``/``sigma_b`` as forecast quantile spreads.  The
    specification treats a_t and b_t as arrays over the same particle grid, so
    we draw independent standard-normal shocks with a seed that is a pure
    function of the request seed and the day.  This keeps reruns bitwise
    reproducible without consuming the Sobol stream used for dW.
    """
    if R != R_REPLICATES or M != M_PARTICLES:
        raise ValueError("production dimensions are locked to R=9 and M=8192")
    generator = np.random.default_rng(
        (int(seed) * 1_000_003 + int(day) * 7_919 + 104_729) % (2**63 - 1)
    )
    if sigma_a > 0:
        a_t = a_base + sigma_a * generator.standard_normal((R, M))
    else:
        a_t = np.full((R, M), a_base, dtype=np.float64)
    if sigma_b > 0:
        b_t = b_base + sigma_b * generator.standard_normal((R, M))
    else:
        b_t = np.full((R, M), b_base, dtype=np.float64)
    return a_t, b_t


def check_backend_available(backend: str) -> bool:
    if backend == "numpy":
        return True
    if backend == "torch":
        try:
            import torch  # noqa: F401
        except ImportError:
            return False
        return True
    if backend == "auto":
        return True
    raise ValueError(f"unknown backend {backend}")


def resolve_backend(backend: str) -> str:
    if backend == "auto":
        return "torch" if check_backend_available("torch") else "numpy"
    if not check_backend_available(backend):
        raise RuntimeError(f"backend {backend!r} is not installed")
    return backend


__all__ = [
    "check_backend_available",
    "control_particles",
    "cusp_barrier_and_crit",
    "draw_sobol_nets_numpy",
    "draw_sobol_nets_torch",
    "initial_particles",
    "numpy_cusp_step",
    "phi1_numpy",
    "resolve_backend",
    "torch_cusp_step",
]
