"""Live random-economics-article fetcher using headless Chromium.

The workflow never starts from a fixture.  It opens a current economics section
with Playwright, selects a random article link, extracts the article body, and
returns a JSON-serializable ``FetchedArticle`` object.  If a page is paywalled,
blocked, or too short, the fetcher tries another candidate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.core.config import settings
from app.schemas.workflow_schema import FetchedArticle

_ARTICLE_PATH_RE = re.compile(
    r"(?:/|-)"
    r"(?:\d{5,}|[a-z0-9][a-z0-9-]{25,})"
    r"(?:\.html?|\.chn)?$",
    re.IGNORECASE,
)
_SKIP_PATH_PARTS = (
    "/topic/",
    "/tag/",
    "/video",
    "/photo",
    "/infographic",
    "/podcast",
    "/rss",
    "/search",
    "javascript:",
    "mailto:",
)
_NOISE_SELECTORS = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HeadlessEconArticleFetcher:
    """Playwright-based fetcher with random source/article selection."""

    def __init__(
        self,
        sources: Optional[Iterable[str]] = None,
        article_attempts: Optional[int] = None,
        timeout_ms: int = 45_000,
    ) -> None:
        configured = sources or [
            item.strip()
            for item in settings.WORKFLOW_ECON_FETCH_SOURCES.split(",")
            if item.strip()
        ]
        self.sources = list(configured)
        self.article_attempts = max(
            1,
            article_attempts
            or settings.WORKFLOW_ECON_FETCH_ARTICLE_ATTEMPTS,
        )
        self.timeout_ms = timeout_ms
        self._rng = random.SystemRandom()

    async def fetch_random(
        self,
        exclude_urls: Optional[Iterable[str]] = None,
    ) -> FetchedArticle:
        """Fetch one random article, excluding URLs already tried."""
        if not self.sources:
            raise RuntimeError("WORKFLOW_ECON_FETCH_SOURCES is empty")
        excluded = {url.strip() for url in (exclude_urls or ()) if url}
        errors: List[str] = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                ],
            )
            try:
                context = await browser.new_context(
                    locale="vi-VN",
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 900},
                )

                async def _block_assets(route):
                    if route.request.resource_type in {"image", "media", "font"}:
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", _block_assets)
                page = await context.new_page()

                source_order = self.sources[:]
                self._rng.shuffle(source_order)
                for section_url in source_order:
                    try:
                        candidates = await self._candidate_links(
                            page, section_url, excluded
                        )
                    except Exception as exc:
                        errors.append(
                            f"section {section_url}: {type(exc).__name__}: {exc}"
                        )
                        continue
                    if not candidates:
                        errors.append(f"section {section_url}: no candidates")
                        continue

                    self._rng.shuffle(candidates)
                    for article_url in candidates[: self.article_attempts]:
                        if article_url in excluded:
                            continue
                        try:
                            article = await self._extract_article(
                                page, article_url, section_url
                            )
                        except Exception as exc:
                            errors.append(
                                f"article {article_url}: {type(exc).__name__}: {exc}"
                            )
                            excluded.add(article_url)
                            continue
                        if article is not None:
                            return article
                        excluded.add(article_url)

                raise RuntimeError(
                    "Could not fetch a live economics article; attempts: "
                    + " | ".join(errors[-8:])
                )
            finally:
                await browser.close()

    async def _candidate_links(
        self,
        page,
        section_url: str,
        excluded: set[str],
    ) -> List[str]:
        response = await page.goto(
            section_url,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        if response is not None and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        await page.wait_for_timeout(1_200)

        raw_links = await page.eval_on_selector_all(
            "a",
            """els => els.map(e => ({
                href: e.href || '',
                text: (e.innerText || e.textContent || '').trim()
            }))""",
        )

        section_host = urlparse(section_url).netloc.lower()
        candidates: List[str] = []
        seen: set[str] = set()
        for item in raw_links:
            href = str(item.get("href") or "").strip()
            text = str(item.get("text") or "").strip()
            if not href or href in seen or href in excluded:
                continue
            if len(text) < 25:
                continue
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc.lower() != section_host:
                continue
            path = parsed.path or "/"
            if any(part in href.lower() for part in _SKIP_PATH_PARTS):
                continue
            if path in {"", "/"}:
                continue
            # Economics section homepages contain many teaser links; accept
            # article-looking paths (numeric IDs or long slugs) and reject
            # navigation/category pages.
            if not _ARTICLE_PATH_RE.search(path):
                continue
            seen.add(href)
            candidates.append(href)
        return candidates

    async def _extract_article(
        self,
        page,
        article_url: str,
        section_url: str,
    ) -> Optional[FetchedArticle]:
        response = await page.goto(
            article_url,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        if response is not None and response.status >= 400:
            return None
        await page.wait_for_timeout(1_500)

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        title = self._extract_title(soup)
        content = self._extract_content(soup)
        if not title or len(content) < 400:
            return None

        canonical = self._extract_canonical(soup, article_url)
        published_at = self._extract_published_at(soup)
        domain = urlparse(canonical).netloc.lower() or urlparse(article_url).netloc
        summary = content[:1_200].strip()

        return FetchedArticle(
            article_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            title=title,
            summary=summary,
            raw_content=content[:40_000],
            cleaned_content=content[:40_000],
            source=domain,
            link=canonical,
            published_at=published_at,
            fetched_at=_now_iso(),
            browser="chromium-headless",
            source_section=section_url,
            selection_seed=self._rng.randint(0, 2**31 - 1),
            domain_tag="macro_econ",
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        for selector in (
            "h1",
            "meta[property='og:title']",
            "meta[name='twitter:title']",
        ):
            node = soup.select_one(selector)
            if node is None:
                continue
            value = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
            if value and len(str(value).strip()) >= 10:
                return str(value).strip()
        return ""

    @staticmethod
    def _extract_content(soup: BeautifulSoup) -> str:
        for node in soup.select(", ".join(_NOISE_SELECTORS)):
            node.decompose()

        selectors = (
            "article",
            ".fck_detail",
            ".article-content",
            ".content-detail",
            ".detail-content",
            ".detail__content",
            "main",
        )
        best: List[str] = []
        for selector in selectors:
            container = soup.select_one(selector)
            if container is None:
                continue
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in container.select("p")
                if len(p.get_text(" ", strip=True)) >= 35
            ]
            paragraphs = list(dict.fromkeys(paragraphs))
            if len(" ".join(paragraphs)) > len(" ".join(best)):
                best = paragraphs
            if len(" ".join(best)) >= 800:
                break

        if not best:
            # Last-resort body paragraph extraction for sites without a
            # stable article container.
            best = [
                p.get_text(" ", strip=True)
                for p in soup.select("body p")
                if len(p.get_text(" ", strip=True)) >= 60
            ]
            best = list(dict.fromkeys(best))

        return "\n".join(best).strip()

    @staticmethod
    def _extract_canonical(soup: BeautifulSoup, fallback: str) -> str:
        node = soup.select_one("link[rel='canonical']")
        if node and node.get("href"):
            href = str(node["href"]).strip()
            if href.startswith(("http://", "https://")):
                return href
        return fallback

    @staticmethod
    def _extract_published_at(soup: BeautifulSoup) -> Optional[str]:
        for selector, attr in (
            ("meta[property='article:published_time']", "content"),
            ("meta[name='date']", "content"),
            ("meta[itemprop='datePublished']", "content"),
            ("time[datetime]", "datetime"),
        ):
            node = soup.select_one(selector)
            if node and node.get(attr):
                return str(node.get(attr)).strip()
        for script in soup.select("script[type='application/ld+json']"):
            try:
                payload = json.loads(script.string or "")
            except Exception:
                continue
            for item in payload if isinstance(payload, list) else [payload]:
                if isinstance(item, dict) and item.get("datePublished"):
                    return str(item["datePublished"]).strip()
        return None


__all__ = ["HeadlessEconArticleFetcher"]
