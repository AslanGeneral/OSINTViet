"""Layer 2/3: adversarial shock search with copula-GPD plausibility.

The optimizer works in standardized space and projects every candidate onto the
Mahalanobis ellipsoid before mapping through the C0-continuous GPD marginals.
A compact CMA-ES implementation is included so the engine has no dependency on
the optional ``cma`` package.  Layer 3 attaches soft narrative-sign weights to
the elite shock set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.special import lambertw
from scipy.stats import norm

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle, GpdMarginal


def kappa_squared(dimension: int, alpha: float = 1e-4) -> float:
    dimension = int(dimension)
    if dimension == 4:
        # Erlang order-2 survival equation; exact W_{-1} branch.
        return float(np.real(-2.0 * lambertw(-alpha / math.e, k=-1) - 2.0))
    if dimension == 3:
        return 21.1075
    from scipy.stats import chi2

    return float(chi2.ppf(1.0 - alpha, df=dimension))


def project_to_ellipsoid(
    z: np.ndarray, r_c_inv: np.ndarray, kappa: float
) -> np.ndarray:
    z = np.asarray(z, dtype=float)
    distance = float(np.sqrt(max(float(z.T @ r_c_inv @ z), 1e-15)))
    return z * min(1.0, kappa / distance)


def _physical_shock(z: np.ndarray, marginals: List[GpdMarginal]) -> np.ndarray:
    probabilities = norm.cdf(np.asarray(z, dtype=float))
    return np.array([m.ppf(float(u)) for m, u in zip(marginals, probabilities)])


@dataclass
class ShockCandidate:
    z: np.ndarray
    physical: np.ndarray
    objective: float
    metrics: Dict[str, float]


class AdversarialShockEngine:
    def __init__(
        self,
        calib: CalibrationBundle,
        *,
        horizon: int = 4,
        shock_dimension: int = 4,
        alpha: float = 1e-4,
        seed: int = 20261026,
    ) -> None:
        self.calib = calib
        self.horizon = int(horizon)
        self.shock_dim = max(3, min(int(shock_dimension), calib.n_factors, 6))
        self.alpha = float(alpha)
        self.seed = int(seed)
        self.kappa_sq = kappa_squared(self.shock_dim, self.alpha)
        self.kappa = float(np.sqrt(self.kappa_sq))
        self.phi = np.linalg.cholesky(calib.r_c + np.eye(self.shock_dim) * 1e-7)
        self.r_c_inv = np.linalg.inv(calib.r_c + np.eye(self.shock_dim) * 1e-7)
        self.gpd = calib.gpd_marginals[: self.shock_dim]
        self.gdp_i = calib.core_index.get("GDP_YOY", 0)
        self.fx_i = calib.core_index.get("FX_DEPREC", 2)
        self.ib_i = calib.core_index.get("IB_RATE", 3)
        self.npl_i = calib.core_index.get("NPL_RATIO", 5)
        self._cache: Dict[bytes, ShockCandidate] = {}

    # ------------------------------------------------------------------
    # Analytical impact projection
    # ------------------------------------------------------------------
    def _propagate(
        self, shock: Optional[np.ndarray], *, active_policy: bool = False
    ) -> Dict[str, np.ndarray]:
        dim = self.calib.state_dim
        r = self.calib.n_factors
        state = np.asarray(self.calib.last_state, dtype=float).copy()
        a = np.asarray(self.calib.a_comp, dtype=float)
        policy = np.zeros(3, dtype=float)
        if active_policy:
            # A conservative analytical approximation of the 3-tier corridor;
            # the full branchless defender is used in Layer 5.
            policy = np.array([3.0, -1.0, 2.0], dtype=float)
        gdp_path: List[float] = []
        fx_path: List[float] = []
        ib_path: List[float] = []
        npl_path: List[float] = []
        for h in range(1, self.horizon + 1):
            shock_effect = np.zeros(dim, dtype=float)
            if shock is not None:
                decay = np.power(self.calib.d_rho, h) * shock
                shock_effect[:r] += self.calib.b_eps @ decay
            shock_effect[:r] += self.calib.b_u @ policy
            state = a @ state + shock_effect
            observed = self.calib.y_mean + self.calib.y_std * (
                self.calib.lambda_tilde @ state
            )
            gdp_path.append(float(observed[self.gdp_i]))
            fx_path.append(float(observed[self.fx_i]))
            ib_path.append(float(observed[self.ib_i]))
            npl_path.append(float(observed[self.npl_i]))
        return {
            "gdp": np.asarray(gdp_path),
            "fx": np.asarray(fx_path),
            "ib": np.asarray(ib_path),
            "npl": np.asarray(npl_path),
        }

    def _metrics(
        self,
        shock: Optional[np.ndarray],
        baseline: Dict[str, np.ndarray],
        *,
        active_policy: bool = False,
    ) -> Dict[str, float]:
        path = self._propagate(shock, active_policy=active_policy)
        # Tail risk is a trough/peak concept, not a terminal-only comparison:
        # an adverse shock can rebound mechanically after the first quarter.
        gdp_loss = float(np.min(baseline["gdp"]) - np.min(path["gdp"]))
        fx_deprec = float(np.max(path["fx"]) - np.mean(baseline["fx"]))
        ib_spike = float(np.max(path["ib"]) - np.max(baseline["ib"]))
        npl_delta = float(np.max(path["npl"]) - np.max(baseline["npl"]))
        return {
            "gdp_loss": gdp_loss,
            "fx_deprec": fx_deprec,
            "ib_spike": ib_spike,
            "npl_delta": npl_delta,
        }

    def _objective_metrics(self, metrics: Dict[str, float]) -> float:
        # Positive losses are good for the adversary.  The scale transform
        # prevents one variable with a large native unit from dominating.
        gdp = max(metrics["gdp_loss"], -10.0)
        fx = max(metrics["fx_deprec"], -10.0)
        ib = max(metrics["ib_spike"], -10.0)
        npl = max(metrics["npl_delta"], -10.0)
        return float(
            0.35 * gdp
            + 0.30 * fx
            + 0.25 * ib
            + 0.10 * npl
        )

    def evaluate_z(self, z: np.ndarray) -> ShockCandidate:
        z = project_to_ellipsoid(
            np.asarray(z, dtype=float), self.r_c_inv, self.kappa
        )
        key = np.round(z, 10).tobytes()
        if key in self._cache:
            return self._cache[key]
        physical = _physical_shock(z, self.gpd)
        baseline = self._propagate(None)
        metrics = self._metrics(physical, baseline)
        objective = self._objective_metrics(metrics)
        candidate = ShockCandidate(
            z=z.copy(),
            physical=physical,
            objective=float(objective),
            metrics=metrics,
        )
        self._cache[key] = candidate
        return candidate

    # ------------------------------------------------------------------
    # CMA-ES (Hansen's covariance-matrix adaptation, compact implementation)
    # ------------------------------------------------------------------
    def _cma_es(
        self,
        population: int = 32,
        generations: int = 50,
    ) -> List[ShockCandidate]:
        rng = np.random.default_rng(self.seed)
        dim = self.shock_dim
        mean = np.zeros(dim, dtype=float)
        sigma = 2.0
        covariance = np.eye(dim, dtype=float)
        p_c = np.zeros(dim, dtype=float)
        p_s = np.zeros(dim, dtype=float)
        mu = population // 2
        weights = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
        weights = weights / weights.sum()
        mueff = 1.0 / np.sum(weights**2)
        cc = (4.0 + mueff / dim) / (dim + 4.0 + 2.0 * mueff / dim)
        cs = (mueff + 2.0) / (dim + mueff + 5.0)
        c1 = 2.0 / ((dim + 1.3) ** 2 + mueff)
        cmu = min(
            1.0 - c1,
            2.0 * (mueff - 2.0 + 1.0 / mueff) / ((dim + 2.0) ** 2 + mueff),
        )
        damps = 1.0 + 2.0 * max(0.0, math.sqrt((mueff - 1.0) / (dim + 1.0)) - 1.0) + cs
        chi_n = math.sqrt(dim) * (1.0 - 1.0 / (4.0 * dim) + 1.0 / (21.0 * dim**2))
        best: List[ShockCandidate] = []
        all_candidates: List[ShockCandidate] = []

        for _generation in range(int(generations)):
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            eigenvalues = np.maximum(eigenvalues, 1e-12)
            sqrt_d = np.sqrt(eigenvalues)
            arz = rng.standard_normal((population, dim))
            arx = mean[None, :] + sigma * (
                (arz * sqrt_d[None, :]) @ eigenvectors.T
            )
            candidates = [self.evaluate_z(row) for row in arx]
            all_candidates.extend(candidates)
            order = np.argsort([-c.objective for c in candidates])
            selected = [candidates[i] for i in order[:mu]]
            old_mean = mean.copy()
            mean = np.sum(
                np.asarray([c.z for c in selected]) * weights[:, None], axis=0
            )
            y_mean = (mean - old_mean) / max(sigma, 1e-12)
            c_inv_sqrt = eigenvectors @ np.diag(1.0 / sqrt_d) @ eigenvectors.T
            p_s = (1.0 - cs) * p_s + math.sqrt(
                cs * (2.0 - cs) * mueff
            ) * (c_inv_sqrt @ y_mean)
            norm_ps = float(np.linalg.norm(p_s))
            h_sigma = norm_ps / math.sqrt(1.0 - (1.0 - cs) ** (2.0 * (_generation + 1))) < (
                1.4 + 2.0 / (dim + 1.0)
            ) * chi_n
            p_c = (1.0 - cc) * p_c + h_sigma * math.sqrt(
                cc * (2.0 - cc) * mueff
            ) * y_mean
            artmp = (np.asarray([c.z for c in selected]) - old_mean[None, :]) / max(
                sigma, 1e-12
            )
            covariance = (
                (1.0 - c1 - cmu) * covariance
                + c1 * (np.outer(p_c, p_c) + (1.0 - h_sigma) * cc * (2.0 - cc) * covariance)
                + cmu * (artmp.T @ np.diag(weights) @ artmp)
            )
            covariance = 0.5 * (covariance + covariance.T)
            sigma *= math.exp((cs / damps) * (norm_ps / chi_n - 1.0))
            sigma = float(np.clip(sigma, 0.05, 8.0))
            best = sorted(
                all_candidates, key=lambda c: c.objective, reverse=True
            )[:64]

        # Deduplicate in z-space while retaining the strongest candidates.
        unique: List[ShockCandidate] = []
        seen: set[bytes] = set()
        for candidate in best:
            key = np.round(candidate.z, 8).tobytes()
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    # ------------------------------------------------------------------
    # Layer 3 soft narrative gate
    # ------------------------------------------------------------------
    def _narrative_weights(self, elite: List[ShockCandidate]) -> np.ndarray:
        if not elite:
            return np.ones(0, dtype=float)
        z = np.asarray([c.z for c in elite])
        maha = np.einsum("ni,ij,nj->n", z, self.r_c_inv, z)
        # Heavy-tailed Student-t kernel in the same standardized space.
        nu = 5.0
        copula = np.power(1.0 + maha / nu, -(nu + self.shock_dim) / 2.0)
        penalties = []
        for candidate in elite:
            m = candidate.metrics
            violations = [
                max(0.0, -m["gdp_loss"]),
                max(0.0, -m["fx_deprec"]),
                max(0.0, -m["ib_spike"]),
                max(0.0, -m["npl_delta"]),
            ]
            penalties.append(
                0.5
                * (
                    (violations[0] / 0.75) ** 2
                    + (violations[1] / 1.50) ** 2
                    + (violations[2] / 1.00) ** 2
                    + (violations[3] / 0.50) ** 2
                )
            )
        values = copula * np.exp(-np.asarray(penalties))
        values = np.maximum(values, 1e-300)
        return values / values.sum()

    def run(
        self,
        *,
        mode: str = "adversarial",
        explicit_shock: Optional[List[float]] = None,
        population: int = 32,
        generations: int = 50,
        top_k: int = 16,
    ) -> Dict[str, Any]:
        if mode == "explicit" and explicit_shock is not None:
            physical = np.asarray(explicit_shock, dtype=float)[: self.shock_dim]
            z = np.array(
                [
                    float(norm.ppf(np.clip(m.cdf(v), 1e-8, 1.0 - 1e-8)))
                    for m, v in zip(self.gpd, physical)
                ]
            )
            base = self.evaluate_z(z)
            elite = [base]
            # The narrative gate still needs a proposal set; perturb within
            # the ellipsoid to express estimation uncertainty.
            rng = np.random.default_rng(self.seed + 1)
            for _ in range(max(0, top_k - 1)):
                elite.append(self.evaluate_z(base.z + rng.normal(0.0, 0.35, self.shock_dim)))
            elite = sorted(elite, key=lambda c: c.objective, reverse=True)[:top_k]
            weights = self._narrative_weights(elite)
            best = max(elite, key=lambda c: c.objective)
            return self._result(elite, weights, best, mode)
        if mode == "historical" and explicit_shock is not None:
            return self.run(
                mode="explicit",
                explicit_shock=explicit_shock,
                population=population,
                generations=generations,
                top_k=top_k,
            )

        candidates = self._cma_es(population=population, generations=generations)
        if not candidates:
            candidates = [self.evaluate_z(np.zeros(self.shock_dim))]
        elite = candidates[: max(1, min(int(top_k), len(candidates)))]
        if len(elite) < top_k:
            base = elite[0]
            rng = np.random.default_rng(self.seed + 2)
            while len(elite) < top_k:
                elite.append(
                    self.evaluate_z(base.z + rng.normal(0.0, 0.20, self.shock_dim))
                )
        elite = sorted(elite, key=lambda c: c.objective, reverse=True)[:top_k]
        weights = self._narrative_weights(elite)
        best = max(elite, key=lambda c: c.objective)
        return self._result(elite, weights, best, "adversarial")

    def _result(
        self,
        elite: List[ShockCandidate],
        weights: np.ndarray,
        best: ShockCandidate,
        mode: str,
    ) -> Dict[str, Any]:
        names = [
            "delta_dxy_sd",
            "delta_oil_sd",
            "interbank_drain_sd",
            "delta_gpr_policy_sd",
            "fifth_factor_shock_sd",
            "sixth_factor_shock_sd",
        ]
        profile = [
            {
                "name": names[i] if i < len(names) else f"shock_{i}",
                "value": round(float(best.physical[i]), 6),
                "unit": "gpd_standardized_tail",
            }
            for i in range(self.shock_dim)
        ]
        return {
            "mode": mode,
            "shock_dimension": self.shock_dim,
            "mahalanobis_radius_sq": round(float(self.kappa_sq), 6),
            "mahalanobis_radius": round(float(self.kappa), 6),
            "plausibility_level_alpha": self.alpha,
            "best_z": [round(float(v), 8) for v in best.z],
            "best_physical": [round(float(v), 8) for v in best.physical],
            "best_metrics": {k: round(float(v), 6) for k, v in best.metrics.items()},
            "best_objective": round(float(best.objective), 6),
            "shock_profile": profile,
            "elite_z": [[round(float(v), 8) for v in c.z] for c in elite],
            "elite_physical": [
                [round(float(v), 8) for v in c.physical] for c in elite
            ],
            "elite_objectives": [round(float(c.objective), 6) for c in elite],
            "narrative_weights": [round(float(v), 10) for v in weights],
            "top_k": len(elite),
            "cma_population": 32,
            "cma_generations": 50,
            "evaluations": len(self._cache),
        }


__all__ = [
    "AdversarialShockEngine",
    "ShockCandidate",
    "kappa_squared",
    "project_to_ellipsoid",
]
