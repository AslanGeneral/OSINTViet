import warnings
import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.stats.diagnostic import het_arch
from arch import arch_model
import pmdarima as pm

from app.schemas.indicator_schema import (
    QuantitativeForecastResponse, 
    DataPoint, 
    ForecastPoint, 
    ModelDiagnostics
)
from app.math_engine.stationarity import find_differencing_order

warnings.filterwarnings("ignore")

class MacroQuantitativeEngine:
    def __init__(self, forecast_steps: int = 12):
        self.forecast_steps = forecast_steps

    def fit_and_forecast(self, dates: list, values: list, series_name: str = "Indicator") -> QuantitativeForecastResponse:
        """
        Thực thi quy trình: Auto-ARIMA -> Residual Extraction -> GARCH(1,1) -> Forecast Bounds
        """
        df = pd.DataFrame({"ds": pd.to_datetime(dates), "y": values}).dropna()
        df = df.sort_values("ds").reset_index(drop=True)
        series = df["y"]

        if len(series) < 30:
            raise ValueError("WTF bro, chuỗi thời gian ngắn quá! Cần tối thiểu 30 điểm dữ liệu.")

        # 1. Tìm bậc sai phân d
        d_order = find_differencing_order(series)

        # 2. Auto-fit ARIMA để tìm p, q tối ưu theo AIC
        auto_model = pm.auto_arima(
            series,
            d=d_order,
            start_p=0, max_p=3,
            start_q=0, max_q=3,
            seasonal=False,
            stepwise=True,
            suppress_warnings=True,
            error_action="ignore"
        )
        p, d, q = auto_model.order
        
        # Fit lại bằng statsmodels để lấy residuals sạch
        arima_fitted = ARIMA(series, order=(p, d, q)).fit()
        residuals = arima_fitted.resid

        # 3. Kiểm định ARCH Effect trên phần dư
        # Lagrange Multiplier (LM) test
        lm_test = het_arch(residuals)
        arch_pvalue = float(lm_test[1])

        # 4. Dự báo Conditional Mean từ ARIMA
        mean_forecast = arima_fitted.forecast(steps=self.forecast_steps)
        
        # 5. Fit GARCH(1, 1) trên residuals để mô hình hóa Volatility
        # Nhân residuals với 100 để tránh lỗi hội tụ do scale quá bé (Standard practice trong Quantitative Finance)
        res_scaled = residuals * 100
        garch = arch_model(res_scaled, vol="Garch", p=1, q=1, mean="Zero", rescale=False)
        garch_fitted = garch.fit(disp="off")
        
        # Dự báo phương sai h bước tiếp theo
        garch_forecast = garch_fitted.forecast(horizon=self.forecast_steps)
        # Scale ngược lại variance và tính độ lệch chuẩn (volatility = sigma)
        forecast_variance = garch_forecast.variance.iloc[-1].values / (100 ** 2)
        forecast_volatility = np.sqrt(np.maximum(forecast_variance, 1e-6))

        # Tính Persistence: alpha + beta
        alpha = garch_fitted.params.get("alpha[1]", 0.0)
        beta = garch_fitted.params.get("beta[1]", 0.0)
        persistence = float(alpha + beta)

        # 6. Tạo timeline tương lai cho forecast
        last_date = df["ds"].iloc[-1]
        # Giả định dữ liệu theo tháng (MS: Month Start) hoặc tuần tùy dữ liệu
        future_dates = pd.date_range(start=last_date, periods=self.forecast_steps + 1, freq="M")[1:]

        # 7. Đóng gói kết quả theo Schema Pydantic
        historical_points = [
            DataPoint(timestamp=row["ds"].strftime("%Y-%m-%d"), value=round(float(row["y"]), 4))
            for _, row in df.iterrows()
        ]

        forecast_points = []
        for dt, mu, sigma in zip(future_dates, mean_forecast, forecast_volatility):
            mu_val = float(mu)
            sigma_val = float(sigma)
            forecast_points.append(
                ForecastPoint(
                    timestamp=dt.strftime("%Y-%m-%d"),
                    predicted_mean=round(mu_val, 4),
                    volatility=round(sigma_val, 4),
                    lower_bound_95=round(mu_val - 1.96 * sigma_val, 4),
                    upper_bound_95=round(mu_val + 1.96 * sigma_val, 4),
                )
            )

        diagnostics = ModelDiagnostics(
            selected_order=[int(p), int(d), int(q)],
            aic=round(float(arima_fitted.aic), 2),
            bic=round(float(arima_fitted.bic), 2),
            arch_effect_pvalue=round(arch_pvalue, 6),
            persistence=round(persistence, 4)
        )

        return QuantitativeForecastResponse(
            series_name=series_name,
            historical_data=historical_points,
            forecast_data=forecast_points,
            diagnostics=diagnostics
        )

quant_engine = MacroQuantitativeEngine(forecast_steps=6)