"""Configuration loading for pipeline v2.

Every threshold, model name, channel list and economic coefficient is loaded
from JSON files under ``config/``.  Missing required files fail loudly instead
of silently falling back to an invented default.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.pipeline_v2.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "pipeline_v2.json"


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ClassifierConfig(_ConfigModel):
    lexicon_weight: float = Field(..., ge=0.0, le=1.0)
    embedding_weight: float = Field(..., ge=0.0, le=1.0)
    mixed_margin: float = Field(..., gt=0.0, le=0.5)
    lexicon_saturation: int = Field(default=6, ge=1)
    max_text_chars: int = Field(default=6000, ge=500)
    llm_assist_enabled: bool = False
    macro_lexicon: List[str] = Field(default_factory=list)
    micro_lexicon: List[str] = Field(default_factory=list)
    macro_prototypes: List[str] = Field(default_factory=list)
    micro_prototypes: List[str] = Field(default_factory=list)
    topic_tags: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_weights(self) -> "ClassifierConfig":
        total = self.lexicon_weight + self.embedding_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError("classifier weights must sum to 1.0")
        if not self.macro_lexicon or not self.micro_lexicon:
            raise ValueError("classifier lexicons must be non-empty")
        return self


class Calibration(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str = "1.0"
    calibrated_at: str
    method: str
    sample_file: str
    n_samples: int = Field(..., ge=1)
    threshold: float = Field(..., gt=0.0, lt=1.0)
    mixed_margin: float = Field(..., gt=0.0, le=0.5)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class CacheLogConfig(_ConfigModel):
    purge_enabled: bool = True
    dry_run_default: bool = False
    store_embedding_vectors: bool = True
    max_content_chars: int = Field(default=20000, ge=1000)


class RevalidationConfig(_ConfigModel):
    grounding_similarity_threshold: float = Field(..., ge=0.0, le=1.0)
    consistency_similarity_threshold: float = Field(..., ge=0.0, le=1.0)
    lexical_overlap_threshold: float = Field(..., ge=0.0, le=1.0)
    min_claim_support_ratio: float = Field(..., ge=0.0, le=1.0)
    require_external_support: bool = True
    embedding_batch_size: int = Field(default=16, ge=1, le=256)
    append_failure_lessons: bool = False


class InputFetchConfig(_ConfigModel):
    require_live_article: bool = False
    analyze_article: bool = True
    max_article_chars: int = Field(default=6000, ge=500)
    fetch_attempts: int = Field(default=2, ge=1, le=10)
    query_breakdown_enabled: bool = True
    query_breakdown_llm_enabled: bool = True


class QueryBreakdownConfig(_ConfigModel):
    indicator_terms: Dict[str, List[str]] = Field(default_factory=dict)
    personal_dimension_terms: Dict[str, List[str]] = Field(default_factory=dict)
    search_query_templates: List[str] = Field(default_factory=list)


class BYOKConfig(_ConfigModel):
    """Names of the environment variables that may carry a user key.

    The values are never stored in configuration; only the variable names are.
    """

    api_key_env: str = "OSINT_BYOK_API_KEY"
    base_url_env: str = "OSINT_BYOK_BASE_URL"
    model_env: str = "OSINT_BYOK_MODEL"
    default_base_url: str
    default_model: str
    timeout_seconds: float = Field(default=180.0, gt=0.0)


class OrchestratorConfig(_ConfigModel):
    enabled: bool = True
    disable_raw_ingestors: bool = True
    domain_tag: str = "macro_econ"
    timeout_seconds: float = Field(default=1800.0, gt=0.0)


class MacroConfig(_ConfigModel):
    artifact_subdir: str = "macro"
    country_code: str = "VNM"


class MicroConfig(_ConfigModel):
    household_config_path: str = "config/micro_household.json"
    channels_path: str = "config/micro_channels.json"


class PipelineV2Config(_ConfigModel):
    version: str = "1.0"
    classifier: ClassifierConfig
    cache_log: CacheLogConfig
    revalidation: RevalidationConfig
    input_fetch: InputFetchConfig
    query_breakdown: QueryBreakdownConfig = Field(
        default_factory=QueryBreakdownConfig
    )
    byok: BYOKConfig
    orchestrator: OrchestratorConfig
    macro: MacroConfig
    micro: MicroConfig
    calibration_path: str = "config/classifier_calibration.json"
    artifact_dir: str = "/tmp/osint_pipeline_v2"


class HouseholdParameters(_ConfigModel):
    quaids_alpha: List[float]
    quaids_beta: List[float]
    quaids_lambda: List[float]
    quaids_gamma: List[List[float]]
    quaids_alpha_0: float
    les_subsistence_quantities: List[float]
    equivalence_scales: Dict[str, Any]
    m_price_transmission: List[List[float]]
    price_projection_clip_ln: List[float]
    poverty_line_z_monthly_million_vnd: float
    policy_dampener_per_unit: float
    regime_markup: float
    erp_base: float
    erp_factor_growth: float
    erp_factor_liquidity: float
    erp_bounds: List[float]
    return_variance_base: float
    return_variance_stress: float
    return_variance_global: float
    return_variance_bounds: List[float]
    sigma_c_base: float
    sigma_c_stress: float
    carroll_toche_unemp_prob_base: float
    carroll_toche_unemp_prob_stress: float
    bpp_price_transitory_pass_through: float
    vep_vulnerable_threshold: float = Field(..., gt=0.0, lt=1.0)
    vep_high_fragility_threshold: float = Field(..., gt=0.0, lt=1.0)
    default_budget_shares: List[float]
    default_crra_rho: float
    default_psi_perm_pass: float
    default_phi_trans_pass: float
    default_beta_perm_macro: List[float]
    default_beta_trans_macro: List[float]
    mpc_by_regime: List[float]
    liquid_buffer_threshold_months: float
    mdsr_threshold: float
    egm_n_grid: int
    egm_a_max: float
    egm_rho: float
    egm_R_gross: float
    egm_beta_disc: float
    egm_gamma_growth: float
    egm_sigma_xi: float
    egm_borrow_limit: float
    egm_max_iter: int
    egm_tol: float
    source_note: str

    @field_validator("quaids_gamma")
    @classmethod
    def _validate_gamma(cls, value: List[List[float]]) -> List[List[float]]:
        if len(value) != 4 or any(len(row) != 4 for row in value):
            raise ValueError("quaids_gamma must be 4x4")
        return value

    @field_validator(
        "quaids_alpha",
        "quaids_beta",
        "quaids_lambda",
        "les_subsistence_quantities",
        "default_budget_shares",
        "default_beta_perm_macro",
        "default_beta_trans_macro",
    )
    @classmethod
    def _validate_four_or_five(cls, value: List[float]) -> List[float]:
        if len(value) not in {4, 5}:
            raise ValueError("vector must have 4 (goods) or 5 (factors) entries")
        return value

    @field_validator("m_price_transmission")
    @classmethod
    def _validate_transmission(cls, value: List[List[float]]) -> List[List[float]]:
        if len(value) != 4 or any(len(row) != 5 for row in value):
            raise ValueError("m_price_transmission must be 4x5")
        return value

    @field_validator("mpc_by_regime")
    @classmethod
    def _validate_mpc(cls, value: List[float]) -> List[float]:
        if len(value) != 3:
            raise ValueError("mpc_by_regime must contain regimes 0, 1, 2")
        return value

    @field_validator("equivalence_scales")
    @classmethod
    def _validate_equivalence_scales(
        cls, value: Dict[str, Any]
    ) -> Dict[str, Any]:
        required = {"oecd_modified", "bls_ses", "eta_child_food"}
        missing = required - set(value)
        if missing:
            raise ValueError(
                "equivalence_scales thiếu khóa: " + ", ".join(sorted(missing))
            )
        return value

    @field_validator("price_projection_clip_ln")
    @classmethod
    def _validate_price_clip(cls, value: List[float]) -> List[float]:
        if len(value) != 2 or float(value[0]) >= float(value[1]):
            raise ValueError(
                "price_projection_clip_ln must be [lower, upper] with lower < upper"
            )
        return value


class ChannelSpec(_ConfigModel):
    id: str
    name_vi: str
    formula: str
    enabled: bool = True
    description_vi: str = ""


class ChannelList(_ConfigModel):
    direction_epsilon: float = Field(..., gt=0.0, lt=0.5)
    channel_direction_epsilons: Dict[str, float] = Field(default_factory=dict)
    channels: List[ChannelSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> "ChannelList":
        ids = [channel.id for channel in self.channels]
        if len(ids) != len(set(ids)):
            raise ValueError("micro channel ids must be unique")
        if not ids:
            raise ValueError("micro channel list must not be empty")
        return self

    def epsilon_for(self, channel_id: str) -> float:
        """Direction dead-band for one channel, in the channel's own unit."""
        return float(
            self.channel_direction_epsilons.get(
                channel_id, self.direction_epsilon
            )
        )


def _resolve(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"Thiếu tệp cấu hình {label}: {path}",
            suggestion=(
                f"Tạo {path} theo mẫu trong repository rồi chạy lại; "
                "pipeline không dùng giá trị mặc định tự phát."
            ),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(
            f"Không đọc được {label} tại {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{label} phải là một JSON object: {path}")
    return payload


@lru_cache(maxsize=8)
def load_pipeline_config(path: Optional[str] = None) -> PipelineV2Config:
    config_path = _resolve(path or DEFAULT_CONFIG_PATH)
    payload = _read_json(config_path, "pipeline v2")
    try:
        return PipelineV2Config(**payload)
    except Exception as exc:
        raise ConfigError(
            f"Cấu hình pipeline không hợp lệ tại {config_path}: {exc}"
        ) from exc


def load_calibration(
    path: Optional[str] = None,
    *,
    config: Optional[PipelineV2Config] = None,
) -> Calibration:
    cfg = config or load_pipeline_config()
    calibration_path = _resolve(path or cfg.calibration_path)
    payload = _read_json(calibration_path, "classifier calibration")
    try:
        calibration = Calibration(**payload)
    except Exception as exc:
        raise ConfigError(
            "Calibration của classifier không hợp lệ tại "
            f"{calibration_path}: {exc}. Chạy scripts/calibrate_pipeline_v2.py."
        ) from exc
    if "threshold" not in payload:
        raise ConfigError(
            "Calibration thiếu trường threshold; chạy "
            "scripts/calibrate_pipeline_v2.py --write."
        )
    return calibration


@lru_cache(maxsize=8)
def load_household_parameters(path: Optional[str] = None) -> HouseholdParameters:
    cfg = load_pipeline_config()
    parameter_path = _resolve(path or cfg.micro.household_config_path)
    payload = _read_json(parameter_path, "micro household parameters")
    try:
        return HouseholdParameters(**payload)
    except Exception as exc:
        raise ConfigError(
            f"Tham số hộ gia đình không hợp lệ tại {parameter_path}: {exc}"
        ) from exc


@lru_cache(maxsize=8)
def load_channel_list(path: Optional[str] = None) -> ChannelList:
    cfg = load_pipeline_config()
    channels_path = _resolve(path or cfg.micro.channels_path)
    payload = _read_json(channels_path, "micro channel list")
    try:
        return ChannelList(**payload)
    except Exception as exc:
        raise ConfigError(
            f"Danh sách kênh vi mô không hợp lệ tại {channels_path}: {exc}"
        ) from exc


__all__ = [
    "BYOKConfig",
    "Calibration",
    "ChannelList",
    "ChannelSpec",
    "HouseholdParameters",
    "PipelineV2Config",
    "QueryBreakdownConfig",
    "REPO_ROOT",
    "load_calibration",
    "load_channel_list",
    "load_household_parameters",
    "load_pipeline_config",
]
