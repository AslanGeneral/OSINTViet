from typing import List, Dict, Any

# Bộ từ vựng vị từ chuẩn (Ontology)
ALLOWED_PREDICATES = [
    "decentralized_routing",
    "realtime_telemetry",
    "low_capex_under_50b",
    "high_infrastructure_cost",
    "latency_under_6h",
    "requires_manual_approval",
    "uses_existing_infra",
    "requires_nationwide_hardware_overhaul",
    "verified_load_balance_sim"  # Vị từ đặc biệt chỉ cấp khi chạy sandbox thành công
]

# Luật khả thi (Positive Rules): Càng đáp ứng nhiều thì Viability càng cao
DOMAIN_POSITIVE_RULES = [
    {
        "name": "viable_fast_deployment",
        "weight": 0.90,
        "conditions": ["uses_existing_infra", "low_capex_under_50b"]
    },
    {
        "name": "viable_resilience",
        "weight": 0.85,
        "conditions": ["decentralized_routing", "realtime_telemetry"]
    }
]

# Luật mâu thuẫn (Conflict Rules): Bắt lỗi ảo giác logic - Phạt cực nặng!
DOMAIN_CONFLICT_RULES = [
    {
        "reason": "Vượt ngân sách: Đòi chi phí thấp nhưng lại đại tu toàn bộ phần cứng toàn quốc",
        "severity": 0.95,
        "conditions": ["low_capex_under_50b", "requires_nationwide_hardware_overhaul"]
    },
    {
        "reason": "Nghẽn độ trễ: Đòi phản ứng dưới 6 giờ nhưng quy trình lại bắt phê duyệt thủ công",
        "severity": 0.90,
        "conditions": ["latency_under_6h", "requires_manual_approval"]
    }
]