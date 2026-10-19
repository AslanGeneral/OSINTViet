import asyncio
from typing import Dict, List, Optional
from app.schemas.tournament_schema import Hypothesis

class SharedBlackboard:
    def __init__(self, task_id: str, problem_statement: str, constraints: List[str]):
        self.task_id = task_id
        self.problem_statement = problem_statement
        self.constraints = constraints
        self.current_generation = 0
        self.hypotheses: Dict[str, Hypothesis] = {}
        self.event_stream: List[Dict] = []
        self._lock = asyncio.Lock()

    async def log_event(self, phase: str, message: str):
        async with self._lock:
            event = {"phase": phase, "message": message}
            self.event_stream.append(event)
            print(f"[\033[94m{phase}\033[0m] {message}")

    async def add_hypotheses(self, new_items: List[Hypothesis]):
        async with self._lock:
            for item in new_items:
                self.hypotheses[item.id] = item

    async def get_active_hypotheses(self) -> List[Hypothesis]:
        async with self._lock:
            return [h for h in self.hypotheses.values() if h.status == "ALIVE"]

    async def update_elo(self, winner_id: str, loser_id: str, k_factor: float = 32.0):
        async with self._lock:
            h_win = self.hypotheses.get(winner_id)
            h_lose = self.hypotheses.get(loser_id)
            if not h_win or not h_lose:
                return

            # Công thức Elo
            r_win = h_win.elo_rating
            r_lose = h_lose.elo_rating

            expected_win = 1.0 / (1.0 + 10.0 ** ((r_lose - r_win) / 400.0))
            expected_lose = 1.0 / (1.0 + 10.0 ** ((r_win - r_lose) / 400.0))

            h_win.elo_rating = round(r_win + k_factor * (1.0 - expected_win), 2)
            h_lose.elo_rating = round(r_lose + k_factor * (0.0 - expected_lose), 2)

    async def attach_critique(self, hyp_id: str, critique_text: str):
        async with self._lock:
            if hyp_id in self.hypotheses:
                self.hypotheses[hyp_id].critiques.append(critique_text)