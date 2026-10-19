import hmac
import hashlib
import json
from typing import Dict

SECRET_SALT = b"osint_viet_co_scientist_secret_kernel_2026"

def generate_execution_receipt(hypothesis_id: str, tool_name: str, exit_code: int, output_digest: str) -> str:
    """Sinh mã xác thực mật mã sau khi chạy code sandbox thật"""
    payload = f"{hypothesis_id}:{tool_name}:{exit_code}:{output_digest}"
    return hmac.new(SECRET_SALT, payload.encode(), hashlib.sha256).hexdigest()

def sanitize_and_attest_facts(
    raw_facts: Dict[str, float], 
    valid_receipt: bool = False
) -> Dict[str, float]:
    """
    Gọt bỏ toàn bộ vị từ 'verified_*' do LLM tự biên tự diễn.
    Chỉ giữ lại nếu có chứng chỉ thực nghiệm (valid_receipt == True).
    """
    clean_facts = {}
    for pred, val in raw_facts.items():
        if pred.startswith("verified_") and not valid_receipt:
            print(f"[\033[91mANTI-HACK\033[0m] Đã gọt bỏ vị từ ảo giác '{pred}' do LLM tự phong!")
            continue
        clean_facts[pred] = float(val)
    return clean_facts