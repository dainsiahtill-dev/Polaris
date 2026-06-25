"""Platform run-ledger projection service.

This public boundary intentionally avoids temporary internal harness naming.
Stress harnesses may still write compatibility ledger files while writer
migration is in progress; formal consumers should call this service or its
HTTP facade.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public.contracts import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    RunLedgerAppendResultV1,
    RunLedgerProjectionResultV1,
)
from polaris.cells.control_plane.run_ledger.public.ledger import RunLedger
from polaris.cells.control_plane.run_ledger.public.projection import (
    build_run_ledger_projection,
    summarize_run_ledger_projection,
)
from polaris.infrastructure.log_pipeline.jetstream_publisher import get_log_jetstream_publisher
from polaris.kernelone.storage import resolve_storage_roots

logger = logging.getLogger(__name__)
_JETSTREAM_PUBLISH_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _jetstream_publish_enabled() -> bool:
    raw = str(os.environ.get("KERNELONE_JETSTREAM_PUBLISH") or "").strip().lower()
    return bool(raw) and raw not in _JETSTREAM_PUBLISH_FALSE_VALUES


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


def _empty_projection(
    *,
    workspace: Path,
    status: str = "pending",
    include_compat_ledgers: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": False,
        "ok": False,
        "status": status,
        "audit_path": str(workspace / "runtime" / "control_plane" / "ledger"),
        "compat_ledgers_included": bool(include_compat_ledgers),
        "total": 0,
        "projected": 0,
        "missing": 0,
        "failed": 0,
        "projects": [],
        "detail": "run ledger projection is not available yet",
        "evidence_policy": {
            "ok": False,
            "enabled_modalities": [],
            "required_modalities": [],
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "evidence_modalities": {},
    }


def _ledger_dirs(workspace: Path, *, include_compat_ledgers: bool = False) -> list[Path]:
    runtime_root = workspace / "runtime"
    dirs = [runtime_root / "control_plane" / "ledger"]
    if include_compat_ledgers:
        dirs.append(runtime_root / "factory" / "ledger")
    return dirs


def _ledger_paths(
    workspace: Path,
    *,
    run_id: str,
    max_runs: int,
    include_compat_ledgers: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    if run_id:
        safe_run_id = _safe_token(run_id)
        for ledger_dir in _ledger_dirs(workspace, include_compat_ledgers=include_compat_ledgers):
            path = ledger_dir / f"{safe_run_id}.ndjson"
            if path.is_file():
                paths.append(path)
        return paths

    for ledger_dir in _ledger_dirs(workspace, include_compat_ledgers=include_compat_ledgers):
        if not ledger_dir.is_dir():
            continue
        paths.extend(path for path in ledger_dir.glob("*.ndjson") if path.is_file())
    unique_paths = sorted(set(paths), key=lambda item: (item.stat().st_mtime, str(item)), reverse=True)
    return unique_paths[:max_runs]


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                parsed.setdefault("_ledger_path", str(path))
                events.append(parsed)
    return events


def _event_project_id(event: dict[str, Any]) -> str:
    token = event.get("job_token")
    token_map = token if isinstance(token, dict) else {}
    return str(
        token_map.get("project_id")
        or event.get("project_id")
        or token_map.get("run_id")
        or event.get("run_id")
        or "workspace"
    ).strip()


def _merge_evidence_policy(projects: list[dict[str, Any]]) -> dict[str, Any]:
    enabled: list[str] = []
    required: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for project in projects:
        policy = project.get("evidence_policy")
        if not isinstance(policy, dict):
            continue
        for target, key in (
            (enabled, "enabled_modalities"),
            (required, "required_modalities"),
            (missing, "missing_required_modalities"),
            (failed, "failed_required_modalities"),
        ):
            raw_items = policy.get(key)
            if isinstance(raw_items, list):
                target.extend(str(item) for item in raw_items if str(item))
    enabled = list(dict.fromkeys(enabled))
    required = list(dict.fromkeys(required))
    missing = list(dict.fromkeys(missing))
    failed = list(dict.fromkeys(failed))
    return {
        "ok": not missing and not failed,
        "enabled_modalities": enabled,
        "required_modalities": required,
        "missing_required_modalities": missing,
        "failed_required_modalities": failed,
    }


def _merge_evidence_modalities(projects: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for project in projects:
        modalities = project.get("evidence_modalities")
        if not isinstance(modalities, dict):
            continue
        for name, raw_summary in modalities.items():
            if not isinstance(raw_summary, dict):
                continue
            summary = merged.setdefault(
                str(name),
                {"total": 0, "present": 0, "ok": 0, "failed": 0, "latest_detail": ""},
            )
            for key in ("total", "present", "ok", "failed"):
                summary[key] = int(summary.get(key) or 0) + int(raw_summary.get(key) or 0)
            detail = str(raw_summary.get("latest_detail") or "").strip()
            if detail:
                summary["latest_detail"] = detail
    return dict(sorted(merged.items()))


def _project_from_events(project_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    projection = build_run_ledger_projection(events)
    summary = summarize_run_ledger_projection(projection)
    capability = projection.get("capability")
    capability_map = capability if isinstance(capability, dict) else {}
    evidence_policy = projection.get("evidence_policy")
    evidence_policy_map = evidence_policy if isinstance(evidence_policy, dict) else {}
    evidence_modalities = projection.get("evidence_modalities")
    evidence_modalities_map = evidence_modalities if isinstance(evidence_modalities, dict) else {}
    return {
        "project_id": project_id,
        "ok": bool(summary.get("ok")),
        "integrity_ok": bool(projection.get("integrity_ok")),
        "outcome_ok": bool(projection.get("outcome_ok")),
        "gate_count": int(projection.get("gate_count") or 0),
        "failed_gate_count": int(summary.get("failed_gate_count") or 0),
        "latest_token_id": str(capability_map.get("latest_token_id") or ""),
        "detail": str(summary.get("detail") or ""),
        "missing": list(summary.get("missing") or []),
        "evidence_policy": evidence_policy_map,
        "evidence_modalities": evidence_modalities_map,
    }


def read_run_ledger_projection(query: ReadRunLedgerProjectionQueryV1) -> RunLedgerProjectionResultV1:
    """Read and project platform run ledger events for one workspace."""

    workspace = Path(query.workspace).expanduser().resolve()
    paths = _ledger_paths(
        workspace,
        run_id=query.run_id,
        max_runs=query.max_runs,
        include_compat_ledgers=query.include_compat_ledgers,
    )
    if not paths:
        return RunLedgerProjectionResultV1(
            projection=_empty_projection(
                workspace=workspace,
                include_compat_ledgers=query.include_compat_ledgers,
            )
        )

    events: list[dict[str, Any]] = []
    for path in paths:
        events.extend(_read_events(path))
    if not events:
        return RunLedgerProjectionResultV1(
            projection=_empty_projection(
                workspace=workspace,
                status="empty",
                include_compat_ledgers=query.include_compat_ledgers,
            )
        )

    grouped_events: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped_events.setdefault(_event_project_id(event), []).append(event)

    projects = [
        _project_from_events(project_id, project_events)
        for project_id, project_events in sorted(grouped_events.items())
    ]
    failed = sum(1 for project in projects if not bool(project.get("ok")))
    projected = len(projects)
    ok = projected > 0 and failed == 0
    return RunLedgerProjectionResultV1(
        projection={
            "schema_version": 1,
            "source": "run_ledger_projection",
            "available": True,
            "ok": ok,
            "status": "ready" if ok else "failed",
            "audit_path": str(workspace / "runtime" / "control_plane" / "ledger"),
            "compat_ledgers_included": query.include_compat_ledgers,
            "total": projected,
            "projected": projected,
            "missing": 0,
            "failed": failed,
            "projects": projects,
            "detail": f"run ledger projection {projected} project(s), {failed} failed",
            "evidence_policy": _merge_evidence_policy(projects),
            "evidence_modalities": _merge_evidence_modalities(projects),
        }
    )


def _publish_run_ledger_projection_update(
    *,
    workspace: Path,
    run_id: str,
    event: dict[str, Any],
) -> bool:
    """Publish the latest control-plane projection after a durable ledger append."""

    if not _jetstream_publish_enabled():
        return False
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return False
    try:
        roots = resolve_storage_roots(workspace_token)
        workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
        if not workspace_key:
            return False
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=workspace_token,
                run_id=str(run_id or "").strip(),
                max_runs=1,
            )
        ).projection
        now = datetime.now(timezone.utc)
        event_id = str(event.get("event_id") or event.get("append_id") or int(now.timestamp() * 1000)).strip()
        envelope = {
            "schema_version": "runtime.v2",
            "event_id": f"control-plane-ledger-{event_id}",
            "workspace_key": workspace_key,
            "run_id": str(run_id or event.get("run_id") or ""),
            "channel": "status.control_plane",
            "kind": "control_plane_ledger_projection_update",
            "ts": str(event.get("timestamp") or event.get("ts") or now.isoformat()),
            "cursor": 0,
            "trace_id": str(event.get("trace_id") or ""),
            "payload": {
                "projection": projection,
                "ledger_event": event,
                "run_id": str(run_id or event.get("run_id") or ""),
            },
            "meta": {"source": "control_plane.run_ledger"},
        }
        return get_log_jetstream_publisher().publish(
            subject=f"hp.runtime.{workspace_key}.status.control_plane",
            payload=envelope,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Run Ledger projection JetStream publish failed: %s", exc)
        return False


def append_run_ledger_event(command: AppendRunLedgerEventCommandV1) -> RunLedgerAppendResultV1:
    """Append one platform run-ledger event through the public control-plane boundary."""

    workspace = Path(command.workspace).expanduser().resolve()
    persisted = RunLedger(workspace, run_id=command.run_id).append_event(dict(command.event))
    event = persisted.get("event")
    if isinstance(event, dict):
        _publish_run_ledger_projection_update(
            workspace=workspace,
            run_id=command.run_id,
            event=event,
        )
    return RunLedgerAppendResultV1(receipt=persisted)


__all__ = ["append_run_ledger_event", "read_run_ledger_projection"]
