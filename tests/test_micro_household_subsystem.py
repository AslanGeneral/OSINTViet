#!/usr/bin/env python3
"""Deterministic tests for the isolated micro household subsystem.

Run:
    .venv/bin/python tests/test_micro_household_subsystem.py
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

from app.math_engine.micro_household import invoke_micro_household_subsystem
from app.math_engine.micro_household.bpp_income_insurance import (
    BPPIncomeAndInsuranceEngine,
)
from app.math_engine.micro_household.buffer_stock_egm_hank_vep import (
    BufferStockEGMAndVulnerabilityEngine,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "micro_household.json"


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _payload(seed: int = 3, stress: float = 0.35) -> dict:
    rng = np.random.default_rng(seed)
    return {
        "F_medoids_terminal": rng.normal(0.0, 0.25, (50, 5)).tolist(),
        "cluster_weights_q": (np.ones(50) / 50.0).tolist(),
        "optimal_policy_u": [0.0, 0.0, 0.0],
        "pi_stress_regime2": stress,
        "macro_snapshot": {"artifact_sha256": "test-sha"},
    }


def _profile() -> dict:
    return {
        "monthly_income_million_vnd": 22.0,
        "monthly_expenditure_million_vnd": 18.0,
        "budget_shares_w0": [0.45, 0.25, 0.12, 0.18],
        "n_adults": 2,
        "n_children": 1,
        "liquid_assets_million_vnd": 18.0,
        "illiquid_assets_million_vnd": 450.0,
        "monthly_debt_service_million_vnd": 3.2,
        "crra_rho": 2.5,
    }


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


class IsolationTests(unittest.TestCase):
    def test_package_import_does_not_load_heavy_modules(self) -> None:
        code = (
            "import sys; import app.math_engine.micro_household; "
            "assert 'numpy' not in sys.modules; "
            "assert 'scipy' not in sys.modules; "
            "assert 'pyarrow' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class MicroBridgeTests(unittest.TestCase):
    def test_full_profile_produces_all_blocks(self) -> None:
        output = invoke_micro_household_subsystem(
            mode="single_individual",
            eco_engine_payload=_payload(),
            individual_profile=_profile(),
            config_overrides=_config(),
        )
        self.assertEqual(output["execution_mode"], "single_individual")
        self.assertEqual(output["missing_inputs"], [])

        coli = output["cost_of_living_evaluation"]
        for key in (
            "expected_tornqvist_index_P_T",
            "expected_fisher_ideal_index_P_F",
            "expected_laspeyres_upper_bound_P_L",
            "expected_paasche_lower_bound_P_P",
            "expected_subsistence_floor_million_vnd",
            "expected_hicksian_cv_million_vnd",
            "expected_real_equivalized_consumption_million_vnd",
        ):
            self.assertTrue(_finite(coli[key]), key)
        self.assertLessEqual(
            coli["expected_paasche_lower_bound_P_P"],
            coli["expected_tornqvist_index_P_T"],
        )
        self.assertLessEqual(
            coli["expected_tornqvist_index_P_T"],
            coli["expected_laspeyres_upper_bound_P_L"],
        )
        self.assertLess(
            abs(
                coli["expected_tornqvist_index_P_T"]
                - coli["expected_fisher_ideal_index_P_F"]
            ),
            0.005,
        )
        shares = coli["post_shock_quaids_shares"]
        self.assertEqual(len(shares), 4)
        self.assertAlmostEqual(sum(shares), 1.0, places=10)
        self.assertTrue(all(share >= 0.005 for share in shares))

        income = output["income_and_buffer_stock_security"]
        for key in (
            "expected_real_disposable_income_million_vnd",
            "carroll_toche_target_buffer_ratio",
            "actual_liquid_buffer_months",
            "modified_debt_service_ratio_mdsr",
            "expected_quarterly_mpc",
            "merton_optimal_risky_asset_share",
            "egm_borrowing_kink_threshold_m",
            "expected_vulnerability_to_poverty_vep",
            "tail_var99_vulnerability_vep",
        ):
            self.assertTrue(_finite(income[key]), key)
        self.assertGreater(income["carroll_toche_target_buffer_ratio"], 1.0)
        self.assertIn(
            income["hank_classification"],
            {
                "Unconstrained_Buffer_Stock_Saver",
                "Wealthy_Hand_to_Mouth_W_HtM",
                "Poor_Hand_to_Mouth_P_HtM",
            },
        )
        self.assertGreaterEqual(income["expected_vulnerability_to_poverty_vep"], 0.0)
        self.assertLessEqual(income["expected_vulnerability_to_poverty_vep"], 1.0)
        self.assertNotIn("carroll_toche_target_buffer_months", income)
        self.assertIn("not months", income["carroll_toche_units"])

        distributional = output["distributional"]
        self.assertTrue(_finite(distributional["aggregate_fgt_p0"]))
        self.assertTrue(_finite(distributional["aggregate_fgt_p1"]))
        self.assertTrue(_finite(distributional["aggregate_fgt_p2"]))
        self.assertGreaterEqual(distributional["aggregate_fgt_p0"], 0.0)
        self.assertLessEqual(distributional["aggregate_fgt_p0"], 1.0)

    def test_missing_inputs_are_not_estimated(self) -> None:
        output = invoke_micro_household_subsystem(
            mode="single_individual_available",
            eco_engine_payload=_payload(),
            individual_profile={"monthly_income_million_vnd": 20.0},
            config_overrides=_config(),
        )
        self.assertIsNone(output["cost_of_living_evaluation"])
        self.assertIsNone(output["income_and_buffer_stock_security"])
        self.assertIsNone(output["distributional"])
        self.assertIn("monthly_expenditure_million_vnd", output["missing_inputs"])
        self.assertIn("budget_shares_w0", output["missing_inputs"])
        self.assertIn("n_adults", output["missing_inputs"])
        self.assertIn("n_children", output["missing_inputs"])
        self.assertNotIn("monthly_income_million_vnd", output["missing_inputs"])

    def test_cost_of_living_without_balance_sheet_is_available(self) -> None:
        profile = {
            "monthly_income_million_vnd": 22.0,
            "monthly_expenditure_million_vnd": 18.0,
            "budget_shares_w0": [0.45, 0.25, 0.12, 0.18],
            "n_adults": 2,
            "n_children": 1,
        }
        output = invoke_micro_household_subsystem(
            mode="single_individual_available",
            eco_engine_payload=_payload(),
            individual_profile=profile,
            config_overrides=_config(),
        )
        self.assertIsNotNone(output["cost_of_living_evaluation"])
        income = output["income_and_buffer_stock_security"]
        self.assertIsNotNone(income)
        self.assertIn(
            "expected_real_disposable_income_million_vnd", income
        )
        self.assertNotIn("hank_classification", income)
        self.assertIsNone(output["distributional"])
        self.assertTrue(output["missing_inputs"])

    def test_bad_macro_payload_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            invoke_micro_household_subsystem(
                mode="single_individual",
                eco_engine_payload={"cluster_weights_q": [1.0]},
                individual_profile=_profile(),
                config_overrides=_config(),
            )


class EquationTests(unittest.TestCase):
    def test_carroll_toche_labels_target_ratio(self) -> None:
        cfg = _config()
        engine = BufferStockEGMAndVulnerabilityEngine(
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
        result = engine.compute_carroll_toche_target_buffer(
            np.array([2.5]),
            np.array([1.01]),
            np.array([0.985]),
            np.array([1.005]),
            np.array([0.05]),
        )
        self.assertIn("m_target_ratio", result)
        self.assertNotIn("m_target_months", result)
        self.assertTrue(_finite(result["m_target_ratio"][0]))
        self.assertGreater(result["m_target_ratio"][0], 1.0)

    def test_egm_policy_is_monotone(self) -> None:
        cfg = _config()
        engine = BufferStockEGMAndVulnerabilityEngine(
            n_grid=80,
            a_max=24.0,
            mpc_by_regime=(0.055, 0.28, 0.52),
            liquid_buffer_threshold_months=3.0,
            mdsr_threshold=0.30,
            vep_vulnerable_threshold=float(cfg["vep_vulnerable_threshold"]),
            vep_high_fragility_threshold=float(
                cfg["vep_high_fragility_threshold"]
            ),
        )
        result = engine.solve_egm_policy_function(
            rho=2.5,
            R_gross=1.01,
            beta_disc=0.985,
            Gamma_growth=1.005,
            sigma_xi=0.15,
            b_borrow_limit=0.0,
        )
        m = result["m_grid_endogenous"]
        c = result["c_grid_endogenous"]
        self.assertTrue(np.all(np.diff(m) > 0.0))
        self.assertTrue(np.all(np.diff(c) >= 0.0))
        slope = np.diff(c) / np.diff(m)
        self.assertTrue(np.all(slope[1:] > 0.0))
        self.assertTrue(np.all(slope[1:] < 1.0))

    def test_bpp_gmm_recovers_synthetic_variances(self) -> None:
        rng = np.random.default_rng(9)
        n_hh, periods = 20000, 6
        sigma_v, sigma_u = 0.04, 0.06
        permanent = rng.normal(0.0, sigma_v, (n_hh, periods))
        transitory = rng.normal(0.0, sigma_u, (n_hh, periods))
        delta_y = (
            permanent[:, 1:]
            + transitory[:, 1:]
            - transitory[:, :-1]
        )
        result = BPPIncomeAndInsuranceEngine.estimate_bpp_gmm_variances(delta_y)
        recovered_v = float(result["sigma_v_permanent_sq"][0])
        recovered_u = float(result["sigma_u_transitory_sq"][0])
        self.assertLess(abs(recovered_v - sigma_v**2) / sigma_v**2, 0.05)
        self.assertLess(abs(recovered_u - sigma_u**2) / sigma_u**2, 0.05)
        self.assertEqual(
            result["sigma_v_permanent_sq_by_period"].shape,
            (delta_y.shape[1] - 2,),
        )
        self.assertEqual(
            result["sigma_u_transitory_sq_by_period"].shape,
            (delta_y.shape[1] - 2,),
        )

    def test_bpp_rejects_short_panel(self) -> None:
        with self.assertRaises(ValueError):
            BPPIncomeAndInsuranceEngine.estimate_bpp_gmm_variances(
                np.zeros((10, 2))
            )


class PopulationTests(unittest.TestCase):
    def test_zero_copy_population_evaluation(self) -> None:
        n_hh = 1000
        rng = np.random.default_rng(11)
        table = pa.table(
            {
                "x_nominal": pa.array(
                    rng.uniform(8.0, 40.0, n_hh), type=pa.float64()
                ),
                "n_adults": pa.array(
                    rng.integers(1, 5, n_hh).astype(np.float64),
                    type=pa.float64(),
                ),
                "n_children": pa.array(
                    rng.integers(0, 4, n_hh).astype(np.float64),
                    type=pa.float64(),
                ),
                "b_liquid": pa.array(
                    rng.uniform(0.0, 80.0, n_hh), type=pa.float64()
                ),
                "a_illiquid": pa.array(
                    rng.uniform(0.0, 1200.0, n_hh), type=pa.float64()
                ),
                "debt_service": pa.array(
                    rng.uniform(0.0, 8.0, n_hh), type=pa.float64()
                ),
                "rho_crra": pa.array(
                    np.full(n_hh, 2.5), type=pa.float64()
                ),
                "w_food": pa.array(
                    np.full(n_hh, 0.45), type=pa.float64()
                ),
                "w_housing": pa.array(
                    np.full(n_hh, 0.25), type=pa.float64()
                ),
                "w_energy": pa.array(
                    np.full(n_hh, 0.12), type=pa.float64()
                ),
                "w_other": pa.array(
                    np.full(n_hh, 0.18), type=pa.float64()
                ),
            }
        )
        with tempfile.TemporaryDirectory(prefix="micro_arrow_") as tmp:
            path = Path(tmp) / "households.arrow"
            with pa.OSFile(str(path), "wb") as sink:
                with ipc.new_file(sink, table.schema) as writer:
                    writer.write_table(table)
            output = invoke_micro_household_subsystem(
                mode="population_cross_section",
                eco_engine_payload=_payload(seed=5),
                arrow_ipc_population_path=str(path),
                config_overrides=_config(),
            )
        self.assertEqual(output["execution_mode"], "population_cross_section")
        self.assertEqual(output["n_households_evaluated"], n_hh)
        aggregates = output["population_aggregates"]
        for key in (
            "fgt_p0_headcount_rate",
            "fgt_p1_poverty_gap",
            "fgt_p2_squared_severity",
            "vulnerable_household_share_vep_gt_029",
            "high_fragility_share_vep_gt_050",
            "poor_htm_share",
            "wealthy_htm_share",
            "population_weighted_quarterly_mpc",
        ):
            self.assertTrue(_finite(aggregates[key]), key)
        quartet = output["distributional_pqr_quartet_feedback"]
        for key in (
            "median_vep",
            "iqr_dispersion",
            "kelley_robust_skewness",
            "upper_tail_q90_stress_share",
        ):
            self.assertTrue(_finite(quartet[key]), key)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for case in (
        IsolationTests,
        MicroBridgeTests,
        EquationTests,
        PopulationTests,
    ):
        suite.addTests(loader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
