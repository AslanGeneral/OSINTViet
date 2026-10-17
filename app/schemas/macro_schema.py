from pydantic import BaseModel, Field
from typing import List

class HouseholdImpact(BaseModel):
    category: str = Field(description="Mặt hàng bị ảnh hưởng: Xăng dầu, Học phí, Lương, v.v.")
    expected_change_pct: float = Field(description="Dự báo tỷ lệ tăng/giảm giá (%)")
    layman_explanation: str = Field(description="Giải thích bằng ngôn ngữ cấp 2 cực kỳ dễ hiểu")

class MacroBreakdownResponse(BaseModel):
    article_title: str
    original_macro_thesis: str = Field(description="Tóm tắt ngắn gọn lập luận vĩ mô cốt lõi")
    layman_summary: str = Field(description="Bản dịch dễ hiểu như giải thích cho một đứa trẻ 13 tuổi")
    micro_impacts: List[HouseholdImpact]
    recommended_actions: List[str] = Field(description="3 hành động cụ thể hộ gia đình nên làm ngay")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Độ tin cậy của phân tích")