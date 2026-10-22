# ============================================================
# app/services/embedding_engine.py — bản nâng cấp Embed v5 Pro [DT §11]
# - Cohere embed-v5.0-pro Batching (96 texts / call ~300ms)
# - Giữ Fallback Local Ollama khi thiếu COHERE_API_KEY
# - 4 thuật toán kiểm lỗi Search (§6.1.3):
#     ALG1 Contrastive Pos-Neg Calibration (thuần constant như code §11 — hàng 6)
#     ALG2 MMR (+ decay e^{-γΔt} theo DT — hàng 7)
#     ALG3 CRAG Tri-State + Decompose-Recompose đầy đủ (hàng 8); ngưỡng từ config (hàng 4)
#     ALG4 Numerical Divergence: CV > 0.35, unit [%, tỷ, VND, USD] (hàng 9)
# - Proximity: ngưỡng > 0.85 [DT §13.1 Proximity Agent] (hàng 1)
# - Cờ lỗi Search & cờ xung đột số liệu theo code §11 (hàng 10, 11)
# ============================================================
import os
import re
import httpx
import chromadb
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.core.config import settings


class EmbeddingFilterEngine:
    def __init__(self, mrl_dim: int = 256):
        self.mrl_dim = mrl_dim

        # Cấu hình Cohere Embed v5 Pro (ưu tiên vì giải tỏa tải tài nguyên về máy cục bộ)
        self.cohere_api_key = os.getenv("COHERE_API_KEY", "")
        self.cohere_model = os.getenv("EMBED_MODEL", "embed-v5.0-pro")  # Hoặc "embed-v5.0-fast"
        self.ollama_url = f"{settings.LOCAL_OLLAMA_URL}/api/embeddings"

        # Dùng chung một AsyncClient để gom Connection Pooling, tránh nghẽn socket
        self.http_client = httpx.AsyncClient(timeout=20.0)
        self.chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        self.collection = self.chroma_client.get_or_create_collection(
            name="osint_macro_knowledge",
            metadata={"hnsw:space": "cosine"}
        )

        # ALGORITHM 1: Bộ cặp anchor (tĩnh mặc định) — Positive (vĩ mô) &
        # Negative (Rác/PR/Quảng cáo) cho Calibration.
        # [Appendix A hàng 15 - quyết định user "Cả hai"]: dùng chuỗi tĩnh tiếng Việt
        # làm mặc định (như code §11); domain khác macro có thể GHI ĐỀ bằng:
        #   await embedding_engine.set_anchors_from_domain(pos_text, neg_text)
        self.pos_anchor_text = (
            "Chính sách tiền tệ, lãi suất ngân hàng trung ương, lạm phát CPI, tỷ giá ngoại hối, "
            "chuỗi cung ứng năng lượng, xăng dầu, thuế xuất nhập khẩu, GDP kinh tế vĩ mô, "
            "nợ công, giải ngân đầu tư công."
        )
        self.neg_anchor_text = (
            "Quảng cáo khuyến mãi thẻ tín dụng, tin thời trang và giải trí, tin mua bán nhà đất, "
            "bài PR xây dựng hình ảnh cá nhân và tin đồn thiếu số liệu thống kê."
        )
        self._pos_anchor_vec: Optional[np.ndarray] = None
        self._neg_anchor_vec: Optional[np.ndarray] = None

    # ---------- [Appendix A hàng 15] Anchor động theo domain ----------
    async def set_anchors_from_domain(self, positive_text: str, negative_text: str):
        """
        [DT §6.1.3 - hàng 15] Ghi đè anchor tĩnh macro bằng anchor sinh
        theo mô tả bài toán (search_query). Gọi ở Stage 1 khi domain ≠ macro.
        """
        self.pos_anchor_text = positive_text
        self.neg_anchor_text = negative_text
        self._pos_anchor_vec = None
        self._neg_anchor_vec = None
        await self._ensure_anchors()

    def _truncate_and_normalize(self, vec: List[float]) -> np.ndarray:
        """Cắt chiều Matryoshka (256-dim) và chuẩn hóa L2"""
        arr = np.array(vec[: self.mrl_dim], dtype=np.float32)
        norm = np.linalg.norm(arr)
        return arr / norm if norm > 1e-12 else arr

    async def get_embeddings_batch(
        self, texts: List[str], input_type: str = "search_document"
    ) -> List[Optional[np.ndarray]]:
        """
        Gọi API batch Cohere Embed v5 Pro (embed 96 bài trong 1 call ~300ms).
        Tự động Fallback về Local Ollama nếu không có COHERE_API_KEY.
        input_type: 'search_document' (khi nạp database) | 'search_query' (khi truy vấn)
        [DT §6.1.1] BẮT BUỘC phân loại input_type, lẫn lộn sẽ mất khoảng 8–12% độ chính xác.
        """
        if not texts:
            return []

        # Priority 1: Dùng Cohere Embed v5 Pro (hoàn toàn dỡ tải embedding khỏi máy cục bộ)
        # Batch cố định 96 text / call theo code §11 để không vượt giới hạn API.
        if self.cohere_api_key:
            try:
                batched_vectors: List[np.ndarray] = []
                for start in range(0, len(texts), 96):
                    chunk = texts[start:start + 96]
                    res = await self.http_client.post(
                        "https://api.cohere.com/v2/embed",
                        headers={
                            "Authorization": f"Bearer {self.cohere_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.cohere_model,
                            "texts": chunk,
                            "input_type": input_type,
                            "embedding_types": ["float"],
                            "output_dimension": self.mrl_dim,  # Hỗ trợ native 256-dim trên Embed v5 Pro!
                        },
                    )
                    if res.status_code != 200:
                        raise RuntimeError(f"Cohere HTTP {res.status_code}")
                    raw_list = res.json()["embeddings"]["float"]
                    batched_vectors.extend(self._truncate_and_normalize(v) for v in raw_list)
                return batched_vectors
            except Exception as e:
                print(f"[Embed v5 Pro Warning] Lỗi gọi Cloud API, chuyển sang Fallback: {e}")

        # Priority 2 (Fallback): chạy Local Ollama nếu không có network/API Key
        results = []
        for t in texts:
            try:
                res = await self.http_client.post(
                    self.ollama_url, json={"model": settings.EMBEDDING_MODEL, "prompt": t}
                )
                if res.status_code == 200 and "embedding" in res.json():
                    results.append(self._truncate_and_normalize(res.json()["embedding"]))
                else:
                    results.append(None)
            except Exception:
                results.append(None)
        return results

    async def _ensure_anchors(self):
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            vecs = await self.get_embeddings_batch(
                [self.pos_anchor_text, self.neg_anchor_text], input_type="search_query"
            )
            if len(vecs) == 2 and vecs[0] is not None and vecs[1] is not None:
                self._pos_anchor_vec, self._neg_anchor_vec = vecs[0], vecs[1]

    async def filter_and_ingest_news(
        self, raw_articles: List[Dict[str, Any]], similarity_threshold: float = 0.45
    ) -> List[Dict[str, Any]]:
        """
        Nạp tin tức siêu nhanh dùng Batch Embedding + lọc nhiễu
        Positive/Negative Contrastive Calibration.
        [Appendix A hàng 5 - quyết định user]: ngưỡng >= 0.45 (theo code cũ).
        """
        await self._ensure_anchors()
        if not raw_articles or self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            return []

        texts_to_embed = [f"{a.get('title', '')}. {a.get('summary', '')}" for a in raw_articles]

        # Bắn Batch một lần cho toàn bộ danh sách bài!
        doc_vecs = await self.get_embeddings_batch(texts_to_embed, input_type="search_document")

        accepted, ids, embeddings, metadatas, documents = [], [], [], [], []
        now_ts = datetime.utcnow().timestamp()

        for item, doc_vec, full_text in zip(raw_articles, doc_vecs, texts_to_embed):
            if doc_vec is None or len(full_text.strip()) < 20:
                continue

            # ALGORITHM 1: Tính Contrast Margin (gần anchor Positive & xa anchor Negative)
            pos_sim = float(np.dot(self._pos_anchor_vec, doc_vec))
            neg_sim = float(np.dot(self._neg_anchor_vec, doc_vec))
            # Hằng số giữ nguyên như code §11 [Appendix A hàng 6]
            calibrated_score = pos_sim - 0.5 * max(0.0, neg_sim - 0.30)

            if calibrated_score >= similarity_threshold:
                doc_id = f"news_{abs(hash(item.get('link', full_text)))}"
                item["macro_relevance_score"] = round(calibrated_score, 4)
                accepted.append(item)
                ids.append(doc_id)
                embeddings.append(doc_vec.tolist())
                metadatas.append({
                    "source": item.get("source", "unknown"),
                    "link": item.get("link", ""),
                    "score": round(calibrated_score, 4),
                    "ingested_at_ts": now_ts,
                })
                documents.append(full_text)

        if ids:
            self.collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        return accepted

    async def is_macro_relevant(self, text: str, threshold: float = 0.55) -> bool:
        """
        Tương thích scraper cũ (macro_news_scraper): chấm điểm hiệu chuẩn
        Positive/Negative anchors trước khi index.
        """
        await self._ensure_anchors()
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            return False
        vecs = await self.get_embeddings_batch([text], input_type="search_document")
        if not vecs or vecs[0] is None:
            return False
        doc_vec = vecs[0]
        pos_sim = float(np.dot(self._pos_anchor_vec, doc_vec))
        neg_sim = float(np.dot(self._neg_anchor_vec, doc_vec))
        calibrated = pos_sim - 0.5 * max(0.0, neg_sim - 0.30)
        return calibrated >= threshold

    async def index_article(self, article_id: str, text: str,
                            metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Tương thích scraper cũ: nạp trực tiếp một bài viết vào ChromaDB."""
        vecs = await self.get_embeddings_batch([text], input_type="search_document")
        if not vecs or vecs[0] is None:
            return False
        meta = dict(metadata or {})
        meta.setdefault("ingested_at_ts", datetime.utcnow().timestamp())
        self.collection.upsert(
            ids=[article_id],
            embeddings=[vecs[0].tolist()],
            metadatas=[meta],
            documents=[text],
        )
        return True

    def _detect_numerical_conflict(self, docs: List[str]) -> Dict[str, Any]:
        """
        ALGORITHM 4: Kiểm tra xung đột số liệu giữa các tài liệu được tìm thấy.
        [Appendix A hàng 9 - quyết định user]: trích cả 4 nhóm unit [%, tỷ, VND, USD];
        CV > settings.NUMERICAL_CV_THRESHOLD (mặc định 0.35) → đánh dấu
        has_numerical_divergence (tên cờ theo code §11 — hàng 11).
        """
        unit_values: Dict[str, List[float]] = {"%": [], "tỷ": [], "VND": [], "USD": []}
        unit_patterns = {
            "%": r"(\d+(?:[\.,]\d+)?)\s*%",
            "tỷ": r"(\d+(?:[\.,]\d+)?)\s*tỷ",
            "VND": r"(\d+(?:[\.,]\d+)?)\s*VND",
            "USD": r"(\d+(?:[\.,]\d+)?)\s*USD",
        }
        for d in docs:
            for unit, pattern in unit_patterns.items():
                for m in re.findall(pattern, d):
                    try:
                        unit_values[unit].append(float(m.replace(",", ".")))
                    except (TypeError, ValueError):
                        continue

        conflicts: Dict[str, Any] = {"has_numerical_divergence": False}
        for unit, values in unit_values.items():
            if len(values) >= 2:
                mean_val = np.mean(values)
                std_val = np.std(values)
                cv = (std_val / mean_val) if mean_val > 1e-6 else 0.0
                if cv > settings.NUMERICAL_CV_THRESHOLD:
                    conflicts = {
                        "has_numerical_divergence": True,
                        f"extracted_{unit}_rates": values,
                        f"cv_{unit}": round(float(cv), 3),
                    }
        return conflicts

    def _decay_for_doc(self, meta: Dict[str, Any], now_ts: float) -> float:
        """Hệ số giảm thời gian e^{-γΔt} applied to content in MMR [Appendix A hàng 7]"""
        try:
            dt_days = max(0.0, (now_ts - float(meta.get("ingested_at_ts", now_ts))) / 86400.0)
        except (TypeError, ValueError):
            dt_days = 0.0
        return float(np.exp(-settings.MMR_TIME_DECAY_GAMMA * dt_days))

    async def semantic_search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Search kèm kiểm lỗi (CRAG Tri-State + MMR chống trùng + Decompose-Recompose)
        - [Appendix A hàng 4]: ngưỡng CRAG đọc từ settings (operator tượng trưng per DT §6.1.3)
        - [Appendix A hàng 7]: MMR có thêm decay e^{-γΔt} theo DT (β = settings.MMR_BETA)
        - [Appendix A hàng 8]: AMBIGUOUS → Decompose-Recompose đầy đủ theo DT desc
        """
        q_vecs = await self.get_embeddings_batch([query], input_type="search_query")
        if not q_vecs or q_vecs[0] is None:
            return []
        q_vec = q_vecs[0]

        # Lấy tập mở rộng ứng cử viên (top_k * 3) cùng vector để chạy MMR
        res = self.collection.query(
            query_embeddings=[q_vec.tolist()],
            n_results=max(top_k * 3, 6),
            include=["documents", "metadatas", "distances", "embeddings"],
        )
        if not res["documents"] or not res["documents"][0]:
            # Trả cờ lỗi Search [Code §11 - hàng 10]
            return [{"search_status": "INCORRECT_EMPTY_DB",
                     "document": "", "action": "TRIGGER_LIVE_SCRAPER"}]

        cand_docs = res["documents"][0]
        cand_metas = res["metadatas"][0]
        cand_vecs = [np.array(v, dtype=np.float32) for v in res["embeddings"][0]]

        # ALGORITHM 2: Chọn đa dạng top_k dùng MMR (Maximum Marginal Relevance)
        # để tránh hiện tượng trùng lặp nguồn (Echo Chamber).
        # MMR(d) = beta * e^{-gamma*dt} * cos(q, doc) - (1 - beta) * max cos(doc, selected)
        selected_indices = []
        beta = settings.MMR_BETA
        now_ts = datetime.utcnow().timestamp()
        for _ in range(min(top_k, len(cand_docs))):
            best_idx, best_mmr = -1, -1e9
            for idx in range(len(cand_docs)):
                if idx in selected_indices:
                    continue
                sim_q = float(np.dot(q_vec, cand_vecs[idx]))
                decay = self._decay_for_doc(cand_metas[idx], now_ts)
                max_sim_selected = max(
                    [float(np.dot(cand_vecs[idx], cand_vecs[j])) for j in selected_indices],
                    default=0.0,
                )
                mmr_score = beta * decay * sim_q - (1.0 - beta) * max_sim_selected
                if mmr_score > best_mmr:
                    best_mmr, best_idx = mmr_score, idx
            if best_idx != -1:
                selected_indices.append(best_idx)

        # ALGORITHM 3: Xét 3 trạng thái CRAG (CORRECT / AMBIGUOUS / INCORRECT)
        max_sim = max([float(np.dot(q_vec, cand_vecs[i])) for i in selected_indices], default=0.0)
        if max_sim >= settings.CRAG_CORRECT_THRESHOLD:
            crag_state = "CORRECT"
        elif max_sim >= settings.CRAG_AMBIGUOUS_THRESHOLD:
            crag_state = "AMBIGUOUS"
        else:
            # Toàn bộ kết quả dưới ngưỡng AMBIGUOUS -> báo lỗi Search để Agent không tự phóng tác!
            return [{
                "search_status": "INCORRECT_LOW_CONFIDENCE",
                "max_similarity": round(max_sim, 4),
                "document": "SEARCH ERROR WARNING: The current data repository does not have sufficient evidence for this query.",
                "action": "REQUIRE_EXTERNAL_WEB_SEARCH",
            }]

        # Kiểm tra xung đột số liệu giữa các bài báo được chọn
        selected_texts = [cand_docs[i] for i in selected_indices]
        num_check = self._detect_numerical_conflict(selected_texts)

        # ALG3 mở rộng [DT desc - hàng 8]: AMBIGUOUS → chạy Decompose-Recompose:
        # tách tài liệu thành câu, embed batch, chỉ giữ câu đạt ngưỡng dot(q, sentence).
        recomposed = [(cand_docs[i], i) for i in selected_indices]
        if crag_state == "AMBIGUOUS":
            keep_threshold = settings.DECOMPOSE_KEEP_THRESHOLD
            kept_pairs = []
            for idx in selected_indices:
                doc_text = cand_docs[idx]
                sentences = [s.strip() for s in re.split(r'(?<=[.!?;])\s+', doc_text) if s.strip()]
                if not sentences:
                    continue
                sent_vecs = await self.get_embeddings_batch(sentences, input_type="search_document")
                kept_sentences = [
                    s for s, sv in zip(sentences, sent_vecs)
                    if sv is not None and float(np.dot(q_vec, sv)) >= keep_threshold
                ]
                if kept_sentences:
                    kept_pairs.append((" ".join(kept_sentences), idx))
            if kept_pairs:
                # Tái kết hợp (Recompose): giữ lại duy nhất câu đạt điều kiện
                recomposed = kept_pairs
            else:
                # Cắt hết câu không đạt → coi như không đủ tuyến bằng chứng
                return [{
                    "search_status": "INCORRECT_LOW_CONFIDENCE",
                    "max_similarity": round(max_sim, 4),
                    "document": "SEARCH ERROR WARNING: The current data repository does not have sufficient evidence for this query.",
                    "action": "REQUIRE_EXTERNAL_WEB_SEARCH",
                }]

        final_hits = []
        for doc_text, idx in recomposed:
            sim_val = round(float(np.dot(q_vec, cand_vecs[idx])), 4)
            final_hits.append({
                "document": doc_text,
                "metadata": cand_metas[idx],
                "similarity": sim_val,
                "search_verification": {
                    "crag_state": crag_state,
                    "numerical_divergence_warning": num_check,
                },
            })
        return final_hits

    async def check_hypothesis_proximity(self, hypotheses_texts: List[str], threshold: float = 0.85) -> List[tuple]:
        """
        ['Proximity Agent' mechanism from the Google DeepMind AI Co-Scientist paper]:
        Tìm các cặp hypothesis có ý tưởng trùng lặp trước khi đưa vào vòng Elo.
        [Appendix A hàng 1 - quyết định user]: gộp khi similarity > 0.85 [DT §13.1]
        """
        vecs = await self.get_embeddings_batch(hypotheses_texts, input_type="search_document")
        redundant_pairs = []
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                if vecs[i] is not None and vecs[j] is not None:
                    sim = float(np.dot(vecs[i], vecs[j]))
                    if sim > threshold:
                        redundant_pairs.append((i, j, round(sim, 4)))
        return redundant_pairs

    async def close(self):
        await self.http_client.aclose()


embedding_engine = EmbeddingFilterEngine(mrl_dim=256)
