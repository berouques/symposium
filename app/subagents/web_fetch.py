"""web_fetch: скачать файл/страницу из интернета в workspace участников.

Файл сохраняется в downloads/ рабочей области — дальше участники анализируют
его кодером (python/shell), как это делает человек: pandas, pypdf и т.д.
Защита: только http(s); адреса loopback/частных сетей отклоняются (иначе
участники могли бы стучаться в Ollama, SearXNG и другие локальные сервисы),
если не включено web_fetch_allow_private; размер ограничен web_fetch_max_bytes.
"""
from __future__ import annotations

import html as html_lib
import ipaddress
import re
import socket
import urllib.parse
from pathlib import Path

import httpx

from .. import db
from ..logging_setup import get_logger
from ..workspace import manager as ws
from .base import AgentContext, AgentResult, raw_log_path

log = get_logger("subagents.web_fetch")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".py", ".js", ".log", ".html", ".htm"}


def _is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False        # не разрешилось — httpx сам сообщит об ошибке
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _safe_name(url: str, disposition: str | None, wanted: str | None) -> str:
    name = wanted or ""
    if not name and disposition:
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition, re.I)
        name = urllib.parse.unquote(m.group(1)) if m else ""
    if not name:
        name = Path(urllib.parse.urlparse(url).path).name or "page.html"
    name = re.sub(r"[^\w.\-]+", "_", Path(name).name)[:100] or "download.bin"
    return name


def _html_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html_lib.unescape(raw)).strip()


def _preview(path: Path, ctype: str) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf" or "pdf" in ctype:
            from pypdf import PdfReader
            r = PdfReader(str(path))
            text = "\n".join((p.extract_text() or "") for p in r.pages[:6])
            return f"PDF, {len(r.pages)} pages. Text of the first pages:\n{text[:2500]}"
        if ext in TEXT_EXT or ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
            raw = path.read_bytes()[:200_000].decode("utf-8", errors="replace")
            if ext in (".html", ".htm") or "html" in ctype:
                return "HTML page, text content:\n" + _html_text(raw)[:2500]
            return raw[:2500]
    except Exception as e:  # noqa: BLE001
        return f"(preview unavailable: {e})"
    return "Binary file — analyze it with the coder agent."


async def run(ctx: AgentContext, params: dict) -> AgentResult:
    url = (params.get("url") or params.get("query") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return AgentResult(ok=False, error="web_fetch: a valid http(s) URL is required")
    allow_private = str(db.get_setting("web_fetch_allow_private", "0")) in ("1", "true", "True")
    if not allow_private and _is_private(parsed.hostname):
        return AgentResult(ok=False, error="web_fetch: local and private-network addresses are not allowed")
    limit = int(db.get_setting("web_fetch_max_bytes", 25 * 1024 * 1024) or 25 * 1024 * 1024)
    sid = ctx.symposium["id"]
    ws.ensure_dirs(sid)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=45.0, headers=UA) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    return AgentResult(ok=False, error=f"web_fetch: server answered HTTP {r.status_code}")
                if not allow_private and r.url.host and _is_private(r.url.host):
                    return AgentResult(ok=False, error="web_fetch: redirect to a private-network address blocked")
                size_hint = int(r.headers.get("content-length") or 0)
                if size_hint > limit:
                    return AgentResult(ok=False, error=f"web_fetch: file is {size_hint} bytes, limit is {limit}")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                name = _safe_name(str(r.url), r.headers.get("content-disposition"), params.get("filename"))
                chunks, total = [], 0
                async for chunk in r.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        return AgentResult(ok=False, error=f"web_fetch: download exceeds the {limit}-byte limit")
                    chunks.append(chunk)
    except httpx.HTTPError as e:
        return AgentResult(ok=False, error=f"web_fetch failed: {type(e).__name__}: {e}")
    rel = f"downloads/{name}"
    target = ws.ws_root(sid) / rel
    n = 1
    while target.exists():
        n += 1
        rel = f"downloads/{Path(name).stem}_{n}{Path(name).suffix}"
        target = ws.ws_root(sid) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"".join(chunks))
    preview = _preview(target, ctype)
    raw = raw_log_path(ctx, "web_fetch")
    raw.write_text(f"URL: {url}\nSaved: {rel}\nType: {ctype}\nSize: {total}\n\n{preview}", encoding="utf-8")
    digest = (f"web_fetch: downloaded {url} → workspace file `{rel}` ({total} bytes, {ctype or 'unknown type'}). "
              f"Analyze it with the coder agent (e.g. python reading '{rel}').\nPreview:\n{preview[:1800]}")
    return AgentResult(ok=True, digest=digest, raw_path=str(raw), extra={"path": rel, "size": total})
