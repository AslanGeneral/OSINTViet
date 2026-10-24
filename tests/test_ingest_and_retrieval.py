#!/usr/bin/env python3
"""Standalone end-to-end verification for the v2.0 embedding engine.

Run:
    .venv/bin/python tests/test_ingest_and_retrieval.py

The deterministic checks use a mock Ollama transport, so they do not need a
network. Cohere and the real local Ollama are tested additionally when they are
configured/available.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence

# Allow `python tests/test_ingest_and_retrieval.py` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb
import httpx
import numpy as np
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core.config import settings
from app.core.plugin_registry import hub
from app.models.article import Article, utc_now
from app.plugins import load_plugins
from app.plugins.ingestors.macro_news_ingestor import MacroVietnamNewsIngestor
from app.services.embedding_engine import EmbeddingFilterEngine

VECTOR_DIM = 512
_SPAM_MARKERS = (
    "quảng cáo",
    "khuyến mãi",
    "trúng thưởng",
    "mua ngay",
    "giảm giá sốc",
)
_POSITIVE_MARKERS = (
    "lạm phát",
    "gdp",
    "lãi suất",
    "xăng",
    "cpi",
    "chính sách",
    "ngân hàng",
    "kinh tế",
)


def _fake_embedding(text: str) -> List[float]:
    """Deterministic 512-d vector; the engine must slice it to 256-d."""
    normalized = text.lower()
    vector = np.zeros(VECTOR_DIM, dtype=np.float32)

    if any(marker in normalized for marker in _SPAM_MARKERS):
        vector[0], vector[1] = 0.05, 0.99
    elif any(marker in normalized for marker in _POSITIVE_MARKERS):
        vector[0], vector[1] = 0.95, 0.08
    else:
        vector[0], vector[1] = 0.55, 0.35

    # Give each article a small orthogonal component so MMR has diversity.
    if "bài " in normalized:
        component_index = 16 + (sum(ord(char) for char in normalized) % 12)
        vector[component_index] = 0.55

    # Values beyond the MRL dimension must be discarded by the engine.
    vector[300] = 0.70
    return vector.tolist()


def _fake_ollama_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/embed":
        payload = json.loads(request.content.decode("utf-8"))
        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        return httpx.Response(
            200,
            json={"embeddings": [_fake_embedding(str(text)) for text in inputs]},
        )
    if request.url.path == "/api/embeddings":
        payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"embedding": _fake_embedding(str(payload.get("prompt", "")))},
        )
    return httpx.Response(404, json={"error": "not found"})


def _assert_unit_vector(vector: Sequence[float]) -> None:
    assert len(vector) == 256, f"Expected 256 dimensions, got {len(vector)}"
    norm = float(np.linalg.norm(np.asarray(vector, dtype=np.float32)))
    assert math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5), norm


async def _check_real_providers() -> None:
    """Verify real providers only when they are configured/available."""
    if settings.COHERE_API_KEY.strip():
        engine = EmbeddingFilterEngine(
            provider="cohere",
            chroma_client=chromadb.EphemeralClient(),
            collection_name=f"cohere_check_{uuid.uuid4().hex}",
            persist_to_db=False,
        )
        try:
            vectors = await engine.get_embeddings_batch(
                ["Lạm phát CPI Việt Nam đang được kiểm chứng."],
                input_type="search_document",
            )
            assert vectors and vectors[0] is not None
            _assert_unit_vector(vectors[0])
            print("[provider] Cohere Embed v5 Pro: OK (256d)")
        finally:
            await engine.close()
    else:
        print("[provider] Cohere: SKIP (COHERE_API_KEY chưa cấu hình)")

    tags_url = f"{settings.LOCAL_OLLAMA_URL.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(tags_url)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
    except Exception as exc:
        print(f"[provider] EmbeddingGemma 2: SKIP (Ollama không sẵn sàng: {exc})")
        return

    engine = EmbeddingFilterEngine(
        provider="embeddinggemma",
        chroma_client=chromadb.EphemeralClient(),
        collection_name=f"gemma_check_{uuid.uuid4().hex}",
        persist_to_db=False,
    )
    try:
        vectors = await engine.get_embeddings_batch(
            ["Lạm phát CPI Việt Nam đang được kiểm chứng."],
            input_type="search_document",
        )
        if not vectors or vectors[0] is None:
            print(
                "[provider] EmbeddingGemma 2: SKIP "
                "(Ollama chưa có model hoặc trả về lỗi)"
            )
            return
        _assert_unit_vector(vectors[0])
        print("[provider] EmbeddingGemma 2 local: OK (256d)")
    finally:
        await engine.close()


async def _check_cohere_transport_contract() -> None:
    """Validate Cohere v2 request shape and 256-d normalization offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/embed"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "embed-v5.0-pro"
        assert payload["input_type"] == "search_document"
        assert payload["output_dimension"] == 256
        assert payload["embedding_types"] == ["float"]
        assert len(payload["texts"]) <= 96
        return httpx.Response(
            200,
            json={
                "embeddings": {
                    "float": [
                        _fake_embedding(str(text))
                        for text in payload["texts"]
                    ]
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, timeout=10.0
    ) as http_client:
        engine = EmbeddingFilterEngine(
            provider="cohere",
            cohere_api_key="test-key",
            http_client=http_client,
            chroma_client=chromadb.EphemeralClient(),
            collection_name=f"cohere_transport_{uuid.uuid4().hex}",
            persist_to_db=False,
        )
        vectors = await engine.get_embeddings_batch(
            [f"văn bản kiểm tra số {index}" for index in range(100)],
            input_type="search_document",
        )
        assert len(vectors) == 100
        for vector in vectors:
            assert vector is not None
            _assert_unit_vector(vector)
        await engine.close()
    print("[provider] Cohere v2 transport contract + 96-batch split: OK")


async def _check_database_storage() -> None:
    with tempfile.TemporaryDirectory() as temporary_dir:
        database_url = f"sqlite+aiosqlite:///{temporary_dir}/test.db"
        test_engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        try:
            async with test_engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with session_factory() as session:
                session.add(
                    Article(
                        id="article-test-1",
                        title="Lạm phát CPI tăng 3.2%",
                        source="unit-test",
                        url="https://example.test/article-1",
                        raw_content="Nội dung thô",
                        cleaned_content="Nội dung đã làm sạch",
                        relevance_score=0.81,
                        ingested_at=utc_now(),
                        domain_tag="macro_econ",
                    )
                )
                await session.commit()
            async with session_factory() as session:
                stored = await session.get(Article, "article-test-1")
                assert stored is not None
                assert stored.title == "Lạm phát CPI tăng 3.2%"
                assert stored.domain_tag == "macro_econ"
            print("[storage] SQLite Article model: OK")
        finally:
            await test_engine.dispose()


async def _check_engine_end_to_end() -> None:
    transport = httpx.MockTransport(_fake_ollama_handler)
    async with httpx.AsyncClient(transport=transport, timeout=10.0) as http_client:
        engine = EmbeddingFilterEngine(
            provider="embeddinggemma",
            chroma_client=chromadb.EphemeralClient(),
            http_client=http_client,
            collection_name=f"ingest_check_{uuid.uuid4().hex}",
            persist_to_db=False,
        )
        engine.configure_domain_anchors("kinh tế vĩ mô Việt Nam")

        vector = (
            await engine.get_embeddings_batch(
                ["Một câu kiểm tra embedding."],
                input_type="search_document",
            )
        )[0]
        assert vector is not None
        _assert_unit_vector(vector)
        print("[embedding] MRL truncation + L2 normalization: OK")

        # 3.2% và 4.8% chỉ cho CV=0.20; thêm 9.0% để CV vượt ngưỡng 0.35
        # đúng theo công thức sigma/mu đã quy định.
        articles: List[Dict[str, Any]] = [
            {
                "title": "Bài 1: Lạm phát CPI tháng 9 tăng 3.2%",
                "summary": (
                    "Ngân hàng Nhà nước theo dõi áp lực giá cả và điều hành "
                    "chính sách tiền tệ thận trọng."
                ),
                "source": "macro-test",
                "link": "https://example.test/bai-1",
            },
            {
                "title": "Bài 2: GDP quý III tăng 6.8%",
                "summary": (
                    "Tăng trưởng kinh tế phục hồi nhờ sản xuất và xuất khẩu; "
                    "một số dự báo lạm phát lên tới 9.0%."
                ),
                "source": "macro-test",
                "link": "https://example.test/bai-2",
            },
            {
                "title": "Bài 3: Lãi suất điều hành được giữ ổn định",
                "summary": (
                    "Ngân hàng Nhà nước giữ lãi suất để hỗ trợ thanh khoản "
                    "và kiểm soát lạm phát."
                ),
                "source": "macro-test",
                "link": "https://example.test/bai-3",
            },
            {
                "title": "Bài 4: Giá xăng RON95 tăng nhẹ",
                "summary": (
                    "Giá xăng chịu áp lực từ địa chính trị; CPI có thể tăng "
                    "4.8% nếu giá dầu neo cao."
                ),
                "source": "macro-test",
                "link": "https://example.test/bai-4",
            },
            {
                "title": "Khuyến mãi thẻ tín dụng giảm giá sốc",
                "summary": (
                    "Mua ngay để nhận quà tặng, trúng thưởng và ưu đãi quảng cáo "
                    "không liên quan đến kinh tế vĩ mô."
                ),
                "source": "advertisement",
                "link": "https://example.test/spam",
            },
        ]

        accepted = await engine.filter_and_ingest_news(articles)
        accepted_titles = [str(item.get("title")) for item in accepted]
        assert len(accepted) == 4, accepted_titles
        assert all("Khuyến mãi" not in title for title in accepted_titles)
        print("[ingestion] Contrastive calibration dropped spam: OK")

        hits = await engine.semantic_search("lạm phát CPI", top_k=4)
        assert hits
        assert "search_status" not in hits[0] or not str(
            hits[0]["search_status"]
        ).startswith("INCORRECT")
        statuses = {
            hit.get("search_verification", {}).get("crag_state")
            for hit in hits
        }
        assert statuses & {"CORRECT", "AMBIGUOUS"}, statuses
        assert len(hits) >= 2, len(hits)
        vectors = [
            _fake_embedding(str(hit.get("document", "")))
            for hit in hits
        ]
        pairwise = []
        for left in range(len(vectors)):
            for right in range(left + 1, len(vectors)):
                a = np.asarray(vectors[left][:256], dtype=np.float32)
                b = np.asarray(vectors[right][:256], dtype=np.float32)
                a /= np.linalg.norm(a)
                b /= np.linalg.norm(b)
                pairwise.append(float(np.dot(a, b)))
        assert not pairwise or max(pairwise) < 0.95, pairwise
        print("[search] CRAG + temporal MMR diversity: OK")

        warning = hits[0]["search_verification"][
            "numerical_divergence_warning"
        ]
        assert warning["flag"] == "DATA_CONFLICT_WARNING", warning
        assert "percent" in warning["spread"], warning
        assert warning["spread"]["percent"]["unit_vi"] == "%", warning
        print("[search] DATA_CONFLICT_WARNING numeric spread: OK")

        legacy_indexed = await engine.index_article(
            article_id="legacy-scraper-1",
            text=(
                "Bài viết kiểm tra API cũ về lạm phát và chính sách tiền tệ "
                "của Ngân hàng Nhà nước."
            ),
            metadata={
                "title": "Bài viết kiểm tra API cũ",
                "url": "https://example.test/legacy-scraper-1",
                "source": "legacy-scraper",
                "score": 0.7,
            },
        )
        assert legacy_indexed is True
        legacy_hits = await engine.semantic_search("lạm phát", top_k=8)
        legacy_ids = [
            hit.get("metadata", {}).get("article_id")
            for hit in legacy_hits
        ]
        assert "legacy-scraper-1" in legacy_ids, legacy_ids
        print("[embedding] legacy index_article API: OK")

        pairs = await engine.check_hypothesis_proximity(
            [
                "Gói hỗ trợ tài khóa cho doanh nghiệp nhỏ và vừa.",
                "Gói hỗ trợ tài khóa cho doanh nghiệp nhỏ và vừa.",
            ]
        )
        assert pairs, "Phải phát hiện cặp hypothesis gần trùng."
        assert pairs[0][2] >= 0.86, pairs
        print("[proximity] Duplicate hypothesis pair caught: OK")

        await engine.close()


async def _check_ambiguous_recompose_branch() -> None:
    transport = httpx.MockTransport(_fake_ollama_handler)
    async with httpx.AsyncClient(
        transport=transport, timeout=10.0
    ) as http_client:
        engine = EmbeddingFilterEngine(
            provider="embeddinggemma",
            http_client=http_client,
            chroma_client=chromadb.EphemeralClient(),
            collection_name=f"ambiguous_check_{uuid.uuid4().hex}",
            persist_to_db=False,
        )
        query_vector = (
            await engine.get_embeddings_batch(
                ["lạm phát"], input_type="search_query"
            )
        )[0]
        assert query_vector is not None
        # Cosine 0.50 with the query: below CORRECT, above INCORRECT.
        document_vector = np.zeros(256, dtype=np.float32)
        document_vector[:] = query_vector * 0.5
        document_vector[2] = math.sqrt(0.75)
        document_vector /= np.linalg.norm(document_vector)
        engine.collection.upsert(
            ids=["ambiguous-1"],
            embeddings=[document_vector.tolist()],
            metadatas=[
                {
                    "article_id": "ambiguous-1",
                    "title": "Lạm phát và chính sách",
                    "source": "test",
                    "link": "https://example.test/ambiguous",
                    "domain_tag": "macro_econ",
                    "score": 0.5,
                    "ingested_at_ts": 0.0,
                }
            ],
            documents=[
                "Lạm phát CPI tăng nhẹ. Chính sách tiền tệ được theo dõi sát sao."
            ],
        )
        hits = await engine.semantic_search("lạm phát", top_k=1)
        assert hits
        verification = hits[0]["search_verification"]
        assert verification["crag_state"] == "AMBIGUOUS", verification
        assert verification["decomposed"] is True, verification
        print("[search] CRAG AMBIGUOUS decompose-recompose: OK")
        await engine.close()


async def _check_low_confidence_branch() -> None:
    transport = httpx.MockTransport(_fake_ollama_handler)
    async with httpx.AsyncClient(
        transport=transport, timeout=10.0
    ) as http_client:
        engine = EmbeddingFilterEngine(
            provider="embeddinggemma",
            http_client=http_client,
            chroma_client=chromadb.EphemeralClient(),
            collection_name=f"low_conf_check_{uuid.uuid4().hex}",
            persist_to_db=False,
        )
        document_vector = np.zeros(256, dtype=np.float32)
        document_vector[2] = 1.0
        engine.collection.upsert(
            ids=["orthogonal-1"],
            embeddings=[document_vector.tolist()],
            metadatas=[
                {
                    "article_id": "orthogonal-1",
                    "title": "Nội dung không liên quan",
                    "source": "test",
                    "domain_tag": "general",
                    "score": 0.0,
                    "ingested_at_ts": 0.0,
                }
            ],
            documents=["Nội dung hoàn toàn không liên quan đến truy vấn."],
        )
        hits = await engine.semantic_search("lạm phát", top_k=1)
        assert hits
        assert hits[0]["search_status"] == "INCORRECT_LOW_CONFIDENCE", hits
        assert hits[0]["action"] == "REQUIRE_EXTERNAL_WEB_SEARCH", hits
        assert hits[0]["document"] == "", hits
        assert hits[0]["message_vi"], hits

        empty_hits = await engine.semantic_search("   ", top_k=1)
        assert empty_hits[0]["search_status"] == "INCORRECT_EMPTY_QUERY"

        class FailingCollection:
            def count(self) -> int:
                raise RuntimeError("vector store offline")

        engine._collection = FailingCollection()
        store_hits = await engine.semantic_search("lạm phát", top_k=1)
        assert (
            store_hits[0]["search_status"]
            == "INCORRECT_VECTOR_STORE_ERROR"
        ), store_hits
        assert store_hits[0]["document"] == "", store_hits
        print("[search] CRAG low-confidence + store guard: OK")
        await engine.close()


async def _check_ollama_legacy_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/api/embeddings":
            payload = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "embedding": _fake_embedding(
                        str(payload.get("prompt", ""))
                    )
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, timeout=10.0
    ) as http_client:
        engine = EmbeddingFilterEngine(
            provider="embeddinggemma",
            http_client=http_client,
            chroma_client=chromadb.EphemeralClient(),
            collection_name=f"legacy_ollama_{uuid.uuid4().hex}",
            persist_to_db=False,
        )
        vectors = await engine.get_embeddings_batch(
            ["Kiểm tra endpoint Ollama cũ."],
            input_type="search_document",
        )
        assert len(vectors) == 1 and vectors[0] is not None
        _assert_unit_vector(vectors[0])
        await engine.close()
    print("[provider] Ollama legacy /api/embeddings fallback: OK")


async def _check_plugin_ingestion() -> None:
    async def fetcher(
        client: httpx.AsyncClient, url: str
    ) -> Dict[str, Any]:
        del client
        return {
            "title": "Bài mẫu: Lạm phát CPI tăng 3.2%",
            "summary": "Dữ liệu vĩ mô có nguồn kiểm chứng.",
            "source": "test-source",
            "link": url,
            "raw_content": "Nội dung bài mẫu về lạm phát và chính sách.",
            "cleaned_content": "Nội dung bài mẫu về lạm phát và chính sách.",
        }

    raw_ingestor = MacroVietnamNewsIngestor(fetcher=fetcher)
    raw_articles = await raw_ingestor.ingest(
        "lạm phát", ["https://example.test/plugin-1"]
    )
    assert len(raw_articles) == 1, raw_articles
    assert raw_articles[0]["link"] == "https://example.test/plugin-1"
    assert raw_articles[0]["query"] == "lạm phát"

    class StubEngine:
        def __init__(self) -> None:
            self.called = False
            self.domain_statement: Any = None
            self.domain_tag: Any = None

        async def filter_and_ingest_news(
            self,
            raw_articles: List[Dict[str, Any]],
            domain_statement: Any = None,
            domain_tag: str = "general",
        ) -> List[Dict[str, Any]]:
            self.called = True
            self.domain_statement = domain_statement
            self.domain_tag = domain_tag
            return raw_articles

    stub_engine = StubEngine()
    storing_ingestor = MacroVietnamNewsIngestor(
        fetcher=fetcher, engine=stub_engine
    )
    accepted = await storing_ingestor.ingest_and_store(
        "lạm phát",
        ["https://example.test/plugin-2"],
        domain_tag="macro_econ",
        domain_statement="kinh tế vĩ mô Việt Nam",
    )
    assert stub_engine.called is True
    assert stub_engine.domain_tag == "macro_econ"
    assert stub_engine.domain_statement == "kinh tế vĩ mô Việt Nam"
    assert len(accepted) == 1, accepted
    print("[plugin] macro_vietnam_news raw + store handoff: OK")


async def _check_plugin_registration() -> None:
    load_plugins()
    assert "macro_vietnam_news" in hub.data_ingestors
    print("[plugin] macro_vietnam_news registered: OK")


async def _run_all_checks() -> None:
    print("=== OSINTViet embedding engine v2.0 verification ===")
    await _check_real_providers()
    await _check_cohere_transport_contract()
    await _check_ollama_legacy_fallback()
    await _check_database_storage()
    await _check_engine_end_to_end()
    await _check_ambiguous_recompose_branch()
    await _check_low_confidence_branch()
    await _check_plugin_ingestion()
    await _check_plugin_registration()
    print("ALL_CHECKS_PASSED")


def test_ingest_and_retrieval() -> None:
    """Pytest-compatible wrapper around the standalone verification script."""
    asyncio.run(_run_all_checks())


if __name__ == "__main__":
    asyncio.run(_run_all_checks())
