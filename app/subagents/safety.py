"""Basic workspace safety: destructive commands need the human's confirmation.

Deliberately simple (pattern-based, not a sandbox): it catches the common
irreversible actions and asks the human — through the same question card as
`ask_human` — before running them. Disable with the setting
`confirm_destructive` = 0. The prompt-level rules (symposium_rules.md) tell
participants to avoid such commands in the first place.
"""
from __future__ import annotations

import asyncio
import re

from .. import db
from ..auth import utcnow
from ..events import bus

_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*[rRf]|--recursive|--force)|\brm\s+[^|;&]*\*", "deletes files (rm)"),
    (r"\brmdir\b|\bshred\b|\btruncate\b|\bunlink\b", "deletes or wipes files"),
    (r"\bfind\b[^|;&]*(-delete|-exec\s+rm)", "deletes files (find)"),
    (r"\b(mkfs|fdisk|parted|wipefs)\b|\bdd\s+[^|;&]*\bof=", "writes to disks/devices"),
    (r"\b(chmod|chown)\s+(-[a-zA-Z]*R|--recursive)", "recursive permission change"),
    (r"\b(kill|pkill|killall)\b", "kills processes"),
    (r"\b(shutdown|reboot|poweroff|halt)\b|\bsystemctl\s+(stop|disable|mask)\b", "stops the system or services"),
    (r"\bsudo\b|\bsu\s+-?\w*", "privilege escalation"),
    (r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "pipes a download into a shell"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|push\s+[^|;&]*--force|push\s+[^|;&]*-f\b)", "discards git history or changes"),
    (r"\bpip3?\s+uninstall\b|\bnpm\s+uninstall\s+-g\b", "uninstalls packages"),
    (r"(?<![<>&0-9])>\s*/(?!tmp\b|dev/null)", "writes to a path outside the workspace"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"\bshutil\.rmtree\b|\bos\.(remove|unlink|rmdir|removedirs)\b|\.unlink\s*\(|\bsend2trash\b", "deletes files from Python"),
]


def classify(text: str) -> str | None:
    """Reason why the command/code is considered destructive, or None."""
    if str(db.get_setting("confirm_destructive", "1")) not in ("1", "true", "True"):
        return None
    for rx, why in _PATTERNS:
        if re.search(rx, text or ""):
            return why
    return None


async def confirm_with_human(ctx, text: str, reason: str, timeout: int = 1200) -> bool:
    """Ask the human (blocking the call) and return True only on an explicit 'Allow'."""
    sid = ctx.symposium["id"]
    member = ctx.member
    req = db.execute(
        "INSERT INTO hitl_requests(symposium_id, member_id, round_id, question, options, context, created_at) VALUES(?,?,?,?,?,?,?)",
        (sid, member["id"], ctx.round_id, f"{member['name']} wants to run a potentially destructive command ({reason}). Allow it?",
         '["Allow", "Deny"]', text[:1500], utcnow()))
    db.execute("UPDATE symposia SET status='waiting_human', updated_at=? WHERE id=?", (utcnow(), sid))
    bus.publish(sid, {"type": "hitl_request", "request_id": req, "member_id": member["id"]})
    waited = 0.0
    while waited < timeout:
        await asyncio.sleep(1.0)
        waited += 1.0
        row = db.query_one("SELECT status, answer FROM hitl_requests WHERE id=?", (req,))
        if row and row["status"] == "answered":
            return (row["answer"] or "").strip().lower().startswith(("allow", "yes", "да", "разреш"))
        if row and row["status"] == "cancelled":
            return False
        sym = db.query_one("SELECT status FROM symposia WHERE id=?", (sid,))
        if sym is None or sym["status"] in ("finished", "paused_manual", "paused_error"):
            db.execute("UPDATE hitl_requests SET status='cancelled', answered_at=? WHERE id=? AND status='pending'", (utcnow(), req))
            return False
    db.execute("UPDATE hitl_requests SET status='cancelled', answered_at=? WHERE id=? AND status='pending'", (utcnow(), req))
    db.execute("UPDATE symposia SET status='active' WHERE id=? AND status='waiting_human'", (sid,))
    return False
