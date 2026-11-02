"""World Bank 36-series panel and the v5.7 macro-stress workflow adapter.

The panel is fetched from the World Bank public API.  Every response is cached
on disk (outside git) so a transient network failure cannot turn a live run into
a synthetic fixture.  The nine core workflow series are derived where the World
Bank publishes a level or a related proxy rather than the exact workflow
transform (for example, FX depreciation from the official exchange rate).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from app.core.config import settings
from app.math_engine.macro_stress_v57.contracts import (
    MacroStressArtifact,
    MacroStressRequest,
    SeriesPanel,
    utc_now_iso,
)
from app.math_engine.macro_stress_v57.engine import MacroStressEngineV57
from app.schemas.workflow_schema import ArticleAnalysis, FetchedArticle
from app.services.word_filter import analysis_context

# The first nine rows are the deterministic core contract consumed by the
# simulation/gatekeeper; the remaining rows are the 27 macro-financial context
# series.  Exactly 36 columns are emitted.
PANEL_SERIES: List[Dict[str, Any]] = [
    {"code": "GDP_YOY", "name_vi": "Tăng trưởng GDP thực (năm, %)", "unit": "%", "source": "NY.GDP.MKTP.KD.ZG", "transform": "direct"},
    {"code": "CPI_YOY", "name_vi": "Lạm phát CPI (năm, %)", "unit": "%", "source": "FP.CPI.TOTL.ZG", "transform": "direct"},
    {"code": "FX_DEPREC", "name_vi": "Tỷ giá danh nghĩa mất giá so với năm trước (%)", "unit": "%", "source": "PA.NUS.FCRF", "transform": "pct_change"},
    {"code": "IB_RATE", "name_vi": "Lãi suất cho vay (năm, %)", "unit": "%", "source": "FR.INR.LEND", "transform": "direct"},
    {"code": "FX_RESERVES", "name_vi": "Dự trữ ngoại hối (tỷ USD)", "unit": "tỷ USD", "source": "FI.RES.TOTL.CD", "transform": "divide_1e9"},
    {"code": "NPL_RATIO", "name_vi": "Tỷ lệ nợ xấu (% tổng dư nợ)", "unit": "%", "source": "FB.AST.NPER.ZS", "transform": "direct"},
    {"code": "CREDIT_GROWTH", "name_vi": "Tăng trưởng tín dụng nội địa cho khu vực tư nhân (%)", "unit": "%", "source": "FS.AST.PRVT.GD.ZS", "transform": "pct_change"},
    {"code": "CURRENT_ACCOUNT", "name_vi": "Cán cân vãng lai (tỷ USD)", "unit": "tỷ USD", "source": "BN.CAB.XOKA.CD", "transform": "divide_1e9"},
    {"code": "FINANCIAL_ACCOUNT", "name_vi": "Cán cân tài chính (tỷ USD)", "unit": "tỷ USD", "source": "BN.FIN.TOTL.CD", "transform": "divide_1e9"},
    {"code": "GDP_PCAP_GROWTH", "name_vi": "Tăng trưởng GDP bình quân đầu người (%)", "unit": "%", "source": "NY.GDP.PCAP.KD.ZG", "transform": "direct"},
    {"code": "GDP_DEFLATOR", "name_vi": "Giảm phát GDP (%)", "unit": "%", "source": "NY.GDP.DEFL.KD.ZG", "transform": "direct"},
    {"code": "INVESTMENT_GDP", "name_vi": "Tổng tích lũy tài sản/GDP (%)", "unit": "% GDP", "source": "NE.GDI.TOTL.ZS", "transform": "direct"},
    {"code": "HOUSEHOLD_CONSUMPTION_GDP", "name_vi": "Tiêu dùng hộ gia đình/GDP (%)", "unit": "% GDP", "source": "NE.CON.PRVT.ZS", "transform": "direct"},
    {"code": "GOV_CONSUMPTION_GDP", "name_vi": "Tiêu dùng chính phủ/GDP (%)", "unit": "% GDP", "source": "NE.CON.GOVT.ZS", "transform": "direct"},
    {"code": "EXPORTS_GDP", "name_vi": "Xuất khẩu hàng hóa và dịch vụ/GDP (%)", "unit": "% GDP", "source": "NE.EXP.GNFS.ZS", "transform": "direct"},
    {"code": "IMPORTS_GDP", "name_vi": "Nhập khẩu hàng hóa và dịch vụ/GDP (%)", "unit": "% GDP", "source": "NE.IMP.GNFS.ZS", "transform": "direct"},
    {"code": "INDUSTRY_VA_GDP", "name_vi": "Giá trị gia tăng công nghiệp/GDP (%)", "unit": "% GDP", "source": "NV.IND.TOTL.ZS", "transform": "direct"},
    {"code": "MANUFACTURING_VA_GDP", "name_vi": "Giá trị gia tăng chế tạo/GDP (%)", "unit": "% GDP", "source": "NV.IND.MANF.ZS", "transform": "direct"},
    {"code": "SERVICES_VA_GDP", "name_vi": "Giá trị gia tăng dịch vụ/GDP (%)", "unit": "% GDP", "source": "NV.SRV.TOTL.ZS", "transform": "direct"},
    {"code": "AGRICULTURE_VA_GDP", "name_vi": "Giá trị gia tăng nông nghiệp/GDP (%)", "unit": "% GDP", "source": "NV.AGR.TOTL.ZS", "transform": "direct"},
    {"code": "POPULATION_GROWTH", "name_vi": "Tăng trưởng dân số (%)", "unit": "%", "source": "SP.POP.GROW", "transform": "direct"},
    {"code": "URBAN_POP_SHARE", "name_vi": "Dân số thành thị (% tổng dân số)", "unit": "%", "source": "SP.URB.TOTL.IN.ZS", "transform": "direct"},
    {"code": "LABOR_FORCE_PARTICIPATION", "name_vi": "Tỷ lệ tham gia lực lượng lao động (%)", "unit": "%", "source": "SL.TLF.CACT.ZS", "transform": "direct"},
    {"code": "UNEMPLOYMENT", "name_vi": "Tỷ lệ thất nghiệp (%)", "unit": "%", "source": "SL.UEM.TOTL.ZS", "transform": "direct"},
    {"code": "PRIMARY_ENROLLMENT", "name_vi": "Tỷ lệ nhập học tiểu học (%)", "unit": "%", "source": "SE.PRM.ENRR", "transform": "direct"},
    {"code": "HEALTH_EXPENDITURE_GDP", "name_vi": "Chi tiêu y tế/GDP (%)", "unit": "% GDP", "source": "SH.XPD.CHEX.GD.ZS", "transform": "direct"},
    {"code": "LIFE_EXPECTANCY", "name_vi": "Tuổi thọ kỳ vọng (năm)", "unit": "năm", "source": "SP.DYN.LE00.IN", "transform": "direct"},
    {"code": "REAL_INTEREST_RATE", "name_vi": "Lãi suất thực (%)", "unit": "%", "source": "FR.INR.RINR", "transform": "direct"},
    {"code": "BROAD_MONEY_GDP", "name_vi": "Cung tiền rộng/GDP (%)", "unit": "% GDP", "source": "FM.LBL.BMNY.GD.ZS", "transform": "direct"},
    {"code": "ODA_GDP", "name_vi": "ODA ròng/GNI (%)", "unit": "% GNI", "source": "DT.ODA.ODAT.GN.ZS", "transform": "direct"},
    {"code": "FDI_GDP", "name_vi": "FDI ròng/GDP (%)", "unit": "% GDP", "source": "BX.KLT.DINV.WD.GD.ZS", "transform": "direct"},
    {"code": "TERMS_OF_TRADE", "name_vi": "Tỷ lệ trao đổi thương mại", "unit": "chỉ số", "source": "TT.PRI.MRCH.XD.WD", "transform": "direct"},
    {"code": "RESERVE_MONTHS", "name_vi": "Dự trữ ngoại hối (tháng nhập khẩu)", "unit": "tháng", "source": "FI.RES.TOTL.MO", "transform": "direct"},
    {"code": "FDI_USD", "name_vi": "Dòng FDI ròng (tỷ USD)", "unit": "tỷ USD", "source": "BX.KLT.DINV.CD.WD", "transform": "divide_1e9"},
    {"code": "GDP_CURRENT_USD", "name_vi": "GDP theo giá hiện hành (tỷ USD)", "unit": "tỷ USD", "source": "NY.GDP.MKTP.CD", "transform": "divide_1e9"},
    {"code": "EXCHANGE_RATE", "name_vi": "Tỷ giá chính thức (VND/USD)", "unit": "VND/USD", "source": "PA.NUS.FCRF", "transform": "direct"},
]

PANEL_YEARS = list(range(1996, 2026))


def _cache_key(country_code: str, source_code: str) -> str:
    return hashlib.sha256(f"{country_code}:{source_code}".encode()).hexdigest()[:20]


def _cache_path(country_code: str, source_code: str) -> Path:
    root = Path(settings.MACRO_STRESS_PANEL_CACHE_DIR).expanduser()
    return root / f"{country_code}_{source_code.replace('.', '_')}_{_cache_key(country_code, source_code)}.json"


def _world_bank_url(country_code: str, source_code: str) -> str:
    base = settings.WORKFLOW_WORLD_BANK_BASE_URL.rstrip("/")
    return (
        f"{base}/country/{country_code}/indicator/{source_code}"
        "?format=json&per_page=200"
    )


def _rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise RuntimeError("World Bank response is not the expected [meta, rows] shape")
    rows = [
        {"year": int(str(row["date"])[:4]), "value": float(row["value"])}
        for row in payload[1]
        if isinstance(row, dict)
        and row.get("value") not in (None, "")
        and row.get("date")
    ]
    rows.sort(key=lambda item: item["year"])
    return rows


async def _fetch_source(
    client: httpx.AsyncClient,
    country_code: str,
    source_code: str,
    semaphore: asyncio.Semaphore,
) -> Tuple[str, List[Dict[str, Any]], str, str]:
    """Fetch one World Bank indicator with a stale-cache fallback."""
    cache_path = _cache_path(country_code, source_code)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    cached_rows: Optional[List[Dict[str, Any]]] = None
    cached_url = ""
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_rows = list(cached.get("rows") or [])
            cached_url = str(cached.get("source_url") or "")
            age = now - float(cached.get("fetched_at_epoch") or 0.0)
            if cached_rows and age <= settings.MACRO_STRESS_CACHE_TTL_HOURS * 3600:
                return "cache", cached_rows, cached_url, "fresh_cache"
        except Exception:
            cached_rows = None

    url = _world_bank_url(country_code, source_code)
    last_error: Optional[Exception] = None
    async with semaphore:
        for attempt in range(2):
            try:
                response = await client.get(url)
                response.raise_for_status()
                rows = _rows_from_payload(response.json())
                if not rows:
                    raise RuntimeError("empty World Bank series")
                cache_path.write_text(
                    json.dumps(
                        {
                            "country_code": country_code,
                            "source_code": source_code,
                            "source_url": url,
                            "fetched_at_epoch": now,
                            "fetched_at": utc_now_iso(),
                            "rows": rows,
                        },
                        ensure_ascii=True,
                    ),
                    encoding="utf-8",
                )
                return "live", rows, url, "live"
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.4 * (attempt + 1))
    if cached_rows:
        return "cache", cached_rows, cached_url or url, f"stale_cache:{type(last_error).__name__}"
    raise RuntimeError(f"World Bank {source_code} unavailable: {last_error}")


def _year_map(rows: Sequence[Dict[str, Any]]) -> Dict[int, float]:
    return {int(row["year"]): float(row["value"]) for row in rows}


def _transform_value(
    transform: str,
    year: int,
    source_values: Dict[int, float],
) -> Optional[float]:
    current = source_values.get(year)
    if current is None:
        return None
    if transform == "direct":
        return float(current)
    if transform == "divide_1e9":
        return float(current) / 1e9
    if transform == "pct_change":
        previous = source_values.get(year - 1)
        if previous is None or abs(previous) < 1e-12:
            return None
        return (float(current) / float(previous) - 1.0) * 100.0
    raise ValueError(f"unknown transform {transform}")


async def fetch_world_bank_panel(
    country_code: str = "VNM",
) -> Tuple[SeriesPanel, Dict[str, Any]]:
    """Build the strict 36-series panel from live World Bank data.

    The returned metadata records cache/live status and every source indicator
    so a workflow artifact can prove the panel was not manufactured.
    """
    source_codes = sorted({spec["source"] for spec in PANEL_SERIES})
    semaphore = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        fetched = await asyncio.gather(
            *[
                _fetch_source(client, country_code, source, semaphore)
                for source in source_codes
            ],
            return_exceptions=True,
        )
    source_rows: Dict[str, List[Dict[str, Any]]] = {}
    source_status: Dict[str, Any] = {}
    source_urls: Dict[str, str] = {}
    failures: List[str] = []
    for source, result in zip(source_codes, fetched):
        if isinstance(result, Exception):
            failures.append(f"{source}: {type(result).__name__}: {result}")
            continue
        status, rows, url, detail = result
        source_rows[source] = rows
        source_urls[source] = url
        source_status[source] = {"status": status, "detail": detail, "n_obs": len(rows)}
    if failures:
        # A live run must not silently degrade to a synthetic panel.  Missing
        # indicators are only tolerated if at least 30 of 36 real columns are
        # available; the calibrator still records missing cells.
        if len(source_rows) < 30:
            raise RuntimeError(
                "World Bank panel incomplete: " + " | ".join(failures[:8])
            )

    source_maps = {
        source: _year_map(rows) for source, rows in source_rows.items()
    }
    values: List[List[Optional[float]]] = []
    missing_cells = 0
    for year in PANEL_YEARS:
        row: List[Optional[float]] = []
        for spec in PANEL_SERIES:
            value = _transform_value(
                spec["transform"],
                year,
                source_maps.get(spec["source"], {}),
            )
            row.append(value)
            if value is None:
                missing_cells += 1
        values.append(row)

    # Ensure every column has at least one observed value.  If a source failed
    # completely, retain the 36-column contract and mark it missing rather than
    # inventing a value; the engine will reject all-missing columns explicitly.
    for column, spec in enumerate(PANEL_SERIES):
        if all(row[column] is None for row in values):
            raise RuntimeError(
                f"panel column {spec['code']} has no observations "
                f"(source {spec['source']}); refusing to synthesize it"
            )

    panel = SeriesPanel(
        series_codes=[spec["code"] for spec in PANEL_SERIES],
        series_names_vi=[spec["name_vi"] for spec in PANEL_SERIES],
        units=[spec["unit"] for spec in PANEL_SERIES],
        dates=[f"{year}-12-31" for year in PANEL_YEARS],
        values=values,
        frequency="annual",
        source="world_bank_live" if any(
            item.get("status") == "live" for item in source_status.values()
        ) else "world_bank_cache",
        source_url=next(
            (
                source_urls[spec["source"]]
                for spec in PANEL_SERIES
                if source_urls.get(spec["source"])
            ),
            "",
        ),
        fetched_at=utc_now_iso(),
    )
    metadata = {
        "country_code": country_code,
        "n_series": len(PANEL_SERIES),
        "n_dates": len(PANEL_YEARS),
        "year_start": PANEL_YEARS[0],
        "year_end": PANEL_YEARS[-1],
        "missing_cells": missing_cells,
        "sources": source_status,
        "source_urls": source_urls,
        "failures": failures,
        "fetched_at": panel.fetched_at,
    }
    return panel, metadata


def _request_defaults() -> Dict[str, Any]:
    return {
        "horizon": settings.MACRO_STRESS_HORIZON,
        "n_particles": settings.MACRO_STRESS_N_PARTICLES,
        "k_rep": settings.MACRO_STRESS_K_REP,
        "m_cand": settings.MACRO_STRESS_M_CAND,
        "cma_population": settings.MACRO_STRESS_CMA_POPULATION,
        "cma_generations": settings.MACRO_STRESS_CMA_GENERATIONS,
        "enable_micro_satellite": settings.MACRO_STRESS_ENABLE_MICRO,
        "firm_count": settings.MACRO_STRESS_FIRM_COUNT,
        "firm_data_path": settings.MACRO_STRESS_FIRM_DATA_PATH or None,
    }


async def build_macro_stress_request(
    article: FetchedArticle,
    analysis: ArticleAnalysis,
    *,
    artifact_dir: Optional[str] = None,
    country_code: str = settings.WORKFLOW_WORLD_BANK_COUNTRY,
) -> MacroStressRequest:
    panel, metadata = await fetch_world_bank_panel(country_code=country_code)
    return MacroStressRequest(
        panel=panel,
        country_code=country_code,
        article_id=article.article_id or "",
        article_url=article.link,
        article_title_vi=analysis.topic_vi or article.title,
        article_context={
            **analysis_context(analysis),
            "panel_metadata": metadata,
        },
        stress_artifact_dir=artifact_dir,
        **_request_defaults(),
    )


def execute_macro_stress(
    request: MacroStressRequest,
    *,
    artifact_dir: Optional[str] = None,
) -> MacroStressArtifact:
    """Run v5.7.  The artifact is the only object returned across the boundary."""
    if artifact_dir and not request.stress_artifact_dir:
        request = request.model_copy(update={"stress_artifact_dir": artifact_dir})
    return MacroStressEngineV57().run(request)


def macro_stress_summary(artifact: MacroStressArtifact) -> Dict[str, Any]:
    if artifact.status != "OK":
        return {
            "status": artifact.status,
            "error": artifact.error,
            "request_id": artifact.request_id,
        }
    return {
        "status": artifact.status,
        "engine_version": artifact.engine_version,
        "request_id": artifact.request_id,
        "artifact_sha256": artifact.artifact_sha256,
        "execution_time_seconds": artifact.execution_time_seconds,
        "peak_rss_mb": artifact.peak_rss_mb,
        "n_series": artifact.dimensions.get("N_series"),
        "joint_valid_particles": artifact.dimensions.get("joint_valid_particles"),
        "acceptance_rate": artifact.dimensions.get("acceptance_rate"),
        "passive": (
            artifact.branches["passive"].model_dump(mode="json")
            if "passive" in artifact.branches
            else {}
        ),
        "active": (
            artifact.branches["active"].model_dump(mode="json")
            if "active" in artifact.branches
            else {}
        ),
        "briefing": artifact.deterministic_briefing[:2],
        "micro_mode": artifact.micro_downscaling.get("mode"),
    }


__all__ = [
    "PANEL_SERIES",
    "PANEL_YEARS",
    "build_macro_stress_request",
    "execute_macro_stress",
    "fetch_world_bank_panel",
    "macro_stress_summary",
]
