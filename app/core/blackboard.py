# ============================================================
# app/core/blackboard.py
# SharedBlackboard: lưu shared state + TaskOntologySchema
# + Short-Term Negative DB (negative_constraints) [AR][DT §6.5]
# ============================================================
import asyncio
from typing import Dict, List, Any
from app.schemas.universal_orchestrator_schema import TaskOntologySchema


class SharedBlackboard:
    """
    Bảng thông tin chung của pipeline đa miền phổ quát:
    - state: trạng thái từng Stage (1 -> 7)
    - ontology: TaskOntologySchema được tạo động ở đầu phiên [DT §3/§6.3]
    - negative_constraints: Short-Term Negative DB — mọi lỗi bắt được ở Stage 4
      và DEAD_BRANCH ở Stage 6 được ghi vào đây để hardlock Workers [DT §6.5.3]
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.state: Dict[str, Any] = {}
        self.ontology: Dict[str, TaskOntologySchema] = {}   # domain_tag -> ontology
        self.negative_constraints: List[Dict[str, Any]] = []
        self.hypotheses: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def set_state(self, key: str, value: Any):
        async with self._lock:
            self.state[key] = value

    async def get_state(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return self.state.get(key, default)

    async def store_ontology(self, ontology: TaskOntologySchema):
        """Nạp TaskOntologySchema động từ Stage 1 (Dynamic Ontology Induction)"""
        async with self._lock:
            self.ontology[ontology.domain_tag] = ontology

    async def get_ontology(self, domain_tag: str) -> Any:
        async with self._lock:
            return self.ontology.get(domain_tag)

    async def add_negative_constraint(self, constraint: Dict[str, Any]):
        """
        [DT §6.5.3] Short-Term Memory: ghi ngay khi Stage 4 phát hiện lỗi
        hoặc Stage 6 đánh dấu DEAD_BRANCH, để Workers không lặp lại sai lầm.
        """
        async with self._lock:
            self.negative_constraints.append(constraint)
        print(f"[BLACKBOARD][ANTI-OBJECT] Nạp nguyên tắc cấm: {constraint.get('lesson_vi', '')[:80]}")

    async def get_negative_constraints(self) -> List[Dict[str, Any]]:
        async with self._lock:
            return list(self.negative_constraints)

    async def add_hypothesis(self, hyp: Dict[str, Any]):
        async with self._lock:
            self.hypotheses.append(hyp)

    async def get_hypotheses(self, status_filter: str = "ALIVE") -> List[Dict[str, Any]]:
        async with self._lock:
            return [h for h in self.hypotheses if h.get("status", "ALIVE") == status_filter]
