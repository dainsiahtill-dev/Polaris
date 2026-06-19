"""Deterministic outbox-id derivation for ``runtime.task_market``.

Leaf helper extracted so both the service base mixin and the ``service``
facade can share a single canonical implementation without an import cycle.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_outbox_id(
    *,
    workspace: str,
    stream: str,
    event_type: str,
    run_id: str,
    task_id: str,
    payload: dict[str, Any],
) -> str:
    basis = {
        "workspace": str(workspace or "").strip(),
        "stream": str(stream or "").strip(),
        "event_type": str(event_type or "").strip(),
        "run_id": str(run_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "payload": dict(payload),
    }
    encoded = json.dumps(
        basis,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"tm-{hashlib.sha256(encoded).hexdigest()}"
