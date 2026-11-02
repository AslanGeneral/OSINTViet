"""Pillar 4: Carroll-Toche buffer stock, EGM, two-asset HANK, Merton and FGT/VEP.

The equations are retained from the supplied specification.  Policy thresholds
and MPC values are constructor parameters loaded from
``config/micro_household.json`` instead of module constants.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
from scipy.special import ndtr


class BufferStockEGMAndVulnerabilityEngine:
    def __init__(
        self,
        *,
        n_grid: int,
        a_max: float,
        mpc_by_regime: tuple[float, float, float],
        liquid_buffer_threshold_months: float,
        mdsr_threshold: float,
        vep_vulnerable_threshold: float,
        vep_high_fragility_threshold: float,
    ) -> None:
        if len(mpc_by_regime) != 3:
            raise ValueError("mpc_by_regime must contain regimes 0, 1, 2")
        self.a_grid = np.expm1(np.linspace(0.0, np.log1p(a_max), n_grid))
        self.n_grid = n_grid
        self.mpc_by_regime = tuple(float(value) for value in mpc_by_regime)
        self.liquid_buffer_threshold_months = float(
            liquid_buffer_threshold_months
        )
        self.mdsr_threshold = float(mdsr_threshold)
        self.vep_vulnerable_threshold = float(vep_vulnerable_threshold)
        self.vep_high_fragility_threshold = float(
            vep_high_fragility_threshold
        )

    @staticmethod
    def compute_carroll_toche_target_buffer(
        rho: np.ndarray,
        R_gross: np.ndarray,
        beta_disc: np.ndarray,
        Gamma_growth: np.ndarray,
        mho_unemp_prob: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Return RIC/GIC, perfect-foresight MPC and target resources ratio.

        ``m_target_ratio`` is the dimensionless Carroll-Toche target market
        resources ratio ``m_check^e`` from Eq. 26 (market resources divided by
        permanent income).  It is not a number of months; convert explicitly
        with household income/expenditure if a month-denominated buffer is
        required.
        """
        rho = np.asarray(rho, dtype=np.float64)
        R_gross = np.asarray(R_gross, dtype=np.float64)
        beta_disc = np.asarray(beta_disc, dtype=np.float64)
        Gamma_growth = np.asarray(Gamma_growth, dtype=np.float64)
        mho = np.asarray(mho_unemp_prob, dtype=np.float64)

        thorn_R = (R_gross ** ((1.0 / rho) - 1.0)) * (
            beta_disc ** (1.0 / rho)
        )
        thorn_Gamma = ((R_gross * beta_disc) ** (1.0 / rho)) / np.maximum(
            1e-6, Gamma_growth
        )
        thorn_R_safe = np.minimum(0.995, thorn_R)
        thorn_Gamma_safe = np.minimum(0.995, thorn_Gamma)

        kappa_u = 1.0 - thorn_R_safe
        vartheta = 1.0 - thorn_Gamma_safe
        gamma_log = np.log(np.maximum(1.0001, 1.0 / thorn_Gamma_safe))
        omega_prudence = 0.5 * (rho - 1.0)

        denominator = vartheta * (
            1.0
            + (gamma_log / np.maximum(1e-6, mho))
            * (1.0 - (gamma_log / np.maximum(1e-6, mho)) * omega_prudence)
        )
        denominator = np.maximum(0.02, denominator)
        m_target_ratio = 1.0 + (1.0 / denominator)

        return {
            "thorn_R": thorn_R,
            "thorn_Gamma": thorn_Gamma,
            "ric_satisfied": thorn_R < 1.0,
            "gic_satisfied": thorn_Gamma < 1.0,
            "kappa_u_pf_mpc": kappa_u,
            "m_target_ratio": m_target_ratio,
        }

    def solve_egm_policy_function(
        self,
        *,
        rho: float,
        R_gross: float,
        beta_disc: float,
        Gamma_growth: float,
        sigma_xi: float,
        b_borrow_limit: float,
        max_iter: int = 120,
        tol: float = 1e-6,
    ) -> Dict[str, np.ndarray]:
        """Carroll (2006) endogenous grid method with exact kink (M22)."""
        gh_nodes = np.array(
            [-2.02018287, -0.95857246, 0.0, 0.95857246, 2.02018287]
        )
        gh_weights = np.array(
            [0.01995324, 0.39361932, 0.94530872, 0.39361932, 0.01995324]
        ) / np.sqrt(np.pi)
        xi_nodes = np.exp(
            np.sqrt(2.0) * sigma_xi * gh_nodes - 0.5 * (sigma_xi**2)
        )

        a_j = self.a_grid - b_borrow_limit
        m_endo = a_j + 0.5 + 0.1 * self.a_grid
        c_endo = 0.5 + 0.1 * self.a_grid

        for _ in range(max_iter):
            m_next = (R_gross / Gamma_growth) * a_j[:, None] + xi_nodes[None, :]
            c_next = np.interp(m_next, m_endo, c_endo)
            constrained_mask = m_next <= m_endo[0]
            c_next = np.where(
                constrained_mask,
                np.maximum(1e-6, m_next + b_borrow_limit),
                c_next,
            )
            expected_mu = np.sum(
                gh_weights[None, :]
                * (Gamma_growth ** (-rho))
                * (np.maximum(1e-6, c_next) ** (-rho)),
                axis=-1,
            )
            c_star = (beta_disc * R_gross * expected_mu) ** (-1.0 / rho)
            m_star = c_star + a_j

            if np.max(np.abs(c_star - c_endo)) < tol:
                c_endo, m_endo = c_star, m_star
                break
            c_endo, m_endo = c_star, m_star

        m_full = np.concatenate(([-b_borrow_limit], m_endo))
        c_full = np.concatenate(([0.0], c_endo))
        return {
            "m_grid_endogenous": m_full,
            "c_grid_endogenous": c_full,
            "m_kink_threshold": float(m_endo[0]),
        }

    def classify_two_asset_hank_and_merton_share(
        self,
        b_liquid: np.ndarray,
        a_illiquid: np.ndarray,
        E_sub_floor: np.ndarray,
        debt_service: np.ndarray,
        x_nominal: np.ndarray,
        rho: np.ndarray,
        equity_risk_premium: np.ndarray,
        return_variance: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Kaplan-Moll-Violante two-asset HANK and Merton share (M23-M24)."""
        b_liq_exp = np.asarray(b_liquid, dtype=np.float64)
        if b_liq_exp.ndim == 1:
            b_liq_exp = b_liq_exp[:, None]
        a_illiq_exp = np.asarray(a_illiquid, dtype=np.float64)
        if a_illiq_exp.ndim == 1:
            a_illiq_exp = a_illiq_exp[:, None]
        e_sub = np.asarray(E_sub_floor, dtype=np.float64)
        debt = np.asarray(debt_service, dtype=np.float64)
        if debt.ndim == 1:
            debt = debt[:, None]
        x_nom = np.asarray(x_nominal, dtype=np.float64)
        if x_nom.ndim == 1:
            x_nom = x_nom[:, None]
        rho_arr = np.asarray(rho, dtype=np.float64).reshape(-1)
        erp = np.asarray(equity_risk_premium, dtype=np.float64).reshape(-1)
        variance = np.asarray(return_variance, dtype=np.float64).reshape(-1)

        liquid_buffer_months = b_liq_exp / np.maximum(1e-6, e_sub)
        supernumerary_inc = np.maximum(1e-6, x_nom - e_sub)
        mdsr = debt / supernumerary_inc

        is_liquid_constrained = (
            liquid_buffer_months < self.liquid_buffer_threshold_months
        ) | (mdsr > self.mdsr_threshold)
        is_w_htm = is_liquid_constrained & (a_illiq_exp > 0.0)
        is_p_htm = is_liquid_constrained & (a_illiq_exp <= 0.0)

        regime_code = np.zeros_like(liquid_buffer_months, dtype=np.int32)
        regime_code = np.where(is_w_htm, 1, regime_code)
        regime_code = np.where(is_p_htm, 2, regime_code)

        mpc_quarterly = np.where(
            regime_code == 2,
            self.mpc_by_regime[2],
            np.where(regime_code == 1, self.mpc_by_regime[1], self.mpc_by_regime[0]),
        )
        raw_merton = erp[None, :] / np.maximum(
            1e-6, rho_arr[:, None] * variance[None, :]
        )
        merton_share = np.where(
            liquid_buffer_months >= self.liquid_buffer_threshold_months,
            np.clip(raw_merton, 0.0, 1.0),
            0.0,
        )
        return {
            "liquid_buffer_months": liquid_buffer_months,
            "mdsr_ratio": mdsr,
            "hank_regime_code": regime_code,
            "mpc_quarterly": mpc_quarterly,
            "merton_optimal_risky_share": merton_share,
        }

    def compute_fgt_and_3step_fgls_vep(
        self,
        x_real_equiv: np.ndarray,
        z_poverty_line: float,
        sigma_c_total: np.ndarray,
        q_weights: np.ndarray,
    ) -> Dict[str, Any]:
        """FGT P0/P1/P2 and Chaudhuri VEP (M25-M26)."""
        x_real = np.asarray(x_real_equiv, dtype=np.float64)
        if x_real.ndim == 1:
            x_real = x_real[:, None]
        sigma = np.asarray(sigma_c_total, dtype=np.float64)
        weights = np.asarray(q_weights, dtype=np.float64).reshape(-1)
        weights = weights / max(1e-12, float(np.sum(weights)))
        if sigma.shape != x_real.shape:
            raise ValueError("sigma_c_total shape must match x_real_equiv")

        gap_ratio = np.maximum(0.0, (z_poverty_line - x_real) / z_poverty_line)
        fgt_p0_hh = np.where(x_real < z_poverty_line, 1.0, 0.0)
        fgt_p1_hh = gap_ratio
        fgt_p2_hh = gap_ratio**2

        ln_z = np.log(max(1e-6, z_poverty_line))
        ln_c_exp = np.log(np.maximum(1e-6, x_real))
        z_score = (ln_z - ln_c_exp) / np.maximum(1e-4, sigma)
        vep_matrix = ndtr(z_score)

        expected_vep_hh = vep_matrix @ weights
        expected_fgt_p2_hh = fgt_p2_hh @ weights

        sort_idx = np.argsort(vep_matrix, axis=1, kind="mergesort")
        sorted_vep = np.take_along_axis(vep_matrix, sort_idx, axis=1)
        sorted_q = weights[sort_idx]
        cum_q = np.cumsum(sorted_q, axis=1)
        var99_pos = np.argmax(cum_q >= 0.99, axis=1)
        var99_pos = np.where(cum_q[:, -1] >= 0.99, var99_pos, weights.size - 1)
        vep_var99_hh = sorted_vep[np.arange(vep_matrix.shape[0]), var99_pos]

        return {
            "expected_vep_per_hh": expected_vep_hh,
            "vep_var99_per_hh": vep_var99_hh,
            "expected_fgt_p2_per_hh": expected_fgt_p2_hh,
            "aggregate_fgt_p0": float(np.mean(fgt_p0_hh @ weights)),
            "aggregate_fgt_p1": float(np.mean(fgt_p1_hh @ weights)),
            "aggregate_fgt_p2": float(np.mean(expected_fgt_p2_hh)),
            "aggregate_vulnerable_share_gt_029": float(
                np.mean(expected_vep_hh >= self.vep_vulnerable_threshold)
            ),
            "aggregate_high_fragility_share_gt_050": float(
                np.mean(expected_vep_hh >= self.vep_high_fragility_threshold)
            ),
        }


__all__ = ["BufferStockEGMAndVulnerabilityEngine"]
