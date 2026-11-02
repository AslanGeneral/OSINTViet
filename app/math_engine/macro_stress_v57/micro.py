"""Layer 0.5/6 micro bridge: Arrow IPC zero-copy and firm downscaling."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.ipc as ipc

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle


class LosslessDistributionalBridgeV57:
    @staticmethod
    def save_firms_to_zero_copy_arrow_ipc(firm_table: pa.Table, path: str) -> None:
        feather.write_feather(firm_table, path, compression="uncompressed")

    @staticmethod
    def encode_cross_section_without_tail_loss(
        dvi_scores: np.ndarray,
        icr_ratios: np.ndarray,
        target_cov_weights: np.ndarray,
        dvi_crit_threshold: float = 0.80,
        icr_crit_threshold: float = 1.25,
    ) -> np.ndarray:
        dvi = np.asarray(dvi_scores, dtype=float)
        icr = np.asarray(icr_ratios, dtype=float)
        target = np.asarray(target_cov_weights, dtype=float)
        positive = 0.5 * (target + np.abs(target))
        total = float(np.sum(positive))
        if total > 1e-12:
            weights = positive / total
        else:
            weights = np.full_like(dvi, 1.0 / max(dvi.size, 1))
        pqr_mean = float(np.dot(weights, dvi))
        p10, p50, p90 = np.percentile(dvi, [10.0, 50.0, 90.0])
        dispersion = float(max(p90 - p10, 1e-8))
        kelley = float(((p90 - p50) - (p50 - p10)) / dispersion)
        critical_mass = float(
            np.mean((dvi > dvi_crit_threshold) & (icr < icr_crit_threshold))
        )
        return np.array(
            [pqr_mean, dispersion, kelley, critical_mass], dtype=float
        )

    @staticmethod
    def decode_macro_archetypes_to_all_firms_zero_copy(
        arrow_ipc_path: str,
        f_medoids_terminal: np.ndarray,
        cluster_weights_q: np.ndarray,
        optimal_policy_u: np.ndarray,
        kelley_skew_t0: float,
        pi_stress_regime2: float = 0.50,
        alpha_tail: float = 0.99,
    ) -> pa.Table:
        q = np.asarray(cluster_weights_q, dtype=float)
        q = q / max(float(q.sum()), 1e-300)
        f_medoids = np.asarray(f_medoids_terminal, dtype=float)
        policy = np.asarray(optimal_policy_u, dtype=float)
        with pa.memory_map(arrow_ipc_path, "r") as mmap_source:
            table = ipc.open_file(mmap_source).read_all()
            dvi = table["dvi_score"].chunk(0).to_numpy(zero_copy_only=True)
            sme = table["is_sme_or_retail"].chunk(0).to_numpy(zero_copy_only=True)
            policy_impact = np.zeros(dvi.shape[0], dtype=float)
            for k in range(3):
                col = f"sens_u{k + 1}"
                if col in table.column_names:
                    policy_impact += (
                        table[col].chunk(0).to_numpy(zero_copy_only=True)
                        * float(policy[k])
                    )
            factor_cols = [
                "beta_f1_global",
                "beta_f2_liquidity",
                "beta_f3_fx_infl",
                "beta_f4_growth",
                "beta_f5_sociopol",
            ]
            n_firms = int(dvi.shape[0])
            k_rep = int(f_medoids.shape[0])
            stress = np.empty((n_firms, k_rep), dtype=float)
            endo_skew = 0.15 * float(kelley_skew_t0) * (
                1.0 + 0.80 * float(pi_stress_regime2)
            )
            stress[:] = (dvi + policy_impact + endo_skew * sme)[:, None]
            for m, column in enumerate(factor_cols):
                if m >= f_medoids.shape[1] or column not in table.column_names:
                    continue
                beta = table[column].chunk(0).to_numpy(zero_copy_only=True)
                stress += np.outer(beta, f_medoids[:, m])

            expected = stress @ q
            order = np.argsort(stress, axis=1, kind="mergesort")
            sorted_stress = np.take_along_axis(stress, order, axis=1)
            sorted_q = q[order]
            cumulative = np.cumsum(sorted_q, axis=1)
            cumulative[:, -1] = 1.0
            first = np.argmax(cumulative >= alpha_tail, axis=1)
            var99 = sorted_stress[np.arange(n_firms), first]
            return (
                table.append_column(
                    "predicted_stress_mean_next_q",
                    pa.array(np.round(expected, 6)),
                )
                .append_column(
                    "predicted_stress_var99_next_q",
                    pa.array(np.round(var99, 6)),
                )
            )


def create_synthetic_firm_table(n_firms: int = 1600, seed: int = 20261026) -> pa.Table:
    rng = np.random.default_rng(seed)
    n = int(n_firms)
    dvi = np.clip(rng.beta(2.0, 3.5, n), 0.0, 1.0)
    icr = np.clip(rng.lognormal(0.35, 0.45, n), 0.15, 8.0)
    sme = (rng.random(n) < 0.62).astype(np.int8)
    beta1 = rng.normal(0.55, 0.30, n)
    beta2 = rng.normal(0.20, 0.25, n)
    beta3 = rng.normal(0.35, 0.45, n)
    beta4 = rng.normal(-0.30, 0.25, n)
    beta5 = rng.normal(0.15, 0.35, n)
    sens1 = rng.normal(0.0, 0.05, n)
    sens2 = rng.normal(0.0, 0.08, n)
    sens3 = rng.normal(0.0, 0.06, n)
    weights = rng.normal(0.0, 1.0, n)
    return pa.table(
        {
            "firm_id": pa.array([f"VN{i:06d}" for i in range(n)]),
            "sector": pa.array(
                rng.choice(
                    ["manufacturing", "retail", "construction", "services", "export"],
                    size=n,
                )
            ),
            "dvi_score": pa.array(dvi.astype(np.float64)),
            "icr_ratio": pa.array(icr.astype(np.float64)),
            "is_sme_or_retail": pa.array(sme, type=pa.int8()),
            "beta_f1_global": pa.array(beta1.astype(np.float64)),
            "beta_f2_liquidity": pa.array(beta2.astype(np.float64)),
            "beta_f3_fx_infl": pa.array(beta3.astype(np.float64)),
            "beta_f4_growth": pa.array(beta4.astype(np.float64)),
            "beta_f5_sociopol": pa.array(beta5.astype(np.float64)),
            "sens_u1": pa.array(sens1.astype(np.float64)),
            "sens_u2": pa.array(sens2.astype(np.float64)),
            "sens_u3": pa.array(sens3.astype(np.float64)),
            "pqr_target_weight": pa.array(weights.astype(np.float64)),
        }
    )


def _quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


def run_micro_downscaling(
    calib: CalibrationBundle,
    *,
    f_medoids_terminal: np.ndarray,
    cluster_weights: np.ndarray,
    optimal_policy_u: np.ndarray,
    firm_count: int,
    firm_data_path: Optional[str],
    artifact_dir: Optional[str],
    enable: bool = True,
    pi_stress_regime2: float = 0.50,
) -> Dict[str, Any]:
    if not enable:
        return {"enabled": False, "mode": "disabled", "n_firms": 0}
    target_dir = Path(artifact_dir) if artifact_dir else Path("/tmp/opencode")
    target_dir.mkdir(parents=True, exist_ok=True)
    if firm_data_path and Path(firm_data_path).exists():
        source_path = str(Path(firm_data_path))
        mode = "provided_arrow_ipc"
    else:
        source_path = str(target_dir / "firms_vietnam_zero_copy.arrow")
        LosslessDistributionalBridgeV57.save_firms_to_zero_copy_arrow_ipc(
            create_synthetic_firm_table(firm_count), source_path
        )
        mode = "synthetic_fixture"

    with pa.memory_map(source_path, "r") as mmap_source:
        table = ipc.open_file(mmap_source).read_all()
    dvi = table["dvi_score"].chunk(0).to_numpy(zero_copy_only=True)
    icr = table["icr_ratio"].chunk(0).to_numpy(zero_copy_only=True)
    weights = table["pqr_target_weight"].chunk(0).to_numpy(zero_copy_only=True)
    quartet = LosslessDistributionalBridgeV57.encode_cross_section_without_tail_loss(
        dvi, icr, weights
    )
    stress_table = LosslessDistributionalBridgeV57.decode_macro_archetypes_to_all_firms_zero_copy(
        source_path,
        f_medoids_terminal,
        cluster_weights,
        optimal_policy_u,
        kelley_skew_t0=float(quartet[2]),
        pi_stress_regime2=float(pi_stress_regime2),
        alpha_tail=0.99,
    )
    output_path = target_dir / "firms_stress_downscaled_v5_7.arrow"
    LosslessDistributionalBridgeV57.save_firms_to_zero_copy_arrow_ipc(
        stress_table, str(output_path)
    )
    expected = stress_table["predicted_stress_mean_next_q"].combine_chunks().to_numpy(
        zero_copy_only=True
    )
    var99 = stress_table["predicted_stress_var99_next_q"].combine_chunks().to_numpy(
        zero_copy_only=True
    )
    return {
        "enabled": True,
        "mode": mode,
        "n_firms": int(expected.size),
        "source_arrow_uri": source_path,
        "output_arrow_uri": str(output_path),
        "zero_copy": {
            "format": "uncompressed_apache_arrow_ipc",
            "raw_columns_zero_copy": True,
            "heap_column_materialization": "only_beta_outer_products_and_output_columns",
        },
        "distributional_shape_quartet": {
            "pqr_convex_mean_dvi": round(float(quartet[0]), 6),
            "dispersion_p90_p10": round(float(quartet[1]), 6),
            "kelley_skewness": round(float(quartet[2]), 6),
            "critical_tail_mass_dvi080_icr125": round(float(quartet[3]), 6),
        },
        "expected_stress_next_q": {
            "p05": round(_quantile(expected, 0.05), 6),
            "p50": round(_quantile(expected, 0.50), 6),
            "p95": round(_quantile(expected, 0.95), 6),
            "mean": round(float(np.mean(expected)), 6),
        },
        "var99_stress_next_q": {
            "p05": round(_quantile(var99, 0.05), 6),
            "p50": round(_quantile(var99, 0.50), 6),
            "p95": round(_quantile(var99, 0.95), 6),
            "mean": round(float(np.mean(var99)), 6),
        },
        "share_expected_stress_above_080": round(float(np.mean(expected > 0.80)), 6),
        "share_var99_above_080": round(float(np.mean(var99 > 0.80)), 6),
    }


__all__ = [
    "LosslessDistributionalBridgeV57",
    "create_synthetic_firm_table",
    "run_micro_downscaling",
]
