"""S1 input and LLM fetch wrapper.

The live article path reuses the existing headless Chromium fetcher and the
existing ``FullCircleWorkflow._analyze_and_filter`` method; no fetch or
evidence-filter logic is re-implemented here.  When live fetching is not
requested, a deterministic query-only material is produced and the fallback is
recorded as a warning.

The personal context stays in memory.  ``RawMaterial`` never carries it, so the
cache log cannot persist it unless the caller deliberately writes it after
``persist_opt_in`` is checked.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.pipeline_v2.config import InputFetchConfig, PipelineV2Config, load_pipeline_config
from app.pipeline_v2.contracts import (
    PersonalContext,
    QueryBreakdown,
    RawInput,
    RawMaterial,
    parse_household_numbers,
)
from app.pipeline_v2.errors import FetchError

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


class _LLMBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    macro_topics: List[str] = []
    micro_topics: List[str] = []
    indicators: List[str] = []
    personal_dimensions: List[str] = []
    search_queries: List[str] = []


def _dedupe(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _context_text(context: Optional[PersonalContext]) -> str:
    if context is None:
        return ""
    return "\n".join(
        part
        for part in (
            context.free_text,
            context.region,
            context.occupation_sector,
            context.household_notes,
            context.assets_liabilities_notes,
        )
        if part
    )


def build_query_breakdown(
    run_id: str,
    query: str,
    context: Optional[PersonalContext],
    config: Optional[PipelineV2Config] = None,
    *,
    llm_payload: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> QueryBreakdown:
    """Build the deterministic S1 decomposition.

    Numbers are parsed only from explicitly written query/context text.  The
    optional LLM payload can add topics and search phrases, but any numeric
    parameter in its response is discarded.
    """
    cfg = config or load_pipeline_config()
    combined = f"{query}\n{_context_text(context)}".strip()
    lowered = combined.casefold()

    macro_topics: List[str] = []
    micro_topics: List[str] = []
    for term in cfg.classifier.macro_lexicon:
        if term.casefold() in lowered:
            tag = cfg.classifier.topic_tags.get(term.casefold())
            macro_topics.append(tag or term)
    for term in cfg.classifier.micro_lexicon:
        if term.casefold() in lowered:
            tag = cfg.classifier.topic_tags.get(term.casefold())
            micro_topics.append(tag or term)

    indicators: List[str] = []
    for code, terms in cfg.query_breakdown.indicator_terms.items():
        if any(term.casefold() in lowered for term in terms):
            indicators.append(code)

    dimensions: List[str] = []
    for name, terms in cfg.query_breakdown.personal_dimension_terms.items():
        if any(term.casefold() in lowered for term in terms):
            dimensions.append(name)

    explicit_parameters: Dict[str, Any] = {}
    query_profile = parse_household_numbers(query)
    for key, value in query_profile.model_dump(exclude_none=True).items():
        if key in {"notes", "equiv_method"}:
            continue
        explicit_parameters[key] = value
    if context is not None:
        context_profile = parse_household_numbers(
            " ".join(
                part
                for part in (
                    context.free_text,
                    context.household_notes,
                    context.assets_liabilities_notes,
                )
                if part
            )
        )
        # Explicit context numbers are kept in memory only and are not marked
        # for persistence here.  They enrich the engine, not the cache payload.
        for key, value in context_profile.model_dump(exclude_none=True).items():
            if key in {"notes", "equiv_method"}:
                continue
            explicit_parameters.setdefault(key, value)

    search_queries = [
        template.format(query=query).strip()
        for template in cfg.query_breakdown.search_query_templates
    ]

    if llm_payload:
        macro_topics.extend(str(item) for item in llm_payload.get("macro_topics") or [])
        micro_topics.extend(str(item) for item in llm_payload.get("micro_topics") or [])
        indicators.extend(str(item) for item in llm_payload.get("indicators") or [])
        dimensions.extend(
            str(item) for item in llm_payload.get("personal_dimensions") or []
        )
        search_queries.extend(
            str(item) for item in llm_payload.get("search_queries") or []
        )

    return QueryBreakdown(
        run_id=run_id,
        query=query,
        macro_topics=_dedupe(macro_topics),
        micro_topics=_dedupe(micro_topics),
        indicators=_dedupe(indicators),
        personal_dimensions=_dedupe(dimensions),
        explicit_parameters=explicit_parameters,
        search_queries=_dedupe(search_queries) or [query],
        method="deterministic+llm" if llm_payload else "deterministic",
        warnings=list(warnings or []),
    )


async def _llm_breakdown(
    run_id: str,
    query: str,
    context: Optional[PersonalContext],
    router: Any,
    config: PipelineV2Config,
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    if router is None or not config.input_fetch.query_breakdown_llm_enabled:
        return None
    context_text = _context_text(context)
    system = (
        "You decompose an economics question into search-oriented topics. "
        "Return JSON only. Never invent or repeat numeric amounts; numeric "
        "parameters are parsed separately by code."
    )
    prompt = (
        "Decompose the request into macro topics, micro/household topics, World "
        "Bank indicator codes, personal dimensions present in the text, and "
        "Vietnamese search queries. Only use information explicitly present.\n"
        f"REQUEST: {query}\nCONTEXT: {context_text or '(none)'}"
    )
    try:
        payload = await router.generate_structured(
            system_prompt=system,
            user_prompt=prompt,
            response_model=_LLMBreakdown,
            role="query_breakdown",
            temperature=0.0,
            max_attempts=2,
        )
        return payload.model_dump(mode="python")
    except Exception as exc:
        warnings.append(
            "LLM query breakdown thất bại; dùng phân rã tất định: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def offline_material(
    raw_input: RawInput,
    context: Optional[PersonalContext],
    config: Optional[PipelineV2Config] = None,
    *,
    warnings: Optional[List[str]] = None,
) -> RawMaterial:
    cfg = config or load_pipeline_config()
    warning_list = list(warnings or [])
    warning_list.append(
        "Không dùng bài báo live; S1 tạo raw material từ truy vấn người dùng."
    )
    breakdown = build_query_breakdown(
        raw_input.run_id,
        raw_input.query,
        context,
        cfg,
        warnings=warning_list,
    )
    return RawMaterial(
        run_id=raw_input.run_id,
        query=raw_input.query,
        requested_mode=raw_input.requested_mode,
        article_title=raw_input.query[:180],
        article_source="user_query",
        article_content="",
        analysis={},
        word_filter={},
        query_breakdown=breakdown,
        source_refs=list(raw_input.source_refs),
        warnings=warning_list,
    )


async def live_material(
    raw_input: RawInput,
    context: Optional[PersonalContext],
    config: Optional[PipelineV2Config] = None,
    *,
    router: Any = None,
) -> RawMaterial:
    """Fetch and analyze one live economics article through existing code."""
    cfg = config or load_pipeline_config()
    warnings: List[str] = []
    try:
        from app.services.full_circle_workflow import FullCircleWorkflow
        from app.scrapers.econ_article_fetcher import HeadlessEconArticleFetcher
    except Exception as exc:  # pragma: no cover - dependency failure
        raise FetchError(
            f"Không import được live fetch path: {type(exc).__name__}: {exc}"
        ) from exc

    fetcher = HeadlessEconArticleFetcher()
    excluded: set[str] = set()
    last_errors: List[str] = []
    article = None
    analysis_payload: Dict[str, Any] = {}
    for attempt in range(1, int(cfg.input_fetch.fetch_attempts) + 1):
        try:
            article = await fetcher.fetch_random(exclude_urls=excluded)
        except Exception as exc:
            last_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            continue
        excluded.add(article.link)
        if router is not None and cfg.input_fetch.analyze_article:
            try:
                workflow = FullCircleWorkflow()
                analysis, kept, dropped, integrity = (
                    await workflow._analyze_and_filter(router, article)
                )
                analysis_payload = analysis.model_dump(mode="json")
                analysis_payload["_evidence"] = {
                    "claims_kept": kept,
                    "claims_dropped": dropped,
                    "integrity": integrity,
                }
            except Exception as exc:
                warnings.append(
                    "Phân tích bài báo bằng LLM thất bại; raw material vẫn được "
                    f"giữ nhưng analysis rỗng: {type(exc).__name__}: {exc}"
                )
                analysis_payload = {}
        break

    if article is None:
        detail = " | ".join(last_errors[-3:]) or "không có bài báo hợp lệ"
        if cfg.input_fetch.require_live_article:
            raise FetchError(
                f"Không fetch được bài báo live: {detail}",
                suggestion="Kiểm tra mạng/Chromium hoặc dùng --no-live.",
            )
        warnings.append(
            "Live fetch thất bại; chuyển sang raw material từ truy vấn: " + detail
        )
        fallback = offline_material(raw_input, context, cfg, warnings=warnings)
        return fallback

    breakdown = build_query_breakdown(
        raw_input.run_id,
        raw_input.query,
        context,
        cfg,
        warnings=warnings,
    )
    content = (article.raw_content or article.cleaned_content or article.summary)[
        : cfg.input_fetch.max_article_chars
    ]
    return RawMaterial(
        run_id=raw_input.run_id,
        query=raw_input.query,
        requested_mode=raw_input.requested_mode,
        article_title=article.title,
        article_url=article.link,
        article_source=article.source,
        article_content=content,
        analysis=analysis_payload,
        word_filter={},
        query_breakdown=breakdown,
        source_refs=_dedupe(
            [*raw_input.source_refs, article.link]
            if article.link
            else raw_input.source_refs
        ),
        warnings=warnings,
    )


async def fetch_material(
    raw_input: RawInput,
    context: Optional[PersonalContext],
    config: Optional[PipelineV2Config] = None,
    *,
    router: Any = None,
    use_live: Optional[bool] = None,
) -> RawMaterial:
    """Run S1 and attach an optional LLM query decomposition."""
    cfg = config or load_pipeline_config()
    live = cfg.input_fetch.require_live_article if use_live is None else use_live
    if live:
        material = await live_material(raw_input, context, cfg, router=router)
    else:
        material = offline_material(raw_input, context, cfg)
        # When a router exists, still allow the optional LLM decomposition;
        # it never sees or returns API keys.
        warnings = list(material.warnings)
        payload = await _llm_breakdown(
            raw_input.run_id,
            raw_input.query,
            context,
            router,
            cfg,
            warnings,
        )
        if payload:
            material.query_breakdown = build_query_breakdown(
                raw_input.run_id,
                raw_input.query,
                context,
                cfg,
                llm_payload=payload,
                warnings=warnings,
            )
        material.warnings = warnings
    return material


__all__ = [
    "build_query_breakdown",
    "fetch_material",
    "live_material",
    "offline_material",
]
