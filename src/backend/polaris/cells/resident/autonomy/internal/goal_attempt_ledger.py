"""Strict durable Goal/Attempt ledger owned by ``resident.autonomy``."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from polaris.cells.resident.autonomy.public.contracts import (
    ObserveResidentGoalAttemptCommandV1,
    QueryResidentGoalExecutionV1,
    ResidentGoalAttemptReceiptV1,
    ResidentGoalAttemptStatusV1,
    ResidentGoalExecutionStatusV1,
    ResidentGoalExecutionV1,
    ResidentGoalLifecycleErrorV1,
    ResidentGoalStateV1,
    SettleResidentGoalAttemptCommandV1,
    StartResidentGoalAttemptCommandV1,
)
from polaris.kernelone.storage import resolve_workspace_persistent_path

_STREAM_SCHEMA = "resident.goal-attempt-stream.v1"
_RECEIPT_KEYS = {
    "receipt_id",
    "workspace",
    "goal_id",
    "attempt_id",
    "run_id",
    "operation",
    "status",
    "execution_status",
    "revision",
    "attempt_number",
    "max_attempts",
    "no_progress_limit",
    "no_progress_fingerprint",
    "no_progress_streak",
    "idempotency_key",
    "semantic_hash",
    "recorded_at",
    "evidence_refs",
    "error",
}
_ALLOWED_GOAL_TRANSITIONS: dict[ResidentGoalStateV1, frozenset[ResidentGoalStateV1]] = {
    ResidentGoalStateV1.PENDING: frozenset({ResidentGoalStateV1.APPROVED, ResidentGoalStateV1.REJECTED}),
    ResidentGoalStateV1.APPROVED: frozenset({ResidentGoalStateV1.MATERIALIZED}),
    ResidentGoalStateV1.REJECTED: frozenset({ResidentGoalStateV1.ARCHIVED}),
    ResidentGoalStateV1.MATERIALIZED: frozenset({ResidentGoalStateV1.ARCHIVED}),
    ResidentGoalStateV1.ARCHIVED: frozenset(),
}


def transition_goal_state(
    current: ResidentGoalStateV1,
    target: ResidentGoalStateV1,
    *,
    current_revision: int,
    expected_revision: int,
) -> tuple[ResidentGoalStateV1, int, bool]:
    """Apply strict Goal transition with CAS and same-state idempotency."""
    if type(current) is not ResidentGoalStateV1:
        raise ResidentGoalLifecycleErrorV1(
            "unknown_persisted_goal_state",
            f"unknown persisted Goal state: {current!r}",
        )
    if type(target) is not ResidentGoalStateV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_transition", f"invalid target Goal state: {target!r}")
    if current_revision != expected_revision:
        raise ResidentGoalLifecycleErrorV1(
            "goal_revision_conflict",
            f"expected revision {expected_revision}, found {current_revision}",
        )
    if target is current:
        return current, current_revision, False
    if target not in _ALLOWED_GOAL_TRANSITIONS[current]:
        raise ResidentGoalLifecycleErrorV1(
            "invalid_goal_transition",
            f"Goal transition {current.value} to {target.value} is not allowed",
        )
    return target, current_revision + 1, True


def _canonical_workspace(workspace: str) -> str:
    return str(Path(workspace).expanduser().resolve())


def _stream_path(workspace: str, goal_id: str) -> Path:
    if goal_id in {".", ".."} or "/" in goal_id or "\\" in goal_id:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_id", "goal_id must be one path-safe segment")
    return Path(
        resolve_workspace_persistent_path(
            _canonical_workspace(workspace),
            f"workspace/meta/resident/goals/{goal_id}/attempts.v1.jsonl",
        )
    )


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _semantic_hash(operation: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json({"operation": operation, "payload": payload})).hexdigest()


def _record_hash(record_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record_without_hash)).hexdigest()


def _receipt_payload(receipt: ResidentGoalAttemptReceiptV1) -> dict[str, Any]:
    payload = asdict(receipt)
    payload.pop("record_hash")
    payload["status"] = receipt.status.value
    payload["execution_status"] = receipt.execution_status.value
    payload["evidence_refs"] = list(receipt.evidence_refs)
    return payload


def _receipt_from_record(record: dict[str, Any]) -> ResidentGoalAttemptReceiptV1:
    try:
        payload = record["receipt"]
        if type(payload) is not dict:
            raise TypeError("receipt must be object")
        if set(payload) != _RECEIPT_KEYS:
            raise ValueError("receipt keys must match exact v1 schema")
        required_strings = (
            "receipt_id",
            "workspace",
            "goal_id",
            "attempt_id",
            "run_id",
            "operation",
            "status",
            "execution_status",
            "idempotency_key",
            "semantic_hash",
            "recorded_at",
        )
        if any(type(payload[name]) is not str or not payload[name].strip() for name in required_strings):
            raise TypeError("required receipt strings must be non-empty exact strings")
        if payload["operation"] not in {"start", "observe", "settle"}:
            raise ValueError("receipt operation is unknown")
        if len(payload["semantic_hash"]) != 64 or any(
            character not in "0123456789abcdef" for character in payload["semantic_hash"]
        ):
            raise ValueError("receipt semantic_hash must be lowercase SHA-256")
        if payload["receipt_id"] != f"resident-goal-attempt-{payload['semantic_hash'][:24]}":
            raise ValueError("receipt_id must bind semantic_hash")
        recorded_at = datetime.fromisoformat(payload["recorded_at"])
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must include timezone")
        for name in ("revision", "attempt_number", "max_attempts", "no_progress_limit", "no_progress_streak"):
            if type(payload[name]) is not int or payload[name] < 0:
                raise TypeError(f"{name} must be a non-negative exact int")
        if any(payload[name] < 1 for name in ("revision", "attempt_number", "max_attempts", "no_progress_limit")):
            raise ValueError("receipt revision, ordinal, and budgets must be >= 1")
        if type(payload["no_progress_fingerprint"]) is not str or type(payload["error"]) is not str:
            raise TypeError("optional receipt text fields must be exact strings")
        evidence_refs = payload["evidence_refs"]
        if type(evidence_refs) is not list or any(type(item) is not str or not item.strip() for item in evidence_refs):
            raise TypeError("receipt evidence_refs must be an exact list of non-empty strings")
        if evidence_refs != sorted(set(evidence_refs)):
            raise ValueError("receipt evidence_refs must be sorted and unique")
        return ResidentGoalAttemptReceiptV1(
            receipt_id=payload["receipt_id"],
            workspace=payload["workspace"],
            goal_id=payload["goal_id"],
            attempt_id=payload["attempt_id"],
            run_id=payload["run_id"],
            operation=payload["operation"],
            status=ResidentGoalAttemptStatusV1(payload["status"]),
            execution_status=ResidentGoalExecutionStatusV1(payload["execution_status"]),
            revision=payload["revision"],
            attempt_number=payload["attempt_number"],
            max_attempts=payload["max_attempts"],
            no_progress_limit=payload["no_progress_limit"],
            no_progress_fingerprint=payload["no_progress_fingerprint"],
            no_progress_streak=payload["no_progress_streak"],
            idempotency_key=payload["idempotency_key"],
            semantic_hash=payload["semantic_hash"],
            record_hash=record["record_hash"],
            recorded_at=payload["recorded_at"],
            evidence_refs=tuple(evidence_refs),
            error=payload["error"],
        )
    except (KeyError, TypeError, ValueError, ResidentGoalLifecycleErrorV1) as exc:
        raise ResidentGoalLifecycleErrorV1(
            "goal_attempt_stream_corrupt",
            f"invalid receipt in Goal attempt stream: {exc}",
        ) from exc


def _read_records_bytes(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ResidentGoalLifecycleErrorV1(
            "goal_attempt_stream_corrupt",
            "Goal attempt stream is not strict UTF-8",
        ) from exc
    records: list[dict[str, Any]] = []
    previous_hash = ""
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_stream_corrupt",
                f"blank Goal attempt stream line {line_number}",
            )
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_stream_corrupt",
                f"invalid Goal attempt JSON at line {line_number}",
            ) from exc
        if type(record) is not dict:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "stream line must be an object")
        required = {
            "schema_version",
            "revision",
            "previous_hash",
            "idempotency_key",
            "semantic_hash",
            "receipt",
            "record_hash",
        }
        if set(record) != required or record["schema_version"] != _STREAM_SCHEMA:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "invalid stream line schema")
        if type(record["revision"]) is not int or record["revision"] != line_number:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "non-contiguous stream revision")
        if record["previous_hash"] != previous_hash:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "broken stream hash chain")
        stored_hash = record["record_hash"]
        if type(stored_hash) is not str or len(stored_hash) != 64:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "invalid stream record hash")
        hash_payload = dict(record)
        hash_payload.pop("record_hash")
        if _record_hash(hash_payload) != stored_hash:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "stream record hash mismatch")
        receipt = _receipt_from_record(record)
        if receipt.revision != record["revision"]:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "receipt revision mismatch")
        if receipt.idempotency_key != record["idempotency_key"] or receipt.semantic_hash != record["semantic_hash"]:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_stream_corrupt", "receipt identity mismatch")
        previous_hash = stored_hash
        records.append(record)
    return records


def _read_locked(handle: BinaryIO) -> list[dict[str, Any]]:
    handle.seek(0)
    return _read_records_bytes(handle.read())


def _project(workspace: str, goal_id: str, records: list[dict[str, Any]]) -> ResidentGoalExecutionV1:
    receipts = tuple(_receipt_from_record(record) for record in records)
    if not receipts:
        return ResidentGoalExecutionV1(
            workspace=_canonical_workspace(workspace),
            goal_id=goal_id,
            status=ResidentGoalExecutionStatusV1.READY,
            revision=0,
            attempt_count=0,
            max_attempts=0,
            no_progress_limit=0,
            no_progress_fingerprint="",
            no_progress_streak=0,
            active_attempt_id="",
        )
    attempts: dict[str, tuple[int, str]] = {}
    canonical_workspace = _canonical_workspace(workspace)
    for receipt in receipts:
        if receipt.workspace != canonical_workspace:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_stream_corrupt",
                "receipt workspace identity differs from stream identity",
            )
        if receipt.goal_id != goal_id:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_stream_corrupt",
                "receipt goal identity differs from stream identity",
            )
        identity = (receipt.attempt_number, receipt.run_id)
        previous = attempts.get(receipt.attempt_id)
        if previous is not None and previous != identity:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_stream_corrupt",
                "attempt identity changed within durable stream",
            )
        attempts[receipt.attempt_id] = identity
    last = receipts[-1]
    return ResidentGoalExecutionV1(
        workspace=canonical_workspace,
        goal_id=goal_id,
        status=last.execution_status,
        revision=last.revision,
        attempt_count=last.attempt_number,
        max_attempts=last.max_attempts,
        no_progress_limit=last.no_progress_limit,
        no_progress_fingerprint=last.no_progress_fingerprint,
        no_progress_streak=last.no_progress_streak,
        active_attempt_id=last.attempt_id if last.execution_status is ResidentGoalExecutionStatusV1.ACTIVE else "",
        receipts=receipts,
    )


def query_goal_execution(query: QueryResidentGoalExecutionV1) -> ResidentGoalExecutionV1:
    path = _stream_path(query.workspace, query.goal_id)
    if not path.is_file():
        return _project(query.workspace, query.goal_id, [])
    try:
        with path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            records = _read_locked(handle)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise ResidentGoalLifecycleErrorV1(
            "goal_attempt_stream_corrupt",
            f"Goal attempt stream cannot be read: {exc}",
        ) from exc
    return _project(query.workspace, query.goal_id, records)


def _check_idempotency(
    records: list[dict[str, Any]],
    *,
    idempotency_key: str,
    semantic_hash: str,
) -> ResidentGoalAttemptReceiptV1 | None:
    for record in records:
        if record["idempotency_key"] != idempotency_key:
            continue
        if record["semantic_hash"] != semantic_hash:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_idempotency_conflict",
                "idempotency key already binds a different semantic payload",
            )
        return _receipt_from_record(record)
    return None


def _append_command(
    *,
    workspace: str,
    goal_id: str,
    idempotency_key: str,
    semantic_hash: str,
    expected_revision: int,
    build_receipt: Any,
) -> ResidentGoalAttemptReceiptV1:
    path = _stream_path(workspace, goal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            records = _read_locked(handle)
            replay = _check_idempotency(
                records,
                idempotency_key=idempotency_key,
                semantic_hash=semantic_hash,
            )
            if replay is not None:
                return replay
            current_revision = len(records)
            if current_revision != expected_revision:
                raise ResidentGoalLifecycleErrorV1(
                    "goal_revision_conflict",
                    f"expected revision {expected_revision}, found {current_revision}",
                )
            execution = _project(workspace, goal_id, records)
            receipt = build_receipt(execution, current_revision + 1, semantic_hash)
            record_without_hash = {
                "schema_version": _STREAM_SCHEMA,
                "revision": receipt.revision,
                "previous_hash": records[-1]["record_hash"] if records else "",
                "idempotency_key": idempotency_key,
                "semantic_hash": semantic_hash,
                "receipt": _receipt_payload(receipt),
            }
            record = {**record_without_hash, "record_hash": _record_hash(record_without_hash)}
            handle.seek(0, os.SEEK_END)
            handle.write(_canonical_json(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            return _receipt_from_record(record)
    except ResidentGoalLifecycleErrorV1:
        raise
    except OSError as exc:
        raise ResidentGoalLifecycleErrorV1(
            "goal_attempt_stream_write_failed",
            f"Goal attempt stream cannot be written: {exc}",
        ) from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_receipt(
    *,
    workspace: str,
    goal_id: str,
    attempt_id: str,
    run_id: str,
    operation: str,
    status: ResidentGoalAttemptStatusV1,
    execution_status: ResidentGoalExecutionStatusV1,
    revision: int,
    attempt_number: int,
    max_attempts: int,
    no_progress_limit: int,
    no_progress_fingerprint: str,
    no_progress_streak: int,
    idempotency_key: str,
    semantic_hash: str,
    evidence_refs: tuple[str, ...] = (),
    error: str = "",
) -> ResidentGoalAttemptReceiptV1:
    return ResidentGoalAttemptReceiptV1(
        receipt_id=f"resident-goal-attempt-{semantic_hash[:24]}",
        workspace=_canonical_workspace(workspace),
        goal_id=goal_id,
        attempt_id=attempt_id,
        run_id=run_id,
        operation=operation,
        status=status,
        execution_status=execution_status,
        revision=revision,
        attempt_number=attempt_number,
        max_attempts=max_attempts,
        no_progress_limit=no_progress_limit,
        no_progress_fingerprint=no_progress_fingerprint,
        no_progress_streak=no_progress_streak,
        idempotency_key=idempotency_key,
        semantic_hash=semantic_hash,
        record_hash="pending",
        recorded_at=_now(),
        evidence_refs=evidence_refs,
        error=error,
    )


def start_goal_attempt(command: StartResidentGoalAttemptCommandV1) -> ResidentGoalAttemptReceiptV1:
    payload = {
        "workspace": _canonical_workspace(command.workspace),
        "goal_id": command.goal_id,
        "run_id": command.run_id,
        "expected_revision": command.expected_revision,
        "max_attempts": command.max_attempts,
        "no_progress_limit": command.no_progress_limit,
        "evidence_refs": list(command.evidence_refs),
    }
    semantic_hash = _semantic_hash("start", payload)

    def build(execution: ResidentGoalExecutionV1, revision: int, value_hash: str) -> ResidentGoalAttemptReceiptV1:
        if execution.status not in {
            ResidentGoalExecutionStatusV1.READY,
            ResidentGoalExecutionStatusV1.RETRY_ELIGIBLE,
        }:
            raise ResidentGoalLifecycleErrorV1(
                "invalid_goal_execution_transition",
                f"cannot start attempt from {execution.status.value}",
            )
        if execution.attempt_count and command.max_attempts != execution.max_attempts:
            raise ResidentGoalLifecycleErrorV1(
                "goal_attempt_budget_conflict",
                "max_attempts must match the durable execution budget",
            )
        if execution.attempt_count >= command.max_attempts:
            raise ResidentGoalLifecycleErrorV1("goal_attempt_budget_exhausted", "attempt budget is exhausted")
        attempt_number = execution.attempt_count + 1
        return _new_receipt(
            workspace=command.workspace,
            goal_id=command.goal_id,
            attempt_id=f"{command.goal_id}-attempt-{attempt_number}",
            run_id=command.run_id,
            operation="start",
            status=ResidentGoalAttemptStatusV1.ACTIVE,
            execution_status=ResidentGoalExecutionStatusV1.ACTIVE,
            revision=revision,
            attempt_number=attempt_number,
            max_attempts=command.max_attempts,
            no_progress_limit=command.no_progress_limit,
            no_progress_fingerprint="",
            no_progress_streak=0,
            idempotency_key=command.idempotency_key,
            semantic_hash=value_hash,
            evidence_refs=command.evidence_refs,
        )

    return _append_command(
        workspace=command.workspace,
        goal_id=command.goal_id,
        idempotency_key=command.idempotency_key,
        semantic_hash=semantic_hash,
        expected_revision=command.expected_revision,
        build_receipt=build,
    )


def observe_goal_attempt(command: ObserveResidentGoalAttemptCommandV1) -> ResidentGoalAttemptReceiptV1:
    payload = {
        "workspace": _canonical_workspace(command.workspace),
        "goal_id": command.goal_id,
        "attempt_id": command.attempt_id,
        "expected_revision": command.expected_revision,
        "progress_fingerprint": command.progress_fingerprint,
        "evidence_refs": list(command.evidence_refs),
    }
    semantic_hash = _semantic_hash("observe", payload)

    def build(execution: ResidentGoalExecutionV1, revision: int, value_hash: str) -> ResidentGoalAttemptReceiptV1:
        if (
            execution.status is not ResidentGoalExecutionStatusV1.ACTIVE
            or execution.active_attempt_id != command.attempt_id
        ):
            raise ResidentGoalLifecycleErrorV1(
                "invalid_goal_execution_transition",
                "observation requires the active durable attempt",
            )
        streak = (
            execution.no_progress_streak + 1 if execution.no_progress_fingerprint == command.progress_fingerprint else 0
        )
        blocked = streak >= execution.no_progress_limit
        return _new_receipt(
            workspace=command.workspace,
            goal_id=command.goal_id,
            attempt_id=command.attempt_id,
            run_id=execution.receipts[-1].run_id,
            operation="observe",
            status=(ResidentGoalAttemptStatusV1.BLOCKED_NO_PROGRESS if blocked else ResidentGoalAttemptStatusV1.ACTIVE),
            execution_status=(
                ResidentGoalExecutionStatusV1.BLOCKED_NO_PROGRESS if blocked else ResidentGoalExecutionStatusV1.ACTIVE
            ),
            revision=revision,
            attempt_number=execution.attempt_count,
            max_attempts=execution.max_attempts,
            no_progress_limit=execution.no_progress_limit,
            no_progress_fingerprint=command.progress_fingerprint,
            no_progress_streak=streak,
            idempotency_key=command.idempotency_key,
            semantic_hash=value_hash,
            evidence_refs=command.evidence_refs,
        )

    return _append_command(
        workspace=command.workspace,
        goal_id=command.goal_id,
        idempotency_key=command.idempotency_key,
        semantic_hash=semantic_hash,
        expected_revision=command.expected_revision,
        build_receipt=build,
    )


def settle_goal_attempt(command: SettleResidentGoalAttemptCommandV1) -> ResidentGoalAttemptReceiptV1:
    payload = {
        "workspace": _canonical_workspace(command.workspace),
        "goal_id": command.goal_id,
        "attempt_id": command.attempt_id,
        "expected_revision": command.expected_revision,
        "status": command.status.value,
        "evidence_refs": list(command.evidence_refs),
        "error": command.error,
    }
    semantic_hash = _semantic_hash("settle", payload)

    def build(execution: ResidentGoalExecutionV1, revision: int, value_hash: str) -> ResidentGoalAttemptReceiptV1:
        if (
            execution.status is not ResidentGoalExecutionStatusV1.ACTIVE
            or execution.active_attempt_id != command.attempt_id
        ):
            raise ResidentGoalLifecycleErrorV1(
                "invalid_goal_execution_transition",
                "settlement requires the active durable attempt",
            )
        if command.status is ResidentGoalAttemptStatusV1.SUCCEEDED:
            execution_status = ResidentGoalExecutionStatusV1.AWAITING_OUTCOME_BINDING
        elif command.status is ResidentGoalAttemptStatusV1.FAILED:
            execution_status = (
                ResidentGoalExecutionStatusV1.EXHAUSTED
                if execution.attempt_count >= execution.max_attempts
                else ResidentGoalExecutionStatusV1.RETRY_ELIGIBLE
            )
        elif command.status is ResidentGoalAttemptStatusV1.CANCELLED:
            execution_status = ResidentGoalExecutionStatusV1.CANCELLED
        else:
            execution_status = ResidentGoalExecutionStatusV1.BLOCKED_NO_PROGRESS
        return _new_receipt(
            workspace=command.workspace,
            goal_id=command.goal_id,
            attempt_id=command.attempt_id,
            run_id=execution.receipts[-1].run_id,
            operation="settle",
            status=command.status,
            execution_status=execution_status,
            revision=revision,
            attempt_number=execution.attempt_count,
            max_attempts=execution.max_attempts,
            no_progress_limit=execution.no_progress_limit,
            no_progress_fingerprint=execution.no_progress_fingerprint,
            no_progress_streak=execution.no_progress_streak,
            idempotency_key=command.idempotency_key,
            semantic_hash=value_hash,
            evidence_refs=command.evidence_refs,
            error=command.error,
        )

    return _append_command(
        workspace=command.workspace,
        goal_id=command.goal_id,
        idempotency_key=command.idempotency_key,
        semantic_hash=semantic_hash,
        expected_revision=command.expected_revision,
        build_receipt=build,
    )


__all__ = [
    "observe_goal_attempt",
    "query_goal_execution",
    "settle_goal_attempt",
    "start_goal_attempt",
    "transition_goal_state",
]
