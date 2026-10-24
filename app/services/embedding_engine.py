"""Dual-provider embedding, ingestion, storage, and semantic-search engine v2.0.

Providers:
- Cohere Embed v5 Pro (cloud, 96 texts per request).
- EmbeddingGemma 2 through a local Ollama server.

Both providers are truncated to 256 dimensions (MRL) and L2-normalized so the
existing ChromaDB collection remains compatible. All code and JSON keys use
English snake_case; Vietnamese is kept for data, queries, and explanations.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import chromadb
import httpx
import numpy as np
from numpy.typing import NDArray

from app.core.config import settings
from app.core.database import init_db, upsert_articles
from app.models.article import Article, utc_now
from app.schemas.workflow_schema import ArticlePayload

Vector = NDArray[np.float32]
ProximityPair = Tuple[int, int, float]
_ALLOWED_INPUT_TYPES = frozenset({"search_document", "search_query"})
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_NUMERICAL_UNIT_RE = re.compile(
    r"(?<!\d)(\d+(?:[\.,]\d+)?)\s*(nghìn\s+tỷ|tỷ|%|USD|VND)",
    re.IGNORECASE,
)
_UNIT_KEY_BY_SURFACE = {
    "%": "percent",
    "tỷ": "billion_vnd",
    "nghìn tỷ": "trillion_vnd",
    "usd": "usd",
    "vnd": "vnd",
}
_UNIT_LABEL_VI = {
    "percent": "%",
    "billion_vnd": "tỷ",
    "trillion_vnd": "nghìn tỷ",
    "usd": "USD",
    "vnd": "VND",
}


class EmbeddingFilterEngine:
    """Dual-provider semantic ingestion and retrieval engine."""

    def __init__(
        self,
        mrl_dim: int = settings.EMBEDDING_DIMENSION,
        provider: Optional[str] = None,
        chroma_client: Any = None,
        http_client: Optional[httpx.AsyncClient] = None,
        collection_name: Optional[str] = None,
        persist_to_db: bool = True,
        cohere_api_key: Optional[str] = None,
    ) -> None:
        if mrl_dim <= 0:
            raise ValueError("mrl_dim phải lớn hơn 0.")

        self.mrl_dim = int(mrl_dim)
        self._configured_provider = str(
            provider or settings.EMBEDDING_PROVIDER
        ).strip().lower()
        if self._configured_provider not in {"auto", "cohere", "embeddinggemma"}:
            raise ValueError(
                "EMBEDDING_PROVIDER phải là 'auto', 'cohere' hoặc 'embeddinggemma'."
            )

        self.cohere_api_key = (
            cohere_api_key
            if cohere_api_key is not None
            else settings.COHERE_API_KEY
        ).strip()
        self.cohere_model = (
            settings.EMBED_MODEL.strip() or settings.COHERE_EMBED_MODEL.strip()
        )
        self.cohere_endpoint = settings.COHERE_EMBED_ENDPOINT
        self.cohere_batch_size = max(
            1, min(96, int(settings.COHERE_EMBED_BATCH_SIZE))
        )
        self.ollama_base_url = settings.LOCAL_OLLAMA_URL.rstrip("/")
        self.ollama_model = settings.EMBEDDING_MODEL
        self.provider = self._resolve_provider()

        self.http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.EMBEDDING_HTTP_TIMEOUT_SECONDS)
        )
        self._owns_http_client = http_client is None
        self._chroma_client = chroma_client
        self._collection_name = (
            collection_name or settings.CHROMA_COLLECTION_NAME
        )
        self._collection: Any = None
        self.persist_to_db = persist_to_db
        self._db_ready = False
        self._last_provider_error: Optional[str] = None
        self._last_search_error: Optional[str] = None

        self.pos_anchor_text = settings.EMBEDDING_POSITIVE_ANCHOR
        self.neg_anchor_text = settings.EMBEDDING_NEGATIVE_ANCHOR
        self._domain_statement: Optional[str] = None
        self._pos_anchor_vec: Optional[Vector] = None
        self._neg_anchor_vec: Optional[Vector] = None
        self._anchor_lock = asyncio.Lock()

        configured_domain = settings.EMBEDDING_DOMAIN_STATEMENT.strip()
        if configured_domain:
            self.configure_domain_anchors(configured_domain)

    # ------------------------------------------------------------------
    # Provider setup
    # ------------------------------------------------------------------
    def _resolve_provider(self) -> str:
        if self._configured_provider == "auto":
            return "cohere" if self.cohere_api_key else "embeddinggemma"
        if self._configured_provider == "cohere" and not self.cohere_api_key:
            raise ValueError(
                "EMBEDDING_PROVIDER=cohere nhưng COHERE_API_KEY đang trống."
            )
        return self._configured_provider

    @property
    def collection(self) -> Any:
        """Lazily open the persistent ChromaDB collection."""
        if self._collection is None:
            if self._chroma_client is None:
                self._chroma_client = chromadb.PersistentClient(
                    path=settings.CHROMA_PERSIST_DIR
                )
            self._collection = self._chroma_client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def provider_status(self) -> Dict[str, Any]:
        """Return a key-free provider snapshot for health checks and tests."""
        return {
            "configured_provider": self._configured_provider,
            "active_provider": self.provider,
            "cohere_model": self.cohere_model,
            "ollama_model": self.ollama_model,
            "mrl_dim": self.mrl_dim,
            "last_provider_error": self._last_provider_error,
            "last_search_error": self._last_search_error,
        }

    # ------------------------------------------------------------------
    # Vector normalization
    # ------------------------------------------------------------------
    def _normalize_vector(self, vector: Sequence[float]) -> Vector:
        """Slice to MRL dimension and L2-normalize; reject incompatible vectors."""
        array = np.asarray(vector, dtype=np.float32)
        if array.ndim != 1:
            raise ValueError("Embedding phải là vector một chiều.")
        if array.size < self.mrl_dim:
            raise ValueError(
                f"Embedding chỉ có {array.size} chiều; cần tối thiểu {self.mrl_dim}."
            )
        array = np.ascontiguousarray(array[: self.mrl_dim], dtype=np.float32)
        norm = float(np.linalg.norm(array))
        if norm <= 1e-12:
            raise ValueError("Embedding có norm bằng 0, không thể chuẩn hóa L2.")
        return array / norm

    @staticmethod
    def _validate_input_type(input_type: str) -> None:
        if input_type not in _ALLOWED_INPUT_TYPES:
            raise ValueError(
                "input_type phải là 'search_document' hoặc 'search_query'."
            )

    # ------------------------------------------------------------------
    # Provider calls
    # ------------------------------------------------------------------
    async def _embed_with_cohere(
        self, texts: Sequence[str], input_type: str
    ) -> List[Vector]:
        vectors: List[Vector] = []
        for start in range(0, len(texts), self.cohere_batch_size):
            chunk = list(texts[start : start + self.cohere_batch_size])
            response = await self.http_client.post(
                self.cohere_endpoint,
                headers={
                    "Authorization": f"Bearer {self.cohere_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.cohere_model,
                    "texts": chunk,
                    "input_type": input_type,
                    "embedding_types": ["float"],
                    "output_dimension": self.mrl_dim,
                },
            )
            if response.status_code != 200:
                body = response.text[:240]
                raise RuntimeError(
                    f"Cohere HTTP {response.status_code}: {body}"
                )

            payload = response.json()
            embeddings = payload.get("embeddings")
            if isinstance(embeddings, dict):
                raw_vectors = embeddings.get("float")
            else:
                raw_vectors = embeddings
            if not isinstance(raw_vectors, list) or len(raw_vectors) != len(chunk):
                raise RuntimeError("Cohere trả về sai số lượng embedding.")

            for raw_vector in raw_vectors:
                vectors.append(self._normalize_vector(raw_vector))
        return vectors

    async def _embed_with_ollama(
        self, texts: Sequence[str], input_type: str
    ) -> List[Optional[Vector]]:
        del input_type  # Ollama không phân biệt query/document; caller vẫn kiểm soát.
        vectors: List[Optional[Vector]] = []
        for start in range(0, len(texts), self.cohere_batch_size):
            chunk = list(texts[start : start + self.cohere_batch_size])
            batch_done = False
            try:
                response = await self.http_client.post(
                    f"{self.ollama_base_url}/api/embed",
                    json={"model": self.ollama_model, "input": chunk},
                )
                if response.status_code == 200:
                    payload = response.json()
                    raw_vectors = payload.get("embeddings")
                    if (
                        isinstance(raw_vectors, list)
                        and len(raw_vectors) == len(chunk)
                    ):
                        for raw_vector in raw_vectors:
                            try:
                                vectors.append(self._normalize_vector(raw_vector))
                            except (TypeError, ValueError) as exc:
                                self._last_provider_error = (
                                    f"Ollama /api/embed vector: {exc}"
                                )
                                vectors.append(None)
                        batch_done = True
                    else:
                        self._last_provider_error = (
                            "Ollama /api/embed trả về sai số lượng vector."
                        )
                else:
                    self._last_provider_error = (
                        f"Ollama /api/embed HTTP {response.status_code}."
                    )
            except Exception as exc:
                self._last_provider_error = f"Ollama /api/embed: {exc}"

            if batch_done:
                continue

            # Tương thích Ollama cũ: mỗi prompt một vector.
            for text in chunk:
                try:
                    response = await self.http_client.post(
                        f"{self.ollama_base_url}/api/embeddings",
                        json={"model": self.ollama_model, "prompt": text},
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        raw_vector = payload.get("embedding")
                        if raw_vector is not None:
                            vectors.append(self._normalize_vector(raw_vector))
                            continue
                        self._last_provider_error = (
                            "Ollama /api/embeddings thiếu trường embedding."
                        )
                    else:
                        self._last_provider_error = (
                            "Ollama /api/embeddings HTTP "
                            f"{response.status_code}."
                        )
                except Exception as exc:
                    self._last_provider_error = f"Ollama /api/embeddings: {exc}"
                vectors.append(None)
        return vectors

    async def get_embeddings_batch(
        self,
        texts: Sequence[str],
        input_type: str = "search_document",
    ) -> List[Optional[Vector]]:
        """Embed texts with the active provider and return 256-d unit vectors."""
        if not texts:
            return []
        self._validate_input_type(input_type)

        if self.provider == "cohere":
            try:
                return await self._embed_with_cohere(texts, input_type)
            except Exception as exc:
                self._last_provider_error = str(exc)
                if self._configured_provider == "cohere":
                    raise RuntimeError(
                        f"Không thể gọi Cohere Embed: {exc}"
                    ) from exc
                print(
                    "[Embedding] Cohere lỗi, chuyển sang Ollama local: "
                    f"{exc}"
                )
                self.provider = "embeddinggemma"
                # Anchor vectors from Cohere live in a different space.
                self._pos_anchor_vec = None
                self._neg_anchor_vec = None

        return await self._embed_with_ollama(texts, input_type)

    # ------------------------------------------------------------------
    # Dynamic anchors
    # ------------------------------------------------------------------
    def configure_domain_anchors(self, domain_statement: str) -> None:
        """Derive positive/negative anchors from a domain statement."""
        statement = domain_statement.strip()
        if not statement:
            raise ValueError("domain_statement không được rỗng.")
        if (
            statement == self._domain_statement
            and self._pos_anchor_vec is not None
            and self._neg_anchor_vec is not None
        ):
            return
        self._domain_statement = statement
        self.pos_anchor_text = (
            f"{statement}. {settings.EMBEDDING_POSITIVE_ANCHOR}"
        )
        self.neg_anchor_text = (
            f"{statement}. {settings.EMBEDDING_NEGATIVE_ANCHOR}"
        )
        self._pos_anchor_vec = None
        self._neg_anchor_vec = None

    async def set_anchors_from_domain(
        self,
        positive_text: str,
        negative_text: str,
    ) -> None:
        """Override anchors explicitly and warm their vectors immediately."""
        if not positive_text.strip() or not negative_text.strip():
            raise ValueError("Anchor positive/negative không được rỗng.")
        self.pos_anchor_text = positive_text.strip()
        self.neg_anchor_text = negative_text.strip()
        self._domain_statement = None
        self._pos_anchor_vec = None
        self._neg_anchor_vec = None
        await self._ensure_anchors()

    async def _ensure_anchors(self) -> None:
        if self._pos_anchor_vec is not None and self._neg_anchor_vec is not None:
            return
        async with self._anchor_lock:
            if self._pos_anchor_vec is not None and self._neg_anchor_vec is not None:
                return
            vectors = await self.get_embeddings_batch(
                [self.pos_anchor_text, self.neg_anchor_text],
                input_type="search_query",
            )
            if (
                len(vectors) == 2
                and vectors[0] is not None
                and vectors[1] is not None
            ):
                self._pos_anchor_vec = vectors[0]
                self._neg_anchor_vec = vectors[1]

    def _contrastive_score(self, document_vector: Vector) -> float:
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            return -1.0
        positive_similarity = float(
            np.dot(self._pos_anchor_vec, document_vector)
        )
        negative_similarity = float(
            np.dot(self._neg_anchor_vec, document_vector)
        )
        return positive_similarity - settings.CONTRASTIVE_NEGATIVE_WEIGHT * max(
            0.0,
            negative_similarity - settings.CONTRASTIVE_NEGATIVE_MARGIN,
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    @staticmethod
    def _article_text(article: Dict[str, Any]) -> str:
        title_value = article.get("title")
        title = str(title_value).strip() if title_value else ""
        candidate_keys = (
            "summary",
            "cleaned_content",
            "content",
            "raw_content",
            "description",
        )
        candidates = [
            article.get(key).strip()
            for key in candidate_keys
            if isinstance(article.get(key), str) and article.get(key).strip()
        ]
        body = next(
            (candidate for candidate in candidates if len(candidate) >= 20),
            candidates[0] if candidates else "",
        )
        if title and body:
            return f"{title}. {body}"
        return title or body

    @staticmethod
    def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Keep ChromaDB metadata scalar and JSON/Chroma-safe."""
        clean: Dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            clean[str(key)] = (
                value
                if isinstance(value, (str, int, float, bool))
                else str(value)
            )
        return clean

    @staticmethod
    def _build_article_id(article: Dict[str, Any], text: str) -> str:
        identity = str(article.get("link") or article.get("url") or "").strip()
        if not identity:
            identity = text
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    async def _persist_articles(
        self,
        articles: Sequence[Dict[str, Any]],
        domain_tag: str,
    ) -> None:
        if not self.persist_to_db or not articles:
            return
        try:
            if not self._db_ready:
                await init_db()
                self._db_ready = True
            ingested_at = utc_now()
            rows: List[Article] = []
            for article in articles:
                text = self._article_text(article)
                raw_content = str(
                    article.get("raw_content")
                    or article.get("content")
                    or text
                )
                cleaned_content = str(
                    article.get("cleaned_content")
                    or article.get("summary")
                    or text
                )
                article_id = str(
                    article.get("article_id")
                    or self._build_article_id(article, text)
                )
                title = str(article.get("title") or "").strip() or text[:120]
                source = str(article.get("source") or "unknown")
                url = str(article.get("link") or article.get("url") or "")
                row_domain_tag = str(
                    article.get("domain_tag") or domain_tag or "general"
                )
                relevance_score = float(
                    article.get("macro_relevance_score") or 0.0
                )
                # The SQLite write input is a Pydantic-validated JSON object,
                # not an ad-hoc dict.  The same object is retained in the row
                # for later recheck and provenance.
                payload = ArticlePayload(
                    article_id=article_id,
                    title=title,
                    source=source,
                    url=url,
                    raw_content=raw_content,
                    cleaned_content=cleaned_content,
                    relevance_score=relevance_score,
                    domain_tag=row_domain_tag,
                    fetched_at=str(article.get("fetched_at") or ""),
                    published_at=(
                        str(article.get("published_at"))
                        if article.get("published_at")
                        else None
                    ),
                    browser=str(article.get("browser") or ""),
                    selection_seed=article.get("selection_seed"),
                )
                rows.append(
                    Article(
                        id=article_id,
                        title=title,
                        source=source,
                        url=url,
                        raw_content=raw_content,
                        cleaned_content=cleaned_content,
                        relevance_score=relevance_score,
                        ingested_at=ingested_at,
                        domain_tag=row_domain_tag,
                        payload_json=payload.model_dump_json(),
                    )
                )
            await upsert_articles(rows)
        except Exception as exc:
            # Vector search must keep working even when SQLite is unavailable.
            print(f"[EmbeddingStorage] Không lưu được Article: {exc}")

    async def filter_and_ingest_news(
        self,
        raw_articles: List[Dict[str, Any]],
        similarity_threshold: float = settings.INGESTION_CONTRASTIVE_THRESHOLD,
        domain_statement: Optional[str] = None,
        domain_tag: str = "general",
    ) -> List[Dict[str, Any]]:
        """Filter noisy articles and persist accepted items to ChromaDB + SQLite."""
        if not raw_articles:
            return []
        if domain_statement:
            self.configure_domain_anchors(domain_statement)
        await self._ensure_anchors()
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            print(
                "[Embedding] Không tạo được anchor; kiểm tra provider "
                f"({self.provider}) và cấu hình Ollama/Cohere."
            )
            return []

        threshold = float(similarity_threshold)
        eligible_articles: List[Dict[str, Any]] = []
        texts: List[str] = []
        for article in raw_articles:
            text = self._article_text(article)
            if len(text.strip()) < 20:
                continue
            eligible_articles.append(article)
            texts.append(text)
        if not texts:
            return []

        document_vectors = await self.get_embeddings_batch(
            texts, input_type="search_document"
        )
        # A Cohere -> Ollama fallback can happen while embedding documents;
        # rebuild anchors in the new provider space before scoring.
        await self._ensure_anchors()
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            print(
                "[Embedding] Không tạo được anchor sau khi embedding tài liệu; "
                "bỏ qua lô tin này."
            )
            return []

        accepted: List[Dict[str, Any]] = []
        ids: List[str] = []
        embeddings: List[List[float]] = []
        metadatas: List[Dict[str, Any]] = []
        documents: List[str] = []
        seen_ids: set[str] = set()
        now_ts = datetime.now(timezone.utc).timestamp()

        for article, document_vector, text in zip(
            eligible_articles, document_vectors, texts
        ):
            if document_vector is None:
                continue
            score = self._contrastive_score(document_vector)
            if score < threshold:
                continue

            article_id = self._build_article_id(article, text)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            accepted_item = dict(article)
            accepted_item["article_id"] = article_id
            accepted_item["macro_relevance_score"] = round(float(score), 4)
            accepted_item["domain_tag"] = str(
                article.get("domain_tag") or domain_tag or "general"
            )
            accepted.append(accepted_item)

            ids.append(article_id)
            embeddings.append(document_vector.tolist())
            metadatas.append(
                self._sanitize_metadata(
                    {
                        "article_id": article_id,
                        "title": str(article.get("title") or "")[:500],
                        "source": str(
                            article.get("source") or "unknown"
                        )[:200],
                        "link": str(
                            article.get("link") or article.get("url") or ""
                        )[:1000],
                        "domain_tag": accepted_item["domain_tag"][:120],
                        "score": round(float(score), 4),
                        "ingested_at_ts": now_ts,
                    }
                )
            )
            documents.append(text)

        if not ids:
            return []

        indexed = True
        try:
            self.collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents,
            )
        except Exception as exc:
            indexed = False
            self._last_search_error = f"Chroma upsert: {exc}"
            print(f"[Embedding] Không ghi được ChromaDB: {exc}")
        await self._persist_articles(accepted, domain_tag=domain_tag)
        return accepted if indexed else []

    async def is_macro_relevant(
        self,
        text: str,
        threshold: float = settings.INGESTION_CONTRASTIVE_THRESHOLD,
    ) -> bool:
        """Score one document with the same contrastive calibration as ingestion."""
        if not text.strip():
            return False
        await self._ensure_anchors()
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            return False
        vectors = await self.get_embeddings_batch(
            [text], input_type="search_document"
        )
        if not vectors or vectors[0] is None:
            return False
        await self._ensure_anchors()
        if self._pos_anchor_vec is None or self._neg_anchor_vec is None:
            return False
        return self._contrastive_score(vectors[0]) >= float(threshold)

    async def index_article(
        self,
        article_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Index one article directly, preserving the legacy scraper API."""
        article_id = str(article_id).strip()
        if not article_id or not text.strip():
            return False
        vectors = await self.get_embeddings_batch(
            [text], input_type="search_document"
        )
        if not vectors or vectors[0] is None:
            return False

        now_ts = datetime.now(timezone.utc).timestamp()
        meta = self._sanitize_metadata(dict(metadata or {}))
        meta.setdefault("article_id", article_id)
        meta.setdefault("ingested_at_ts", now_ts)
        meta.setdefault("score", 0.0)
        meta = self._sanitize_metadata(meta)
        indexed = True
        try:
            self.collection.upsert(
                ids=[article_id],
                embeddings=[vectors[0].tolist()],
                metadatas=[meta],
                documents=[text],
            )
        except Exception as exc:
            indexed = False
            self._last_search_error = f"Chroma upsert: {exc}"
            print(f"[Embedding] Không ghi được ChromaDB: {exc}")

        if self.persist_to_db:
            await self._persist_articles(
                [
                    {
                        "article_id": article_id,
                        "title": str(meta.get("title") or "") or text[:120],
                        "source": str(meta.get("source") or "unknown"),
                        "link": str(meta.get("link") or meta.get("url") or ""),
                        "raw_content": text,
                        "cleaned_content": text,
                        "macro_relevance_score": float(
                            meta.get("score") or 0.0
                        ),
                        "domain_tag": str(
                            meta.get("domain_tag") or "general"
                        ),
                    }
                ],
                domain_tag=str(meta.get("domain_tag") or "general"),
            )
        return indexed

    # ------------------------------------------------------------------
    # Algorithm 4: numerical divergence
    # ------------------------------------------------------------------
    def _detect_numerical_conflict(
        self, docs: Sequence[str]
    ) -> Dict[str, Any]:
        """Detect cross-document numerical spread with coefficient of variation."""
        unit_values: Dict[str, List[float]] = {}
        for document in docs:
            for match in _NUMERICAL_UNIT_RE.finditer(document):
                raw_value = match.group(1).replace(",", ".")
                raw_unit = re.sub(r"\s+", " ", match.group(2)).strip().lower()
                unit = _UNIT_KEY_BY_SURFACE.get(raw_unit)
                if unit is None:
                    continue
                try:
                    value = float(raw_value)
                except ValueError:
                    continue
                unit_values.setdefault(unit, []).append(value)

        spread: Dict[str, Dict[str, Any]] = {}
        for unit, values in unit_values.items():
            if len(values) < 2:
                continue
            mean_value = float(np.mean(values))
            std_value = float(np.std(values))
            coefficient = (
                std_value / mean_value if mean_value > 1e-9 else 0.0
            )
            if coefficient > settings.NUMERICAL_CV_THRESHOLD:
                spread[unit] = {
                    "unit_vi": _UNIT_LABEL_VI[unit],
                    "values": values,
                    "cv": round(coefficient, 3),
                    "mean": round(mean_value, 4),
                    "min": min(values),
                    "max": max(values),
                    "range": round(max(values) - min(values), 4),
                }

        has_conflict = bool(spread)
        result: Dict[str, Any] = {
            "has_numerical_divergence": has_conflict,
            "flag": "DATA_CONFLICT_WARNING" if has_conflict else None,
            "spread": spread,
        }
        # Compatibility with the previous engine's flat fields.
        for unit, details in spread.items():
            result[f"extracted_{unit}_rates"] = details["values"]
            result[f"cv_{unit}"] = details["cv"]
        return result

    # ------------------------------------------------------------------
    # Algorithm 2 helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _decay_for_doc(meta: Dict[str, Any], now_ts: float) -> float:
        try:
            ingested_at = float(meta.get("ingested_at_ts", now_ts))
            delta_days = max(0.0, (now_ts - ingested_at) / 86400.0)
        except (TypeError, ValueError):
            delta_days = 0.0
        return float(np.exp(-settings.MMR_TIME_DECAY_GAMMA * delta_days))

    @staticmethod
    def _search_error(
        status: str,
        max_similarity: Optional[float] = None,
        message_vi: Optional[str] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "search_status": status,
            "action": "REQUIRE_EXTERNAL_WEB_SEARCH",
            # Empty document prevents the orchestrator from treating this
            # operational warning as retrieved evidence.
            "document": "",
            "message_vi": message_vi
            or (
                "CẢNH BÁO: kho dữ liệu hiện tại không đủ bằng chứng đáng tin cậy "
                "cho truy vấn này; cần bổ sung nguồn bên ngoài."
            ),
        }
        if max_similarity is not None:
            result["max_similarity"] = round(float(max_similarity), 4)
        return result

    # ------------------------------------------------------------------
    # Algorithm 2 + 3: temporal MMR and CRAG tri-state
    # ------------------------------------------------------------------
    async def semantic_search(
        self, query: str, top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """Search with temporal MMR, CRAG tri-state, and numerical conflict checks."""
        if not query.strip():
            return [
                self._search_error(
                    "INCORRECT_EMPTY_QUERY",
                    message_vi="Truy vấn rỗng, không thể tìm kiếm ngữ nghĩa.",
                )
            ]
        top_k = max(1, int(top_k))
        query_vectors = await self.get_embeddings_batch(
            [query], input_type="search_query"
        )
        if not query_vectors or query_vectors[0] is None:
            return [
                self._search_error(
                    "INCORRECT_EMBEDDING_UNAVAILABLE",
                    message_vi=(
                        "Không tạo được embedding cho truy vấn; "
                        "cần kiểm tra nhà cung cấp embedding."
                    ),
                )
            ]
        query_vector = query_vectors[0]

        try:
            collection = self.collection
            available = int(collection.count())
            if available == 0:
                return [self._search_error("INCORRECT_EMPTY_DB")]
            result = collection.query(
                query_embeddings=[query_vector.tolist()],
                n_results=min(max(top_k * 3, 6), available),
                include=["documents", "metadatas", "embeddings"],
            )
        except Exception as exc:
            self._last_search_error = f"Chroma search: {exc}"
            print(f"[Embedding] Lỗi truy vấn ChromaDB: {exc}")
            return [
                self._search_error(
                    "INCORRECT_VECTOR_STORE_ERROR",
                    message_vi=(
                        "Không truy cập được kho vector; "
                        "cần kiểm tra ChromaDB và cấu hình collection."
                    ),
                )
            ]
        raw_documents = result.get("documents") or [[]]
        raw_metadatas = result.get("metadatas") or [[]]
        raw_embeddings = result.get("embeddings")
        if (
            not raw_documents
            or not raw_documents[0]
            or raw_embeddings is None
        ):
            return [self._search_error("INCORRECT_EMPTY_DB")]

        metadata_rows: List[Any] = (
            raw_metadatas[0]
            if raw_metadatas and raw_metadatas[0]
            else [None] * len(raw_documents[0])
        )
        embedding_rows: List[Any] = (
            raw_embeddings[0] if raw_embeddings else []
        )
        candidates: List[Tuple[str, Dict[str, Any], Vector]] = []
        seen_documents: set[str] = set()
        for document, metadata, embedding in zip(
            raw_documents[0],
            metadata_rows,
            embedding_rows,
        ):
            if document is None or embedding is None:
                continue
            try:
                vector = self._normalize_vector(embedding)
            except (TypeError, ValueError):
                # Ignore legacy or malformed vectors instead of failing a query.
                continue
            document_text = str(document)
            document_key = re.sub(
                r"\s+", " ", document_text
            ).strip().lower()
            if document_key in seen_documents:
                continue
            seen_documents.add(document_key)
            candidates.append(
                (document_text, dict(metadata or {}), vector)
            )
        if not candidates:
            return [self._search_error("INCORRECT_EMPTY_DB")]

        query_similarities = [
            float(np.dot(query_vector, candidate[2]))
            for candidate in candidates
        ]
        max_similarity = max(query_similarities)

        if max_similarity < settings.CRAG_AMBIGUOUS_THRESHOLD:
            return [
                self._search_error(
                    "INCORRECT_LOW_CONFIDENCE",
                    max_similarity=max_similarity,
                )
            ]

        if max_similarity >= settings.CRAG_CORRECT_THRESHOLD:
            crag_state = "CORRECT"
        else:
            crag_state = "AMBIGUOUS"

        # Temporal MMR.
        selected_indices: List[int] = []
        mmr_scores: Dict[int, float] = {}
        now_ts = datetime.now(timezone.utc).timestamp()
        for _ in range(min(top_k, len(candidates))):
            best_index = -1
            best_score = float("-inf")
            for index, (_, metadata, vector) in enumerate(candidates):
                if index in selected_indices:
                    continue
                decay = self._decay_for_doc(metadata, now_ts)
                similarity_to_selected = max(
                    (
                        float(np.dot(vector, candidates[selected][2]))
                        for selected in selected_indices
                    ),
                    default=0.0,
                )
                mmr_score = (
                    settings.MMR_BETA
                    * decay
                    * query_similarities[index]
                    - (1.0 - settings.MMR_BETA)
                    * similarity_to_selected
                )
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_index = index
            if best_index < 0:
                break
            selected_indices.append(best_index)
            mmr_scores[best_index] = best_score

        selected_texts = [candidates[index][0] for index in selected_indices]
        numerical_warning = self._detect_numerical_conflict(selected_texts)

        # CRAG AMBIGUOUS: decompose to sentences, embed as documents, recompose.
        recomposed: List[Tuple[str, int, float]] = []
        if crag_state == "AMBIGUOUS":
            sentence_records: List[Tuple[int, str]] = []
            for index in selected_indices:
                sentences = [
                    sentence.strip()
                    for sentence in _SENTENCE_SPLIT_RE.split(
                        candidates[index][0]
                    )
                    if sentence.strip()
                ]
                for sentence in sentences:
                    sentence_records.append((index, sentence))

            if sentence_records:
                sentence_vectors = await self.get_embeddings_batch(
                    [record[1] for record in sentence_records],
                    input_type="search_document",
                )
                kept_by_index: Dict[int, List[str]] = {}
                for (index, sentence), sentence_vector in zip(
                    sentence_records, sentence_vectors
                ):
                    if sentence_vector is None:
                        continue
                    if (
                        float(np.dot(query_vector, sentence_vector))
                        >= settings.DECOMPOSE_KEEP_THRESHOLD
                    ):
                        kept_by_index.setdefault(index, []).append(sentence)
                recomposed = [
                    (
                        " ".join(kept_by_index[index]),
                        index,
                        query_similarities[index],
                    )
                    for index in selected_indices
                    if index in kept_by_index
                ]

            if not recomposed:
                return [
                    self._search_error(
                        "INCORRECT_LOW_CONFIDENCE",
                        max_similarity=max_similarity,
                    )
                ]
        else:
            recomposed = [
                (
                    candidates[index][0],
                    index,
                    query_similarities[index],
                )
                for index in selected_indices
            ]

        hits: List[Dict[str, Any]] = []
        for document, index, original_similarity in recomposed:
            hits.append(
                {
                    "document": document,
                    "metadata": candidates[index][1],
                    "similarity": round(float(original_similarity), 4),
                    "mmr_score": round(
                        float(mmr_scores.get(index, 0.0)), 4
                    ),
                    "search_status": crag_state,
                    "search_verification": {
                        "crag_state": crag_state,
                        "max_similarity": round(float(max_similarity), 4),
                        "numerical_divergence_warning": numerical_warning,
                        "data_conflict_warning": numerical_warning["flag"],
                        "decomposed": crag_state == "AMBIGUOUS",
                    },
                }
            )
        return hits

    # ------------------------------------------------------------------
    # Proximity Agent
    # ------------------------------------------------------------------
    async def check_hypothesis_proximity(
        self,
        hypotheses_texts: List[str],
        threshold: float = settings.PROXIMITY_SIMILARITY_THRESHOLD,
    ) -> List[ProximityPair]:
        """Return all hypothesis pairs at or above the mode-collapse threshold."""
        if len(hypotheses_texts) < 2:
            return []
        vectors = await self.get_embeddings_batch(
            hypotheses_texts, input_type="search_document"
        )
        pairs: List[ProximityPair] = []
        for left in range(len(vectors)):
            if vectors[left] is None:
                continue
            for right in range(left + 1, len(vectors)):
                if vectors[right] is None:
                    continue
                similarity = float(
                    np.dot(vectors[left], vectors[right])
                )
                if similarity >= float(threshold):
                    pairs.append(
                        (left, right, round(similarity, 4))
                    )
        return pairs

    async def close(self) -> None:
        """Close the owned HTTP connection pool."""
        if self._owns_http_client:
            await self.http_client.aclose()


embedding_engine = EmbeddingFilterEngine(mrl_dim=settings.EMBEDDING_DIMENSION)

__all__ = [
    "EmbeddingFilterEngine",
    "ProximityPair",
    "Vector",
    "embedding_engine",
]
