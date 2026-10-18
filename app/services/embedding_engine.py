import httpx
import numpy as np
import chromadb
from typing import List, Dict, Optional
from app.core.config import settings

class EmbeddingGemmaEngine:
    def __init__(self, collection_name: str = "macro_articles"):
        self.ollama_url = settings.LOCAL_OLLAMA_URL
        self.model_name = "embeddinggemma-2"
        
        # Persistent storage ngay trên disk của Linux Mint
        self.chroma_client = chromadb.PersistentClient(path="./data/chroma_db")
        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"} # Tối ưu cosine distance
        )
        
        # Vector neo dùng để lọc rác kinh tế vĩ mô
        self.macro_anchor_text = (
            "Kinh tế vĩ mô, lạm phát, chỉ số giá tiêu dùng CPI, lãi suất điều hành, "
            "tỷ giá ngoại hối, ngân sách nhà nước, chi phí sinh hoạt gia đình, giá xăng dầu."
        )
        self._anchor_embedding: Optional[List[float]] = None

    async def get_embedding(self, text: str, truncate_dim: int = 256) -> List[float]:
        """
        Gọi Ollama endpoint /api/embed để lấy vector của EmbeddingGemma 2.
        Ứng dụng Matryoshka Representation Learning (MRL) cắt ngắn về 256 chiều.
        """
        url = f"{self.ollama_url}/api/embed"
        payload = {
            "model": self.model_name,
            "input": text
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(url, json=payload)
                res.raise_for_status()
                # Format trả về của Ollama: {"embeddings": [[...]]}
                raw_emb = res.json()["embeddings"][0]
                
                # MRL Truncation: Cắt lấy 256 chiều đầu tiên và L2-Normalize
                vec = np.array(raw_emb[:truncate_dim], dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                return vec.tolist()
            except Exception as e:
                raise RuntimeError(f"Lỗi embedding Ollama: {str(e)}")

    async def is_macro_relevant(self, text: str, threshold: float = 0.55) -> bool:
        """
        Semantic Filter: Đo Cosine Similarity với vector neo.
        Loại bỏ tin tức rác trước khi đẩy sang LLM phân tích.
        """
        if self._anchor_embedding is None:
            self._anchor_embedding = await self.get_embedding(self.macro_anchor_text)
            
        doc_emb = await self.get_embedding(text[:1000]) # Lấy 1000 ký tự đầu để lọc nhanh
        
        # Cosine similarity = dot product (vì cả 2 vector đã được L2-normalized)
        sim = float(np.dot(self._anchor_embedding, doc_emb))
        return sim >= threshold

    async def index_article(self, article_id: str, text: str, metadata: Dict):
        """Lưu bài báo và vector vào ChromaDB"""
        vector = await self.get_embedding(text)
        self.collection.add(
            ids=[article_id],
            embeddings=[vector],
            documents=[text[:2000]], # Lưu preview văn bản
            metadatas=[metadata]
        )

    async def semantic_search(self, query: str, top_k: int = 3) -> List[Dict]:
        """Truy xuất thông tin ngữ nghĩa phục vụ RAG cho Reading Room"""
        query_vec = await self.get_embedding(query)
        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=top_k
        )
        
        parsed_results = []
        if results["ids"] and len(results["ids"][0]) > 0:
            for i in range(len(results["ids"][0])):
                parsed_results.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] # Cosine distance
                })
        return parsed_results

embedding_engine = EmbeddingGemmaEngine()