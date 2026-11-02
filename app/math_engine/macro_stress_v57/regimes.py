"""Layer 1.5: Hamilton/Kim regime filter and set-identified Haar bank."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from app.math_engine.macro_stress_v57.calibration import CalibrationBundle


def run_regime_layer(calib: CalibrationBundle) -> Dict[str, Any]:
    xi = np.asarray(calib.regime_probs, dtype=float)
    last = xi[-1] / max(float(np.sum(xi[-1])), 1e-15)
    q_bank = calib.haar_bank
    # Structural sign acceptance is reported rather than silently assumed:
    # a rotation is accepted if its first column is positively aligned with
    # the GDP loading and its third column is positively aligned with the FX
    # loading.  The bank itself is fixed and valid regardless of this audit.
    gdp_idx = calib.core_index.get("GDP_YOY", 0)
    fx_idx = calib.core_index.get("FX_DEPREC", 2)
    gdp_loading = calib.loadings[gdp_idx, :]
    fx_loading = calib.loadings[fx_idx, :]
    accepted = 0
    for s in (0, 1):
        for m in range(q_bank.shape[1]):
            q = q_bank[s, m]
            if np.dot(gdp_loading, q[:, 0]) >= 0 and np.dot(fx_loading, q[:, 2]) >= 0:
                accepted += 1
    return {
        "filter": "hamilton_kim_collapsing",
        "regime_probabilities_last": [round(float(v), 6) for v in last],
        "regime_probabilities_mean": [round(float(v), 6) for v in xi.mean(axis=0)],
        "stress_probability_last": round(float(last[1]), 6),
        "transition_matrix": np.asarray(calib.p_trans).round(6).tolist(),
        "regime_counts": [int((calib.regimes == s).sum()) for s in (0, 1)],
        "haar_bank_shape": list(q_bank.shape),
        "haar_sign_accepted": int(accepted),
        "haar_sign_total": int(q_bank.shape[0] * q_bank.shape[1]),
        "set_identification": "O(r) Haar rotations with structural sign audit",
    }


__all__ = ["run_regime_layer"]
