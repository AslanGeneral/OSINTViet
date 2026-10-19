import re
import json
import asyncio
import random
import httpx
from typing import List, Optional, Dict, Any
from app.schemas.tournament_schema import Hypothesis, CritiqueResult, MatchBattleResult
from app.memory.blackboard_store import SharedBlackboard

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
            return json.loads(raw_text[start:end+1])
        except Exception:
            pass

    return {}

def clean_facts_dict(raw_facts: Any) -> Dict[str, float]:
    """Ép kiểu số tự vệ: Chỉ nhận Float trong khoảng [0.001 - 0.999] cho ProbLog"""
    default_facts = {"uses_existing_infra": 0.90, "low_capex_under_50b": 0.85}
    if not isinstance(raw_facts, dict):
        return default_facts
        
    cleaned = {}
    for k, v in raw_facts.items():
        try:
            val = float(v)
            if val > 1.0: val = 0.95
            elif val <= 0.0: val = 0.05
            cleaned[str(k)] = round(val, 4)
        except (ValueError, TypeError):
            continue
            
    return cleaned if cleaned else default_facts

class TournamentDAGOrchestrator:
    def __init__(
        self, 
        base_url: str = "http://localhost:3001/v1", 
        api_key: str = "freellmapi-56e34cb0ae55c51ea525b15bbea3e233415a3efd4bbd6d4f",
        model_name: str = "nvidia/llama-3.1-nemotron-70b-instruct",
        candidate_models: Optional[List[str]] = None,
        concurrency_limit: int = 2
    ):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        
        if candidate_models:
            self.candidate_models = candidate_models
        else:
            self.candidate_models = [model_name]

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=30.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
        )

    async def _call_llm_with_retry(self, system: str, user: str, max_retries: int = 3) -> str:
        async with self.semaphore:
            await asyncio.sleep(random.uniform(0.3, 0.6))
            
            last_err = None
            for attempt in range(1, max_retries + 1):
                current_model = self.candidate_models[(attempt - 1) % len(self.candidate_models)]
                
                try:
                    payload = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user}
                        ],
                        "temperature": 0.4
                    }
                    res = await self.client.post("/chat/completions", json=payload)
                    res.raise_for_status()
                    return res.json()["choices"][0]["message"]["content"]
                
                except Exception as e:
                    last_err = e
                    sleep_time = (2 ** attempt) + random.uniform(0.5, 1.0)
                    print(f"[\033[93mWARNING\033[0m] Lỗi gọi model '{current_model}' ({str(e)[:45]}). Thử lại lần {attempt} sau {sleep_time:.1f}s...")
                    await asyncio.sleep(sleep_time)

            raise RuntimeError(f"Chết ngắc sau {max_retries} lần thử! Lỗi: {str(last_err)}")

    # ================= PHASE 1: GENERATE =================
    async def phase_generate(self, bb: SharedBlackboard, num_agents: int = 3) -> List[Hypothesis]:
        await bb.log_event("PHASE_1_GENERATE", f"Khởi động {num_agents} Agent sinh giải pháp bằng Nvidia Nemotron...")
        
        system_prompt = (
            "You are an Elite Co-Scientist Hypothesis Generator. "
            "You MUST respond ONLY with valid JSON. Do not write explanation outside the JSON. "
            "Format: {\"title\": \"string\", \"mechanism\": \"string\", \"assumptions\": [\"string\"], \"facts\": {\"predicate\": 0.85}}"
        )

        new_hypotheses = []
        for idx in range(1, num_agents + 1):
            user_prompt = (
                f"Problem: {bb.problem_statement}\nConstraints: {json.dumps(bb.constraints)}\n"
                f"Agent #{idx}: Propose an innovative, resilient engineering/economic mechanism. "
                "Output strictly a JSON object with keys: title, mechanism, assumptions, facts."
            )
            raw = await self._call_llm_with_retry(system_prompt, user_prompt)
            data = extract_clean_json(raw)
            safe_facts = clean_facts_dict(data.get("facts", {}))

            hyp = Hypothesis(
                id=f"gen_{bb.current_generation}_hyp_{idx}",
                generation=bb.current_generation,
                title=data.get("title", f"Nemotron_Hypothesis_{idx}"),
                mechanism=data.get("mechanism", raw[:300]),
                assumptions=data.get("assumptions", ["Standard deployment"]),
                facts=safe_facts
            )
            new_hypotheses.append(hyp)
            await asyncio.sleep(0.4)

        await bb.add_hypotheses(new_hypotheses)
        await bb.log_event("PHASE_1_GENERATE", f"Đã sinh {len(new_hypotheses)} giả thuyết vào Blackboard.")
        return new_hypotheses

    # ================= PHASE 2: REFLECT =================
    async def phase_reflect(self, bb: SharedBlackboard):
        active = await bb.get_active_hypotheses()
        await bb.log_event("PHASE_2_REFLECT", f"Dàn Nemotron Reflector mổ xẻ {len(active)} giả thuyết...")

        system_prompt = (
            "You are a brutal Academic Peer-Reviewer. "
            "Output strictly a JSON object: {\"flaws\": [\"string\"], \"bottlenecks\": [\"string\"], \"suggested_verification\": \"string\"}"
        )

        for hyp in active:
            user_prompt = f"Hypothesis: {hyp.title}\nMechanism: {hyp.mechanism}\nExpose fatal failure modes and bottlenecks."
            raw = await self._call_llm_with_retry(system_prompt, user_prompt)
            data = extract_clean_json(raw)
            
            flaws = data.get("flaws", ["Potential edge case failure"])
            bottlenecks = data.get("bottlenecks", ["Resource constraint"])
            formatted = f"FLAWS: {'; '.join(flaws)} | BOTTLENECKS: {'; '.join(bottlenecks)}"
            await bb.attach_critique(hyp.id, formatted)
            await asyncio.sleep(0.4)

        await bb.log_event("PHASE_2_REFLECT", "Hoàn tất phản biện toàn bộ các phương án.")

    # ================= PHASE 3: ELO TOURNAMENT =================
    async def phase_tournament_rank(self, bb: SharedBlackboard):
        active = await bb.get_active_hypotheses()
        await bb.log_event("PHASE_3_RANK", f"Bắt đầu vòng đấu loại Elo giữa {len(active)} đấu thủ...")

        system_prompt = (
            "You are an impartial Grandmaster Arbiter. "
            "Compare two hypotheses. Output strictly JSON: {\"winner_id\": \"string\", \"loser_id\": \"string\", \"rationale\": \"string\"}"
        )

        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                h1, h2 = active[i], active[j]
                user_prompt = (
                    f"Contender A: {h1.id} ({h1.title})\nCritiques: {'; '.join(h1.critiques)[:200]}\n\n"
                    f"Contender B: {h2.id} ({h2.title})\nCritiques: {'; '.join(h2.critiques)[:200]}\n\n"
                    f"Decide who wins. Return winner_id and loser_id strictly as '{h1.id}' or '{h2.id}'."
                )
                try:
                    raw = await self._call_llm_with_retry(system_prompt, user_prompt)
                    data = extract_clean_json(raw)
                    win_id = data.get("winner_id")
                    lose_id = data.get("loser_id")
                    
                    if win_id in [h1.id, h2.id] and lose_id in [h1.id, h2.id]:
                        await bb.update_elo(win_id, lose_id)
                        print(f"  [Match] \033[92m{win_id}\033[0m thắng \033[91m{lose_id}\033[0m")
                except Exception as e:
                    print(f"  [Match] Xử hòa {h1.id} vs {h2.id}")
                await asyncio.sleep(0.4)

        sorted_by_elo = sorted(active, key=lambda x: x.elo_rating, reverse=True)
        await bb.log_event("PHASE_3_RANK", "=== BẢNG XẾP HẠNG ELO SAU VÒNG ĐẤU ===")
        for rank, h in enumerate(sorted_by_elo, 1):
            await bb.log_event("ELO_LEADERBOARD", f"Top {rank}: [{h.elo_rating:.1f} pts] {h.id} - {h.title}")

    # ================= PHASE 4: EVOLVE =================
    async def phase_evolve(self, bb: SharedBlackboard) -> List[Hypothesis]:
        active = await bb.get_active_hypotheses()
        sorted_by_elo = sorted(active, key=lambda x: x.elo_rating, reverse=True)
        
        parent_a = sorted_by_elo[0]
        parent_b = sorted_by_elo[1] if len(sorted_by_elo) > 1 else sorted_by_elo[0]
        
        bb.current_generation += 1
        await bb.log_event("PHASE_4_EVOLVE", f"Lai ghép gen: {parent_a.id} x {parent_b.id} -> Gen {bb.current_generation}")

        system_prompt = (
            "You are an Evolutionary Synthesis Engine. "
            "Synthesize Parent A and Parent B into a dominant hybrid. "
            "Output strictly JSON: {\"title\": \"string\", \"mechanism\": \"string\", \"assumptions\": [\"string\"], \"facts\": {\"predicate\": 0.9}}"
        )

        user_prompt = f"PARENT A: {parent_a.title}\n{parent_a.mechanism[:400]}\n\nPARENT B: {parent_b.title}\n{parent_b.mechanism[:400]}"
        raw = await self._call_llm_with_retry(system_prompt, user_prompt)
        data = extract_clean_json(raw)
        safe_facts = clean_facts_dict(data.get("facts", {}))

        child = Hypothesis(
            id=f"gen_{bb.current_generation}_nemotron_hybrid",
            generation=bb.current_generation,
            title=data.get("title", "Nemotron Hybrid Solution"),
            mechanism=data.get("mechanism", raw[:300]),
            assumptions=data.get("assumptions", []),
            facts=safe_facts,
            parent_ids=[parent_a.id, parent_b.id],
            elo_rating=1300.0
        )

        await bb.add_hypotheses([child])
        await bb.log_event("PHASE_4_EVOLVE", f"Đã sinh ra Quán quân ưu việt: {child.id} ({child.title})")
        return [child]

    async def close(self):
        await self.client.aclose()
