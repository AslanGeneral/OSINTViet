from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from app.schemas.indicator_schema import QuantitativeForecastResponse
from app.math_engine.arima_garch_pipeline import quant_engine
from app.scrapers.tradingeconomics_scraper import trading_economics_scraper

router = APIRouter(prefix="/indicating-room", tags=["Phòng Thông Tin (Indicating Room)"])

class TimeSeriesPayload(BaseModel):
    series_name: str
    dates: List[str]
    values: List[float]

@router.post("/forecast", response_model=QuantitativeForecastResponse)
async def run_econometric_forecast(payload: TimeSeriesPayload):
    if len(payload.dates) != len(payload.values):
        raise HTTPException(status_code=400, detail="Độ dài mảng dates và values lệch nhau kìa vro!")
    
    try:
        result = quant_engine.fit_and_forecast(
            dates=payload.dates,
            values=payload.values,
            series_name=payload.series_name
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tính toán toán học: {str(e)}")


@router.get("/parameters")
async def get_parameters(country: str = Query("vietnam", description="Mã hoặc tên quốc gia: Mỹ, Anh, TQ, Việt Nam")):
    """Trả về dữ liệu chứng khoán và chỉ số kinh tế từ TradingEconomics cho quốc gia được chọn."""
    return trading_economics_scraper.get_parameters_for_country(country)


@router.get("/parameters/all")
async def get_all_parameters():
    """Trả về toàn bộ 4 thị trường: Mỹ, Anh, TQ, Việt Nam."""
    return trading_economics_scraper.get_all_countries()


class LiveScrapeRequest(BaseModel):
    country: str = "vietnam"


@router.post("/live-fetch")
async def trigger_live_fetch(payload: LiveScrapeRequest, background_tasks: BackgroundTasks):
    """Kích hoạt headless browser Playwright cào chỉ số thời gian thực từ Trading Economics."""
    norm_key = payload.country.lower()
    background_tasks.add_task(trading_economics_scraper.scrape_live_country, norm_key)
    return {
        "status": "queued",
        "message": f"Đã kích hoạt headless browser Playwright cào chỉ số cho {payload.country}",
        "country": payload.country,
        "engine": "Playwright Headless Chromium",
    }
