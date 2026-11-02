"""Mutable Xi_t state and slow-moving driver updates for CLIO-CUSP.

Only this module holds NumPy arrays.  ``orchestrator`` reads the state, calls
the pure subsystem functions, and writes JSON-native day records.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from app.math_engine.clio_cusp.contracts import CLIOExogenous
from app.math_engine.clio_cusp.cusp import initial_particles
from app.math_engine.clio_cusp.kernel import compute_military_fragmentation


@dataclass
class CLIOState:
    """The mutable part of Xi_t that is not already a JSON contract."""

    X: np.ndarray
    psi: float = 0.0
    penalty: float = 0.0
    bias: float = 0.0
    hawkes: float = 0.0
    latent: float = 0.0
    day: int = 0
    military_history: List[float] = field(default_factory=list)

    @classmethod
    def initialize(cls, params: CLIOExogenous) -> "CLIOState":
        psychology = params.psychology
        X = initial_particles(
            x0=psychology.x0,
            spread=psychology.x0_spread,
        )
        military = params.military
        initial_f = (
            float(military.f_mil_initial)
            if military.f_mil_initial is not None
            else compute_military_fragmentation(military.military_shares)
        )
        history = [initial_f] * 365
        if military.delta_f_mil_365_initial:
            history[0] = max(1e-9, initial_f - float(military.delta_f_mil_365_initial))
        return cls(
            X=X,
            psi=0.0,
            penalty=0.0,
            bias=0.0,
            hawkes=float(psychology.hawkes_initial),
            latent=float(psychology.latent_initial),
            military_history=history,
        )

    def delta_f_mil_365(self, current_f: float) -> float:
        if not self.military_history:
            return 0.0
        reference = self.military_history[0]
        return float(current_f - reference)

    def push_military(self, current_f: float) -> None:
        self.military_history.append(float(current_f))
        if len(self.military_history) > 365:
            del self.military_history[: len(self.military_history) - 365]

    def correct_penalty(self, value: float) -> None:
        self.penalty = float(np.clip(value, self.bias, 1.0))


def advance_slow_drivers(params: CLIOExogenous) -> None:
    """Advance income, debt and fiscal-capacity drivers by one day.

    The specification fixes the state equations but leaves the observed input
    path to the data layer.  These updates are deliberately simple and
    auditable: they keep levels continuous without introducing a second
    unstated model.
    """
    s = params.structural
    s.gdp_pc = max(1e-9, s.gdp_pc * math.exp(s.gdp_pc_growth_daily))
    s.median_wage = max(1e-9, s.median_wage * math.exp(s.wage_growth_daily))
    expected_growth = max(-0.001, s.gdp_pc_growth_daily + 0.5 * s.inflation_daily)
    s.expected_value = max(1e-9, s.expected_value * (1.0 + expected_growth))
    s.capability_value = max(
        1e-9, s.capability_value * (1.0 + s.wage_growth_daily - s.inflation_daily)
    )
    nominal_rate_daily = max(0.0, 0.06 / 365.0)
    primary_balance_daily = 0.01 / 365.0
    s.public_debt_gdp = max(
        0.0,
        s.public_debt_gdp * (1.0 + nominal_rate_daily)
        / (1.0 + s.gdp_pc_growth_daily)
        - primary_balance_daily,
    )


def update_protester_pool(
    params: CLIOExogenous,
    events: List[object],
    turnout: float,
    day: Optional[int] = None,
) -> float:
    """Smooth the active-protest pool from same-day event headcounts.

    Events scheduled later in the horizon must not leak into today's pool, so
    the filter is applied whenever the caller supplies the current day.
    """
    crowd = 0.0
    for event in events:
        if day is not None:
            event_day = getattr(event, "day", None)
            if event_day is None or int(event_day) != int(day):
                continue
        headcount: Optional[float] = getattr(event, "headcount", None)
        if headcount is not None and headcount > 0:
            crowd += float(headcount)
    active = float(params.events.active_protesters)
    target = max(1_000.0, crowd * max(0.0, turnout)) if crowd > 0 else active * 0.98
    updated = 0.70 * active + 0.30 * target
    params.events.active_protesters = max(0.0, updated)
    return params.events.active_protesters


__all__ = ["CLIOState", "advance_slow_drivers", "update_protester_pool"]
