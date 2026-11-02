"""Public and internal JSON contracts for pipeline v2.

The public contracts mirror the JSON-Schema contracts supplied with the task.
Field names are kept stable: the CLI, the HTTP API and the cache log all use
these models.  Internal contracts (``QueryBreakdown``, ``RawMaterial`` and
``HouseholdProfile``) are deliberately kept out of the public response unless a
caller asks for diagnostics.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RequestedMode = Literal["macro", "micro", "dual", "auto"]
EffectiveMode = Literal["macro", "micro", "dual"]
CacheState = Literal[
    "written",
    "consumed_by_engine",
    "revalidating",
    "verified",
    "purged",
    "quarantined",
]
CACHE_STATES: tuple[str, ...] = (
    "written",
    "consumed_by_engine",
    "revalidating",
    "verified",
    "purged",
    "quarantined",
)
CACHE_TRANSITIONS: Dict[str, tuple[str, ...]] = {
    "written": ("consumed_by_engine", "quarantined"),
    "consumed_by_engine": ("revalidating", "quarantined"),
    "revalidating": ("verified", "quarantined"),
    "verified": ("purged",),
    "purged": (),
    "quarantined": (),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


def new_entry_id() -> str:
    return f"cache_{uuid.uuid4().hex[:20]}"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ExtensibleModel(BaseModel):
    """Result contracts may grow optional metadata without breaking clients."""

    model_config = ConfigDict(extra="allow")


class PersonalContext(_StrictModel):
    """User-provided context.  Every field is optional.

    ``persist_opt_in`` controls whether the cache log may persist the context.
    The context itself never contains an API key.
    """

    free_text: str = ""
    region: str = ""
    occupation_sector: str = ""
    household_notes: str = ""
    assets_liabilities_notes: str = ""
    persist_opt_in: bool = False


class RawInput(_StrictModel):
    run_id: str = Field(default_factory=new_run_id, min_length=1)
    query: str = Field(..., min_length=1)
    requested_mode: RequestedMode = "auto"
    personal_context: Optional[PersonalContext] = None
    source_refs: List[str] = Field(default_factory=list)


class ClassifierResult(_StrictModel):
    run_id: str
    label: Literal["macro", "micro", "mixed"]
    topic_tags: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., min_length=1)
    routing_recommendation: Optional[EffectiveMode] = None


class CacheLogEntry(_StrictModel):
    entry_id: str
    run_id: str
    state: CacheState
    created_at: datetime = Field(default_factory=utc_now)
    classifier: Optional[ClassifierResult] = None
    content: str = Field(..., min_length=1)
    embedding_ref: Optional[str] = None
    source_refs: List[str] = Field(default_factory=list)


class MacroClaim(_ExtensibleModel):
    text: str = Field(..., min_length=1)
    support: List[str] = Field(default_factory=list)


class MacroResult(_ExtensibleModel):
    run_id: str
    summary: str = Field(..., min_length=1)
    claims: List[MacroClaim] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class MicroTransmissionChain(_ExtensibleModel):
    channel: str = Field(..., min_length=1)
    direction: Literal["positive", "negative", "ambiguous", "none"]
    mechanism: str = Field(..., min_length=1)
    magnitude_status: Literal["computed", "sourced", "not_estimated"]
    magnitude_value: Optional[str] = None
    source_or_formula: Optional[str] = None

    @model_validator(mode="after")
    def _check_magnitude(self) -> "MicroTransmissionChain":
        if self.magnitude_status in {"computed", "sourced"}:
            if not self.magnitude_value or not self.source_or_formula:
                raise ValueError(
                    "computed/sourced magnitude requires magnitude_value and "
                    "source_or_formula"
                )
        else:
            if self.magnitude_value is not None:
                raise ValueError(
                    "not_estimated channels must not carry a magnitude_value"
                )
        return self


class MicroResult(_ExtensibleModel):
    run_id: str
    upstream_macro_used: bool = False
    transmission_chains: List[MicroTransmissionChain] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    what_would_change_this: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RevalidationCheck(_ExtensibleModel):
    name: Literal["grounding", "consistency", "completeness", "mode_integrity"]
    passed: bool
    failed_items: List[str] = Field(default_factory=list)
    method: str = ""


class PurgeReport(_ExtensibleModel):
    attempted: bool = False
    committed: bool = False
    entries_removed: int = 0
    dry_run: bool = False
    outcome: str = "not_attempted"
    audit_id: Optional[str] = None


class RevalidationReport(_ExtensibleModel):
    run_id: str
    passed: bool
    checks: List[RevalidationCheck] = Field(default_factory=list)
    purge: Optional[PurgeReport] = None
    warnings: List[str] = Field(default_factory=list)


class StructuredError(_ExtensibleModel):
    error: Literal[True] = True
    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    suggestion: str = Field(..., min_length=1)


class PipelineOutput(_ExtensibleModel):
    run_id: str
    mode_used: EffectiveMode
    status: Literal["ok", "ok_with_warnings", "failed"]
    macro: Optional[MacroResult] = None
    micro: Optional[MicroResult] = None
    validation: Optional[RevalidationReport] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[StructuredError] = None
    classifier: Optional[ClassifierResult] = None
    orchestration: Optional[Dict[str, Any]] = None
    artifacts: Dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Internal contracts
# ---------------------------------------------------------------------------


class QueryBreakdown(_ExtensibleModel):
    """Structured decomposition produced in S1.

    The numbers in this object are only parameters explicitly present in the
    user query/context.  No numeric value is inferred by the LLM.
    """

    run_id: str
    query: str
    macro_topics: List[str] = Field(default_factory=list)
    micro_topics: List[str] = Field(default_factory=list)
    indicators: List[str] = Field(default_factory=list)
    personal_dimensions: List[str] = Field(default_factory=list)
    explicit_parameters: Dict[str, Any] = Field(default_factory=dict)
    search_queries: List[str] = Field(default_factory=list)
    method: str = "deterministic"
    warnings: List[str] = Field(default_factory=list)


class RawMaterial(_ExtensibleModel):
    run_id: str
    query: str
    requested_mode: RequestedMode = "auto"
    article_title: str = ""
    article_url: str = ""
    article_source: str = ""
    article_content: str = ""
    analysis: Dict[str, Any] = Field(default_factory=dict)
    word_filter: Dict[str, Any] = Field(default_factory=dict)
    query_breakdown: QueryBreakdown
    source_refs: List[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=utc_now)
    warnings: List[str] = Field(default_factory=list)

    @property
    def text_for_classification(self) -> str:
        parts = [
            self.query,
            self.article_title,
            self.article_content[:8000],
            str(self.analysis.get("topic_vi") or ""),
            str(self.analysis.get("summary_vi") or ""),
        ]
        return "\n".join(part for part in parts if part)


class HouseholdProfile(_ExtensibleModel):
    """Optional structured household file.

    This is not part of ``PersonalContext``.  It lets the CLI/API carry explicit
    numeric inputs without weakening the public personal-context contract.
    Missing inputs are never replaced by invented user data; the affected
    channel is reported as ``not_estimated``.
    """

    monthly_income_million_vnd: Optional[float] = Field(default=None, gt=0)
    monthly_expenditure_million_vnd: Optional[float] = Field(default=None, gt=0)
    budget_shares_w0: Optional[List[float]] = None
    n_adults: Optional[int] = Field(default=None, ge=1, le=20)
    n_children: Optional[int] = Field(default=None, ge=0, le=20)
    liquid_assets_million_vnd: Optional[float] = Field(default=None, ge=0)
    illiquid_assets_million_vnd: Optional[float] = Field(default=None, ge=0)
    debt_balance_million_vnd: Optional[float] = Field(default=None, ge=0)
    monthly_debt_service_million_vnd: Optional[float] = Field(default=None, ge=0)
    fx_assets_million_vnd: Optional[float] = Field(default=None, ge=0)
    fx_liabilities_million_vnd: Optional[float] = Field(default=None, ge=0)
    crra_rho: Optional[float] = Field(default=None, gt=0.0, le=20.0)
    equity_risk_premium: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    return_variance: Optional[float] = Field(default=None, gt=0.0, le=1.0)
    psi_perm_pass: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    phi_trans_pass: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    beta_perm_macro: Optional[List[float]] = None
    beta_trans_macro: Optional[List[float]] = None
    equiv_method: Literal["oecd_modified", "oecd_sqrt", "bls_ses"] = "oecd_modified"
    poverty_line_z_monthly_million_vnd: Optional[float] = Field(
        default=None, gt=0.0
    )
    notes: str = ""

    @field_validator("budget_shares_w0")
    @classmethod
    def _validate_budget_shares(
        cls, value: Optional[List[float]]
    ) -> Optional[List[float]]:
        if value is None:
            return value
        if len(value) != 4:
            raise ValueError("budget_shares_w0 must contain exactly 4 shares")
        if any(share < 0.0 or share > 1.0 for share in value):
            raise ValueError("budget shares must be between 0 and 1")
        if abs(sum(value) - 1.0) > 1e-6:
            raise ValueError("budget shares must sum to 1")
        return [float(share) for share in value]

    @field_validator("beta_perm_macro", "beta_trans_macro")
    @classmethod
    def _validate_beta(cls, value: Optional[List[float]]) -> Optional[List[float]]:
        if value is None:
            return value
        if len(value) != 5:
            raise ValueError("macro beta vectors must contain exactly 5 loadings")
        return [float(item) for item in value]

    @classmethod
    def from_personal_context(
        cls, context: Optional[PersonalContext]
    ) -> "HouseholdProfile":
        if context is None:
            return cls()
        parsed = parse_household_numbers(context.free_text)
        parsed.notes = context.household_notes or context.assets_liabilities_notes
        return parsed

    def merged_over(self, base: "HouseholdProfile") -> "HouseholdProfile":
        """Return a copy where explicit fields in ``self`` win."""
        merged = base.model_dump(mode="python")
        for key, value in self.model_dump(mode="python").items():
            if value is None:
                continue
            if key == "equiv_method" and value == "oecd_modified":
                # A default does not override a deliberately supplied base.
                if base.equiv_method != "oecd_modified":
                    continue
            merged[key] = value
        return HouseholdProfile(**merged)


_NUMBER = r"(\d+(?:[.,]\d+)?)"
_THOUSAND = r"(?:nghìn|ngàn|k)"
_MILLION = r"(?:triệu|tr|million|m)"
_BILLION = r"(?:tỷ|ti|tỉ|billion|bn)"


def _to_float(raw: str) -> float:
    return float(raw.replace(".", "").replace(",", ".")) if "," in raw and "." in raw else float(raw.replace(",", "."))


def _scale(prefix: str, suffix: str) -> float:
    text = f"{prefix} {suffix}".strip().lower()
    if any(unit in text for unit in ("tỷ", "ti", "tỉ", "billion", "bn")):
        return 1000.0
    if any(unit in text for unit in ("triệu", "tr", "million")):
        return 1.0
    if any(unit in text for unit in ("nghìn", "ngàn", "k")):
        return 0.001
    return 1.0


def parse_household_numbers(free_text: str) -> HouseholdProfile:
    """Parse only explicitly stated numbers from Vietnamese/English text.

    The parser accepts the common ``20 triệu``/``20 million`` forms and monthly
    income/expense/debt-service phrases.  It never guesses a missing amount.
    """
    profile = HouseholdProfile()
    text = (free_text or "").lower()

    def find(pattern: str) -> Optional[float]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        value = _to_float(match.group("value"))
        unit = match.groupdict().get("unit") or ""
        return value * _scale("", unit)

    income = find(
        rf"(?:thu\s*nhập|income)[^\d]{{0,40}}(?P<value>{_NUMBER})\s*(?P<unit>{_MILLION}|{_BILLION}|{_THOUSAND})?"
    )
    expense = find(
        rf"(?:chi\s*tiêu|chi\s*phí|expense|spending)[^\d]{{0,40}}(?P<value>{_NUMBER})\s*(?P<unit>{_MILLION}|{_BILLION}|{_THOUSAND})?"
    )
    liquid = find(
        rf"(?:tiết\s*kiệm|tiền\s*mặt|liquid|savings)[^\d]{{0,40}}(?P<value>{_NUMBER})\s*(?P<unit>{_MILLION}|{_BILLION}|{_THOUSAND})?"
    )
    debt_service = find(
        rf"(?:trả\s*nợ|debt\s*service)[^\d]{{0,40}}(?P<value>{_NUMBER})\s*(?P<unit>{_MILLION}|{_BILLION}|{_THOUSAND})?"
    )
    debt_balance = find(
        rf"(?:dư\s*nợ|khoản\s*nợ|debt\s*balance|nợ)[^\d]{{0,40}}(?P<value>{_NUMBER})\s*(?P<unit>{_MILLION}|{_BILLION}|{_THOUSAND})?"
    )
    adults = re.search(r"(\d+)\s*(?:người\s*lớn|adults?)", text)
    children = re.search(r"(\d+)\s*(?:trẻ\s*em|con|children|kids?)", text)

    if income is not None:
        profile.monthly_income_million_vnd = income
    if expense is not None:
        profile.monthly_expenditure_million_vnd = expense
    if liquid is not None:
        profile.liquid_assets_million_vnd = liquid
    if debt_service is not None:
        profile.monthly_debt_service_million_vnd = debt_service
    if debt_balance is not None:
        profile.debt_balance_million_vnd = debt_balance
    if adults is not None:
        profile.n_adults = int(adults.group(1))
    if children is not None:
        profile.n_children = int(children.group(1))
    return profile


__all__ = [
    "CACHE_STATES",
    "CACHE_TRANSITIONS",
    "CacheLogEntry",
    "CacheState",
    "ClassifierResult",
    "EffectiveMode",
    "HouseholdProfile",
    "MacroClaim",
    "MacroResult",
    "MicroResult",
    "MicroTransmissionChain",
    "PersonalContext",
    "PipelineOutput",
    "PurgeReport",
    "QueryBreakdown",
    "RawInput",
    "RawMaterial",
    "RequestedMode",
    "RevalidationCheck",
    "RevalidationReport",
    "StructuredError",
    "new_entry_id",
    "new_run_id",
    "parse_household_numbers",
    "utc_now",
]
