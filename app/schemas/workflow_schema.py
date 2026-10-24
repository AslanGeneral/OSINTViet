"""JSON contracts for the full-circle workflow.

Every stage of the workflow exchanges a validated Pydantic/JSON object:
article -> analysis -> word filter -> stats request -> ARIMA-GARCH artifact ->
orchestrator output -> recheck.  The SQLite ledger stores the same envelope as
JSON so a run can be audited without trusting free-form LLM prose.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FetchedArticle(BaseModel):
    """Live article fetched through a headless Chromium browser."""

    model_config = ConfigDict(extra="allow")

    article_id: Optional[str] = None
    title: str = Field(..., min_length=1)
    summary: str = ""
    raw_content: str = ""
    cleaned_content: str = ""
    source: str = "unknown"
    link: str = ""
    published_at: Optional[str] = None
    fetched_at: str = Field(default_factory=utc_now_iso)
    browser: str = "chromium-headless"
    source_section: str = ""
    selection_seed: Optional[int] = None
    domain_tag: str = "macro_econ"

    @field_validator("link")
    @classmethod
    def _validate_link(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("article link must be an absolute HTTP(S) URL")
        return value


class ArticlePayload(BaseModel):
    """Canonical JSON validated immediately before a SQLite Article write."""

    model_config = ConfigDict(extra="allow")

    article_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    source: str = "unknown"
    url: str = ""
    raw_content: str = ""
    cleaned_content: str = ""
    relevance_score: float = 0.0
    domain_tag: str = "general"
    fetched_at: str = Field(default_factory=utc_now_iso)
    published_at: Optional[str] = None
    browser: str = ""
    selection_seed: Optional[int] = None


class EvidenceClaim(BaseModel):
    claim_vi: str = Field(..., min_length=3)
    evidence_quote: str = Field(..., min_length=3)


class IndicatorMention(BaseModel):
    name_vi: str = Field(..., min_length=1)
    code: str = ""
    value: Optional[float] = None
    unit: str = ""
    evidence_quote: str = ""


class ArticleAnalysis(BaseModel):
    """Structured article analysis. Every claim must carry an exact quote.

    The topic/summary/relevance/confidence fields are required in the JSON
    schema so a small local model cannot return a minimal object that omits
    the analysis.  Empty claim lists are allowed; the workflow adds a
    deterministic exact-quote fallback when the model paraphrases.
    """

    article_id: str = ""
    title_vi: str = ""
    topic_vi: str = Field(..., min_length=2)
    summary_vi: str = Field(..., min_length=10)
    macro_relevance: float = Field(..., ge=0.0, le=1.0)
    key_claims: List[EvidenceClaim] = Field(...)
    indicator_mentions: List[IndicatorMention] = Field(...)
    risk_flags: List[str] = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)


class WordFilterResult(BaseModel):
    article_id: str = ""
    accepted: bool = False
    lexical_score: float = 0.0
    matched_positive_terms: List[str] = Field(default_factory=list)
    matched_negative_terms: List[str] = Field(default_factory=list)
    analysis_macro_relevance: float = 0.0
    embedding_relevance: Optional[bool] = None
    embedding_threshold: float = 0.0
    claims_kept: int = 0
    claims_dropped: int = 0
    evidence_integrity: float = 0.0
    reason_vi: str = ""


class StatsRequest(BaseModel):
    """JSON input passed to ARIMA-GARCH.  This is the only allowed input shape."""

    series_name: str = Field(..., min_length=1)
    indicator_code: str = Field(..., min_length=1)
    indicator_name_vi: str = ""
    country_code: str = "VNM"
    frequency: Literal["annual", "quarterly", "monthly", "weekly", "daily", "unknown"] = "unknown"
    source: str = "world_bank"
    source_url: str = ""
    dates: List[str] = Field(..., min_length=1)
    values: List[float] = Field(..., min_length=1)
    point_count: int = 0
    article_id: str = ""
    article_url: str = ""
    selection_reason_vi: str = ""
    article_context: Dict[str, Any] = Field(default_factory=dict)
    fetched_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_series(self) -> "StatsRequest":
        if len(self.dates) != len(self.values):
            raise ValueError("dates and values must have the same length")
        if len(self.dates) < 30:
            raise ValueError("ARIMA-GARCH requires at least 30 observations")
        if any(not math.isfinite(float(v)) for v in self.values):
            raise ValueError("values must contain only finite numbers")
        if self.point_count and self.point_count != len(self.values):
            raise ValueError("point_count must match values length")
        self.point_count = len(self.values)
        return self


class StatsArtifact(BaseModel):
    """JSON output of the ARIMA-GARCH stage."""

    status: Literal["OK", "FAILED", "SKIPPED"]
    request: Optional[StatsRequest] = None
    response: Optional[Dict[str, Any]] = None
    descriptive_stats: Dict[str, float] = Field(default_factory=dict)
    forecast_frequency: str = "unknown"
    executed_at: str = Field(default_factory=utc_now_iso)
    evidence_sha256: str = ""
    error: Optional[str] = None


class RecheckCheck(BaseModel):
    name: str
    passed: bool
    required: bool = True
    details: str = ""


class RecheckReport(BaseModel):
    passed: bool = False
    business_passed: bool = False
    checks: List[RecheckCheck] = Field(default_factory=list)
    failed_checks: List[str] = Field(default_factory=list)
    llm_call_count: int = 0
    final_llm_call_count: int = 0
    checked_at: str = Field(default_factory=utc_now_iso)


class WorkflowEnvelope(BaseModel):
    """The single JSON document persisted in SQLite and on disk for one run."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    status: Literal[
        "running",
        "succeeded",
        "succeeded_with_dead_branch",
        "failed",
        "failed_recheck",
    ] = "running"
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    domain_tag: str = "macro_econ"
    query: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    reset_report: Dict[str, Any] = Field(default_factory=dict)
    article: Optional[FetchedArticle] = None
    analysis: Optional[ArticleAnalysis] = None
    word_filter: Optional[WordFilterResult] = None
    stats_request: Optional[StatsRequest] = None
    stats_artifact: Optional[StatsArtifact] = None
    orchestration_output: Optional[Dict[str, Any]] = None
    orchestration_meta: Dict[str, Any] = Field(default_factory=dict)
    llm_calls: List[Dict[str, Any]] = Field(default_factory=list)
    recheck: Optional[RecheckReport] = None
    artifacts: Dict[str, str] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Role-specific JSON schemas used by the strict orchestrator router.
# These are deliberately small and explicit: the local model gets a grammar
# constrained by these schemas, and the same objects are validated again in
# Python before the pipeline sees them.
# ---------------------------------------------------------------------------


class WorkerHypothesis(BaseModel):
    hypothesis_id: Optional[str] = None
    hypothesis_title_vi: str = Field(..., min_length=3)
    mechanism_vi: str = Field(..., min_length=10)
    prolog_predicates: Dict[str, float] = Field(default_factory=dict)
    practical_critic_search_queries: List[str] = Field(default_factory=list)
    sandbox_forced_params: Dict[str, float] = Field(default_factory=dict)
    assumptions: List[str] = Field(default_factory=list)


class PracticalCritique(BaseModel):
    empirical_flaws: List[str] = Field(default_factory=list)
    verified_external_evidence: List[str] = Field(default_factory=list)


class TheoreticalCritique(BaseModel):
    theoretical_risks: List[str] = Field(default_factory=list)
    enforced_sandbox_bounds: List[Dict[str, Any]] = Field(default_factory=list)


class JudgeVote(BaseModel):
    winner_id: Optional[str] = None
    rationale_vi: str = ""


class SandboxWorkerOutput(BaseModel):
    repaired_mechanism_vi: str = Field(..., min_length=10)
    updated_facts: Dict[str, float] = Field(...)
    python_simulation_code: str = Field(..., min_length=20)


__all__ = [
    "ArticleAnalysis",
    "ArticlePayload",
    "EvidenceClaim",
    "FetchedArticle",
    "IndicatorMention",
    "JudgeVote",
    "PracticalCritique",
    "RecheckCheck",
    "RecheckReport",
    "SandboxWorkerOutput",
    "StatsArtifact",
    "StatsRequest",
    "TheoreticalCritique",
    "WorkerHypothesis",
    "WordFilterResult",
    "WorkflowEnvelope",
    "utc_now_iso",
]
