"""Strict JSON contracts for the v5.7 macro-financial stress engine.

The workflow treats the request and artifact as the only boundary between the
macro engine and the orchestrator.  Every field is JSON-native; NumPy arrays
are never passed across the boundary.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SeriesPanel(BaseModel):
    """A ragged-edge macro panel aligned on a common date index.

    ``values`` is ``T x N`` and may contain ``None`` for a missing publication.
    The v5.7 calibrator imputes only for estimation; the artifact records the
    exact missing-cell count so this is auditable.
    """

    model_config = ConfigDict(extra="allow")

    series_codes: List[str] = Field(..., min_length=1)
    series_names_vi: List[str] = Field(default_factory=list)
    units: List[str] = Field(default_factory=list)
    dates: List[str] = Field(..., min_length=1)
    values: List[List[Optional[float]]] = Field(..., min_length=1)
    frequency: Literal[
        "annual", "quarterly", "monthly", "weekly", "daily", "mixed", "unknown"
    ] = "annual"
    source: str = "world_bank"
    source_url: str = ""
    fetched_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_panel(self) -> "SeriesPanel":
        n_series = len(self.series_codes)
        if len(set(self.series_codes)) != n_series:
            raise ValueError("series_codes must be unique")
        if len(self.dates) != len(self.values):
            raise ValueError("dates length must equal values rows")
        for row in self.values:
            if len(row) != n_series:
                raise ValueError("each values row must have len(series_codes) cells")
            for value in row:
                if value is not None and not math.isfinite(float(value)):
                    raise ValueError("values must be finite numbers or null")
        if self.series_names_vi and len(self.series_names_vi) != n_series:
            raise ValueError("series_names_vi length must equal series_codes")
        if self.units and len(self.units) != n_series:
            raise ValueError("units length must equal series_codes")
        return self

    @property
    def n_series(self) -> int:
        return len(self.series_codes)

    @property
    def n_dates(self) -> int:
        return len(self.dates)

    @property
    def missing_cells(self) -> int:
        return sum(1 for row in self.values for value in row if value is None)


class MacroStressRequest(BaseModel):
    """Input contract for one v5.7 engine run."""

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(
        default_factory=lambda: f"stress_{uuid.uuid4().hex[:12]}"
    )
    engine_version: str = "5.7.0"
    country_code: str = "VNM"
    country_archetype: Literal[
        "EMDE_IPF_CORRIDOR",
        "FREE_FLOATING_RESERVE",
        "HARD_PEG_CURRENCY_BOARD",
    ] = "EMDE_IPF_CORRIDOR"
    panel: SeriesPanel
    horizon: int = Field(default=4, ge=1, le=12)
    n_factors: int = Field(default=5, ge=2, le=8)
    n_particles: int = Field(default=8192, ge=64, le=65536)
    k_rep: int = Field(default=50, ge=4, le=250)
    m_cand: int = Field(default=250, ge=8, le=2000)
    alpha_tail: float = Field(default=1e-4, gt=0.0, lt=0.2)
    confidence: float = 0.99
    seed: int = 20261026
    shock_mode: Literal["adversarial", "explicit", "historical"] = "adversarial"
    explicit_shock: Optional[List[float]] = None
    shock_dimension: int = Field(default=4, ge=3, le=6)
    cma_population: int = Field(default=32, ge=8, le=128)
    cma_generations: int = Field(default=50, ge=5, le=200)
    enable_micro_satellite: bool = True
    enable_structural_satellites: bool = True
    firm_count: int = Field(default=1600, ge=1, le=60000)
    firm_data_path: Optional[str] = None
    stress_artifact_dir: Optional[str] = None
    article_id: str = ""
    article_url: str = ""
    article_title_vi: str = ""
    article_context: Dict[str, Any] = Field(default_factory=dict)
    qpm_anchor_gdp_yoy_pct: Optional[float] = None
    reserves_min_billion_usd: float = 10.0
    sfc_tolerance_billion_usd: float = 5.0

    @field_validator("explicit_shock")
    @classmethod
    def _validate_explicit_shock(
        cls, value: Optional[List[float]]
    ) -> Optional[List[float]]:
        if value is None:
            return value
        if any(not math.isfinite(float(v)) for v in value):
            raise ValueError("explicit_shock must contain finite numbers")
        return [float(v) for v in value]

    @model_validator(mode="after")
    def _validate_request(self) -> "MacroStressRequest":
        if self.panel.n_series < 6:
            raise ValueError("v5.7 requires at least 6 panel series")
        if self.panel.n_dates < 20:
            raise ValueError("v5.7 calibration requires at least 20 observations")
        if self.shock_dimension > self.n_factors:
            raise ValueError("shock_dimension cannot exceed n_factors")
        if self.explicit_shock is not None:
            if len(self.explicit_shock) != self.shock_dimension:
                raise ValueError("explicit_shock length must equal shock_dimension")
        if self.k_rep > self.n_particles:
            raise ValueError("k_rep cannot exceed n_particles")
        return self


class BranchMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    gdp_yoy_mean_pct: float = 0.0
    gdp_yoy_trough_mean_pct: float = 0.0
    gdp_yoy_std_pct: float = 0.0
    gdp_tail_loss_var_pct: float = 0.0
    gdp_tail_loss_es_pct: float = 0.0
    fx_deprec_var_pct: float = 0.0
    fx_reserves_drain_billion_usd: float = 0.0
    interbank_peak_p95_pct: float = 0.0
    npl_breach_probability: float = 0.0
    npl_terminal_mean_pct: float = 0.0
    valid_particle_count: int = 0
    acceptance_rate: float = 0.0


class TailRiskRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    series_code: str
    series_name_vi: str = ""
    mean: float = 0.0
    std: float = 0.0
    var_99: float = 0.0
    es_99: float = 0.0
    tail_sign: int = 1
    unit: str = ""


class GFEVDEdge(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_index: int
    target_index: int
    source_code: str
    target_code: str
    variance_contribution_pct: float


class MacroStressArtifact(BaseModel):
    """JSON output consumed by the workflow, plugin and API."""

    model_config = ConfigDict(extra="allow")

    status: Literal["OK", "FAILED", "SKIPPED"] = "FAILED"
    engine_version: str = "5.7.0"
    request_id: str = ""
    request_sha256: str = ""
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: str = Field(default_factory=utc_now_iso)
    execution_time_seconds: float = 0.0
    peak_rss_mb: float = 0.0
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    calibration: Dict[str, Any] = Field(default_factory=dict)
    nowcast: Dict[str, Any] = Field(default_factory=dict)
    adversarial_shock: Dict[str, Any] = Field(default_factory=dict)
    branches: Dict[str, BranchMetrics] = Field(default_factory=dict)
    gatekeeper: Dict[str, Any] = Field(default_factory=dict)
    tail_risk: List[TailRiskRow] = Field(default_factory=list)
    wasserstein: Dict[str, Any] = Field(default_factory=dict)
    gfevd: List[GFEVDEdge] = Field(default_factory=list)
    gfevd_matrix: List[List[float]] = Field(default_factory=list)
    structural_satellites: Dict[str, Any] = Field(default_factory=dict)
    micro_downscaling: Dict[str, Any] = Field(default_factory=dict)
    deterministic_briefing: List[str] = Field(default_factory=list)
    audit: Dict[str, Any] = Field(default_factory=dict)
    artifact_sha256: str = ""
    error: Optional[str] = None


__all__ = [
    "BranchMetrics",
    "GFEVDEdge",
    "MacroStressArtifact",
    "MacroStressRequest",
    "SeriesPanel",
    "TailRiskRow",
    "utc_now_iso",
]
