# ============================================================
# app/sandbox/runner.py
# UniversalParameterForcingSandbox [DT §6.5 / mục 18]:
# - Chạy code Python trong tiến trình con cô lập bằng asyncio.create_subprocess_exec
# - Thư mục riêng: /tmp/sandbox/{task_id}/{hyp_id}/
# - Timeout 8.0 giây
# - Code phải in ra dòng cuối là JSON chuẩn:
#     {"computed_metrics": {...}, "passed": true, "all_bounds_satisfied": true}
#   (nhận cả hai khóa passed / all_bounds_satisfied — Appendix A hàng 12)
# ============================================================
import os
import sys
import json
import uuid
import shutil
import asyncio
from typing import Dict, Any, Optional

SANDBOX_ROOT = "/tmp/sandbox"
SANDBOX_TIMEOUT_SECONDS = 8.0


class UniversalParameterForcingSandbox:
    def __init__(self, timeout_seconds: float = SANDBOX_TIMEOUT_SECONDS):
        self.timeout = timeout_seconds

    async def run_code(self, task_id: str, hypothesis_id: str, code_snippet: str) -> Dict[str, Any]:
        """
        Ghi code vào thư mục sandbox riêng và thực thi bất đồng bộ.
        Trả về exit_code / stdout / stderr / parsed payload / computed_metrics.
        (Receipt HMAC do attestation.py cấp sau khi pipeline kiểm biên thành công.)
        """
        work_dir = os.path.join(SANDBOX_ROOT, str(task_id), str(hypothesis_id))
        os.makedirs(work_dir, exist_ok=True)
        script_path = os.path.join(work_dir, f"simulation_{uuid.uuid4().hex[:8]}.py")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code_snippet)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env={
                    # Môi trường tối thiểu — không kế thừa toàn bộ biến của orchestrator
                    "PATH": os.getenv("PATH", "/usr/bin:/bin"),
                    "PYTHONIOENCODING": "utf-8",
                },
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            stdout = stdout_b.decode("utf-8", errors="replace").strip()
            stderr = stderr_b.decode("utf-8", errors="replace").strip()

            payload: Optional[Dict[str, Any]] = None
            try:
                payload = json.loads(stdout.strip().splitlines()[-1])
            except Exception:
                payload = None

            # Receipt HMAC chỉ được sinh ở attestation.py SAU khi tầng kiểm tra
            # biên (_verify_universal_bounds) xác nhận đạt — không cấp ở đây.
            receipt = None

            return {
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "payload": payload,
                "computed_metrics": (payload or {}).get("computed_metrics", {}),
                "receipt": receipt,
                "verified": proc.returncode == 0,
                "work_dir": work_dir,
            }
        except asyncio.TimeoutError:
            if proc is not None:
                proc.kill()
                await proc.wait()
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Code chạy quá thời gian cho phép ({self.timeout}s)!",
                "payload": None,
                "computed_metrics": {},
                "receipt": None,
                "verified": False,
                "work_dir": work_dir,
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "payload": None,
                "computed_metrics": {},
                "receipt": None,
                "verified": False,
                "work_dir": work_dir,
            }

    async def cleanup(self, task_id: str, hypothesis_id: str):
        """Dọn thư mục sandbox sau khi kết thúc nhánh (giữ tùy chọn gỡ lỗi)."""
        work_dir = os.path.join(SANDBOX_ROOT, str(task_id), str(hypothesis_id))
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


sandbox_runner = UniversalParameterForcingSandbox()
