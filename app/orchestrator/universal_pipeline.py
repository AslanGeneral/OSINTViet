# ============================================================
# app/orchestrator/universal_pipeline.py
# UniversalCoScientistPipeline [DT §5 / §10]
# 7 stage:
#   1. Ingest + Embedding + Dynamic Ontology Induction
#   2. Multi-model Worker Pool (>= 4 Workers) — Judge không sinh hypothesis
#   3. Embedding Proximity + Negative Constraints (cả trong Stage 2 lẫn Stage 3)
#   4. Dual-Critic (Practical + Theoretical) + Dynamic Fuzzy Prolog
#   5. Multi-Judge Elo Round-Robin + Hybrid Fitness + Agent Evolver crossover
#   6. Universal Parameter Forcing Sandbox + RepairTicket + HMAC attestation
#   7. Chuẩn hóa JSON Schema + Pareto Export
# ============================================================
import json
import asyncio
from typing import List, Dict, Any, Optional

from app.core.plugin_registry import hub
from app.services.embedding_engine import embedding_engine
from app.logic_engine.problog_scanner import problog_scanner
from app.sandbox.runner import sandbox_runner
from app.sandbox.attestation import sanitize_and_attest_facts, attest_sandbox_result
from app.memory.negative_memory_compiler import NegativeMemoryCompiler
from app.schemas.universal_orchestrator_schema import (
    IngestedClusterSchema, DualCritiqueReport, MetricBoundSpec,
    FinalOrchestratorOutput,
)
from app.orchestrator.tournament import extract_clean_json

VI_LANGUAGE_TAIL = (
    "Analyze and reason structurally, keep all JSON keys and Prolog predicates "
    "in English snake_case, but write all domain analysis, search queries, "
    "and explanations in Vietnamese."
)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Đọc thuộc tính trên cả Pydantic object lẫn dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class UniversalCoScientistPipeline:
    def __init__(self, key_router: Any, blackboard: Any):
        self.router = key_router
        self.bb = blackboard

    # ---------------------------------------------------------
    # STAGE 1 — Automated Ingestion + Embedding + Dynamic Ontology
    # ---------------------------------------------------------
    async def stage1_ingest_and_structure(
        self, user_requirement: str, raw_inputs: List[Dict[str, Any]], domain_tag: str
    ) -> IngestedClusterSchema:
        """
        Phase 1: Nạp dữ liệu tự động qua Ingestor Plugins + Embedding v5 Pro,
        kiểm tra lỗi Search, trích xuất Raw JSON Schema Cluster kèm Dynamic Ontology.
        """
        # 1. Chạy toàn bộ Ingestor plugin đã đăng ký.
        #    [Appendix A hàng 13 - quyết định user]: nhận cả list và dict.
        for name, ingest_fn in hub.data_ingestors.items():
            try:
                extra_data = await ingest_fn(user_requirement)
                if isinstance(extra_data, list):
                    raw_inputs.extend(extra_data)
                elif isinstance(extra_data, dict):
                    nested = extra_data.get("raw_data")
                    if isinstance(nested, list):
                        raw_inputs.extend(nested)
                    else:
                        raw_inputs.append(extra_data)
            except Exception:
                continue

        # 2. Embedding filter + CRAG/MMR validation
        if raw_inputs:
            await embedding_engine.filter_and_ingest_news(raw_inputs)

        verified_hits = await embedding_engine.semantic_search(user_requirement, top_k=4)
        crag_state = "CORRECT" if verified_hits else "INCORRECT"
        num_warning: Dict[str, Any] = {}
        excerpts: List[str] = []

        for hit in verified_hits:
            if "search_status" in hit and hit["search_status"].startswith("INCORRECT"):
                crag_state = "INCORRECT"
            if hit.get("document"):
                excerpts.append(hit.get("document", ""))
            ver = hit.get("search_verification", {})
            crag_state = ver.get("crag_state", crag_state)
            num_warning = ver.get("numerical_divergence_warning", num_warning)

        # [DT §4.4] Chỉ khi CRAG INCORRECT/AMBIGUOUS mới gọi Fast Search API
        # để bổ sung dữ liệu nóng; kết quả được đưa thẳng vào excerpts cho Worker.
        if crag_state in ("INCORRECT", "AMBIGUOUS"):
            for tool_name, tool_fn in hub.practical_tools.items():
                try:
                    live = await tool_fn(user_requirement)
                    if live:
                        excerpts.append(json.dumps(live, ensure_ascii=False))
                except Exception:
                    continue

        # 3. Worker phân tích dữ liệu thô, trích cluster JSON + Dynamic Ontology
        structuring_sys = (
            "You are an Automated Data Structuring & Ontology Induction Worker. "
            "Analyze the user requirements and raw excerpts to produce a generalized JSON schema cluster "
            "and induce domain-specific Prolog predicates (in English snake_case). "
            "Output strictly JSON matching IngestedClusterSchema. "
            f"{VI_LANGUAGE_TAIL}"
        )
        raw_llm = await self.router.call_worker(
            role="structuring_worker",
            system_prompt=structuring_sys,
            user_prompt=(
                f"Domain: {domain_tag}\nRequirement: {user_requirement}\n"
                f"Raw Data: {json.dumps(excerpts, ensure_ascii=False)}"
            ),
        )
        parsed = extract_clean_json(raw_llm)
        # CRAG/MMR là kết quả đo được từ embedding engine, không phải do LLM
        # tự khai báo trong JSON. Ghi đè để cluster phản ánh đúng trạng thái
        # Search (đặc biệt khi local search rỗng -> INCORRECT).
        parsed["crag_verification_state"] = crag_state
        parsed["numerical_divergence_warning"] = num_warning
        cluster = IngestedClusterSchema(**parsed)

        # Dynamic Ontology Induction lưu lên Blackboard [DT §3.2]
        try:
            await self.bb.store_ontology(cluster.induced_ontology)
            await self.bb.set_state("ingested_cluster", cluster.model_dump())
        except Exception:
            pass
        return cluster

    # ---------------------------------------------------------
    # STAGE 2 — Multi-model Worker Pool
    # ---------------------------------------------------------
    async def stage2_workers_gen(
        self, user_requirement: str, cluster: IngestedClusterSchema,
        num_workers: int = 4,
    ) -> List[Dict[str, Any]]:
        """
        [DT §4.1/§5 Phase 2] Tối thiểu 4 Workers trên các model/nhiệt độ khác nhau.
        Judge tuyệt đối không sinh hypothesis để tránh self-preference bias.
        """
        num_workers = max(4, num_workers)
        negative_constraints = await self.bb.get_negative_constraints()

        worker_sys = (
            "You are an Independent Hypothesis Generator (Worker). "
            "Generate one novel mechanism for the user requirement, grounded in the raw JSON cluster. "
            "Output strictly JSON with keys: hypothesis_id, hypothesis_title_vi, mechanism_vi, "
            "prolog_predicates (English snake_case keys, float 0-1), "
            "practical_critic_search_queries (Vietnamese), sandbox_forced_params, assumptions. "
            f"{VI_LANGUAGE_TAIL}"
        )

        async def run_one(idx: int) -> Optional[Dict[str, Any]]:
            temperature = [0.3, 0.5, 0.7, 0.9][idx % 4]
            user_prompt = (
                f"Requirement: {user_requirement}\n"
                f"Raw JSON cluster: {cluster.model_dump_json()}\n"
                f"Negative constraints (tuyệt đối không lặp lại): "
                f"{json.dumps(negative_constraints, ensure_ascii=False)}\n"
                f"Worker #{idx + 1}: propose a different perspective from other workers."
            )
            try:
                # [DT §6.2] Để Router tự chọn model bằng UCB1; không ép vòng lặp
                # cố định i % len(models) để bảo đảm đa dạng model và học điểm.
                raw = await self.router.call_worker(
                    role="worker", system_prompt=worker_sys,
                    user_prompt=user_prompt,
                    temperature=temperature,
                )
            except Exception:
                return None
            data = extract_clean_json(raw)
            if not isinstance(data, dict) or not data:
                return None
            # ID do pipeline cấp để tránh LLM trả về None/trùng ID giữa các Worker.
            data["hypothesis_id"] = f"gen_1_hyp_{idx + 1}"
            data.setdefault("hypothesis_title_vi", f"Phương án {idx + 1}")
            data.setdefault("mechanism_vi", "")
            data.setdefault("prolog_predicates", {})
            data.setdefault("practical_critic_search_queries", [])
            data.setdefault("sandbox_forced_params", {})
            data["elo_rating"] = 1200.0
            data["status"] = "ALIVE"
            data["generation"] = 1
            return data

        results = await asyncio.gather(*[run_one(i) for i in range(num_workers)])
        hypotheses = [h for h in results if h]

        # [Appendix A hàng 2 - quyết định user "Cả hai"]: Proximity chạy ngay trong
        # vòng sinh hypothesis VÀ đồng thời có Stage 3 độc lập.
        hypotheses = await self.stage3_embedding_proximity(hypotheses)

        for hyp in hypotheses:
            await self.bb.add_hypothesis(hyp)
        return hypotheses

    # ---------------------------------------------------------
    # STAGE 3 — Embedding Proximity + Negative Constraints DB
    # ---------------------------------------------------------
    async def stage3_embedding_proximity(self, hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        [DT §5 Phase 3] Cosine matrix bằng Embed v5 Pro; cặp > 0.85 thì merge
        (chống Mode Collapse). Đồng thời so vector hypothesis với Negative
        Constraints DB để phát hiện lặp lại sai lầm cũ [DT §4.3.3].
        """
        if len(hypotheses) < 2:
            return hypotheses

        texts = [f"{h.get('hypothesis_title_vi', '')}. {h.get('mechanism_vi', '')}" for h in hypotheses]
        pairs = await embedding_engine.check_hypothesis_proximity(texts)

        merged_indices = set()
        for i, j, sim in pairs:
            if j in merged_indices or i in merged_indices:
                continue
            kept, dropped = hypotheses[i], hypotheses[j]
            kept.setdefault("merged_from", []).append(dropped.get("hypothesis_id"))
            kept["merged_similarity"] = sim
            dropped["status"] = "MERGED"
            merged_indices.add(j)

        alive = [h for idx, h in enumerate(hypotheses) if idx not in merged_indices]

        # So khớp với Negative Constraints DB
        negatives = await self.bb.get_negative_constraints()
        neg_texts = [str(n.get("lesson_vi", "")) for n in negatives if n.get("lesson_vi")]
        if neg_texts and alive:
            all_vecs = await embedding_engine.get_embeddings_batch(
                neg_texts + [f"{h.get('hypothesis_title_vi', '')}. {h.get('mechanism_vi', '')}" for h in alive],
                input_type="search_document",
            )
            neg_vecs, hyp_vecs = all_vecs[: len(neg_texts)], all_vecs[len(neg_texts):]
            for h, hv in zip(alive, hyp_vecs):
                if hv is None:
                    continue
                best = 0.0
                for nv in neg_vecs:
                    if nv is not None:
                        best = max(best, float((hv * nv).sum()))
                if best > 0.85:
                    h["negative_constraint_warning"] = {
                        "similarity": round(best, 4),
                        "action": "REQUIRE_REBRANCH",
                    }
        return alive

    # ---------------------------------------------------------
    # STAGE 4 — Dual-Critic + Dynamic Fuzzy Prolog (§10)
    # ---------------------------------------------------------
    async def stage4_run_dual_critics_and_prolog(
        self, hyp: Any, cluster: IngestedClusterSchema
    ) -> DualCritiqueReport:
        """
        Phase 4: Chạy song song Practical Critique (Internet/RAG) + Theoretical Critique
        (đánh giá rủi ro) + quét lỗi logic bằng Dynamic Fuzzy Prolog.
        """
        hyp_id = _get(hyp, "id") or _get(hyp, "hypothesis_id", "unknown_hyp")
        hyp_title = _get(hyp, "title") or _get(hyp, "hypothesis_title_vi", "")
        hyp_mechanism = _get(hyp, "mechanism") or _get(hyp, "mechanism_vi", "")
        hyp_facts = _get(hyp, "facts") or _get(hyp, "prolog_predicates", {})

        # 4A. Practical Critic: ưu tiên kho vector cục bộ; chỉ gọi tool Internet
        # khi CRAG báo INCORRECT/AMBIGUOUS [DT §4.4] để tránh Playwright 10–15s.
        rag_hits = await embedding_engine.semantic_search(hyp_title, top_k=3)

        rag_state = "INCORRECT" if not rag_hits else "CORRECT"
        for hit in rag_hits:
            if str(hit.get("search_status", "")).startswith("INCORRECT"):
                rag_state = "INCORRECT"
                break
            rag_state = hit.get("search_verification", {}).get("crag_state", rag_state)

        external_tool_outputs: Dict[str, Any] = {}
        if rag_state in ("INCORRECT", "AMBIGUOUS"):
            for tool_name, tool_fn in hub.practical_tools.items():
                try:
                    external_tool_outputs[tool_name] = await tool_fn(hyp_title)
                except Exception as e:
                    external_tool_outputs[tool_name] = {"error": str(e)}
        else:
            external_tool_outputs = {
                "_note": "Local vector repository returned CRAG=CORRECT; live search skipped."
            }

        prac_sys = (
            "You are an Empirical Fact-Checking Critic. "
            "Verify the hypothesis against ingested raw data and external internet tool outputs. "
            "Output JSON: {'empirical_flaws': [...], 'verified_external_evidence': [...]}. "
            f"{VI_LANGUAGE_TAIL}"
        )

        # 4B. Theoretical Critic: thuật toán rủi ro + đặt ngưỡng Sandbox
        theo_algo_outputs: Dict[str, Any] = {}
        for algo_name, algo_fn in hub.theory_algorithms.items():
            try:
                theo_algo_outputs[algo_name] = await algo_fn(hyp_facts)
            except Exception as e:
                theo_algo_outputs[algo_name] = {"error": str(e)}

        theo_sys = (
            "You are a Theoretical Risk & Structural Critic. "
            "Identify theoretical flaws, edge-case risks, and define strict numerical MetricBoundSpec "
            "to force the hypothesis into the Sandbox. "
            "Output JSON: {'theoretical_risks': [...], 'enforced_sandbox_bounds': [...]}. "
            f"{VI_LANGUAGE_TAIL}"
        )

        prac_raw, theo_raw = await asyncio.gather(
            self.router.call_worker(
                "practical_critic", prac_sys,
                f"Hyp: {hyp_mechanism}\nEvidence: {json.dumps(external_tool_outputs, ensure_ascii=False)}"
                f"\nRAG hits: {json.dumps(rag_hits, ensure_ascii=False)}",
            ),
            self.router.call_worker(
                "theoretical_critic", theo_sys,
                f"Hyp: {hyp_mechanism}\nMathCheck: {json.dumps(theo_algo_outputs, ensure_ascii=False)}",
            ),
        )

        prac_json = extract_clean_json(prac_raw)
        theo_json = extract_clean_json(theo_raw)

        # 4C. Fuzzy Prolog với Dynamic Ontology của bài toán
        clean_facts = sanitize_and_attest_facts(hyp_facts, valid_receipt=False)
        prolog_res = problog_scanner.scan_hypothesis_dynamic(
            hypothesis_id=hyp_id,
            extracted_facts=clean_facts,
            ontology=cluster.induced_ontology,
        )

        # [DT §6.5.3] Lỗi phát hiện ở Stage 4 ghi ngay vào Short-Term Negative DB
        if prolog_res.get("conflict_trace"):
            triggered_preds: List[str] = []
            for rule in cluster.induced_ontology.conflict_rules:
                if all(clean_facts.get(p, 0.0) > 0.5 for p in rule.conditions):
                    triggered_preds.extend(rule.conditions)
            await self.bb.add_negative_constraint({
                "constraint_id": f"stage4_{hyp_id}",
                "conflict_predicates": sorted(set(triggered_preds)),
                "lesson_vi": "; ".join(prolog_res.get("conflict_trace", [])),
            })

        return DualCritiqueReport(
            hypothesis_id=hyp_id,
            practical_critic_model="multi_model_pool_A",
            empirical_flaws=prac_json.get("empirical_flaws", []),
            verified_external_evidence=prac_json.get("verified_external_evidence", []),
            theoretical_critic_model="multi_model_pool_B",
            theoretical_risks=theo_json.get("theoretical_risks", []),
            enforced_sandbox_bounds=[
                MetricBoundSpec(**b) for b in theo_json.get("enforced_sandbox_bounds", [])
            ],
            prolog_scan_result=prolog_res,
        )

    # ---------------------------------------------------------
    # STAGE 5 — Multi-Judge Elo + Hybrid Fitness + Crossover
    # ---------------------------------------------------------
    async def stage5_elo_tournament(
        self, hypotheses: List[Dict[str, Any]],
        reports: Dict[str, DualCritiqueReport],
    ) -> List[Dict[str, Any]]:
        """
        [DT §6.4] Round-Robin Elo với >= 2 Judges khác model Worker.
        Judge đọc toàn bộ transcript Practical + Theoretical + Prolog conflict_trace.
        Sau đó lai ghép 2 cá thể có Hybrid Fitness cao nhất.
        """
        if len(hypotheses) < 2:
            return hypotheses

        judge_models = self._pick_judge_models(count=2)
        for i in range(len(hypotheses)):
            for j in range(i + 1, len(hypotheses)):
                h1, h2 = hypotheses[i], hypotheses[j]
                votes = await asyncio.gather(
                    self._judge_match(h1, h2, reports, "judge_a", judge_models[0] if judge_models else None),
                    self._judge_match(h1, h2, reports, "judge_b", judge_models[1] if len(judge_models) > 1 else None),
                )
                valid_votes = [v for v in votes if v in (h1["hypothesis_id"], h2["hypothesis_id"])]
                if not valid_votes:
                    continue
                wins_h1 = sum(1 for v in valid_votes if v == h1["hypothesis_id"])
                wins_h2 = sum(1 for v in valid_votes if v == h2["hypothesis_id"])
                if wins_h1 == wins_h2:
                    s1 = 0.5  # hòa — Elo bảo toàn tổng điểm
                else:
                    s1 = 1.0 if wins_h1 > wins_h2 else 0.0
                self._update_elo(h1, h2, s1)

        # [DT §6.4.2] Hybrid Fitness = alpha * NormalizedElo + (1-alpha) * Net_Fuzzy_Score
        # (alpha = 0.5). Không dùng Elo thô cộng Fuzzy vì Elo 1200 sẽ lấn át hoàn toàn.
        for h in hypotheses:
            report = reports.get(h["hypothesis_id"])
            fuzzy = 0.0
            if report:
                fuzzy = float(report.prolog_scan_result.get("net_fuzzy_score", 0.0))
            h["net_fuzzy_score"] = fuzzy

        self._assign_hybrid_fitness(hypotheses)

        ranked = sorted(hypotheses, key=lambda x: x["hybrid_fitness"], reverse=True)
        # Agent Evolver: lai 2 cá thể tốt nhất, xóa conflict_trace
        if len(ranked) >= 2:
            child = await self._agent_evolver(ranked[0], ranked[1], reports)
            if child:
                ranked.append(child)
                await self.bb.add_hypothesis(child)
        return ranked

    async def _judge_match(self, h1: Dict[str, Any], h2: Dict[str, Any],
                           reports: Dict[str, DualCritiqueReport], role: str,
                           preferred_model: Optional[str]) -> Optional[str]:
        judge_sys = (
            "You are an Impartial Elo Judge. Compare two hypotheses using the full review "
            "transcripts (practical critique, theoretical risks, fuzzy Prolog conflict_trace). "
            "Output strictly JSON: {'winner_id': '...', 'rationale_vi': '...'}. "
            f"{VI_LANGUAGE_TAIL}"
        )

        def review_block(h: Dict[str, Any]) -> Dict[str, Any]:
            r = reports.get(h["hypothesis_id"])
            return {
                "hypothesis_id": h["hypothesis_id"],
                "title_vi": h.get("hypothesis_title_vi", ""),
                "mechanism_vi": h.get("mechanism_vi", "")[:1500],
                "empirical_flaws": r.empirical_flaws if r else [],
                "theoretical_risks": r.theoretical_risks if r else [],
                "conflict_trace": (r.prolog_scan_result.get("conflict_trace", []) if r else []),
                "net_fuzzy_score": (r.prolog_scan_result.get("net_fuzzy_score", 0.0) if r else 0.0),
            }

        user_prompt = (
            f"Contender A: {json.dumps(review_block(h1), ensure_ascii=False)}\n\n"
            f"Contender B: {json.dumps(review_block(h2), ensure_ascii=False)}\n\n"
            f"Return winner_id strictly as '{h1['hypothesis_id']}' or '{h2['hypothesis_id']}'."
        )
        try:
            raw = await self.router.call_worker(
                role, judge_sys, user_prompt, preferred_model=preferred_model, temperature=0.2
            )
            data = extract_clean_json(raw)
            winner = data.get("winner_id")
            return winner if winner in (h1["hypothesis_id"], h2["hypothesis_id"]) else None
        except Exception:
            return None

    @staticmethod
    def _update_elo(h1: Dict[str, Any], h2: Dict[str, Any], score_a: float, k_factor: float = 32.0):
        ra = float(h1.get("elo_rating", 1200.0))
        rb = float(h2.get("elo_rating", 1200.0))
        expected_a = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
        expected_b = 1.0 / (1.0 + 10.0 ** ((ra - rb) / 400.0))
        h1["elo_rating"] = round(ra + k_factor * (score_a - expected_a), 2)
        h2["elo_rating"] = round(rb + k_factor * ((1.0 - score_a) - expected_b), 2)

    @staticmethod
    def _assign_hybrid_fitness(hypotheses: List[Dict[str, Any]], alpha: float = 0.5):
        """[DT §6.4.2] Gán Hybrid Fitness chuẩn hóa cho toàn bộ quần thể."""
        if not hypotheses:
            return
        for h in hypotheses:
            h["net_fuzzy_score"] = float(h.get("net_fuzzy_score", 0.0) or 0.0)
        elo_values = [float(h.get("elo_rating", 1200.0)) for h in hypotheses]
        elo_min, elo_max = min(elo_values), max(elo_values)
        elo_span = elo_max - elo_min
        for h in hypotheses:
            norm_elo = (
                (float(h.get("elo_rating", 1200.0)) - elo_min) / (elo_span + 1e-9)
                if elo_span > 0 else 0.0
            )
            h["hybrid_fitness"] = round(
                alpha * norm_elo + (1.0 - alpha) * h["net_fuzzy_score"], 4
            )

    async def _agent_evolver(self, parent_a: Dict[str, Any], parent_b: Dict[str, Any],
                             reports: Dict[str, DualCritiqueReport]) -> Optional[Dict[str, Any]]:
        evolver_sys = (
            "You are an Evolutionary Synthesis Engine (Agent Evolver). "
            "Combine the strengths of Parent A and Parent B into one dominant hybrid, "
            "and eliminate every conflict_trace detected by the Fuzzy Prolog scanner. "
            "Output strictly JSON with keys: hypothesis_title_vi, mechanism_vi, prolog_predicates, "
            "practical_critic_search_queries, sandbox_forced_params, assumptions. "
            f"{VI_LANGUAGE_TAIL}"
        )
        conflicts = []
        for p in (parent_a, parent_b):
            r = reports.get(p["hypothesis_id"])
            if r:
                conflicts.extend(r.prolog_scan_result.get("conflict_trace", []))
        user_prompt = (
            f"PARENT A: {json.dumps(parent_a, ensure_ascii=False)[:4000]}\n\n"
            f"PARENT B: {json.dumps(parent_b, ensure_ascii=False)[:4000]}\n\n"
            f"CONFLICT_TRACE TO ELIMINATE: {json.dumps(conflicts, ensure_ascii=False)}"
        )
        try:
            raw = await self.router.call_worker("worker", evolver_sys, user_prompt, temperature=0.5)
            data = extract_clean_json(raw)
        except Exception:
            return None
        if not data:
            return None

        gen = int(self.bb.state.get("generation", 1)) if hasattr(self.bb, "state") else 1
        child = {
            "hypothesis_id": f"gen_{gen}_hybrid_crossover",
            "hypothesis_title_vi": data.get("hypothesis_title_vi", "Hybrid Crossover"),
            "mechanism_vi": data.get("mechanism_vi", ""),
            "prolog_predicates": data.get("prolog_predicates", {}),
            "practical_critic_search_queries": data.get("practical_critic_search_queries", []),
            "sandbox_forced_params": data.get("sandbox_forced_params", {}),
            "assumptions": data.get("assumptions", []),
            "parent_ids": [parent_a["hypothesis_id"], parent_b["hypothesis_id"]],
            "elo_rating": 1200.0,
            "status": "ALIVE",
            "generation": gen,
        }
        return child

    # ---------------------------------------------------------
    # STAGE 6 — Sandbox + RepairTicket + HMAC
    # ---------------------------------------------------------
    async def stage6_sandbox_rewrite(
        self, task_id: str, hyp: Dict[str, Any],
        report: Optional[DualCritiqueReport], cluster: IngestedClusterSchema,
        parameter_bounds: Optional[List[MetricBoundSpec]] = None,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        """
        [DT §6.5] Đưa hypothesis dẫn đầu vào UniversalParameterForcingSandbox.
        Attempt 1 fail -> Attempt 2 đổi model + inject stderr (Recursive Test-Time Scaling).
        Sau 2 lần fail -> DEAD_BRANCH + ghi vào Negative Memory.
        """
        hyp_id = hyp["hypothesis_id"]
        bounds: List[MetricBoundSpec] = []
        if report is not None:
            bounds.extend(report.enforced_sandbox_bounds)
        for b in parameter_bounds or []:
            # Cho phép cả MetricBoundSpec lẫn dict thô từ MCP/CLI.
            bounds.append(b if isinstance(b, MetricBoundSpec) else MetricBoundSpec(**b))

        repair_ticket = {
            "task_id": task_id,
            "hypothesis_id": hyp_id,
            "metric_bounds": [b.model_dump() for b in bounds],
            "empirical_flaws": (report.empirical_flaws if report else []),
            "theoretical_risks": (report.theoretical_risks if report else []),
            "conflict_trace": (
                report.prolog_scan_result.get("conflict_trace", []) if report else []
            ),
            "sandbox_limits": {"timeout_seconds": 8.0, "private_dir": f"/tmp/sandbox/{task_id}/{hyp_id}/"},
        }

        worker_sys = (
            "You are a Parameter Forcing Engineer inside a Universal Sandbox. "
            "Rewrite the hypothesis mechanism to satisfy every MetricBoundSpec in the RepairTicket. "
            "Write Python code (standard library only) that simulates the mechanism and prints, "
            "as the last stdout line, a single JSON string: "
            '{"computed_metrics": {...}, "passed": true, "all_bounds_satisfied": true}. '
            "Output strictly JSON with keys: repaired_mechanism_vi, updated_facts, python_simulation_code. "
            f"{VI_LANGUAGE_TAIL}"
        )

        stderr_trace: List[str] = []
        models = self._available_models()
        used_model: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            # Attempt 2: đổi model và nhồi stderr của Attempt 1 vào System Prompt [DT §6.5.2]
            if attempt == 1:
                preferred = models[0] if models else None
            else:
                alternatives = [m for m in models if m != used_model]
                preferred = alternatives[0] if alternatives else None
            used_model = preferred

            system_prompt = worker_sys
            if attempt > 1:
                system_prompt = (
                    f"{worker_sys}\n\n[RECURSIVE DEBUG — STDOUT/STDERR ATTEMPT {attempt - 1}]\n"
                    f"{json.dumps(stderr_trace, ensure_ascii=False)}"
                )

            user_prompt = (
                f"REPAIR_TICKET: {json.dumps(repair_ticket, ensure_ascii=False)}\n"
                f"HYPOTHESIS: {json.dumps(hyp, ensure_ascii=False)}\n"
                f"ONTOLOGY DOMAIN: {getattr(getattr(cluster, 'induced_ontology', None), 'domain_tag', 'general')}\n"
                f"ATTEMPT {attempt}/{max_attempts}."
            )
            try:
                raw = await self.router.call_worker(
                    "sandbox_worker", system_prompt, user_prompt,
                    preferred_model=preferred, temperature=0.3,
                )
            except Exception as e:
                stderr_trace.append(f"LLM call failed: {e}")
                continue

            data = extract_clean_json(raw)
            code = data.get("python_simulation_code", "")
            if not code:
                stderr_trace.append("Worker did not return python_simulation_code")
                continue

            exec_res = await sandbox_runner.run_code(task_id, hyp_id, code)
            bounds_ok = self._verify_universal_bounds(exec_res.get("stdout", ""), bounds)
            success = exec_res.get("exit_code") == 0 and bounds_ok

            if not success:
                stderr_trace.append(json.dumps({
                    "attempt": attempt,
                    "stderr": exec_res.get("stderr", "")[:2000],
                    "computed_metrics": exec_res.get("computed_metrics", {}),
                    "bounds": [b.model_dump() for b in bounds],
                }, ensure_ascii=False))
                continue

            # Cấp HMAC-SHA256 receipt + đóng dấu verified_* = 0.95
            attestation = attest_sandbox_result(
                hyp_id, exec_res.get("computed_metrics", {}), bounds_ok, exec_res.get("stdout", "")
            )
            clean_updated = sanitize_and_attest_facts(
                data.get("updated_facts", {}), valid_receipt=False
            )
            hyp["updated_facts"] = {**clean_updated, **attestation["verified_facts"]}
            hyp["mechanism_vi"] = data.get("repaired_mechanism_vi", hyp.get("mechanism_vi", ""))
            hyp["status"] = "VIABLE"

            await sandbox_runner.cleanup(task_id, hyp_id)
            return {
                "status": "VIABLE",
                "hypothesis": hyp,
                "attempts": attempt,
                "sandbox_certificate": {
                    "receipt": attestation["receipt"],
                    "computed_metrics": exec_res.get("computed_metrics", {}),
                    "bounds_satisfied": True,
                },
                "repair_ticket": repair_ticket,
                "stderr_trace": stderr_trace,
            }

        # DEAD_BRANCH sau 2 lần thất bại + ghi Negative Memory [DT §6.5.2/3]
        hyp["status"] = "DEAD_BRANCH"
        await self.bb.add_negative_constraint({
            "constraint_id": f"sandbox_{hyp_id}",
            "conflict_predicates": list((hyp.get("prolog_predicates") or {}).keys()),
            "lesson_vi": f"Nhánh {hyp_id} không vượt qua Parameter Forcing sau {max_attempts} lần.",
        })
        await sandbox_runner.cleanup(task_id, hyp_id)
        return {
            "status": "DEAD_BRANCH",
            "hypothesis": hyp,
            "attempts": max_attempts,
            "sandbox_certificate": None,
            "repair_ticket": repair_ticket,
            "stderr_trace": stderr_trace,
        }

    # ---------------------------------------------------------
    # STAGE 7 — JSON Schema Standardization + Pareto export
    # ---------------------------------------------------------
    async def stage7_export(
        self, task_id: str, domain_tag: str,
        viable: List[Dict[str, Any]], dead: List[Dict[str, Any]],
    ) -> FinalOrchestratorOutput:
        """
        [DT §5 Phase 7] Đóng gói các giải pháp qua kiểm định thành JSON Schema chuẩn:
        Elo score, Fuzzy Logic score, Sandbox certificate, error inference trace.
        """
        frontier = []
        for item in viable:
            hyp = item["hypothesis"]
            elo_score = float(hyp.get("elo_rating", 1200.0))
            fuzzy_score = float(hyp.get("net_fuzzy_score", 0.0))
            stored_fitness = hyp.get("hybrid_fitness")
            frontier.append({
                "hypothesis_id": hyp["hypothesis_id"],
                "hypothesis_title_vi": hyp.get("hypothesis_title_vi", ""),
                "mechanism_vi": hyp.get("mechanism_vi", ""),
                "elo_score": elo_score,
                "fuzzy_logic_score": fuzzy_score,
                "hybrid_fitness": (
                    round(float(stored_fitness), 4)
                    if stored_fitness is not None else None
                ),
                "sandbox_certificate": item.get("sandbox_certificate"),
                "error_inference_trace": item.get("stderr_trace", []),
                "updated_facts": hyp.get("updated_facts", {}),
            })

        # Nếu Stage 7 được gọi độc lập (không qua Stage 5), vẫn xuất đúng
        # công thức Hybrid Fitness chuẩn hóa thay vì Elo thô + Fuzzy.
        elo_values = [e["elo_score"] for e in frontier]
        elo_min = min(elo_values) if elo_values else 0.0
        elo_max = max(elo_values) if elo_values else 0.0
        elo_span = elo_max - elo_min
        for entry in frontier:
            if entry["hybrid_fitness"] is None:
                norm_elo = (
                    (entry["elo_score"] - elo_min) / (elo_span + 1e-9)
                    if elo_span > 0 else 0.0
                )
                entry["hybrid_fitness"] = round(
                    0.5 * norm_elo + 0.5 * entry["fuzzy_logic_score"], 4
                )

        compiler = NegativeMemoryCompiler()
        constraints = await self.bb.get_negative_constraints()
        compiled = compiler.compile_to_long_term(domain_tag, constraints)

        from app.schemas.universal_orchestrator_schema import DynamicRuleSpec
        return FinalOrchestratorOutput(
            task_id=task_id,
            domain_tag=domain_tag,
            viable_pareto_frontier=self._pareto_frontier(frontier),
            rejected_dead_branches=[
                {
                    "hypothesis_id": d["hypothesis"]["hypothesis_id"],
                    "reason_vi": d["hypothesis"].get("status", "DEAD_BRANCH"),
                    "attempts": d.get("attempts", 0),
                }
                for d in dead
            ],
            newly_compiled_negative_rules=[
                DynamicRuleSpec(
                    rule_id=r["rule_id"],
                    weight=float(r["weight"]),
                    conditions=list(r["conditions"]),
                    description_vi=r.get("description_vi", ""),
                )
                for r in compiled
            ],
        )

    @staticmethod
    def _pareto_frontier(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Lọc Pareto: không bị trội bởi cá thể khác trên cả Elo lẫn Fuzzy score."""
        frontier = []
        for i, a in enumerate(entries):
            dominated = False
            for j, b in enumerate(entries):
                if i == j:
                    continue
                if (b["elo_score"] >= a["elo_score"]
                        and b["fuzzy_logic_score"] >= a["fuzzy_logic_score"]
                        and (b["elo_score"] > a["elo_score"]
                             or b["fuzzy_logic_score"] > a["fuzzy_logic_score"])):
                    dominated = True
                    break
            if not dominated:
                frontier.append(a)
        return frontier

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------
    def _verify_universal_bounds(self, stdout_str: str, bounds: List[MetricBoundSpec]) -> bool:
        """[§10] Kiểm tra phổ quát mọi tham số định lượng Sandbox in ra."""
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

    def _available_models(self) -> List[str]:
        endpoints = getattr(self.router, "endpoints", []) or []
        models: List[str] = []
        for e in endpoints:
            name = getattr(e, "model", None)
            if name and name not in models:
                models.append(name)
        return models

    def _pick_judge_models(self, count: int = 2) -> List[str]:
        """Chọn >= 2 model khác nhau, loại các model đang được Worker sử dụng."""
        endpoints = getattr(self.router, "endpoints", []) or []
        worker_models = set(getattr(self.router, "_worker_models_in_use", set()))
        models: List[str] = []
        for e in endpoints:
            name = getattr(e, "model", None)
            if not name or name in worker_models or name in models:
                continue
            if getattr(e, "available", lambda: True)():
                models.append(name)
            if len(models) >= count:
                break
        if len(models) < count:
            # Fallback: chấp nhận mọi model còn sống (vẫn khác model Judge khác)
            for e in endpoints:
                name = getattr(e, "model", None)
                if name and name not in models:
                    models.append(name)
                if len(models) >= count:
                    break
        return models

    # ---------------------------------------------------------
    # Orchestrator tổng hợp 7 stage
    # ---------------------------------------------------------
    async def run(
        self, query: str, domain_tag: str, raw_sources: List[Dict[str, Any]],
        parameter_bounds: Optional[List[Dict[str, Any]]] = None,
    ) -> FinalOrchestratorOutput:
        """Chạy trọn 7 stage; dùng cho MCP tool execute_universal_orchestrator."""
        task_id = f"task_{abs(hash(query)) % 10**8}"
        await self.bb.set_state("generation", 1)

        # Stage 1
        cluster = await self.stage1_ingest_and_structure(query, raw_sources, domain_tag)

        # Stage 2 (đã gồm Proximity) + Stage 3 độc lập
        hypotheses = await self.stage2_workers_gen(query, cluster)
        hypotheses = await self.stage3_embedding_proximity(hypotheses)

        # Stage 4 — chạy song song cho toàn bộ hypothesis
        reports_list = await asyncio.gather(
            *[self.stage4_run_dual_critics_and_prolog(h, cluster) for h in hypotheses],
            return_exceptions=True,
        )
        reports: Dict[str, DualCritiqueReport] = {}
        for rep in reports_list:
            if isinstance(rep, DualCritiqueReport):
                reports[rep.hypothesis_id] = rep

        # Stage 5
        ranked = await self.stage5_elo_tournament(hypotheses, reports)

        # Ưu tiên cá thể lai gen_1_hybrid_crossover làm winner vào Sandbox
        crossovers = [h for h in ranked if h["hypothesis_id"].endswith("_hybrid_crossover")]
        leader = crossovers[-1] if crossovers else (ranked[0] if ranked else None)
        if leader is None:
            raise RuntimeError("Không có hypothesis nào sống sót sau Stage 2.")

        # Review checkpoint cho winner nếu chưa có hồ sơ (ví dụ cá thể crossover mới sinh)
        leader_report = reports.get(leader["hypothesis_id"])
        if leader_report is None:
            try:
                leader_report = await self.stage4_run_dual_critics_and_prolog(leader, cluster)
                reports[leader["hypothesis_id"]] = leader_report
                leader["net_fuzzy_score"] = float(
                    leader_report.prolog_scan_result.get("net_fuzzy_score", 0.0)
                )
            except Exception:
                leader_report = None

        # Cá thể lai vừa sinh chưa có điểm; tính lại Fitness trên toàn quần thể
        # (bố mẹ + con) sau khi đã có Net_Fuzzy_Score của winner.
        self._assign_hybrid_fitness(ranked)

        bounds_specs = [
            b if isinstance(b, MetricBoundSpec) else MetricBoundSpec(**b)
            for b in (parameter_bounds or [])
        ]

        # Stage 6
        sandbox_result = await self.stage6_sandbox_rewrite(
            task_id, leader, leader_report, cluster, bounds_specs
        )

        viable = [sandbox_result] if sandbox_result["status"] == "VIABLE" else []
        dead = [sandbox_result] if sandbox_result["status"] != "VIABLE" else []

        # Stage 7
        return await self.stage7_export(task_id, domain_tag, viable, dead)
