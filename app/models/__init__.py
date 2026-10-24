"""SQLModel models for persisted OSINT data."""

from app.models.article import Article
from app.models.workflow_run import WorkflowRun

__all__ = ["Article", "WorkflowRun"]
