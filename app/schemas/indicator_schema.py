from pydantic import BaseModel
from typing import List

class DataPoint(BaseModel):
    timestamp: str
    value: float

class ForecastPoint(BaseModel):
    timestamp: str
    predicted_mean: float
    volatility: float          # sigma_t từ GARCH
    lower_bound_95: float      # mean - 1.96 * sigma
    upper_bound_95: float      # mean + 1.96 * sigma

class ModelDiagnostics(BaseModel):
    selected_order: List[int]  # [p, d, q]
    aic: float
    bic: float
    arch_effect_pvalue: float  # p-value từ Lagrange Multiplier test
    persistence: float         # alpha + beta từ GARCH
    frequency: str = "unknown" # inferred series frequency (YE/ME/...)

class QuantitativeForecastResponse(BaseModel):
    series_name: str
    historical_data: List[DataPoint]
    forecast_data: List[ForecastPoint]
    diagnostics: ModelDiagnostics