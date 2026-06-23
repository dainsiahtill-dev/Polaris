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
observes the same events through Nats-JetStream/WebSocket fanout. Failures here are always
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

from .run_ledger import summarize_run_ledger_projection

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


def _empty_control_plane_projection(*, session_status: str, audit_path: Path | None = None) -> dict[str, Any]:
    state = "pending" if session_status == "running" else "missing"
    return {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": False,
        "ok": False,
        "status": state,
        "audit_path": str(audit_path) if audit_path is not None else "",
        "total": 0,
        "projected": 0,
        "missing": 0,
        "failed": 0,
        "projects": [],
        "detail": "factory audit ledger projection is not available yet",
    }


def _merge_project_evidence_policy(projects: list[dict[str, Any]]) -> dict[str, Any]:
    enabled: list[str] = []
    required: list[str] = []
    missing: list[str] = []
    has_policy = False
    for project in projects:
        policy = project.get("evidence_policy")
        if not isinstance(policy, dict):
            continue
        has_policy = True
        raw_enabled = policy.get("enabled_modalities")
        if isinstance(raw_enabled, list):
            enabled.extend(str(item) for item in raw_enabled if str(item))
        raw_required = policy.get("required_modalities")
        if isinstance(raw_required, list):
            required.extend(str(item) for item in raw_required if str(item))
        raw_missing = policy.get("missing_required_modalities")
        if isinstance(raw_missing, list):
            missing.extend(str(item) for item in raw_missing if str(item))
    enabled = list(dict.fromkeys(enabled))
    required = list(dict.fromkeys(required))
    missing = list(dict.fromkeys(missing))
    return {
        "ok": has_policy and not missing,
        "enabled_modalities": enabled,
        "required_modalities": required,
        "missing_required_modalities": missing,
    }


def _merge_project_evidence_modalities(projects: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for project in projects:
        modalities = project.get("evidence_modalities")
        if not isinstance(modalities, dict):
            continue
        for name, raw_summary in modalities.items():
            if not isinstance(raw_summary, dict):
                continue
            key = str(name)
            summary = merged.setdefault(
                key,
                {"total": 0, "present": 0, "ok": 0, "failed": 0, "latest_detail": ""},
            )
            for count_key in ("total", "present", "ok", "failed"):
                summary[count_key] = int(summary.get(count_key) or 0) + int(raw_summary.get(count_key) or 0)
            detail = str(raw_summary.get("latest_detail") or "")
            if detail:
                summary["latest_detail"] = detail
    return dict(sorted(merged.items()))


def _control_plane_projection_from_audit(status: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only control-plane projection from factory_audits.json."""

    work_dir = str(status.get("work_dir") or "").strip()
    session_status = str(status.get("status") or "").strip().lower()
    if not work_dir:
        return _empty_control_plane_projection(session_status=session_status)
    audit_path = Path(work_dir) / "factory_audits.json"
    if not audit_path.is_file():
        return _empty_control_plane_projection(session_status=session_status, audit_path=audit_path)
    try:
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            **_empty_control_plane_projection(session_status=session_status, audit_path=audit_path),
            "status": "invalid",
            "detail": f"factory audit ledger projection could not be read: {exc}",
        }
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return {
            **_empty_control_plane_projection(session_status=session_status, audit_path=audit_path),
            "status": "invalid",
            "detail": "factory audit ledger projection missing records",
        }

    projects: list[dict[str, Any]] = []
    projected = 0
    failed = 0
    for index, item in enumerate(records):
        record: dict[str, Any] = item if isinstance(item, dict) else {}
        projection = record.get("run_ledger_projection")
        projection_status = summarize_run_ledger_projection(projection)
        projection_map: dict[str, Any] = projection if isinstance(projection, dict) else {}
        ok = bool(projection_status.get("ok"))
        if ok:
            projected += 1
        else:
            failed += 1
        capability = projection_map.get("capability")
        capability_map: dict[str, Any] = capability if isinstance(capability, dict) else {}
        evidence_policy = projection_map.get("evidence_policy")
        evidence_policy_map: dict[str, Any] = evidence_policy if isinstance(evidence_policy, dict) else {}
        evidence_modalities = projection_map.get("evidence_modalities")
        evidence_modalities_map: dict[str, Any] = evidence_modalities if isinstance(evidence_modalities, dict) else {}
        projects.append(
            {
                "project_id": str(record.get("project_id") or record.get("id") or f"record-{index + 1}"),
                "ok": ok,
                "integrity_ok": bool(projection_map.get("integrity_ok")),
                "outcome_ok": bool(projection_map.get("outcome_ok")),
                "gate_count": int(projection_map.get("gate_count") or 0),
                "failed_gate_count": int(projection_status.get("failed_gate_count") or 0),
                "latest_token_id": str(capability_map.get("latest_token_id") or ""),
                "detail": str(projection_status.get("detail") or ""),
                "missing": list(projection_status.get("missing") or []),
                "evidence_policy": evidence_policy_map,
                "evidence_modalities": evidence_modalities_map,
            }
        )

    total = len(records)
    missing = max(0, total - projected)
    ready = total > 0 and projected == total and failed == 0
    goal_audit = payload.get("goal_audit") if isinstance(payload, dict) else {}
    goal_ledger = goal_audit.get("run_ledger") if isinstance(goal_audit, dict) else None
    return {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": True,
        "ok": ready,
        "status": "ready" if ready else "degraded",
        "audit_path": str(audit_path),
        "total": total,
        "projected": projected,
        "missing": missing,
        "failed": failed,
        "projects": projects,
        "goal_audit": goal_ledger if isinstance(goal_ledger, dict) else {},
        "detail": f"run ledger projection {projected}/{total} project(s) ready",
        "evidence_policy": _merge_project_evidence_policy(projects),
        "evidence_modalities": _merge_project_evidence_modalities(projects),
    }


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
        status["control_plane_projection"] = _control_plane_projection_from_audit(status)
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
            status["control_plane_projection"] = _control_plane_projection_from_audit(status)
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
