from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from polaris.kernelone.events.io_events import emit_event
from polaris.kernelone.fs.jsonl.ops import scan_last_seq
from polaris.kernelone.fs.text_ops import read_file_safe
from polaris.kernelone.memory.refs import has_memory_refs

logger = logging.getLogger(__name__)


def _hash_payload(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def compute_contract_fingerprint(pm_payload: dict[str, Any] | None) -> str:
    if not isinstance(pm_payload, dict):
        return ""
    contract: dict[str, Any] = {
        "overall_goal": pm_payload.get("overall_goal"),
        "focus": pm_payload.get("focus"),
        "tasks": [],
    }
    tasks = pm_payload.get("tasks") or []
    if isinstance(tasks, list):
        for item in tasks:
            if not isinstance(item, dict):
                continue
            contract["tasks"].append(
                {
                    "id": item.get("id"),
                    "goal": item.get("goal"),
                    "acceptance": item.get("acceptance") or item.get("acceptance_criteria"),
                }
            )
    return _hash_payload(contract)


def load_contract_fingerprint(pm_task_path: str) -> str:
    if not pm_task_path:
        return ""
    text = read_file_safe(pm_task_path)
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Invariant sentinel skipped contract fingerprint due to invalid JSON: path=%s error=%s",
            pm_task_path,
            exc,
        )
        return ""
    return compute_contract_fingerprint(payload if isinstance(payload, dict) else None)


def _check_events_append_only(
    events_path: str,
    *,
    start_seq: int = 0,
    start_size: int = 0,
) -> dict[str, Any] | None:
    if not events_path or not os.path.exists(events_path):
        return None
    end_size = 0
    try:
        end_size = os.path.getsize(events_path)
    except OSError as exc:
        logger.debug(
            "Invariant sentinel could not stat events file: path=%s error=%s",
            events_path,
            exc,
        )
        end_size = 0
    end_seq = scan_last_seq(events_path)
    if (start_size and end_size < start_size) or (start_seq and end_seq and end_seq < start_seq - 1):
        return {
            "code": "EVENTS_APPEND_ONLY",
            "message": "events.jsonl appears to shrink or rewind",
            "details": {
                "start_seq": start_seq,
                "end_seq": end_seq,
                "start_size": start_size,
                "end_size": end_size,
            },
        }
    return None


def _check_contract_immutable(
    *,
    initial_hash: str,
    pm_task_path: str,
) -> dict[str, Any] | None:
    if not initial_hash or not pm_task_path:
        return None
    current_hash = load_contract_fingerprint(pm_task_path)
    if not current_hash or current_hash == initial_hash:
        return None
    return {
        "code": "CONTRACT_IMMUTABLE",
        "message": "PM_TASKS contract fields changed during run",
        "details": {"initial_hash": initial_hash, "current_hash": current_hash, "pm_task_path": pm_task_path},
    }


def _check_memory_refs(memory_path: str, run_id: str) -> dict[str, Any] | None:
    if not memory_path or not os.path.exists(memory_path) or not run_id:
        return None
    missing: list[str] = []
    try:
        with open(memory_path, encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.debug(
                        "Invariant sentinel skipped malformed memory line: path=%s line=%s error=%s",
                        memory_path,
                        line_number,
                        exc,
                    )
                    continue
                context = data.get("context") if isinstance(data, dict) else None
                if not isinstance(context, dict):
                    continue
                if str(context.get("run_id") or "") != run_id:
                    continue
                if not has_memory_refs(context):
                    mem_id = str(data.get("id") or "")
                    if mem_id:
                        missing.append(mem_id)
    except OSError as exc:
        logger.warning(
            "Invariant sentinel could not scan memory refs: path=%s error=%s",
            memory_path,
            exc,
        )
        return None
    if not missing:
        return None
    return {
        "code": "MEMORY_REFS",
        "message": "Memory items missing evidence refs",
        "details": {"missing_count": len(missing), "memory_ids": missing[:10]},
    }


def _check_failure_hops_ready(director_result_path: str) -> dict[str, Any] | None:
    if not director_result_path or not os.path.isfile(director_result_path):
        return None
    try:
        with open(director_result_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Invariant sentinel could not parse director result: path=%s error=%s",
            director_result_path,
            exc,
        )
        return None
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "").strip().lower()
    acceptance = payload.get("acceptance")
    is_failure = status in ("fail", "blocked") or acceptance is False
    if not is_failure:
        return None
    ready = bool(payload.get("failure_hops_ready"))
    if ready:
        return None
    return {
        "code": "FAILURE_3HOPS_MISSING",
        "message": "Failed Director result missing failure_hops readiness",
        "details": {
            "director_result_path": director_result_path,
            "status": status,
            "acceptance": acceptance,
        },
    }


def run_invariant_sentinel(
    *,
    events_path: str,
    run_id: str,
    step: int,
    pm_task_path: str = "",
    contract_fingerprint: str = "",
    events_seq_start: int = 0,
    events_size_start: int = 0,
    memory_path: str = "",
    director_result_path: str = "",
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    contract_violation = _check_contract_immutable(
        initial_hash=contract_fingerprint,
        pm_task_path=pm_task_path,
    )
    if contract_violation:
        violations.append(contract_violation)
    events_violation = _check_events_append_only(
        events_path,
        start_seq=events_seq_start,
        start_size=events_size_start,
    )
    if events_violation:
        violations.append(events_violation)
    memory_violation = _check_memory_refs(memory_path, run_id)
    if memory_violation:
        violations.append(memory_violation)
    failure_hops_violation = _check_failure_hops_ready(director_result_path)
    if failure_hops_violation:
        violations.append(failure_hops_violation)

    refs = {"run_id": run_id, "step": step, "phase": "sentinel"}
    emit_event(
        events_path,
        kind="observation",
        actor="System",
        name="invariant.check",
        refs=refs,
        summary="Invariant check " + ("PASS" if not violations else "FAIL"),
        output={"ok": not violations, "violations": violations},
    )
    for violation in violations:
        emit_event(
            events_path,
            kind="observation",
            actor="System",
            name="invariant.violation",
            refs=refs,
            summary=violation.get("message", "Invariant violation"),
            output=violation,
        )
    return {"ok": not violations, "violations": violations}
