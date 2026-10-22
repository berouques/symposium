"""Workspace: per-symposium isolated artifact directory + sandbox.

NG_06: Docker is the PRIMARY mode (one container per symposium, memory/CPU
caps, host FS invisible except the mounted workspace). v0.2.2: containers
join a network (default 'bridge', admin-configurable) — a fully networkless
container cannot even `pip install`, which blocked the coder subagent in
practice. Local mode is an explicitly allowed fallback only
(workspace_allow_local), running code as host subprocesses with rlimits —
weaker isolation, always flagged to the user and admin.

Workspace tree (spec NG_40 §4):
  artifacts/         - files created by participants (coder)
  uploads/           - chat uploads (read-only for agents)
  protocol/          - secretary protocol, summaries, final report
  subagent_logs/     - raw subagent outputs (not part of member context)
  avatars/           - member avatars (SVG)
  tmp/               - scratch dir for sandboxed execution

Lifecycle states: none -> starting -> running <-> stopped(reason) / error.
"""
from __future__ import annotations

import asyncio
import os
import resource
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db
from ..auth import utcnow
from ..config import get_config
from ..events import bus
from ..logging_setup import get_logger

log = get_logger("workspace")

DIRS = ("artifacts", "uploads", "protocol", "subagent_logs", "avatars", "tmp")

_starting: set[int] = set()
_last_activity: dict[int, datetime] = {}
_monitor_task: asyncio.Task | None = None
_docker_probe: tuple[bool, float] | None = None  # (available, checked_at)


def ws_root(symposium_id: int) -> Path:
    return get_config().workspaces_dir / str(symposium_id)


def ensure_dirs(symposium_id: int) -> None:
    """Create the directory skeleton without changing the lifecycle state."""
    root = ws_root(symposium_id)
    root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (root / d).mkdir(exist_ok=True)


def exists(symposium_id: int) -> bool:
    return (ws_root(symposium_id) / ".ready").exists()


def state_of(symposium_id: int) -> str:
    row = db.query_one("SELECT ws_state FROM symposia WHERE id=?", (symposium_id,))
    return row["ws_state"] if row else "none"


def mode_of(symposium_id: int | None = None) -> str:
    return str(db.get_setting("workspace_mode", "docker") or "docker")


def state_info(symposium_id: int) -> dict:
    row = db.query_one("SELECT ws_state, ws_reason FROM symposia WHERE id=?", (symposium_id,))
    if row is None:
        return {"state": "none", "reason": "", "mode": mode_of()}
    return {"state": row["ws_state"] or "none", "reason": row["ws_reason"] or "",
            "mode": mode_of()}


def local_allowed() -> bool:
    return str(db.get_setting("workspace_allow_local", "0")) in ("1", "true", "True")


def docker_network() -> str:
    """Docker network for workspace containers (v0.2.2).

    Default 'bridge': without a network the agents cannot even install a
    package inside the container (pip needs to reach PyPI). The admin can
    set it to 'none' (fully isolated) or a custom network name.
    """
    value = str(db.get_setting("workspace_docker_network", "bridge") or "bridge").strip()
    return value or "bridge"


def _set_state(symposium_id: int, state: str, reason: str | None = None) -> None:
    if reason is None:
        reason = "" if state == "running" else _reason_of(symposium_id)
    db.execute("UPDATE symposia SET ws_state=?, ws_reason=? WHERE id=?", (state, reason, symposium_id))
    bus.publish(symposium_id, {"type": "workspace", "state": state, "reason": reason})


def _reason_of(symposium_id: int) -> str:
    row = db.query_one("SELECT ws_reason FROM symposia WHERE id=?", (symposium_id,))
    return (row["ws_reason"] or "") if row else ""


def touch(symposium_id: int) -> None:
    _last_activity[symposium_id] = datetime.now(timezone.utc)


async def ensure(symposium_id: int) -> str:
    """Create + start workspace if needed; returns final state."""
    touch(symposium_id)
    state = state_of(symposium_id)
    if state == "running":
        return state
    if symposium_id in _starting:
        return "starting"
    return await start(symposium_id)


async def start(symposium_id: int) -> str:
    if symposium_id in _starting:
        return "starting"
    _starting.add(symposium_id)
    _set_state(symposium_id, "starting", "starting")
    try:
        root = ws_root(symposium_id)
        root.mkdir(parents=True, exist_ok=True)
        for d in DIRS:
            (root / d).mkdir(exist_ok=True)
        mode = mode_of()
        if mode == "docker":
            available = await docker_available()
            if available:
                ok = await _docker_start(symposium_id)
                if ok:
                    (root / ".ready").write_text(utcnow())
                    _set_state(symposium_id, "running", "")
                    log.info("workspace %d running (mode=docker)", symposium_id)
                    return "running"
                _set_state(symposium_id, "error", "docker_start_failed")
                log.error("workspace %d: docker container failed to start", symposium_id)
                return "error"
            # Docker unavailable -> local fallback only if explicitly allowed
            if local_allowed():
                (root / ".ready").write_text(utcnow())
                _set_state(symposium_id, "running", "local_fallback")
                log.warning(
                    "workspace %d runs in LOCAL fallback mode (docker unavailable): "
                    "no full isolation", symposium_id,
                )
                return "running"
            _set_state(symposium_id, "error", "docker_unavailable")
            log.error(
                "workspace %d: docker unavailable and local fallback is forbidden "
                "(workspace_allow_local=0)", symposium_id,
            )
            return "error"
        # mode == "local" (explicitly configured by the admin)
        (root / ".ready").write_text(utcnow())
        _set_state(symposium_id, "running", "local_mode")
        log.info("workspace %d running (mode=local)", symposium_id)
        return "running"
    finally:
        _starting.discard(symposium_id)


async def stop(symposium_id: int, reason: str = "manual") -> None:
    mode = mode_of()
    if mode == "docker":
        await _docker_stop(symposium_id)
    _set_state(symposium_id, "stopped", reason)
    log.info("workspace %d stopped (%s)", symposium_id, reason)


async def restart(symposium_id: int) -> str:
    await stop(symposium_id, "restart")
    return await start(symposium_id)


async def remove(symposium_id: int) -> None:
    await _docker_stop(symposium_id)
    root = ws_root(symposium_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    _last_activity.pop(symposium_id, None)
    db.execute("UPDATE symposia SET ws_state='none', ws_reason='' WHERE id=?", (symposium_id,))


# ------------------------------------------------------------------ docker

async def docker_available() -> bool:
    """Is the docker binary present AND the daemon responding? Cached 60 s."""
    global _docker_probe
    if shutil.which("docker") is None:
        return False
    if _docker_probe is not None and time.monotonic() - _docker_probe[1] < 60:
        return _docker_probe[0]
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "version", "--format", "{{.Server.Version}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        available = proc.returncode == 0 and bool(out.strip())
    except (OSError, asyncio.TimeoutError):
        available = False
    _docker_probe = (available, time.monotonic())
    log.debug("docker available: %s", available)
    return available


def _container_name(symposium_id: int) -> str:
    return f"symposium-ws-{symposium_id}"


async def _docker_start(symposium_id: int) -> bool:
    image = db.get_setting("workspace_docker_image", "python:3.12-alpine")
    root = ws_root(symposium_id)
    root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (root / d).mkdir(exist_ok=True)
    mem = db.get_setting("workspace_mem_limit_mb", "4096")
    cpus = db.get_setting("workspace_cpu_limit", "1.0")
    network = docker_network()
    await _docker_stop(symposium_id)
    # Docker rejects relative bind-mount sources ("includes invalid characters
    # for a local volume name") — always pass an absolute host path.
    host_dir = str(root.resolve())
    cmd = [
        "docker", "run", "-d", "--rm", "--name", _container_name(symposium_id),
        "--network", network,
        "--memory", f"{mem}m", "--cpus", str(cpus),
        "--read-only=false",
        "-v", f"{host_dir}:/workspace",
        "-w", "/workspace",
        image, "sleep", "infinity",
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.error("docker workspace start failed: %s", err.decode()[:300])
        return False
    # make sure the daemon actually keeps the container up (image may be
    # missing and `docker run` can still exit 0 in edge cases on old daemons)
    for _ in range(3):
        if _docker_running(symposium_id):
            return True
        await asyncio.sleep(0.3)
    log.error("docker workspace container %s is not running after start", _container_name(symposium_id))
    return False


async def _docker_stop(symposium_id: int) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker", "rm", "-f", _container_name(symposium_id),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


def _docker_running(symposium_id: int) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", _container_name(symposium_id)],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0 and b"true" in proc.stdout
    except Exception:  # noqa: BLE001
        return False


# -------------------------------------------------------------- file access

def safe_path(symposium_id: int, rel: str) -> Path:
    root = ws_root(symposium_id).resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise PermissionError("path escapes workspace")
    return target


def tree(symposium_id: int) -> dict:
    """File tree for the UI browser (spec NG_10 §7)."""
    info = state_info(symposium_id)
    root = ws_root(symposium_id)
    if not root.exists():
        return {"state": info["state"], "reason": info["reason"], "mode": info["mode"], "tree": []}

    def build(path: Path) -> list[dict]:
        nodes = []
        try:
            for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if child.name == ".ready":
                    continue
                rel = str(child.relative_to(root))
                if child.is_dir():
                    nodes.append({
                        "name": child.name, "path": rel, "type": "dir",
                        "children": build(child),
                    })
                else:
                    nodes.append({
                        "name": child.name, "path": rel, "type": "file",
                        "size": child.stat().st_size,
                    })
        except PermissionError:
            pass
        return nodes

    return {"state": info["state"], "reason": info["reason"], "mode": info["mode"],
            "tree": build(root)}


def read_file(symposium_id: int, rel: str, max_bytes: int = 256 * 1024) -> dict:
    path = safe_path(symposium_id, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    data = path.read_bytes()[:max_bytes]
    try:
        content = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        content = None
        binary = True
    return {"path": rel, "size": path.stat().st_size, "binary": binary, "content": content}


def save_file(symposium_id: int, rel: str, content: str) -> None:
    path = safe_path(symposium_id, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------- sandbox exec

SANDBOX_TIMEOUT_SEC = 30


def _limits(timeout: int | None = None) -> tuple:
    mem_mb = int(db.get_setting("workspace_mem_limit_mb", "4096"))
    cpu = (timeout or SANDBOX_TIMEOUT_SEC) + 5      # лимит CPU не должен убивать команду раньше таймаута
    return (
        resource.RLIMIT_CPU, cpu,
    ), (resource.RLIMIT_AS, mem_mb * 1024 * 1024)


def _make_preexec(timeout: int | None):
    def _preexec() -> None:  # POSIX only
        for what, value in _limits(timeout):
            try:
                resource.setrlimit(what, (value, value))
            except (ValueError, OSError):
                pass
        os.setsid()
    return _preexec


_PASS_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
             "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "LANG", "LC_ALL")


async def ensure_venv(root: Path) -> Path | None:
    """Постоянное виртуальное окружение workspace (.venv): `pip install` работает
    и переживает перезапуски. Создаётся через uv (если есть) или python -m venv."""
    venv = root / ".venv"
    py = venv / "bin" / "python"
    if py.exists():
        return venv
    import shutil
    import sys
    cmds = []
    if shutil.which("uv"):
        cmds.append(["uv", "venv", "--python", sys.executable, str(venv)])
    cmds.append([sys.executable, "-m", "venv", str(venv)])
    for cmd in cmds:
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=120)
            if py.exists():
                return venv
        except Exception as e:  # noqa: BLE001
            log.warning("не удалось создать venv командой %s: %s", cmd[0], e)
    return None


def local_env(root: Path, workdir: Path, venv: Path | None) -> dict:
    path = "/usr/local/bin:/usr/bin:/bin"
    env = {"PATH": f"{venv}/bin:{path}" if venv else path, "HOME": str(root), "PYTHONDONTWRITEBYTECODE": "1",
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "TMPDIR": str(root / "tmp")}
    if venv:
        env["VIRTUAL_ENV"] = str(venv)
    for k in _PASS_ENV:
        if os.environ.get(k):
            env[k] = os.environ[k]
    (root / "tmp").mkdir(exist_ok=True)
    return env


async def run_sandbox(symposium_id: int, workdir_rel: str, command: list[str],
                      timeout: int | None = None) -> dict:
    """Execute a command in the workspace sandbox. Returns dict with
    exit_code, stdout, stderr, duration_ms. Raw output truncated."""
    touch(symposium_id)
    mode = mode_of()
    root = ws_root(symposium_id)
    workdir = (root / workdir_rel).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()

    if mode == "docker":
        # auto-wake (NG_06 3.8); never silently fall back to host execution
        if not _docker_running(symposium_id):
            await ensure(symposium_id)
        if _docker_running(symposium_id):
            # пакеты ставятся в /workspace/.local и переживают перезапуск контейнера
            docker_cmd = ["docker", "exec", "-w", f"/workspace/{workdir_rel}".rstrip("/."),
                          "-e", "PYTHONUSERBASE=/workspace/.local", "-e", "PIP_USER=1",
                          "-e", "PIP_NO_CACHE_DIR=1", "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
                          "-e", "PATH=/workspace/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
                          _container_name(symposium_id)] + command
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        else:
            info = state_info(symposium_id)
            return {
                "exit_code": -1, "stdout": "",
                "stderr": _mode_error(info),
                "timed_out": False, "duration_ms": 0,
            }
    else:
        venv = await ensure_venv(root)
        if venv and command and command[0] in ("python", "python3"):
            command = [str(venv / "bin" / "python")] + command[1:]
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_make_preexec(timeout) if os.name == "posix" else None,
            env=local_env(root, workdir, venv),
        )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout or SANDBOX_TIMEOUT_SEC)
        exit_code = proc.returncode
        timed_out = False
    except asyncio.TimeoutError:
        proc.kill()
        out, err = await proc.communicate()
        exit_code = -1
        timed_out = True

    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    return {
        "exit_code": exit_code,
        "stdout": out.decode("utf-8", errors="replace")[:8000],
        "stderr": err.decode("utf-8", errors="replace")[:4000],
        "timed_out": timed_out,
        "duration_ms": duration_ms,
    }


def _mode_error(info: dict) -> str:
    reason = info.get("reason") or "unknown"
    if reason == "docker_unavailable":
        return (
            "workspace unavailable: Docker is not reachable and the local fallback "
            "is disabled. Ask the administrator to start Docker or to explicitly allow "
            "the limited local mode (admin -> Workspace)."
        )
    if reason == "docker_start_failed":
        return (
            "workspace unavailable: the Docker container failed to start "
            "(image missing or daemon error). See server log and admin -> Workspace."
        )
    return f"workspace unavailable (state={info.get('state')}, reason={reason})"


# ------------------------------------------------------------ auto-stop

async def start_monitor() -> None:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor(), name="ws-monitor")


async def stop_monitor() -> None:
    global _monitor_task
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _monitor_task = None


async def _monitor() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            timeout_sec = int(db.get_setting("workspace_idle_timeout_sec", "3600"))
            rows = db.query_all("SELECT id, ws_state FROM symposia WHERE ws_state IN ('running','error')")
            now = datetime.now(timezone.utc)
            for row in rows:
                sid = row["id"]
                last = _last_activity.get(sid)
                if last is None:
                    # fall back to last_activity column
                    la = db.query_value("SELECT last_activity FROM symposia WHERE id=?", (sid,))
                    try:
                        last = datetime.fromisoformat(la) if la else now
                    except ValueError:
                        last = now
                    _last_activity[sid] = last
                if now - last > timedelta(seconds=timeout_sec):
                    await stop(sid, reason="idle")
                    log.info("workspace %d auto-stopped after inactivity", sid)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("ws monitor error: %s", e)
