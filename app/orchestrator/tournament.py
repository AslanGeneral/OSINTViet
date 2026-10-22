# ============================================================
# app/orchestrator/tournament.py
# TournamentDAGOrchestrator — bản nâng cấp [DT §4.2 / §6.2 / §6.4 / mục 25]:
# - phase_reflect: Dual-Critic song song + Dynamic Fuzzy Prolog (bỏ Reflector đơn)
# - Chọn model bằng UCB1 Bandit thay cho i % len(candidate_models)
# - phase_evolve: chọn bố mẹ theo Hybrid Fitness (Elo + ProbLog score)
# ============================================================
import re
import os
import json
import math
import asyncio
import random
import httpx
from typing import List, Optional, Dict, Any
from app.schemas.tournament_schema import Hypothesis
from app.memory.blackboard_store import SharedBlackboard
from app.schemas.universal_orchestrator_schema import TaskOntologySchema
from app.logic_engine.problog_scanner import problog_scanner
from app.sandbox.attestation import sanitize_and_attest_facts
from app.core.config import settings

VI_LANGUAGE_TAIL = (
    "Analyze and reason structurally, keep all JSON keys and Prolog predicates "
    "in English snake_case, but write all domain analysis, search queries, "
    "and explanations in Vietnamese."
)


def extract_clean_json(raw_text: str) -> dict:
    """Bóc tách JSON ra khỏi bãi rác markdown / think tags của LLM"""
    if not raw_text:
        return {}
    raw_text = raw_text.strip()

    # 1. Thử parse trực tiếp
    try:
        return json.loads(raw_text)
    except Exception:
        pass

    # 2. Xóa các block markdown ```json ... ```
    cleaned = re.sub(r'```json\s*', '', raw_text)
    cleaned = re.sub(r'```', '', cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 3. Dùng Regex tìm cặp ngoặc nhọn { ... } ngoài cùng
    match = re.search(r'(\{.*\})', raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 4. Fallback: Cắt từ { đầu tiên đến } cuối cùng
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw_text[start:end + 1])
        except Exception:
            pass

    return {}


def clean_facts_dict(raw_facts: Any) -> Dict[str, float]:
    """Ép kiểu số tự vệ: chỉ nhận float trong khoảng [0.001 - 0.999] cho ProbLog"""
    if not isinstance(raw_facts, dict):
        return {}

    cleaned = {}
    for k, v in raw_facts.items():
        try:
            val = float(v)
            if val > 1.0:
                val = 0.95
            elif val <= 0.0:
                val = 0.05
            cleaned[str(k)] = round(val, 4)
        except (ValueError, TypeError):
            continue
    return cleaned


def compute_hybrid_fitness(hypotheses: List[Hypothesis], alpha: float = 0.5) -> Dict[str, float]:
    """
    [DT §6.4.2] Fitness(H) = alpha * NormalizedElo(H) + (1-alpha) * Net_Fuzzy_Score(H).
    Chuẩn hóa Elo trong chính quần thể để Elo thô 1200 không lấn át ProbLog score.
    """
    if not hypotheses:
        return {}
    elo_values = [float(h.elo_rating) for h in hypotheses]
    elo_min, elo_max = min(elo_values), max(elo_values)
    span = elo_max - elo_min
    scores: Dict[str, float] = {}
    for h in hypotheses:
        norm_elo = (float(h.elo_rating) - elo_min) / (span + 1e-9) if span > 0 else 0.0
        fuzzy = float(getattr(h, "net_fuzzy_score", 0.0) or 0.0)
        scores[h.id] = round(alpha * norm_elo + (1.0 - alpha) * fuzzy, 4)
    return scores


class TournamentDAGOrchestrator:
    def __init__(
        self,
        base_url: str = "http://localhost:3001/v1",
        api_key: Optional[str] = None,
        model_name: str = "nvidia/llama-3.1-nemotron-70b-instruct",
        candidate_models: Optional[List[str]] = None,
        concurrency_limit: int = 2,
    ):
        self.base_url = base_url
        api_key = api_key or os.getenv("TOURNAMENT_API_KEY", "")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.semaphore = asyncio.Semaphore(concurrency_limit)

        if candidate_models:
            self.candidate_models = candidate_models
        else:
            self.candidate_models = [model_name]

        # UCB1 Bandit state [DT §6.2]
        self._success: Dict[tuple, int] = {}
        self._trials: Dict[tuple, int] = {}
        self._role_calls: Dict[str, int] = {}

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=30.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

    # ================= UCB1 MODEL SELECTION =================
    def _ucb1(self, model: str, role: str) -> float:
        n_mr = self._trials.get((model, role), 0)
        n_r = self._role_calls.get(role, 0)
        if n_mr == 0:
            return float("inf")
        mu = self._success.get((model, role), 0) / n_mr
        return mu + settings.UCB1_EXPLORATION_C * math.sqrt(math.log(n_r + 1) / n_mr)

    def _update_bandit(self, role: str, model: str, success: bool):
        key = (model, role)
        self._trials[key] = self._trials.get(key, 0) + 1
        self._success[key] = self._success.get(key, 0) + (1 if success else 0)
        self._role_calls[role] = self._role_calls.get(role, 0) + 1

    def _pick_model(self, role: str, preferred_model: Optional[str] = None) -> str:
        if preferred_model and preferred_model in self.candidate_models:
            return preferred_model
        return max(self.candidate_models, key=lambda m: self._ucb1(m, role))

    async def _call_llm_with_retry(
        self, system: str, user: str, max_retries: int = 3,
        role: str = "worker", preferred_model: Optional[str] = None,
    ) -> str:
        async with self.semaphore:
            await asyncio.sleep(random.uniform(0.3, 0.6))

            last_err = None
            for attempt in range(1, max_retries + 1):
                # UCB1 thay cho i % len(self.candidate_models)
                current_model = self._pick_model(role, preferred_model if attempt == 1 else None)

                try:
                    payload = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.4,
                    }
                    res = await self.client.post("/chat/completions", json=payload)
                    res.raise_for_status()
                    self._update_bandit(role, current_model, True)
                    return res.json()["choices"][0]["message"]["content"]

                except Exception as e:
                    last_err = e
                    self._update_bandit(role, current_model, False)
                    sleep_time = (2 ** attempt) + random.uniform(0.5, 1.0)
                    print(f"[WARNING] Lỗi gọi model '{current_model}' ({str(e)[:45]}). "
                          f"Thử lại lần {attempt} sau {sleep_time:.1f}s...")
                    await asyncio.sleep(sleep_time)

            raise RuntimeError(f"Chết ngắc sau {max_retries} lần thử! Lỗi: {str(last_err)}")

    # ================= PHASE 1: GENERATE =================
    async def phase_generate(self, bb: SharedBlackboard, num_agents: int = 4) -> List[Hypothesis]:
        await bb.log_event("PHASE_1_GENERATE", f"Khởi động {num_agents} Worker sinh giải pháp...")

        system_prompt = (
            "You are an Elite Co-Scientist Hypothesis Generator. "
            "You MUST respond ONLY with valid JSON. Do not write explanation outside the JSON. "
            "Format: {\"title\": \"string\", \"mechanism\": \"string\", \"assumptions\": [\"string\"], "
            "\"facts\": {\"predicate\": 0.85}} "
            f"{VI_LANGUAGE_TAIL}"
        )

        new_hypotheses = []
        for idx in range(1, num_agents + 1):
            user_prompt = (
                f"Problem: {bb.problem_statement}\nConstraints: {json.dumps(bb.constraints)}\n"
                f"Agent #{idx}: Propose an innovative, resilient engineering/economic mechanism. "
                "Output strictly a JSON object with keys: title, mechanism, assumptions, facts."
            )
            raw = await self._call_llm_with_retry(system_prompt, user_prompt, role="worker")
            data = extract_clean_json(raw)
            safe_facts = clean_facts_dict(data.get("facts", {}))

            hyp = Hypothesis(
                id=f"gen_{bb.current_generation}_hyp_{idx}",
                generation=bb.current_generation,
                title=data.get("title", f"Hypothesis_{idx}"),
                mechanism=data.get("mechanism", raw[:300]),
                assumptions=data.get("assumptions", ["Standard deployment"]),
                facts=safe_facts,
            )
            new_hypotheses.append(hyp)
            await asyncio.sleep(0.4)

        await bb.add_hypotheses(new_hypotheses)
        await bb.log_event("PHASE_1_GENERATE", f"Đã sinh {len(new_hypotheses)} giả thuyết vào Blackboard.")
        return new_hypotheses

    # ================= PHASE 2: DUAL-CRITIC + FUZZY PROLOG =================
    async def _induce_ontology(self, bb: SharedBlackboard, hypotheses: List[Hypothesis]) -> TaskOntologySchema:
        """Dynamic Ontology Induction cho phiên đấu giá (thay danh sách predicate tĩnh)."""
        system_prompt = (
            "You are an Ontology Induction Worker. From the problem statement, induce a dynamic "
            "TaskOntologySchema. Output strictly JSON with keys: domain_tag, allowed_predicates, "
            "positive_rules, conflict_rules. Each rule: {rule_id, weight (0.01-0.99), "
            "conditions (English snake_case predicates), description_vi}. "
            f"{VI_LANGUAGE_TAIL}"
        )
        user_prompt = (
            f"Problem: {bb.problem_statement}\nConstraints: {bb.constraints}\n"
            f"Hypotheses sample: {json.dumps([h.facts for h in hypotheses[:3]], ensure_ascii=False)}"
        )
        try:
            raw = await self._call_llm_with_retry(system_prompt, user_prompt, role="structuring_worker")
            data = extract_clean_json(raw)
            return TaskOntologySchema(**data)
        except Exception:
            # Fallback tối giản nhưng vẫn động: predicates lấy từ chính facts của phiên
            predicates = sorted({p for h in hypotheses for p in h.facts})
            return TaskOntologySchema(
                domain_tag="session_general",
                allowed_predicates=predicates,
                positive_rules=[],
                conflict_rules=[],
            )

    async def phase_reflect(self, bb: SharedBlackboard):
        active = await bb.get_active_hypotheses()
        await bb.log_event("PHASE_2_REFLECT", f"Khởi động Dual-Critic + Fuzzy Prolog cho {len(active)} giả thuyết...")

        ontology = await self._induce_ontology(bb, active)
        prac_sys = (
            "You are an Empirical Fact-Checking Critic (Practical Critic). "
            "Verify the hypothesis against real-world data, legal context, prices, policies "
            "and operational feasibility. Output strictly JSON: "
            "{\"empirical_flaws\": [\"...\"], \"verified_external_evidence\": [\"...\"]} "
            f"{VI_LANGUAGE_TAIL}"
        )
        theo_sys = (
            "You are a Theoretical Risk & Structural Critic. "
            "Identify game-theory flaws, bottlenecks, edge cases, tail risks, and systemic risks. "
            "Output strictly JSON: {\"theoretical_risks\": [\"...\"], \"enforced_sandbox_bounds\": ["
            "{\"metric_name\": \"...\", \"operator\": \"<=\", \"threshold_value\": 0.0, "
            "\"upper_bound\": null, \"unit\": \"...\"}]} "
            f"{VI_LANGUAGE_TAIL}"
        )

        async def critique(hyp: Hypothesis):
            user_prompt = f"Hypothesis: {hyp.title}\nMechanism: {hyp.mechanism}\nFacts: {hyp.facts}"
            prac_raw, theo_raw = await asyncio.gather(
                self._call_llm_with_retry(prac_sys, user_prompt, role="practical_critic"),
                self._call_llm_with_retry(theo_sys, user_prompt, role="theoretical_critic"),
            )
            prac_json = extract_clean_json(prac_raw)
            theo_json = extract_clean_json(theo_raw)

            # Fuzzy Prolog — quét lỗi logic trên ontology động của phiên
            clean_facts = sanitize_and_attest_facts(hyp.facts, valid_receipt=False)
            scan = problog_scanner.scan_hypothesis_dynamic(hyp.id, clean_facts, ontology)
            hyp.net_fuzzy_score = float(scan.get("net_fuzzy_score", 0.0))

            transcript = {
                "practical_critic": prac_json,
                "theoretical_critic": theo_json,
                "fuzzy_prolog": scan,
            }
            await bb.attach_critique(hyp.id, json.dumps(transcript, ensure_ascii=False))

        await asyncio.gather(*[critique(h) for h in active])
        await bb.log_event("PHASE_2_REFLECT", "Hoàn tất phản biện kép + quét logic toàn bộ phương án.")

    # ================= PHASE 3: ELO TOURNAMENT =================
    async def phase_tournament_rank(self, bb: SharedBlackboard):
        active = await bb.get_active_hypotheses()
        await bb.log_event("PHASE_3_RANK", f"Bắt đầu vòng đấu loại Elo giữa {len(active)} đấu thủ...")

        system_prompt = (
            "You are an impartial Grandmaster Arbiter. "
            "Compare two hypotheses using their full critique transcripts. "
            "Output strictly JSON: {\"winner_id\": \"string\", \"loser_id\": \"string\", "
            "\"rationale\": \"string\"} "
            f"{VI_LANGUAGE_TAIL}"
        )

        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                h1, h2 = active[i], active[j]
                user_prompt = (
                    f"Contender A: {h1.id} ({h1.title})\nCritiques: {'; '.join(h1.critiques)[:1200]}\n\n"
                    f"Contender B: {h2.id} ({h2.title})\nCritiques: {'; '.join(h2.critiques)[:1200]}\n\n"
                    f"Decide who wins. Return winner_id and loser_id strictly as '{h1.id}' or '{h2.id}'."
                )
                try:
                    raw = await self._call_llm_with_retry(system_prompt, user_prompt, role="judge")
                    data = extract_clean_json(raw)
                    win_id = data.get("winner_id")
                    lose_id = data.get("loser_id")

                    if win_id in [h1.id, h2.id] and lose_id in [h1.id, h2.id]:
                        await bb.update_elo(win_id, lose_id)
                        print(f"  [Match] {win_id} thắng {lose_id}")
                except Exception:
                    print(f"  [Match] Xử hòa {h1.id} vs {h2.id}")
                await asyncio.sleep(0.4)

        sorted_by_elo = sorted(active, key=lambda x: x.elo_rating, reverse=True)
        await bb.log_event("PHASE_3_RANK", "=== BẢNG XẾP HẠNG ELO SAU VÒNG ĐẤU ===")
        for rank, h in enumerate(sorted_by_elo, 1):
            await bb.log_event("ELO_LEADERBOARD", f"Top {rank}: [{h.elo_rating:.1f} pts] {h.id} - {h.title}")

    # ================= PHASE 4: EVOLVE (HYBRID FITNESS) =================
    async def phase_evolve(self, bb: SharedBlackboard) -> List[Hypothesis]:
        active = await bb.get_active_hypotheses()

        # [DT §6.4.2] Hybrid Fitness chuẩn hóa, không dùng Elo thô + ProbLog score.
        fitness = compute_hybrid_fitness(active)
        ranked = sorted(active, key=lambda x: fitness.get(x.id, 0.0), reverse=True)
        parent_a = ranked[0]
        parent_b = ranked[1] if len(ranked) > 1 else ranked[0]

        bb.current_generation += 1
        await bb.log_event("PHASE_4_EVOLVE", f"Lai ghép gen: {parent_a.id} x {parent_b.id} -> Gen {bb.current_generation}")

        system_prompt = (
            "You are an Evolutionary Synthesis Engine. "
            "Synthesize Parent A and Parent B into a dominant hybrid. "
            "The hybrid MUST eliminate all conflict_trace found by the Fuzzy Prolog scanner. "
            "Output strictly JSON: {\"title\": \"string\", \"mechanism\": \"string\", "
            "\"assumptions\": [\"string\"], \"facts\": {\"predicate\": 0.9}} "
            f"{VI_LANGUAGE_TAIL}"
        )

        conflict_trace = []
        for parent in (parent_a, parent_b):
            for critique in parent.critiques:
                try:
                    data = json.loads(critique)
                    conflict_trace.extend(
                        data.get("fuzzy_prolog", {}).get("conflict_trace", [])
                    )
                except Exception:
                    continue

        user_prompt = (
            f"PARENT A: {parent_a.title}\n{parent_a.mechanism[:400]}\n\n"
            f"PARENT B: {parent_b.title}\n{parent_b.mechanism[:400]}\n\n"
            f"CONFLICT_TRACE TO ELIMINATE: {json.dumps(conflict_trace, ensure_ascii=False)}"
        )
        raw = await self._call_llm_with_retry(system_prompt, user_prompt, role="worker")
        data = extract_clean_json(raw)
        safe_facts = clean_facts_dict(data.get("facts", {}))

        child = Hypothesis(
            id=f"gen_{bb.current_generation}_hybrid_crossover",
            generation=bb.current_generation,
            title=data.get("title", "Hybrid Crossover Solution"),
            mechanism=data.get("mechanism", raw[:300]),
            assumptions=data.get("assumptions", []),
            facts=safe_facts,
            parent_ids=[parent_a.id, parent_b.id],
            elo_rating=1300.0,
        )

        await bb.add_hypotheses([child])
        await bb.log_event("PHASE_4_EVOLVE", f"Đã sinh ra Quán quân ưu việt: {child.id} ({child.title})")
        return [child]

    async def close(self):
        await self.client.aclose()
