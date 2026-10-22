"""Audit trail: admin-visible log of administrative actions."""
from __future__ import annotations

import json

from . import db
from .auth import utcnow


def audit(user_id: int | None, action: str, object_type: str = "", object_id: str | int = "", details: dict | None = None) -> None:
    db.execute(
        "INSERT INTO audit_log(user_id, action, object_type, object_id, details, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (
            user_id,
            action,
            object_type,
            str(object_id),
            json.dumps(details, ensure_ascii=False) if details else None,
            utcnow(),
        ),
    )
