import sys
import subprocess
import tempfile
import os
import hashlib
from typing import Dict, Any
from app.sandbox.attestation import generate_execution_receipt

class LocalPythonSandbox:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout = timeout_seconds

    def execute_code(self, hypothesis_id: str, code_snippet: str) -> Dict[str, Any]:
        """
        Ghi code vào file tạm và thực thi trong Subprocess cô lập.
        Nếu chạy thành công (exit_code == 0), cấp mã HMAC-SHA256 Receipt!
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code_snippet)
            temp_path = f.name

        try:
            # Chạy code Python với interpreter hiện tại của .venv
            proc = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            exit_code = proc.returncode
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            receipt = None
            if exit_code == 0:
                # Sinh chữ ký mật mã bảo chứng kết quả chạy thật
                digest = hashlib.sha256(stdout.encode()).hexdigest()[:16]
                receipt = generate_execution_receipt(hypothesis_id, "python_sandbox", exit_code, digest)

            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "receipt": receipt,
                "verified": exit_code == 0
            }

        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Code chạy quá thời gian cho phép ({self.timeout}s)!",
                "receipt": None,
                "verified": False
            }
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

sandbox_runner = LocalPythonSandbox()