"""Theory-plugin bridge for SPEC-MACRO-QUANT v5.7.

The orchestrator calls this plugin once per hypothesis.  The plugin never calls
an LLM and never invents data: it loads the signed JSON artifact produced by
``app.services.macro_stress`` and returns a compact evidence object.  Every
hypothesis invocation writes a receipt so the workflow recheck can prove that
the theoretical critic was actually grounded in the v5.7 engine.
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
from app.math_engine.macro_stress_v57.contracts import MacroStressArtifact
from app.math_engine.macro_stress_v57.jcs import sha256_value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_artifact() -> MacroStressArtifact | None:
    path = os.getenv("MACRO_STRESS_ARTIFACT_PATH", "").strip()
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    artifact = MacroStressArtifact.model_validate(
        json.loads(file_path.read_text(encoding="utf-8"))
    )
    payload = artifact.model_dump(mode="json")
    digest = payload.pop("artifact_sha256", "")
    if not digest or sha256_value(payload) != digest:
        raise ValueError("MacroStressArtifact SHA-256 digest mismatch")
    return artifact


def _record_invocation(evidence: Dict[str, Any], parameters: Dict[str, Any]) -> None:
    directory = os.getenv("MACRO_STRESS_INVOCATION_DIR", "").strip()
    if not directory:
        return
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    receipt = {
        "invoked_at": _now_iso(),
        "plugin": "macro_stress_v57",
        "engine_version": evidence.get("engine_version"),
        "artifact_sha256": evidence.get("artifact_sha256"),
        "request_id": evidence.get("request_id"),
        "hypothesis_facts_sha256": hashlib.sha256(
            json.dumps(parameters, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    (target / f"invocation_{uuid.uuid4().hex}.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@hub.register_theory_algo("macro_stress_v57")
async def macro_stress_v57_theory(
    parameters: Dict[str, Any] | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Return compact, signed v5.7 evidence for the theoretical critic."""
    parameters = parameters or {}
    try:
        artifact = _load_artifact()
    except Exception as exc:
        return {
            "status": "ERROR",
            "plugin": "macro_stress_v57",
            "reason_vi": (
                "Không đọc được MacroStressArtifact: "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    if artifact is None:
        return {
            "status": "SKIPPED",
            "plugin": "macro_stress_v57",
            "reason_vi": (
                "MACRO_STRESS_ARTIFACT_PATH chưa được đặt hoặc file không tồn tại."
            ),
        }
    if artifact.status != "OK":
        return {
            "status": artifact.status,
            "plugin": "macro_stress_v57",
            "reason_vi": "Engine v5.7 không tạo được artifact hợp lệ.",
            "error": artifact.error,
        }

    passive = artifact.branches.get("passive")
    active = artifact.branches.get("active")
    gate = artifact.gatekeeper or {}
    entropy = gate.get("entropy_pooling") or {}
    micro = artifact.micro_downscaling or {}
    shock = artifact.adversarial_shock or {}
    evidence = {
        "status": "OK",
        "plugin": "macro_stress_v57",
        "engine_version": artifact.engine_version,
        "request_id": artifact.request_id,
        "artifact_sha256": artifact.artifact_sha256,
        "execution_time_seconds": artifact.execution_time_seconds,
        "peak_rss_mb": artifact.peak_rss_mb,
        "nowcast_state_source": (artifact.nowcast or {}).get("state_source"),
        "dimensions": {
            key: artifact.dimensions.get(key)
            for key in (
                "N_series",
                "r_factors",
                "companion_dim_tilde_r",
                "p_lags",
                "regimes_K",
                "haar_rotation_draws_MQ",
                "sobol_particles_Np",
                "joint_valid_particles",
                "acceptance_rate",
                "wasserstein_archetypes_Krep",
                "micro_firms",
            )
        },
        "gatekeeper": {
            "joint_valid_particles": gate.get("joint_valid_particles"),
            "acceptance_rate": gate.get("acceptance_rate"),
            "fallback_used": gate.get("fallback_used"),
            "entropy_kish_ess": entropy.get("kish_ess"),
            "prior_kish_ess": gate.get("prior_kish_ess"),
        },
        "passive_branch": passive.model_dump(mode="json") if passive else {},
        "active_branch": active.model_dump(mode="json") if active else {},
        "shock": {
            "mode": shock.get("mode"),
            "mahalanobis_radius": shock.get("mahalanobis_radius"),
            "best_metrics": shock.get("best_metrics"),
            "top_k": shock.get("top_k"),
        },
        "micro_downscaling": {
            "enabled": micro.get("enabled"),
            "mode": micro.get("mode"),
            "n_firms": micro.get("n_firms"),
            "share_expected_stress_above_080": micro.get(
                "share_expected_stress_above_080"
            ),
        },
        "deterministic_briefing_vi": artifact.deterministic_briefing,
        "interpretation_vi": (
            "Đây là bằng chứng mô phỏng vĩ mô vi mô có kiểm chứng rủi ro đuôi, "
            "không phải bằng chứng nhân quả. Artifact được ký bằng RFC 8785 JCS "
            "và SHA-256; mọi so sánh passive/active dùng cùng common random numbers."
        ),
    }
    _record_invocation(evidence, parameters)
    return evidence


__all__ = ["macro_stress_v57_theory"]
