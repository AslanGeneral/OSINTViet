# ============================================================
# app/pipeline/stage4_elo_tournament.py
# [AR Step 4 / mục 2A] Chuẩn hóa vòng loại Elo từ test_tournament_round.py
# thành module chuẩn trên main branch:
#   phase_generate -> Proximity -> phase_reflect (Dual-Critic + Prolog)
#   -> phase_tournament_rank -> phase_evolve (gen_1_hybrid_crossover)
# ============================================================
import argparse
import asyncio
import os
from typing import Any, Dict, List, Optional

from app.orchestrator.tournament import TournamentDAGOrchestrator, compute_hybrid_fitness
from app.memory.blackboard_store import SharedBlackboard
from app.services.embedding_engine import embedding_engine


async def _deduplicate_by_proximity(bb: SharedBlackboard) -> List[str]:
    """[AR Step 2] Gộp hypothesis trùng ý tưởng trước khi vào vòng Elo."""
    active = await bb.get_active_hypotheses()
    if len(active) < 2:
        return []
    texts = [f"{h.title}. {h.mechanism}" for h in active]
    pairs = await embedding_engine.check_hypothesis_proximity(texts)
    merged = []
    for i, j, sim in pairs:
        if active[j].status != "ALIVE":
            continue
        active[j].status = "MERGED"
        active[i].critiques.append(
            f"PROXIMITY_MERGE: gộp {active[j].id} (cosine={sim})"
        )
        merged.append(f"{active[j].id}->{active[i].id} (cosine={sim})")
    return merged


async def run_elo_tournament_round(
    problem_statement: str,
    constraints: List[str],
    num_agents: int = 4,
    candidate_models: Optional[List[str]] = None,
    base_url: str = "http://localhost:3001/v1",
    api_key: Optional[str] = None,
    model_name: str = "nvidia/llama-3.1-nemotron-70b-instruct",
) -> Dict[str, Any]:
    """
    Chạy trọn một vòng Elo khép kín (tương đương test_tournament_round.py):
    sinh hypothesis -> gộp trùng -> dual-critic/prolog -> round-robin Elo -> hybrid crossover.
    """
    bb = SharedBlackboard("elo_round_1", problem_statement, constraints)
    orchestrator = TournamentDAGOrchestrator(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        candidate_models=candidate_models,
    )
    try:
        await orchestrator.phase_generate(bb, num_agents=num_agents)
        merged = await _deduplicate_by_proximity(bb)
        await orchestrator.phase_reflect(bb)
        await orchestrator.phase_tournament_rank(bb)

        active = await bb.get_active_hypotheses()
        fitness = compute_hybrid_fitness(active)
        ranked = sorted(active, key=lambda x: fitness.get(x.id, 0.0), reverse=True)
        children = await orchestrator.phase_evolve(bb)
        return {
            "blackboard": bb,
            "ranked": ranked,
            "children": children,
            "winner": children[0] if children else (ranked[0] if ranked else None),
            "merged_pairs": merged,
            "events": bb.event_stream,
        }
    finally:
        await orchestrator.close()


def _main():
    parser = argparse.ArgumentParser(description="OSINTViet Elo tournament round")
    parser.add_argument("--problem", required=True, help="Bài toán cần giải")
    parser.add_argument("--constraint", action="append", default=[], help="Ràng buộc (lặp lại được)")
    parser.add_argument("--agents", type=int, default=4)
    parser.add_argument("--models", default="", help="Danh sách model, phẩy cách")
    parser.add_argument("--base-url", dest="base_url", default="http://localhost:3001/v1")
    parser.add_argument("--api-key", dest="api_key", default=os.getenv("TOURNAMENT_API_KEY", ""))
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()] or None
    result = asyncio.run(run_elo_tournament_round(
        args.problem, args.constraint, args.agents, models,
        args.base_url, args.api_key,
    ))
    winner = result["winner"]
    print("=== KẾT QUẢ VÒNG ELO ===")
    if winner is not None:
        print(f"Winner: {winner.id} | Elo={winner.elo_rating} | Fuzzy={winner.net_fuzzy_score}")
    print(f"Số sự kiện Blackboard: {len(result['events'])}")


if __name__ == "__main__":
    _main()
