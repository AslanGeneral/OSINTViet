"""Run-scoped cache log service with lifecycle transitions and atomic purge."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import delete, select
from sqlmodel import select as sqlmodel_select

from app.core.database import async_session_factory, init_db
from app.models.cache_log import CacheLogEntryRow, CachePurgeAuditRow, utc_now
from app.pipeline_v2.config import CacheLogConfig
from app.pipeline_v2.contracts import (
    CACHE_STATES,
    CACHE_TRANSITIONS,
    CacheLogEntry,
    ClassifierResult,
    PersonalContext,
    PurgeReport,
    new_entry_id,
)
from app.pipeline_v2.errors import CacheLogError, PurgeError
from app.pipeline_v2.errors import redact_secrets


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_audit_id() -> str:
    return f"audit_{uuid.uuid4().hex[:20]}"


def _row_to_contract(row: CacheLogEntryRow) -> CacheLogEntry:
    try:
        classifier_payload = json.loads(row.classifier_json or "{}")
        classifier = (
            ClassifierResult(**classifier_payload) if classifier_payload else None
        )
    except Exception:
        classifier = None
    try:
        source_refs = list(json.loads(row.source_refs_json or "[]"))
    except Exception:
        source_refs = []
    return CacheLogEntry(
        entry_id=row.entry_id,
        run_id=row.run_id,
        state=row.state,  # type: ignore[arg-type]
        created_at=row.created_at,
        classifier=classifier,
        content=row.content,
        embedding_ref=row.embedding_ref,
        source_refs=[str(item) for item in source_refs],
    )


def _row_embedding(row: CacheLogEntryRow) -> Optional[List[float]]:
    if not row.embedding_json:
        return None
    try:
        payload = json.loads(row.embedding_json)
        if isinstance(payload, list) and payload:
            return [float(item) for item in payload]
    except Exception:
        return None
    return None


class CacheLogStore:
    """A persistent or in-memory cache log.

    ``persist=False`` is used by ``--dry-run``: the same API is available but
    nothing is written to SQLite, Chroma or disk.
    """

    def __init__(
        self,
        config: Optional[CacheLogConfig] = None,
        *,
        persist: bool = True,
    ) -> None:
        self.config = config or CacheLogConfig(
            purge_enabled=True,
            dry_run_default=False,
            store_embedding_vectors=True,
            max_content_chars=20000,
        )
        self.persist = persist
        self.warnings: List[str] = []
        self._memory: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._memory_audits: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def write_entry(
        self,
        *,
        run_id: str,
        content: str,
        classifier: Optional[ClassifierResult] = None,
        source_refs: Optional[Sequence[str]] = None,
        personal_context: Optional[PersonalContext] = None,
        persist_personal_context: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[Sequence[float]] = None,
    ) -> CacheLogEntry:
        if not run_id:
            raise CacheLogError("cache log yêu cầu run_id")
        clean_content = redact_secrets(content)[: self.config.max_content_chars]
        if not clean_content:
            raise CacheLogError("cache log không lưu nội dung rỗng")
        entry_id = new_entry_id()
        now = utc_now()
        vector = [float(item) for item in embedding] if embedding else None
        embedding_ref = (
            f"sqlite://cache_log_entries/{entry_id}#embedding"
            if vector is not None and self.config.store_embedding_vectors
            else None
        )
        context_payload = None
        if personal_context is not None and persist_personal_context:
            context_payload = redact_secrets(
                personal_context.model_dump_json()
            )

        entry = CacheLogEntry(
            entry_id=entry_id,
            run_id=run_id,
            state="written",
            created_at=now,
            classifier=classifier,
            content=clean_content,
            embedding_ref=embedding_ref,
            source_refs=[str(item) for item in (source_refs or [])],
        )

        if not self.persist:
            self._memory.setdefault(run_id, {})[entry_id] = {
                "entry": entry,
                "embedding": vector,
                "metadata": metadata or {},
                "personal_context": context_payload,
            }
            return entry

        row = CacheLogEntryRow(
            entry_id=entry_id,
            run_id=run_id,
            state="written",
            created_at=now,
            updated_at=now,
            classifier_json=(
                classifier.model_dump_json() if classifier is not None else "{}"
            ),
            content=clean_content,
            content_sha256=_sha256(clean_content),
            embedding_ref=embedding_ref,
            embedding_json=(
                _dump_json(vector)
                if vector is not None and self.config.store_embedding_vectors
                else None
            ),
            source_refs_json=_dump_json(
                [str(item) for item in (source_refs or [])]
            ),
            metadata_json=_dump_json(metadata or {}),
            personal_context_json=context_payload,
        )
        await init_db()
        async with async_session_factory() as session:
            session.add(row)
            await session.commit()
        return entry

    async def write_with_embedding(
        self,
        *,
        run_id: str,
        content: str,
        embedding_client: Any,
        classifier: Optional[ClassifierResult] = None,
        source_refs: Optional[Sequence[str]] = None,
        personal_context: Optional[PersonalContext] = None,
        persist_personal_context: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CacheLogEntry:
        vector: Optional[List[float]] = None
        if embedding_client is not None:
            try:
                vectors = await embedding_client.get_embeddings_batch(
                    [content], input_type="search_document"
                )
                if vectors and vectors[0] is not None:
                    vector = [float(item) for item in vectors[0]]
                else:
                    self.warnings.append(
                        "Không tạo được vector cho cache log; re-validation sẽ "
                        "dùng fallback từ vựng."
                    )
            except Exception as exc:
                self.warnings.append(
                    "Embedding cho cache log thất bại; dùng fallback từ vựng: "
                    f"{type(exc).__name__}"
                )
        return await self.write_entry(
            run_id=run_id,
            content=content,
            classifier=classifier,
            source_refs=source_refs,
            personal_context=personal_context,
            persist_personal_context=persist_personal_context,
            metadata=metadata,
            embedding=vector,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_entries(self, run_id: str) -> List[CacheLogEntry]:
        if not self.persist:
            return [
                item["entry"]
                for item in self._memory.get(run_id, {}).values()
            ]
        await init_db()
        async with async_session_factory() as session:
            result = await session.execute(
                sqlmodel_select(CacheLogEntryRow)
                .where(CacheLogEntryRow.run_id == run_id)
                .order_by(CacheLogEntryRow.created_at)
            )
            return [_row_to_contract(row) for row in result.scalars().all()]

    async def get_entry(self, entry_id: str) -> Optional[CacheLogEntry]:
        if not self.persist:
            for entries in self._memory.values():
                if entry_id in entries:
                    return entries[entry_id]["entry"]
            return None
        await init_db()
        async with async_session_factory() as session:
            row = await session.get(CacheLogEntryRow, entry_id)
            return _row_to_contract(row) if row is not None else None

    async def get_embedding(self, entry_id: str) -> Optional[List[float]]:
        if not self.persist:
            for entries in self._memory.values():
                if entry_id in entries:
                    return entries[entry_id]["embedding"]
            return None
        await init_db()
        async with async_session_factory() as session:
            row = await session.get(CacheLogEntryRow, entry_id)
            return _row_embedding(row) if row is not None else None

    async def list_quarantined(
        self, run_id: Optional[str] = None
    ) -> List[CacheLogEntry]:
        if not self.persist:
            results: List[CacheLogEntry] = []
            for current_run, entries in self._memory.items():
                if run_id and current_run != run_id:
                    continue
                for item in entries.values():
                    if item["entry"].state == "quarantined":
                        results.append(item["entry"])
            return results
        await init_db()
        async with async_session_factory() as session:
            statement = sqlmodel_select(CacheLogEntryRow).where(
                CacheLogEntryRow.state == "quarantined"
            )
            if run_id:
                statement = statement.where(CacheLogEntryRow.run_id == run_id)
            result = await session.execute(
                statement.order_by(CacheLogEntryRow.created_at)
            )
            return [_row_to_contract(row) for row in result.scalars().all()]

    async def get_audit(self, audit_id: str) -> Optional[Dict[str, Any]]:
        if not self.persist:
            for audit in self._memory_audits:
                if audit["audit_id"] == audit_id:
                    return audit
            return None
        await init_db()
        async with async_session_factory() as session:
            row = await session.get(CachePurgeAuditRow, audit_id)
            if row is None:
                return None
            return {
                "audit_id": row.audit_id,
                "run_id": row.run_id,
                "requested_at": row.requested_at,
                "completed_at": row.completed_at,
                "dry_run": row.dry_run,
                "outcome": row.outcome,
                "entries_verified": row.entries_verified,
                "entries_removed": row.entries_removed,
                "counts": json.loads(row.counts_json or "{}"),
                "error_redacted": row.error_redacted,
            }

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------
    @staticmethod
    def _check_transition(from_state: str, to_state: str) -> None:
        if from_state not in CACHE_STATES or to_state not in CACHE_STATES:
            raise CacheLogError(
                f"trạng thái cache không hợp lệ: {from_state} -> {to_state}"
            )
        if to_state not in CACHE_TRANSITIONS.get(from_state, ()) and (
            from_state != to_state
        ):
            raise CacheLogError(
                f"chuyển trạng thái cache bị cấm: {from_state} -> {to_state}"
            )

    async def transition_run(
        self, run_id: str, from_state: str, to_state: str
    ) -> int:
        self._check_transition(from_state, to_state)
        if from_state == to_state:
            return 0
        if not self.persist:
            count = 0
            for item in self._memory.get(run_id, {}).values():
                entry = item["entry"]
                if entry.state == from_state:
                    entry.state = to_state  # type: ignore[assignment]
                    count += 1
            return count
        await init_db()
        async with async_session_factory() as session:
            result = await session.execute(
                sqlmodel_select(CacheLogEntryRow).where(
                    CacheLogEntryRow.run_id == run_id,
                    CacheLogEntryRow.state == from_state,
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                row.state = to_state
                row.updated_at = utc_now()
                session.add(row)
            await session.commit()
            return len(rows)

    async def transition_run_any(
        self, run_id: str, from_states: Iterable[str], to_state: str
    ) -> int:
        states = tuple(from_states)
        for state in states:
            self._check_transition(state, to_state)
        if not self.persist:
            count = 0
            for item in self._memory.get(run_id, {}).values():
                entry = item["entry"]
                if entry.state in states:
                    entry.state = to_state  # type: ignore[assignment]
                    count += 1
            return count
        await init_db()
        async with async_session_factory() as session:
            result = await session.execute(
                sqlmodel_select(CacheLogEntryRow).where(
                    CacheLogEntryRow.run_id == run_id,
                    CacheLogEntryRow.state.in_(states),  # type: ignore[attr-defined]
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                row.state = to_state
                row.updated_at = utc_now()
                session.add(row)
            await session.commit()
            return len(rows)

    async def mark_consumed(self, run_id: str) -> int:
        return await self.transition_run(run_id, "written", "consumed_by_engine")

    async def mark_revalidating(self, run_id: str) -> int:
        return await self.transition_run(
            run_id, "consumed_by_engine", "revalidating"
        )

    async def mark_verified(self, run_id: str) -> int:
        return await self.transition_run(run_id, "revalidating", "verified")

    async def mark_quarantined(self, run_id: str) -> int:
        return await self.transition_run_any(
            run_id, ("written", "consumed_by_engine", "revalidating"), "quarantined"
        )

    # ------------------------------------------------------------------
    # Purge / explicit quarantine deletion
    # ------------------------------------------------------------------
    async def purge_run(self, run_id: str, *, dry_run: bool = False) -> PurgeReport:
        if not run_id:
            raise PurgeError("purge yêu cầu run_id")
        if not self.persist:
            return self._purge_memory(run_id, dry_run=dry_run)
        await init_db()
        async with async_session_factory() as session:
            result = await session.execute(
                sqlmodel_select(CacheLogEntryRow).where(
                    CacheLogEntryRow.run_id == run_id
                )
            )
            rows = list(result.scalars().all())
            verified = [row for row in rows if row.state == "verified"]
            quarantined = [row for row in rows if row.state == "quarantined"]
            counts = {
                "total": len(rows),
                "verified": len(verified),
                "quarantined": len(quarantined),
                "other": len(rows) - len(verified) - len(quarantined),
            }
            if dry_run:
                return PurgeReport(
                    attempted=True,
                    committed=False,
                    entries_removed=0,
                    dry_run=True,
                    outcome="dry_run",
                    entries_matched=len(verified),  # type: ignore[call-arg]
                )
            audit_id = _new_audit_id()
            audit = CachePurgeAuditRow(
                audit_id=audit_id,
                run_id=run_id,
                requested_at=utc_now(),
                dry_run=False,
                entries_verified=len(verified),
                entries_removed=0,
                counts_json=_dump_json(counts),
            )
            removed = 0
            if verified:
                delete_result = await session.execute(
                    delete(CacheLogEntryRow).where(
                        CacheLogEntryRow.run_id == run_id,
                        CacheLogEntryRow.state == "verified",
                    )
                )
                removed = int(delete_result.rowcount or 0)
                audit.outcome = "committed"
                audit.entries_removed = removed
            else:
                audit.outcome = "idempotent_noop"
            audit.completed_at = utc_now()
            session.add(audit)
            await session.commit()
            return PurgeReport(
                attempted=True,
                committed=True,
                entries_removed=removed,
                dry_run=False,
                outcome=audit.outcome,
                audit_id=audit_id,
                entries_matched=len(verified),  # type: ignore[call-arg]
            )

    def _purge_memory(self, run_id: str, *, dry_run: bool) -> PurgeReport:
        entries = self._memory.get(run_id, {})
        verified_ids = [
            entry_id
            for entry_id, item in entries.items()
            if item["entry"].state == "verified"
        ]
        if dry_run:
            return PurgeReport(
                attempted=True,
                committed=False,
                entries_removed=0,
                dry_run=True,
                outcome="dry_run",
                entries_matched=len(verified_ids),  # type: ignore[call-arg]
            )
        for entry_id in verified_ids:
            entries.pop(entry_id, None)
        audit_id = _new_audit_id()
        self._memory_audits.append(
            {
                "audit_id": audit_id,
                "run_id": run_id,
                "outcome": "committed" if verified_ids else "idempotent_noop",
                "entries_verified": len(verified_ids),
                "entries_removed": len(verified_ids),
                "dry_run": False,
            }
        )
        return PurgeReport(
            attempted=True,
            committed=True,
            entries_removed=len(verified_ids),
            dry_run=False,
            outcome="committed" if verified_ids else "idempotent_noop",
            audit_id=audit_id,
            entries_matched=len(verified_ids),  # type: ignore[call-arg]
        )

    async def delete_quarantined(
        self, *, run_id: Optional[str] = None, dry_run: bool = False
    ) -> PurgeReport:
        if not self.persist:
            run_ids = [run_id] if run_id else list(self._memory)
            targets = [
                (current_run, entry_id)
                for current_run in run_ids
                for entry_id, item in self._memory.get(current_run, {}).items()
                if item["entry"].state == "quarantined"
            ]
            if dry_run:
                return PurgeReport(
                    attempted=True,
                    committed=False,
                    dry_run=True,
                    outcome="quarantine_dry_run",
                    entries_matched=len(targets),  # type: ignore[call-arg]
                )
            for current_run, entry_id in targets:
                self._memory[current_run].pop(entry_id, None)
            audit_id = _new_audit_id()
            self._memory_audits.append(
                {
                    "audit_id": audit_id,
                    "run_id": run_id or "*",
                    "outcome": "quarantine_deleted",
                    "entries_verified": 0,
                    "entries_removed": len(targets),
                    "dry_run": False,
                }
            )
            return PurgeReport(
                attempted=True,
                committed=True,
                entries_removed=len(targets),
                outcome="quarantine_deleted",
                audit_id=audit_id,
                entries_matched=len(targets),  # type: ignore[call-arg]
            )
        await init_db()
        async with async_session_factory() as session:
            statement = sqlmodel_select(CacheLogEntryRow).where(
                CacheLogEntryRow.state == "quarantined"
            )
            if run_id:
                statement = statement.where(CacheLogEntryRow.run_id == run_id)
            result = await session.execute(statement)
            rows = list(result.scalars().all())
            if dry_run:
                return PurgeReport(
                    attempted=True,
                    committed=False,
                    dry_run=True,
                    outcome="quarantine_dry_run",
                    entries_matched=len(rows),  # type: ignore[call-arg]
                )
            removed = 0
            if rows:
                delete_result = await session.execute(
                    delete(CacheLogEntryRow).where(
                        CacheLogEntryRow.state == "quarantined"
                    )
                    if not run_id
                    else delete(CacheLogEntryRow).where(
                        CacheLogEntryRow.state == "quarantined",
                        CacheLogEntryRow.run_id == run_id,
                    )
                )
                removed = int(delete_result.rowcount or 0)
            audit = CachePurgeAuditRow(
                audit_id=_new_audit_id(),
                run_id=run_id or "*",
                requested_at=utc_now(),
                completed_at=utc_now(),
                dry_run=False,
                outcome="quarantine_deleted",
                entries_verified=0,
                entries_removed=removed,
                counts_json=_dump_json({"quarantined_matched": len(rows)}),
            )
            session.add(audit)
            await session.commit()
            return PurgeReport(
                attempted=True,
                committed=True,
                entries_removed=removed,
                outcome="quarantine_deleted",
                audit_id=audit.audit_id,
                entries_matched=len(rows),  # type: ignore[call-arg]
            )


__all__ = ["CacheLogStore"]


def _unused_imports_guard() -> None:  # pragma: no cover
    """Keep static linters quiet for imports used only in type comments."""
    _ = (datetime, timezone, select)
