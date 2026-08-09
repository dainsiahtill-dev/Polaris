"""GR3B-B4/B5: managed-process orchestrator on public Cell ports.

Flow (fail-closed before side effects)::

    validate authority → refuse duplicate effect launch → spawn once
    → wait (timeout → terminate at most once) → content-addressed receipt
    → typed Run Ledger projection (pending does not re-spawn)

TaskRuntime DEO claim/commit is optional and injectable; when omitted, the
orchestrator still owns durable recovery journal + evidence + ledger projection.
Caller-supplied pass/fail/missing gate verdicts are rejected on the command.

Complexity: O(1) journal ops + O(process wait) + O(receipt JSON size).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from polaris.cells.audit.evidence.public.contracts import (
    MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1,
    PersistManagedProcessReceiptCommandV1,
)
from polaris.cells.audit.evidence.public.service import persist_managed_process_receipt
from polaris.cells.control_plane.run_ledger.public.contracts import ControlPlaneRunLedgerV1Error
from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle import (
    ProjectManagedProcessLifecycleCommandV1,
    derive_managed_process_evidence_presence,
    project_managed_process_lifecycle,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionBrokerError,
    ExecutionProcessStatusV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter

_JOURNAL_LOGICAL = "runtime/evidence/managed_process_effect_journal.json"
_FORBIDDEN_COMMAND_KEYS = frozenset(
    {
        "passed",
        "failed",
        "missing",
        "ok",
        "gate_ok",
        "gate_policy",
        "missing_required_modalities",
        "failed_required_modalities",
        "verdict",
        "success",
        "completed_verified",
    }
)


def _require_non_empty(name: str, value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{name} must be a non-empty string")
    return token


def _normalize_workspace(value: str) -> str:
    workspace = _require_non_empty("workspace", value)
    path = Path(workspace).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"workspace must be an existing directory: {path}")
    return str(path)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _receipt_hash(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(receipt).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ManagedProcessAuthorityV1:
    """Minimal authority binding checked before spawn (fail-closed)."""

    attempt_id: str
    lease_id: str
    effect_key: str
    lease_expires_at_unix: float
    authority_token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _require_non_empty("attempt_id", self.attempt_id))
        object.__setattr__(self, "lease_id", _require_non_empty("lease_id", self.lease_id))
        object.__setattr__(self, "effect_key", _require_non_empty("effect_key", self.effect_key))
        object.__setattr__(
            self,
            "authority_token",
            str(self.authority_token or "").strip(),
        )
        expires = float(self.lease_expires_at_unix)
        object.__setattr__(self, "lease_expires_at_unix", expires)


@dataclass(frozen=True, slots=True)
class RunManagedProcessCommandV1:
    """Public managed-process execution command (no gate-verdict fields)."""

    workspace: str
    run_id: str
    name: str
    args: tuple[str, ...]
    authority: ManagedProcessAuthorityV1
    timeout_seconds: float | None = 30.0
    task_id: str = ""
    project_id: str = ""
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        if type(self.authority) is not ManagedProcessAuthorityV1:
            raise TypeError("authority must be ManagedProcessAuthorityV1")
        args = tuple(str(item) for item in self.args if str(item).strip())
        if not args:
            raise ValueError("args must contain at least one command token")
        object.__setattr__(self, "args", args)
        if self.timeout_seconds is not None and float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")
        object.__setattr__(self, "timeout_seconds", self.timeout_seconds)
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "project_id", str(self.project_id or "").strip())
        object.__setattr__(
            self,
            "env",
            {str(k): str(v) for k, v in dict(self.env or {}).items() if str(k).strip()},
        )


@dataclass(frozen=True, slots=True)
class ManagedProcessExecutionResultV1:
    """Durable outcome of one managed-process attempt."""

    code: str
    spawned: bool
    exit_code: int | None
    timed_out: bool
    terminate_count: int
    receipt_hash: str | None
    receipt_ref: str | None
    evidence_presence: str | None
    missing_evidence: bool
    ledger_projected: bool
    ledger_projection_pending: bool
    process_ok: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ManagedProcessRunnerPortV1(Protocol):
    """Injectable process backend (default: ExecutionBrokerService)."""

    async def launch(
        self,
        *,
        name: str,
        args: tuple[str, ...],
        workspace: str,
        timeout_seconds: float | None,
        env: Mapping[str, str],
    ) -> tuple[bool, str | None, str | None]:
        """Return (ok, execution_id, error_message)."""

    async def wait(
        self,
        execution_id: str,
        *,
        timeout_seconds: float | None,
    ) -> tuple[int | None, bool, bool]:
        """Return (exit_code, timed_out, success)."""

    async def terminate(self, execution_id: str) -> bool:
        """Terminate once; return whether terminate call succeeded."""


class _ExecutionBrokerRunner:
    async def launch(
        self,
        *,
        name: str,
        args: tuple[str, ...],
        workspace: str,
        timeout_seconds: float | None,
        env: Mapping[str, str],
    ) -> tuple[bool, str | None, str | None]:
        from polaris.cells.runtime.execution_broker.public.service import get_execution_broker_service

        service = get_execution_broker_service()
        result = await service.launch_process(
            LaunchExecutionProcessCommandV1(
                name=name,
                args=args,
                workspace=workspace,
                timeout_seconds=timeout_seconds,
                env=env,
            )
        )
        if not result.success or result.handle is None:
            return False, None, str(result.error_message or "launch_failed")
        return True, result.handle.execution_id, None

    async def wait(
        self,
        execution_id: str,
        *,
        timeout_seconds: float | None,
    ) -> tuple[int | None, bool, bool]:
        from polaris.cells.runtime.execution_broker.public.service import get_execution_broker_service

        service = get_execution_broker_service()
        waited = await service.wait_process(execution_id, timeout_seconds=timeout_seconds)
        timed_out = bool(waited.timed_out) or waited.status == ExecutionProcessStatusV1.TIMED_OUT
        return waited.exit_code, timed_out, bool(waited.success)

    async def terminate(self, execution_id: str) -> bool:
        from polaris.cells.runtime.execution_broker.public.service import get_execution_broker_service

        service = get_execution_broker_service()
        return bool(await service.terminate_process(execution_id))


def _journal_path(workspace: str) -> Path:
    return Path(workspace) / _JOURNAL_LOGICAL


def _load_journal(workspace: str) -> dict[str, Any]:
    path = _journal_path(workspace)
    if not path.is_file():
        return {"schema_version": "managed_process_effect_journal.v1", "effects": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionBrokerError(
            f"managed process journal unreadable: {exc}",
            code="execution_broker.managed_process_journal_corrupt",
        ) from exc
    if not isinstance(payload, dict):
        raise ExecutionBrokerError(
            "managed process journal must be an object",
            code="execution_broker.managed_process_journal_corrupt",
        )
    effects = payload.get("effects")
    if effects is None:
        payload["effects"] = {}
    elif not isinstance(effects, dict):
        raise ExecutionBrokerError(
            "managed process journal effects must be an object",
            code="execution_broker.managed_process_journal_corrupt",
        )
    return payload


def _save_journal(workspace: str, payload: dict[str, Any]) -> None:
    KernelFileSystem(workspace, get_default_adapter()).workspace_write_text_atomic(
        _JOURNAL_LOGICAL,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_authority(authority: ManagedProcessAuthorityV1, *, now: float) -> str | None:
    if not authority.authority_token:
        return "execution_broker.authority_token_missing"
    if now > float(authority.lease_expires_at_unix):
        return "execution_broker.authority_lease_expired"
    return None


def _reject_command_gate_verdicts(command: RunManagedProcessCommandV1) -> None:
    # Command is a typed dataclass without gate fields; defend against attrs smuggling
    # if someone subclasses or patches __dict__.
    raw = getattr(command, "__dict__", {})
    hits = sorted(key for key in _FORBIDDEN_COMMAND_KEYS if key in raw)
    if hits:
        raise ExecutionBrokerError(
            f"managed process command forbids caller gate verdicts: {','.join(hits)}",
            code="execution_broker.managed_process_forbids_caller_verdicts",
        )


async def _run_managed_process_async(
    command: RunManagedProcessCommandV1,
    *,
    runner: ManagedProcessRunnerPortV1,
    clock: Callable[[], float],
    project_ledger: bool,
) -> ManagedProcessExecutionResultV1:
    _reject_command_gate_verdicts(command)
    now = float(clock())
    auth_error = _validate_authority(command.authority, now=now)
    if auth_error is not None:
        return ManagedProcessExecutionResultV1(
            code=auth_error,
            spawned=False,
            exit_code=None,
            timed_out=False,
            terminate_count=0,
            receipt_hash=None,
            receipt_ref=None,
            evidence_presence=None,
            missing_evidence=True,
            ledger_projected=False,
            ledger_projection_pending=False,
            process_ok=False,
            details={"fail_before_spawn": True},
        )

    journal = _load_journal(command.workspace)
    effects: dict[str, Any] = journal.setdefault("effects", {})
    existing = effects.get(command.authority.effect_key)
    if isinstance(existing, dict):
        state = str(existing.get("state") or "")
        spawn_count = int(existing.get("spawn_count") or 0)
        # Projection-pending: receipt exists; only retry ledger, never re-spawn.
        if state == "ledger_projection_pending" and existing.get("receipt_hash"):
            return await _retry_ledger_projection_only(command, existing=existing, journal=journal)
        if spawn_count >= 1 or state in {
            "started",
            "receipt_committed",
            "ledger_projected",
            "failed_receipt",
        }:
            return ManagedProcessExecutionResultV1(
                code="execution_broker.managed_process_duplicate_launch_refused",
                spawned=False,
                exit_code=None if not existing.get("exit_code") else int(existing["exit_code"]),
                timed_out=bool(existing.get("timed_out")),
                terminate_count=int(existing.get("terminate_count") or 0),
                receipt_hash=str(existing.get("receipt_hash") or "") or None,
                receipt_ref=str(existing.get("receipt_ref") or "") or None,
                evidence_presence=str(existing.get("evidence_presence") or "") or None,
                missing_evidence=not bool(existing.get("receipt_hash")),
                ledger_projected=state == "ledger_projected",
                ledger_projection_pending=state == "ledger_projection_pending",
                process_ok=bool(existing.get("process_ok")),
                details={"prior_state": state, "spawn_count": spawn_count},
            )

    effect_row: dict[str, Any] = {
        "state": "started",
        "spawn_count": 0,
        "terminate_count": 0,
        "attempt_id": command.authority.attempt_id,
        "lease_id": command.authority.lease_id,
        "run_id": command.run_id,
        "name": command.name,
    }
    effects[command.authority.effect_key] = effect_row
    _save_journal(command.workspace, journal)

    launch_ok, execution_id, launch_error = await runner.launch(
        name=command.name,
        args=command.args,
        workspace=command.workspace,
        timeout_seconds=command.timeout_seconds,
        env=command.env,
    )
    if not launch_ok or not execution_id:
        receipt = {
            "effect_key": command.authority.effect_key,
            "attempt_id": command.authority.attempt_id,
            "run_id": command.run_id,
            "command": list(command.args),
            "launch_failed": True,
            "error": str(launch_error or "launch_failed"),
            "exit_code": None,
            "timeout": False,
            "cancelled": False,
        }
        # Launch failed after authority: durable failed receipt, no process.
        effect_row["state"] = "failed_receipt"
        return _persist_and_project(
            command,
            receipt=receipt,
            effect_row=effect_row,
            journal=journal,
            spawned=False,
            exit_code=1,
            timed_out=False,
            terminate_count=0,
            process_ok=False,
            code="execution_broker.managed_process_launch_failed",
            project_ledger=project_ledger,
        )

    effect_row["spawn_count"] = 1
    effect_row["execution_id"] = execution_id
    effect_row["state"] = "started"
    _save_journal(command.workspace, journal)

    exit_code, timed_out, process_ok = await runner.wait(
        execution_id,
        timeout_seconds=command.timeout_seconds,
    )
    terminate_count = 0
    if timed_out:
        await runner.terminate(execution_id)
        terminate_count = 1
        effect_row["terminate_count"] = 1
        # Ensure we never terminate twice on the managed path for this effect.
        process_ok = False

    receipt = {
        "effect_key": command.authority.effect_key,
        "attempt_id": command.authority.attempt_id,
        "run_id": command.run_id,
        "execution_id": execution_id,
        "command": list(command.args),
        "exit_code": exit_code if exit_code is not None else (1 if timed_out else None),
        "timeout": bool(timed_out),
        "timed_out": bool(timed_out),
        "cancelled": False,
        "terminate_count": terminate_count,
    }
    raw_exit = receipt.get("exit_code")
    if isinstance(raw_exit, bool):
        resolved_exit = int(raw_exit)
    elif isinstance(raw_exit, int):
        resolved_exit = raw_exit
    elif isinstance(raw_exit, str) and raw_exit.strip().lstrip("-").isdigit():
        resolved_exit = int(raw_exit)
    else:
        resolved_exit = 1 if timed_out or not process_ok else 0
    receipt["exit_code"] = resolved_exit

    code = (
        "execution_broker.managed_process_timed_out"
        if timed_out
        else (
            "execution_broker.managed_process_succeeded"
            if process_ok and resolved_exit == 0
            else "execution_broker.managed_process_failed"
        )
    )
    return _persist_and_project(
        command,
        receipt=receipt,
        effect_row=effect_row,
        journal=journal,
        spawned=True,
        exit_code=resolved_exit,
        timed_out=bool(timed_out),
        terminate_count=terminate_count,
        process_ok=bool(process_ok and resolved_exit == 0),
        code=code,
        project_ledger=project_ledger,
    )


async def _retry_ledger_projection_only(
    command: RunManagedProcessCommandV1,
    *,
    existing: Mapping[str, Any],
    journal: dict[str, Any],
) -> ManagedProcessExecutionResultV1:
    receipt_hash = str(existing.get("receipt_hash") or "")
    receipt_ref = str(existing.get("receipt_ref") or "")
    try:
        project_managed_process_lifecycle(
            ProjectManagedProcessLifecycleCommandV1(
                workspace=command.workspace,
                run_id=command.run_id,
                receipt_hash=receipt_hash,
                receipt_ref=receipt_ref or f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}",
                task_id=command.task_id,
                attempt_id=command.authority.attempt_id,
                project_id=command.project_id,
            )
        )
        effects = journal.setdefault("effects", {})
        row = dict(existing)
        row["state"] = "ledger_projected"
        effects[command.authority.effect_key] = row
        _save_journal(command.workspace, journal)
        return ManagedProcessExecutionResultV1(
            code="execution_broker.managed_process_ledger_projected",
            spawned=False,
            exit_code=None if existing.get("exit_code") is None else int(existing["exit_code"]),
            timed_out=bool(existing.get("timed_out")),
            terminate_count=int(existing.get("terminate_count") or 0),
            receipt_hash=receipt_hash,
            receipt_ref=receipt_ref or f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}",
            evidence_presence=str(existing.get("evidence_presence") or "") or None,
            missing_evidence=False,
            ledger_projected=True,
            ledger_projection_pending=False,
            process_ok=bool(existing.get("process_ok")),
            details={"projection_retry": True, "spawn_count": int(existing.get("spawn_count") or 0)},
        )
    except (ControlPlaneRunLedgerV1Error, ValueError, TypeError, OSError, RuntimeError) as exc:
        return ManagedProcessExecutionResultV1(
            code="execution_broker.managed_process_ledger_projection_pending",
            spawned=False,
            exit_code=None if existing.get("exit_code") is None else int(existing["exit_code"]),
            timed_out=bool(existing.get("timed_out")),
            terminate_count=int(existing.get("terminate_count") or 0),
            receipt_hash=receipt_hash or None,
            receipt_ref=receipt_ref or None,
            evidence_presence=str(existing.get("evidence_presence") or "") or None,
            missing_evidence=False,
            ledger_projected=False,
            ledger_projection_pending=True,
            process_ok=bool(existing.get("process_ok")),
            details={"projection_retry": True, "error": str(exc)},
        )


def _persist_and_project(
    command: RunManagedProcessCommandV1,
    *,
    receipt: dict[str, Any],
    effect_row: dict[str, Any],
    journal: dict[str, Any],
    spawned: bool,
    exit_code: int | None,
    timed_out: bool,
    terminate_count: int,
    process_ok: bool,
    code: str,
    project_ledger: bool,
) -> ManagedProcessExecutionResultV1:
    # Ensure integer exit for presence of failed evidence (never missing).
    if receipt.get("exit_code") is None:
        receipt["exit_code"] = 1
    presence = derive_managed_process_evidence_presence(
        {
            "exit_code": receipt["exit_code"],
            "timeout": bool(receipt.get("timeout") or receipt.get("timed_out")),
            "cancelled": bool(receipt.get("cancelled")),
        }
    )
    persisted = persist_managed_process_receipt(
        PersistManagedProcessReceiptCommandV1(
            workspace=command.workspace,
            receipt=receipt,
        )
    )
    effect_row.update(
        {
            "state": "receipt_committed",
            "receipt_hash": persisted.receipt_hash,
            "receipt_ref": persisted.receipt_ref,
            "evidence_presence": presence,
            "exit_code": receipt.get("exit_code"),
            "timed_out": timed_out,
            "process_ok": process_ok,
            "terminate_count": terminate_count,
            "spawn_count": max(int(effect_row.get("spawn_count") or 0), 1 if spawned else 0),
        }
    )
    journal.setdefault("effects", {})[command.authority.effect_key] = effect_row
    _save_journal(command.workspace, journal)

    ledger_projected = False
    ledger_projection_pending = False
    if project_ledger:
        try:
            project_managed_process_lifecycle(
                ProjectManagedProcessLifecycleCommandV1(
                    workspace=command.workspace,
                    run_id=command.run_id,
                    receipt_hash=persisted.receipt_hash,
                    receipt_ref=persisted.receipt_ref,
                    task_id=command.task_id,
                    attempt_id=command.authority.attempt_id,
                    project_id=command.project_id,
                )
            )
            ledger_projected = True
            effect_row["state"] = "ledger_projected"
        except (ControlPlaneRunLedgerV1Error, ValueError, TypeError, OSError, RuntimeError) as exc:
            ledger_projection_pending = True
            effect_row["state"] = "ledger_projection_pending"
            effect_row["ledger_error"] = str(exc)
        journal.setdefault("effects", {})[command.authority.effect_key] = effect_row
        _save_journal(command.workspace, journal)

    return ManagedProcessExecutionResultV1(
        code=code
        if not ledger_projection_pending
        else "execution_broker.managed_process_ledger_projection_pending",
        spawned=spawned,
        exit_code=None if exit_code is None else int(exit_code),
        timed_out=timed_out,
        terminate_count=terminate_count,
        receipt_hash=persisted.receipt_hash,
        receipt_ref=persisted.receipt_ref,
        evidence_presence=presence,
        missing_evidence=False,
        ledger_projected=ledger_projected,
        ledger_projection_pending=ledger_projection_pending,
        process_ok=process_ok,
        details={"effect_key": command.authority.effect_key},
    )


def run_managed_process(
    command: RunManagedProcessCommandV1,
    *,
    runner: ManagedProcessRunnerPortV1 | None = None,
    clock: Callable[[], float] | None = None,
    project_ledger: bool = True,
) -> ManagedProcessExecutionResultV1:
    """Synchronous public entry for one managed-process effect."""

    if type(command) is not RunManagedProcessCommandV1:
        raise TypeError("command must be RunManagedProcessCommandV1")
    return asyncio.run(
        _run_managed_process_async(
            command,
            runner=runner or _ExecutionBrokerRunner(),
            clock=clock or time.time,
            project_ledger=project_ledger,
        )
    )


__all__ = [
    "ManagedProcessAuthorityV1",
    "ManagedProcessExecutionResultV1",
    "ManagedProcessRunnerPortV1",
    "RunManagedProcessCommandV1",
    "run_managed_process",
]
