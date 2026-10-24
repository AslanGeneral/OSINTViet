"""SQLite ledger for full-circle workflow JSON envelopes."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowRun(SQLModel, table=True):
    """One row per workflow run; payload_json is the canonical envelope."""

    __tablename__ = "workflow_runs"

    run_id: str = Field(primary_key=True, max_length=96)
    status: str = Field(default="running", index=True)
    query: str = Field(default="")
    payload_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


__all__ = ["WorkflowRun", "utc_now"]
