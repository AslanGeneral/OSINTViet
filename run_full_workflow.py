#!/usr/bin/env python3
"""Force one live full-circle run.

Examples:
    .venv/bin/python run_full_workflow.py --reset-memory
    .venv/bin/python run_full_workflow.py --query "Phân tích lạm phát Việt Nam" --reset-memory

Every invocation fetches a random current economics article through headless
Chromium (``--query`` only changes the orchestration question), analyzes it with
JSON-schema-constrained local LLM calls, runs the real World Bank series through
ARIMA-GARCH, executes the universal orchestrator, and writes the recheck
envelope as JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.services.full_circle_workflow import FullCircleWorkflow
from app.services.embedding_engine import embedding_engine
from app.services.economic_series import stats_summary


async def _run(args: argparse.Namespace) -> int:
    workflow = FullCircleWorkflow(
        model=args.model,
        artifact_dir=args.artifact_dir,
    )
    envelope = await workflow.run(
        query=args.query,
        domain_tag=args.domain,
        reset_memory=args.reset_memory,
        fetch_attempts=args.fetch_attempts,
    )
    await embedding_engine.close()

    workflow_path = envelope.artifacts.get("workflow_json", "")
    summary = {
        "run_id": envelope.run_id,
        "status": envelope.status,
        "workflow_json": workflow_path,
        "article": (
            {
                "title": envelope.article.title,
                "url": envelope.article.link,
                "source": envelope.article.source,
                "fetched_at": envelope.article.fetched_at,
            }
            if envelope.article
            else None
        ),
        "word_filter": (
            envelope.word_filter.model_dump(mode="json")
            if envelope.word_filter
            else None
        ),
        "stats": (
            stats_summary(envelope.stats_artifact)
            if envelope.stats_artifact
            else None
        ),
        "orchestration": envelope.orchestration_meta,
        "recheck": (
            {
                "passed": envelope.recheck.passed,
                "business_passed": envelope.recheck.business_passed,
                "failed_checks": envelope.recheck.failed_checks,
                "checks": [
                    check.model_dump(mode="json")
                    for check in envelope.recheck.checks
                ],
            }
            if envelope.recheck
            else None
        ),
        "errors": envelope.errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if envelope.status in {"succeeded", "succeeded_with_dead_branch"}:
        return 0
    print(f"[full-circle] failed; inspect {workflow_path}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Live full-circle OSINT macro workflow")
    parser.add_argument(
        "--query",
        default=None,
        help="Optional fixed orchestration query; the article is still autofetched",
    )
    parser.add_argument("--domain", default="macro_econ")
    parser.add_argument("--model", default=None, help="Local Ollama model override")
    parser.add_argument(
        "--artifact-dir",
        default=None,
        help="JSON artifact root (default: /tmp/osint_workflow)",
    )
    parser.add_argument(
        "--fetch-attempts",
        type=int,
        default=None,
        help="Maximum live article attempts after word-filter rejection",
    )
    parser.add_argument(
        "--reset-memory",
        dest="reset_memory",
        action="store_true",
        default=True,
        help="Clear articles, workflow ledger, Chroma collection, LLM cache, artifacts",
    )
    parser.add_argument(
        "--keep-memory",
        dest="reset_memory",
        action="store_false",
        help="Do not clear runtime memory before the run",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
