import httpx
import numpy as np
import pandas as pd

# Tạo 60 tháng dữ liệu mô phỏng giá xăng có xu hướng tăng + biến động mạnh
dates = pd.date_range("2021-01-01", periods=60, freq="M").strftime("%Y-%m-%d").tolist()
# Random walk with drift + heteroskedastic shock
np.random.seed(42)
innovations = np.random.normal(0, 1, 60) * np.linspace(1, 4, 60) # Volatility tăng dần
values = np.cumsum(0.5 + innovations) + 20.0
values = [round(float(v), 2) for v in values]

payload = {
    "series_name": "Gia_Xang_RON95_Mophong",
    "dates": dates,
    "values": values
}

res = httpx.post("http://127.0.0.1:8000/api/v1/indicating-room/forecast", json=payload, timeout=30.0)

if res.status_code == 200:
    data = res.json()
    print("[+] Test thành công! Kết quả trả về:")
    print(f"    - Order được chọn: {data['diagnostics']['selected_order']}")
    print(f"    - Persistence (Alpha + Beta): {data['diagnostics']['persistence']}")
    print(f"    - Dự báo tháng đầu tiên: {data['forecast_data'][0]['predicted_mean']} "
          f"[Dải rủi ro: {data['forecast_data'][0]['lower_bound_95']} -> {data['forecast_data'][0]['upper_bound_95']}]")
else:
    print(f"[-] Lỗi {res.status_code}: {res.text}")