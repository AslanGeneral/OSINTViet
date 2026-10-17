import pandas as pd
from statsmodels.tsa.stattools import adfuller

def find_differencing_order(series: pd.Series, max_d: int = 2) -> int:
    """
    Sử dụng Augmented Dickey-Fuller (ADF) test để tìm bậc sai phân d tối thiểu
    giúp chuỗi thời gian đạt tính dừng (stationary) ở mức ý nghĩa 5%.
    """
    clean_series = series.dropna()
    d = 0
    
    while d <= max_d:
        # H0: Chuỗi có nghiệm đơn vị (Non-stationary)
        # H1: Chuỗi dừng (Stationary)
        result = adfuller(clean_series)
        p_value = result[1]
        
        if p_value < 0.05:
            return d
        
        # Lấy sai phân bậc tiếp theo
        clean_series = clean_series.diff().dropna()
        d += 1
        
    return max_d  # Fallback nếu vượt quá max_d