"""Thin adapter around the existing SPEC-MACRO-QUANT v5.7 engine.

No macro internals are modified.  The adapter builds the existing request,
executes the existing engine, and maps the signed artifact to ``MacroResult``
plus the read-only payload consumed by the household subsystem.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.math_engine.macro_stress_v57.contracts import MacroStressArtifact
from app.pipeline_v2.config import MacroConfig
from app.pipeline_v2.contracts import (
    CacheLogEntry,
    MacroClaim,
    MacroResult,
    RawMaterial,
)
from app.pipeline_v2.errors import MacroEngineError
from app.schemas.workflow_schema import ArticleAnalysis, FetchedArticle
from app.services.macro_stress import (
    build_macro_stress_request,
    execute_macro_stress,
)


@dataclass
class MacroBundle:
    result: MacroResult
    artifact: MacroStressArtifact
    artifact_path: str
    micro_payload: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def _fallback_article(raw: RawMaterial) -> FetchedArticle:
    return FetchedArticle(
        article_id=raw.analysis.get("article_id") or raw.run_id,
        title=raw.article_title or raw.query[:180],
        summary=raw.article_content[:500],
        raw_content=raw.article_content,
        cleaned_content=raw.article_content,
        source=raw.article_source or "pipeline_v2_query",
        link=raw.article_url,
        domain_tag="macro_econ",
    )


def _fallback_analysis(raw: RawMaterial) -> ArticleAnalysis:
    topic = str(
        raw.analysis.get("topic_vi")
        or raw.query_breakdown.macro_topics[:1]
        or raw.query[:80]
    )
    summary = str(
        raw.analysis.get("summary_vi")
        or "Phân tích dựa trên truy vấn người dùng và panel dữ liệu vĩ mô."
    )
    return ArticleAnalysis(
        article_id=raw.analysis.get("article_id") or raw.run_id,
        title_vi=str(raw.analysis.get("title_vi") or raw.article_title or raw.query[:120]),
        topic_vi=topic,
        summary_vi=summary,
        macro_relevance=float(raw.analysis.get("macro_relevance") or 0.5),
        key_claims=[],
        indicator_mentions=[],
        risk_flags=list(raw.analysis.get("risk_flags") or []),
        confidence=float(raw.analysis.get("confidence") or 0.5),
    )


def _artifact_to_claims(
    artifact: MacroStressArtifact,
    support: Sequence[str],
) -> List[MacroClaim]:
    claims: List[MacroClaim] = []
    sha_support = f"artifact:sha256:{artifact.artifact_sha256 or artifact.request_id}"
    all_support = [*support, sha_support]

    def add(text: str) -> None:
        claims.append(MacroClaim(text=text, support=list(all_support)))

    active = artifact.branches.get("active")
    passive = artifact.branches.get("passive")
    if active is not None:
        add(
            "Kịch bản chủ động: GDP YoY trung bình "
            f"{active.gdp_yoy_mean_pct:.3f}%, đáy {active.gdp_yoy_trough_mean_pct:.3f}%, "
            f"độ lệch chuẩn {active.gdp_yoy_std_pct:.3f} điểm phần trăm."
        )
        add(
            "Tổn thất đuôi GDP VaR 99% là "
            f"{active.gdp_tail_loss_var_pct:.3f} điểm phần trăm; ES 99% là "
            f"{active.gdp_tail_loss_es_pct:.3f} điểm phần trăm."
        )
        add(
            "Mất giá tỷ giá VaR 99% là "
            f"{active.fx_deprec_var_pct:.3f}%; dự trữ ngoại hối giảm tối đa "
            f"{active.fx_reserves_drain_billion_usd:.3f} tỷ USD."
        )
        add(
            "Lãi suất liên ngân hàng đỉnh P95 là "
            f"{active.interbank_peak_p95_pct:.3f}%; xác suất NPL vượt ngưỡng là "
            f"{active.npl_breach_probability:.4f}."
        )
    if passive is not None:
        add(
            "Kịch bản thụ động: GDP YoY trung bình "
            f"{passive.gdp_yoy_mean_pct:.3f}%, đáy {passive.gdp_yoy_trough_mean_pct:.3f}%; "
            f"VaR 99% tổn thất GDP {passive.gdp_tail_loss_var_pct:.3f} điểm phần trăm."
        )

    regime = (artifact.nowcast or {}).get("regime") or {}
    stress_probability = regime.get("stress_probability_last")
    if stress_probability is not None:
        add(
            "Xác suất trạng thái căng thẳng vĩ mô gần nhất là "
            f"{float(stress_probability):.4f} theo bộ lọc regime của engine v5.7."
        )

    gatekeeper = artifact.gatekeeper or {}
    if gatekeeper:
        add(
            "Gatekeeper chấp nhận "
            f"{int(gatekeeper.get('joint_valid_particles', 0))} hạt với tỷ lệ "
            f"{float(gatekeeper.get('acceptance_rate', 0.0)):.4f}."
        )
    # ``deterministic_briefing`` is English technical prose produced by the
    # reused macro engine.  The adapter must keep user-facing text Vietnamese,
    # so it renders the same artifact facts with Vietnamese templates instead
    # of copying the briefing verbatim.
    adversarial = artifact.adversarial_shock or {}
    objective = adversarial.get("best_objective")
    if objective is not None:
        text = (
            "Lớp đối kháng v5.7 chọn cú sốc copula-GPD với mục tiêu "
            f"{float(objective):.4f}"
        )
        if stress_probability is not None:
            text += (
                "; xác suất đồng thời ở trạng thái căng thẳng là "
                f"{float(stress_probability) * 100.0:.1f}%."
            )
        else:
            text += "."
        add(text)

    if active is not None and passive is not None:
        passive_loss = float(passive.gdp_tail_loss_var_pct)
        active_loss = float(active.gdp_tail_loss_var_pct)
        if passive_loss > 0.0:
            mitigation = (passive_loss - active_loss) / passive_loss * 100.0
            add(
                "Tổn thất đuôi GDP 99% có trọng số của nhánh thụ động là "
                f"{passive_loss:.4f} điểm phần trăm; hành lang ba tầng của "
                f"NHNN ở nhánh chủ động giảm xuống {active_loss:.4f} điểm "
                f"phần trăm (giảm {mitigation:.2f}% rủi ro đuôi)."
            )
        add(
            "Hành lang chủ động giữ đỉnh lãi suất liên ngân hàng P95 ở "
            f"{float(active.interbank_peak_p95_pct):.2f}% so với "
            f"{float(passive.interbank_peak_p95_pct):.2f}% ở nhánh thụ động, "
            "và giới hạn xác suất vượt ngưỡng NPL ở "
            f"{float(active.npl_breach_probability) * 100.0:.1f}% so với "
            f"{float(passive.npl_breach_probability) * 100.0:.1f}%."
        )
    return claims


def _summary(artifact: MacroStressArtifact) -> str:
    active = artifact.branches.get("active")
    if active is None:
        return (
            "Engine v5.7 hoàn tất nhưng không có nhánh chủ động trong artifact; "
            "kết quả vĩ mô chỉ mang tính mô tả."
        )
    return (
        "Engine SPEC-MACRO-QUANT v5.7 hoàn tất với "
        f"{int(artifact.dimensions.get('joint_valid_particles', 0))} hạt hợp lệ. "
        f"Kịch bản chủ động có GDP YoY trung bình {active.gdp_yoy_mean_pct:.3f}%, "
        f"đáy {active.gdp_yoy_trough_mean_pct:.3f}%; VaR 99% tổn thất GDP "
        f"{active.gdp_tail_loss_var_pct:.3f} điểm phần trăm; mất giá tỷ giá VaR 99% "
        f"{active.fx_deprec_var_pct:.3f}%."
    )


def _micro_payload(artifact: MacroStressArtifact) -> tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    wasserstein = artifact.wasserstein or {}
    factors = wasserstein.get("F_medoids_terminal")
    weights = wasserstein.get("cluster_weights")
    if not factors or not weights:
        raise MacroEngineError(
            "Artifact v5.7 thiếu F_medoids_terminal/cluster_weights; "
            "không thể chạy micro."
        )
    payload: Dict[str, Any] = {
        "F_medoids_terminal": factors,
        "cluster_weights_q": weights,
        "optimal_policy_u": [0.0, 0.0, 0.0],
        "pi_stress_regime2": 0.0,
    }
    # The signed artifact does not expose the optimal policy vector.  A zero
    # vector is an explicit assumption, never a fabricated policy estimate.
    warnings.append(
        "Artifact v5.7 không công bố optimal_policy_u; dùng vector 0 và ghi rõ "
        "giả định trong MicroResult."
    )
    regime = (artifact.nowcast or {}).get("regime") or {}
    stress = regime.get("stress_probability_last")
    if stress is not None:
        payload["pi_stress_regime2"] = float(stress)
    else:
        warnings.append(
            "Artifact v5.7 thiếu nowcast.regime.stress_probability_last; dùng 0."
        )
    payload["macro_snapshot"] = {
        "core_series": (artifact.nowcast or {}).get("core_series") or {},
        "active": (
            artifact.branches["active"].model_dump(mode="json")
            if "active" in artifact.branches
            else {}
        ),
        "passive": (
            artifact.branches["passive"].model_dump(mode="json")
            if "passive" in artifact.branches
            else {}
        ),
        "tail_risk": [
            row.model_dump(mode="json") for row in artifact.tail_risk
        ],
        "artifact_sha256": artifact.artifact_sha256,
        "request_id": artifact.request_id,
    }
    return payload, warnings


async def run_macro(
    *,
    run_id: str,
    raw: RawMaterial,
    cache_entries: Sequence[CacheLogEntry],
    config: MacroConfig,
    artifact_root: Path,
    write_artifact: bool = True,
) -> MacroBundle:
    article = _fallback_article(raw)
    analysis = _fallback_analysis(raw)
    run_dir = artifact_root / run_id / config.artifact_subdir
    if write_artifact:
        run_dir.mkdir(parents=True, exist_ok=True)
    try:
        request = await build_macro_stress_request(
            article,
            analysis,
            artifact_dir=str(run_dir) if write_artifact else None,
            country_code=config.country_code,
        )
    except RuntimeError as exc:
        raise MacroEngineError(
            f"Không xây dựng được yêu cầu vĩ mô: {exc}"
        ) from exc
    if not write_artifact:
        # The v5.7 satellite writes a population file.  Dry-run promises no
        # external writes, so disable only that optional satellite.
        request = request.model_copy(update={"enable_micro_satellite": False})
    try:
        artifact = await asyncio.to_thread(
            execute_macro_stress,
            request,
            artifact_dir=str(run_dir) if write_artifact else None,
        )
    except Exception as exc:
        raise MacroEngineError(
            f"SPEC-MACRO-QUANT v5.7 thất bại: {type(exc).__name__}: {exc}"
        ) from exc
    artifact_path = run_dir / "macro_stress_artifact.json"
    if write_artifact:
        artifact_path.write_text(
            json.dumps(
                artifact.model_dump(mode="json"), ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
    if artifact.status != "OK":
        raise MacroEngineError(
            "SPEC-MACRO-QUANT v5.7 thất bại: " + str(artifact.error)
        )
    payload, warnings = _micro_payload(artifact)
    support = [entry.entry_id for entry in cache_entries][:5]
    result = MacroResult(
        run_id=run_id,
        summary=_summary(artifact),
        claims=_artifact_to_claims(artifact, support),
        warnings=warnings,
        artifact_sha256=artifact.artifact_sha256,
        artifact_path=str(artifact_path) if write_artifact else "",
        request_id=artifact.request_id,
        branch_metrics={
            "active": (
                artifact.branches["active"].model_dump(mode="json")
                if "active" in artifact.branches
                else {}
            ),
            "passive": (
                artifact.branches["passive"].model_dump(mode="json")
                if "passive" in artifact.branches
                else {}
            ),
        },
    )
    return MacroBundle(
        result=result,
        artifact=artifact,
        artifact_path=str(artifact_path) if write_artifact else "",
        micro_payload=payload,
        warnings=warnings,
    )


__all__ = ["MacroBundle", "run_macro"]
