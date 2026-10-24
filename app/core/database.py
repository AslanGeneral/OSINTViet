"""Async SQLite storage for normalized OSINT articles and workflow JSON."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel, select

from app.core.config import settings
from app.models.article import Article
from app.models.workflow_run import WorkflowRun, utc_now

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create missing tables and migrate the empty legacy Article schema."""
    # Import inside the function so SQLModel metadata is populated even when
    # this module is imported before any API route or CLI command.
    from app.models.article import Article as _Article  # noqa: F401
    from app.models.workflow_run import WorkflowRun as _WorkflowRun  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

        # SQLite's CREATE TABLE IF NOT EXISTS does not add columns.  The
        # original Article table had no payload_json, so add it idempotently.
        result = await connection.exec_driver_sql("PRAGMA table_info(articles)")
        columns = {row[1] for row in result.fetchall()}
        if "payload_json" not in columns:
            await connection.exec_driver_sql(
                "ALTER TABLE articles ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
            )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    async with async_session_factory() as session:
        yield session


async def upsert_articles(articles: Sequence[Article]) -> None:
    """Insert or update article rows in one transaction."""
    if not articles:
        return
    async with async_session_factory() as session:
        for article in articles:
            await session.merge(article)
        await session.commit()


async def get_article(article_id: str) -> Optional[Article]:
    """Read one article row by primary key."""
    async with async_session_factory() as session:
        return await session.get(Article, article_id)


async def save_workflow_run(
    run_id: str,
    status: str,
    query: str,
    payload_json: str,
) -> None:
    """Persist the canonical workflow envelope as validated JSON text."""
    await init_db()
    async with async_session_factory() as session:
        row = await session.get(WorkflowRun, run_id)
        if row is None:
            row = WorkflowRun(
                run_id=run_id,
                status=status,
                query=query,
                payload_json=payload_json,
            )
        else:
            row.status = status
            row.query = query
            row.payload_json = payload_json
            row.updated_at = utc_now()
        session.add(row)
        await session.commit()


async def get_workflow_run(run_id: str) -> Optional[WorkflowRun]:
    """Read one workflow ledger row by primary key."""
    async with async_session_factory() as session:
        return await session.get(WorkflowRun, run_id)


async def delete_all_articles() -> int:
    """Delete all article rows and return the number removed."""
    await init_db()
    async with async_session_factory() as session:
        result = await session.execute(select(Article))
        rows = list(result.scalars().all())
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


async def delete_all_workflow_runs() -> int:
    """Delete workflow history rows and return the number removed."""
    await init_db()
    async with async_session_factory() as session:
        result = await session.execute(select(WorkflowRun))
        rows = list(result.scalars().all())
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


__all__ = [
    "Article",
    "WorkflowRun",
    "async_session_factory",
    "delete_all_articles",
    "delete_all_workflow_runs",
    "engine",
    "get_article",
    "get_session",
    "get_workflow_run",
    "init_db",
    "save_workflow_run",
    "upsert_articles",
]
