"""Layer 0/0.5 calibration for the v5.7 macro-financial engine.

This module implements the estimation kernel that the online layers consume:
  * ragged-edge panel preparation, robust standardization and density imputation;
  * PCA factor extraction (5 factors by default);
  * VAR(5) companion form with the Mariano-Murasawa lag state;
  * two-regime Markov switching covariance and Hamilton probabilities;
  * GPD marginal tails with the C0-continuous threshold convention;
  * Student-t copula rank correlation;
  * Haar orthogonal rotation banks in O(r);
  * deterministic shock/transmission and policy matrices.

The implementation is intentionally explicit about its approximations.  The
artifact records ``estimation_backend`` and ``imputation_policy`` so a later
layer-0 production EM fit can be swapped in without changing the JSON contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats
from scipy.stats import kendalltau, norm


CORE_CODES = (
    "GDP_YOY",
    "CPI_YOY",
    "FX_DEPREC",
    "IB_RATE",
    "FX_RESERVES",
    "NPL_RATIO",
    "CREDIT_GROWTH",
    "CURRENT_ACCOUNT",
    "FINANCIAL_ACCOUNT",
)

CORE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "GDP_YOY": ("GDP_YOY", "NY.GDP.MKTP.KD.ZG"),
    "CPI_YOY": ("CPI_YOY", "FP.CPI.TOTL.ZG"),
    "FX_DEPREC": ("FX_DEPREC", "PA.NUS.FCRF", "PX.REX.REER"),
    "IB_RATE": ("IB_RATE", "FR.INR.LEND", "FR.INR.RINR"),
    "FX_RESERVES": ("FX_RESERVES", "FI.RES.TOTL.CD", "FI.RES.TOTL.MO"),
    "NPL_RATIO": ("NPL_RATIO", "FB.AST.NPER.ZS"),
    "CREDIT_GROWTH": ("CREDIT_GROWTH", "FS.AST.PRVT.GD.ZS"),
    "CURRENT_ACCOUNT": ("CURRENT_ACCOUNT", "BN.CAB.XOKA.GD.ZS"),
    "FINANCIAL_ACCOUNT": ("FINANCIAL_ACCOUNT", "BN.FIN.TOTL.CD"),
}

TAIL_SIGNS = {
    "GDP_YOY": -1,
    "CPI_YOY": 1,
    "FX_DEPREC": 1,
    "IB_RATE": 1,
    "FX_RESERVES": -1,
    "NPL_RATIO": 1,
    "CREDIT_GROWTH": -1,
    "CURRENT_ACCOUNT": 1,
    "FINANCIAL_ACCOUNT": 1,
}


@dataclass
class GpdMarginal:
    """Semi-parametric ECDF + GPD marginal with C0 threshold continuity."""

    threshold: float
    n_excess: int
    n_observations: int
    xi: float
    beta: float
    ecdf_values: np.ndarray
    tail_probability: float

    def cdf(self, x: np.ndarray | float) -> np.ndarray:
        values = np.asarray(x, dtype=float)
        flat = values.reshape(-1)
        rank = np.searchsorted(self.ecdf_values, flat, side="right")
        ecdf = rank.astype(float) / float(self.n_observations + 1)
        tail = np.ones_like(flat, dtype=float)
        mask = flat > self.threshold
        if self.n_excess and np.any(mask):
            z = np.maximum(
                1.0 + self.xi * (flat[mask] - self.threshold) / self.beta,
                1e-12,
            )
            tail[mask] = 1.0 - self.tail_probability * np.power(z, -1.0 / self.xi)
        result = np.where(mask, tail, ecdf)
        return np.clip(result, 1e-12, 1.0 - 1e-12).reshape(values.shape)

    def ppf(self, u: np.ndarray | float) -> np.ndarray:
        values = np.asarray(u, dtype=float)
        flat = np.clip(values.reshape(-1), 1e-12, 1.0 - 1e-12)
        upper = 1.0 - self.tail_probability
        out = np.empty_like(flat)
        mask = flat <= upper
        if np.any(mask):
            quantile = np.clip(flat[mask], 0.0, 1.0)
            # The ECDF inverse is linear between observed order statistics;
            # this is the standard plotting-position interpolation.
            out[mask] = np.quantile(self.ecdf_values, quantile)
        if np.any(~mask):
            if self.n_excess == 0 or self.xi <= 0:
                out[~mask] = np.max(self.ecdf_values)
            else:
                z = (1.0 - flat[~mask]) / self.tail_probability
                out[~mask] = self.threshold + (self.beta / self.xi) * (
                    np.power(np.maximum(z, 1e-12), -self.xi) - 1.0
                )
        return out.reshape(values.shape)


@dataclass
class CalibrationBundle:
    codes: List[str]
    names_vi: List[str]
    units: List[str]
    dates: List[str]
    y: np.ndarray
    y_mean: np.ndarray
    y_std: np.ndarray
    x_std: np.ndarray
    missing_mask: np.ndarray
    factors: np.ndarray
    loadings: np.ndarray
    a_lags: List[np.ndarray]
    a_comp: np.ndarray
    omega: np.ndarray
    omega_regimes: np.ndarray
    p_chol: np.ndarray
    p_trans: np.ndarray
    regime_probs: np.ndarray
    regimes: np.ndarray
    residuals: np.ndarray
    r_diag: np.ndarray
    lambda_tilde: np.ndarray
    w_proj: np.ndarray
    b_eps: np.ndarray
    b_u: np.ndarray
    d_rho: np.ndarray
    gpd_marginals: List[GpdMarginal]
    r_c: np.ndarray
    haar_bank: np.ndarray
    last_state: np.ndarray
    last_factor: np.ndarray
    core_index: Dict[str, int]
    tail_signs: Dict[str, int]
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_series(self) -> int:
        return int(self.y.shape[1])

    @property
    def n_dates(self) -> int:
        return int(self.y.shape[0])

    @property
    def n_factors(self) -> int:
        return int(self.loadings.shape[1])

    @property
    def state_dim(self) -> int:
        return int(self.a_comp.shape[0])

    def index_of(self, code: str) -> int:
        if code in self.core_index:
            return int(self.core_index[code])
        return int(self.codes.index(code)) if code in self.codes else 0

    def original_from_state(self, state: np.ndarray) -> np.ndarray:
        """Project a companion state to the panel's original units."""
        state = np.asarray(state, dtype=float)
        standardized = state @ self.lambda_tilde.T
        return self.y_mean + self.y_std * standardized

    def nowcast_observables(self) -> np.ndarray:
        return self.original_from_state(self.last_state)


def _impute_and_standardize(
    values: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return y, missing mask, robust mean/std and standardized imputed matrix.

    A column with fewer than 20% observed values is still retained: it is
    imputed with its observed median so the factor model can use the rest of
    the panel without silently dropping an entire macro block.
    """
    y = np.asarray(values, dtype=float)
    missing = ~np.isfinite(y)
    observed_counts = (~missing).sum(axis=0)
    if np.any(observed_counts == 0):
        raise ValueError("every panel series must have at least one observation")
    filled = y.copy()
    for j in range(filled.shape[1]):
        col = filled[:, j]
        valid = ~missing[:, j]
        if valid.all():
            continue
        if observed_counts[j] >= 3:
            # Linear interpolation inside the observed span, median outside.
            idx = np.arange(len(col))
            col[~valid] = np.interp(idx[~valid], idx[valid], col[valid])
            col[~valid & ((idx < np.min(idx[valid])) | (idx > np.max(idx[valid])))] = np.nanmedian(
                col[valid]
            )
        else:
            col[~valid] = np.nanmedian(col[valid])
        filled[:, j] = col
    mean = np.nanmean(filled, axis=0)
    std = np.nanstd(filled, axis=0, ddof=1)
    std = np.where(np.isfinite(std) & (std > 1e-10), std, 1.0)
    x_std = (filled - mean) / std
    return filled, missing, mean, std


def _fit_var(
    factors: np.ndarray, lags: int = 5, ridge: float = 1e-4
) -> Tuple[List[np.ndarray], np.ndarray, np.ndarray]:
    t, r = factors.shape
    if t <= lags + r + 2:
        lags = max(1, min(lags, max(1, t - 5)))
    rows = t - lags
    y = factors[lags:]
    x = np.concatenate(
        [factors[lags - lag : t - lag] for lag in range(1, lags + 1)], axis=1
    )
    # Ridge-stabilized OLS; the intercept is removed because factors are
    # centered by construction.
    gram = x.T @ x + ridge * np.eye(x.shape[1])
    beta = np.linalg.solve(gram, x.T @ y)
    resid = y - x @ beta
    a_lags = [beta[(lag - 1) * r : lag * r, :].T for lag in range(1, lags + 1)]
    return a_lags, resid, beta


def _companion(a_lags: List[np.ndarray], r: int, lags: int) -> np.ndarray:
    dim = r * lags
    a = np.zeros((dim, dim), dtype=float)
    for lag, coef in enumerate(a_lags):
        a[:r, lag * r : (lag + 1) * r] = coef
    for lag in range(1, lags):
        a[lag * r : (lag + 1) * r, (lag - 1) * r : lag * r] = np.eye(r)
    return a


def _regime_covariances(
    resid: np.ndarray,
    p_trans: np.ndarray,
    iterations: int = 8,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fit a two-regime covariance mixture with a Hamilton filter.

    Returns regimes (T,), smoothed probabilities (T,2), transition matrix and
    regime covariance stack (2,r,r).  It is a transparent approximation to the
    full Kim collapsing EM and is deterministic.
    """
    t, r = resid.shape
    vol = np.sqrt(np.maximum(np.sum(resid**2, axis=1), 1e-12))
    window = min(5, max(2, t // 10))
    rolling = np.convolve(vol, np.ones(window) / window, mode="same")
    threshold = float(np.median(rolling))
    regimes = (rolling > threshold).astype(int)
    if regimes.min() == regimes.max():
        regimes = (np.arange(t) >= t // 2).astype(int)
    p = p_trans.copy()
    probs = np.full((t, 2), 0.5, dtype=float)
    omegas = np.stack(
        [
            np.cov(resid[regimes == s].T) + np.eye(r) * 1e-5
            if int(np.sum(regimes == s)) > r + 2
            else np.cov(resid.T) + np.eye(r) * 1e-5
            for s in (0, 1)
        ]
    )
    for _ in range(iterations):
        # Estimate transition probabilities from the current hard regimes.
        counts = np.ones((2, 2), dtype=float)
        for prev, cur in zip(regimes[:-1], regimes[1:]):
            counts[int(prev), int(cur)] += 1.0
        p = counts / counts.sum(axis=1, keepdims=True)

        # Hamilton filter on the residual likelihood.
        probs = np.zeros((t, 2), dtype=float)
        prior = np.array([0.5, 0.5], dtype=float)
        for idx in range(t):
            if idx > 0:
                prior = p.T @ prior
            dens = np.array(
                [
                    stats.multivariate_normal.pdf(
                        resid[idx], mean=np.zeros(r), cov=omegas[s], allow_singular=True
                    )
                    for s in (0, 1)
                ]
            )
            unnorm = np.maximum(prior * np.maximum(dens, 1e-300), 1e-300)
            prior = unnorm / unnorm.sum()
            probs[idx] = prior
        new_regimes = np.argmax(probs, axis=1)
        omegas = np.stack(
            [
                (
                    np.cov(resid[new_regimes == s].T)
                    if int(np.sum(new_regimes == s)) > r + 2
                    else np.cov(resid.T)
                )
                + np.eye(r) * 1e-5
                for s in (0, 1)
            ]
        )
        regimes = new_regimes
    return regimes, probs, p, omegas


def _fit_gpd(values: np.ndarray, threshold_quantile: float = 0.90) -> GpdMarginal:
    clean = np.sort(np.asarray(values, dtype=float)[np.isfinite(values)])
    n = int(clean.size)
    if n < 12:
        # Degenerate fallback: a very light exponential tail.
        return GpdMarginal(
            threshold=float(clean[-1]) if n else 0.0,
            n_excess=0,
            n_observations=n,
            xi=0.1,
            beta=1.0,
            ecdf_values=clean,
            tail_probability=1.0 / (n + 1),
        )
    threshold = float(np.quantile(clean, threshold_quantile))
    excess = clean[clean > threshold] - threshold
    n_excess = int(excess.size)
    tail_probability = n_excess / float(n + 1)
    if n_excess >= 5:
        try:
            xi, _, beta = stats.genpareto.fit(excess, floc=0.0)
            xi = float(np.clip(xi, 1e-4, 0.95))
            beta = float(max(beta, 1e-6))
        except Exception:
            xi, beta = 0.1, float(max(np.std(excess), 1e-3))
    else:
        xi, beta = 0.1, float(max(np.std(excess) if n_excess else 1.0, 1e-3))
    return GpdMarginal(
        threshold=threshold,
        n_excess=n_excess,
        n_observations=n,
        xi=xi,
        beta=beta,
        ecdf_values=clean,
        tail_probability=tail_probability,
    )


def _copula_correlation(resid: np.ndarray) -> np.ndarray:
    d = resid.shape[1]
    corr = np.eye(d)
    for i in range(d):
        for j in range(i + 1, d):
            tau = float(kendalltau(resid[:, i], resid[:, j]).statistic)
            if not np.isfinite(tau):
                tau = 0.0
            value = float(np.sin(np.pi * tau / 2.0))
            corr[i, j] = corr[j, i] = value
    # Project to the nearest SPD correlation matrix.
    vals, vecs = np.linalg.eigh((corr + corr.T) / 2.0)
    vals = np.maximum(vals, 0.05)
    corr = vecs @ np.diag(vals) @ vecs.T
    diag = np.sqrt(np.maximum(np.diag(corr), 1e-12))
    corr = corr / np.outer(diag, diag)
    return (corr + corr.T) / 2.0


def _haar_bank(
    r: int, regimes: int = 2, draws: int = 64, seed: int = 0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    bank = np.empty((regimes, draws, r, r), dtype=float)
    for s in range(regimes):
        for m in range(draws):
            z = rng.standard_normal((r, r))
            q, rr = np.linalg.qr(z)
            signs = np.sign(np.diag(rr))
            signs[signs == 0] = 1.0
            q = q @ np.diag(signs)
            bank[s, m] = q
    return bank


def _core_indices(codes: Sequence[str]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    folded = {str(code).upper(): idx for idx, code in enumerate(codes)}
    for core, aliases in CORE_ALIASES.items():
        for alias in aliases:
            if alias.upper() in folded:
                mapping[core] = folded[alias.upper()]
                break
    # If the caller supplied a generic panel, use the conventional first nine
    # columns as the macro transmission block.
    for offset, core in enumerate(CORE_CODES):
        if core not in mapping and offset < len(codes):
            mapping[core] = offset
    return mapping


def calibrate_panel(
    *,
    codes: Sequence[str],
    names_vi: Sequence[str],
    units: Sequence[str],
    dates: Sequence[str],
    values: np.ndarray,
    n_factors: int = 5,
    lags: int = 5,
    seed: int = 20261026,
) -> CalibrationBundle:
    y, missing_mask, y_mean, y_std = _impute_and_standardize(values)
    x_std = (y - y_mean) / y_std
    t, n = x_std.shape
    r = max(2, min(int(n_factors), n, t - lags - 2))
    u, singular_values, vt = np.linalg.svd(x_std, full_matrices=False)
    factors = u[:, :r] * singular_values[:r]
    loadings = vt[:r, :].T  # N x r
    reconstruction = factors @ loadings.T
    r_diag = np.maximum(np.var(x_std - reconstruction, axis=0), 1e-5)

    # Ridge is deliberately non-trivial: with a 30-year annual panel, a
    # five-factor VAR(5) is nearly saturated (25 regressors).  A tiny ridge
    # would make the residual covariance numerically zero, which in turn
    # collapses the particle filter.  The ridge is recorded as part of the
    # reference calibration.
    a_lags, resid, _ = _fit_var(factors, lags=lags, ridge=0.10)
    effective_lags = len(a_lags)
    a_comp = _companion(a_lags, r, effective_lags)
    # The reference VAR is fitted on PCA levels.  Real annual panels can contain
    # near-unit-root or trending factors; an unconstrained companion matrix
    # would explode when the online layer propagates it for several horizons.
    # Shrink all lag blocks until the spectral radius is strictly inside the
    # unit circle, and record the adjustment instead of hiding it.
    spectral_radius = float(
        np.max(np.abs(np.linalg.eigvals(a_comp))) if a_comp.size else 0.0
    )
    stability_shrink_applied = False
    if spectral_radius >= 0.97:
        shrink = 0.97 / spectral_radius
        a_lags = [coef * shrink for coef in a_lags]
        a_comp = _companion(a_lags, r, effective_lags)
        spectral_radius = float(np.max(np.abs(np.linalg.eigvals(a_comp))))
        stability_shrink_applied = True
    omega = np.cov(resid.T) + np.eye(r) * 1e-5
    p_trans = np.array([[0.94, 0.06], [0.18, 0.82]], dtype=float)
    regimes, regime_probs, p_trans, omega_regimes = _regime_covariances(
        resid, p_trans
    )
    p_chol = np.empty((2, r, r), dtype=float)
    for s in (0, 1):
        try:
            p_chol[s] = np.linalg.cholesky(omega_regimes[s] + np.eye(r) * 1e-7)
        except np.linalg.LinAlgError:
            p_chol[s] = np.linalg.cholesky(omega + np.eye(r) * 1e-5)

    # Exogenous shock proxies: the first d factor innovation directions.
    d = min(4, r)
    shock_series = resid[:, :d]
    gpd_marginals = [_fit_gpd(shock_series[:, j]) for j in range(d)]
    r_c = _copula_correlation(shock_series)
    haar_bank = _haar_bank(r, regimes=2, draws=64, seed=seed)

    lambda_tilde = np.zeros((n, r * effective_lags), dtype=float)
    lambda_tilde[:, :r] = loadings

    r_inv = 1.0 / np.maximum(r_diag, 1e-6)
    w_proj = np.linalg.solve(
        loadings.T @ (r_inv[:, None] * loadings) + np.eye(r) * 1e-7,
        loadings.T * r_inv[None, :],
    )

    core_map = _core_indices(codes)
    target_core_codes = (
        "GDP_YOY",
        "CPI_YOY",
        "FX_DEPREC",
        "IB_RATE",
        "NPL_RATIO",
    )
    core_rows = [
        core_map.get(core, min(index, n - 1))
        for index, core in enumerate(target_core_codes)
    ]
    core_loadings = loadings[core_rows, :]

    # Four reference adverse directions, expressed as target responses of the
    # standardized core block (GDP, CPI, FX, IB, NPL).  Projecting these
    # targets through the estimated loading geometry removes the arbitrary PCA
    # sign and makes a positive GPD tail draw unambiguously adverse.
    adverse_targets = np.array(
        [
            [-0.90, -0.35, -0.55, -0.65],  # GDP
            [0.20, 0.80, 0.20, 0.30],      # CPI
            [0.85, 0.15, 0.25, 0.65],      # FX depreciation
            [0.30, 0.10, 0.95, 0.20],      # interbank rate
            [0.50, 0.20, 0.60, 0.40],      # NPL ratio
        ],
        dtype=float,
    )[:, :d]
    ridge = 0.01
    b_eps = np.linalg.solve(
        core_loadings.T @ core_loadings + ridge * np.eye(r),
        core_loadings.T @ adverse_targets,
    )

    # Policy transmission: bills, FX intervention (B USD), OMO.  Targets are
    # stated at reference tool scales and then converted to per-unit
    # coefficients.  This is the reference reduced-form policy map used by the
    # three-tier SBV corridor.
    policy_targets = np.array(
        [
            [-0.08, 0.015, 0.16],   # GDP
            [0.02, -0.03, -0.02],   # CPI
            [0.04, -0.16, -0.10],   # FX depreciation
            [0.10, -0.04, -0.20],   # interbank rate
            [0.04, 0.00, -0.06],    # NPL ratio
        ],
        dtype=float,
    )
    policy_scales = np.array([10.0, 5.0, 20.0], dtype=float)
    b_u_target = np.linalg.solve(
        core_loadings.T @ core_loadings + ridge * np.eye(r),
        core_loadings.T @ policy_targets,
    )
    b_u = b_u_target / policy_scales[None, :]
    d_rho = np.array(
        [0.85, 0.82, 0.42, 0.88, 0.80, 0.75, 0.78, 0.82][:d], dtype=float
    )

    core_index = _core_indices(codes)
    tail_signs = {
        code: int(TAIL_SIGNS.get(code, 1))
        for code in CORE_CODES
    }
    last_state = np.zeros(r * effective_lags, dtype=float)
    for lag in range(effective_lags):
        if lag < t:
            last_state[lag * r : (lag + 1) * r] = factors[t - 1 - lag]
    last_factor = factors[-1]

    return CalibrationBundle(
        codes=list(codes),
        names_vi=list(names_vi),
        units=list(units),
        dates=list(dates),
        y=y,
        y_mean=y_mean,
        y_std=y_std,
        x_std=x_std,
        missing_mask=missing_mask,
        factors=factors,
        loadings=loadings,
        a_lags=a_lags,
        a_comp=a_comp,
        omega=omega,
        omega_regimes=omega_regimes,
        p_chol=p_chol,
        p_trans=p_trans,
        regime_probs=regime_probs,
        regimes=regimes,
        residuals=resid,
        r_diag=r_diag,
        lambda_tilde=lambda_tilde,
        w_proj=w_proj,
        b_eps=b_eps,
        b_u=b_u,
        d_rho=d_rho,
        gpd_marginals=gpd_marginals,
        r_c=r_c,
        haar_bank=haar_bank,
        last_state=last_state,
        last_factor=last_factor,
        core_index=core_index,
        tail_signs=tail_signs,
        diagnostics={
            "estimation_backend": "pca_var5_hard_em_covariance",
            "imputation_policy": "interpolate_inside_median_outside",
            "n_factors": r,
            "var_lags": effective_lags,
            "var_companion_spectral_radius": round(float(spectral_radius), 8),
            "var_stability_shrink_applied": bool(stability_shrink_applied),
            "state_dim": int(a_comp.shape[0]),
            "missing_cells": int(missing_mask.sum()),
            "missing_ratio": round(float(missing_mask.mean()), 6),
            "regime_counts": [int((regimes == s).sum()) for s in (0, 1)],
            "p_transition": p_trans.tolist(),
            "factor_variance_share": float(
                np.sum(singular_values[:r] ** 2)
                / np.sum(singular_values**2)
            ),
            "shock_dimension": d,
            "tail_thresholds": [g.threshold for g in gpd_marginals],
            "tail_xis": [g.xi for g in gpd_marginals],
        },
    )


__all__ = [
    "CORE_CODES",
    "CalibrationBundle",
    "GpdMarginal",
    "TAIL_SIGNS",
    "calibrate_panel",
]
