"""Lazy-loaded on-demand household subsystem.

Importing this package must not import NumPy/SciPy/pyarrow modules.  The heavy
bridge is imported only inside :func:`invoke_micro_household_subsystem`, which
keeps idle RAM/CPU overhead at zero for macro-only runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[3] / "config" / "micro_household.json"
)


def _load_config(config_overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if config_overrides:
        return config_overrides
    payload = json.loads(_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("config/micro_household.json must be a JSON object")
    return payload


def invoke_micro_household_subsystem(
    mode: str,
    eco_engine_payload: Dict[str, Any],
    individual_profile: Optional[Dict[str, Any]] = None,
    arrow_ipc_population_path: Optional[str] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch to the isolated bridge without importing it at package load."""
    from .bridge_eco_engine import MicroHouseholdOnDemandEngine

    engine = MicroHouseholdOnDemandEngine(
        config_overrides=_load_config(config_overrides)
    )
    if mode == "single_individual":
        if individual_profile is None:
            raise ValueError(
                "individual_profile is required for single_individual mode"
            )
        return engine.evaluate_single_household(
            individual_profile, eco_engine_payload
        )
    if mode == "single_individual_available":
        if individual_profile is None:
            raise ValueError(
                "individual_profile is required for "
                "single_individual_available mode"
            )
        return engine.evaluate_single_household_available(
            individual_profile, eco_engine_payload
        )
    if mode == "population_cross_section":
        if arrow_ipc_population_path is None:
            raise ValueError(
                "arrow_ipc_population_path is required for "
                "population_cross_section mode"
            )
        return engine.evaluate_population_zero_copy(
            arrow_ipc_population_path, eco_engine_payload
        )
    raise ValueError(f"unsupported execution mode: {mode}")


__all__ = ["invoke_micro_household_subsystem"]
