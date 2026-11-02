"""S6/S7 pipeline orchestrator.

Sequence: S1 fetch -> S2 classifier -> S3 cache log -> S4 engine(s) ->
S5 re-validation + purge -> S6 existing universal orchestrator -> S7 output.

The macro cluster and the universal orchestrator are reused through their
existing entry points.  This module only owns run_id, stage ordering, error
handling and the public ``PipelineOutput`` envelope.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.pipeline_v2.byok_router import RouterBundle, build_router
from app.pipeline_v2.cache_log import CacheLogStore
from app.pipeline_v2.classifier import InputClassifier
from app.pipeline_v2.config import (
    PipelineV2Config,
    load_calibration,
    load_pipeline_config,
)
from app.pipeline_v2.contracts import (
    CacheLogEntry,
    ClassifierResult,
    EffectiveMode,
    HouseholdProfile,
    MacroResult,
    MicroResult,
    PersonalContext,
    PipelineOutput,
    PurgeReport,
    RawInput,
    RawMaterial,
    RequestedMode,
    StructuredError,
    new_run_id,
    utc_now,
)
from app.pipeline_v2.errors import (
    ERROR_EXIT_CODES,
    CacheLogError,
    FetchError,
    InputError,
    MacroEngineError,
    MicroEngineError,
    PipelineError,
    PurgeError,
    RevalidationError,
    redact_secrets,
)
from app.pipeline_v2.input_fetch import fetch_material
from app.pipeline_v2.macro_adapter import MacroBundle, run_macro
from app.pipeline_v2.micro_engine import MicroBundle, MicroChannelEngine
from app.pipeline_v2.mode_router import resolve_mode
from app.pipeline_v2.revalidation import Revalidator

_ORCHESTRATOR_LOCK = asyncio.Lock()


@dataclass
class PipelineRunResult:
    output: PipelineOutput
    exit_code: int = 0


def _write_json(path: Path, payload: Any, enabled: bool) -> str:
    if not enabled:
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return str(path)


_PERSONAL_RAW_KEYS = ("personal_dimensions", "explicit_parameters")


def _artifact_safe_raw_material(
    raw: RawMaterial, persist_opt_in: bool
) -> Dict[str, Any]:
    """Serialize ``raw`` for disk without leaking opted-out context.

    Personal parameters and dimensions are derived from the personal context.
    They stay in memory for the run but never reach ``raw_material.json`` when
    the caller did not opt in to persistence.
    """
    payload = raw.model_dump(mode="json")
    if not persist_opt_in:
        breakdown = dict(payload.get("query_breakdown") or {})
        for key in _PERSONAL_RAW_KEYS:
            breakdown.pop(key, None)
        breakdown["personal_parameters_redacted"] = True
        payload["query_breakdown"] = breakdown
    return payload


_TRACE_PERSONAL_KEYS = (
    "user_prompt_excerpt",
    "response_excerpt",
    "parsed",
    "validation_error",
    "transport_error",
)


def _artifact_safe_trace(
    trace: Sequence[Dict[str, Any]], persist_opt_in: bool
) -> List[Dict[str, Any]]:
    """Return an auditable LLM trace with no opted-out prompt content.

    Prompt/response hashes and call metadata remain so reviewers can still
    verify which call ran.  The S1 ``query_breakdown`` call embeds the personal
    context in its prompt, so its record is replaced by metadata only.
    """
    if persist_opt_in:
        return [dict(record) for record in trace]
    safe: List[Dict[str, Any]] = []
    for record in trace:
        item = {
            key: value
            for key, value in record.items()
            if key not in _TRACE_PERSONAL_KEYS
        }
        if record.get("role") == "query_breakdown":
            item["prompt_redacted"] = True
            item.pop("user_prompt_sha256", None)
            item.pop("system_prompt_sha256", None)
        safe.append(item)
    return safe


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        config: Optional[PipelineV2Config] = None,
        artifact_dir: Optional[str] = None,
        model: Optional[str] = None,
        embedding_client: Any = None,
        include_orchestration_in_output: bool = False,
    ) -> None:
        self.config = config or load_pipeline_config()
        self.calibration = load_calibration(config=self.config)
        self.artifact_dir = Path(
            artifact_dir or self.config.artifact_dir
        ).expanduser()
        self.model = model
        self._embedding_client = embedding_client
        self.include_orchestration_in_output = include_orchestration_in_output

    # ------------------------------------------------------------------
    # Embedding helper (existing engine only)
    # ------------------------------------------------------------------
    async def _embedding(self) -> Any:
        if self._embedding_client is not None:
            return self._embedding_client
        try:
            from app.services.embedding_engine import embedding_engine

            self._embedding_client = embedding_engine
        except Exception:
            self._embedding_client = None
        return self._embedding_client

    async def _embeddings(self, texts: Sequence[str], warnings: List[str]) -> List[Optional[List[float]]]:
        client = await self._embedding()
        if client is None or not texts:
            return [None] * len(texts)
        try:
            return [
                None if vector is None else [float(item) for item in vector]
                for vector in await client.get_embeddings_batch(
                    list(texts), input_type="search_document"
                )
            ]
        except Exception as exc:
            warnings.append(
                "Không tạo được embedding cho cache log; lưu nội dung không "
                f"vector: {type(exc).__name__}"
            )
            return [None] * len(texts)

    # ------------------------------------------------------------------
    # S3 cache content
    # ------------------------------------------------------------------
    def _cache_contents(
        self,
        raw: RawMaterial,
        persist_opt_in: bool,
    ) -> List[tuple[str, List[str], Dict[str, Any]]]:
        entries: List[tuple[str, List[str], Dict[str, Any]]] = []
        base_refs = list(raw.source_refs)
        entries.append(
            (
                "Truy vấn người dùng: " + raw.query,
                base_refs,
                {"kind": "query", "run_mode": raw.requested_mode},
            )
        )

        breakdown = raw.query_breakdown.model_copy(deep=True)
        if not persist_opt_in:
            # Personal dimensions and amounts parsed from the context stay in
            # memory for this run when the user did not opt in.  The user
            # query itself is already in the first entry.
            breakdown.personal_dimensions = []
            breakdown.explicit_parameters = {}
        entries.append(
            (
                "Phân rã truy vấn: "
                + json.dumps(
                    breakdown.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                base_refs,
                {"kind": "query_breakdown"},
            )
        )

        if raw.article_content:
            entries.append(
                (
                    f"Bài báo: {raw.article_title}\n{raw.article_content}",
                    base_refs,
                    {
                        "kind": "article",
                        "title": raw.article_title,
                        "url": raw.article_url,
                        "source": raw.article_source,
                    },
                )
            )
        analysis = raw.analysis or {}
        if analysis:
            summary = str(analysis.get("summary_vi") or "")
            claims = analysis.get("key_claims") or []
            claim_texts = [
                str(claim.get("claim_vi") or "")
                for claim in claims
                if isinstance(claim, dict)
            ]
            content = "\n".join(
                part for part in [summary, *claim_texts] if part.strip()
            )
            if content:
                entries.append(
                    (
                        "Phân tích bài báo:\n" + content,
                        base_refs,
                        {"kind": "analysis"},
                    )
                )
        return entries

    async def _write_cache(
        self,
        *,
        run_id: str,
        raw: RawMaterial,
        classifier: ClassifierResult,
        context: Optional[PersonalContext],
        store: CacheLogStore,
        persist_opt_in: bool,
        warnings: List[str],
    ) -> List[CacheLogEntry]:
        contents = self._cache_contents(raw, persist_opt_in)
        vectors = await self._embeddings([item[0] for item in contents], warnings)
        entries: List[CacheLogEntry] = []
        for (content, source_refs, metadata), vector in zip(contents, vectors):
            entries.append(
                await store.write_entry(
                    run_id=run_id,
                    content=content,
                    classifier=classifier,
                    source_refs=source_refs,
                    personal_context=context,
                    persist_personal_context=persist_opt_in,
                    metadata=metadata,
                    embedding=vector,
                )
            )
        return entries

    # ------------------------------------------------------------------
    # S6 universal orchestrator
    # ------------------------------------------------------------------
    async def _run_orchestrator(
        self,
        *,
        run_id: str,
        raw: RawMaterial,
        macro: Optional[MacroResult],
        micro: Optional[MicroResult],
        bundle: RouterBundle,
        run_dir: Path,
        write_artifacts: bool,
        persist_opt_in: bool,
        warnings: List[str],
    ) -> Optional[Dict[str, Any]]:
        async with _ORCHESTRATOR_LOCK:
            try:
                from app.core.blackboard import SharedBlackboard
                from app.core.plugin_registry import hub
                from app.orchestrator.universal_pipeline import (
                    UniversalCoScientistPipeline,
                )
                from app.plugins import load_plugins

                load_plugins()
                # The v5.7 theory plugin is already registered by load_plugins.
                if macro is not None:
                    macro_path = macro.model_dump(mode="json").get(
                        "artifact_path", ""
                    )
                    if macro_path:
                        os.environ["MACRO_STRESS_ARTIFACT_PATH"] = str(
                            macro_path
                        )
                invocation_dir = run_dir / "macro_stress_invocations"
                if write_artifacts:
                    invocation_dir.mkdir(parents=True, exist_ok=True)
                    os.environ["MACRO_STRESS_INVOCATION_DIR"] = str(
                        invocation_dir
                    )

                import app.memory.negative_memory_compiler as negative_memory

                compiled_path = run_dir / "verifier_compiled_memory.json"
                negative_memory.COMPILED_MEMORY_PATH = str(compiled_path)

                raw_sources: List[Dict[str, Any]] = [
                    {
                        "title": raw.article_title or raw.query[:180],
                        "url": raw.article_url,
                        "source": raw.article_source or "pipeline_v2",
                        "raw_content": raw.article_content or raw.query,
                        "summary": str(
                            (raw.analysis or {}).get("summary_vi") or raw.query
                        ),
                    }
                ]
                if macro is not None:
                    raw_sources.append(
                        {
                            "title": "MacroResult v5.7",
                            "source": "pipeline_v2_macro",
                            "raw_content": macro.summary,
                            "summary": macro.summary,
                        }
                    )
                if micro is not None:
                    chain_text = "\n".join(
                        f"{chain.channel}: {chain.direction}; {chain.mechanism}"
                        for chain in micro.transmission_chains
                    )
                    raw_sources.append(
                        {
                            "title": "MicroResult",
                            "source": "pipeline_v2_micro",
                            "raw_content": chain_text,
                            "summary": chain_text[:1000],
                        }
                    )

                effective_query = raw.query
                if macro is not None:
                    effective_query += (
                        "\n\nBối cảnh vĩ mô đã kiểm chứng: " + macro.summary
                    )
                if micro is not None:
                    effective_query += (
                        "\n\nCơ chế truyền dẫn vi mô: "
                        + "; ".join(
                            chain.channel for chain in micro.transmission_chains
                        )
                    )
                effective_query += (
                    "\n\nHãy tổng hợp thành khuyến nghị tiếng Việt có thể kiểm "
                    "chứng; không bịa số liệu hoặc nguồn."
                )

                blackboard = SharedBlackboard(task_id=run_id)
                pipeline = UniversalCoScientistPipeline(bundle.router, blackboard)
                original_ingestors = dict(hub.data_ingestors)
                if self.config.orchestrator.disable_raw_ingestors:
                    hub.data_ingestors.clear()
                try:
                    output = await asyncio.wait_for(
                        pipeline.run(
                            effective_query,
                            self.config.orchestrator.domain_tag,
                            raw_sources,
                            parameter_bounds=[],
                        ),
                        timeout=float(self.config.orchestrator.timeout_seconds),
                    )
                finally:
                    hub.data_ingestors.update(original_ingestors)

                payload = output.model_dump(mode="json")
                _write_json(
                    run_dir / "orchestration_output.json",
                    payload,
                    write_artifacts,
                )
                trace_obj = getattr(bundle.router, "trace", [])
                trace = list(
                    getattr(trace_obj, "records", trace_obj) or []
                )
                _write_json(
                    run_dir / "llm_calls.json",
                    _artifact_safe_trace(trace, persist_opt_in),
                    write_artifacts,
                )
                return {
                    "task_id": output.task_id,
                    "domain_tag": output.domain_tag,
                    "viable_count": len(output.viable_pareto_frontier),
                    "dead_count": len(output.rejected_dead_branches),
                    "compiled_negative_rules": len(
                        output.newly_compiled_negative_rules
                    ),
                    "theory_algorithms": sorted(hub.theory_algorithms.keys()),
                    "disabled_ingestor_plugins": sorted(original_ingestors)
                    if self.config.orchestrator.disable_raw_ingestors
                    else [],
                    "router": type(bundle.router).__name__,
                    "model": getattr(bundle.router, "last_model", self.model),
                    "artifact_path": str(run_dir / "orchestration_output.json")
                    if write_artifacts
                    else "",
                    "output": payload if self.include_orchestration_in_output else None,
                }
            except asyncio.TimeoutError:
                warnings.append(
                    "S6 orchestrator vượt timeout cấu hình; kết quả engine vẫn "
                    "được giữ."
                )
                return None
            except Exception as exc:
                message = redact_secrets(
                    f"{type(exc).__name__}: {exc}",
                    bundle.extra_secrets,
                )
                warnings.append(f"S6 orchestrator thất bại: {message}")
                return None

    # ------------------------------------------------------------------
    # Output assembly
    # ------------------------------------------------------------------
    def _failed_output(
        self,
        *,
        run_id: str,
        mode: EffectiveMode,
        started_at: datetime,
        error: StructuredError,
        classifier: Optional[ClassifierResult] = None,
        macro: Optional[MacroResult] = None,
        micro: Optional[MicroResult] = None,
        validation: Optional[Any] = None,
        warnings: Optional[List[str]] = None,
        artifacts: Optional[Dict[str, str]] = None,
    ) -> PipelineOutput:
        return PipelineOutput(
            run_id=run_id,
            mode_used=mode,
            status="failed",
            macro=macro,
            micro=micro,
            validation=validation,
            warnings=list(warnings or []),
            error=error,
            classifier=classifier,
            artifacts=dict(artifacts or {}),
            started_at=started_at,
            finished_at=utc_now(),
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def run(
        self,
        *,
        query: str,
        requested_mode: RequestedMode = "auto",
        personal_context: Optional[PersonalContext] = None,
        household_profile: Optional[HouseholdProfile] = None,
        allow_dual: bool = False,
        dry_run: bool = False,
        use_live: Optional[bool] = None,
        enable_orchestrator: Optional[bool] = None,
        byok_stdin: bool = False,
        stdin_stream: Any = None,
    ) -> PipelineRunResult:
        query = (query or "").strip()
        if not query:
            raise InputError("Truy vấn rỗng.")
        if requested_mode not in {"macro", "micro", "dual", "auto"}:
            raise InputError(
                f"Chế độ không hợp lệ: {requested_mode!r}.",
                suggestion="Dùng macro, micro, dual hoặc auto.",
            )

        started_at = utc_now()
        run_id = new_run_id()
        run_dir = self.artifact_dir / run_id
        write_artifacts = not dry_run
        warnings: List[str] = []
        artifacts: Dict[str, str] = {}
        classifier: Optional[ClassifierResult] = None
        macro_bundle: Optional[MacroBundle] = None
        micro_bundle: Optional[MicroBundle] = None
        store = CacheLogStore(self.config.cache_log, persist=not dry_run)
        router_bundle: Optional[RouterBundle] = None
        mode: EffectiveMode = (
            requested_mode if requested_mode in {"macro", "micro", "dual"} else "macro"
        )

        orchestrator_enabled = (
            self.config.orchestrator.enabled
            if enable_orchestrator is None
            else bool(enable_orchestrator)
        )
        live = (
            self.config.input_fetch.require_live_article
            if use_live is None
            else bool(use_live)
        )
        if live:
            # The live fetch is a read-only network operation; dry-run may still
            # use it.  The orchestrator is skipped in dry-run to guarantee that
            # no external writes happen.
            router_bundle = build_router(
                config=self.config,
                trace=[],
                model=self.model,
                use_stdin=byok_stdin,
                stdin_stream=stdin_stream,
            )
            warnings.extend(router_bundle.warnings)

        try:
            raw_input = RawInput(
                run_id=run_id,
                query=query,
                requested_mode=requested_mode,
                personal_context=personal_context,
                source_refs=[],
            )
            raw = await fetch_material(
                raw_input,
                personal_context,
                self.config,
                router=router_bundle.router if router_bundle else None,
                use_live=live,
            )
            warnings.extend(raw.warnings)
            persist_opt_in = bool(
                personal_context and personal_context.persist_opt_in
            )
            artifacts["raw_material_json"] = _write_json(
                run_dir / "raw_material.json",
                _artifact_safe_raw_material(raw, persist_opt_in),
                write_artifacts,
            )

            classifier = await InputClassifier(
                config=self.config.classifier,
                calibration=self.calibration,
                embedding_client=await self._embedding(),
            ).classify(raw_input, text=raw.text_for_classification)
            if requested_mode == "auto" and classifier.routing_recommendation in {
                "macro",
                "micro",
                "dual",
            }:
                # Best-effort mode for a possible structured error; the router
                # below still decides the authoritative mode.
                mode = classifier.routing_recommendation
            artifacts["classifier_json"] = _write_json(
                run_dir / "classifier.json",
                classifier.model_dump(mode="json"),
                write_artifacts,
            )

            decision = resolve_mode(
                requested_mode,
                classifier,
                self.calibration,
                allow_dual=allow_dual,
            )
            mode = decision.mode
            warnings.extend(decision.warnings)

            entries = await self._write_cache(
                run_id=run_id,
                raw=raw,
                classifier=classifier,
                context=personal_context,
                store=store,
                persist_opt_in=persist_opt_in,
                warnings=warnings,
            )
            warnings.extend(store.warnings)
            artifacts["cache_entry_ids_json"] = _write_json(
                run_dir / "cache_entries.json",
                {
                    "run_id": run_id,
                    "entry_ids": [entry.entry_id for entry in entries],
                    "states": [entry.state for entry in entries],
                    "persisted": not dry_run,
                },
                write_artifacts,
            )

            # ---------------- S4 engines ----------------
            try:
                if mode in {"macro", "dual"}:
                    macro_bundle = await run_macro(
                        run_id=run_id,
                        raw=raw,
                        cache_entries=entries,
                        config=self.config.macro,
                        artifact_root=run_dir,
                        write_artifact=write_artifacts,
                    )
                if mode == "micro":
                    micro_engine = MicroChannelEngine()
                    micro_bundle = await asyncio.to_thread(
                        micro_engine.run,
                        run_id=run_id,
                        raw=raw,
                        household=household_profile,
                        personal_context=personal_context,
                        macro_bundle=None,
                    )
                elif mode == "dual":
                    if macro_bundle is None:
                        raise MacroEngineError(
                            "Dual mode không có MacroBundle để chuyển cho micro."
                        )
                    micro_engine = MicroChannelEngine()
                    micro_bundle = await asyncio.to_thread(
                        micro_engine.run,
                        run_id=run_id,
                        raw=raw,
                        household=household_profile,
                        personal_context=personal_context,
                        macro_bundle=macro_bundle,
                    )
                await store.mark_consumed(run_id)
            except (MacroEngineError, MicroEngineError) as exc:
                try:
                    await store.mark_quarantined(run_id)
                except CacheLogError as quarantine_exc:
                    warnings.append(
                        "Không chuyển cache sang quarantined: "
                        f"{type(quarantine_exc).__name__}: {quarantine_exc}"
                    )
                output = self._failed_output(
                    run_id=run_id,
                    mode=mode,
                    started_at=started_at,
                    error=exc.to_contract(),
                    classifier=classifier,
                    macro=macro_bundle.result if macro_bundle else None,
                    micro=micro_bundle.result if micro_bundle else None,
                    warnings=warnings,
                    artifacts=artifacts,
                )
                _write_json(
                    run_dir / "pipeline_output.json",
                    output.model_dump(mode="json"),
                    write_artifacts,
                )
                return PipelineRunResult(output=output, exit_code=exc.exit_code)

            macro_result = macro_bundle.result if macro_bundle else None
            micro_result = micro_bundle.result if micro_bundle else None
            if macro_result is not None:
                warnings.extend(macro_result.warnings)
                artifacts["macro_artifact_json"] = macro_bundle.artifact_path
            if micro_result is not None:
                warnings.extend(micro_result.warnings)
                artifacts["micro_bundle_json"] = _write_json(
                    run_dir / "micro_bundle.json",
                    {
                        "result": micro_result.model_dump(mode="json"),
                        "evaluation": micro_bundle.evaluation
                        if micro_bundle
                        else None,
                    },
                    write_artifacts,
                )

            # ---------------- S5 re-validation + purge ----------------
            revalidator = Revalidator(
                self.config.revalidation,
                embedding_client=await self._embedding(),
            )
            outcome = await revalidator.run(
                run_id=run_id,
                mode=mode,
                macro=macro_result,
                micro=micro_result,
                entries=entries,
                store=store,
                dry_run=dry_run,
            )
            warnings.extend(outcome.report.warnings)
            artifacts["revalidation_json"] = _write_json(
                run_dir / "revalidation.json",
                outcome.report.model_dump(mode="json"),
                write_artifacts,
            )
            if not outcome.report.passed:
                error = RevalidationError(
                    "Re-validation thất bại; cache entry đã được cách ly và "
                    "không bị purge.",
                ).to_contract()
                output = self._failed_output(
                    run_id=run_id,
                    mode=mode,
                    started_at=started_at,
                    error=error,
                    classifier=classifier,
                    macro=macro_result,
                    micro=micro_result,
                    validation=outcome.report,
                    warnings=warnings,
                    artifacts=artifacts,
                )
                _write_json(
                    run_dir / "pipeline_output.json",
                    output.model_dump(mode="json"),
                    write_artifacts,
                )
                return PipelineRunResult(output=output, exit_code=5)

            if (
                self.config.cache_log.purge_enabled
                and not outcome.purge.committed
                and not dry_run
            ):
                error = PurgeError(
                    "Purge tự động không commit; cache entry vẫn ở trạng thái "
                    "verified."
                ).to_contract()
                output = self._failed_output(
                    run_id=run_id,
                    mode=mode,
                    started_at=started_at,
                    error=error,
                    classifier=classifier,
                    macro=macro_result,
                    micro=micro_result,
                    validation=outcome.report,
                    warnings=warnings,
                    artifacts=artifacts,
                )
                _write_json(
                    run_dir / "pipeline_output.json",
                    output.model_dump(mode="json"),
                    write_artifacts,
                )
                return PipelineRunResult(
                    output=output,
                    exit_code=ERROR_EXIT_CODES["PURGE_FAILED"],
                )

            # ---------------- S6 existing orchestrator ----------------
            orchestration = None
            if orchestrator_enabled and not dry_run:
                if router_bundle is None:
                    router_bundle = build_router(
                        config=self.config,
                        trace=[],
                        model=self.model,
                        use_stdin=byok_stdin,
                        stdin_stream=stdin_stream,
                    )
                    warnings.extend(router_bundle.warnings)
                orchestration = await self._run_orchestrator(
                    run_id=run_id,
                    raw=raw,
                    macro=macro_result,
                    micro=micro_result,
                    bundle=router_bundle,
                    run_dir=run_dir,
                    write_artifacts=write_artifacts,
                    persist_opt_in=persist_opt_in,
                    warnings=warnings,
                )
            elif dry_run and orchestrator_enabled:
                warnings.append(
                    "--dry-run bỏ qua S6 orchestrator để không tạo external "
                    "writes; các stage S1-S5 vẫn chạy đầy đủ."
                )

            status = "ok_with_warnings" if warnings else "ok"
            output = PipelineOutput(
                run_id=run_id,
                mode_used=mode,
                status=status,
                macro=macro_result,
                micro=micro_result,
                validation=outcome.report,
                warnings=warnings,
                classifier=classifier,
                orchestration=orchestration,
                artifacts=artifacts,
                started_at=started_at,
                finished_at=utc_now(),
            )
            artifacts["pipeline_output_json"] = _write_json(
                run_dir / "pipeline_output.json",
                output.model_dump(mode="json"),
                write_artifacts,
            )
            return PipelineRunResult(output=output, exit_code=0)

        except PipelineError as exc:
            # Any failure after the cache write must not leave live entries in
            # an ambiguous state.  Marking quarantined is the safe terminal.
            if store.persist:
                try:
                    await store.mark_quarantined(run_id)
                except Exception:
                    pass
            warnings.append(
                redact_secrets(
                    f"{exc.code}: {exc.message}",
                    router_bundle.extra_secrets if router_bundle else None,
                )
            )
            output = self._failed_output(
                run_id=run_id,
                mode=mode,
                started_at=started_at,
                error=exc.to_contract(
                    router_bundle.extra_secrets if router_bundle else None
                ),
                classifier=classifier,
                macro=macro_bundle.result if macro_bundle else None,
                micro=micro_bundle.result if micro_bundle else None,
                warnings=warnings,
                artifacts=artifacts,
            )
            _write_json(
                run_dir / "pipeline_output.json",
                output.model_dump(mode="json"),
                write_artifacts,
            )
            return PipelineRunResult(output=output, exit_code=exc.exit_code)
        except Exception as exc:
            if store.persist:
                try:
                    await store.mark_quarantined(run_id)
                except Exception:
                    pass
            message = redact_secrets(
                f"{type(exc).__name__}: {exc}",
                router_bundle.extra_secrets if router_bundle else None,
            )
            warnings.append(message)
            error = StructuredError(
                code="INTERNAL_ERROR",
                message=message,
                suggestion="Xem log máy chủ; không có khóa bí mật nào được ghi ra.",
            )
            output = self._failed_output(
                run_id=run_id,
                mode=mode,
                started_at=started_at,
                error=error,
                classifier=classifier,
                macro=macro_bundle.result if macro_bundle else None,
                micro=micro_bundle.result if micro_bundle else None,
                warnings=warnings,
                artifacts=artifacts,
            )
            _write_json(
                run_dir / "pipeline_output.json",
                output.model_dump(mode="json"),
                write_artifacts,
            )
            return PipelineRunResult(output=output, exit_code=1)
        finally:
            if router_bundle is not None:
                try:
                    await router_bundle.router.close()
                except Exception:
                    pass


__all__ = ["PipelineOrchestrator", "PipelineRunResult"]
