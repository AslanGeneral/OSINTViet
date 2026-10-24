"""Real economic time-series input for ARIMA-GARCH.

The series is fetched from the World Bank's public JSON API and converted into
the strict ``StatsRequest`` contract.  No synthetic or fixture data is used by
the full-circle workflow.  The same JSON is embedded in the workflow envelope
and is the only input accepted by the ARIMA-GARCH stage.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np

from app.core.config import settings
from app.math_engine.arima_garch_pipeline import MacroQuantitativeEngine
from app.schemas.workflow_schema import (
    ArticleAnalysis,
    FetchedArticle,
    StatsArtifact,
    StatsRequest,
    utc_now_iso,
)
from app.services.word_filter import analysis_context

# Only indicators with at least 30 usable annual observations are included.
INDICATOR_CATALOG: Dict[str, Dict[str, Any]] = {
    "FP.CPI.TOTL.ZG": {
        "name_vi": "Tỷ lệ lạm phát theo CPI (năm, %)",
        "keywords": (
            "lạm phát",
            "cpi",
            "giá cả",
            "giá vàng",
            "giá dầu",
            "sức mua",
            "hàng hóa",
        ),
        "reason_vi": "Bài viết đề cập giá cả/lạm phát; dùng chuỗi CPI Việt Nam.",
    },
    "NY.GDP.MKTP.KD.ZG": {
        "name_vi": "Tăng trưởng GDP thực (năm, %)",
        "keywords": (
            "gdp",
            "tăng trưởng",
            "kinh tế",
            "sản lượng",
            "đầu tư",
            "tiêu dùng",
        ),
        "reason_vi": "Bài viết đề cập tăng trưởng/kinh tế; dùng chuỗi GDP Việt Nam.",
    },
    "SL.UEM.TOTL.ZS": {
        "name_vi": "Tỷ lệ thất nghiệp (năm, %)",
        "keywords": ("thất nghiệp", "việc làm", "lao động"),
        "reason_vi": "Bài viết đề cập việc làm/thất nghiệp; dùng chuỗi lao động.",
    },
}


def _fetch_url(indicator_code: str, country_code: str) -> str:
    base = settings.WORKFLOW_WORLD_BANK_BASE_URL.rstrip("/")
    return (
        f"{base}/country/{country_code}/indicator/{indicator_code}"
        "?format=json&per_page=100"
    )


def select_indicator(
    article: FetchedArticle,
    analysis: ArticleAnalysis,
) -> Tuple[str, str]:
    """Choose a real indicator deterministically from the article context."""
    for mention in analysis.indicator_mentions:
        if mention.code in INDICATOR_CATALOG:
            return mention.code, "LLM analysis named this World Bank indicator."

    text = " ".join(
        [
            article.title,
            article.summary,
            analysis.topic_vi,
            analysis.summary_vi,
            " ".join(claim.claim_vi for claim in analysis.key_claims),
        ]
    ).casefold()

    best_code = "FP.CPI.TOTL.ZG"
    best_hits = 0
    for code, spec in INDICATOR_CATALOG.items():
        hits = sum(1 for term in spec["keywords"] if term in text)
        if hits > best_hits:
            best_code = code
            best_hits = hits
    if best_hits == 0:
        return (
            "FP.CPI.TOTL.ZG",
            "Không có chỉ báo cụ thể; dùng CPI làm bối cảnh vĩ mô mặc định.",
        )
    return best_code, str(INDICATOR_CATALOG[best_code]["reason_vi"])


async def fetch_world_bank_series(
    indicator_code: str,
    country_code: str = settings.WORKFLOW_WORLD_BANK_COUNTRY,
) -> Tuple[List[str], List[float], str]:
    """Return ascending annual dates/values from the World Bank JSON API."""
    url = _fetch_url(indicator_code, country_code)
    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise RuntimeError("Unexpected World Bank response shape")

    rows = [
        row
        for row in payload[1]
        if isinstance(row, dict) and row.get("value") is not None and row.get("date")
    ]
    rows.sort(key=lambda row: int(str(row["date"])[:4]))
    dates = [f"{int(str(row['date'])[:4])}-12-31" for row in rows]
    values = [float(row["value"]) for row in rows]
    if len(values) < settings.WORKFLOW_MIN_STATS_POINTS:
        raise RuntimeError(
            f"{indicator_code} has only {len(values)} usable observations"
        )
    return dates, values, url


async def build_stats_request(
    article: FetchedArticle,
    analysis: ArticleAnalysis,
) -> StatsRequest:
    """Fetch the selected real series and wrap it in the JSON contract."""
    selected_code, reason = select_indicator(article, analysis)
    errors: List[str] = []
    candidates = [selected_code] + [
        code for code in INDICATOR_CATALOG if code != selected_code
    ]
    for code in candidates:
        try:
            dates, values, source_url = await fetch_world_bank_series(code)
        except Exception as exc:
            errors.append(f"{code}: {type(exc).__name__}: {exc}")
            continue
        return StatsRequest(
            series_name=INDICATOR_CATALOG[code]["name_vi"],
            indicator_code=code,
            indicator_name_vi=INDICATOR_CATALOG[code]["name_vi"],
            country_code=settings.WORKFLOW_WORLD_BANK_COUNTRY,
            frequency="annual",
            source="world_bank",
            source_url=source_url,
            dates=dates,
            values=values,
            point_count=len(values),
            article_id=article.article_id or "",
            article_url=article.link,
            selection_reason_vi=reason,
            article_context=analysis_context(analysis),
            fetched_at=utc_now_iso(),
        )
    raise RuntimeError("No usable World Bank series: " + " | ".join(errors))


def execute_arima_garch(
    request: StatsRequest,
    forecast_steps: int = settings.WORKFLOW_ARIMA_FORECAST_STEPS,
) -> StatsArtifact:
    """Run the existing ARIMA-GARCH engine on the validated JSON request."""
    try:
        engine = MacroQuantitativeEngine(forecast_steps=forecast_steps)
        result = engine.fit_and_forecast(
            dates=list(request.dates),
            values=list(request.values),
            series_name=request.series_name,
        )
        response = result.model_dump(mode="json")
        values = np.asarray(request.values, dtype=float)
        descriptive = {
            "count": float(values.size),
            "mean": round(float(np.mean(values)), 6),
            "std": round(float(np.std(values, ddof=1)), 6),
            "min": round(float(np.min(values)), 6),
            "max": round(float(np.max(values)), 6),
            "last": round(float(values[-1]), 6),
        }
        digest = hashlib.sha256(
            json.dumps(
                {"request": request.model_dump(mode="json"), "response": response},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return StatsArtifact(
            status="OK",
            request=request,
            response=response,
            descriptive_stats=descriptive,
            forecast_frequency=str(result.diagnostics.frequency),
            executed_at=utc_now_iso(),
            evidence_sha256=digest,
        )
    except Exception as exc:
        return StatsArtifact(
            status="FAILED",
            request=request,
            error=f"{type(exc).__name__}: {exc}",
            executed_at=utc_now_iso(),
        )


def stats_summary(artifact: StatsArtifact) -> Dict[str, Any]:
    """Compact summary for logs and the CLI."""
    if artifact.status != "OK" or not artifact.response:
        return {"status": artifact.status, "error": artifact.error}
    diagnostics = artifact.response.get("diagnostics", {})
    forecast = artifact.response.get("forecast_data", [])
    return {
        "status": artifact.status,
        "series": artifact.request.series_name if artifact.request else "",
        "indicator_code": artifact.request.indicator_code if artifact.request else "",
        "point_count": artifact.request.point_count if artifact.request else 0,
        "frequency": artifact.forecast_frequency,
        "order": diagnostics.get("selected_order"),
        "persistence": diagnostics.get("persistence"),
        "last_forecast": forecast[-1] if forecast else None,
        "descriptive_stats": artifact.descriptive_stats,
    }


__all__ = [
    "INDICATOR_CATALOG",
    "build_stats_request",
    "execute_arima_garch",
    "fetch_world_bank_series",
    "select_indicator",
    "stats_summary",
]
