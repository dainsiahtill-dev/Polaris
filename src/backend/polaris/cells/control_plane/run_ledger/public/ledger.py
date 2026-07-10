"""Append-only platform Run Ledger writer."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def stable_json(value: Any) -> str:
    """Serialize ledger content into deterministic UTF-8 JSON text."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    """Return the stable content hash for a ledger payload."""

    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _event_content_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in event.items() if key not in {"append_id", "content_id", "event_id", "recorded_at"}
    }


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


class RunLedger:
    """Append-only JSONL ledger for platform control-plane evidence."""

    def __init__(self, workspace: Path, *, run_id: str) -> None:
        self.workspace = Path(workspace)
        safe_run_id = _safe_token(run_id or "unknown")
        self.path = self.workspace / "runtime" / "control_plane" / "ledger" / f"{safe_run_id}.ndjson"

    def prepare_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Build the deterministic projection row without writing it."""

        payload = dict(event)
        payload.setdefault("schema_version", 1)
        payload.setdefault("content_id", stable_hash(_event_content_payload(payload)))
        payload.setdefault("event_id", payload["content_id"])
        recorded_at = datetime.now(timezone.utc).isoformat()
        payload.setdefault("recorded_at", recorded_at)
        payload.setdefault(
            "append_id",
            stable_hash(
                {
                    "content_id": payload["content_id"],
                    "ledger_path": str(self.path),
                    "recorded_at": payload["recorded_at"],
                }
            ),
        )
        return payload

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append one rebuildable projection row and return its receipt."""

        payload = self.prepare_event(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(stable_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {"ledger_path": str(self.path), "event": payload}

    def read_events(self) -> list[dict[str, Any]]:
        """Read all events in append order."""

        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            for line in handle.read().splitlines():
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    events.append(parsed)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return events


__all__ = ["RunLedger", "stable_hash", "stable_json"]
