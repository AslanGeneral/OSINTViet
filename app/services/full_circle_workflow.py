"""Forced full-circle live workflow.

Flow:
    reset runtime memory (optional)
    -> random live article via headless Chromium
    -> LLM JSON analysis with exact-quote verification
    -> deterministic Vietnamese macro word filter + embedding filter
    -> real World Bank series -> strict JSON StatsRequest -> ARIMA-GARCH
    -> universal orchestrator with JSON-schema-constrained LLM calls and the
       ARIMA-GARCH theory plugin
    -> JSON output
    -> deterministic JSON recheck (SQLite, Chroma, ARIMA, LLM trace, sandbox)

No stage consumes free-form LLM prose.  All stage payloads are Pydantic/JSON
objects, and the final envelope is stored in SQLite ``workflow_runs`` as well as
``/tmp/osint_workflow/<run_id>/workflow.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.blackboard import SharedBlackboard
from app.core.config import settings
from app.core.database import (
    delete_all_articles,
    delete_all_workflow_runs,
    get_article,
    get_workflow_run,
    init_db,
    save_workflow_run,
)
from app.core.plugin_registry import hub
from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline
from app.plugins import load_plugins
from app.scrapers.econ_article_fetcher import HeadlessEconArticleFetcher
from app.schemas.workflow_schema import (
    ArticleAnalysis,
    EvidenceClaim,
    FetchedArticle,
    RecheckCheck,
    RecheckReport,
    StatsArtifact,
    StatsRequest,
    WordFilterResult,
    WorkflowEnvelope,
    utc_now_iso,
)
from app.services.economic_series import (
    build_stats_request,
    execute_arima_garch,
)
from app.services.embedding_engine import embedding_engine
from app.services.json_router import build_orchestrator_router
from app.services.word_filter import (
    analysis_context,
    apply_word_filter,
    article_text_for_embedding,
    filter_analysis_evidence,
)

_RUN_LOCK = asyncio.Lock()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"wf_{stamp}_{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return str(path)


def _first_quote(text: str, min_length: int = 30) -> Optional[str]:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        candidate = sentence.strip()
        if len(candidate) >= min_length:
            return candidate
    stripped = (text or "").strip()
    return stripped[:300] if len(stripped) >= min_length else None


class FullCircleWorkflow:
    """One locked, auditable end-to-end live run."""

    def __init__(
        self,
        model: Optional[str] = None,
        artifact_dir: Optional[str] = None,
    ) -> None:
        self.model = model or settings.LOCAL_MODEL_NAME
        self.artifact_dir = Path(
            artifact_dir or settings.WORKFLOW_ARTIFACT_DIR
        ).expanduser()

    async def run(
        self,
        query: Optional[str] = None,
        domain_tag: str = "macro_econ",
        reset_memory: bool = False,
        fetch_attempts: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> WorkflowEnvelope:
        async with _RUN_LOCK:
            return await self._run_locked(
                query=query,
                domain_tag=domain_tag,
                reset_memory=reset_memory,
                fetch_attempts=fetch_attempts,
                run_id=run_id or new_run_id(),
            )

    async def _run_locked(
        self,
        *,
        query: Optional[str],
        domain_tag: str,
        reset_memory: bool,
        fetch_attempts: Optional[int],
        run_id: str,
    ) -> WorkflowEnvelope:
        await init_db()
        envelope = WorkflowEnvelope(
            run_id=run_id,
            status="running",
            started_at=utc_now_iso(),
            domain_tag=domain_tag,
            query=query or "",
            config={
                "llm_model": self.model,
                "embedding_provider": embedding_engine.provider,
                "embedding_model": (
                    embedding_engine.ollama_model
                    if embedding_engine.provider == "embeddinggemma"
                    else embedding_engine.cohere_model
                ),
                "chroma_collection": settings.CHROMA_COLLECTION_NAME,
                "database_url": settings.DATABASE_URL,
                "arima_forecast_steps": settings.WORKFLOW_ARIMA_FORECAST_STEPS,
                "artifact_dir": str(self.artifact_dir),
            },
        )

        if reset_memory:
            envelope.reset_report = await reset_runtime_memory(self.artifact_dir)
        else:
            envelope.reset_report = {"enabled": False}

        run_dir = self.artifact_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        envelope.artifacts["run_dir"] = str(run_dir)
        trace: List[Dict[str, Any]] = []
        router = build_orchestrator_router(trace=trace, model=self.model)
        fetcher: Optional[HeadlessEconArticleFetcher] = None

        # Preload the local model once before the first structured call.  This
        # avoids a cold-start timeout and keeps the same 4096-token context for
        # every subsequent JSON call.  A cloud router has no warmup method.
        warmup = getattr(router, "warmup", None)
        if callable(warmup):
            try:
                warmup_result = await warmup()
                envelope.artifacts["llm_warmup_json"] = _write_json(
                    run_dir / "llm_warmup.json", warmup_result
                )
            except Exception as exc:
                envelope.artifacts["llm_warmup_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )

        try:
            # ---------------------------------------------------------
            # 1-2. Live fetch + LLM analysis + word filter
            # ---------------------------------------------------------
            fetcher = HeadlessEconArticleFetcher()
            max_attempts = max(
                1,
                fetch_attempts or settings.WORKFLOW_ECON_FETCH_MAX_ATTEMPTS,
            )
            excluded: set[str] = set()
            accepted_article: Optional[FetchedArticle] = None
            accepted_analysis: Optional[ArticleAnalysis] = None
            accepted_filter: Optional[WordFilterResult] = None
            fetch_errors: List[str] = []

            for attempt in range(1, max_attempts + 1):
                try:
                    article = await fetcher.fetch_random(exclude_urls=excluded)
                except Exception as exc:
                    fetch_errors.append(
                        f"fetch attempt {attempt}: {type(exc).__name__}: {exc}"
                    )
                    break
                excluded.add(article.link)
                envelope.article = article
                envelope.artifacts["article_json"] = _write_json(
                    run_dir / "article.json", article.model_dump(mode="json")
                )

                analysis, kept, dropped, integrity = await self._analyze_and_filter(
                    router, article
                )
                envelope.analysis = analysis
                envelope.artifacts["analysis_json"] = _write_json(
                    run_dir / "analysis.json", analysis.model_dump(mode="json")
                )

                relevance_ok = await embedding_engine.is_macro_relevant(
                    article_text_for_embedding(article),
                    threshold=settings.INGESTION_CONTRASTIVE_THRESHOLD,
                )
                filtered = apply_word_filter(
                    article,
                    analysis,
                    claims_kept=kept,
                    claims_dropped=dropped,
                    evidence_integrity=integrity,
                    embedding_relevance=relevance_ok,
                )
                envelope.word_filter = filtered
                envelope.artifacts["word_filter_json"] = _write_json(
                    run_dir / "word_filter.json", filtered.model_dump(mode="json")
                )
                await self._persist(envelope)

                if filtered.accepted:
                    accepted_article = article
                    accepted_analysis = analysis
                    accepted_filter = filtered
                    break

                fetch_errors.append(
                    f"attempt {attempt}: rejected {article.link}: "
                    f"{filtered.reason_vi}"
                )

            if (
                accepted_article is None
                or accepted_analysis is None
                or accepted_filter is None
            ):
                raise RuntimeError(
                    "No live economics article passed the JSON analysis and word "
                    "filter. " + " | ".join(fetch_errors[-5:])
                )

            envelope.article = accepted_article
            envelope.analysis = accepted_analysis
            envelope.word_filter = accepted_filter

            # ---------------------------------------------------------
            # 3. Stats input JSON -> real series -> ARIMA-GARCH JSON
            # ---------------------------------------------------------
            stats_request = await build_stats_request(
                accepted_article, accepted_analysis
            )
            envelope.stats_request = stats_request
            envelope.artifacts["stats_request_json"] = _write_json(
                run_dir / "stats_request.json",
                stats_request.model_dump(mode="json"),
            )
            await self._persist(envelope)

            stats_artifact = execute_arima_garch(
                stats_request,
                forecast_steps=settings.WORKFLOW_ARIMA_FORECAST_STEPS,
            )
            envelope.stats_artifact = stats_artifact
            stats_result_path = _write_json(
                run_dir / "stats_result.json",
                stats_artifact.model_dump(mode="json"),
            )
            envelope.artifacts["stats_result_json"] = stats_result_path
            if stats_artifact.status != "OK":
                raise RuntimeError(
                    "ARIMA-GARCH failed: " + str(stats_artifact.error)
                )
            await self._persist(envelope)

            # ---------------------------------------------------------
            # 4. Orchestrator with JSON-constrained calls + ARIMA plugin
            # ---------------------------------------------------------
            load_plugins()
            arima_invocation_dir = run_dir / "arima_invocations"
            arima_invocation_dir.mkdir(parents=True, exist_ok=True)
            envelope.artifacts["arima_invocations_dir"] = str(arima_invocation_dir)
            os.environ["ARIMA_GARCH_STATS_PATH"] = stats_result_path
            os.environ["ARIMA_GARCH_INVOCATION_DIR"] = str(arima_invocation_dir)

            # Isolate negative memory to this run.  The module constant is
            # read by NegativeMemoryCompiler at call time.
            import app.memory.negative_memory_compiler as negative_memory_module

            compiled_memory_path = run_dir / "verifier_compiled_memory.json"
            negative_memory_module.COMPILED_MEMORY_PATH = str(compiled_memory_path)
            envelope.artifacts["compiled_memory_json"] = str(compiled_memory_path)

            effective_query = query or self._build_query(
                accepted_article, accepted_analysis, stats_request
            )
            envelope.query = effective_query
            raw_sources = [accepted_article.model_dump(mode="json")]
            parameter_bounds = [
                {
                    "metric_name": "macro_signal_score",
                    "operator": ">=",
                    "threshold_value": 0.5,
                    "unit": "score",
                }
            ]

            blackboard = SharedBlackboard(task_id=run_id)
            pipeline = UniversalCoScientistPipeline(router, blackboard)
            # The live article was fetched with headless Chromium.  Existing
            # sample ingestors would add other HTTP-fetched articles to the
            # same run, making the envelope and SQLite/Chroma evidence
            # ambiguous.  Disable only the raw-data ingestor hooks for the
            # duration of the orchestration; critics/sandbox/theory plugins
            # remain registered.
            original_ingestors = dict(hub.data_ingestors)
            disabled_ingestors = sorted(original_ingestors)
            hub.data_ingestors.clear()
            try:
                output = await pipeline.run(
                    effective_query,
                    domain_tag,
                    raw_sources,
                    parameter_bounds=parameter_bounds,
                )
            finally:
                hub.data_ingestors.update(original_ingestors)
            envelope.orchestration_output = output.model_dump(mode="json")
            envelope.orchestration_meta = {
                "task_id": output.task_id,
                "domain_tag": output.domain_tag,
                "viable_count": len(output.viable_pareto_frontier),
                "dead_count": len(output.rejected_dead_branches),
                "compiled_negative_rules": len(
                    output.newly_compiled_negative_rules
                ),
                "blackboard_state": blackboard.state,
                "negative_constraints": blackboard.negative_constraints,
                "theory_algorithms": sorted(hub.theory_algorithms.keys()),
                "disabled_ingestor_plugins": disabled_ingestors,
                "router": type(router).__name__,
                "model": getattr(router, "last_model", self.model),
            }
            envelope.llm_calls = trace
            envelope.artifacts["llm_calls_json"] = _write_json(
                run_dir / "llm_calls.json", trace
            )
            if compiled_memory_path.exists():
                envelope.artifacts["compiled_memory_json"] = str(compiled_memory_path)
            await self._persist(envelope)

            # ---------------------------------------------------------
            # 5. Recheck
            # ---------------------------------------------------------
            recheck = await self._recheck(envelope, arima_invocation_dir)
            envelope.recheck = recheck
            if not recheck.passed:
                envelope.status = "failed_recheck"
            elif not recheck.business_passed:
                envelope.status = "succeeded_with_dead_branch"
            else:
                envelope.status = "succeeded"
            envelope.finished_at = utc_now_iso()
            await self._persist(envelope)
            return envelope

        except Exception as exc:
            envelope.errors.append(f"{type(exc).__name__}: {exc}")
            envelope.llm_calls = trace
            envelope.finished_at = utc_now_iso()
            if envelope.recheck is not None and not envelope.recheck.passed:
                envelope.status = "failed_recheck"
            else:
                envelope.status = "failed"
            await self._persist(envelope)
            return envelope
        finally:
            try:
                await router.close()
            except Exception:
                pass
            if fetcher is not None:
                # The fetcher closes its own browser per call; this is a
                # no-op placeholder for future persistent-browser reuse.
                pass

    # ------------------------------------------------------------------
    # Article analysis / evidence
    # ------------------------------------------------------------------
    async def _analyze_and_filter(
        self,
        router: Any,
        article: FetchedArticle,
    ) -> Tuple[ArticleAnalysis, int, int, float]:
        article_text = article.raw_content or article.cleaned_content or article.summary
        prompt = (
            "You are an evidence-bound macroeconomics analyst. Read the ARTICLE "
            "and return the required JSON object. Rules:\n"
            "1. Use only facts explicitly present in the article.\n"
            "2. Every key_claim must include an evidence_quote copied exactly "
            "from the article (at least 15 characters); do not paraphrase quotes.\n"
            "3. indicator_mentions must use a World Bank indicator code only when "
            "the article clearly refers to it; otherwise use an empty list.\n"
            "4. topic_vi is a short Vietnamese topic; summary_vi is 2-3 "
            "Vietnamese sentences. macro_relevance and confidence are numbers "
            "from 0 to 1; use >= 0.5 when the article concerns taxes, prices, "
            "interest rates, GDP, jobs, trade, or macro policy.\n"
            "5. key_claims, indicator_mentions, and risk_flags must always be "
            "present (use [] when empty).\n"
            "6. Do not invent numbers, sources, dates, or causal claims.\n\n"
            f"ARTICLE_ID: {article.article_id}\n"
            f"TITLE: {article.title}\n"
            f"URL: {article.link}\n"
            f"ARTICLE:\n{article_text[:6000]}"
        )
        system = (
            "You are a JSON-only analyst. Return one object matching the schema. "
            "If a claim cannot be supported by an exact quote, omit that claim."
        )

        analysis = await router.generate_structured(
            system_prompt=system,
            user_prompt=prompt,
            response_model=ArticleAnalysis,
            role="article_analyst",
            temperature=0.1,
            max_attempts=2,
        )
        filtered, kept, dropped, integrity = filter_analysis_evidence(
            article, analysis
        )

        if kept == 0:
            retry_prompt = (
                prompt
                + "\n\nCORRECTION: The previous attempt had no exact quote. "
                "Return at least one key_claim whose evidence_quote is copied "
                "verbatim from the ARTICLE text. Do not paraphrase."
            )
            retry = await router.generate_structured(
                system_prompt=system,
                user_prompt=retry_prompt,
                response_model=ArticleAnalysis,
                role="article_analyst",
                temperature=0.05,
                max_attempts=2,
            )
            retry_filtered, retry_kept, retry_dropped, retry_integrity = (
                filter_analysis_evidence(article, retry)
            )
            if retry_kept > kept:
                filtered, kept, dropped, integrity = (
                    retry_filtered,
                    retry_kept,
                    retry_dropped,
                    retry_integrity,
                )

        if kept == 0:
            quote = _first_quote(article_text, min_length=30)
            if quote:
                filtered.key_claims.append(
                    EvidenceClaim(claim_vi=quote, evidence_quote=quote)
                )
                filtered.risk_flags.append("deterministic_evidence_fallback")
                kept = 1
                integrity = 1.0

        filtered.article_id = article.article_id or ""
        filtered.title_vi = filtered.title_vi or article.title
        return filtered, kept, dropped, integrity

    @staticmethod
    def _build_query(
        article: FetchedArticle,
        analysis: ArticleAnalysis,
        stats_request: StatsRequest,
    ) -> str:
        return (
            f"Phân tích tác động kinh tế vĩ mô của bài báo '{article.title}'. "
            f"Bối cảnh: {analysis.topic_vi or analysis.summary_vi or article.summary[:300]}. "
            f"Đối chiếu với chuỗi thật {stats_request.series_name} "
            f"({stats_request.indicator_code}, {stats_request.point_count} điểm). "
            "Đề xuất cơ chế truyền dẫn, rủi ro và khuyến nghị có thể kiểm chứng; "
            "không bịa số liệu hoặc nguồn."
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def _persist(self, envelope: WorkflowEnvelope) -> None:
        run_dir = Path(envelope.artifacts.get("run_dir") or self.artifact_dir / envelope.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        workflow_path = run_dir / "workflow.json"
        envelope.artifacts["workflow_json"] = str(workflow_path)
        payload = envelope.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        tmp = workflow_path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, workflow_path)
        await save_workflow_run(
            envelope.run_id,
            envelope.status,
            envelope.query,
            text,
        )

    # ------------------------------------------------------------------
    # Recheck
    # ------------------------------------------------------------------
    async def _recheck(
        self,
        envelope: WorkflowEnvelope,
        arima_invocation_dir: Path,
    ) -> RecheckReport:
        checks: List[RecheckCheck] = []

        def add(
            name: str,
            passed: bool,
            details: str = "",
            required: bool = True,
        ) -> None:
            checks.append(
                RecheckCheck(
                    name=name,
                    passed=bool(passed),
                    required=required,
                    details=details,
                )
            )

        article = envelope.article
        analysis = envelope.analysis
        word_filter = envelope.word_filter
        stats_request = envelope.stats_request
        stats_artifact = envelope.stats_artifact
        output = envelope.orchestration_output or {}

        add(
            "article_live_url",
            bool(article and article.link.startswith(("http://", "https://"))),
            article.link if article else "missing article",
        )
        add(
            "article_content_length",
            bool(article and len(article.raw_content) >= 400),
            f"{len(article.raw_content) if article else 0} chars",
        )
        add(
            "analysis_has_verified_quote",
            bool(analysis and len(analysis.key_claims) >= 1),
            f"{len(analysis.key_claims) if analysis else 0} claims",
        )
        add(
            "word_filter_accepted",
            bool(word_filter and word_filter.accepted),
            word_filter.reason_vi if word_filter else "missing",
        )

        if stats_request is None:
            add("stats_request_json", False, "missing StatsRequest")
            add("stats_source_real", False, "missing StatsRequest")
        else:
            add(
                "stats_request_json",
                stats_request.point_count >= settings.WORKFLOW_MIN_STATS_POINTS
                and len(stats_request.dates) == len(stats_request.values),
                f"{stats_request.point_count} observations, {stats_request.indicator_code}",
            )
            add(
                "stats_source_real",
                stats_request.source == "world_bank"
                and stats_request.source_url.startswith("https://"),
                stats_request.source_url,
            )

        if stats_artifact is None or stats_artifact.status != "OK" or not stats_artifact.response:
            add("arima_artifact_ok", False, "missing or failed StatsArtifact")
            add("arima_forecast_bounds", False, "missing forecast")
            add("arima_request_matches", False, "missing request")
        else:
            response = stats_artifact.response
            forecast = list(response.get("forecast_data") or [])
            bounds_ok = bool(forecast) and all(
                float(item["lower_bound_95"])
                <= float(item["predicted_mean"])
                <= float(item["upper_bound_95"])
                for item in forecast
            )
            add(
                "arima_artifact_ok",
                stats_artifact.status == "OK",
                f"order={response.get('diagnostics', {}).get('selected_order')}",
            )
            add(
                "arima_forecast_bounds",
                bounds_ok,
                f"{len(forecast)} forecasts at {stats_artifact.forecast_frequency}",
            )
            same_request = bool(
                stats_request
                and stats_artifact.request
                and stats_artifact.request.model_dump(mode="json")
                == stats_request.model_dump(mode="json")
            )
            add(
                "arima_request_matches",
                same_request,
                "StatsArtifact.request equals StatsRequest",
            )

        # SQLite article row + canonical JSON payload.
        article_row = None
        if article and article.article_id:
            try:
                article_row = await get_article(article.article_id)
            except Exception as exc:
                add("sqlite_article_row", False, f"{type(exc).__name__}: {exc}")
        payload_ok = False
        if article_row is not None:
            try:
                payload = json.loads(article_row.payload_json)
                payload_ok = (
                    payload.get("article_id") == article.article_id
                    and payload.get("url") == article.link
                )
            except Exception:
                payload_ok = False
        add(
            "sqlite_article_row",
            article_row is not None,
            article.article_id if article else "missing",
        )
        add(
            "sqlite_article_payload_json",
            payload_ok,
            "validated JSON payload with matching article_id/url",
        )

        # Workflow ledger row.
        run_row = None
        try:
            run_row = await get_workflow_run(envelope.run_id)
        except Exception as exc:
            add("sqlite_workflow_ledger", False, f"{type(exc).__name__}: {exc}")
        ledger_ok = False
        if run_row is not None:
            try:
                ledger_payload = json.loads(run_row.payload_json)
                ledger_ok = ledger_payload.get("run_id") == envelope.run_id
            except Exception:
                ledger_ok = False
        add(
            "sqlite_workflow_ledger",
            run_row is not None and ledger_ok,
            envelope.run_id,
        )

        # Chroma retrieval of the exact article URL.
        chroma_ok = False
        chroma_detail = "missing article"
        if article:
            try:
                hits = await embedding_engine.semantic_search(article.title, top_k=2)
                chroma_ok = any(
                    str((hit.get("metadata") or {}).get("link") or "") == article.link
                    for hit in hits
                    if hit.get("document")
                )
                chroma_detail = f"{len(hits)} hits; statuses={[h.get('search_status') for h in hits]}"
            except Exception as exc:
                chroma_detail = f"{type(exc).__name__}: {exc}"
        add("chroma_article_retrieval", chroma_ok, chroma_detail)

        # LLM trace: every final call must be parsed JSON and schema-valid.
        final_calls = [call for call in envelope.llm_calls if call.get("final")]
        final_ok = bool(final_calls) and all(
            call.get("parsed_ok") and call.get("schema_ok") for call in final_calls
        )
        roles = {str(call.get("role") or "") for call in final_calls}
        expected = {
            "structuring_worker",
            "worker",
            "practical_critic",
            "theoretical_critic",
        }
        # The pipeline deliberately skips the Elo tournament when Stage 3
        # proximity merges the worker pool down to one hypothesis.  In that
        # legitimate mode-collapse case there are no judge calls; they are
        # required only when at least two hypotheses survived to the frontier.
        surviving_count = len(
            output.get("viable_pareto_frontier") or []
        ) + len(output.get("rejected_dead_branches") or [])
        judge_required = surviving_count >= 2
        roles_ok = expected.issubset(roles) and (
            not judge_required
            or bool(roles.intersection({"judge", "judge_a", "judge_b"}))
        )
        add(
            "llm_calls_are_json",
            final_ok,
            f"{len(final_calls)} final calls",
        )
        add(
            "llm_expected_roles_present",
            roles_ok,
            f"roles={sorted(roles)}; judge_required={judge_required}; "
            f"surviving={surviving_count}",
        )

        # ARIMA theory plugin invocation receipt per hypothesis.
        invocations = (
            list(arima_invocation_dir.glob("invocation_*.json"))
            if arima_invocation_dir.exists()
            else []
        )
        add(
            "arima_garch_plugin_invoked",
            len(invocations) >= 1,
            f"{len(invocations)} invocation receipts",
        )

        # Orchestrator output shape and viable sandbox proof.
        output_shape_ok = (
            isinstance(output, dict)
            and bool(output.get("task_id"))
            and isinstance(output.get("viable_pareto_frontier"), list)
            and isinstance(output.get("rejected_dead_branches"), list)
        )
        add(
            "orchestrator_output_json_shape",
            output_shape_ok,
            f"task_id={output.get('task_id')}",
        )
        viable = output.get("viable_pareto_frontier") or []
        viable_ok = bool(viable) and all(
            isinstance(item, dict)
            and item.get("hypothesis_id")
            and item.get("hypothesis_title_vi")
            and item.get("mechanism_vi")
            and isinstance(item.get("sandbox_certificate"), dict)
            and bool(item["sandbox_certificate"].get("receipt"))
            for item in viable
        )
        add(
            "viable_frontier_with_sandbox_receipt",
            viable_ok,
            f"{len(viable)} viable hypotheses",
            required=False,
        )

        required_checks = [check for check in checks if check.required]
        passed = all(check.passed for check in required_checks)
        business_passed = bool(viable_ok)
        report = RecheckReport(
            passed=passed,
            business_passed=business_passed,
            checks=checks,
            failed_checks=[check.name for check in required_checks if not check.passed],
            llm_call_count=len(envelope.llm_calls),
            final_llm_call_count=len(final_calls),
            checked_at=utc_now_iso(),
        )
        return report

    # ------------------------------------------------------------------
    # Runtime memory reset
    # ------------------------------------------------------------------
    async def reset_runtime_memory(self) -> Dict[str, Any]:
        return await reset_runtime_memory(self.artifact_dir)


async def reset_runtime_memory(artifact_dir: Path | str) -> Dict[str, Any]:
    """Delete runtime examples/vectors/cache and start with a clean memory."""
    artifact_dir = Path(artifact_dir).expanduser()
    report: Dict[str, Any] = {"enabled": True}
    try:
        articles_deleted = await delete_all_articles()
        report["articles_deleted"] = articles_deleted
    except Exception as exc:
        report["articles_deleted_error"] = f"{type(exc).__name__}: {exc}"

    try:
        runs_deleted = await delete_all_workflow_runs()
        report["workflow_runs_deleted"] = runs_deleted
    except Exception as exc:
        report["workflow_runs_deleted_error"] = f"{type(exc).__name__}: {exc}"

    chroma_count = 0
    chroma_recreated = False
    try:
        collection = embedding_engine.collection
        chroma_count = int(collection.count())
        client = embedding_engine._chroma_client
        if client is not None:
            client.delete_collection(settings.CHROMA_COLLECTION_NAME)
            chroma_recreated = True
        embedding_engine._collection = None
    except Exception as exc:
        report["chroma_reset_error"] = f"{type(exc).__name__}: {exc}"
        try:
            embedding_engine._collection = None
        except Exception:
            pass
    embedding_engine._pos_anchor_vec = None
    embedding_engine._neg_anchor_vec = None
    if hasattr(embedding_engine, "_last_search_error"):
        embedding_engine._last_search_error = None
    report["chroma_embeddings_deleted"] = chroma_count
    report["chroma_collection_recreated"] = chroma_recreated

    cache_dir = Path(settings.CACHE_DIR)
    cache_files = 0
    if cache_dir.exists():
        cache_files = sum(1 for path in cache_dir.rglob("*") if path.is_file())
        shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    report["llm_cache_files_deleted"] = cache_files

    artifact_files = 0
    if artifact_dir.exists():
        artifact_files = sum(1 for path in artifact_dir.rglob("*") if path.is_file())
        shutil.rmtree(artifact_dir, ignore_errors=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report["artifact_files_deleted"] = artifact_files
    report["compiled_memory_mode"] = "run_scoped"
    return report


__all__ = [
    "FullCircleWorkflow",
    "new_run_id",
    "reset_runtime_memory",
]
