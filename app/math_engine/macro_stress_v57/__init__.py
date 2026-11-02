"""Reference implementation of SPEC-MACRO-QUANT v5.7.

The package is deliberately self-contained: NumPy/SciPy/pandas/pyarrow do the
numerical work, and the orchestrator only sees a compact JSON evidence object.
The implementation follows the v5.7 tensor equations while keeping the online
path block-vectorized and deterministic for a fixed seed.
"""

from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
    SeriesPanel,
)
from app.math_engine.macro_stress_v57.engine import MacroStressEngineV57
from app.math_engine.macro_stress_v57.markov import (
    CompleteMarkovSwitchingEngineV57,
    branchless_pos,
    kim_backward_regime_probability_smoother,
    predict_and_collapse_kim_hamilton_step,
    rts_exact_kalman_smoother_regime_aware,
    step1_and_step3_endogenous_markov_propagation,
)

__all__ = [
    "CompleteMarkovSwitchingEngineV57",
    "MacroStressArtifact",
    "MacroStressEngineV57",
    "MacroStressRequest",
    "SeriesPanel",
    "branchless_pos",
    "kim_backward_regime_probability_smoother",
    "predict_and_collapse_kim_hamilton_step",
    "rts_exact_kalman_smoother_regime_aware",
    "step1_and_step3_endogenous_markov_propagation",
]
