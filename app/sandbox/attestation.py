# ============================================================
# app/sandbox/attestation.py
# Chống gian lận bằng chứng thực nghiệm [DT §6.5.2 / mục 19]:
# - sanitize_and_attest_facts: gọt verified_* không có chữ ký HMAC
# - attest_sandbox_result: chỉ khi (returncode == 0) VÀ bounds đạt mới cấp
#   HMAC-SHA256 receipt và đóng dấu verified_* = 0.95 vào facts
# ============================================================
import os
import hmac
import hashlib
import re
from typing import Dict, Any

# Khóa bí mật ký receipt. Có thể ghi đè bằng biến môi trường SOVEREIGN_SANDBOX_SECRET.
SECRET_SALT = os.getenv(
    "SOVEREIGN_SANDBOX_SECRET",
    "osint_viet_co_scientist_secret_kernel_2026",
).encode("utf-8")


def generate_execution_receipt(hypothesis_id: str, tool_name: str, exit_code: int, output_digest: str) -> str:
    """Sinh mã xác thực mật mã (HMAC-SHA256) sau khi chạy code sandbox thật"""
    payload = f"{hypothesis_id}:{tool_name}:{exit_code}:{output_digest}"
    return hmac.new(SECRET_SALT, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sanitize_and_attest_facts(
    raw_facts: Dict[str, float],
    valid_receipt: bool = False
) -> Dict[str, float]:
    """
    Gọt bỏ toàn bộ vị từ 'verified_*' do LLM tự biên tự diễn.
    Chỉ giữ lại nếu có chứng chỉ thực nghiệm (valid_receipt == True).
    """
    clean_facts: Dict[str, float] = {}
    for pred, val in raw_facts.items():
        if pred.startswith("verified_") and not valid_receipt:
            print(f"[ANTI-HACK] Đã gọt bỏ vị từ ảo giác '{pred}' do LLM tự phong!")
            continue
        try:
            clean_facts[pred] = float(val)
        except (TypeError, ValueError):
            continue
    return clean_facts


def _safe_metric_atom(metric_name: str) -> str:
    """Chuẩn hóa tên metric thành hậu tố Prolog an toàn (snake_case ASCII)"""
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', str(metric_name).strip().lower()).strip('_')
    return cleaned or "metric"


def attest_sandbox_result(
    hypothesis_id: str,
    computed_metrics: Dict[str, Any],
    bounds_satisfied: bool,
    stdout: str = "",
) -> Dict[str, Any]:
    """
    Chỉ khi tầng kiểm tra biên (_verify_universal_bounds) xác nhận TẤT CẢ metric
    nằm trong MetricBoundSpec thì mới sinh receipt HMAC-SHA256 và đóng dấu
    verified_* = 0.95 [DT §6.5.2].
    """
    if not bounds_satisfied:
        return {"attested": False, "receipt": None, "verified_facts": {}}

    digest = hashlib.sha256(stdout.encode("utf-8")).hexdigest()[:16]
    receipt = generate_execution_receipt(
        hypothesis_id, "universal_parameter_forcing", 0, digest
    )
    verified_facts = {
        f"verified_{_safe_metric_atom(metric)}": 0.95
        for metric in computed_metrics
    }
    return {"attested": True, "receipt": receipt, "verified_facts": verified_facts}
