# ============================================================
# app/logic_engine/problog_scanner.py
# DynamicProbLogScanner [DT §6.3 / mục 17]:
# - KHÔNG dùng bộ rule tĩnh trên disk (ontology.py cũ bị khử cứng — mục 22):
#   scanner nhận TaskOntologySchema động của phiên (từ SharedBlackboard).
# - Nạp thêm "Bộ Logic Đối Nghịch Lịch Sử" từ verifier_compiled_memory.json,
#   lọc theo domain_context_tag (NegativeMemoryCompiler biên dịch).
# - Chạy ProcessPoolExecutor, timeout = 2.0s.
# - Queries: viable(H_k), has_conflict(H_k).
#   Net_Fuzzy_Score = P(viable) * (1 - P(has_conflict))  [DT §6.3.4]
# ============================================================
import os
import re
import json
import concurrent.futures
from typing import Dict, Any, List, Optional
from problog.program import PrologString
from problog import get_evaluatable
from app.schemas.universal_orchestrator_schema import TaskOntologySchema, DynamicRuleSpec

# File "bộ logic đối nghịch lịch sử" (cross-project compiled logic)
COMPILED_MEMORY_PATH = os.getenv(
    "VERIFIER_COMPILED_MEMORY_PATH", "./verifier_compiled_memory.json"
)


def sanitize_prolog_atom(text: str) -> str:
    """Nén chuỗi về chuẩn Prolog Atom: chỉ chữ thường, số và dấu gạch dưới, bắt đầu bằng chữ cái"""
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', str(text).strip().lower())
    # Bắt buộc bắt đầu bằng chữ cái
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "p_" + cleaned
    # Rút ngắn nếu quá dài
    cleaned = cleaned[:40].strip('_')
    return cleaned or "p_unknown"


def _problog_worker(program_str: str) -> Dict[str, Any]:
    try:
        model = PrologString(program_str)
        evaluatable = get_evaluatable()
        result = evaluatable.create_from(model).evaluate()
        return {str(k): round(float(v), 4) for k, v in result.items()}
    except Exception as e:
        return {"error": str(e), "failed_program": program_str}


def _load_compiled_memory(domain_tag: str) -> List[DynamicRuleSpec]:
    """
    Nạp "Bộ Logic Đối Nghịch Lịch Sử" từ verifier_compiled_memory.json,
    lọc theo domain_context_tag == domain_tag [DT §6.3.3 / mục 17].
    """
    if not os.path.exists(COMPILED_MEMORY_PATH):
        return []
    try:
        with open(COMPILED_MEMORY_PATH, "r", encoding="utf-8") as f:
            store = json.load(f)
        rules: List[DynamicRuleSpec] = []
        for entry in store.get(domain_tag, []):
            # ProbLog rule weight phải nằm trong [0.01, 0.99]; dữ liệu cũ có thể
            # vượt biên và làm Pydantic ném lỗi khiến toàn bộ memory bị bỏ.
            weight = max(0.01, min(0.99, float(entry.get("weight", 0.99))))
            rules.append(DynamicRuleSpec(
                rule_id=entry.get("rule_id", "compiled_rule"),
                weight=weight,
                conditions=list(entry.get("conditions", [])),
                description_vi=entry.get("description_vi", ""),
            ))
        return rules
    except Exception as e:
        print(f"[PROLOG] Lỗi đọc compiled memory, bỏ qua: {e}")
        return []


class DynamicProbLogScanner:
    def __init__(self, max_cpu_workers: int = 2):
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_cpu_workers)

    def scan_hypothesis_dynamic(
        self,
        hypothesis_id: str,
        extracted_facts: Dict[str, float],
        ontology: TaskOntologySchema,
    ) -> Dict[str, Any]:
        """
        [DT §6.3 / mục 17] Scanner với tập luật động:
        - events: các fact đã qua filter sanitize_and_attest_facts ở Stage 4.
        - luật khả thi + luật mâu thuẫn: do Theoretical Critic & Supervisor lập cho bài toán.
        - luật phạt lịch sử: nạp từ compiled memory theo domain_tag.
        """
        safe_hyp_id = sanitize_prolog_atom(hypothesis_id)
        prolog_lines: List[str] = []

        # 1. Chỉ nhận fact có vị từ thuộc tập hợp lệ động của phiên.
        #    Chuẩn hóa cả ontology để không lệch do hoa/thường/ký tự lạ.
        allowed = {sanitize_prolog_atom(p) for p in ontology.allowed_predicates}
        full_facts: Dict[str, float] = {}
        for raw_pred, score in (extracted_facts or {}).items():
            clean_pred = sanitize_prolog_atom(raw_pred)
            if clean_pred in allowed:
                try:
                    safe_val = max(0.001, min(0.999, float(score)))
                    full_facts[clean_pred] = safe_val
                except (TypeError, ValueError):
                    continue
            # LLM tự bịa vị từ ngoài dynamic Ontology -> bỏ qua, không nạp

        # 1b. Các vị từ xuất hiện trong luật nhưng thiếu fact -> nạp prior 0.001
        rule_pred_names: List[str] = []
        for rule in list(ontology.positive_rules) + list(ontology.conflict_rules):
            rule_pred_names.extend([sanitize_prolog_atom(c) for c in rule.conditions])
        for pred in sorted(set(rule_pred_names)):
            if pred not in full_facts:
                full_facts[pred] = 0.001

        # 2. Xuất các dòng facts chuẩn ProbLog (không chữ lạ, không viết hoa)
        for pred, score in full_facts.items():
            prolog_lines.append(f"{score}::{pred}({safe_hyp_id}).")

        # 3. Khai báo các luật khả thi (viability rules) — tập động cho bài toán
        for idx, rule in enumerate(ontology.positive_rules):
            conds = ", ".join([f"{sanitize_prolog_atom(c)}({safe_hyp_id})" for c in rule.conditions])
            if not conds:
                continue
            prolog_lines.append(f"{rule.weight}::viable_clause_{idx}({safe_hyp_id}) :- {conds}.")
            prolog_lines.append(f"viable({safe_hyp_id}) :- viable_clause_{idx}({safe_hyp_id}).")

        # 4. Khai báo các luật mâu thuẫn (conflict rules — tập động)
        #    + luật phạt lịch sử (compiled memory cùng domain_tag)
        conflict_rules = list(ontology.conflict_rules) + _load_compiled_memory(ontology.domain_tag)
        conflict_queries: List[tuple] = []
        for idx, c_rule in enumerate(conflict_rules):
            conds = ", ".join([f"{sanitize_prolog_atom(c)}({safe_hyp_id})" for c in c_rule.conditions])
            if not conds:
                continue
            c_pred = f"conflict_{idx}({safe_hyp_id})"
            prolog_lines.append(f"{c_rule.weight}::{c_pred} :- {conds}.")
            prolog_lines.append(f"has_conflict({safe_hyp_id}) :- {c_pred}.")
            conflict_queries.append((c_pred, c_rule.description_vi))

        # 5. Queries — các proposition tính xác suất [DT §6.3.4]
        #    Chỉ query predicate đã có định nghĩa để tránh lỗi "No clauses found".
        if ontology.positive_rules:
            prolog_lines.append(f"query(viable({safe_hyp_id})).")
        if conflict_queries:
            prolog_lines.append(f"query(has_conflict({safe_hyp_id})).")
            for c_pred, _ in conflict_queries:
                prolog_lines.append(f"query({c_pred}).")

        program_str = "\n".join(prolog_lines)

        # 6. Chạy suy diễn qua một Process riêng, timeout 2.0s [mục 17]
        future = self.executor.submit(_problog_worker, program_str)
        try:
            raw_scores = future.result(timeout=2.0)
        except concurrent.futures.TimeoutError:
            return {
                "hypothesis_id": safe_hyp_id,
                "status": "TIMEOUT",
                "error": "ProbLog suy diễn vượt quá 2.0s!",
                "net_fuzzy_score": 0.0,
                "conflict_trace": [],
            }

        if "error" in raw_scores:
            return {
                "hypothesis_id": safe_hyp_id,
                "status": "SYNTAX_ERROR",
                "error": raw_scores["error"],
                "net_fuzzy_score": 0.0,
                "conflict_trace": [],
            }

        # 7. Tính Net_Fuzzy_Score = P(viable) * (1 - P(has_conflict)) [DT §6.3.4]
        viable_score = raw_scores.get(f"viable({safe_hyp_id})", 0.0)
        conflict_prob = raw_scores.get(f"has_conflict({safe_hyp_id})", 0.0)
        net_score = round(viable_score * (1.0 - conflict_prob), 4)

        active_conflicts = []
        for c_pred, reason in conflict_queries:
            c_val = raw_scores.get(c_pred, 0.0)
            if c_val > 0.25:
                active_conflicts.append(f"{reason} (Xác suất vi phạm: {int(c_val * 100)}%)")

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
            "conflict_trace": active_conflicts,
            "domain_tag": ontology.domain_tag,
        }

    def scan_hypothesis(
        self, hypothesis_id: str, facts: Dict[str, float],
        ontology: Optional[TaskOntologySchema] = None,
    ) -> Dict[str, Any]:
        """
        Giao diện tương thích cho luồng cũ (cegar_loop, tournament):
        nếu không có Ontology động nào được truyền -> dựng ontology tối thiểu
        từ chính các vị ngữ có trong facts của phiên (Dynamic Ontology tối giản).
        """
        if ontology is None:
            preds = [sanitize_prolog_atom(p) for p in (facts or {})]
            ontology = TaskOntologySchema(
                domain_tag="session_free_form",
                allowed_predicates=preds,
                positive_rules=[(
                    DynamicRuleSpec(
                        rule_id="passthrough_viable",
                        weight=0.99,
                        conditions=preds[:1],
                        description_vi="Chấp nhận tối thiểu vì phiên này chưa induction luật động.",
                    )
                ) if preds else DynamicRuleSpec(
                    rule_id="empty_viable",
                    weight=0.01,
                    conditions=["passthrough_placeholder"],
                    description_vi="Không có fact nào trong phiên.",
                )],
                conflict_rules=[],
            )
        clamped = {
            sanitize_prolog_atom(p): max(0.001, min(0.999, float(v)))
            for p, v in (facts or {}).items()
            if isinstance(v, (int, float))
        }
        return self.scan_hypothesis_dynamic(hypothesis_id, clamped, ontology)

    def close(self):
        self.executor.shutdown(wait=False)


problog_scanner = DynamicProbLogScanner()
