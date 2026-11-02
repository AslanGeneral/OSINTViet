"""SQLModel tables for the run-scoped cache log.

The cache log reuses the existing async SQLite engine.  It stores processed
content and an optional derived vector, never an API key.  Personal context is
stored only when ``persist_opt_in`` is true.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CacheLogEntryRow(SQLModel, table=True):
    __tablename__ = "cache_log_entries"

    entry_id: str = Field(primary_key=True, max_length=96)
    run_id: str = Field(index=True, max_length=96)
    state: str = Field(default="written", index=True, max_length=32)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
    classifier_json: str = Field(default="{}")
    content: str = Field(default="")
    content_sha256: str = Field(default="", max_length=64)
    embedding_ref: Optional[str] = Field(default=None, max_length=256)
    embedding_json: Optional[str] = Field(default=None)
    source_refs_json: str = Field(default="[]")
    metadata_json: str = Field(default="{}")
    # Only populated when the user explicitly opted in.
    personal_context_json: Optional[str] = Field(default=None)


class CachePurgeAuditRow(SQLModel, table=True):
    __tablename__ = "cache_purge_audits"

    audit_id: str = Field(primary_key=True, max_length=96)
    run_id: str = Field(index=True, max_length=96)
    requested_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: Optional[datetime] = Field(default=None)
    dry_run: bool = Field(default=False)
    outcome: str = Field(default="not_attempted", index=True, max_length=48)
    entries_verified: int = Field(default=0)
    entries_removed: int = Field(default=0)
    counts_json: str = Field(default="{}")
    # Redacted message only; never cache content.
    error_redacted: str = Field(default="")


__all__ = ["CacheLogEntryRow", "CachePurgeAuditRow", "utc_now"]
