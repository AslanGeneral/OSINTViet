"""CLIO-CUSP 8192 v4.0: Xi_t state space, six subsystems and Array-mRQMC.

The package is a separate module under ``app.math_engine``.  Its numerical
primitives come from the locked repository-level kernel; its boundary with the
rest of the project is a strict JSON request/artifact pair.
"""

from app.math_engine.clio_cusp.contracts import (
    CLIOArtifact,
    CLIODayRecord,
    CLIOEventRecord,
    CLIOExogenous,
    CLIORequest,
    CoupCoefficients,
    CuspWeights,
    EdgeInput,
    EventInput,
    EventInputs,
    InterestGroupInput,
    MacroFeedback,
    MilitaryInputs,
    PowerInputs,
    PsychologyInputs,
    RemediationInput,
    SignedNetworkInput,
    StructuralInputs,
    WeatherInputs,
)
from app.math_engine.clio_cusp.macro_bridge import (
    apply_feedback_to_macro_request,
    apply_macro_artifact,
    clio_to_macro_feedback,
    map_macro_to_drivers,
    run_orchestra,
    synthesize_macro_events,
)
from app.math_engine.clio_cusp.orchestrator import run_clio

__all__ = [
    "CLIOArtifact",
    "CLIODayRecord",
    "CLIOEventRecord",
    "CLIOExogenous",
    "CLIORequest",
    "CoupCoefficients",
    "CuspWeights",
    "EdgeInput",
    "EventInput",
    "EventInputs",
    "InterestGroupInput",
    "MacroFeedback",
    "MilitaryInputs",
    "PowerInputs",
    "PsychologyInputs",
    "RemediationInput",
    "SignedNetworkInput",
    "StructuralInputs",
    "WeatherInputs",
    "apply_feedback_to_macro_request",
    "apply_macro_artifact",
    "clio_to_macro_feedback",
    "map_macro_to_drivers",
    "run_clio",
    "run_orchestra",
    "synthesize_macro_events",
]
