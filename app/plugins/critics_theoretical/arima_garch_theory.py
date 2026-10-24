"""Theory-plugin bridge between the orchestrator and ARIMA-GARCH.

Stage 4 asks every registered theory algorithm for a JSON check.  This plugin
does not generate data and does not call an LLM: it loads the workflow's
validated ``StatsArtifact`` (written from the real World Bank series), returns a
compact evidence object, and records a per-hypothesis invocation receipt.

The full artifact stays in the workflow envelope; the prompt receives only the
compact summary so a 3B local model is not flooded with historical points.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.core.plugin_registry import hub
from app.schemas.workflow_schema import StatsArtifact


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_artifact() -> StatsArtifact | None:
    path = os.getenv("ARIMA_GARCH_STATS_PATH", "").strip()
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    return StatsArtifact.model_validate(payload)


def _record_invocation(evidence: Dict[str, Any], parameters: Dict[str, Any]) -> None:
    directory = os.getenv("ARIMA_GARCH_INVOCATION_DIR", "").strip()
    if not directory:
        return
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    invocation = {
        "invoked_at": _now_iso(),
        "plugin": "arima_garch",
        "hypothesis_facts_sha256": hashlib.sha256(
            json.dumps(parameters, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "evidence_sha256": evidence.get("evidence_sha256", ""),
        "indicator_code": evidence.get("indicator_code", ""),
    }
    (target / f"invocation_{uuid.uuid4().hex}.json").write_text(
        json.dumps(invocation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@hub.register_theory_algo("arima_garch")
async def arima_garch_theory(
    parameters: Dict[str, Any] | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Return the validated ARIMA-GARCH evidence as a JSON object."""
    parameters = parameters or {}
    try:
        artifact = _load_artifact()
    except Exception as exc:
        return {
            "status": "ERROR",
            "plugin": "arima_garch",
            "reason_vi": f"Không đọc được StatsArtifact: {type(exc).__name__}: {exc}",
        }

    if artifact is None:
        return {
            "status": "SKIPPED",
            "plugin": "arima_garch",
            "reason_vi": "ARIMA_GARCH_STATS_PATH chưa được đặt hoặc file không tồn tại.",
        }

    if artifact.status != "OK" or not artifact.response or not artifact.request:
        return {
            "status": artifact.status,
            "plugin": "arima_garch",
            "reason_vi": "ARIMA-GARCH không tạo được artifact hợp lệ.",
            "error": artifact.error,
        }

    response = artifact.response
    forecast = list(response.get("forecast_data") or [])
    diagnostics = dict(response.get("diagnostics") or {})
    evidence = {
        "status": "OK",
        "plugin": "arima_garch",
        "series_name": artifact.request.series_name,
        "indicator_code": artifact.request.indicator_code,
        "country_code": artifact.request.country_code,
        "source": artifact.request.source,
        "source_url": artifact.request.source_url,
        "point_count": artifact.request.point_count,
        "forecast_frequency": artifact.forecast_frequency,
        "descriptive_stats": artifact.descriptive_stats,
        "selected_order": diagnostics.get("selected_order"),
        "aic": diagnostics.get("aic"),
        "bic": diagnostics.get("bic"),
        "arch_effect_pvalue": diagnostics.get("arch_effect_pvalue"),
        "persistence": diagnostics.get("persistence"),
        "last_observation": (
            response.get("historical_data", [])[-1]
            if response.get("historical_data")
            else None
        ),
        "forecast_head": forecast[:2],
        "forecast_tail": forecast[-2:] if forecast else [],
        "evidence_sha256": artifact.evidence_sha256,
        "interpretation_vi": (
            "Chuỗi là dữ liệu thật từ World Bank; đây là kiểm tra thống kê "
            "có điều kiện, không phải bằng chứng nhân quả."
        ),
    }
    _record_invocation(evidence, parameters)
    return evidence


__all__ = ["arima_garch_theory"]
