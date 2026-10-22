"""Standalone frontend listener (default port 23607).

Serves the SPA on its own port and transparently reverse-proxies /api/*
(including SSE streams and file downloads) to the backend on SYMPOSIUM_PORT
(23606). The browser therefore talks to a single origin — no CORS, cookies
and EventSource work as with the embedded mode.

Disabled via SYMPOSIUM_UI_PORT=off (then the backend serves the SPA itself).
"""
from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

_HOP_HEADERS = {
    "content-encoding", "content-length", "transfer-encoding",
    "connection", "keep-alive",
}

_API_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def create_ui_app(api_host: str, api_port: int) -> FastAPI:
    target_host = "127.0.0.1" if api_host in ("0.0.0.0", "::", "") else api_host
    client = httpx.AsyncClient(
        base_url=f"http://{target_host}:{api_port}",
        timeout=httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0),
        follow_redirects=False,
    )
    app = FastAPI(title="Symposium UI", docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/api/{rest:path}", methods=_API_METHODS)
    async def proxy(rest: str, request: Request):
        body = await request.body()
        headers = [
            (k, v) for k, v in request.headers.raw
            if k.lower() not in (b"host", b"content-length", b"connection", b"accept-encoding")
        ]
        headers.append((b"accept-encoding", b"identity"))
        url = f"/api/{rest}"
        # v0.3: обрывы соединения с бэкендом (RemoteProtocolError и т.п.)
        # больше не роняют ASGI-приложение стек-трейсом в консоль — клиент
        # получает аккуратный 502 JSON; идемпотентные GET повторяем один раз
        attempts = 2 if request.method == "GET" else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                upstream = await client.send(
                    client.build_request(
                        request.method, url, params=request.query_params,
                        headers=headers, content=body,
                    ),
                    stream=True,
                )
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError,
                    httpx.WriteError, httpx.PoolTimeout) as e:
                last_exc = e
                if attempt + 1 < attempts:
                    continue
                return JSONResponse(
                    status_code=502,
                    content={"detail": f"backend unreachable: {type(e).__name__}"},
                )
            out_headers = [
                (k.decode("latin-1"), v.decode("latin-1"))
                for k, v in upstream.headers.raw
                if k.decode("latin-1").lower() not in _HOP_HEADERS
            ]

            async def stream_body():
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                    # стрим оборвался посреди ответа: логируем, молча завершаем
                    print(f"[ui-proxy] stream aborted: {type(e).__name__}")
                finally:
                    await upstream.aclose()

            return StreamingResponse(
                stream_body(), status_code=upstream.status_code,
                headers=dict(out_headers),
            )
        return JSONResponse(status_code=502, content={"detail": "backend unreachable"})

    @app.get("/api/health")
    async def ui_health():
        try:
            resp = await client.get("/api/health")
            return {"ok": True, "upstream": resp.json()}
        except Exception:  # noqa: BLE001
            return {"ok": False, "upstream": "unreachable"}

    from .config import get_config
    frontend = get_config().frontend_dir()
    if frontend is not None:
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")
    return app
