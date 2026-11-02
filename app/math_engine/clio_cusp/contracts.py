"""Strict JSON contracts for the CLIO-CUSP 8192 v4.0 orchestrator.

The numerical kernel is stateless.  These models are the only objects that
cross the package boundary: an orchestrator request contains a macro-stress
artifact (or standalone drivers), and one CLIO artifact is returned.  NumPy
arrays never appear in a contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.math_engine.macro_stress_v57.contracts import MacroStressArtifact


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class EdgeInput(BaseModel):
    """One directed edge in a signed interest-group layer."""

    model_config = ConfigDict(extra="allow")

    source: int = Field(..., ge=0)
    target: int = Field(..., ge=0)
    weight: float = Field(default=1.0, ge=0.0)

    @field_validator("weight")
    @classmethod
    def _finite_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("edge weight must be finite")
        return value


class SignedNetworkInput(BaseModel):
    """Two non-negative adjacency layers on a shared node set."""

    model_config = ConfigDict(extra="allow")

    n_nodes: int = Field(default=0, ge=0)
    positive_edges: List[EdgeInput] = Field(default_factory=list)
    negative_edges: List[EdgeInput] = Field(default_factory=list)
    state_nodes: List[int] = Field(default_factory=list)
    node_labels: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_edges(self) -> "SignedNetworkInput":
        if self.n_nodes:
            for edge in [*self.positive_edges, *self.negative_edges]:
                if edge.source >= self.n_nodes or edge.target >= self.n_nodes:
                    raise ValueError("edge endpoint is outside n_nodes")
            if any(node < 0 or node >= self.n_nodes for node in self.state_nodes):
                raise ValueError("state_nodes contains an out-of-range index")
        if self.node_labels and self.n_nodes and len(self.node_labels) != self.n_nodes:
            raise ValueError("node_labels length must equal n_nodes")
        return self


class InterestGroupInput(BaseModel):
    """Assets, media reach and graph membership of one interest group."""

    model_config = ConfigDict(extra="allow")

    group_id: str = Field(..., min_length=1)
    label: str = ""
    node_indices: List[int] = Field(default_factory=list)
    assets: float = Field(default=0.0, ge=0.0)
    media_virality: float = Field(default=0.0, ge=0.0)
    oppositional: Optional[bool] = None
    state_affiliated: bool = False

    @field_validator("assets", "media_virality")
    @classmethod
    def _finite_nonnegative(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("interest-group values must be finite")
        return value


class OmegaWeights(BaseModel):
    """Weights of the three Omega_r,t components in Section 4.3."""

    model_config = ConfigDict(extra="allow")

    assets: float = Field(default=1.0 / 3.0, ge=0.0)
    centrality: float = Field(default=1.0 / 3.0, ge=0.0)
    media: float = Field(default=1.0 / 3.0, ge=0.0)


class StructuralInputs(BaseModel):
    """Subsystem 1 drivers.  Debt and fiscal flows are GDP ratios, not percent."""

    model_config = ConfigDict(extra="allow")

    gdp_pc: float = Field(default=3200.0, gt=0.0)
    median_wage: float = Field(default=900.0, gt=0.0)
    urban_share: float = Field(default=0.40, ge=0.0, le=1.0)
    youth_share: float = Field(default=0.20, ge=0.0, le=1.0)
    top10_share: float = Field(default=0.45, ge=0.0, le=1.0)
    bottom50_share: float = Field(default=0.15, gt=0.0, le=1.0)
    top1_share: float = Field(default=0.20, ge=0.0, le=1.0)
    labor_share: float = Field(default=0.50, ge=0.0, lt=1.0)
    elite_share: float = Field(default=0.10, ge=0.0, le=1.0)
    public_debt_gdp: float = Field(default=0.40, ge=0.0)
    interest_plus_deficit_tax: float = Field(default=0.60, ge=0.0)
    reform: float = Field(default=0.05, ge=0.0, le=1.0)
    expected_value: float = Field(default=100.0, gt=0.0)
    capability_value: float = Field(default=72.0, ge=0.0)
    productivity_growth: float = 0.02
    median_wage_growth: float = 0.01
    omega_gini: float = Field(default=1.0, ge=0.0)
    omega_tech: float = Field(default=1.0, ge=0.0)
    delta_psi: float = Field(default=0.20, gt=0.0, le=1.0)
    eta_reform: float = Field(default=0.05, ge=0.0)
    w_debt: float = Field(default=1.0, ge=0.0)
    w_def: float = Field(default=1.0, ge=0.0)
    gdp_pc_growth_daily: float = 0.0
    wage_growth_daily: float = 0.0
    inflation_daily: float = 0.0

    @model_validator(mode="after")
    def _validate_expected_value(self) -> "StructuralInputs":
        if self.capability_value > self.expected_value:
            # RDI is defined as max(0, (V_exp - V_cap)/V_exp), so capability
            # may legitimately exceed expectation; no clipping is performed.
            pass
        return self


class PowerInputs(BaseModel):
    """Subsystem 2 drivers."""

    model_config = ConfigDict(extra="allow")

    faction_shares: List[float] = Field(default_factory=lambda: [0.50, 0.30, 0.20])
    selectorate: float = Field(default=100.0, gt=0.0)
    coalition: float = Field(default=30.0, ge=0.0)
    lambda_olson: float = Field(default=0.020, ge=0.0)
    stability_duration: float = Field(default=20.0, ge=0.0)
    veto_players: float = Field(default=3.0, ge=0.0)
    rent_gdp: float = Field(default=0.05, ge=0.0)
    interest_groups: List[InterestGroupInput] = Field(default_factory=list)
    network: Optional[SignedNetworkInput] = None
    omega_weights: OmegaWeights = Field(default_factory=OmegaWeights)
    opposition_threshold: float = Field(default=0.10, ge=0.0)
    lambda_conflict: float = Field(default=0.5, ge=0.0)
    omega_eps_floor: float = Field(default=1e-6, gt=0.0)

    @field_validator("faction_shares")
    @classmethod
    def _validate_shares(cls, value: List[float]) -> List[float]:
        if not value or any(not math.isfinite(v) or v < 0 for v in value):
            raise ValueError("faction_shares must be finite and nonnegative")
        if not math.isclose(sum(value), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("faction_shares must sum to one")
        return value

    @model_validator(mode="after")
    def _validate_power(self) -> "PowerInputs":
        if self.coalition > self.selectorate:
            raise ValueError("coalition cannot exceed selectorate")
        for group in self.interest_groups:
            if group.state_affiliated and group.oppositional is True:
                raise ValueError("a state-affiliated group cannot be oppositional")
        return self


class MilitaryInputs(BaseModel):
    """Subsystem 3 drivers.  Personnel counts and expenditure are absolute."""

    model_config = ConfigDict(extra="allow")

    military_shares: List[float] = Field(default_factory=lambda: [0.50, 0.30, 0.20])
    active_military: float = Field(default=300_000.0, ge=0.0)
    paramilitary: float = Field(default=50_000.0, ge=0.0)
    reserve: float = Field(default=2_000_000.0, ge=0.0)
    military_expenditure: float = Field(default=5.0e9, ge=0.0)
    gdp: float = Field(default=4.0e11, gt=0.0)
    population: float = Field(default=100_000_000.0, gt=0.0)
    active_max: float = Field(default=100.0, gt=0.0)
    reserve_max: float = Field(default=50.0, gt=0.0)
    expenditure_max: float = Field(default=10.0, gt=0.0)
    baseline_pay: float = Field(default=2000.0, gt=0.0)
    military_total: Optional[float] = Field(default=None, gt=0.0)
    eta_coup: float = Field(default=0.50, ge=0.0)
    f_mil_initial: Optional[float] = Field(default=None, gt=0.0)
    delta_f_mil_365_initial: float = 0.0

    @field_validator("military_shares")
    @classmethod
    def _validate_military_shares(cls, value: List[float]) -> List[float]:
        if not value or any(not math.isfinite(v) or v < 0 for v in value):
            raise ValueError("military_shares must be finite and nonnegative")
        if not math.isclose(sum(value), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("military_shares must sum to one")
        return value


class EventInput(BaseModel):
    """One deduplicated, ConfliBERT-2 classified event."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:10]}")
    day: int = Field(default=1, ge=1)
    time: str = ""
    source: str = ""
    target: str = ""
    event_type: str = "PROTEST"
    mode: str = "demonstrate"
    context: str = "policy"
    headcount: Optional[float] = Field(default=None, ge=0.0)
    damage: float = Field(default=0.0, ge=0.0)
    new_shares_per_day: float = Field(default=0.0, ge=0.0)
    probabilities: Dict[str, float] = Field(default_factory=dict)
    goldstein_expectation: Optional[float] = None
    parent_indices: List[int] = Field(default_factory=list)
    negative_event_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_agitated: Optional[bool] = None

    @field_validator("probabilities")
    @classmethod
    def _validate_probabilities(cls, value: Dict[str, float]) -> Dict[str, float]:
        for key, probability in value.items():
            if not math.isfinite(probability) or probability < 0:
                raise ValueError(f"invalid probability for {key}: {probability}")
        if value and sum(value.values()) <= 0:
            raise ValueError("event probabilities must have positive mass")
        return value

    @field_validator("goldstein_expectation")
    @classmethod
    def _validate_goldstein(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("goldstein_expectation must be finite")
        return value

    @model_validator(mode="after")
    def _validate_parents(self) -> "EventInput":
        if not self.parent_indices:
            return self
        if self.parent_indices[0] != -1:
            raise ValueError("parent_indices[0] must be -1")
        for index, parent in enumerate(self.parent_indices[1:], 1):
            if parent < 0 or parent >= index:
                raise ValueError("parent_indices must be a root-first tree")
        return self


class RemediationInput(BaseModel):
    """A positive event's remediation amount and its age in days."""

    model_config = ConfigDict(extra="allow")

    amount: float = Field(..., ge=0.0)
    age_days: float = Field(default=0.0, ge=0.0)


class EventInputs(BaseModel):
    """Subsystem 4 drivers and the daily event stream."""

    model_config = ConfigDict(extra="allow")

    events: List[EventInput] = Field(default_factory=list)
    remediation: List[RemediationInput] = Field(default_factory=list)
    alpha_social: float = Field(default=1.0, ge=0.0)
    baseline_loss: float = Field(default=1.0, gt=0.0)
    theta_bias: float = Field(default=0.10, ge=0.0)
    delay_rate: float = Field(default=0.02, ge=0.0)
    harbingers: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    gamma_harbingers: List[float] = Field(default_factory=lambda: [0.20, 0.20, 0.20, 0.20])
    eta_select: float = Field(default=0.50, ge=0.0, le=1.0)
    coercive_intensity: float = Field(default=0.20, ge=0.0, le=1.0)
    police_count: float = Field(default=10_000.0, ge=0.0)
    active_protesters: float = Field(default=20_000.0, ge=0.0)
    population_pool: float = Field(default=10_000_000.0, gt=0.0)
    gamma_esim: float = Field(default=1.0, ge=0.0)
    spi_override: Optional[float] = Field(default=None, ge=0.0)
    enable_epstein: bool = True

    @field_validator("harbingers", "gamma_harbingers")
    @classmethod
    def _four_rails(cls, value: List[float]) -> List[float]:
        if len(value) != 4:
            raise ValueError("exactly four harbinger values/coefficients are required")
        if any(not math.isfinite(v) for v in value):
            raise ValueError("harbinger values must be finite")
        return [float(v) for v in value]

    @model_validator(mode="after")
    def _validate_events(self) -> "EventInputs":
        if self.harbingers and self.gamma_harbingers and len(self.harbingers) != len(self.gamma_harbingers):
            raise ValueError("harbingers and gamma_harbingers must have equal length")
        return self


class WeatherInputs(BaseModel):
    """Subsystem 5 meteorological controls."""

    model_config = ConfigDict(extra="allow")

    z_temp: float = 0.0
    z_pm25: float = 0.0
    rain_mm: float = 0.0
    daytime_temp_c: float = 30.0
    holiday: bool = False
    delta_weekend: float = 0.0
    aggression_override: Optional[float] = Field(default=None, gt=0.0)
    turnout_override: Optional[float] = Field(default=None, gt=0.0)


class PsychologyInputs(BaseModel):
    """Subsystem 5 threshold, Hawkes, latent-discontent and SDE drivers."""

    model_config = ConfigDict(extra="allow")

    mu_theta: float = Field(default=0.25, ge=0.0, le=1.0)
    sigma_theta: float = Field(default=0.10, gt=0.0)
    rho_mem: float = Field(default=0.85, ge=0.0, le=1.0)
    gamma_econ: float = Field(default=0.05, ge=0.0)
    beta_agit: float = Field(default=0.02, ge=0.0)
    decay_hawkes: float = Field(default=0.20, ge=0.0)
    alpha_hawkes: float = Field(default=0.30, ge=0.0)
    kappa_compress: float = Field(default=0.50, ge=0.0)
    sigma0: float = Field(default=0.03, ge=0.0)
    hawkes_initial: float = Field(default=0.0, ge=0.0)
    latent_initial: float = Field(default=0.0, ge=0.0)
    x0: float = 0.0
    x0_spread: float = Field(default=0.10, ge=0.0)
    epstein_agents: int = Field(default=1024, ge=1, le=65536)
    epstein_h_sigma: float = Field(default=0.30, ge=0.0)
    epstein_r_sigma: float = Field(default=0.30, ge=0.0)
    dt: float = Field(default=1.0, gt=0.0)


class CuspWeights(BaseModel):
    """Linear weights of the cusp control and volatility equations."""

    model_config = ConfigDict(extra="allow")

    a_psi: float = 0.35
    a_spi: float = 0.25
    a_ig: float = 0.20
    a_coup: float = 1.00
    b_latent: float = 1.00
    b_penalty: float = 2.00
    b_sclerosis: float = 0.80
    b_esim: float = 1.00
    b_asi: float = 0.80
    # Normal-form baseline.  A negative a0 is the status-quo well; this value
    # is calibrated so the no-shock baseline stays stable while a severe
    # protest wave crosses the fold.  The specification leaves a0 to the
    # calibrated initial condition.
    a0: float = -0.45
    sigma_a: float = Field(default=0.02, ge=0.0)
    sigma_b: float = Field(default=0.02, ge=0.0)

    def weights_a(self) -> Dict[str, float]:
        return {
            "psi": self.a_psi,
            "spi": self.a_spi,
            "ig": self.a_ig,
            "coup": self.a_coup,
        }

    def weights_b(self) -> Dict[str, float]:
        return {
            "latent": self.b_latent,
            "penalty": self.b_penalty,
            "sclerosis": self.b_sclerosis,
            "esim": self.b_esim,
            "asi": self.b_asi,
        }


class CoupCoefficients(BaseModel):
    """Section 5.2/5.3 coefficients.  Defaults are structural priors."""

    model_config = ConfigDict(extra="allow")

    intercept: float = -2.0
    beta_penalty: float = 1.00
    beta_emp: float = 0.20
    beta_gdp: float = 0.50
    beta_preempt: float = 0.80
    gamma_harbingers: List[float] = Field(default_factory=lambda: [0.20, 0.20, 0.20, 0.20])
    alpha_def: float = 1.00
    alpha_legit: float = 1.00
    alpha_defection: float = 0.50

    @field_validator("gamma_harbingers")
    @classmethod
    def _four_coefficients(cls, value: List[float]) -> List[float]:
        if len(value) != 4:
            raise ValueError("gamma_harbingers must contain exactly four coefficients")
        return [float(v) for v in value]


class CLIOExogenous(BaseModel):
    """All non-macro inputs consumed by the six subsystems."""

    model_config = ConfigDict(extra="allow")

    structural: StructuralInputs = Field(default_factory=StructuralInputs)
    power: PowerInputs = Field(default_factory=PowerInputs)
    military: MilitaryInputs = Field(default_factory=MilitaryInputs)
    events: EventInputs = Field(default_factory=EventInputs)
    weather: WeatherInputs = Field(default_factory=WeatherInputs)
    psychology: PsychologyInputs = Field(default_factory=PsychologyInputs)
    foreign_pressure: float = Field(default=0.0, ge=0.0)
    rdi_override: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    crisis_horizon_days: int = Field(default=365, ge=1)


class CLIORequest(BaseModel):
    """Input contract for one 128-day CLIO-CUSP orchestration."""

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(default_factory=lambda: f"clio_{uuid.uuid4().hex[:12]}")
    engine_version: str = "4.0.0"
    country_code: str = "VNM"
    horizon: int = Field(default=128, ge=1, le=128)
    seed: int = 20261102
    macro_artifact: Optional[MacroStressArtifact] = None
    exogenous: CLIOExogenous = Field(default_factory=CLIOExogenous)
    weights: CuspWeights = Field(default_factory=CuspWeights)
    coup: CoupCoefficients = Field(default_factory=CoupCoefficients)
    backend: Literal["auto", "numpy", "torch"] = "numpy"
    enable_macro_events: bool = True
    horizon_start_date: str = ""

    @field_validator("horizon_start_date")
    @classmethod
    def _validate_date(cls, value: str) -> str:
        if not value:
            return value
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("horizon_start_date must be ISO-8601") from exc
        return value


class CLIOEventRecord(BaseModel):
    """Auditable output of one event after all Subsystem 4 transforms."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    event_type: str = ""
    mode: str = ""
    context: str = ""
    agitated: bool = False
    goldstein_plover: float = 0.0
    size_weight: float = 1.0
    severity: float = 0.0
    virality: float = 0.0
    impact: float = 0.0
    penalty_increment: float = 0.0
    social_amplification: float = 0.0


class CLIODayRecord(BaseModel):
    """One daily step of the Xi_t state vector and its estimators."""

    model_config = ConfigDict(extra="allow")

    day: int = Field(..., ge=1)
    date: str = ""

    # Subsystem 1
    mmp_aug: float = 0.0
    auto_ineq: float = 0.0
    emp: float = 0.0
    sfd: float = 0.0
    rdi: float = 0.0
    psi: float = 0.0

    # Subsystem 2
    hhi_pol: float = 0.0
    pci: float = 0.0
    enp: float = 0.0
    sclerosis: float = 0.0
    omega_total: float = 0.0
    omega_groups: List[float] = Field(default_factory=list)
    delta_p_ig: float = 0.0

    # Subsystem 3
    smr: float = 0.0
    f_mil: float = 0.0
    delta_f_mil_365: float = 0.0
    asi_outcome: float = 0.0
    p_attempt: float = 0.0
    p_success: float = 0.0
    p_regime_change: float = 0.0
    p_bloody_failed: float = 0.0

    # Subsystem 4
    penalty: float = 0.0
    bias: float = 0.0
    k_exo: float = 1.0
    spi: float = 0.0
    esim_conversion: float = 0.0
    esim_agents: float = 0.0
    event_count: int = 0
    event_penalty_total: float = 0.0
    events: List[CLIOEventRecord] = Field(default_factory=list)

    # Subsystem 5
    aggression: float = 1.0
    turnout: float = 1.0
    hawkes: float = 0.0
    latent_discontent: float = 0.0
    epstein_activation_rate: float = 0.0
    granovetter_z_star: float = 0.0
    granovetter_sigma_c: float = 0.0
    granovetter_r_star: float = 0.0

    # Subsystem 6
    a_t: float = 0.0
    b_t: float = 0.0
    sigma_t: float = 0.0
    x_mean: float = 0.0
    x_q05: float = 0.0
    x_q50: float = 0.0
    x_q95: float = 0.0
    stability: float = 0.0
    stability_median: float = 0.0
    p_collapse: float = 0.0
    p_hysteresis: float = 0.0
    stress_index: float = 0.0
    gdp_feedback_pct: float = 0.0


class MacroFeedback(BaseModel):
    """CLIO -> macro channel consumed by a subsequent v5.7 rerun."""

    model_config = ConfigDict(extra="allow")

    source_request_id: str = ""
    source_macro_request_id: str = ""
    stress_index: float = 0.0
    gdp_yoy_adjustment_pct: float = 0.0
    gdp_yoy_stressed_pct: float = 0.0
    npl_uplift_pct: float = 0.0
    fx_pressure_uplift_pct: float = 0.0
    investment_haircut_pct: float = 0.0
    regime_change_probability: float = 0.0
    collapse_probability: float = 0.0
    terminal_stability: float = 0.0
    rationale: str = ""


class CLIOArtifact(BaseModel):
    """JSON output of one CLIO-CUSP orchestration."""

    model_config = ConfigDict(extra="allow")

    status: Literal["OK", "FAILED", "SKIPPED"] = "FAILED"
    engine_version: str = "4.0.0"
    request_id: str = ""
    request_sha256: str = ""
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: str = Field(default_factory=utc_now_iso)
    execution_time_seconds: float = 0.0
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    backend: str = "numpy"
    driver_source: str = "standalone"
    macro_request_id: str = ""
    macro_mapping: Dict[str, Any] = Field(default_factory=dict)
    days: List[CLIODayRecord] = Field(default_factory=list)
    terminal: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    macro_feedback: Optional[MacroFeedback] = None
    warnings: List[str] = Field(default_factory=list)
    audit: Dict[str, Any] = Field(default_factory=dict)
    artifact_sha256: str = ""
    error: Optional[str] = None


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
    "OmegaWeights",
    "PowerInputs",
    "PsychologyInputs",
    "RemediationInput",
    "SignedNetworkInput",
    "StructuralInputs",
    "WeatherInputs",
    "sha256_value",
    "utc_now_iso",
]
