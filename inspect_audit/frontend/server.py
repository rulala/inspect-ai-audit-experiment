"""Loopback web chat for ACP-enabled Inspect evals.

Hosting mirrors `inspect view` (FastAPI + uvicorn + static files, bound to
127.0.0.1). The server itself stays a dumb relay: the browser speaks real ACP
JSON-RPC over a WebSocket, and this process pumps frames 1:1 to the eval's
ACP endpoint (newline-delimited JSON over an AF_UNIX socket or TCP loopback)
and back. Protocol semantics live entirely in the standard and in the page.

Usage:

    # terminal 1: any eval launched with --acp-server
    inspect eval inspect_audit/report -T logs=<dir> --model <m> \
        --acp-server --display none

    # terminal 2
    python frontend/server.py            # http://127.0.0.1:7676

Running evals are found the same way `inspect acp` finds them: the discovery
files Inspect writes per ACP-enabled eval process.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from inspect_ai.agent._acp.discovery import (
    DiscoveredEval,
    list_discovered_evals,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()


# --- live reload (dev mode) -------------------------------------------------
# old-fashioned and dependency-free: pages poll /api/dev/version (the newest
# mtime under static/) and reload when it moves. The middleware injects the
# polling snippet into every HTML response, so pages need no dev markup.

DEV = os.environ.get("FRONTEND_DEV") == "1"

RELOAD_SNIPPET = (
    b"<script>(()=>{let v;setInterval(async()=>{try{const r=await "
    b"fetch('/api/dev/version');const t=await r.text();"
    b"if(v&&t!==v)location.reload();v=t;}catch(e){}},700)})()</script>"
)


def _static_version() -> str:
    newest = 0.0
    for f in STATIC_DIR.rglob("*"):
        if f.is_file():
            newest = max(newest, f.stat().st_mtime)
    return str(newest)


@app.get("/api/dev/version")
def dev_version() -> str:
    return _static_version()


class LiveReloadMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if DEV and response.headers.get("content-type", "").startswith("text/html"):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            body += RELOAD_SNIPPET
            headers = dict(response.headers)
            headers["content-length"] = str(len(body))
            from starlette.responses import Response

            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )
        return response


app.add_middleware(LiveReloadMiddleware)


@dataclass(frozen=True)
class _Target:
    socket_path: str | None
    host: str | None
    port: int | None


def _find(eval_id: str) -> DiscoveredEval:
    for entry in list_discovered_evals():
        if entry.eval_id == eval_id:
            return entry
    raise KeyError(eval_id)


@app.get("/api/sessions")
def sessions() -> list[dict[str, object]]:
    return [
        {
            "eval_id": entry.eval_id,
            "started_at": entry.started_at,
            "address": entry.target.describe(),
        }
        for entry in list_discovered_evals()
    ]


@app.websocket("/ws/{eval_id}")
async def ws(websocket: WebSocket, eval_id: str) -> None:
    try:
        entry = _find(eval_id)
    except KeyError:
        await websocket.close(code=1008, reason=f"no running eval {eval_id!r}")
        return

    target = entry.target
    try:
        if target.socket_path is not None:
            reader, writer = await asyncio.open_unix_connection(
                str(target.socket_path)
            )
        else:
            reader, writer = await asyncio.open_connection(
                target.host, target.port
            )
    except OSError as ex:
        await websocket.close(code=1011, reason=f"connect failed: {ex}")
        return

    await websocket.accept()

    async def sock_to_ws() -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            await websocket.send_text(line.decode("utf-8"))

    async def ws_to_sock() -> None:
        while True:
            text = await websocket.receive_text()
            # JSON.stringify output never contains raw newlines, so one
            # websocket message is exactly one NDJSON frame
            writer.write(text.encode("utf-8") + b"\n")
            await writer.drain()

    pumps = [asyncio.create_task(sock_to_ws()), asyncio.create_task(ws_to_sock())]
    try:
        await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for pump in pumps:
            pump.cancel()
        writer.close()
        try:
            await websocket.close()
        except Exception:
            pass


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7676)
    args = parser.parse_args()
    # loopback only: the ACP server has no authentication, and the evals
    # this fronts may hold private benchmark content
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
