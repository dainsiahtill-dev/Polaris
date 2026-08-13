"""Physical owner for exact project artifact and verifier receipts."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from polaris.cells.control_plane.verifier_policy.public import evaluate_verifier_proof
from polaris.cells.runtime.execution_broker.internal.project_verification_sandbox import (
    PreparedProjectVerificationSandbox,
    prepare_project_verification_sandbox,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessStatusV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ConsumeProjectVerificationCapabilityCommandV1,
    ProjectArtifactExecutionAuthorityV1,
    ProjectArtifactReceiptV1,
    ProjectVerificationArtifactInputV1,
    ProjectVerificationArtifactSnapshotV1,
    ProjectVerificationCapabilityConsumptionV1,
    ProjectVerificationExecutionAuthorityPortV1,
    ProjectVerificationExecutionAuthorityV1,
    ProjectVerificationExecutionResultV1,
    ProjectVerificationProcessResultV1,
    ProjectVerificationReceiptV1,
    QueryProjectArtifactReceiptV1,
    QueryProjectVerificationReceiptV1,
    RecordProjectArtifactCommandV1,
    ResolveProjectArtifactAuthorityQueryV1,
    ResolveProjectVerificationAuthorityQueryV1,
    RunProjectVerificationCommandV1,
)
from polaris.kernelone.storage import resolve_storage_roots

_DB_RELATIVE_PATH = "evidence/project_verification_receipts.sqlite3"
_AUTH_KEY_RELATIVE_PATH = "execution_broker/project_verification_receipt_hmac.key"
_PROVENANCE_SCHEMA = "runtime.execution_broker.project_verification_provenance.v1"
_MAX_TRANSIENT_ATTEMPTS = 3
_ENTRYPOINT_READINESS_SECONDS = 2.0
_RECEIPT_REF_PREFIX = "execution-broker://project-verification/"
_RECEIPT_SEAL = object()
_COMMAND_SEAL = object()
_CAPABILITY_COMMAND_SEAL = object()
_EXECUTION_AUTHORITY_PORT: ProjectVerificationExecutionAuthorityPortV1 | None = None


@dataclass(frozen=True, slots=True)
class _EffectReservation:
    state: str
    attempt_id: str | None
    attempt_number: int


class _ProjectVerificationProcessRunnerPortV1(Protocol):
    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1: ...


def _is_project_verification_receipt_seal(value: object | None) -> bool:
    """Private constructor check used by public immutable receipt types."""

    return value is _RECEIPT_SEAL


def _is_project_verification_command_seal(value: object | None) -> bool:
    """Private constructor check for broker-authorized execution commands."""

    return value is _COMMAND_SEAL


def _is_project_verification_capability_command_seal(value: object | None) -> bool:
    """Private constructor check for one-use capability consume commands."""

    return value is _CAPABILITY_COMMAND_SEAL


def bind_project_verification_execution_authority_port(
    port: ProjectVerificationExecutionAuthorityPortV1,
) -> None:
    """Bind trusted bootstrap authority exactly once."""

    global _EXECUTION_AUTHORITY_PORT
    if not isinstance(port, ProjectVerificationExecutionAuthorityPortV1):
        raise TypeError("port must satisfy ProjectVerificationExecutionAuthorityPortV1")
    if _EXECUTION_AUTHORITY_PORT is not None and _EXECUTION_AUTHORITY_PORT is not port:
        raise RuntimeError("project verification execution authority port is already bound")
    _EXECUTION_AUTHORITY_PORT = port


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _ensure_no_symlink(root: Path, relative_path: str) -> Path:
    candidate = root
    for part in Path(relative_path).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"project verification path must not traverse symlinks: {relative_path}")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"project verification path escapes workspace: {relative_path}") from exc
    return resolved


def _artifact_hash(workspace: str, relative_path: str) -> str:
    root = Path(workspace).resolve()
    path = _ensure_no_symlink(root, relative_path)
    if path.is_file():
        return _hash_file(path)
    if not path.is_dir():
        raise ValueError(f"artifact must be a regular file or directory: {relative_path}")
    rows: list[dict[str, str]] = []
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = child.relative_to(path).as_posix()
        if child.is_symlink():
            raise ValueError(f"artifact directory must not contain symlinks: {relative_path}/{relative}")
        if child.is_file():
            rows.append({"path": relative, "kind": "file", "sha256": _hash_file(child)})
        elif child.is_dir():
            rows.append({"path": relative, "kind": "directory", "sha256": ""})
        else:
            raise ValueError(f"artifact directory contains an unsupported entry: {relative_path}/{relative}")
    return _hash_payload({"domain": "runtime.execution_broker.artifact_tree.v1", "entries": rows})


def _snapshot_inputs(
    workspace: str,
    inputs: tuple[ProjectVerificationArtifactInputV1, ...],
) -> tuple[tuple[ProjectVerificationArtifactSnapshotV1, ...], str]:
    snapshots = tuple(
        ProjectVerificationArtifactSnapshotV1(
            obligation_id=item.obligation_id,
            path=item.path,
            artifact_hash=_artifact_hash(workspace, item.path),
        )
        for item in inputs
    )
    aggregate = _hash_payload(
        {
            "domain": "runtime.execution_broker.project_verification_input.v1",
            "artifacts": [
                {
                    "obligation_id": item.obligation_id,
                    "path": item.path,
                    "artifact_hash": item.artifact_hash,
                }
                for item in snapshots
            ],
        }
    )
    return snapshots, aggregate


def _authority_hash(
    command: (
        ProjectVerificationExecutionAuthorityV1 | QueryProjectVerificationReceiptV1 | RunProjectVerificationCommandV1
    ),
) -> str:
    return _hash_payload(
        {
            "domain": "polaris.project_completion_verification_command_authority.v1",
            "task_id": command.owner_task_id,
            "modality": command.modality,
            "argv": list(command.argv),
            "cwd": command.cwd,
        }
    )


def _require_exact_command_authority(
    command: (
        ProjectVerificationExecutionAuthorityV1 | QueryProjectVerificationReceiptV1 | RunProjectVerificationCommandV1
    ),
) -> None:
    if _authority_hash(command) != command.command_authority_hash:
        raise ValueError("command_authority_hash does not match exact owner_task_id/modality/argv/cwd")


def _require_executable_identity(
    command: ProjectVerificationExecutionAuthorityV1
    | QueryProjectVerificationReceiptV1
    | RunProjectVerificationCommandV1,
) -> str:
    selected = Path(command.executable_path)
    realpath = selected.resolve(strict=True)
    if str(realpath) != command.executable_realpath or not realpath.is_file():
        raise ValueError("verifier executable realpath changed")
    if _hash_file(realpath) != command.executable_hash:
        raise ValueError("verifier executable content changed")
    return str(selected)


def _db_path(workspace: str) -> Path:
    roots = resolve_storage_roots(workspace)
    return Path(roots.runtime_root) / _DB_RELATIVE_PATH


def _auth_key_path(workspace: str) -> Path:
    roots = resolve_storage_roots(workspace)
    return Path(roots.config_root) / _AUTH_KEY_RELATIVE_PATH


def _receipt_auth_key(workspace: str) -> bytes:
    """Load the platform-owned receipt MAC key, creating it with owner-only mode."""

    path = _auth_key_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            os.write(descriptor, secrets.token_bytes(32).hex().encode("ascii"))
        finally:
            os.close(descriptor)
    raw = path.read_text(encoding="ascii").strip()
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError("authenticated receipt provenance key is malformed") from exc
    if len(key) != 32:
        raise ValueError("authenticated receipt provenance key must contain 32 bytes")
    return key


def _connect(workspace: str) -> sqlite3.Connection:
    path = _db_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS project_verification_receipt_events (
            sequence INTEGER PRIMARY KEY NOT NULL,
            effect_key TEXT NOT NULL,
            event_json TEXT NOT NULL,
            previous_auth_hash TEXT NOT NULL,
            auth_hash TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_verification_receipt_events_effect "
        "ON project_verification_receipt_events(effect_key, sequence)"
    )
    return connection


def _provenance_auth_hash(
    *,
    key: bytes,
    sequence: int,
    effect_key: str,
    event_json: str,
    previous_auth_hash: str,
) -> str:
    authenticated = _canonical_json(
        {
            "domain": _PROVENANCE_SCHEMA,
            "sequence": sequence,
            "effect_key": effect_key,
            "event_json": event_json,
            "previous_auth_hash": previous_auth_hash,
        }
    )
    return hmac.new(key, authenticated.encode("utf-8"), hashlib.sha256).hexdigest()


def _read_authenticated_events(
    connection: sqlite3.Connection,
    *,
    workspace: str,
) -> tuple[dict[str, Any], ...]:
    key = _receipt_auth_key(workspace)
    rows = connection.execute(
        "SELECT sequence, effect_key, event_json, previous_auth_hash, auth_hash "
        "FROM project_verification_receipt_events ORDER BY sequence"
    ).fetchall()
    events: list[dict[str, Any]] = []
    expected_sequence = 1
    previous_auth_hash = "0" * 64
    for row in rows:
        sequence = int(row[0])
        effect_key = str(row[1])
        event_json = str(row[2])
        stored_previous = str(row[3])
        stored_auth_hash = str(row[4])
        expected_auth_hash = _provenance_auth_hash(
            key=key,
            sequence=sequence,
            effect_key=effect_key,
            event_json=event_json,
            previous_auth_hash=stored_previous,
        )
        if (
            sequence != expected_sequence
            or stored_previous != previous_auth_hash
            or not hmac.compare_digest(stored_auth_hash, expected_auth_hash)
        ):
            raise ValueError("authenticated receipt provenance chain validation failed")
        try:
            payload = json.loads(event_json)
        except json.JSONDecodeError as exc:
            raise ValueError("authenticated receipt provenance event is malformed") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != _PROVENANCE_SCHEMA:
            raise ValueError("authenticated receipt provenance event schema mismatch")
        if payload.get("effect_key") != effect_key:
            raise ValueError("authenticated receipt provenance effect identity mismatch")
        events.append(payload)
        expected_sequence += 1
        previous_auth_hash = stored_auth_hash
    return tuple(events)


def _append_authenticated_event(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    payload: dict[str, Any],
) -> None:
    rows = _read_authenticated_events(connection, workspace=workspace)
    del rows
    tail = connection.execute(
        "SELECT sequence, auth_hash FROM project_verification_receipt_events ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    sequence = int(tail[0]) + 1 if tail is not None else 1
    previous_auth_hash = str(tail[1]) if tail is not None else "0" * 64
    event_json = _canonical_json(payload)
    effect_key = str(payload["effect_key"])
    auth_hash = _provenance_auth_hash(
        key=_receipt_auth_key(workspace),
        sequence=sequence,
        effect_key=effect_key,
        event_json=event_json,
        previous_auth_hash=previous_auth_hash,
    )
    connection.execute(
        "INSERT INTO project_verification_receipt_events "
        "(sequence, effect_key, event_json, previous_auth_hash, auth_hash) VALUES (?, ?, ?, ?, ?)",
        (sequence, effect_key, event_json, previous_auth_hash, auth_hash),
    )


def _latest_event(events: tuple[dict[str, Any], ...], effect_key: str) -> dict[str, Any] | None:
    return next((item for item in reversed(events) if item.get("effect_key") == effect_key), None)


def _read_row(workspace: str, effect_key: str) -> tuple[str, str, str | None, str | None] | None:
    connection = _connect(workspace)
    try:
        event = _latest_event(_read_authenticated_events(connection, workspace=workspace), effect_key)
    finally:
        connection.close()
    if event is None:
        return None
    expected_kind = effect_key.partition(":")[0]
    if event.get("kind") != expected_kind:
        raise ValueError("authenticated receipt provenance kind mismatch")
    receipt_payload = event.get("receipt_payload")
    return (
        str(event.get("state") or ""),
        str(event.get("request_hash") or ""),
        (str(event["receipt_hash"]) if event.get("receipt_hash") is not None else None),
        (_canonical_json(receipt_payload) if isinstance(receipt_payload, dict) else None),
    )


def _attempt_id(*, effect_key: str, request_hash: str, attempt_number: int) -> str:
    return _hash_payload(
        {
            "domain": "runtime.execution_broker.project_verification_attempt.v1",
            "effect_key": effect_key,
            "request_hash": request_hash,
            "attempt_number": attempt_number,
        }
    )


def _started_event(
    *,
    effect_key: str,
    kind: str,
    request_hash: str,
    attempt_number: int,
    lease_seconds: float,
) -> dict[str, Any]:
    return {
        "schema_version": _PROVENANCE_SCHEMA,
        "effect_key": effect_key,
        "kind": kind,
        "state": "started",
        "request_hash": request_hash,
        "attempt_id": _attempt_id(
            effect_key=effect_key,
            request_hash=request_hash,
            attempt_number=attempt_number,
        ),
        "attempt_number": attempt_number,
        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(),
        "retryable": False,
        "receipt_hash": None,
        "receipt_payload": None,
        "error_code": None,
    }


def _lease_is_live(event: dict[str, Any]) -> bool:
    raw = str(event.get("lease_expires_at") or "")
    try:
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expires_at > datetime.now(timezone.utc)


def _reserve(
    workspace: str,
    *,
    effect_key: str,
    kind: str,
    request_hash: str,
    lease_seconds: float = 30.0,
    retry_completed_failed_proof: bool = False,
) -> _EffectReservation:
    connection = _connect(workspace)
    try:
        connection.execute("BEGIN IMMEDIATE")
        event = _latest_event(_read_authenticated_events(connection, workspace=workspace), effect_key)
        if event is None:
            started = _started_event(
                effect_key=effect_key,
                kind=kind,
                request_hash=request_hash,
                attempt_number=1,
                lease_seconds=lease_seconds,
            )
            _append_authenticated_event(
                connection,
                workspace=workspace,
                payload=started,
            )
            connection.execute("COMMIT")
            return _EffectReservation("reserved", str(started["attempt_id"]), 1)
        state = str(event.get("state") or "")
        stored_request_hash = str(event.get("request_hash") or "")
        if stored_request_hash != request_hash or event.get("kind") != kind:
            raise ValueError("project verification effect key collided with a different request")
        attempt_number = int(event.get("attempt_number") or 0)
        receipt_payload = event.get("receipt_payload")
        if (
            state == "completed"
            and retry_completed_failed_proof
            and kind == "command"
            and isinstance(receipt_payload, dict)
            and receipt_payload.get("proof_satisfied") is False
            and attempt_number < _MAX_TRANSIENT_ATTEMPTS
        ):
            # A completed physical attempt whose verifier proof failed is an
            # immutable failed-evidence fact, not a permanently reusable
            # success. A later explicit execution request may open a bounded,
            # newly fenced attempt. The old receipt stays in the authenticated
            # append-only history; passive receipt queries never trigger work.
            next_attempt = attempt_number + 1
            started = _started_event(
                effect_key=effect_key,
                kind=kind,
                request_hash=request_hash,
                attempt_number=next_attempt,
                lease_seconds=lease_seconds,
            )
            _append_authenticated_event(connection, workspace=workspace, payload=started)
            connection.execute("COMMIT")
            return _EffectReservation("reserved", str(started["attempt_id"]), next_attempt)
        if state == "started" and not _lease_is_live(event):
            process_pid = event.get("process_pid")
            process_start_token = event.get("process_start_token")
            if process_pid is not None or process_start_token is not None:
                if not isinstance(process_pid, int) or not isinstance(process_start_token, str):
                    raise ValueError("expired project verification process fence is incomplete")
                _terminate_fenced_process(pid=process_pid, process_start_token=process_start_token)
            event = {
                **event,
                "state": "expired",
                "retryable": True,
                "error_code": "attempt_lease_expired",
            }
            _append_authenticated_event(connection, workspace=workspace, payload=event)
            state = "expired"
        if state in {"failed", "expired"} and event.get("retryable") is True:
            if attempt_number < _MAX_TRANSIENT_ATTEMPTS:
                next_attempt = attempt_number + 1
                started = _started_event(
                    effect_key=effect_key,
                    kind=kind,
                    request_hash=request_hash,
                    attempt_number=next_attempt,
                    lease_seconds=lease_seconds,
                )
                _append_authenticated_event(connection, workspace=workspace, payload=started)
                connection.execute("COMMIT")
                return _EffectReservation("reserved", str(started["attempt_id"]), next_attempt)
            state = "failed"
        connection.execute("COMMIT")
        return _EffectReservation(state, str(event.get("attempt_id") or "") or None, attempt_number)
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _commit_receipt(
    workspace: str,
    *,
    effect_key: str,
    request_hash: str,
    receipt_hash: str,
    receipt_payload: dict[str, Any],
    attempt_id: str,
) -> None:
    connection = _connect(workspace)
    try:
        connection.execute("BEGIN IMMEDIATE")
        event = _latest_event(_read_authenticated_events(connection, workspace=workspace), effect_key)
        if (
            event is None
            or event.get("state") != "started"
            or event.get("request_hash") != request_hash
            or event.get("attempt_id") != attempt_id
        ):
            raise ValueError("project verification reservation changed before receipt commit")
        _append_authenticated_event(
            connection,
            workspace=workspace,
            payload={
                **event,
                "state": "completed",
                "receipt_hash": receipt_hash,
                "receipt_payload": receipt_payload,
                "error_code": None,
            },
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _mark_failed(
    workspace: str,
    *,
    effect_key: str,
    request_hash: str,
    attempt_id: str,
    error_code: str,
    retryable: bool,
) -> None:
    connection = _connect(workspace)
    try:
        connection.execute("BEGIN IMMEDIATE")
        event = _latest_event(_read_authenticated_events(connection, workspace=workspace), effect_key)
        if (
            event is not None
            and event.get("request_hash") == request_hash
            and event.get("state") == "started"
            and event.get("attempt_id") == attempt_id
        ):
            _append_authenticated_event(
                connection,
                workspace=workspace,
                payload={**event, "state": "failed", "retryable": retryable, "error_code": error_code},
            )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _mark_spawned(
    workspace: str,
    *,
    effect_key: str,
    request_hash: str,
    attempt_id: str,
    execution_id: str,
    pid: int | None,
    process_start_token: str | None,
) -> None:
    """Durably fence the physical process before the owner begins waiting."""

    if pid is not None and process_start_token is None:
        raise ValueError("physical verifier PID requires a durable process start token")

    connection = _connect(workspace)
    try:
        connection.execute("BEGIN IMMEDIATE")
        event = _latest_event(_read_authenticated_events(connection, workspace=workspace), effect_key)
        if (
            event is None
            or event.get("request_hash") != request_hash
            or event.get("state") != "started"
            or event.get("attempt_id") != attempt_id
        ):
            raise ValueError("project verification reservation changed before process fence")
        _append_authenticated_event(
            connection,
            workspace=workspace,
            payload={
                **event,
                "process_execution_id": execution_id,
                "process_pid": pid,
                "process_start_token": process_start_token,
            },
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _process_start_token(pid: int | None) -> str | None:
    """Return Linux proc start-time token, preventing PID-reuse confusion."""

    if pid is None or pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[1].split()
        return fields[19]
    except (FileNotFoundError, IndexError, OSError):
        return None


def _terminate_fenced_process(*, pid: int, process_start_token: str) -> None:
    """Terminate only the exact previously recorded process, never a reused PID."""

    if _process_start_token(pid) != process_start_token:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(20):
        if _process_start_token(pid) != process_start_token:
            return
        time.sleep(0.05)
    if _process_start_token(pid) == process_start_token:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _artifact_request_body(
    command: RecordProjectArtifactCommandV1 | QueryProjectArtifactReceiptV1,
    *,
    artifact_hash: str,
    authority: ProjectArtifactExecutionAuthorityV1,
) -> dict[str, Any]:
    return {
        "schema_version": "runtime.execution_broker.project_artifact_request.v1",
        "workspace": command.workspace,
        "project_id": command.project_id,
        "run_id": command.run_id,
        "completion_contract_hash": command.completion_contract_hash,
        "obligation_id": command.obligation_id,
        "owner_task_id": command.owner_task_id,
        "path": command.path,
        "artifact_hash": artifact_hash,
        "job_token_id": authority.job_token_id,
        "job_token_set_hash": authority.job_token_set_hash,
        "execution_policy_hash": authority.execution_policy_hash,
        "authority_revision": authority.authority_revision,
    }


def _artifact_receipt_body(request_body: dict[str, Any]) -> dict[str, Any]:
    return {
        **request_body,
        "schema_version": "runtime.execution_broker.project_artifact_receipt.v1",
        "owner_module_id": "runtime.execution_broker",
    }


def _build_artifact_receipt(payload: dict[str, Any], receipt_hash: str) -> ProjectArtifactReceiptV1:
    body = dict(payload)
    if body.pop("schema_version", None) != "runtime.execution_broker.project_artifact_receipt.v1":
        raise ValueError("project artifact receipt schema mismatch")
    if body.pop("owner_module_id", None) != "runtime.execution_broker":
        raise ValueError("project artifact receipt owner mismatch")
    if _hash_payload(payload) != receipt_hash:
        raise ValueError("project artifact receipt hash mismatch")
    # The request schema is persisted for audit but is not a constructor field.
    body.pop("schema_version", None)
    return ProjectArtifactReceiptV1(
        **body,
        receipt_hash=receipt_hash,
        receipt_ref=f"{_RECEIPT_REF_PREFIX}artifact/{receipt_hash}",
        _authority_token=_RECEIPT_SEAL,
    )


def record_project_artifact(command: RecordProjectArtifactCommandV1) -> ProjectArtifactReceiptV1:
    if type(command) is not RecordProjectArtifactCommandV1:
        raise TypeError("command must be an exact RecordProjectArtifactCommandV1")
    port = _EXECUTION_AUTHORITY_PORT
    if port is None:
        raise RuntimeError("project verification execution authority port is not bound")
    authority = port.resolve_project_artifact_authority(
        ResolveProjectArtifactAuthorityQueryV1(
            workspace=command.workspace,
            project_id=command.project_id,
            run_id=command.run_id,
            completion_contract_hash=command.completion_contract_hash,
            obligation_id=command.obligation_id,
        )
    )
    if type(authority) is not ProjectArtifactExecutionAuthorityV1:
        raise TypeError("artifact authority owner returned a lookalike")
    if (
        authority.workspace,
        authority.project_id,
        authority.run_id,
        authority.completion_contract_hash,
        authority.obligation_id,
        authority.owner_task_id,
        authority.path,
    ) != (
        command.workspace,
        command.project_id,
        command.run_id,
        command.completion_contract_hash,
        command.obligation_id,
        command.owner_task_id,
        command.path,
    ):
        raise ValueError("artifact authority changed or does not match the requested obligation")
    artifact_hash = _artifact_hash(command.workspace, command.path)
    request_body = _artifact_request_body(command, artifact_hash=artifact_hash, authority=authority)
    request_hash = _hash_payload(request_body)
    effect_key = f"artifact:{request_hash}"
    reservation = _reserve(
        command.workspace,
        effect_key=effect_key,
        kind="artifact",
        request_hash=request_hash,
    )
    if reservation.state == "completed":
        receipt = query_project_artifact_receipt(
            QueryProjectArtifactReceiptV1(
                workspace=command.workspace,
                project_id=command.project_id,
                run_id=command.run_id,
                completion_contract_hash=command.completion_contract_hash,
                obligation_id=command.obligation_id,
                owner_task_id=command.owner_task_id,
                path=command.path,
            )
        )
        if receipt is None:
            raise ValueError("completed artifact reservation lacks a current receipt")
        return receipt
    if reservation.state != "reserved" or reservation.attempt_id is None:
        raise ValueError(f"artifact receipt reservation is not reusable: {reservation.state}")
    payload = _artifact_receipt_body(request_body)
    receipt_hash = _hash_payload(payload)
    _commit_receipt(
        command.workspace,
        effect_key=effect_key,
        request_hash=request_hash,
        receipt_hash=receipt_hash,
        receipt_payload=payload,
        attempt_id=reservation.attempt_id,
    )
    return _build_artifact_receipt(payload, receipt_hash)


def query_project_artifact_receipt(query: QueryProjectArtifactReceiptV1) -> ProjectArtifactReceiptV1 | None:
    if type(query) is not QueryProjectArtifactReceiptV1:
        raise TypeError("query must be an exact QueryProjectArtifactReceiptV1")
    port = _EXECUTION_AUTHORITY_PORT
    if port is None:
        raise RuntimeError("project verification execution authority port is not bound")
    try:
        authority = port.resolve_project_artifact_authority(
            ResolveProjectArtifactAuthorityQueryV1(
                workspace=query.workspace,
                project_id=query.project_id,
                run_id=query.run_id,
                completion_contract_hash=query.completion_contract_hash,
                obligation_id=query.obligation_id,
            )
        )
        if type(authority) is not ProjectArtifactExecutionAuthorityV1 or (
            authority.owner_task_id,
            authority.path,
        ) != (query.owner_task_id, query.path):
            return None
        artifact_hash = _artifact_hash(query.workspace, query.path)
    except (FileNotFoundError, ValueError, OSError):
        return None
    request_body = _artifact_request_body(query, artifact_hash=artifact_hash, authority=authority)
    request_hash = _hash_payload(request_body)
    row = _read_row(query.workspace, f"artifact:{request_hash}")
    if row is None or row[0] != "completed" or row[1] != request_hash or row[2] is None or row[3] is None:
        return None
    payload = json.loads(row[3])
    if not isinstance(payload, dict):
        raise ValueError("project artifact receipt payload must be an object")
    receipt = _build_artifact_receipt(payload, row[2])
    expected_identity = (
        query.workspace,
        query.project_id,
        query.run_id,
        query.completion_contract_hash,
        query.obligation_id,
        query.owner_task_id,
        query.path,
        artifact_hash,
        authority.job_token_id,
        authority.job_token_set_hash,
        authority.execution_policy_hash,
        authority.authority_revision,
    )
    observed_identity = (
        receipt.workspace,
        receipt.project_id,
        receipt.run_id,
        receipt.completion_contract_hash,
        receipt.obligation_id,
        receipt.owner_task_id,
        receipt.path,
        receipt.artifact_hash,
        receipt.job_token_id,
        receipt.job_token_set_hash,
        receipt.execution_policy_hash,
        receipt.authority_revision,
    )
    return receipt if observed_identity == expected_identity else None


def _verification_request_body(
    command: RunProjectVerificationCommandV1 | QueryProjectVerificationReceiptV1,
    *,
    snapshots: tuple[ProjectVerificationArtifactSnapshotV1, ...],
    input_artifact_hash: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    return {
        "schema_version": "runtime.execution_broker.project_verification_request.v1",
        "workspace": command.workspace,
        "project_id": command.project_id,
        "run_id": command.run_id,
        "completion_contract_hash": command.completion_contract_hash,
        "obligation_id": command.obligation_id,
        "owner_task_id": command.owner_task_id,
        "modality": command.modality,
        "argv": list(command.argv),
        "cwd": command.cwd,
        "command_authority_hash": command.command_authority_hash,
        "job_token_id": command.job_token_id,
        "job_token_set_hash": command.job_token_set_hash,
        "execution_policy_hash": command.execution_policy_hash,
        "authority_revision": command.authority_revision,
        "policy_profile_id": command.policy_profile_id,
        "policy_decision_hash": command.policy_decision_hash,
        "executable_path": command.executable_path,
        "executable_realpath": command.executable_realpath,
        "executable_hash": command.executable_hash,
        "input_artifacts": [
            {
                "obligation_id": item.obligation_id,
                "path": item.path,
                "artifact_hash": item.artifact_hash,
            }
            for item in snapshots
        ],
        "input_artifact_hash": input_artifact_hash,
        "timeout_seconds": timeout_seconds,
    }


def _verification_receipt_body(
    request_body: dict[str, Any],
    *,
    result: ProjectVerificationProcessResultV1,
) -> dict[str, Any]:
    body = dict(request_body)
    body["schema_version"] = "runtime.execution_broker.project_verification_receipt.v1"
    body["owner_module_id"] = "runtime.execution_broker"
    body["exit_code"] = result.exit_code
    body["timed_out"] = result.timed_out
    body["output_hash"] = hashlib.sha256(result.output_bytes).hexdigest()
    body["proof_satisfied"] = evaluate_verifier_proof(
        profile_id=str(request_body["policy_profile_id"]),
        modality=str(request_body["modality"]),
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        output_bytes=result.output_bytes,
    )
    if (
        request_body["modality"] == "entrypoint"
        and result.readiness_satisfied
        and result.controlled_termination
        and not result.timed_out
    ):
        body["proof_satisfied"] = True
    body["process_pid"] = result.process_pid
    body["process_start_token"] = result.process_start_token
    body["readiness_probe_kind"] = result.readiness_probe_kind
    body["readiness_satisfied"] = result.readiness_satisfied
    body["controlled_termination"] = result.controlled_termination
    body["proof_evidence_hash"] = _hash_payload(
        {
            "domain": "runtime.execution_broker.verifier_proof.v1",
            "profile_id": request_body["policy_profile_id"],
            "modality": request_body["modality"],
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "output_hash": body["output_hash"],
            "proof_satisfied": body["proof_satisfied"],
            "process_pid": result.process_pid,
            "process_start_token": result.process_start_token,
            "readiness_probe_kind": result.readiness_probe_kind,
            "readiness_satisfied": result.readiness_satisfied,
            "controlled_termination": result.controlled_termination,
        }
    )
    return body


def _require_sandbox_matches_snapshot(
    prepared: PreparedProjectVerificationSandbox,
    snapshots: tuple[ProjectVerificationArtifactSnapshotV1, ...],
) -> None:
    observed = tuple(
        (str(original.relative_to(prepared.workspace)), digest) for original, _copy, digest in prepared.snapshots
    )
    expected = tuple((item.path, item.artifact_hash) for item in snapshots)
    if observed != expected:
        raise ValueError("immutable verifier sandbox does not match authoritative input snapshot")


def _build_verification_receipt(payload: dict[str, Any], receipt_hash: str) -> ProjectVerificationReceiptV1:
    body = dict(payload)
    if body.pop("schema_version", None) != "runtime.execution_broker.project_verification_receipt.v1":
        raise ValueError("project verification receipt schema mismatch")
    if body.pop("owner_module_id", None) != "runtime.execution_broker":
        raise ValueError("project verification receipt owner mismatch")
    if _hash_payload(payload) != receipt_hash:
        raise ValueError("project verification receipt hash mismatch")
    raw_artifacts = body.pop("input_artifacts", None)
    if not isinstance(raw_artifacts, list):
        raise ValueError("project verification receipt input_artifacts must be a list")
    snapshots = tuple(ProjectVerificationArtifactSnapshotV1(**dict(item)) for item in raw_artifacts)
    raw_argv = body.pop("argv", None)
    if not isinstance(raw_argv, list):
        raise ValueError("project verification receipt argv must be a list")
    return ProjectVerificationReceiptV1(
        **body,
        argv=tuple(str(item) for item in raw_argv),
        input_artifacts=snapshots,
        receipt_hash=receipt_hash,
        receipt_ref=f"{_RECEIPT_REF_PREFIX}command/{receipt_hash}",
        _authority_token=_RECEIPT_SEAL,
    )


class _ExecutionBrokerProjectVerificationRunner:
    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1:
        return asyncio.run(
            self._run_async(
                name=name,
                argv=argv,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                log_path=log_path,
                metadata=metadata,
                on_launched=on_launched,
            )
        )

    async def _run_async(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1:
        from polaris.cells.runtime.execution_broker.public.service import get_execution_broker_service

        service = get_execution_broker_service()
        launch = await service.launch_process(
            LaunchExecutionProcessCommandV1(
                name=name,
                args=argv,
                workspace=cwd,
                timeout_seconds=timeout_seconds,
                log_path=log_path,
                metadata={
                    "receipt_owner": "runtime.execution_broker",
                    "effect_kind": "project_verification",
                    **metadata,
                },
            )
        )
        if not launch.success or launch.handle is None:
            raise ValueError(f"physical project verifier launch failed: {launch.error_message or 'unknown'}")
        try:
            process_start_token = _process_start_token(launch.handle.pid)
            on_launched(
                launch.handle.execution_id,
                launch.handle.pid,
                process_start_token,
            )
        except Exception:
            await service.terminate_process(launch.handle)
            raise
        readiness_probe_kind = "none"
        readiness_satisfied = False
        controlled_termination = False
        if metadata.get("modality") == "entrypoint":
            if launch.handle.pid is None or process_start_token is None:
                await service.terminate_process(launch.handle)
                raise ValueError("entrypoint readiness requires durable PID/start-token identity")
            readiness_window = min(_ENTRYPOINT_READINESS_SECONDS, max(0.25, timeout_seconds / 4.0))
            waited = await service.wait_process(launch.handle, timeout_seconds=readiness_window)
            if waited.timed_out and waited.status == ExecutionProcessStatusV1.RUNNING:
                readiness_probe_kind = "process_liveness"
                readiness_satisfied = _process_start_token(launch.handle.pid) == process_start_token
                if not readiness_satisfied:
                    await service.terminate_process(launch.handle)
                    raise ValueError("entrypoint process identity changed before readiness")
                controlled_termination = await service.terminate_process(launch.handle)
                if not controlled_termination:
                    raise ValueError("ready entrypoint could not be terminated by its owner")
                waited = await service.wait_process(launch.handle, timeout_seconds=5.0)
        else:
            waited = await service.wait_process(launch.handle, timeout_seconds=timeout_seconds + 5.0)
        if waited.timed_out and waited.status not in {
            ExecutionProcessStatusV1.TIMED_OUT,
            ExecutionProcessStatusV1.CANCELLED,
        }:
            await service.terminate_process(launch.handle)
        output_path = Path(log_path)
        if not output_path.is_file():
            raise ValueError("physical project verifier output log is missing")
        return ProjectVerificationProcessResultV1(
            exit_code=waited.exit_code,
            timed_out=(waited.timed_out or waited.status == ExecutionProcessStatusV1.TIMED_OUT)
            and not controlled_termination,
            output_bytes=output_path.read_bytes(),
            process_pid=launch.handle.pid,
            process_start_token=process_start_token,
            readiness_probe_kind=readiness_probe_kind,
            readiness_satisfied=readiness_satisfied,
            controlled_termination=controlled_termination,
        )


def _resolved_cwd(workspace: str, cwd: str) -> str:
    root = Path(workspace).resolve()
    if cwd == ".":
        return str(root)
    path = _ensure_no_symlink(root, cwd)
    if not path.is_dir():
        raise ValueError("project verifier cwd must resolve to a directory")
    return str(path)


def _query_from_command(command: RunProjectVerificationCommandV1) -> QueryProjectVerificationReceiptV1:
    return QueryProjectVerificationReceiptV1(
        workspace=command.workspace,
        project_id=command.project_id,
        run_id=command.run_id,
        completion_contract_hash=command.completion_contract_hash,
        obligation_id=command.obligation_id,
        owner_task_id=command.owner_task_id,
        modality=command.modality,
        argv=command.argv,
        cwd=command.cwd,
        command_authority_hash=command.command_authority_hash,
        input_artifacts=command.input_artifacts,
        timeout_seconds=command.timeout_seconds,
        job_token_id=command.job_token_id,
        job_token_set_hash=command.job_token_set_hash,
        execution_policy_hash=command.execution_policy_hash,
        authority_revision=command.authority_revision,
        policy_profile_id=command.policy_profile_id,
        policy_decision_hash=command.policy_decision_hash,
        executable_path=command.executable_path,
        executable_realpath=command.executable_realpath,
        executable_hash=command.executable_hash,
    )


def authorize_project_verification_command(
    query: ResolveProjectVerificationAuthorityQueryV1,
) -> RunProjectVerificationCommandV1:
    """Resolve exact CE command and committed JobToken policy before spawn."""

    if type(query) is not ResolveProjectVerificationAuthorityQueryV1:
        raise TypeError("query must be an exact ResolveProjectVerificationAuthorityQueryV1")
    port = _EXECUTION_AUTHORITY_PORT
    if port is None:
        raise RuntimeError("project verification execution authority port is not bound")
    authority = port.resolve_project_verification_authority(query)
    if type(authority) is not ProjectVerificationExecutionAuthorityV1:
        raise TypeError("execution authority owner returned a lookalike")
    expected_identity = (
        query.workspace,
        query.project_id,
        query.run_id,
        query.completion_contract_hash,
        query.obligation_id,
    )
    observed_identity = (
        authority.workspace,
        authority.project_id,
        authority.run_id,
        authority.completion_contract_hash,
        authority.obligation_id,
    )
    if observed_identity != expected_identity:
        raise ValueError("execution authority owner returned mismatched project/run/contract/obligation identity")
    _require_exact_command_authority(authority)
    _require_executable_identity(authority)
    return RunProjectVerificationCommandV1(
        workspace=authority.workspace,
        project_id=authority.project_id,
        run_id=authority.run_id,
        completion_contract_hash=authority.completion_contract_hash,
        obligation_id=authority.obligation_id,
        owner_task_id=authority.owner_task_id,
        modality=authority.modality,
        argv=authority.argv,
        cwd=authority.cwd,
        command_authority_hash=authority.command_authority_hash,
        input_artifacts=authority.input_artifacts,
        timeout_seconds=authority.timeout_seconds,
        job_token_id=authority.job_token_id,
        job_token_set_hash=authority.job_token_set_hash,
        execution_policy_hash=authority.execution_policy_hash,
        authority_revision=authority.authority_revision,
        policy_profile_id=authority.policy_profile_id,
        policy_decision_hash=authority.policy_decision_hash,
        executable_path=authority.executable_path,
        executable_realpath=authority.executable_realpath,
        executable_hash=authority.executable_hash,
        _authority_token=_COMMAND_SEAL,
    )


def run_project_verification(
    command: RunProjectVerificationCommandV1,
) -> ProjectVerificationExecutionResultV1:
    if type(command) is not RunProjectVerificationCommandV1:
        raise TypeError("command must be an exact RunProjectVerificationCommandV1")
    fresh_command = authorize_project_verification_command(
        ResolveProjectVerificationAuthorityQueryV1(
            workspace=command.workspace,
            project_id=command.project_id,
            run_id=command.run_id,
            completion_contract_hash=command.completion_contract_hash,
            obligation_id=command.obligation_id,
        )
    )
    if fresh_command != command:
        raise ValueError("project verification execution authority changed before spawn")
    _require_exact_command_authority(command)
    executable_path = _require_executable_identity(command)
    snapshots, input_hash = _snapshot_inputs(command.workspace, command.input_artifacts)
    request_body = _verification_request_body(
        command,
        snapshots=snapshots,
        input_artifact_hash=input_hash,
        timeout_seconds=command.timeout_seconds,
    )
    request_hash = _hash_payload(request_body)
    effect_key = f"command:{request_hash}"
    reservation = _reserve(
        command.workspace,
        effect_key=effect_key,
        kind="command",
        request_hash=request_hash,
        lease_seconds=command.timeout_seconds + 30.0,
        retry_completed_failed_proof=True,
    )
    if reservation.state == "completed":
        receipt = query_project_verification_receipt(_query_from_command(command))
        return ProjectVerificationExecutionResultV1(
            code="project_verification_receipt_reused" if receipt is not None else "project_verification_receipt_stale",
            spawned=False,
            receipt=receipt,
        )
    if reservation.state == "started":
        return ProjectVerificationExecutionResultV1(
            code="project_verification_in_progress",
            spawned=False,
            receipt=None,
        )
    if reservation.state == "failed":
        return ProjectVerificationExecutionResultV1(
            code="project_verification_owner_execution_failed",
            spawned=False,
            receipt=None,
        )
    if reservation.state != "reserved" or reservation.attempt_id is None:
        raise ValueError(f"unsupported project verification reservation state: {reservation.state}")
    attempt_id = reservation.attempt_id
    port = _EXECUTION_AUTHORITY_PORT
    if port is None:
        raise RuntimeError("project verification execution authority port is not bound")
    prepared = prepare_project_verification_sandbox(
        workspace=command.workspace,
        inputs=command.input_artifacts,
        request_hash=request_hash,
        cwd=command.cwd,
    )
    _require_sandbox_matches_snapshot(prepared, snapshots)
    try:
        consumption = port.consume_project_verification_execution_capability(
            ConsumeProjectVerificationCapabilityCommandV1(
                workspace=command.workspace,
                project_id=command.project_id,
                run_id=command.run_id,
                completion_contract_hash=command.completion_contract_hash,
                obligation_id=command.obligation_id,
                owner_task_id=command.owner_task_id,
                modality=command.modality,
                argv=command.argv,
                cwd=command.cwd,
                command_authority_hash=command.command_authority_hash,
                input_artifacts=command.input_artifacts,
                timeout_seconds=command.timeout_seconds,
                job_token_id=command.job_token_id,
                job_token_set_hash=command.job_token_set_hash,
                execution_policy_hash=command.execution_policy_hash,
                authority_revision=command.authority_revision,
                policy_profile_id=command.policy_profile_id,
                policy_decision_hash=command.policy_decision_hash,
                executable_path=command.executable_path,
                executable_realpath=command.executable_realpath,
                executable_hash=command.executable_hash,
                effect_key=effect_key,
                attempt_id=attempt_id,
                _authority_token=_CAPABILITY_COMMAND_SEAL,
            )
        )
    except Exception:
        prepared.cleanup()
        _mark_failed(
            command.workspace,
            effect_key=effect_key,
            request_hash=request_hash,
            attempt_id=reservation.attempt_id,
            error_code="capability_consume_failed",
            retryable=False,
        )
        raise
    if type(consumption) is not ProjectVerificationCapabilityConsumptionV1:
        raise TypeError("capability owner returned a lookalike")
    expected_consumption = (
        effect_key,
        reservation.attempt_id,
        command.authority_revision,
        command.job_token_id,
        command.job_token_set_hash,
        command.execution_policy_hash,
        command.policy_profile_id,
        command.policy_decision_hash,
    )
    observed_consumption = (
        consumption.effect_key,
        consumption.attempt_id,
        consumption.authority_revision,
        consumption.job_token_id,
        consumption.job_token_set_hash,
        consumption.execution_policy_hash,
        consumption.policy_profile_id,
        consumption.policy_decision_hash,
    )
    if observed_consumption != expected_consumption:
        raise ValueError("capability owner returned mismatched fenced attempt identity")
    execution_runner: _ProjectVerificationProcessRunnerPortV1 = _ExecutionBrokerProjectVerificationRunner()
    cwd = _resolved_cwd(command.workspace, command.cwd)
    log_path = str(
        Path(command.workspace)
        / ".polaris"
        / "runtime"
        / "evidence"
        / "project-verification-output"
        / f"{request_hash}.log"
    )

    def _record_launch_and_release(
        execution_id: str,
        pid: int | None,
        process_start_token: str | None,
    ) -> None:
        _mark_spawned(
            command.workspace,
            effect_key=effect_key,
            request_hash=request_hash,
            attempt_id=attempt_id,
            execution_id=execution_id,
            pid=pid,
            process_start_token=process_start_token,
        )
        prepared.release_after_fence()

    try:
        result = execution_runner.run(
            name=f"project-verification-{request_hash[:16]}",
            argv=prepared.wrap_command(command.argv, executable_path=executable_path),
            cwd=cwd,
            timeout_seconds=command.timeout_seconds,
            log_path=log_path,
            metadata={
                "capability_id": consumption.capability_id,
                "attempt_id": consumption.attempt_id,
                "authority_revision": consumption.authority_revision,
                "job_token_id": consumption.job_token_id,
                "job_token_set_hash": consumption.job_token_set_hash,
                "execution_policy_hash": consumption.execution_policy_hash,
                "policy_profile_id": consumption.policy_profile_id,
                "policy_decision_hash": consumption.policy_decision_hash,
                "modality": command.modality,
                "sandbox": "bubblewrap-immutable-inputs-v1",
            },
            on_launched=_record_launch_and_release,
        )
        if type(result) is not ProjectVerificationProcessResultV1:
            raise TypeError("physical runner must return an exact ProjectVerificationProcessResultV1")
        prepared.assert_inputs_unchanged()
        post_snapshots, post_input_hash = _snapshot_inputs(command.workspace, command.input_artifacts)
        if post_snapshots != snapshots or post_input_hash != input_hash:
            raise ValueError("project verification input snapshot changed during physical execution")
        _require_executable_identity(command)
        post_run_command = authorize_project_verification_command(
            ResolveProjectVerificationAuthorityQueryV1(
                workspace=command.workspace,
                project_id=command.project_id,
                run_id=command.run_id,
                completion_contract_hash=command.completion_contract_hash,
                obligation_id=command.obligation_id,
            )
        )
        if post_run_command != command:
            raise ValueError("project verification execution authority changed during physical execution")
        payload = _verification_receipt_body(request_body, result=result)
        payload["capability_id"] = consumption.capability_id
        payload["attempt_id"] = consumption.attempt_id
        receipt_hash = _hash_payload(payload)
        _commit_receipt(
            command.workspace,
            effect_key=effect_key,
            request_hash=request_hash,
            receipt_hash=receipt_hash,
            receipt_payload=payload,
            attempt_id=reservation.attempt_id,
        )
    except Exception as exc:
        _mark_failed(
            command.workspace,
            effect_key=effect_key,
            request_hash=request_hash,
            attempt_id=reservation.attempt_id,
            error_code=type(exc).__name__,
            retryable=isinstance(exc, (OSError, TimeoutError, ConnectionError)),
        )
        raise
    finally:
        prepared.cleanup()
    return ProjectVerificationExecutionResultV1(
        code="project_verification_executed",
        spawned=True,
        receipt=_build_verification_receipt(payload, receipt_hash),
    )


def query_project_verification_receipt(
    query: QueryProjectVerificationReceiptV1,
) -> ProjectVerificationReceiptV1 | None:
    if type(query) is not QueryProjectVerificationReceiptV1:
        raise TypeError("query must be an exact QueryProjectVerificationReceiptV1")
    _require_exact_command_authority(query)
    try:
        current = authorize_project_verification_command(
            ResolveProjectVerificationAuthorityQueryV1(
                workspace=query.workspace,
                project_id=query.project_id,
                run_id=query.run_id,
                completion_contract_hash=query.completion_contract_hash,
                obligation_id=query.obligation_id,
            )
        )
    except (RuntimeError, TypeError, ValueError):
        return None
    expected_current = tuple(
        getattr(query, name) for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__
    )
    observed_current = tuple(
        getattr(current, name) for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__
    )
    if observed_current != expected_current:
        return None
    try:
        _require_executable_identity(query)
    except (FileNotFoundError, ValueError, OSError):
        return None
    try:
        snapshots, input_hash = _snapshot_inputs(query.workspace, query.input_artifacts)
    except (FileNotFoundError, ValueError, OSError):
        return None
    request_body = _verification_request_body(
        query,
        snapshots=snapshots,
        input_artifact_hash=input_hash,
        timeout_seconds=query.timeout_seconds,
    )
    request_hash = _hash_payload(request_body)
    row = _read_row(query.workspace, f"command:{request_hash}")
    if row is None or row[0] != "completed" or row[1] != request_hash or row[2] is None or row[3] is None:
        return None
    payload = json.loads(row[3])
    if not isinstance(payload, dict):
        raise ValueError("project verification receipt payload must be an object")
    receipt = _build_verification_receipt(payload, row[2])
    expected_identity = (
        query.workspace,
        query.project_id,
        query.run_id,
        query.completion_contract_hash,
        query.obligation_id,
        query.owner_task_id,
        query.modality,
        query.argv,
        query.cwd,
        query.command_authority_hash,
        query.job_token_id,
        query.job_token_set_hash,
        query.execution_policy_hash,
        query.authority_revision,
        query.policy_profile_id,
        query.policy_decision_hash,
        query.executable_path,
        query.executable_realpath,
        query.executable_hash,
        snapshots,
        input_hash,
        query.timeout_seconds,
    )
    observed_identity = (
        receipt.workspace,
        receipt.project_id,
        receipt.run_id,
        receipt.completion_contract_hash,
        receipt.obligation_id,
        receipt.owner_task_id,
        receipt.modality,
        receipt.argv,
        receipt.cwd,
        receipt.command_authority_hash,
        receipt.job_token_id,
        receipt.job_token_set_hash,
        receipt.execution_policy_hash,
        receipt.authority_revision,
        receipt.policy_profile_id,
        receipt.policy_decision_hash,
        receipt.executable_path,
        receipt.executable_realpath,
        receipt.executable_hash,
        receipt.input_artifacts,
        receipt.input_artifact_hash,
        receipt.timeout_seconds,
    )
    return receipt if observed_identity == expected_identity else None


__all__ = [
    "query_project_artifact_receipt",
    "query_project_verification_receipt",
    "record_project_artifact",
    "run_project_verification",
]
