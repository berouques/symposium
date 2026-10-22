"""Интерактивный терминал workspace (WebSocket + PTY).

Клиент (xterm.js) шлёт текстовые JSON-кадры:
    {"type": "input",  "data": "ls\\r"}
    {"type": "resize", "cols": 120, "rows": 30}
а получает бинарные кадры — «сырой» вывод терминала. Оболочка запускается в
том же контейнере песочницы (docker exec), что и субагент coder, поэтому
файлы, установленные пакеты и workspace у человека и участников общие.
Доступ — только владельцу симпозиума и администратору.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import termios

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db
from ..auth import SESSION_COOKIE, user_for_token
from ..logging_setup import get_logger
from ..workspace import manager as ws

log = get_logger("terminal")
router = APIRouter(prefix="/api/symposia", tags=["terminal"])

SHELL_CMD = "exec bash -l 2>/dev/null || exec sh"


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _command(sid: int) -> tuple[list[str], str | None, dict | None]:
    if ws.mode_of() == "local":
        root = ws.ws_root(sid)
        venv = root / ".venv" if (root / ".venv" / "bin" / "python").exists() else None
        env = ws.local_env(root, root, venv)
        env.update({"TERM": "xterm-256color", "PS1": "$ "})
        return ["sh", "-c", SHELL_CMD], str(root), env
    return ["docker", "exec", "-it", "-w", "/workspace", "-e", "TERM=xterm-256color", "-e", "PS1=$ ",
            "-e", "PYTHONUSERBASE=/workspace/.local", "-e", "PIP_USER=1",
            "-e", "PATH=/workspace/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
            ws._container_name(sid), "sh", "-c", SHELL_CMD], None, None


@router.websocket("/{sid}/terminal")
async def terminal(websocket: WebSocket, sid: int):
    user = user_for_token(websocket.cookies.get(SESSION_COOKIE))
    sym = db.query_one("SELECT owner_id FROM symposia WHERE id=?", (sid,))
    if user is None or sym is None or (sym["owner_id"] != user["id"] and not user["is_admin"]):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    if ws.state_of(sid) != "running":
        await websocket.send_bytes(b"\x1b[33mstarting workspace...\x1b[0m\r\n")
        if await ws.ensure(sid) != "running":
            await websocket.send_bytes(b"\x1b[31mworkspace could not be started: "
                                       + str(ws.state_info(sid).get("reason") or "unknown").encode() + b"\x1b[0m\r\n")
            await websocket.close()
            return

    cmd, cwd, env = _command(sid)
    master, slave = pty.openpty()
    _set_winsize(master, 30, 110)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env, start_new_session=True)
    except FileNotFoundError as e:
        os.close(master)
        os.close(slave)
        await websocket.send_bytes(f"\x1b[31mcannot start shell: {e}\x1b[0m\r\n".encode())
        await websocket.close()
        return
    os.close(slave)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data or None)
        if not data:
            loop.remove_reader(master)

    loop.add_reader(master, on_readable)

    async def pump_out() -> None:
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            await websocket.send_bytes(chunk)

    out_task = asyncio.create_task(pump_out())
    try:
        while not out_task.done():
            recv = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait({recv, out_task}, return_when=asyncio.FIRST_COMPLETED)
            if recv not in done:
                recv.cancel()
                break
            event = recv.result()
            if event["type"] == "websocket.disconnect":
                break
            raw = event.get("text") if event.get("text") is not None else event.get("bytes")
            if isinstance(raw, bytes):
                os.write(master, raw)
                continue
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                os.write(master, (raw or "").encode())
                continue
            if frame.get("type") == "input":
                os.write(master, str(frame.get("data", "")).encode())
            elif frame.get("type") == "resize":
                _set_winsize(master, max(2, int(frame.get("rows", 30))), max(10, int(frame.get("cols", 110))))
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()
        try:
            loop.remove_reader(master)
        except Exception:  # noqa: BLE001
            pass
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.close(master)
        except OSError:
            pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
