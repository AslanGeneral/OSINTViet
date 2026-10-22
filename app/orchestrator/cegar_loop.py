# ============================================================
# app/orchestrator/cegar_loop.py
# CEGARRepairEngine — bản nâng cấp [DT §6.5 / mục 26]:
# - Bỏ hoàn toàn biểu thức kiểm tra chuỗi cứng theo lĩnh vực năng lượng
#   -> dùng hợp đồng JSON Assertions & Metrics (computed_metrics + bounds)
# - Attempt 1 fail -> Attempt 2 đổi model + nhồi stderr lỗi Attempt 1 vào System Prompt
# - Thất bại quá 2 lần -> DEAD_BRANCH + ghi Negative Memory
# ============================================================
import json
from typing import Dict, Any, List, Optional

from app.schemas.tournament_schema import Hypothesis
from app.schemas.universal_orchestrator_schema import MetricBoundSpec, TaskOntologySchema
from app.orchestrator.tournament import TournamentDAGOrchestrator, extract_clean_json
from app.sandbox.runner import sandbox_runner
from app.sandbox.attestation import sanitize_and_attest_facts, attest_sandbox_result
from app.logic_engine.problog_scanner import problog_scanner

VI_LANGUAGE_TAIL = (
    "Analyze and reason structurally, keep all JSON keys and Prolog predicates "
    "in English snake_case, but write all domain analysis, search queries, "
    "and explanations in Vietnamese."
)


def _verify_bounds(stdout_str: str, bounds: List[MetricBoundSpec]) -> bool:
    """Đọc đúng ngữ nghĩa kiểm biên của §10 (_verify_universal_bounds) cho luồng CEGAR."""
    try:
        payload = json.loads(stdout_str.strip().splitlines()[-1])
        metrics = payload.get("computed_metrics", {})
        for b in bounds:
            if b.metric_name not in metrics:
                return False
            val = float(metrics[b.metric_name])
            if b.operator == "<=" and not (val <= b.threshold_value):
                return False
            if b.operator == ">=" and not (val >= b.threshold_value):
                return False
            if b.operator == "==" and not (abs(val - b.threshold_value) < 1e-5):
                return False
            if b.operator == "in_range":
                if b.upper_bound is None or not (b.threshold_value <= val <= b.upper_bound):
                    return False
        return True
    except Exception:
        return False


class CEGARRepairEngine:
    def __init__(self, orchestrator: TournamentDAGOrchestrator):
        self.orchestrator = orchestrator

    async def repair_hypothesis(
        self,
        hyp: Hypothesis,
        conflict_trace: List[str],
        parameter_bounds: Optional[List[MetricBoundSpec]] = None,
        ontology: Optional[TaskOntologySchema] = None,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        print(f"\n[CEGAR REPAIR] Khởi động quy trình tự chữa lành cho '{hyp.id}'...")
        bounds = parameter_bounds or []

        repair_ticket = {
            "hypothesis_id": hyp.id,
            "conflict_trace": conflict_trace,
            "metric_bounds": [b.model_dump() for b in bounds],
            "sandbox_contract": {
                "stdout_last_line_json": {
                    "computed_metrics": {"metric_name": "float"},
                    "passed": True,
                    "all_bounds_satisfied": True,
                }
            },
        }

        system_prompt = (
            "You are an Elite CEGAR Self-Healing Engineer. "
            "Resolve every logic conflict found by the Verifier and satisfy all MetricBoundSpec. "
            "Write Python code (standard library only) that simulates the repaired mechanism and "
            "prints, as the last stdout line, the JSON contract from the RepairTicket. "
            "You MUST output valid JSON with fields: "
            "'repaired_mechanism_vi', 'updated_facts', 'python_simulation_code'. "
            f"{VI_LANGUAGE_TAIL}"
        )

        models = list(getattr(self.orchestrator, "candidate_models", []))
        first_model = self.orchestrator._pick_model("sandbox_worker") if models else None

        stderr_trace: List[str] = []
        for attempt in range(1, max_attempts + 1):
            # Attempt 2: đổi model + inject stderr của Attempt 1 vào System Prompt
            preferred_model = first_model
            if attempt > 1:
                alternatives = [m for m in models if m != first_model]
                preferred_model = alternatives[0] if alternatives else first_model
            current_system = system_prompt
            if attempt > 1:
                current_system = (
                    f"{system_prompt}\n\n[RECURSIVE DEBUG — STDERR ATTEMPT {attempt - 1}]\n"
                    f"{json.dumps(stderr_trace, ensure_ascii=False)}"
                )

            user_prompt = (
                f"REPAIR_TICKET: {json.dumps(repair_ticket, ensure_ascii=False)}\n"
                f"HYPOTHESIS: {hyp.model_dump_json()}\n"
                f"ATTEMPT {attempt}/{max_attempts}."
            )

            raw = await self.orchestrator._call_llm_with_retry(
                current_system, user_prompt,
                role="sandbox_worker", preferred_model=preferred_model,
            )
            data = extract_clean_json(raw)
            repaired_mech = data.get("repaired_mechanism_vi", hyp.mechanism)
            python_code = data.get("python_simulation_code", "")

            if not python_code:
                stderr_trace.append("Worker không trả về python_simulation_code")
                continue

            print("  -> Worker đã nộp bản sửa đổi và code thực nghiệm. Đang đưa vào Sandbox...")
            exec_res = await sandbox_runner.run_code(hyp.id, hyp.id, python_code)
            bounds_ok = _verify_bounds(exec_res.get("stdout", ""), bounds)
            success = exec_res.get("exit_code") == 0 and bounds_ok

            if not success:
                print(f"  [SANDBOX FAILED] {exec_res.get('stderr', '')[:120]}")
                stderr_trace.append(json.dumps({
                    "attempt": attempt,
                    "stderr": exec_res.get("stderr", "")[:2000],
                    "computed_metrics": exec_res.get("computed_metrics", {}),
                    "metric_bounds": [b.model_dump() for b in bounds],
                }, ensure_ascii=False))
                continue

            # Cấp HMAC-SHA256 receipt + đóng dấu verified_* = 0.95
            attestation = attest_sandbox_result(
                hyp.id, exec_res.get("computed_metrics", {}), bounds_ok, exec_res.get("stdout", "")
            )
            clean_facts = sanitize_and_attest_facts(data.get("updated_facts", {}), valid_receipt=False)
            hyp.facts = {**clean_facts, **attestation["verified_facts"]}
            hyp.mechanism = repaired_mech

            rescan = problog_scanner.scan_hypothesis(hyp.id, hyp.facts, ontology)
            print(f"  -> Điểm sau khi sửa: Viability={rescan.get('viability_score')} | "
                  f"Conflict={rescan.get('conflict_penalty')} | Net={rescan.get('net_fuzzy_score')}")

            if rescan.get("status") == "PASSED" and rescan.get("conflict_penalty", 1.0) < 0.20:
                print("[CEGAR REPAIRED] Giả thuyết đã tự chữa lành thành công!")
                await sandbox_runner.cleanup(hyp.id, hyp.id)
                return {
                    "success": True,
                    "repaired_hypothesis": hyp,
                    "final_score": rescan,
                    "sandbox_certificate": {
                        "receipt": attestation["receipt"],
                        "computed_metrics": exec_res.get("computed_metrics", {}),
                    },
                }

        print("[CIRCUIT BREAKER] Quá 2 lần sửa chữa thất bại. Đánh dấu DEAD_BRANCH!")
        hyp.status = "DEAD_BRANCH"
        await sandbox_runner.cleanup(hyp.id, hyp.id)
        return {"success": False, "hypothesis": hyp, "stderr_trace": stderr_trace}
