from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class Hypothesis(BaseModel):
    id: str
    generation: int
    title: str
    mechanism: str = Field(description="Cơ chế chi tiết giải quyết bài toán")
    assumptions: List[str] = Field(default_factory=list, description="Các giả định ngầm")
    facts: Dict[str, float] = Field(
        default_factory=dict, 
        description="Các vị từ và trọng số [0.0 - 1.0] chuẩn bị nạp vào ProbLog ở Khối 2"
    )
    parent_ids: List[str] = Field(default_factory=list)
    elo_rating: float = 1200.0  # Điểm Elo khởi tạo mặc định
    net_fuzzy_score: float = 0.0  # Điểm Fuzzy Prolog (Net_Fuzzy_Score) của phiên
    critiques: List[str] = Field(default_factory=list)
    status: str = "ALIVE"       # ALIVE | DEAD | EVOLVED

class CritiqueResult(BaseModel):
    # ĐẶT OPTIONAL ĐỂ KHÔNG BỊ CRASH KHI LLM KHÔNG TRẢ VỀ ID
    hypothesis_id: Optional[str] = None
    flaws: List[str] = Field(default_factory=list, description="Các điểm yếu chí mạng, lỗ hổng logic")
    bottlenecks: List[str] = Field(default_factory=list, description="Nghẽn cổ chai về hiệu năng hoặc chi phí")
    suggested_verification: str = Field(default="", description="Thực nghiệm cần làm để chứng minh")

class MatchBattleResult(BaseModel):
    winner_id: str = Field(default="", description="ID phương án thắng")
    loser_id: str = Field(default="", description="ID phương án thua")
    rationale: str = Field(default="", description="Lý do phân xử")
