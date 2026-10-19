import re
import concurrent.futures
from typing import Dict, Any, List
from problog.program import PrologString
from problog import get_evaluatable
from app.logic_engine.ontology import DOMAIN_POSITIVE_RULES, DOMAIN_CONFLICT_RULES, ALLOWED_PREDICATES

def sanitize_prolog_atom(text: str) -> str:
    """Ép chuỗi về chuẩn Prolog Atom: chỉ chữ thường, số và dấu gạch dưới, bắt đầu bằng chữ cái"""
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', text.strip().lower())
    # Bắt buộc bắt đầu bằng chữ cái thường
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "p_" + cleaned
    # Rút ngắn nếu quá dài
    return cleaned[:40].strip('_')

def _problog_worker(program_str: str) -> Dict[str, Any]:
    try:
        model = PrologString(program_str)
        evaluatable = get_evaluatable()
        result = evaluatable.create_from(model).evaluate()
        return {str(k): round(float(v), 4) for k, v in result.items()}
    except Exception as e:
        return {"error": str(e), "failed_program": program_str}

class ProbLogNeuroSymbolicScanner:
    def __init__(self, max_cpu_workers: int = 2):
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_cpu_workers)

    def scan_hypothesis(
        self, 
        hypothesis_id: str, 
        facts: Dict[str, float]
    ) -> Dict[str, Any]:
        
        # Làm sạch ID của giả thuyết (phải là Prolog atom)
        safe_hyp_id = sanitize_prolog_atom(hypothesis_id)
        prolog_lines = []

        # 1. KHỞI TẠO TẤT CẢ VỊ TỪ TRONG ONTOLOGY VỚI XÁC SUẤT MẶC ĐỊNH (PRIOR = 0.001)
        full_facts = {pred: 0.001 for pred in ALLOWED_PREDICATES}

        # 2. CHỈ NẠP NHỮNG FACTS NÀO THỰC SỰ HỢP LỆ VÀ KHỚP VỚI ONTOLOGY
        for raw_pred, score in facts.items():
            clean_pred = sanitize_prolog_atom(raw_pred)
            # Nếu vị từ này nằm trong Ontology chuẩn
            if clean_pred in ALLOWED_PREDICATES:
                safe_val = max(0.001, min(0.999, float(score)))
                full_facts[clean_pred] = safe_val
            else:
                # Nếu LLM chém gió ra câu văn lạ ngoài Ontology -> BỎ QUA, không cho phá hoại Prolog!
                pass

        # 3. XUẤT CÁC DÒNG FACTS CHUẨN PROBLOG (KHÔNG CÓ KÝ TỰ LẠ, KHÔNG VIẾT HOA)
        for pred, score in full_facts.items():
            prolog_lines.append(f"{score}::{pred}({safe_hyp_id}).")

        # 4. KHAI BÁO CÁC LUẬT KHẢ THI (VIABILITY RULES)
        for idx, rule in enumerate(DOMAIN_POSITIVE_RULES):
            w = rule["weight"]
            conds = ", ".join([f"{c}({safe_hyp_id})" for c in rule["conditions"]])
            prolog_lines.append(f"{w}::viable_clause_{idx}({safe_hyp_id}) :- {conds}.")
            prolog_lines.append(f"viable({safe_hyp_id}) :- viable_clause_{idx}({safe_hyp_id}).")

        # 5. KHAI BÁO CÁC LUẬT MÂU THUẪN (CONFLICT RULES)
        conflict_queries = []
        for idx, c_rule in enumerate(DOMAIN_CONFLICT_RULES):
            sev = c_rule["severity"]
            conds = ", ".join([f"{c}({safe_hyp_id})" for c in c_rule["conditions"]])
            c_pred = f"conflict_{idx}({safe_hyp_id})"
            prolog_lines.append(f"{sev}::{c_pred} :- {conds}.")
            prolog_lines.append(f"has_conflict({safe_hyp_id}) :- {c_pred}.")
            conflict_queries.append((c_pred, c_rule["reason"]))

        # 6. QUERIES
        prolog_lines.append(f"query(viable({safe_hyp_id})).")
        prolog_lines.append(f"query(has_conflict({safe_hyp_id})).")
        for c_pred, _ in conflict_queries:
            prolog_lines.append(f"query({c_pred}).")

        program_str = "\n".join(prolog_lines)

        # 7. CHẠY SUY DIỄN QUA PROCESSSPOOL
        future = self.executor.submit(_problog_worker, program_str)
        try:
            raw_scores = future.result(timeout=2.0)
        except concurrent.futures.TimeoutError:
            return {
                "hypothesis_id": safe_hyp_id,
                "status": "TIMEOUT",
                "error": "ProbLog compilation bùng nổ tổ hợp quá 2.0s!"
            }

        if "error" in raw_scores:
            return {
                "hypothesis_id": safe_hyp_id, 
                "status": "SYNTAX_ERROR", 
                "error": raw_scores["error"]
            }

        # 8. TÍNH TOÁN CHỈ SỐ NET SCORE
        viable_score = raw_scores.get(f"viable({safe_hyp_id})", 0.0)
        conflict_prob = raw_scores.get(f"has_conflict({safe_hyp_id})", 0.0)
        
        # Net Score = Viability * (1 - Conflict)
        net_score = round(viable_score * (1.0 - conflict_prob), 4)

        active_conflicts = []
        for c_pred, reason in conflict_queries:
            c_val = raw_scores.get(c_pred, 0.0)
            if c_val > 0.25:
                active_conflicts.append(f"{reason} (Xác suất vi phạm: {int(c_val*100)}%)")

        status = "PASSED"
        if conflict_prob > 0.65:
            status = "NEEDS_REPAIR"
        elif net_score < 0.20:
            status = "REJECTED"

        return {
            "hypothesis_id": safe_hyp_id,
            "status": status,
            "net_fuzzy_score": net_score,
            "viability_score": viable_score,
            "conflict_penalty": conflict_prob,
            "conflict_trace": active_conflicts
        }

    def close(self):
        self.executor.shutdown(wait=False)

problog_scanner = ProbLogNeuroSymbolicScanner()
