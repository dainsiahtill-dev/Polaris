"""GR3B-B3: typed managed-process lifecycle projection onto Run Ledger.

Boundary:
    audit.evidence owns the full receipt body (content-addressed hash).
    Run Ledger only projects a committed receipt identity + derived presence
    class.  Nonzero exit is *present failed* evidence, never missing evidence.
    Generic append and tool-lifecycle append are not substitutes on this path.

Complexity:
    O(r + e) for receipt read and ledger event preparation where r is receipt
    JSON size and e is the existing Run Ledger append transaction cost.
"""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.audit.evidence.public.contracts import (
    MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1,
    ReadManagedProcessReceiptQueryV1,
)
from polaris.cells.audit.evidence.public.service import read_managed_process_receipt
from polaris.cells.control_plane.run_ledger.public.contracts import (
    AppendRunLedgerEventCommandV1,
    ControlPlaneRunLedgerV1Error,
    RunLedgerAppendResultV1,
)

MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE = "managed_process_lifecycle"
MANAGED_PROCESS_LIFECYCLE_SCHEMA_V1 = "managed_process_lifecycle.v1"

# Call-stack authorization for managed-process append.  Only
# ``project_managed_process_lifecycle`` sets this; public append never takes a
# caller-supplied authorize flag (skeptic: no public bool bypass).
_MANAGED_PROCESS_APPEND_AUTHORIZED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "polaris_managed_process_lifecycle_append_authorized",
    default=False,
)


def managed_process_append_is_authorized() -> bool:
    """Return whether the current call stack authorized a managed-process append."""

    return bool(_MANAGED_PROCESS_APPEND_AUTHORIZED.get())


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


def _require_lower_sha256(name: str, value: str) -> str:
    digest = str(value or "").strip()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return digest


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    if isinstance(value, list):
        return [_plain_json_value(item) for item in value]
    return value


def _require_receipt_ref(receipt_ref: str, *, receipt_hash: str) -> str:
    token = str(receipt_ref or "").strip()
    expected = f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}"
    if token != expected:
        raise ValueError("receipt_ref must bind the managed-process receipt hash")
    return token


@dataclass(frozen=True, slots=True)
class ProjectManagedProcessLifecycleCommandV1:
    """Project one committed managed-process receipt into the Run Ledger.

    Does not accept caller-supplied pass/fail/missing gate verdicts.  The
    receipt body is authoritative only after audit.evidence ownership is proven
    by content hash lookup.
    """

    workspace: str
    run_id: str
    receipt_hash: str
    receipt_ref: str = ""
    task_id: str = ""
    attempt_id: str = ""
    project_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        receipt_hash = _require_lower_sha256("receipt_hash", self.receipt_hash)
        object.__setattr__(self, "receipt_hash", receipt_hash)
        receipt_ref = str(self.receipt_ref or "").strip()
        if receipt_ref:
            receipt_ref = _require_receipt_ref(receipt_ref, receipt_hash=receipt_hash)
        else:
            receipt_ref = f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}"
        object.__setattr__(self, "receipt_ref", receipt_ref)
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "attempt_id", str(self.attempt_id or "").strip())
        object.__setattr__(self, "project_id", str(self.project_id or "").strip())


def derive_managed_process_evidence_presence(receipt: Mapping[str, Any]) -> str:
    """Map a durable receipt body to present evidence presence (never missing).

    A committed process receipt is always *present* evidence.  Nonzero exit,
    timeout, or cancel are present_failed.  Zero exit is present_succeeded.
    Absence of exit_code after a durable body still remains present (unknown
    outcome class), not missing modality.
    """

    if not isinstance(receipt, Mapping) or not receipt:
        raise ValueError("receipt must be a non-empty mapping")
    if bool(receipt.get("timeout")) or bool(receipt.get("timed_out")):
        return "present_failed"
    if bool(receipt.get("cancelled")) or bool(receipt.get("canceled")):
        return "present_failed"
    if "exit_code" not in receipt:
        return "present_unknown_outcome"
    try:
        exit_code = int(receipt.get("exit_code"))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("receipt.exit_code must be an integer when present") from exc
    if exit_code != 0:
        return "present_failed"
    return "present_succeeded"


def build_managed_process_lifecycle_event(
    *,
    run_id: str,
    receipt_hash: str,
    receipt_ref: str,
    receipt: Mapping[str, Any],
    task_id: str = "",
    attempt_id: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """Build the canonical managed-process lifecycle ledger event payload."""

    presence = derive_managed_process_evidence_presence(receipt)
    plain_receipt = _plain_json_value(receipt)
    if not isinstance(plain_receipt, dict):
        raise TypeError("receipt must project to a mapping")
    exit_code = plain_receipt.get("exit_code")
    return {
        "event_type": MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE,
        "schema_version": MANAGED_PROCESS_LIFECYCLE_SCHEMA_V1,
        "run_id": _require_non_empty("run_id", run_id),
        "task_id": str(task_id or "").strip(),
        "attempt_id": str(attempt_id or "").strip(),
        "project_id": str(project_id or "").strip(),
        "receipt_hash": _require_lower_sha256("receipt_hash", receipt_hash),
        "receipt_ref": _require_receipt_ref(receipt_ref, receipt_hash=receipt_hash),
        "evidence_presence": presence,
        # Explicit: nonzero exit is failed evidence present, never missing.
        "exit_code": exit_code,
        "missing_evidence": False,
        "managed_process_lifecycle": {
            "schema_version": MANAGED_PROCESS_LIFECYCLE_SCHEMA_V1,
            "receipt_hash": receipt_hash,
            "receipt_ref": receipt_ref,
            "evidence_presence": presence,
            "exit_code": exit_code,
            "missing_evidence": False,
            # Projection binds identity; full body remains owned by audit.evidence.
            "receipt_body_owned_by": "audit.evidence",
            "receipt_summary": {
                key: plain_receipt[key]
                for key in ("exit_code", "timeout", "timed_out", "cancelled", "canceled", "command")
                if key in plain_receipt
            },
        },
    }


def looks_like_managed_process_tool_lifecycle_substitute(payload: Mapping[str, Any] | None) -> bool:
    """Detect attempts to smuggle managed-process facts via tool-lifecycle."""

    if not isinstance(payload, Mapping):
        return False
    if str(payload.get("event_type") or "").strip() == MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE:
        return True
    if str(payload.get("schema_version") or "").strip() == MANAGED_PROCESS_LIFECYCLE_SCHEMA_V1:
        return True
    if payload.get("managed_process_lifecycle") is not None:
        return True
    if payload.get("managed_process_receipt_hash") or payload.get("receipt_hash_for_managed_process"):
        return True
    stage = str(payload.get("stage") or "").strip().lower()
    return stage in {"managed_process", "managed_process_lifecycle", "process_receipt"}


def project_managed_process_lifecycle(
    command: ProjectManagedProcessLifecycleCommandV1,
) -> RunLedgerAppendResultV1:
    """Public B3 entry: bind evidence ownership then append typed lifecycle fact.

    Hash ownership always goes through audit.evidence ``read_managed_process_receipt``.
    The durable receipt body may contain process-native fields such as ``ok`` or
    ``failed``; presence is derived only from exit_code/timeout/cancel.  Append
    authorization is a private ContextVar set only for this call stack — public
    ``append_run_ledger_event`` has no authorize parameter.
    """

    if type(command) is not ProjectManagedProcessLifecycleCommandV1:
        raise TypeError("command must be ProjectManagedProcessLifecycleCommandV1")

    record = read_managed_process_receipt(
        ReadManagedProcessReceiptQueryV1(
            workspace=command.workspace,
            receipt_hash=command.receipt_hash,
        )
    )
    if record is None:
        raise ControlPlaneRunLedgerV1Error(
            f"managed_process_receipt_not_found:workspace={command.workspace}:receipt_hash={command.receipt_hash}"
        )
    if record.receipt_hash != command.receipt_hash:
        raise ControlPlaneRunLedgerV1Error("managed_process_receipt_hash_mismatch")
    if command.receipt_ref and record.receipt_ref != command.receipt_ref:
        raise ControlPlaneRunLedgerV1Error("managed_process_receipt_ref_mismatch")

    receipt_map = dict(record.receipt) if isinstance(record.receipt, Mapping) else {}
    # Do NOT reject process-native receipt keys (ok/failed/success); those are
    # durable owner fields.  Presence class is derived from exit/timeout/cancel.
    event = build_managed_process_lifecycle_event(
        run_id=command.run_id,
        receipt_hash=record.receipt_hash,
        receipt_ref=record.receipt_ref,
        receipt=receipt_map,
        task_id=command.task_id,
        attempt_id=command.attempt_id,
        project_id=command.project_id,
    )
    # Always force missing_evidence=false on the projected event (builder already
    # does this); never trust a pre-fabricated event from outside.
    if event.get("missing_evidence") is not False:
        raise ControlPlaneRunLedgerV1Error("managed_process_lifecycle_missing_evidence_invariant")

    from polaris.cells.control_plane.run_ledger.public import service as run_ledger_service

    auth_token = _MANAGED_PROCESS_APPEND_AUTHORIZED.set(True)
    try:
        return run_ledger_service.append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=command.workspace,
                run_id=command.run_id,
                event=event,
            )
        )
    finally:
        _MANAGED_PROCESS_APPEND_AUTHORIZED.reset(auth_token)


__all__ = [
    "MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE",
    "MANAGED_PROCESS_LIFECYCLE_SCHEMA_V1",
    "ProjectManagedProcessLifecycleCommandV1",
    "build_managed_process_lifecycle_event",
    "derive_managed_process_evidence_presence",
    "looks_like_managed_process_tool_lifecycle_substitute",
    "managed_process_append_is_authorized",
    "project_managed_process_lifecycle",
]
