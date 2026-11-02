#!/usr/bin/env python3
"""Deterministic contract tests for the CLIO-CUSP 8192 v4.0 kernel.

Run:
    .venv/bin/python tests/test_clio_cusp_kernel.py

The core suite needs only NumPy and SciPy.  Tests that exercise the torch,
polars, igraph, and leidenalg adapters are skipped when those optional
packages are not installed; install the project extras to run them.
"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import scipy.sparse as sp

import clio_cusp_8192_v4_kernel as kernel


def _available(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


HAS_TORCH = _available("torch")
HAS_POLARS = _available("polars")
HAS_IGRAPH = _available("igraph") and _available("leidenalg")


def _hilbert_spec_reference(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bit-masking reference from the specification, independent of the kernel."""
    x_cur = np.asarray(x, dtype=np.int64).copy()
    y_cur = np.asarray(y, dtype=np.int64).copy()
    distance = np.zeros_like(x_cur)
    for exponent in range(kernel.M_BITS - 1, -1, -1):
        scale = 1 << exponent
        rx = (x_cur & scale) > 0
        ry = (y_cur & scale) > 0
        distance += (scale * scale) * ((3 * rx.astype(np.int64)) ^ ry.astype(np.int64))
        x_cur = x_cur & (scale - 1)
        y_cur = y_cur & (scale - 1)
        rotate = ~ry
        flip = rotate & rx
        x_cur = np.where(flip, (scale - 1) - x_cur, x_cur)
        y_cur = np.where(flip, (scale - 1) - y_cur, y_cur)
        x_cur, y_cur = np.where(rotate, y_cur, x_cur), np.where(rotate, x_cur, y_cur)
    return distance


def _brute_force_virality(parents: list[int]) -> float:
    n = len(parents)
    if n <= 1:
        return 0.0
    adjacency = [[] for _ in range(n)]
    for node in range(1, n):
        adjacency[node].append(parents[node])
        adjacency[parents[node]].append(node)
    total = 0
    for source in range(n):
        distances = [-1] * n
        distances[source] = 0
        queue = [source]
        for node in queue:
            for neighbour in adjacency[node]:
                if distances[neighbour] < 0:
                    distances[neighbour] = distances[node] + 1
                    queue.append(neighbour)
        total += sum(distances)
    return total / (n * (n - 1))


class TestLockedConstants(unittest.TestCase):
    def test_constants_match_specification(self) -> None:
        self.assertEqual(kernel.R_REPLICATES, 9)
        self.assertEqual(kernel.M_PARTICLES, 8192)
        self.assertEqual(kernel.M_BITS, 13)
        self.assertEqual(kernel.H_HORIZON, 128)
        self.assertEqual(kernel.U_CLIP_EPS, 2.0**-14)
        self.assertEqual(kernel.AGE_YOUTH_BULGE, (20, 29))
        self.assertEqual(kernel.NU_STAR_LIMIT, 2.0)
        self.assertEqual(kernel.LAMBDA_CPT, 2.25)
        self.assertEqual(kernel.ALPHA_CPT, 0.88)
        self.assertEqual(kernel.BETA_VULN, 0.95)
        self.assertEqual(kernel.K_EPSTEIN, 2.30)
        self.assertEqual(kernel.T_ACT_EPSTEIN, 0.10)
        self.assertEqual(kernel.SIGMA_C_GRAN, 0.1222)
        self.assertEqual(kernel.F_MIL_OPT, 2.0)
        self.assertEqual(kernel.BETA_TEMP, 0.113)
        self.assertEqual(kernel.BETA_PM25, 0.029)
        self.assertEqual(kernel.GAMMA_RAIN, 0.25)
        self.assertEqual(kernel.T_NAE_CELSIUS, 35.0)


class TestStructuralVirality(unittest.TestCase):
    def test_random_trees_match_brute_force(self) -> None:
        rng = random.Random(0)
        for n in range(1, 26):
            for _ in range(20):
                parents = [-1] + [rng.randrange(i) for i in range(1, n)]
                self.assertAlmostEqual(
                    kernel.compute_structural_virality_o_n(parents),
                    _brute_force_virality(parents),
                    places=12,
                )

    def test_star_and_path_closed_forms(self) -> None:
        # Structural virality is the mean pairwise distance of the tree.
        self.assertAlmostEqual(
            kernel.compute_structural_virality_o_n([-1] + [0] * 31),
            2 * 31 / 32,
        )
        self.assertAlmostEqual(
            kernel.compute_structural_virality_o_n([-1] + list(range(31))),
            (32 + 1) / 3,
        )

    def test_rejects_invalid_trees(self) -> None:
        with self.assertRaises(ValueError):
            kernel.compute_structural_virality_o_n([0])
        with self.assertRaises(ValueError):
            kernel.compute_structural_virality_o_n([-1, -1])
        with self.assertRaises(ValueError):
            kernel.compute_structural_virality_o_n([-1, 2])


class TestHilbert(unittest.TestCase):
    def test_matches_specification_reference(self) -> None:
        xs, ys = np.meshgrid(np.arange(256), np.arange(256), indexing="ij")
        xs, ys = xs.ravel(), ys.ravel()
        np.testing.assert_array_equal(
            kernel.xy_to_hilbert_13bit(xs, ys), _hilbert_spec_reference(xs, ys)
        )
        rng = np.random.default_rng(1)
        x = rng.integers(0, 8192, size=100_000)
        y = rng.integers(0, 8192, size=100_000)
        np.testing.assert_array_equal(
            kernel.xy_to_hilbert_13bit(x, y), _hilbert_spec_reference(x, y)
        )

    def test_sampled_bijection(self) -> None:
        import os

        # Every fourth 128-row block by default; CLIO_FULL_HILBERT=1 runs all
        # 8192 rows, which is the complete 2^26-point bijection check.
        step = 128 if os.environ.get("CLIO_FULL_HILBERT") == "1" else 512
        seen = np.zeros(1 << 26, dtype=np.bool_)
        blocks = 0
        for x0 in range(0, 8192, step):
            width = min(128, 8192 - x0)
            x = np.repeat(np.arange(x0, x0 + width), 8192)
            y = np.tile(np.arange(8192), width)
            distance = kernel.xy_to_hilbert_13bit(x, y)
            self.assertFalse(seen[distance].any(), f"collision in block {x0}")
            seen[distance] = True
            blocks += width
        self.assertEqual(int(seen.sum()), blocks * 8192)

    def test_rejects_out_of_range_coordinates(self) -> None:
        with self.assertRaises(ValueError):
            kernel.xy_to_hilbert_13bit(np.array([-1]), np.array([0]))
        with self.assertRaises(ValueError):
            kernel.xy_to_hilbert_13bit(np.array([8192]), np.array([0]))
        with self.assertRaises(ValueError):
            kernel.xy_to_hilbert_13bit(np.array([0]), np.array([-1]))


class TestKatzBonacich(unittest.TestCase):
    @staticmethod
    def _dense_reference(
        a_pos: np.ndarray, a_neg: np.ndarray, omega: float, eps: float
    ) -> np.ndarray:
        rho = max(
            np.max(np.abs(np.linalg.eigvals(a_pos))),
            np.max(np.abs(np.linalg.eigvals(a_neg))),
            1e-6,
        )
        beta = 0.85 / rho
        identity = np.eye(len(a_pos))
        pos = np.linalg.solve(
            identity - beta**2 * (a_pos @ a_pos),
            (a_pos + beta * (a_pos @ a_pos)) @ np.ones(len(a_pos)),
        )
        neg = np.linalg.solve(
            identity - beta**2 * (a_neg @ a_neg),
            (a_neg + beta * (a_neg @ a_neg)) @ np.ones(len(a_neg)),
        )
        return np.sqrt(pos**2 + omega * neg**2) + eps

    def test_random_graphs_match_dense_solution(self) -> None:
        rng = np.random.default_rng(7)
        for n in range(1, 15):
            a_pos = rng.random((n, n))
            a_pos = np.triu(a_pos, 1)
            a_pos = a_pos + a_pos.T
            a_neg = rng.random((n, n))
            a_neg = np.triu(a_neg, 1)
            a_neg = a_neg + a_neg.T
            actual = kernel.compute_signed_bilinear_katz_bonacich(
                sp.csr_matrix(a_pos), sp.csr_matrix(a_neg), omega_conflict=0.5
            )
            expected = self._dense_reference(a_pos, a_neg, 0.5, 1e-6)
            np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-12)

    def test_two_node_and_empty_graphs(self) -> None:
        # Regression: scipy.sparse.linalg.eigs rejects 2x2 inputs.
        actual = kernel.compute_signed_bilinear_katz_bonacich(
            sp.csr_matrix(np.array([[0.0, 1.0], [1.0, 0.0]])),
            sp.csr_matrix(np.zeros((2, 2))),
        )
        self.assertTrue(np.isfinite(actual).all())

        empty = kernel.compute_signed_bilinear_katz_bonacich(
            sp.csr_matrix((3, 3)), sp.csr_matrix((3, 3))
        )
        np.testing.assert_allclose(empty, np.full(3, 1e-6))

    def test_rejects_invalid_layers(self) -> None:
        with self.assertRaises(ValueError):
            kernel.compute_signed_bilinear_katz_bonacich(
                sp.csr_matrix((2, 3)), sp.csr_matrix((2, 3))
            )
        with self.assertRaises(ValueError):
            kernel.compute_signed_bilinear_katz_bonacich(
                sp.csr_matrix(np.array([[-1.0, 0.0], [0.0, 0.0]])),
                sp.csr_matrix((2, 2)),
            )


class TestSDTAndPower(unittest.TestCase):
    def test_mmp_aug_and_inequality(self) -> None:
        mmp, auto_ineq = kernel.compute_mmp_aug(
            50_000, 25_000, 0.6, 0.2, 0.35, 0.2, 0.05, 0.01
        )
        self.assertAlmostEqual(auto_ineq, 0.35 / 0.2 + (0.05 - 0.01))
        self.assertAlmostEqual(
            mmp, (50_000 / 25_000) * 0.6 * 0.2 * (1 + auto_ineq)
        )

    def test_emp_sfd_rdi_psi(self) -> None:
        self.assertAlmostEqual(
            kernel.compute_emp(0.01, 0.55, 0.15, 0.35),
            (0.01**2 / (1 - 0.55)) * (1 + 0.15 / 0.35),
        )
        self.assertAlmostEqual(kernel.compute_sfd(1.2, 0.4, 2, 3), 2 * 1.2 + 3 * 0.4)
        self.assertEqual(kernel.compute_rdi(10, 3), 0.7)
        self.assertEqual(kernel.compute_rdi(10, 20), 0.0)
        psi = kernel.update_psi(0.5, 0.8, 0.9, 0.7, 0.2, 0.3, 0.1, 0.5)
        self.assertAlmostEqual(
            psi, max(0.0, (0.9 * 0.5 + 0.1 * 0.8 * 0.9 * 0.7) - 0.5 * 0.8 * 0.3)
        )

    def test_power_sclerosis_fragmentation_smr(self) -> None:
        power = kernel.compute_power_metrics([0.5, 0.3, 0.2], 1000, 200)
        self.assertAlmostEqual(power["HHI_pol"], 0.38)
        self.assertAlmostEqual(power["PCI"], 0.38 * 0.8)
        self.assertAlmostEqual(power["ENP"], 1 / 0.38)
        self.assertAlmostEqual(
            kernel.compute_sclerosis(0.1, 10, 2, 0.05),
            1 - math.exp(-0.1 * 10 * 2 * 0.05),
        )
        self.assertAlmostEqual(
            kernel.compute_military_fragmentation([0.5, 0.3, 0.2]),
            1 / (0.25 + 0.09 + 0.04),
        )

        active, para, reserve = 100_000, 50_000, 200_000
        population, expenditure, gdp = 10_000_000, 2e10, 1e12
        active_max, reserve_max, expenditure_max = 2000, 5000, 10
        terms = (
            math.log1p((active + para) / population * 1000) / math.log1p(active_max),
            math.log1p(reserve / population * 1000) / math.log1p(reserve_max),
            math.log1p(expenditure / gdp * 100) / math.log1p(expenditure_max),
        )
        self.assertAlmostEqual(
            kernel.compute_smr(
                active,
                para,
                reserve,
                population,
                expenditure,
                gdp,
                active_max,
                reserve_max,
                expenditure_max,
            ),
            sum(terms) / 3,
        )


class TestGranovetterAndCusp(unittest.TestCase):
    def test_granovetter_fold_and_participation(self) -> None:
        z_star, sigma_c, r_star = kernel.granovetter_fold(0.25)
        self.assertAlmostEqual(z_star, -1.5381722544551681, places=12)
        self.assertAlmostEqual(sigma_c, 0.12222086826897859, places=12)
        self.assertAlmostEqual(r_star, 0.062005, places=5)
        from scipy.special import ndtr

        self.assertAlmostEqual(
            kernel.granovetter_participation(0.1, 0.25, 0.12),
            float(ndtr((0.1 - 0.25) / 0.12)),
        )

    def test_cusp_parameters_and_volatility(self) -> None:
        weights_a = {"psi": 0.1, "spi": 0.2, "ig": 0.3, "coup": 0.4}
        weights_b = {"latent": 0.5, "penalty": 0.6, "sclerosis": 0.7, "esim": 0.8, "asi": 0.9}
        a, b = kernel.compute_cusp_parameters(
            0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.2,
            weights_a, weights_b, 0.1,
        )
        self.assertAlmostEqual(
            a,
            0.1
            + 0.1 * 0.2
            + 0.2 * 0.3**2
            + 0.3 * 0.4
            + 0.4 * 0.5
            + kernel.vulnerability_amplifier(0.7) * 0.6,
        )
        self.assertAlmostEqual(
            b, 0.5 * 0.8 + 0.6 * 0.7 + 0.7 * 0.9 + 0.8 * 1.0 - 0.9 * 0.2
        )
        self.assertAlmostEqual(
            kernel.diffusion_volatility(0.1, 0.7, 1.1, 0.9, 0.12),
            0.1 * kernel.vulnerability_amplifier(0.7) * 1.1 * 0.9 * (1 + 0.12 / 0.1222),
        )

    def test_coup_and_regime_defense(self) -> None:
        coup = kernel.coup_probabilities(0.0, 0.5, 0.3, 0.4, 0.2, 0.3, 0.4)
        success = kernel.logistic(-0.2 * 0.5 + 0.3 * 0.3 + 0.4 * 0.4)
        self.assertEqual(coup["attempt"], 0.5)
        self.assertAlmostEqual(coup["success_given_attempt"], success)
        self.assertAlmostEqual(coup["regime_change"], 0.5 * success)
        self.assertAlmostEqual(
            kernel.compute_regime_defense_index(100, 1000, 0.5, 2, 1000, 200, 0.2),
            math.tanh(0.2) * 0.8,
        )


class TestWeatherHawkesLatent(unittest.TestCase):
    def test_weather_modulators(self) -> None:
        aggression, turnout = kernel.compute_weather_modulators(1, 1, 2.54, 35, True, 0.1)
        self.assertAlmostEqual(aggression, 1.113 * 1.029)
        self.assertAlmostEqual(
            turnout, math.exp(-kernel.GAMMA_RAIN * 2.54) * 1.1
        )

    def test_hawkes_and_latent_discontent(self) -> None:
        hawkes = kernel.update_hawkes_state(2, 0.1, 0.5, 1, 100, 1000, [1, 2, 3])
        self.assertAlmostEqual(hawkes, math.exp(-0.1) * 2 + 0.5 * 0.9 * 6)
        latent = kernel.update_latent_discontent(
            2, 0.9, 0.1, 0.2, 1.1, 2.25, 0.3, 1.2, 0.8, 0.4, [1, 2, 3], 2, 0.1, 1
        )
        self.assertAlmostEqual(
            latent,
            0.9 * 2
            + 0.1 * 0.2
            + 1.1 * 2.25 * 0.3 * (1 + 1.2 * (1 - 0.8))
            + 0.4 * (6 + 2 * math.exp(-0.1)),
        )


class TestPenaltyAndSeverity(unittest.TestCase):
    def test_penalty_ladder(self) -> None:
        self.assertAlmostEqual(kernel.penalty_increment(0.1), (1 + 8.9 * 0.1 / 0.15) * 1e-4)
        self.assertAlmostEqual(kernel.penalty_increment(2.0), 0.99)
        previous = 0.0
        for impact in np.linspace(1e-6, 2.0, 20_000):
            value = kernel.penalty_increment(float(impact))
            self.assertGreaterEqual(value, previous * (1 - 1e-12))
            previous = value
        with self.assertRaises(ValueError):
            kernel.penalty_increment(0.0)
        with self.assertRaises(ValueError):
            kernel.penalty_increment(2.0001)

    def test_vulnerability_and_state_updates(self) -> None:
        self.assertEqual(kernel.vulnerability_amplifier(0), 1)
        self.assertAlmostEqual(kernel.vulnerability_amplifier(1), 20, places=12)
        penalty, bias = kernel.update_penalty_state(
            0.2, [0.01, 0.02], [(0.1, 0), (0.2, 2)], 0.2, 0.1, 0.05, 2.25
        )
        recovery = 0.1 + 0.2 * math.exp(-0.2)
        self.assertAlmostEqual(penalty, 0.2 + 0.03 - (0.8 / 2.25) * recovery)
        self.assertEqual(bias, 0.05)
        self.assertAlmostEqual(
            kernel.update_accumulated_bias(0.1, [(0.2, 2), (0.1, 0)], 0.5, 0.1),
            0.1 + 0.5 * (1 - math.exp(-0.2)) * 0.2,
        )

    def test_severity_ceiling_and_social_amplification(self) -> None:
        self.assertEqual(kernel.compute_severity_score(-10, 5, 0, 1), 1.0)
        self.assertEqual(kernel.compute_severity_score(-10, 5, 1e9, 1), 1.0)
        self.assertAlmostEqual(kernel.compute_severity_score(-5, 2.5, 0, 1), 0.25)
        # A capped severity is always a legal ladder impact.
        self.assertGreater(kernel.penalty_increment(kernel.compute_severity_score(-10, 5, 1e9, 1)), 0)
        amplification = kernel.compute_social_amplification(1, 10, 5, 0.3)
        self.assertAlmostEqual(
            amplification, math.tanh(0.3 * math.log(11) * (5 / kernel.NU_STAR_LIMIT))
        )
        self.assertGreaterEqual(amplification, 0)
        self.assertLessEqual(amplification, 1)

    def test_crowd_size_weight(self) -> None:
        self.assertEqual(kernel.crowd_size_weight(None), 1)
        self.assertEqual(kernel.crowd_size_weight(19), 1)
        self.assertEqual(kernel.crowd_size_weight(20), 2)
        self.assertEqual(kernel.crowd_size_weight(100), 3)
        self.assertEqual(kernel.crowd_size_weight(1000), 4)
        self.assertEqual(kernel.crowd_size_weight(10_000), 5)
        self.assertAlmostEqual(kernel.crowd_size_weight(20_000), 1 + math.log10(20_000))


@unittest.skipUnless(HAS_POLARS, "polars is not installed")
class TestEventIngestion(unittest.TestCase):
    def test_empty_frame_has_normalized_schema(self) -> None:
        frame = kernel.create_event_frame([])
        self.assertEqual(
            frame.columns,
            [
                "event_id",
                "event_time",
                "source",
                "target",
                "event_type",
                "mode",
                "context",
                "probabilities",
                "headcount",
                "damage",
                "parent_indices",
            ],
        )
        self.assertEqual(frame.height, 0)

    def test_service_injects_deduplicator_and_classifier(self) -> None:
        def deduplicate(records):
            seen = set()
            unique = []
            for record in records:
                if record["event_id"] not in seen:
                    seen.add(record["event_id"])
                    unique.append(record)
            return unique

        def classify(record):
            return {"event_type": "protest", "probabilities": [0.9, 0.1]}

        service = kernel.EventIngestionService(deduplicate, classify)
        frame = service.process(
            [
                {"event_id": "a", "headcount": 100},
                {"event_id": "a", "headcount": 100},
                {"event_id": "b", "headcount": 20_000},
            ]
        )
        self.assertEqual(frame.height, 2)
        self.assertEqual(frame["event_type"].to_list(), ["protest", "protest"])


class TestForecastAdapters(unittest.TestCase):
    def test_timesfm3_license_guard(self) -> None:
        with self.assertRaises(ValueError):
            kernel.load_timesfm("google/timesfm-3.0-200m-pytorch")

    def test_forecast_dispatch(self) -> None:
        history = np.arange(10, dtype=float)

        class PredictModel:
            def predict(self, values, **kwargs):
                return {"values": values, "kwargs": kwargs}

        class BatchModel:
            def predict_batch(self, values, **kwargs):
                return [{"values": values, "kwargs": kwargs}]

        class XRegModel:
            def forecast_with_covariates(self, **kwargs):
                return kwargs

        class PlainModel:
            def forecast(self, **kwargs):
                return kwargs

        result = kernel.forecast_timesfm(PredictModel(), history, horizon=4)
        self.assertEqual(result["kwargs"]["horizon"], 4)
        self.assertTrue(result["kwargs"]["return_quantiles"])

        result = kernel.forecast_timesfm(BatchModel(), history, horizon=3)
        self.assertEqual(len(result["values"]), 1)
        self.assertEqual(result["kwargs"]["horizon"], 3)

        result = kernel.forecast_timesfm(
            XRegModel(), np.vstack([history, history]), horizon=2,
            static_numeric_covariates={"x": np.ones(2)},
        )
        self.assertEqual(len(result["inputs"]), 2)
        self.assertEqual(result["xreg_mode"], "timesfm + xreg")
        np.testing.assert_array_equal(
            result["static_numerical_covariates"]["x"], np.ones(2)
        )

        result = kernel.forecast_timesfm(PlainModel(), np.vstack([history, history]), horizon=2)
        self.assertEqual(len(result["inputs"]), 2)

    def test_horizon_bounds(self) -> None:
        class PlainModel:
            def forecast(self, **kwargs):
                return kwargs

        with self.assertRaises(ValueError):
            kernel.forecast_timesfm(PlainModel(), [1.0], horizon=0)
        with self.assertRaises(ValueError):
            kernel.forecast_timesfm(PlainModel(), [1.0], horizon=kernel.H_HORIZON + 1)
        with self.assertRaises(TypeError):
            kernel.forecast_timesfm(object(), [1.0], horizon=1)


@unittest.skipUnless(HAS_TORCH, "torch is not installed")
class TestTorchKernel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import torch

        cls.torch = torch

    def test_hilbert_torch_matches_numpy(self) -> None:
        torch = self.torch
        rng = np.random.default_rng(42)
        x = rng.integers(0, 8192, size=(9, 8192))
        y = rng.integers(0, 8192, size=(9, 8192))
        np.testing.assert_array_equal(
            kernel.xy_to_hilbert_13bit(torch.from_numpy(x), torch.from_numpy(y)).numpy(),
            kernel.xy_to_hilbert_13bit(x, y),
        )

    def test_rank_permutation_is_a_permutation(self) -> None:
        torch = self.torch
        rng = np.random.default_rng(3)
        x = torch.from_numpy(rng.standard_normal((9, 8192)))
        b = torch.from_numpy(rng.standard_normal((9, 8192)))
        ranks = kernel.compute_true_2d_hilbert_rank_permutation(x, b)
        expected = torch.arange(8192).expand_as(ranks)
        self.assertTrue(torch.equal(torch.sort(ranks, dim=-1).values, expected))
        np.testing.assert_array_equal(
            ranks.numpy(),
            kernel.compute_true_2d_hilbert_rank_permutation(x.numpy(), b.numpy()),
        )
        with self.assertRaises(ValueError):
            kernel.compute_true_2d_hilbert_rank_permutation(x[:, :100], b[:, :100])

    def test_sobol_nets(self) -> None:
        torch = self.torch
        v, u = kernel.draw_owen_scrambled_0_13_2_nets(123)
        self.assertEqual(tuple(v.shape), (9, 8192))
        self.assertEqual(tuple(u.shape), (9, 8192))
        self.assertTrue(torch.isfinite(v).all() and torch.isfinite(u).all())
        self.assertGreaterEqual(float(v.min()), 0.0)
        self.assertLessEqual(float(v.max()), 1.0)
        for row in range(9):
            self.assertEqual(len(torch.unique(v[row])), 8192)
            self.assertEqual(len(torch.unique(u[row])), 8192)
        state = kernel.build_sobol_state(seed=3)
        self.assertEqual([tuple(t.shape) for t in state[:3]], [(9, 8192)] * 3)
        self.assertEqual(state[3].numel(), 9)

    def test_phi1_and_barrier(self) -> None:
        torch = self.torch
        z = torch.tensor([-10.0, -1e-3, -1e-8, 0.0, 1e-8, 1e-3, 10.0], dtype=torch.float64)
        phi = kernel.phi1_hochbruck(z).numpy()
        for value, actual in zip(z.numpy(), phi):
            reference = (
                (math.exp(value) - 1) / value
                if abs(value) >= 1e-4
                else 1 + value / 2 + value**2 / 6 + value**3 / 24
            )
            self.assertAlmostEqual(actual, reference, places=10)
        barrier, a_crit = kernel.compute_cusp_barrier_and_crit(
            torch.tensor([0.0], dtype=torch.float64),
            torch.tensor([1.0], dtype=torch.float64),
        )
        self.assertAlmostEqual(float(barrier), 0.0, places=12)
        self.assertAlmostEqual(float(a_crit), math.sqrt(4 / 27), places=12)
        barrier, _ = kernel.compute_cusp_barrier_and_crit(
            torch.tensor([0.0], dtype=torch.float64),
            torch.tensor([-1.0], dtype=torch.float64),
        )
        self.assertEqual(float(barrier), 0.0)

    def test_exact_bernoulli_flow(self) -> None:
        torch = self.torch
        x = torch.full((9, 8192), 0.3, dtype=torch.float64)
        a = torch.zeros((9, 8192), dtype=torch.float64)
        b = torch.full((9, 8192), 0.7, dtype=torch.float64)
        sigma = torch.zeros((9, 8192), dtype=torch.float64)
        v, u = kernel.draw_owen_scrambled_0_13_2_nets(11, dtype=torch.float64)
        out = kernel.clio_cusp_v4_array_mrqmc_step(x, a, b, sigma, v, u, dt=1.0)
        z = -2.0 * b
        expected = x / torch.sqrt(torch.exp(z) + 2.0 * kernel.phi1_hochbruck(z) * x.square())
        torch.testing.assert_close(out["X_next"], expected, rtol=1e-12, atol=1e-12)

        # The b = 0 case integrates dX/dt = -X^3 exactly.
        out = kernel.clio_cusp_v4_array_mrqmc_step(
            x, a, torch.zeros_like(b), sigma, v, u, dt=1.0
        )
        torch.testing.assert_close(
            out["X_next"], x / torch.sqrt(1.0 + 2.0 * x.square()), rtol=1e-12, atol=1e-12
        )

    def test_array_step_shapes_and_determinism(self) -> None:
        torch = self.torch
        torch.manual_seed(0)
        x = torch.randn(9, 8192, dtype=torch.float64) * 0.5
        a = torch.randn(9, 8192, dtype=torch.float64) * 0.2
        b = torch.randn(9, 8192, dtype=torch.float64) * 0.3
        sigma = torch.rand(9, 8192, dtype=torch.float64) * 0.5 + 0.01
        v, u = kernel.draw_owen_scrambled_0_13_2_nets(7, dtype=torch.float64)
        first = kernel.clio_cusp_v4_array_mrqmc_step(x, a, b, sigma, v, u)
        second = kernel.clio_cusp_v4_array_mrqmc_step(x, a, b, sigma, v, u)
        self.assertEqual(tuple(first["X_next"].shape), (9, 8192))
        self.assertTrue(torch.isfinite(first["X_next"]).all())
        self.assertTrue(torch.equal(first["X_next"], second["X_next"]))
        for key in ("S_next_mRQMC", "P_hysteresis_mRQMC", "P_collapse_mRQMC"):
            self.assertGreaterEqual(float(first[key]), 0.0)
            self.assertLessEqual(float(first[key]), 1.0)
        with self.assertRaises(ValueError):
            kernel.clio_cusp_v4_array_mrqmc_step(x[:, :10], a[:, :10], b[:, :10], sigma[:, :10], v, u)
        with self.assertRaises(ValueError):
            kernel.clio_cusp_v4_array_mrqmc_step(x, a, b, sigma, v, u, dt=0)


@unittest.skipUnless(HAS_IGRAPH, "igraph and leidenalg are not installed")
class TestSignedLeiden(unittest.TestCase):
    @staticmethod
    def _two_cliques(size: int = 4) -> tuple[sp.csr_matrix, sp.csr_matrix]:
        n = 2 * size
        a_pos = np.zeros((n, n))
        a_neg = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if i // size == j // size:
                    a_pos[i, j] = 1.0
                else:
                    a_neg[i, j] = 0.2
        return sp.csr_matrix(a_pos), sp.csr_matrix(a_neg)

    def test_recovers_positive_communities(self) -> None:
        a_pos, a_neg = self._two_cliques()
        for resolution in (0.1, 0.5, 1.0):
            clusters = kernel.build_signed_leiden_partitions(
                a_pos, a_neg, resolution=resolution, seed=0
            )
            self.assertEqual(sorted(sorted(cluster) for cluster in clusters),
                             [list(range(4)), list(range(4, 8))])

    def test_symmetric_layers_are_not_double_counted(self) -> None:
        a_pos, a_neg = self._two_cliques()
        symmetric = kernel.build_signed_leiden_partitions(a_pos, a_neg, resolution=0.5)
        upper = kernel.build_signed_leiden_partitions(
            sp.triu(a_pos), sp.triu(a_neg), resolution=0.5
        )
        self.assertEqual(sorted(symmetric), sorted(upper))

    def test_lower_only_empty_and_validation(self) -> None:
        a_pos, a_neg = self._two_cliques()
        lower = kernel.build_signed_leiden_partitions(
            sp.csr_matrix(np.tril(a_pos.toarray())),
            sp.csr_matrix(np.tril(a_neg.toarray())),
            resolution=0.5,
        )
        self.assertEqual(sorted(lower), [list(range(4)), list(range(4, 8))])
        empty = kernel.build_signed_leiden_partitions(
            sp.csr_matrix((3, 3)), sp.csr_matrix((3, 3)), resolution=0.5
        )
        self.assertEqual(sorted(empty), [[0], [1], [2]])
        with self.assertRaises(ValueError):
            kernel.build_signed_leiden_partitions(a_pos, a_neg, resolution=0)
        with self.assertRaises(ValueError):
            kernel.build_signed_leiden_partitions(a_pos[:, :3], a_neg)
        bad = sp.csr_matrix(a_pos.toarray())
        bad[0, 1] = -1
        with self.assertRaises(ValueError):
            kernel.build_signed_leiden_partitions(bad, a_neg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
