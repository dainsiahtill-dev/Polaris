"""Typed contracts for the Director Repair Kernel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .advisory_policy import (
    copy_valid_repair_advisory_metadata,
    copy_valid_repair_advisory_suggested_rules,
)

RepairMode = str
RepairStatus = str


def sha256_text(value: str) -> str:
    """Return a stable UTF-8 SHA-256 hash for text content."""

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 24) -> str:
    """Build a deterministic short id from UTF-8 encoded parts."""

    raw = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _dict_copy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _tuple_str(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


@dataclass(frozen=True)
class RepairDiagnostic:
    """Structured version of a quality/verifier/compiler error."""

    source: str
    code: str
    message: str
    severity: str = "error"
    path: str | None = None
    line: int | None = None
    column: int | None = None
    span_start: int | None = None
    span_end: int | None = None
    diagnostic_id: str = ""
    raw: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source = str(self.source or "unknown").strip() or "unknown"
        code = str(self.code or "unknown").strip() or "unknown"
        message = str(self.message or "").strip()
        path = str(self.path).strip().replace("\\", "/") if self.path is not None else None
        raw = str(self.raw or message)
        diagnostic_id = str(self.diagnostic_id or "").strip() or stable_id(
            "diag",
            source,
            code,
            path or "",
            self.line or "",
            self.column or "",
            message,
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "path", path or None)
        object.__setattr__(self, "diagnostic_id", diagnostic_id)
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "metadata", _dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostic_id": self.diagnostic_id,
            "source": self.source,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "raw": self.raw,
            "metadata": _dict_copy(self.metadata),
        }


@dataclass(frozen=True)
class RepairAdvisorNote:
    """Non-authoritative future advisory input, e.g. optional AGI guidance."""

    source: str
    message: str
    confidence: float = 0.0
    authoritative: bool = False
    suggested_rules: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", str(self.source or "unknown").strip() or "unknown")
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(
            self,
            "suggested_rules",
            tuple(copy_valid_repair_advisory_suggested_rules(self.suggested_rules)),
        )
        object.__setattr__(self, "metadata", copy_valid_repair_advisory_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "message": self.message,
            "confidence": float(self.confidence),
            "authoritative": bool(self.authoritative),
            "suggested_rules": [dict(item) for item in self.suggested_rules],
            "metadata": _dict_copy(self.metadata),
        }


@dataclass(frozen=True)
class RepairRevalidationEvidence:
    """Post-repair verifier evidence tied back to repaired diagnostics."""

    command: tuple[str, ...] = ()
    exit_code: int | None = None
    diagnostics_before: tuple[RepairDiagnostic, ...] = ()
    diagnostics_after: tuple[RepairDiagnostic, ...] = ()
    resolved_diagnostic_ids: tuple[str, ...] = ()
    residual_diagnostic_ids: tuple[str, ...] = ()
    round_number: int | None = None
    raw_output_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        before = tuple(self.diagnostics_before or ())
        after = tuple(self.diagnostics_after or ())
        before_ids = {diagnostic.diagnostic_id for diagnostic in before}
        after_ids = {diagnostic.diagnostic_id for diagnostic in after}
        resolved = tuple(self.resolved_diagnostic_ids or tuple(sorted(before_ids - after_ids)))
        residual = tuple(self.residual_diagnostic_ids or tuple(sorted(after_ids & before_ids)))
        object.__setattr__(self, "command", _tuple_str(list(self.command)))
        object.__setattr__(self, "diagnostics_before", before)
        object.__setattr__(self, "diagnostics_after", after)
        object.__setattr__(self, "resolved_diagnostic_ids", _tuple_str(list(resolved)))
        object.__setattr__(self, "residual_diagnostic_ids", _tuple_str(list(residual)))
        object.__setattr__(self, "round_number", None if self.round_number is None else max(0, int(self.round_number)))
        object.__setattr__(self, "raw_output_ref", str(self.raw_output_ref or "").strip() or None)
        object.__setattr__(self, "metadata", _dict_copy(self.metadata))

    @property
    def errors_before(self) -> int:
        return len(self.diagnostics_before)

    @property
    def errors_after(self) -> int:
        return len(self.diagnostics_after)

    @property
    def net_error_reduction(self) -> int:
        return self.errors_before - self.errors_after

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "round_number": self.round_number,
            "errors_before": self.errors_before,
            "errors_after": self.errors_after,
            "net_error_reduction": self.net_error_reduction,
            "resolved_diagnostic_ids": list(self.resolved_diagnostic_ids),
            "residual_diagnostic_ids": list(self.residual_diagnostic_ids),
            "diagnostics_before": [diagnostic.to_dict() for diagnostic in self.diagnostics_before],
            "diagnostics_after": [diagnostic.to_dict() for diagnostic in self.diagnostics_after],
            "raw_output_ref": self.raw_output_ref,
            "metadata": _dict_copy(self.metadata),
        }


@dataclass(frozen=True)
class RepairOperation:
    """One planned mutation or observation."""

    kind: str
    path: str
    operation_id: str = ""
    replacement: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    expected: str | None = None
    content: str | None = None
    json_path: tuple[str, ...] = ()
    value: Any = None
    before_hash: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip()
        path = str(self.path or "").strip().replace("\\", "/")
        operation_id = str(self.operation_id or "").strip() or stable_id(
            "op",
            kind,
            path,
            self.span_start if self.span_start is not None else "",
            self.span_end if self.span_end is not None else "",
            self.json_path,
            self.replacement or self.content or self.value or "",
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "json_path", _tuple_str(self.json_path))
        object.__setattr__(self, "metadata", _dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "path": self.path,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "expected": self.expected,
            "replacement": self.replacement,
            "content": self.content,
            "json_path": list(self.json_path),
            "value": self.value,
            "before_hash": self.before_hash,
            "metadata": _dict_copy(self.metadata),
        }


@dataclass(frozen=True)
class RepairPlan:
    """A rule-produced repair plan before composition and policy checks."""

    rule_id: str
    source_tool: str
    operations: tuple[RepairOperation, ...]
    diagnostics: tuple[RepairDiagnostic, ...] = ()
    plan_id: str = ""
    mode: RepairMode = "commit"
    risk_level: str = "low"
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        rule_id = str(self.rule_id or "").strip()
        source_tool = str(self.source_tool or "").strip()
        plan_id = str(self.plan_id or "").strip() or stable_id(
            "plan",
            rule_id,
            source_tool,
            tuple(op.operation_id for op in self.operations),
            tuple(diag.diagnostic_id for diag in self.diagnostics),
        )
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "source_tool", source_tool)
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "operations", tuple(self.operations or ()))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "mode": self.mode,
            "risk_level": self.risk_level,
            "operations": [op.to_dict() for op in self.operations],
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
            "metadata": _dict_copy(self.metadata),
        }


@dataclass(frozen=True)
class CompositionIssue:
    """Fail-closed patch composition issue."""

    code: str
    message: str
    path: str | None = None
    operation_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "operation_ids": list(self.operation_ids),
        }


@dataclass(frozen=True)
class ComposedPatch:
    """Final per-file content after composing all operations for one file."""

    path: str
    content_before: str
    content_after: str
    operation_ids: tuple[str, ...]
    before_hash: str = ""
    after_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "before_hash", self.before_hash or sha256_text(self.content_before))
        object.__setattr__(self, "after_hash", self.after_hash or sha256_text(self.content_after))
        object.__setattr__(self, "operation_ids", tuple(self.operation_ids or ()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation_ids": list(self.operation_ids),
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "changed": self.before_hash != self.after_hash,
        }


@dataclass(frozen=True)
class CompositionResult:
    """Patch composition result."""

    ok: bool
    patches: tuple[ComposedPatch, ...] = ()
    issues: tuple[CompositionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "patches": [patch.to_dict() for patch in self.patches],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class RepairReceipt:
    """Auditable repair outcome."""

    plan_id: str
    rule_id: str
    source_tool: str
    status: RepairStatus
    mode: RepairMode
    authoritative: bool
    receipt_id: str = ""
    files_changed: tuple[str, ...] = ()
    operation_ids: tuple[str, ...] = ()
    diagnostics: tuple[RepairDiagnostic, ...] = ()
    before_hashes: Mapping[str, str] = field(default_factory=dict)
    after_hashes: Mapping[str, str] = field(default_factory=dict)
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        files_changed = _tuple_str(list(self.files_changed))
        operation_ids = _tuple_str(list(self.operation_ids))
        receipt_id = str(self.receipt_id or "").strip() or stable_id(
            "repair_receipt",
            self.plan_id,
            self.rule_id,
            self.source_tool,
            self.status,
            self.mode,
            files_changed,
            operation_ids,
        )
        object.__setattr__(self, "receipt_id", receipt_id)
        object.__setattr__(self, "files_changed", files_changed)
        object.__setattr__(self, "operation_ids", operation_ids)
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "before_hashes", dict(self.before_hashes or {}))
        object.__setattr__(self, "after_hashes", dict(self.after_hashes or {}))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "status": self.status,
            "mode": self.mode,
            "authoritative": self.authoritative,
            "files_changed": list(self.files_changed),
            "operation_ids": list(self.operation_ids),
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "before_hashes": dict(self.before_hashes),
            "after_hashes": dict(self.after_hashes),
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
            "metadata": _dict_copy(self.metadata),
        }
        payload["authority_hash"] = self.authority_hash()
        payload["projection_hash"] = self.projection_hash()
        return payload

    def authority_hash(self) -> str:
        """Hash authoritative receipt fields, excluding advisory overlays."""

        return sha256_text(_stable_json(self._authority_payload()))

    def projection_hash(self) -> str:
        """Hash the full projected receipt, including advisory overlays."""

        return sha256_text(_stable_json(self._projection_payload()))

    def _authority_payload(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "status": self.status,
            "mode": self.mode,
            "authoritative": self.authoritative,
            "files_changed": list(self.files_changed),
            "operation_ids": list(self.operation_ids),
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "before_hashes": dict(self.before_hashes),
            "after_hashes": dict(self.after_hashes),
            "metadata": _dict_copy(self.metadata),
        }

    def _projection_payload(self) -> dict[str, Any]:
        return {
            **self._authority_payload(),
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
        }


@dataclass(frozen=True)
class RepairExecutionResult:
    """Execution result for a composed plan."""

    ok: bool
    receipt: RepairReceipt
    rolled_back: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "receipt": self.receipt.to_dict(),
        }


def _stable_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
