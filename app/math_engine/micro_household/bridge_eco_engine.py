"""Read-only macro-to-micro bridge for the isolated household subsystem.

The bridge consumes the signed v5.7 artifact's Wasserstein medoids and the
configured sectoral price-transmission matrix.  It never mutates macro state.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import numpy as np

from app.math_engine.micro_household.bpp_income_insurance import (
    BPPIncomeAndInsuranceEngine,
)
from app.math_engine.micro_household.buffer_stock_egm_hank_vep import (
    BufferStockEGMAndVulnerabilityEngine,
)
from app.math_engine.micro_household.coli_quaids_equivalence import (
    CostOfLivingAndEquivalenceEngine,
)

CORE_PROFILE_FIELDS = (
    "monthly_expenditure_million_vnd",
    "budget_shares_w0",
    "n_adults",
    "n_children",
)
INCOME_PROFILE_FIELDS = ("monthly_income_million_vnd",)
BALANCE_PROFILE_FIELDS = (
    "liquid_assets_million_vnd",
    "illiquid_assets_million_vnd",
    "monthly_debt_service_million_vnd",
)


class MicroHouseholdOnDemandEngine:
    def __init__(self, config_overrides: Optional[Dict[str, Any]] = None):
        cfg = dict(config_overrides or {})
        if not cfg:
            raise ValueError(
                "MicroHouseholdOnDemandEngine requires the household config"
            )
        self.cfg = cfg
        self.coli_engine = CostOfLivingAndEquivalenceEngine(cfg)
        self.bpp_engine = BPPIncomeAndInsuranceEngine(
            price_transitory_pass_through=float(
                cfg["bpp_price_transitory_pass_through"]
            )
        )
        self.egm_engine = BufferStockEGMAndVulnerabilityEngine(
            n_grid=int(cfg["egm_n_grid"]),
            a_max=float(cfg["egm_a_max"]),
            mpc_by_regime=tuple(cfg["mpc_by_regime"]),
            liquid_buffer_threshold_months=float(
                cfg["liquid_buffer_threshold_months"]
            ),
            mdsr_threshold=float(cfg["mdsr_threshold"]),
            vep_vulnerable_threshold=float(cfg["vep_vulnerable_threshold"]),
            vep_high_fragility_threshold=float(
                cfg["vep_high_fragility_threshold"]
            ),
        )
        self.M_price = np.asarray(cfg["m_price_transmission"], dtype=np.float64)
        if self.M_price.shape != (4, 5):
            raise ValueError("M_price_transmission must be 4x5")
        self.price_projection_clip_ln = tuple(
            float(value) for value in cfg["price_projection_clip_ln"]
        )
        self.poverty_line_z = float(
            cfg["poverty_line_z_monthly_million_vnd"]
        )

    # ------------------------------------------------------------------
    # Macro payload and prices
    # ------------------------------------------------------------------
    @staticmethod
    def _macro_payload(
        eco_engine_payload: Dict[str, Any],
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, bool, bool]:
        if "F_medoids_terminal" not in eco_engine_payload:
            raise ValueError("eco payload thiếu F_medoids_terminal")
        if "cluster_weights_q" not in eco_engine_payload:
            raise ValueError("eco payload thiếu cluster_weights_q")
        factors = np.asarray(
            eco_engine_payload["F_medoids_terminal"], dtype=np.float64
        )
        weights = np.asarray(
            eco_engine_payload["cluster_weights_q"], dtype=np.float64
        ).reshape(-1)
        if factors.ndim != 2 or factors.shape[1] != 5:
            raise ValueError("F_medoids_terminal must have shape (K, 5)")
        if factors.shape[0] != weights.size:
            raise ValueError("F_medoids_terminal and cluster_weights_q mismatch")
        weights = weights / max(1e-12, float(np.sum(weights)))

        policy_missing = "optimal_policy_u" not in eco_engine_payload
        policy = np.asarray(
            eco_engine_payload.get("optimal_policy_u", [0.0, 0.0, 0.0]),
            dtype=np.float64,
        ).reshape(-1)
        if policy.size != 3:
            raise ValueError("optimal_policy_u must have 3 entries")
        stress_missing = "pi_stress_regime2" not in eco_engine_payload
        stress = float(eco_engine_payload.get("pi_stress_regime2", 0.0))
        return (
            factors,
            weights,
            policy,
            max(0.0, min(1.0, stress)),
            policy_missing,
            stress_missing,
        )

    def _project_sectoral_prices_from_macro_medoids(
        self,
        F_medoids_terminal: np.ndarray,
        optimal_policy_u: np.ndarray,
        pi_stress_regime2: float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        p_0 = np.ones(4, dtype=np.float64)
        policy_dampener = float(self.cfg["policy_dampener_per_unit"]) * float(
            np.sum(optimal_policy_u)
        )
        regime_markup = float(self.cfg["regime_markup"]) * float(
            pi_stress_regime2
        )
        delta_ln_p = (
            F_medoids_terminal @ self.M_price.T
            + policy_dampener
            + regime_markup
        )
        p_1 = p_0[None, :] * np.exp(
            np.clip(delta_ln_p, *self.price_projection_clip_ln)
        )
        return p_0, p_1

    @staticmethod
    def _missing(profile: Dict[str, Any], fields: Tuple[str, ...]) -> list[str]:
        return [
            field
            for field in fields
            if profile.get(field) is None
        ]

    def _equivalence(self, profile: Dict[str, Any], p_0: np.ndarray, p_1: np.ndarray) -> np.ndarray:
        method = str(profile.get("equiv_method") or "oecd_modified")
        return self.coli_engine.compute_equivalence_scale(
            np.array([float(profile["n_adults"])]),
            np.array([float(profile["n_children"])]),
            method=method,
            price_relatives=(p_1 / p_0[None, :])[None, :, :],
        )

    def _market_prices(
        self,
        factors: np.ndarray,
        weights: np.ndarray,
        stress: float,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        erp = np.clip(
            float(self.cfg["erp_base"])
            + float(self.cfg["erp_factor_growth"]) * factors[:, 3]
            + float(self.cfg["erp_factor_liquidity"]) * factors[:, 1],
            float(self.cfg["erp_bounds"][0]),
            float(self.cfg["erp_bounds"][1]),
        )
        variance = np.clip(
            float(self.cfg["return_variance_base"])
            + float(self.cfg["return_variance_stress"]) * stress
            + float(self.cfg["return_variance_global"]) * np.abs(factors[:, 0]),
            float(self.cfg["return_variance_bounds"][0]),
            float(self.cfg["return_variance_bounds"][1]),
        )
        return erp, variance, factors, weights, np.asarray([stress])

    # ------------------------------------------------------------------
    # Single household
    # ------------------------------------------------------------------
    def _evaluate_blocks(
        self,
        individual_profile: Dict[str, Any],
        eco_engine_payload: Dict[str, Any],
        *,
        require_complete: bool,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        factors, weights, policy, stress, policy_missing, stress_missing = (
            self._macro_payload(eco_engine_payload)
        )
        missing_core = self._missing(individual_profile, CORE_PROFILE_FIELDS)
        missing_income = self._missing(individual_profile, INCOME_PROFILE_FIELDS)
        missing_balance = self._missing(
            individual_profile, BALANCE_PROFILE_FIELDS
        )
        missing_all = missing_core + missing_income + missing_balance
        if require_complete and missing_all:
            raise ValueError(
                "household profile thiếu trường bắt buộc: "
                + ", ".join(missing_all)
            )

        result: Dict[str, Any] = {
            "execution_mode": "single_individual",
            "missing_inputs": missing_all,
            "assumptions": [
                "Danh mục 4 nhóm hàng: thực phẩm, nhà ở/tiện ích, "
                "năng lượng/đi lại, dịch vụ tùy ý.",
                "Giá kịch bản được chiếu từ 5 nhân tố vĩ mô qua "
                "M_price_transmission trong config/micro_household.json.",
                str(self.cfg.get("source_note") or ""),
            ],
        }
        if policy_missing:
            result["assumptions"].append(
                "Artifact v5.7 không công bố optimal_policy_u; dùng vector 0 "
                "và ghi rõ đây là giả định."
            )
        if stress_missing:
            result["assumptions"].append(
                "Artifact không có pi_stress_regime2; dùng 0 và ghi rõ giả định."
            )
        if missing_core:
            result["cost_of_living_evaluation"] = None
            result["income_and_buffer_stock_security"] = None
            result["distributional"] = None
            result["latency_ms"] = round(
                (time.perf_counter() - started) * 1000.0, 4
            )
            return result

        p_0, p_1 = self._project_sectoral_prices_from_macro_medoids(
            factors, policy, stress
        )
        x_nom = np.array(
            [float(individual_profile["monthly_expenditure_million_vnd"])],
            dtype=np.float64,
        )
        w_base = np.asarray(
            individual_profile["budget_shares_w0"], dtype=np.float64
        )[None, :]
        d_equiv = self._equivalence(individual_profile, p_0, p_1)
        coli = self.coli_engine.evaluate_quaids_and_superlative_indices(
            x_nom, w_base, p_0, p_1, d_equiv
        )
        result["cost_of_living_evaluation"] = {
            "expected_tornqvist_index_P_T": float(
                coli["P_T_tornqvist"][0] @ weights
            ),
            "expected_fisher_ideal_index_P_F": float(
                coli["P_F_fisher"][0] @ weights
            ),
            "expected_laspeyres_upper_bound_P_L": float(
                coli["P_L_laspeyres"][0] @ weights
            ),
            "expected_paasche_lower_bound_P_P": float(
                coli["P_P_paasche"][0] @ weights
            ),
            "expected_subsistence_floor_million_vnd": float(
                coli["E_sub_floor"][0] @ weights
            ),
            "expected_hicksian_cv_million_vnd": float(
                coli["CV_hicksian"][0] @ weights
            ),
            "expected_real_equivalized_consumption_million_vnd": float(
                coli["x_real_equivalized"][0] @ weights
            ),
            "post_shock_quaids_shares": (
                coli["w_post_quaids"][0].T @ weights
            ).tolist(),
        }

        bpp_result = None
        if not missing_income:
            y_nom = np.array(
                [float(individual_profile["monthly_income_million_vnd"])],
                dtype=np.float64,
            )
            beta_perm = np.asarray(
                individual_profile.get("beta_perm_macro")
                or self.cfg["default_beta_perm_macro"],
                dtype=np.float64,
            )
            beta_trans = np.asarray(
                individual_profile.get("beta_trans_macro")
                or self.cfg["default_beta_trans_macro"],
                dtype=np.float64,
            )
            psi = np.array(
                [
                    float(
                        individual_profile.get("psi_perm_pass")
                        if individual_profile.get("psi_perm_pass") is not None
                        else self.cfg["default_psi_perm_pass"]
                    )
                ],
                dtype=np.float64,
            )
            phi = np.array(
                [
                    float(
                        individual_profile.get("phi_trans_pass")
                        if individual_profile.get("phi_trans_pass") is not None
                        else self.cfg["default_phi_trans_pass"]
                    )
                ],
                dtype=np.float64,
            )
            bpp_result = self.bpp_engine.project_conditional_income_and_consumption_shocks(
                y_nom,
                coli["P_T_tornqvist"],
                factors,
                beta_perm,
                beta_trans,
                psi,
                phi,
            )
            result["assumptions"].append(
                "Hệ số truyền dẫn thu nhập BPP lấy từ hồ sơ nếu có, "
                "ngược lại dùng tham số hiệu chuẩn trong config."
            )

        hank_result = None
        merton_share = None
        if not missing_balance:
            erp, variance, _, _, _ = self._market_prices(
                factors, weights, stress
            )
            rho = np.array(
                [
                    float(
                        individual_profile.get("crra_rho")
                        if individual_profile.get("crra_rho") is not None
                        else self.cfg["default_crra_rho"]
                    )
                ],
                dtype=np.float64,
            )
            if individual_profile.get("equity_risk_premium") is not None:
                erp = np.full_like(
                    erp, float(individual_profile["equity_risk_premium"])
                )
            if individual_profile.get("return_variance") is not None:
                variance = np.full_like(
                    variance, float(individual_profile["return_variance"])
                )
            hank_result = self.egm_engine.classify_two_asset_hank_and_merton_share(
                np.array(
                    [float(individual_profile["liquid_assets_million_vnd"])]
                ),
                np.array(
                    [float(individual_profile["illiquid_assets_million_vnd"])]
                ),
                coli["E_sub_floor"],
                np.array(
                    [float(individual_profile["monthly_debt_service_million_vnd"])]
                ),
                x_nom,
                rho,
                erp,
                variance,
            )
            merton_share = hank_result["merton_optimal_risky_share"]

        if hank_result is not None:
            modal_regime = int(
                np.round(float(hank_result["hank_regime_code"][0] @ weights))
            )
            regime_labels = {
                0: "Unconstrained_Buffer_Stock_Saver",
                1: "Wealthy_Hand_to_Mouth_W_HtM",
                2: "Poor_Hand_to_Mouth_P_HtM",
            }
            x_real = coli["x_real_equivalized"]
            sigma_c = np.full_like(
                x_real,
                float(self.cfg["sigma_c_base"])
                + float(self.cfg["sigma_c_stress"]) * stress,
            )
            vep = self.egm_engine.compute_fgt_and_3step_fgls_vep(
                x_real, self.poverty_line_z, sigma_c, weights
            )
            income_block = {
                "expected_real_disposable_income_million_vnd": (
                    float(bpp_result["y_post_real"][0] @ weights)
                    if bpp_result is not None
                    else None
                ),
                "carroll_toche_target_buffer_ratio": float(
                    self.egm_engine.compute_carroll_toche_target_buffer(
                        rho,
                        np.array([float(self.cfg["egm_R_gross"])]),
                        np.array([float(self.cfg["egm_beta_disc"])]),
                        np.array([float(self.cfg["egm_gamma_growth"])]),
                        np.array(
                            [
                                float(self.cfg["carroll_toche_unemp_prob_base"])
                                + float(
                                    self.cfg["carroll_toche_unemp_prob_stress"]
                                )
                                * stress
                            ]
                        ),
                    )["m_target_ratio"][0]
                ),
                "carroll_toche_units": (
                    "dimensionless target market-resources ratio "
                    "(m_check^e, not months)"
                ),
                "actual_liquid_buffer_months": float(
                    hank_result["liquid_buffer_months"][0] @ weights
                ),
                "modified_debt_service_ratio_mdsr": float(
                    hank_result["mdsr_ratio"][0] @ weights
                ),
                "hank_classification": regime_labels.get(modal_regime, "Unknown"),
                "expected_quarterly_mpc": float(
                    hank_result["mpc_quarterly"][0] @ weights
                ),
                "merton_optimal_risky_asset_share": float(
                    merton_share[0] @ weights
                ),
                "egm_borrowing_kink_threshold_m": float(
                    self.egm_engine.solve_egm_policy_function(
                        rho=float(individual_profile.get("crra_rho") or self.cfg["default_crra_rho"]),
                        R_gross=float(self.cfg["egm_R_gross"]),
                        beta_disc=float(self.cfg["egm_beta_disc"]),
                        Gamma_growth=float(self.cfg["egm_gamma_growth"]),
                        sigma_xi=float(self.cfg["egm_sigma_xi"]),
                        b_borrow_limit=float(self.cfg["egm_borrow_limit"]),
                        max_iter=int(self.cfg["egm_max_iter"]),
                        tol=float(self.cfg["egm_tol"]),
                    )["m_kink_threshold"]
                ),
                "expected_vulnerability_to_poverty_vep": float(
                    vep["expected_vep_per_hh"][0]
                ),
                "tail_var99_vulnerability_vep": float(
                    vep["vep_var99_per_hh"][0]
                ),
            }
            result["income_and_buffer_stock_security"] = income_block
            result["distributional"] = {
                "aggregate_fgt_p0": vep["aggregate_fgt_p0"],
                "aggregate_fgt_p1": vep["aggregate_fgt_p1"],
                "aggregate_fgt_p2": vep["aggregate_fgt_p2"],
            }
        elif bpp_result is not None:
            result["income_and_buffer_stock_security"] = {
                "expected_real_disposable_income_million_vnd": float(
                    bpp_result["y_post_real"][0] @ weights
                ),
            }
            result["distributional"] = None
        else:
            result["income_and_buffer_stock_security"] = None
            result["distributional"] = None

        result["latency_ms"] = round(
            (time.perf_counter() - started) * 1000.0, 4
        )
        return result

    def evaluate_single_household(
        self,
        individual_profile: Dict[str, Any],
        eco_engine_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._evaluate_blocks(
            individual_profile, eco_engine_payload, require_complete=True
        )

    def evaluate_single_household_available(
        self,
        individual_profile: Dict[str, Any],
        eco_engine_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate whichever blocks have explicit user inputs."""
        return self._evaluate_blocks(
            individual_profile, eco_engine_payload, require_complete=False
        )

    # ------------------------------------------------------------------
    # Population zero-copy
    # ------------------------------------------------------------------
    def evaluate_population_zero_copy(
        self,
        arrow_ipc_path: str,
        eco_engine_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        import pyarrow as pa
        import pyarrow.ipc as ipc

        started = time.perf_counter()
        factors, weights, policy, stress, _, _ = self._macro_payload(
            eco_engine_payload
        )
        with pa.memory_map(arrow_ipc_path, "r") as mmap_source:
            table = ipc.open_file(mmap_source).read_all()

        x_nom = table["x_nominal"].chunk(0).to_numpy(zero_copy_only=True)
        n_adults = table["n_adults"].chunk(0).to_numpy(zero_copy_only=True)
        n_children = table["n_children"].chunk(0).to_numpy(zero_copy_only=True)
        b_liquid = table["b_liquid"].chunk(0).to_numpy(zero_copy_only=True)
        a_illiquid = table["a_illiquid"].chunk(0).to_numpy(zero_copy_only=True)
        debt_service = (
            table["debt_service"].chunk(0).to_numpy(zero_copy_only=True)
        )
        rho_crra = table["rho_crra"].chunk(0).to_numpy(zero_copy_only=True)
        w_base = np.column_stack(
            (
                table["w_food"].chunk(0).to_numpy(zero_copy_only=True),
                table["w_housing"].chunk(0).to_numpy(zero_copy_only=True),
                table["w_energy"].chunk(0).to_numpy(zero_copy_only=True),
                table["w_other"].chunk(0).to_numpy(zero_copy_only=True),
            )
        )

        p_0, p_1 = self._project_sectoral_prices_from_macro_medoids(
            factors, policy, stress
        )
        d_equiv = self.coli_engine.compute_equivalence_scale(
            n_adults,
            n_children,
            method="oecd_modified",
            price_relatives=(p_1 / p_0[None, :])[None, :, :],
        )
        coli = self.coli_engine.evaluate_quaids_and_superlative_indices(
            x_nom, w_base, p_0, p_1, d_equiv
        )
        erp, variance, _, _, _ = self._market_prices(factors, weights, stress)
        hank = self.egm_engine.classify_two_asset_hank_and_merton_share(
            b_liquid,
            a_illiquid,
            coli["E_sub_floor"],
            debt_service,
            x_nom,
            rho_crra,
            erp,
            variance,
        )
        sigma_c = np.full_like(
            coli["x_real_equivalized"],
            float(self.cfg["sigma_c_base"])
            + float(self.cfg["sigma_c_stress"]) * stress,
        )
        vep = self.egm_engine.compute_fgt_and_3step_fgls_vep(
            coli["x_real_equivalized"],
            self.poverty_line_z,
            sigma_c,
            weights,
        )
        vep_exp = vep["expected_vep_per_hh"]
        q10, q25, q50, q75, q90 = np.percentile(
            vep_exp, [10, 25, 50, 75, 90]
        )
        iqr = max(1e-6, q75 - q25)
        kelley_skew = ((q90 - q50) - (q50 - q10)) / max(1e-6, q90 - q10)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "execution_mode": "population_cross_section",
            "n_households_evaluated": int(x_nom.shape[0]),
            "latency_ms": round(elapsed_ms, 4),
            "population_aggregates": {
                "fgt_p0_headcount_rate": vep["aggregate_fgt_p0"],
                "fgt_p1_poverty_gap": vep["aggregate_fgt_p1"],
                "fgt_p2_squared_severity": vep["aggregate_fgt_p2"],
                "vulnerable_household_share_vep_gt_029": vep[
                    "aggregate_vulnerable_share_gt_029"
                ],
                "high_fragility_share_vep_gt_050": vep[
                    "aggregate_high_fragility_share_gt_050"
                ],
                "poor_htm_share": float(
                    np.mean((hank["hank_regime_code"] == 2) @ weights)
                ),
                "wealthy_htm_share": float(
                    np.mean((hank["hank_regime_code"] == 1) @ weights)
                ),
                "population_weighted_quarterly_mpc": float(
                    np.mean(hank["mpc_quarterly"] @ weights)
                ),
            },
            "distributional_pqr_quartet_feedback": {
                "median_vep": float(q50),
                "iqr_dispersion": float(iqr),
                "kelley_robust_skewness": float(kelley_skew),
                "upper_tail_q90_stress_share": float(
                    np.mean(
                        vep_exp >= self.egm_engine.vep_high_fragility_threshold
                    )
                ),
            },
        }


__all__ = ["MicroHouseholdOnDemandEngine"]
