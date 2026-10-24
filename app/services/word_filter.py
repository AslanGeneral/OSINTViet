"""Deterministic lexical and evidence filter for live economics articles.

The LLM analysis is useful, but it must not be the only gate: this module
applies an explicit Vietnamese macro lexicon and verifies every claimed quote
against the fetched article.  The result is a JSON object that is persisted in
the workflow envelope before the statistical stage.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Tuple

from app.core.config import settings
from app.schemas.workflow_schema import (
    ArticleAnalysis,
    FetchedArticle,
    WordFilterResult,
)

_POSITIVE_TERMS: Tuple[str, ...] = (
    "lạm phát",
    "cpi",
    "gdp",
    "tăng trưởng",
    "lãi suất",
    "ngân hàng",
    "tỷ giá",
    "xuất khẩu",
    "nhập khẩu",
    "thất nghiệp",
    "việc làm",
    "giá cả",
    "giá vàng",
    "giá dầu",
    "thị trường",
    "chính sách",
    "tài khóa",
    "tiền tệ",
    "đầu tư",
    "tiêu dùng",
    "sản xuất",
    "doanh nghiệp",
    "ngân sách",
    "tín dụng",
    "hàng hóa",
    "năng lượng",
    "kinh tế",
    "vĩ mô",
    "tài chính",
    "thuế",
    "cung cầu",
    "bán lẻ",
    "sản lượng",
    "thương mại",
    "đầu tư công",
    "nợ công",
    "chỉ số",
    "dự báo",
    "nguồn cung",
    "sức mua",
)

_NEGATIVE_TERMS: Tuple[str, ...] = (
    "khuyến mãi",
    "mua ngay",
    "giảm giá sốc",
    "quảng cáo",
    "tài trợ",
    "tin đồn",
    "giật gân",
    "showbiz",
    "bóng đá",
    "ca sĩ",
    "hoa hậu",
    "phim",
    "tử vong",
    "tai nạn",
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Case-fold and collapse whitespace for exact evidence checks."""
    value = unicodedata.normalize("NFC", value or "")
    return _WHITESPACE_RE.sub(" ", value).strip().casefold()


def filter_analysis_evidence(
    article: FetchedArticle,
    analysis: ArticleAnalysis,
) -> Tuple[ArticleAnalysis, int, int, float]:
    """Keep only claims/indicators whose quotes occur in the article.

    Returns (filtered_analysis, kept_claims, dropped_claims, integrity).
    A quote must be at least 15 characters after normalization.  This is a
    deliberately strict deterministic check, not an LLM self-assessment.
    """
    article_text = normalize_text(
        " ".join(
            part
            for part in (
                article.title,
                article.raw_content,
                article.cleaned_content,
                article.summary,
            )
            if part
        )
    )

    kept_claims = []
    dropped_claims = 0
    for claim in analysis.key_claims:
        quote = normalize_text(claim.evidence_quote)
        if len(quote) >= 15 and quote in article_text:
            kept_claims.append(claim)
        else:
            dropped_claims += 1

    kept_indicators = []
    for indicator in analysis.indicator_mentions:
        quote = normalize_text(indicator.evidence_quote)
        if not quote:
            # An indicator without a quote is only retained when its numeric
            # value is independently visible in the source text.
            if indicator.value is not None and str(indicator.value) in article_text:
                kept_indicators.append(indicator)
            continue
        if len(quote) >= 8 and quote in article_text:
            kept_indicators.append(indicator)

    filtered = analysis.model_copy(
        update={
            "key_claims": kept_claims,
            "indicator_mentions": kept_indicators,
        }
    )
    # Acceptance needs at least one verified quote.  The dropped-claim count is
    # still recorded; a paraphrase-heavy analysis is visible in the JSON.
    integrity = 1.0 if kept_claims else 0.0
    return filtered, len(kept_claims), dropped_claims, integrity


def apply_word_filter(
    article: FetchedArticle,
    analysis: ArticleAnalysis,
    *,
    claims_kept: int,
    claims_dropped: int,
    evidence_integrity: float,
    embedding_relevance: bool | None,
) -> WordFilterResult:
    """Apply the live macro lexicon plus the already-computed signals."""
    text = normalize_text(
        " ".join(
            part
            for part in (
                article.title,
                article.summary,
                article.raw_content,
            )
            if part
        )
    )
    title = normalize_text(article.title)

    positive = sorted({term for term in _POSITIVE_TERMS if term in text})
    negative = sorted({term for term in _NEGATIVE_TERMS if term in text})
    title_hits = sum(1 for term in _POSITIVE_TERMS if term in title)

    lexical_score = min(1.0, (len(positive) + 0.5 * title_hits) / 8.0)
    threshold_ok = (
        len(positive) >= settings.WORKFLOW_WORD_FILTER_MIN_TERMS
        and lexical_score >= settings.WORKFLOW_WORD_FILTER_MIN_SCORE
    )
    analysis_ok = (
        analysis.macro_relevance
        >= settings.WORKFLOW_WORD_FILTER_MIN_ANALYSIS_RELEVANCE
        or lexical_score >= 0.60
    )
    evidence_ok = claims_kept >= 1 and evidence_integrity >= 0.5
    embedding_ok = embedding_relevance is True

    accepted = bool(
        threshold_ok
        and analysis_ok
        and evidence_ok
        and embedding_ok
        and not negative
    )

    reasons: List[str] = []
    if not threshold_ok:
        reasons.append("lexical_macro_score_below_threshold")
    if not analysis_ok:
        reasons.append("llm_macro_relevance_below_threshold")
    if not evidence_ok:
        reasons.append("no_verified_evidence_claim")
    if not embedding_ok:
        reasons.append("embedding_contrastive_filter_rejected")
    if negative:
        reasons.append("blocked_terms:" + ",".join(negative))

    return WordFilterResult(
        article_id=article.article_id or "",
        accepted=accepted,
        lexical_score=round(lexical_score, 4),
        matched_positive_terms=positive,
        matched_negative_terms=negative,
        analysis_macro_relevance=round(analysis.macro_relevance, 4),
        embedding_relevance=embedding_relevance,
        embedding_threshold=settings.INGESTION_CONTRASTIVE_THRESHOLD,
        claims_kept=claims_kept,
        claims_dropped=claims_dropped,
        evidence_integrity=round(evidence_integrity, 4),
        reason_vi=(
            "Đạt bộ lọc từ khóa, bằng chứng trích dẫn và bộ lọc ngữ nghĩa."
            if accepted
            else "Bị loại: " + "; ".join(reasons)
        ),
    )


def article_text_for_embedding(article: FetchedArticle) -> str:
    """Use the same title+summary shape as the ingestion engine."""
    body = article.summary or article.cleaned_content or article.raw_content
    return f"{article.title}. {body}".strip()


def analysis_context(analysis: ArticleAnalysis) -> Dict[str, Any]:
    """Compact JSON context attached to the stats request."""
    return {
        "title_vi": analysis.title_vi,
        "topic_vi": analysis.topic_vi,
        "summary_vi": analysis.summary_vi,
        "macro_relevance": analysis.macro_relevance,
        "key_claims": [claim.model_dump(mode="json") for claim in analysis.key_claims],
        "indicator_mentions": [
            mention.model_dump(mode="json")
            for mention in analysis.indicator_mentions
        ],
        "risk_flags": list(analysis.risk_flags),
    }


__all__ = [
    "analysis_context",
    "apply_word_filter",
    "article_text_for_embedding",
    "filter_analysis_evidence",
    "normalize_text",
]
