"""Pillar 1 & 2: cost-of-living indices, QUAIDS demand and equivalence scales.

Adapted from SPEC-MICRO-HOUSEHOLD-SUBSYSTEM-v5.8 without changing the
equations.  All coefficients are injected from ``config/micro_household.json``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


class CostOfLivingAndEquivalenceEngine:
    def __init__(self, params: Dict[str, Any]):
        self.alpha = np.asarray(params["quaids_alpha"], dtype=np.float64)
        self.beta = np.asarray(params["quaids_beta"], dtype=np.float64)
        self.lambda_q = np.asarray(params["quaids_lambda"], dtype=np.float64)
        self.gamma_matrix = np.asarray(params["quaids_gamma"], dtype=np.float64)
        self.subsistence_gamma_les = np.asarray(
            params["les_subsistence_quantities"], dtype=np.float64
        )
        self.alpha_0 = float(params["quaids_alpha_0"])
        self.equivalence_scales = dict(params["equivalence_scales"])

        if self.alpha.shape != (4,) or self.beta.shape != (4,):
            raise ValueError("QUAIDS alpha/beta must have 4 goods")
        if self.lambda_q.shape != (4,):
            raise ValueError("QUAIDS lambda must have 4 goods")
        if self.gamma_matrix.shape != (4, 4):
            raise ValueError("QUAIDS gamma must be 4x4")
        if self.subsistence_gamma_les.shape != (4,):
            raise ValueError("LES subsistence quantities must have 4 goods")
        if not np.isclose(np.sum(self.alpha), 1.0, atol=1e-6):
            raise ValueError("QUAIDS alpha must sum to 1.0")
        if not np.isclose(np.sum(self.beta), 0.0, atol=1e-6):
            raise ValueError("QUAIDS beta must sum to 0.0")
        if not np.isclose(np.sum(self.lambda_q), 0.0, atol=1e-6):
            raise ValueError("QUAIDS lambda must sum to 0.0")
        if not np.allclose(self.gamma_matrix, self.gamma_matrix.T, atol=1e-6):
            raise ValueError("QUAIDS gamma must be symmetric")

    def compute_equivalence_scale(
        self,
        n_adults: np.ndarray,
        n_children: np.ndarray,
        method: str = "oecd_modified",
        price_relatives: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Equivalence scale D_h (equations M11-M14)."""
        n_a = np.maximum(1.0, np.asarray(n_adults, dtype=np.float64))
        n_c = np.maximum(0.0, np.asarray(n_children, dtype=np.float64))
        if method == "oecd_modified":
            scales = self.equivalence_scales["oecd_modified"]
            d_base = (
                1.0
                + float(scales["adult_weight"]) * (n_a - 1.0)
                + float(scales["child_weight"]) * n_c
            )
        elif method == "oecd_sqrt":
            d_base = np.sqrt(n_a + n_c)
        elif method == "bls_ses":
            scales = self.equivalence_scales["bls_ses"]
            d_base = (
                1.0
                + float(scales["adult_weight"]) * (n_a - 1.0)
                + float(scales["child_weight"]) * n_c
            )
        else:
            raise ValueError(f"unsupported equivalence method: {method}")

        if price_relatives is not None:
            relatives = np.asarray(price_relatives, dtype=np.float64)
            if relatives.ndim == 3:
                if relatives.shape[0] != 1:
                    raise ValueError(
                        "price_relatives must be (K, M) or (1, K, M)"
                    )
                relatives = relatives[0]
            p_food_rel = relatives[..., 0]
            n_ratio = n_c / (n_a + n_c + 1e-8)
            eta_child_food = float(self.equivalence_scales["eta_child_food"])
            exponent = eta_child_food * n_ratio
            if p_food_rel.ndim == 0:
                return d_base * (float(p_food_rel) ** exponent)
            if np.asarray(d_base).ndim == 1 and exponent.ndim == 1:
                # Households x scenarios: D_hk = D_h * p_k ** eta_h.
                return d_base[:, None] * (
                    p_food_rel[None, :] ** exponent[:, None]
                )
            d_base = d_base * (p_food_rel ** exponent)
        return d_base

    def evaluate_quaids_and_superlative_indices(
        self,
        x_nominal: np.ndarray,
        w_base: np.ndarray,
        p_0: np.ndarray,
        p_1: np.ndarray,
        d_equiv: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Evaluate QUAIDS shares, Konus bounds, Fisher/Tornqvist and CV."""
        x_nominal = np.asarray(x_nominal, dtype=np.float64).reshape(-1)
        w_base = np.asarray(w_base, dtype=np.float64)
        if w_base.ndim == 1:
            w_base = w_base[None, :]
        p_0 = np.asarray(p_0, dtype=np.float64).reshape(-1)
        p_1 = np.asarray(p_1, dtype=np.float64)
        if p_1.ndim == 1:
            p_1 = p_1[None, :]
        if p_0.shape != (4,) or p_1.shape[1] != 4:
            raise ValueError("p_0 must be (4,) and p_1 must be (K,4)")
        if w_base.shape[1] != 4:
            raise ValueError("w_base must have 4 goods")

        n_hh, k_rep = x_nominal.shape[0], p_1.shape[0]
        d_equiv = np.asarray(d_equiv, dtype=np.float64)
        if d_equiv.ndim == 0:
            d_equiv = np.full((n_hh, k_rep), float(d_equiv))
        elif d_equiv.ndim == 1:
            if d_equiv.size == k_rep:
                d_equiv = np.broadcast_to(d_equiv[None, :], (n_hh, k_rep))
            elif d_equiv.size == n_hh:
                d_equiv = np.broadcast_to(d_equiv[:, None], (n_hh, k_rep))
            else:
                raise ValueError("d_equiv must be (N,), (K,) or (N,K)")
        elif d_equiv.shape != (n_hh, k_rep):
            raise ValueError("d_equiv shape must match (N_hh, K_rep)")

        ln_p1 = np.log(np.maximum(1e-8, p_1))
        quad_term = 0.5 * np.einsum(
            "km,mn,kn->k", ln_p1, self.gamma_matrix, ln_p1
        )
        ln_a_p1 = self.alpha_0 + (ln_p1 @ self.alpha) + quad_term
        b_p1 = np.exp(ln_p1 @ self.beta)

        ln_x_real = np.log(
            np.maximum(1e-6, x_nominal[:, None] / np.maximum(1e-6, d_equiv))
        ) - ln_a_p1[None, :]

        ln_p_ratio = np.log(np.maximum(1e-8, p_1 / p_0[None, :]))
        price_shift = ln_p_ratio @ self.gamma_matrix.T

        w_post = (
            w_base[:, None, :]
            + price_shift[None, :, :]
            + self.beta[None, None, :] * ln_x_real[:, :, None]
            + (
                self.lambda_q[None, None, :]
                / np.maximum(1e-8, b_p1[None, :, None])
            )
            * (ln_x_real[:, :, None] ** 2)
        )
        w_post = np.clip(w_post, 0.005, 0.985)
        w_post /= np.sum(w_post, axis=-1, keepdims=True)

        ln_p_t = 0.5 * np.sum(
            (w_base[:, None, :] + w_post) * ln_p_ratio[None, :, :], axis=-1
        )
        p_t = np.exp(ln_p_t)

        p_rel = p_1 / p_0[None, :]
        p_l = np.sum(w_base[:, None, :] * p_rel[None, :, :], axis=-1)
        p_p = 1.0 / np.sum(w_post * (1.0 / p_rel[None, :, :]), axis=-1)
        p_f = np.sqrt(p_l * p_p)

        e_sub_single = p_1 @ self.subsistence_gamma_les
        e_sub = d_equiv * e_sub_single[None, :]
        cv_h = x_nominal[:, None] * (p_t - 1.0)
        x_real_equiv = x_nominal[:, None] / np.maximum(1e-8, d_equiv * p_t)

        return {
            "w_post_quaids": w_post,
            "P_T_tornqvist": p_t,
            "P_F_fisher": p_f,
            "P_L_laspeyres": p_l,
            "P_P_paasche": p_p,
            "E_sub_floor": e_sub,
            "CV_hicksian": cv_h,
            "x_real_equivalized": x_real_equiv,
        }


__all__ = ["CostOfLivingAndEquivalenceEngine"]
