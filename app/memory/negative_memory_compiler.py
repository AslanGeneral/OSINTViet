# ============================================================
# app/memory/negative_memory_compiler.py
# NegativeMemoryCompiler [DT §6.5.3 / mục 20]:
# Nén các bài học lỗi trong Short-Term DB (SharedBlackboard.negative_constraints)
# thành luật phạt ProbLog gắn domain_context_tag, nhân hệ số decay,
# lưu vào verifier_compiled_memory.json để các phiên cùng domain tự trừ điểm.
# ============================================================
import os
import json
import hashlib
from typing import Dict, Any, List
from app.core.config import settings

COMPILED_MEMORY_PATH = os.getenv(
    "VERIFIER_COMPILED_MEMORY_PATH", "./verifier_compiled_memory.json"
)


class NegativeMemoryCompiler:
    def __init__(self, decay_factor: float = None):
        self.decay_factor = (
            settings.NEGATIVE_MEMORY_DECAY_FACTOR
            if decay_factor is None else decay_factor
        )

    def _load_store(self) -> Dict[str, List[Dict[str, Any]]]:
        if not os.path.exists(COMPILED_MEMORY_PATH):
            return {}
        try:
            with open(COMPILED_MEMORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_store(self, store: Dict[str, List[Dict[str, Any]]]):
        with open(COMPILED_MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    def compile_to_long_term(self, domain_tag: str, negative_constraints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        [DT §6.5.3] Nén lesson ngắn hạn thành luật phạt dài hạn.
        Mỗi constraint cần: constraint_id, conflict_predicates (list), lesson_vi.
        Trọng số luật phạt = base_weight * decay_factor (giới hạn [0.01, 0.99]).
        Trùng constraint_id thì giữ luật mạnh hơn (không nhân decay nhiều lần).
        """
        store = self._load_store()
        bucket = store.setdefault(domain_tag, [])
        existing = {r.get("rule_id"): r for r in bucket}

        compiled_now: List[Dict[str, Any]] = []
        for c in negative_constraints:
            conditions = [str(p) for p in c.get("conflict_predicates", []) if p]
            if not conditions:
                continue
            base_weight = float(c.get("penalty_weight", 0.99))
            weight = round(max(0.01, min(0.99, base_weight * self.decay_factor)), 4)
            rule_id = str(c.get("constraint_id") or hashlib.sha1(
                "|".join(conditions).encode("utf-8")
            ).hexdigest()[:12])

            rule = {
                "rule_id": rule_id,
                "weight": weight,
                "conditions": conditions,
                "description_vi": str(c.get("lesson_vi", "")),
                "domain_context_tag": domain_tag,
            }
            if rule_id in existing:
                # Không hạ nhiệt luật cũ: giữ trọng số lớn hơn
                if weight > float(existing[rule_id].get("weight", 0.0)):
                    existing[rule_id].update(rule)
                    compiled_now.append(rule)
            else:
                bucket.append(rule)
                existing[rule_id] = rule
                compiled_now.append(rule)

        self._save_store(store)
        return compiled_now
