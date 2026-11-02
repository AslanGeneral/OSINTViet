"""S5 re-validation against the run cache log, then automatic purge.

The checks are deterministic and use the existing embedding engine when it is
available.  When embeddings fail, a token-overlap fallback is used and the
degraded method is recorded in the report.  Thresholds come from
``config/pipeline_v2.json``; none of them is hard-coded here.

Lifecycle: ``consumed_by_engine -> revalidating -> verified -> purged`` on
success, or ``consumed_by_engine -> revalidating -> quarantined`` on any failed
check.  Quarantined rows are never deleted by this module.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.pipeline_v2.cache_log import CacheLogStore
from app.pipeline_v2.config import (
    CacheLogConfig,
    RevalidationConfig,
    load_pipeline_config,
)
from app.pipeline_v2.contracts import (
    CacheLogEntry,
    MacroResult,
    MicroResult,
    PurgeReport,
    RevalidationCheck,
    RevalidationReport,
)
from app.pipeline_v2.errors import CacheLogError, PurgeError

_TOKEN_RE = re.compile(r"[0-9a-zA-ZÀ-ỹĐđ%]+", re.UNICODE)
_CITATION_PREFIXES = (
    "artifact:",
    "source:",
    "doi:",
    "http://",
    "https://",
    "world_bank:",
)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value or "").casefold().strip()


def _tokens(value: str) -> set[str]:
    text = _normalize(value)
    # Strip diacritics for a language-tolerant fallback.
    text = "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return {token for token in _TOKEN_RE.findall(text) if len(token) > 1}


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    l_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    r_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if l_norm <= 1e-12 or r_norm <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (l_norm * r_norm)))


def _is_citation(value: str) -> bool:
    lowered = _normalize(value)
    return lowered.startswith(_CITATION_PREFIXES)


@dataclass
class RevalidationOutcome:
    report: RevalidationReport
    purge: PurgeReport
    warnings: List[str] = field(default_factory=list)


class Revalidator:
    def __init__(
        self,
        config: Optional[RevalidationConfig] = None,
        *,
        embedding_client: Any = None,
    ) -> None:
        pipeline_config = load_pipeline_config()
        self.config = config or pipeline_config.revalidation
        self.cache_config = pipeline_config.cache_log
        self._embedding_client = embedding_client
        self.warnings: List[str] = []

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    async def _ensure_embedding_client(self) -> Any:
        if self._embedding_client is not None:
            return self._embedding_client
        try:
            from app.services.embedding_engine import embedding_engine

            self._embedding_client = embedding_engine
        except Exception:
            self._embedding_client = None
        return self._embedding_client

    async def _embed(self, texts: Sequence[str]) -> List[Optional[List[float]]]:
        if not texts:
            return []
        client = await self._ensure_embedding_client()
        if client is None:
            self.warnings.append(
                "Không có embedding client; re-validation dùng fallback từ vựng."
            )
            return [None] * len(texts)
        try:
            vectors: List[Optional[List[float]]] = []
            batch_size = int(self.config.embedding_batch_size)
            for start in range(0, len(texts), batch_size):
                batch = list(texts[start : start + batch_size])
                result = await client.get_embeddings_batch(
                    batch, input_type="search_document"
                )
                if len(result) != len(batch):
                    raise ValueError("embedding batch size mismatch")
                vectors.extend(
                    None
                    if vector is None
                    else [float(item) for item in vector]
                    for vector in result
                )
            return vectors
        except Exception as exc:
            self.warnings.append(
                "Embedding cho re-validation thất bại; dùng fallback từ vựng: "
                f"{type(exc).__name__}"
            )
            return [None] * len(texts)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------
    def _grounding(
        self,
        macro: Optional[MacroResult],
        micro: Optional[MicroResult],
        entries: Sequence[CacheLogEntry],
    ) -> RevalidationCheck:
        entry_ids = {entry.entry_id for entry in entries}
        failed: List[str] = []

        def support_ok(items: Iterable[str]) -> bool:
            for item in items:
                if item in entry_ids or _is_citation(str(item)):
                    return True
            return False

        if macro is not None:
            for index, claim in enumerate(macro.claims):
                if not claim.support or not support_ok(claim.support):
                    failed.append(
                        f"macro.claims[{index}] không có support là cache entry "
                        "hoặc nguồn được trích dẫn"
                    )

        if micro is not None:
            for index, chain in enumerate(micro.transmission_chains):
                if chain.magnitude_status == "computed":
                    if not chain.source_or_formula:
                        failed.append(
                            f"micro.transmission_chains[{index}] computed nhưng "
                            "thiếu source_or_formula"
                        )
                elif chain.magnitude_status == "sourced":
                    if not (
                        chain.source_or_formula
                        and (
                            _is_citation(chain.source_or_formula)
                            or support_ok([chain.source_or_formula])
                        )
                    ):
                        failed.append(
                            f"micro.transmission_chains[{index}] sourced nhưng "
                            "thiếu nguồn trích dẫn hợp lệ"
                        )

        if self.config.require_external_support and macro is not None:
            has_external = any(
                _is_citation(str(item))
                for claim in macro.claims
                for item in claim.support
            )
            if macro.claims and not has_external:
                failed.append(
                    "không có claim vĩ mô nào trích dẫn nguồn ngoài cache log"
                )

        return RevalidationCheck(
            name="grounding",
            passed=not failed,
            failed_items=failed,
            method="entry-id/source-citation check",
        )

    async def _consistency(
        self,
        macro: Optional[MacroResult],
        micro: Optional[MicroResult],
        entries: Sequence[CacheLogEntry],
        *,
        dry_run: bool,
    ) -> RevalidationCheck:
        corpus = [
            entry.content[:12000]
            for entry in entries
            if entry.content.strip()
        ]
        if not corpus:
            return RevalidationCheck(
                name="consistency",
                passed=False,
                failed_items=["cache log rỗng; không thể đối chiếu đầu ra"],
                method="no corpus",
            )

        items: List[tuple[str, str]] = []
        if macro is not None:
            items.extend(
                (f"macro.claims[{index}]", claim.text)
                for index, claim in enumerate(macro.claims)
            )
        if micro is not None:
            items.extend(
                (f"micro.channels[{index}]", chain.mechanism)
                for index, chain in enumerate(micro.transmission_chains)
            )
        if not items:
            return RevalidationCheck(
                name="consistency",
                passed=False,
                failed_items=["không có claim/kênh nào để đối chiếu"],
                method="empty output",
            )

        vectors = await self._embed(corpus + [text for _, text in items])
        corpus_vectors = vectors[: len(corpus)]
        item_vectors = vectors[len(corpus) :]
        used_embeddings = any(vector is not None for vector in corpus_vectors)

        scored: List[tuple[str, float, str]] = []
        for (label, text), vector in zip(items, item_vectors):
            best = 0.0
            for index, corpus_text in enumerate(corpus):
                corpus_vector = corpus_vectors[index]
                if vector is not None and corpus_vector is not None:
                    score = (_cosine(vector, corpus_vector) + 1.0) / 2.0
                else:
                    score = _jaccard(text, corpus_text)
                best = max(best, score)
            scored.append((label, best, text[:160]))

        if used_embeddings:
            threshold = float(self.config.consistency_similarity_threshold)
            method = "embedding cosine (normalized [0,1])"
        else:
            threshold = float(self.config.lexical_overlap_threshold)
            method = "token-overlap Jaccard fallback"

        failed = [
            f"{label} similarity {score:.3f} < {threshold:.3f}: {excerpt}"
            for label, score, excerpt in scored
            if score < threshold
        ]
        passed_ratio = 1.0 - (len(failed) / len(scored))
        passed = passed_ratio >= float(self.config.min_claim_support_ratio)
        if not passed:
            self.warnings.append(
                f"Re-validation consistency chỉ đạt {passed_ratio:.3f} "
                f"(ngưỡng {self.config.min_claim_support_ratio:.3f})."
            )
        return RevalidationCheck(
            name="consistency",
            passed=passed,
            failed_items=failed,
            method=(
                f"{method}; support_ratio={passed_ratio:.3f}; "
                f"threshold={threshold:.3f}; dry_run={dry_run}"
            ),
        )

    def _completeness(
        self,
        mode: str,
        macro: Optional[MacroResult],
        micro: Optional[MicroResult],
    ) -> RevalidationCheck:
        failed: List[str] = []
        if mode in {"macro", "dual"}:
            if macro is None:
                failed.append("thiếu MacroResult")
            else:
                if not macro.summary.strip():
                    failed.append("MacroResult.summary rỗng")
                if not macro.claims:
                    failed.append("MacroResult.claims rỗng")
                for index, claim in enumerate(macro.claims):
                    if not claim.text.strip():
                        failed.append(f"MacroResult.claims[{index}].text rỗng")
        if mode in {"micro", "dual"}:
            if micro is None:
                failed.append("thiếu MicroResult")
            else:
                if not micro.transmission_chains:
                    failed.append("MicroResult.transmission_chains rỗng")
                if not micro.assumptions:
                    failed.append("MicroResult.assumptions rỗng")
                for index, chain in enumerate(micro.transmission_chains):
                    if not chain.channel.strip() or not chain.mechanism.strip():
                        failed.append(
                            f"MicroResult.transmission_chains[{index}] thiếu "
                            "channel/mechanism"
                        )
                    if chain.magnitude_status in {"computed", "sourced"} and not (
                        chain.magnitude_value and chain.source_or_formula
                    ):
                        failed.append(
                            f"MicroResult.transmission_chains[{index}] thiếu "
                            "magnitude_value/source_or_formula"
                        )
        return RevalidationCheck(
            name="completeness",
            passed=not failed,
            failed_items=failed,
            method="required-field presence check",
        )

    def _mode_integrity(
        self,
        mode: str,
        macro: Optional[MacroResult],
        micro: Optional[MicroResult],
    ) -> RevalidationCheck:
        failed: List[str] = []
        if mode == "dual":
            if macro is None or micro is None:
                failed.append("dual yêu cầu cả MacroResult và MicroResult")
            else:
                if not micro.upstream_macro_used:
                    failed.append(
                        "dual nhưng MicroResult.upstream_macro_used=false"
                    )
                computed_refs = [
                    chain.source_or_formula or ""
                    for chain in micro.transmission_chains
                    if chain.magnitude_status == "computed"
                ]
                if not computed_refs:
                    failed.append(
                        "dual nhưng không có kênh computed nào từ artifact vĩ mô"
                    )
                elif not any("artifact:" in ref for ref in computed_refs):
                    failed.append(
                        "không có kênh computed nào tham chiếu artifact vĩ mô"
                    )
        elif mode == "macro":
            if macro is None:
                failed.append("macro mode thiếu MacroResult")
            if micro is not None:
                failed.append("macro mode không được trả MicroResult")
        elif mode == "micro":
            if micro is None:
                failed.append("micro mode thiếu MicroResult")
            if macro is not None:
                failed.append("micro mode không được trả MacroResult")
        return RevalidationCheck(
            name="mode_integrity",
            passed=not failed,
            failed_items=failed,
            method="mode/dependency consistency check",
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def run(
        self,
        *,
        run_id: str,
        mode: str,
        macro: Optional[MacroResult],
        micro: Optional[MicroResult],
        entries: Sequence[CacheLogEntry],
        store: CacheLogStore,
        dry_run: bool = False,
    ) -> RevalidationOutcome:
        if not entries:
            report = RevalidationReport(
                run_id=run_id,
                passed=False,
                checks=[
                    RevalidationCheck(
                        name="grounding",
                        passed=False,
                        failed_items=["run không có cache entry"],
                        method="empty cache log",
                    )
                ],
                purge=PurgeReport(
                    attempted=False,
                    committed=False,
                    outcome="not_purged_quarantined",
                ),
                warnings=["Cache log rỗng; engine output bị cách ly."],
            )
            return RevalidationOutcome(report=report, purge=report.purge or PurgeReport())

        # ``dry_run`` uses an in-memory store, so this transition is not an
        # external write; it is needed to report the purge candidate count.
        await store.mark_revalidating(run_id)

        checks = [
            self._grounding(macro, micro, entries),
            await self._consistency(macro, micro, entries, dry_run=dry_run),
            self._completeness(mode, macro, micro),
            self._mode_integrity(mode, macro, micro),
        ]
        passed = all(check.passed for check in checks)
        warnings = list(self.warnings)

        if not passed:
            try:
                await store.mark_quarantined(run_id)
            except CacheLogError as exc:
                warnings.append(
                    "Không chuyển được cache entry sang quarantined: "
                    f"{type(exc).__name__}: {exc}"
                )
            report = RevalidationReport(
                run_id=run_id,
                passed=False,
                checks=checks,
                purge=PurgeReport(
                    attempted=False,
                    committed=False,
                    dry_run=dry_run,
                    outcome="not_purged_quarantined",
                ),
                warnings=warnings,
            )
            return RevalidationOutcome(report=report, purge=report.purge or PurgeReport())

        try:
            await store.mark_verified(run_id)
        except CacheLogError as exc:
            report = RevalidationReport(
                run_id=run_id,
                passed=False,
                checks=checks
                + [
                    RevalidationCheck(
                        name="completeness",
                        passed=False,
                        failed_items=["không chuyển được cache sang verified"],
                        method=str(exc),
                    )
                ],
                purge=PurgeReport(
                    attempted=False,
                    committed=False,
                    dry_run=dry_run,
                    outcome="not_purged_state_error",
                ),
                warnings=warnings,
            )
            return RevalidationOutcome(report=report, purge=report.purge or PurgeReport())

        purge = PurgeReport(attempted=False, committed=False, outcome="disabled")
        if self.cache_config.purge_enabled:
            try:
                purge = await store.purge_run(run_id, dry_run=dry_run)
            except PurgeError as exc:
                warnings.append(
                    "Purge thất bại sau khi verified; dữ liệu vẫn ở trạng thái "
                    f"verified: {type(exc).__name__}: {exc}"
                )
                purge = PurgeReport(
                    attempted=True,
                    committed=False,
                    dry_run=dry_run,
                    outcome="purge_error",
                )
        else:
            warnings.append(
                "cache_log.purge_enabled=false; bỏ qua purge tự động."
            )

        report = RevalidationReport(
            run_id=run_id,
            passed=True,
            checks=checks,
            purge=purge,
            warnings=warnings,
        )
        return RevalidationOutcome(report=report, purge=purge, warnings=warnings)


__all__ = ["RevalidationOutcome", "Revalidator"]
