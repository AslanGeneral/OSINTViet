"""Sample macro-news ingestor plugin.

The plugin returns raw article dictionaries so Stage 1 can pass them through
``filter_and_ingest_news``. Call ``ingest_and_store`` when direct persistence is
needed (for example from a CLI or a scheduled job).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.plugin_registry import hub
from app.services.embedding_engine import embedding_engine

ArticleFetcher = Callable[
    [httpx.AsyncClient, str],
    Awaitable[Optional[Dict[str, Any]]],
]


class MacroVietnamNewsIngestor:
    """Fetch and normalize articles from a configured list of public URLs."""

    def __init__(
        self,
        fetcher: Optional[ArticleFetcher] = None,
        engine: Any = None,
    ) -> None:
        self._fetcher = fetcher or self._fetch_article
        self._engine = engine or embedding_engine

    @staticmethod
    def _normalize_sources(
        crawl_sources: Optional[Sequence[str] | str],
    ) -> List[str]:
        if crawl_sources is None:
            raw = settings.MACRO_NEWS_SOURCES
            return [item.strip() for item in raw.split(",") if item.strip()]
        if isinstance(crawl_sources, str):
            return [
                item.strip()
                for item in crawl_sources.split(",")
                if item.strip()
            ]
        return [str(item).strip() for item in crawl_sources if str(item).strip()]

    async def ingest(
        self,
        query: str,
        crawl_sources: Optional[Sequence[str] | str] = None,
    ) -> List[Dict[str, Any]]:
        """Return raw article dictionaries without writing to storage."""
        sources = self._normalize_sources(crawl_sources)
        if not sources:
            return []

        limit = max(1, int(settings.MACRO_NEWS_MAX_ARTICLES))
        articles: List[Dict[str, Any]] = []
        headers = {
            "User-Agent": (
                "OSINTViet/2.0 (+https://github.com/AslanGeneral/OSINTViet)"
            )
        }
        timeout = httpx.Timeout(settings.EMBEDDING_HTTP_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for source_url in sources:
                try:
                    article = await self._fetcher(client, source_url)
                except Exception as exc:
                    print(
                        f"[MacroNewsIngestor] Bỏ qua '{source_url}': {exc}"
                    )
                    continue
                if not isinstance(article, dict):
                    continue
                article.setdefault("query", query)
                articles.append(article)
                if len(articles) >= limit:
                    break
        return articles

    async def ingest_and_store(
        self,
        query: str,
        crawl_sources: Optional[Sequence[str] | str] = None,
        domain_tag: str = "general",
        domain_statement: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch raw articles and feed accepted items into the embedding engine."""
        raw_articles = await self.ingest(query, crawl_sources=crawl_sources)
        if not raw_articles:
            return []
        return await self._engine.filter_and_ingest_news(
            raw_articles,
            domain_statement=domain_statement,
            domain_tag=domain_tag,
        )

    @staticmethod
    async def _fetch_article(
        client: httpx.AsyncClient,
        url: str,
    ) -> Optional[Dict[str, Any]]:
        response = await client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title = ""
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()
        if not title and soup.h1:
            title = soup.h1.get_text(" ", strip=True)
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)

        body = soup.find("article") or soup.find("main") or soup.body
        if body is None:
            return None
        paragraphs = [
            paragraph.get_text(" ", strip=True)
            for paragraph in body.find_all("p")
        ]
        content = "\n".join(
            paragraph for paragraph in paragraphs if paragraph
        ).strip()
        if not title or len(content) < 80:
            return None

        source = urlparse(url).netloc or "unknown"
        return {
            "title": title,
            "summary": content[:500],
            "source": source,
            "link": url,
            "raw_content": content[:20000],
            "cleaned_content": content[:20000],
        }


_ingestor = MacroVietnamNewsIngestor()


@hub.register_ingestor("macro_vietnam_news")
async def macro_vietnam_news_ingestor(
    query: str,
    crawl_sources: Optional[Sequence[str] | str] = None,
) -> List[Dict[str, Any]]:
    """Plugin entrypoint used by Stage 1; returns raw articles."""
    return await _ingestor.ingest(query, crawl_sources=crawl_sources)


async def ingest_macro_news_and_store(
    query: str,
    crawl_sources: Optional[Sequence[str] | str] = None,
    domain_tag: str = "general",
    domain_statement: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Convenience helper that also persists accepted articles."""
    return await _ingestor.ingest_and_store(
        query,
        crawl_sources=crawl_sources,
        domain_tag=domain_tag,
        domain_statement=domain_statement,
    )


__all__ = [
    "ArticleFetcher",
    "MacroVietnamNewsIngestor",
    "ingest_macro_news_and_store",
    "macro_vietnam_news_ingestor",
]
