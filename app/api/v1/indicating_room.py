from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from app.schemas.indicator_schema import QuantitativeForecastResponse
from app.math_engine.arima_garch_pipeline import quant_engine

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