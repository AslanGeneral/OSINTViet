"""Locked enumerations and literature-backed weights for CLIO-CUSP 8192 v4.0.

The kernel constants (``R_REPLICATES``, ``M_PARTICLES`` ...) live in
``clio_cusp_8192_v4_kernel.py`` and are re-exported by ``clio_cusp.kernel``.
This file only holds the event taxonomy used by Subsystem 4, because the
specification fixes the 18 PLOVER event types but does not publish a machine
readable table.
"""

from __future__ import annotations

# PLOVER / NGEC 18-type taxonomy used by ConfliBERT-2.  The order is the
# canonical CAMEO root ordering.
PLOVER_EVENT_TYPES: tuple[str, ...] = (
    "MAKE_PUBLIC_STATEMENT",
    "APPEAL",
    "EXPRESS_INTENT_TO_COOPERATE",
    "CONSULT",
    "ENGAGE_IN_DIPLOMATIC_COOPERATION",
    "ENGAGE_IN_MATERIAL_COOPERATION",
    "PROVIDE_AID",
    "YIELD",
    "INVESTIGATE",
    "DEMAND",
    "DISAPPROVE",
    "REJECT",
    "THREATEN",
    "PROTEST",
    "EXHIBIT_FORCE_POSTURE",
    "REDUCE_RELATIONS",
    "COERCE",
    "ASSAULT",
)

# Goldstein (1992) root-level scores.  The table spans the specification's
# locked range [-10.0, +8.3]; the +8.3 upper bound is reached only through the
# cooperative mode bonus below, which keeps G_PLOVER inside the stated range.
PLOVER_GOLDSTEIN_BASE: dict[str, float] = {
    "MAKE_PUBLIC_STATEMENT": 0.0,
    "APPEAL": 1.1,
    "EXPRESS_INTENT_TO_COOPERATE": 1.5,
    "CONSULT": 2.0,
    "ENGAGE_IN_DIPLOMATIC_COOPERATION": 3.4,
    "ENGAGE_IN_MATERIAL_COOPERATION": 4.0,
    "PROVIDE_AID": 5.4,
    "YIELD": 6.5,
    "INVESTIGATE": 2.2,
    "DEMAND": -1.1,
    "DISAPPROVE": -2.0,
    "REJECT": -2.9,
    "THREATEN": -4.0,
    "PROTEST": -5.0,
    "EXHIBIT_FORCE_POSTURE": -6.4,
    "REDUCE_RELATIONS": -7.0,
    "COERCE": -8.3,
    "ASSAULT": -10.0,
}

# Mode adjustments are small relative to the root score.  They let the
# ConfliBERT-2 Mode field change severity without changing the event family.
PLOVER_MODE_WEIGHTS: dict[str, float] = {
    "demonstrate": 0.0,
    "strike": -0.4,
    "boycott": -0.2,
    "hunger_strike": -0.5,
    "obstruct": -0.3,
    "riot_violent": -1.0,
    "armed": -1.5,
    "negotiate": 0.5,
    "aid": 0.8,
    "sanction": -0.7,
    "statement": 0.0,
}

# Contexts that turn a policy protest into an agitated anti-regime event.
AGITATED_CONTEXTS: frozenset[str] = frozenset(
    {"regime_change", "leadership_ouster", "autonomy", "foreign_backed"}
)

AGITATED_EVENT_TYPES: frozenset[str] = frozenset({"PROTEST", "ASSAULT", "COERCE"})

POLICY_PROTEST_CONTEXTS: frozenset[str] = frozenset(
    {"economic", "policy", "labor", "welfare", "environment"}
)

POLICY_PROTEST_MODES: frozenset[str] = frozenset(
    {"demonstrate", "strike", "boycott", "hunger_strike", "obstruct"}
)

__all__ = [
    "AGITATED_CONTEXTS",
    "AGITATED_EVENT_TYPES",
    "PLOVER_EVENT_TYPES",
    "PLOVER_GOLDSTEIN_BASE",
    "PLOVER_MODE_WEIGHTS",
    "POLICY_PROTEST_CONTEXTS",
    "POLICY_PROTEST_MODES",
]
