from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx

from .config import settings
from .cache import TTLCache


cache = TTLCache(ttl_seconds=settings.CACHE_TTL_SECONDS)


def _valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


async def serper_search(
    client: httpx.AsyncClient, query: str, num: int = 5, country: str | None = None, language: str | None = None
) -> list[dict]:
    query = " ".join(query.split()).strip()
    if not query or not settings.SERPER_API_KEY:
        return []

    country = (country or settings.SEARCH_COUNTRY).strip().lower()
    language = (language or settings.SEARCH_LANGUAGE).strip().lower()
    key = cache.key_for("serper", f"{country}:{language}:{query}")
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": settings.SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "num": max(1, min(num, 10)),
                # gl/hl are locale hints derived per-query from the goal's
                # detected currency/geography (see research.infer_search_locale)
                # — NOT a fixed default. A hardcoded "in" here previously broke
                # USD/EUR/GBP queries by forcing Indian search results on them.
                "gl": country,
                "hl": language,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []

    results: list[dict] = []
    for result in (data.get("organic", []) or [])[:num]:
        if not isinstance(result, dict):
            continue
        url = str(result.get("link", ""))
        if not _valid_url(url):
            continue
        results.append({
            "title": str(result.get("title", "")),
            "url": url,
            "snippet": str(result.get("snippet", "")),
            "source": "serper",
            "query": query,
        })

    cache.set(key, results)
    return results


async def jina_fetch(client: httpx.AsyncClient, url: str, fallback: str = "") -> str:
    if not _valid_url(url):
        return fallback

    key = cache.key_for("jina", url)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        response = await client.get(
            f"https://r.jina.ai/{url}",
            timeout=12,
            headers={"Accept": "text/plain"},
        )
        response.raise_for_status()
        text = response.text[:7000].strip()
    except Exception:
        # Preserve the Serper snippet.
        text = fallback

    cache.set(key, text)
    return text


async def search_and_fetch(
    query: str,
    search_num: int = settings.SEARCH_RESULTS_PER_QUERY,
    fetch_num: int = settings.FETCH_DEPTH_PER_QUERY,
    country: str | None = None,
    language: str | None = None,
) -> list[dict]:
    """
    Search first, then deeply fetch only the highest-value results.

    Search is cheap. Page fetching is slower. Scout fetches the top results
    and retains snippets for the remaining search results.
    """
    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=12, max_keepalive_connections=6)
    ) as client:
        results = await serper_search(client, query, num=search_num, country=country, language=language)
        if not results:
            return []

        targets = results[: max(1, min(fetch_num, len(results)))]

        pages = await asyncio.gather(
            *[jina_fetch(client, result["url"], fallback=result.get("snippet", "")) for result in targets],
            return_exceptions=True,
        )

        for result, page in zip(targets, pages):
            if isinstance(page, str) and page.strip():
                result["content"] = page[:7000]
            else:
                result["content"] = result.get("snippet", "")

        # Remaining results still contain usable search evidence.
        for result in results[len(targets):]:
            result["content"] = result.get("snippet", "")

        return results


async def run_parallel_searches(
    queries: list[str], country: str | None = None, language: str | None = None
) -> dict[str, list[dict]]:
    cleaned: list[str] = []
    seen: set[str] = set()

    for query in queries or []:
        if not isinstance(query, str):
            continue
        query = " ".join(query.split()).strip()
        key = query.lower()
        if query and key not in seen:
            seen.add(key)
            cleaned.append(query)

    if not cleaned:
        return {}

    results = await asyncio.gather(
        *[search_and_fetch(query, country=country, language=language) for query in cleaned],
        return_exceptions=True,
    )

    output: dict[str, list[dict]] = {}
    for query, result in zip(cleaned, results):
        output[query] = result if isinstance(result, list) else []

    return output
