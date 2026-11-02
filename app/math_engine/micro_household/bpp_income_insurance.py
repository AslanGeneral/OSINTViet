"""Pillar 3: BPP permanent/transitory income shocks and partial insurance."""

from __future__ import annotations

from typing import Dict

import numpy as np


class BPPIncomeAndInsuranceEngine:
    def __init__(self, *, price_transitory_pass_through: float) -> None:
        self.price_transitory_pass_through = float(
            price_transitory_pass_through
        )

    @staticmethod
    def estimate_bpp_gmm_variances(
        delta_y_panel: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Estimate sigma_v^2 and sigma_u^2 from log residual income growth.

        Equations M17-M18 (Blundell, Pistaferri & Preston, 2008).
        """
        if delta_y_panel.ndim == 1:
            delta_y_panel = delta_y_panel[None, :]
        n_hh, periods = delta_y_panel.shape
        if periods < 3:
            raise ValueError(
                "BPP covariance identification requires at least T >= 3 periods"
            )
        dy_tm1 = delta_y_panel[:, :-2]
        dy_t = delta_y_panel[:, 1:-1]
        dy_tp1 = delta_y_panel[:, 2:]

        # Equations M17-M18 are cross-sectional moment conditions: at each
        # period t the covariance is taken across households, not across time
        # within a household.  Taking a within-household time covariance
        # produces a finite-T bias and does not identify the BPP variances.
        def _cross_cov(left: np.ndarray, right: np.ndarray) -> np.ndarray:
            left_centered = left - np.mean(left, axis=0, keepdims=True)
            right_centered = right - np.mean(right, axis=0, keepdims=True)
            return np.mean(left_centered * right_centered, axis=0)

        cov_lag1 = _cross_cov(dy_t, dy_tm1)
        sigma_u_sq_period = np.maximum(1e-5, -cov_lag1)

        sum_3 = dy_tm1 + dy_t + dy_tp1
        cov_3 = _cross_cov(dy_t, sum_3)
        sigma_v_sq_period = np.maximum(1e-5, cov_3)

        # Pool the identified periods.  The per-period vectors are returned
        # for diagnostics; the scalar arrays are the GMM estimates used by
        # downstream calibration.
        sigma_u_sq = np.array([float(np.mean(sigma_u_sq_period))])
        sigma_v_sq = np.array([float(np.mean(sigma_v_sq_period))])

        return {
            "sigma_v_permanent_sq": sigma_v_sq,
            "sigma_u_transitory_sq": sigma_u_sq,
            "sigma_v_permanent": np.sqrt(sigma_v_sq),
            "sigma_u_transitory": np.sqrt(sigma_u_sq),
            "sigma_v_permanent_sq_by_period": sigma_v_sq_period,
            "sigma_u_transitory_sq_by_period": sigma_u_sq_period,
        }

    def project_conditional_income_and_consumption_shocks(
        self,
        y_base_nominal: np.ndarray,
        P_T_tornqvist: np.ndarray,
        F_medoids_terminal: np.ndarray,
        beta_perm_macro: np.ndarray,
        beta_trans_macro: np.ndarray,
        psi_perm_pass: np.ndarray,
        phi_trans_pass: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Project real income shocks across K macro scenarios (M19)."""
        y_base = np.asarray(y_base_nominal, dtype=np.float64).reshape(-1)
        p_t = np.asarray(P_T_tornqvist, dtype=np.float64)
        if p_t.ndim == 1:
            p_t = p_t[None, :]
        factors = np.asarray(F_medoids_terminal, dtype=np.float64)
        beta_perm = np.asarray(beta_perm_macro, dtype=np.float64)
        if beta_perm.ndim == 1:
            beta_perm = beta_perm[None, :]
        beta_trans = np.asarray(beta_trans_macro, dtype=np.float64)
        if beta_trans.ndim == 1:
            beta_trans = beta_trans[None, :]
        psi = np.asarray(psi_perm_pass, dtype=np.float64).reshape(-1)
        phi = np.asarray(phi_trans_pass, dtype=np.float64).reshape(-1)

        v_perm_shock = beta_perm @ factors.T
        u_trans_shock = beta_trans @ factors.T - float(
            self.price_transitory_pass_through
        ) * np.log(np.maximum(1e-8, p_t))
        delta_ln_c_bpp = psi[:, None] * v_perm_shock + phi[:, None] * u_trans_shock
        y_post_real = (
            y_base[:, None] / np.maximum(1e-8, p_t)
        ) * np.exp(v_perm_shock + u_trans_shock)
        return {
            "v_perm_shock": v_perm_shock,
            "u_trans_shock": u_trans_shock,
            "delta_ln_c_bpp": delta_ln_c_bpp,
            "y_post_real": y_post_real,
        }


__all__ = ["BPPIncomeAndInsuranceEngine"]
