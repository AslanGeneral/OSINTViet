"""Persisted article metadata for the ingestion and retrieval engine."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for SQLModel DateTime columns."""
    return datetime.now(timezone.utc)


class Article(SQLModel, table=True):
    """Normalized article row shared by SQLite storage and ChromaDB metadata."""

    __tablename__ = "articles"

    id: str = Field(primary_key=True, max_length=64)
    title: str = Field(index=True)
    source: str = Field(default="unknown", index=True)
    url: str = Field(default="", index=True)
    raw_content: str = Field(default="")
    cleaned_content: str = Field(default="")
    relevance_score: float = Field(default=0.0)
    ingested_at: datetime = Field(default_factory=utc_now, index=True)
    domain_tag: str = Field(default="general", index=True)
    # Canonical, validated JSON payload used as the SQLite write input.  The
    # normalized columns above remain queryable; this column is the audit trail.
    payload_json: str = Field(default="{}")


__all__ = ["Article", "utc_now"]
