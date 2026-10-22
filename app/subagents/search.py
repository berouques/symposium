"""Web search backends for the web_explorer subagent.

Engines (NG_05 reworked per user requirement — DuckDuckGo is unavailable in
the user's country, so it is NOT enabled by default):

- google : HTML endpoint; optional Google Custom Search JSON API when
           `web_search_google_cx` + `web_search_google_api_key` are set.
- bing   : HTML endpoint (b_algo result blocks).
- yandex : HTML endpoint (serp-item result blocks).
- searxng: JSON API of a self-hosted instance (spec NG_05 primary).
- ddg    : optional, off by default, kept for completeness.

All engines are tried sequentially (low-load system), failures are logged and
skipped; results are merged and de-duplicated by URL. If every enabled engine
fails, web_explorer reports "web search unavailable" per NG_05 §5.1.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse

import httpx

from .. import db
from ..logging_setup import get_logger

log = get_logger("websearch")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.8",
}

_RESULT: dict[str, str] = {"title": "", "url": "", "snippet": ""}


# ----------------------------------------------------------------- config

def _setting(key: str, default: str = "") -> str:
    return str(db.get_setting(key, default) or default)


def engines_enabled() -> list[str]:
    if _setting("web_search_enabled", "1") not in ("1", "true", "True"):
        return []
    try:
        engines = json.loads(_setting("web_search_engines", '["google","bing","yandex"]'))
    except ValueError:
        engines = ["google", "bing", "yandex"]
    out = []
    for name in engines:
        if name == "searxng" and not _setting("web_search_searxng_url").strip():
            continue  # no instance configured -> engine inactive (NG_05 2.2)
        out.append(name)
    return out


def search_status() -> dict:
    return {
        "enabled": _setting("web_search_enabled", "1") in ("1", "true", "True"),
        "engines": engines_enabled(),
        "searxng_url": _setting("web_search_searxng_url"),
        "google_cse": bool(_setting("web_search_google_cx") and _setting("web_search_google_api_key")),
    }


# ----------------------------------------------------------------- search

async def search(query: str) -> list[dict]:
    """Query every enabled engine, merge, dedup, cap. Raises RuntimeError
    when the search is configured off. Returns [] when all engines fail."""
    engines = engines_enabled()
    if not engines and _setting("web_search_enabled", "1") in ("0", "false", "False"):
        raise RuntimeError("web search disabled by administrator")
    timeout = float(_setting("web_search_timeout_sec", "10") or 10)
    limit = int(_setting("web_search_max_results", "8") or 8)
    results: list[dict] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        for engine in engines:
            try:
                fetcher = _FETCHERS[engine]
                found = await fetcher(client, query)
                for r in found:
                    r["engine"] = engine
                results.extend(found)
            except Exception as e:  # noqa: BLE001 — one dead engine must not kill the rest
                errors.append(f"{engine}: {type(e).__name__}")
                log.warning("search engine '%s' failed: %s", engine, e)
    if errors and not results:
        log.warning("all search engines failed: %s", "; ".join(errors))
    return _dedup(results)[:limit]


def _dedup(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in results:
        url = (r.get("url") or "").split("#")[0].rstrip("/")
        if not url.startswith("http"):
            continue
        if not (r.get("title") or "").strip():
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(r)
    return out


# ----------------------------------------------------------------- google

async def _fetch_google(client: httpx.AsyncClient, query: str) -> list[dict]:
    cx = _setting("web_search_google_cx").strip()
    key = _setting("web_search_google_api_key").strip()
    if cx and key:
        return await _fetch_google_cse(client, query, cx, key)
    url = "https://www.google.com/search?" + urllib.parse.urlencode(
        {"q": query, "num": "10", "hl": "en"}
    )
    resp = await client.get(url)
    resp.raise_for_status()
    return parse_google_html(resp.text)


async def _fetch_google_cse(
    client: httpx.AsyncClient, query: str, cx: str, key: str
) -> list[dict]:
    resp = await client.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": key, "cx": cx, "q": query, "num": "10"},
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        }
        for item in data.get("items", [])
    ]


_GOOGLE_URL_A = re.compile(
    r'<a[^>]+href="(/url\?q=(https?://[^"&]+)[^"]*)"[^>]*>(.*?)</a>', re.DOTALL
)
_GOOGLE_H3_A = re.compile(
    r'<a[^>]+href="(https?://[^"]+)"[^>]*>\s*(?:<div[^>]*>)?\s*<h3[^>]*>(.*?)</h3>', re.DOTALL
)
_BAD_HOSTS = (
    "google.", "gstatic.", "googleusercontent.", "googleapis.", "youtube.com/results",
    "support.google", "accounts.google", "policies.google",
)


def parse_google_html(page: str) -> list[dict]:
    results = []
    for _, url, title in _GOOGLE_URL_A.findall(page)[:10]:
        clean = _strip_tags(title).strip()
        if clean and not _bad_host(url):
            results.append({"title": clean, "url": urllib.parse.unquote(url), "snippet": ""})
    if not results:
        for url, title in _GOOGLE_H3_A.findall(page)[:10]:
            clean = _strip_tags(title).strip()
            if clean and not _bad_host(url):
                results.append({"title": clean, "url": url, "snippet": ""})
    return results


def _bad_host(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in _BAD_HOSTS)


# ------------------------------------------------------------------- bing

async def _fetch_bing(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": "10"})
    resp = await client.get(url)
    resp.raise_for_status()
    return parse_bing_html(resp.text)


_BING_BLOCK = re.compile(r'<li class="b_algo".*?>(.*?)</li>', re.DOTALL)
_BING_LINK = re.compile(r'<h2[^>]*><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a></h2>', re.DOTALL)
_BING_SNIPPET = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)


def parse_bing_html(page: str) -> list[dict]:
    results = []
    for block in _BING_BLOCK.findall(page):
        link = _BING_LINK.search(block)
        if not link:
            continue
        snippet_match = _BING_SNIPPET.search(block)
        snippet = _strip_tags(snippet_match.group(1)).strip() if snippet_match else ""
        results.append({
            "title": _strip_tags(link.group(2)).strip(),
            "url": link.group(1),
            "snippet": snippet,
        })
    return results[:10]


# ------------------------------------------------------------------ yandex

async def _fetch_yandex(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = "https://yandex.ru/search/?" + urllib.parse.urlencode({"text": query})
    resp = await client.get(url)
    resp.raise_for_status()
    return parse_yandex_html(resp.text)


_YANDEX_BLOCK = re.compile(r'<li[^>]*class="[^"]*serp-item[^"]*"[^>]*>(.*?)</li>', re.DOTALL)
_YANDEX_LINK = re.compile(
    r'<a[^>]+href="(https?://[^"]+)"[^>]*>(?:(?!</a>).)*?<h2[^>]*>(.*?)</h2>', re.DOTALL
)
_YANDES_LINK2 = re.compile(
    r'<a[^>]+class="[^"]*organic__url[^"]*"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.DOTALL
)


def parse_yandex_html(page: str) -> list[dict]:
    results = []
    for block in _YANDEX_BLOCK.findall(page):
        link = _YANDEX_LINK.search(block) or _YANDES_LINK2.search(block)
        if not link:
            continue
        url, title = link.group(1), _strip_tags(link.group(2)).strip()
        if not title or _bad_host(url):
            continue
        text = _strip_tags(block).replace(title, " ", 1)
        snippet = " ".join(text.split())[:300]
        results.append({"title": title, "url": url, "snippet": snippet})
    return results[:10]


# ----------------------------------------------------------------- searxng

async def _fetch_searxng(client: httpx.AsyncClient, query: str) -> list[dict]:
    base = _setting("web_search_searxng_url").strip().rstrip("/")
    resp = await client.get(f"{base}/search", params={"q": query, "format": "json"})
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in data.get("results", [])
    ][:10]


# --------------------------------------------------------------------- ddg

async def _fetch_ddg(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    resp.raise_for_status()
    return parse_ddg_html(resp.text)


_DDGA = re.compile(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DDGS = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def parse_ddg_html(page: str) -> list[dict]:
    results = []
    urls = _DDGA.findall(page)
    snippets = _DDGS.findall(page)
    for idx, (url, title) in enumerate(urls[:8]):
        if url.startswith("//duckduckgo.com/l/?uddg="):
            url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
        elif not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        snippet = _strip_tags(snippets[idx]) if idx < len(snippets) else ""
        results.append({"title": _strip_tags(title).strip(), "url": url, "snippet": snippet.strip()})
    return results


_FETCHERS = {
    "google": _fetch_google,
    "bing": _fetch_bing,
    "yandex": _fetch_yandex,
    "searxng": _fetch_searxng,
    "ddg": _fetch_ddg,
}


# ------------------------------------------------------------------ utils

def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text)


def format_raw(results: list[dict]) -> str:
    if not results:
        return "Web search returned no results."
    lines = [f"Search results ({len(results)}):"]
    for r in results:
        lines.append(
            f"- {r['title']} [{r.get('engine', '?')}]\n  {r['url']}\n  {r.get('snippet', '')[:400]}"
        )
    return "\n".join(lines)
