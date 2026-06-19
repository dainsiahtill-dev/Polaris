"""Workspace-agnostic factory-bench session store.

The factory HTTP router is keyed by workspace: ``FactoryRunService`` lives in
each workspace's runtime root and tracks chain runs that mutate that workspace.
The ``scripts/factory_bench/run_factory_bench.py`` runner is different — it
drives L1-L8 projects sequentially across many workspaces, so its lifecycle
events need a storage layer that is not bound to any one workspace.

This service stores bench sessions + their event stream in
``~/.cache/polaris/factory_bench/sessions/<session_id>/``:

    sessions/<id>/status.json   — session metadata + lifecycle state
    sessions/<id>/events.jsonl  — append-only event log
    sessions/<id>/index.json    — registered project list (mutable until run start)

The bench subprocess publishes through the HTTP router (see
``polaris/delivery/http/routers/factory.py``); the Factory front-end panel
observes the same events through Nat-JetStream/WebSocket fanout. Failures here are always
soft: a missing session dir or a corrupt event line is logged and skipped,
never raised to the HTTP layer, so the bench can never crash the UI.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_DEFAULT_SESSIONS_ROOT = Path(os.path.expanduser("~/.cache/polaris/factory_bench/sessions"))


def _sessions_root() -> Path:
    override = os.environ.get("FACTORY_BENCH_SESSIONS_ROOT")
    if override:
        return Path(override)
    return _DEFAULT_SESSIONS_ROOT


def _session_dir(session_id: str) -> Path:
    if not session_id or "/" in session_id or "\\" in session_id or session_id.startswith("."):
        raise ValueError(f"invalid bench session id: {session_id!r}")
    return _sessions_root() / session_id


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class FactoryBenchService:
    """Workspace-agnostic bench session store.

    All methods are synchronous and side-effect-only (file I/O). The HTTP
    router is expected to run them inside the FastAPI thread pool when it
    needs async behavior; the bench subprocess client (which runs in a
    terminal) calls them directly via the HTTP layer.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _sessions_root()
        # In-memory per-session event sequence counter. Used by state snapshot
        # readers to filter events strictly greater than ``since_seq``.
        # Reset on process restart (durable cursor in JSONL not needed — the
        # last appended line's position can be replayed if a client wants
        # to resume from a specific point).
        self._seq_by_session: dict[str, int] = {}

    @property
    def root(self) -> Path:
        return self._root

    def register_session(
        self,
        *,
        work_dir: str,
        project_ids: list[str],
        total: int,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        sid = (session_id or "").strip() or f"bench-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        sdir = _session_dir(sid)
        if sdir.exists() and any(sdir.iterdir()):
            raise FileExistsError(f"bench session {sid!r} already exists at {sdir}")
        sdir.mkdir(parents=True, exist_ok=False)
        status = {
            "session_id": sid,
            "work_dir": str(work_dir),
            "project_ids": list(project_ids),
            "total": int(total),
            "completed": 0,
            "failed": 0,
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "metadata": dict(metadata or {}),
        }
        _atomic_write_json(sdir / "status.json", status)
        _atomic_write_json(sdir / "index.json", {"project_ids": list(project_ids), "total": int(total)})
        (sdir / "events.jsonl").write_text("", encoding="utf-8")
        logger.info("Registered factory-bench session %s at %s", sid, sdir)
        return sid

    def append_event(self, session_id: str, event: dict[str, Any]) -> bool:
        sdir = _session_dir(session_id)
        if not sdir.is_dir():
            logger.warning("bench append_event: session %s not found", session_id)
            return False
        events_path = sdir / "events.jsonl"
        payload = dict(event)
        payload.setdefault("ts", _now_iso())
        payload.setdefault("session_id", session_id)
        # Per-session monotonic seq so snapshot readers can resume with
        # ``since_seq``. Stored in the JSONL line itself (durable) and also
        # cached in-memory for cursor comparison.
        next_seq = self._seq_by_session.get(session_id, 0) + 1
        self._seq_by_session[session_id] = next_seq
        payload["seq"] = next_seq
        try:
            with events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("bench append_event: write failed for %s: %s", session_id, exc)
            # Roll back the in-memory counter so a retry produces a
            # contiguous sequence instead of a gap.
            self._seq_by_session[session_id] = next_seq - 1
            return False
        self._touch_status(session_id)
        return True

    def update_progress(
        self,
        session_id: str,
        *,
        completed: int | None = None,
        failed: int | None = None,
    ) -> bool:
        """Update the per-project counters in the session status.

        Called by the bench subprocess after each project finishes so the
        front-end can see real-time ``X/Y 通过`` progress, not just a stale
        "0/Y" until the run terminates.
        """
        sdir = _session_dir(session_id)
        status_path = sdir / "status.json"
        if not status_path.is_file():
            return False
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if completed is not None:
            status["completed"] = max(0, int(completed))
        if failed is not None:
            status["failed"] = max(0, int(failed))
        status["updated_at"] = _now_iso()
        _atomic_write_json(status_path, status)
        return True

    def complete_session(
        self,
        session_id: str,
        *,
        success: bool = True,
        summary: dict[str, Any] | None = None,
    ) -> bool:
        sdir = _session_dir(session_id)
        status_path = sdir / "status.json"
        if not status_path.is_file():
            return False
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        status["status"] = "completed" if success else "failed"
        status["updated_at"] = _now_iso()
        status["completed_at"] = _now_iso()
        if summary:
            status.setdefault("metadata", {}).update(summary)
            # The summary may carry the final counter snapshot; honour it so
            # the terminal payload matches what the front-end expects.
            if "completed" in summary:
                status["completed"] = int(summary["completed"])
            if "failed" in summary:
                status["failed"] = int(summary["failed"])
        _atomic_write_json(status_path, status)
        return True

    def _touch_status(self, session_id: str) -> None:
        status_path = _session_dir(session_id) / "status.json"
        if not status_path.is_file():
            return
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        status["updated_at"] = _now_iso()
        _atomic_write_json(status_path, status)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        sdir = _session_dir(session_id)
        status_path = sdir / "status.json"
        if not status_path.is_file():
            return None
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("get_session: status parse failed for %s: %s", session_id, exc)
            return None
        status["events_path"] = str(sdir / "events.jsonl")
        status["events"] = list(self.tail_events(session_id, max_lines=200))
        return status

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        root = self.root
        if not root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        try:
            entries = sorted(
                (p for p in root.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError as exc:
            logger.warning("list_sessions: scan failed: %s", exc)
            return []
        for entry in entries[:limit]:
            status_path = entry / "status.json"
            if not status_path.is_file():
                continue
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(status)
        return out

    def tail_events(self, session_id: str, *, max_lines: int = 200) -> Iterable[dict[str, Any]]:
        events_path = _session_dir(session_id) / "events.jsonl"
        if not events_path.is_file():
            return
        try:
            with events_path.open("r", encoding="utf-8") as fh:
                data = fh.read()
        except OSError as exc:
            logger.warning("tail_events: read failed for %s: %s", session_id, exc)
            return
        lines = data.splitlines()[-max_lines:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def read_events_from(self, session_id: str, *, start_offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        events_path = _session_dir(session_id) / "events.jsonl"
        if not events_path.is_file():
            return [], 0
        try:
            with events_path.open("r", encoding="utf-8") as fh:
                fh.seek(start_offset)
                data = fh.read()
                new_offset = fh.tell()
        except OSError as exc:
            logger.warning("read_events_from: read failed for %s: %s", session_id, exc)
            return [], start_offset
        events: list[dict[str, Any]] = []
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return events, new_offset
