"""Repair diagnostics, advisory overlay, and receipt public contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    copy_valid_repair_advisory_metadata,
    copy_valid_repair_advisory_suggested_rules,
)
from polaris.cells.director.runtime.public.contracts._helpers import (
    _optional_int,
    _optional_non_negative_int,
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_mapping_from_any,
    _to_tuple_str_from_any,
)


@dataclass(frozen=True)
class RepairDiagnosticV1:
    """Structured repair diagnostic for Director Runtime."""

    source: str
    code: str
    message: str
    path: str | None = None
    severity: str = "error"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "code", _require_non_empty("code", self.code))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


def _repair_diagnostic_v1_to_dict(diagnostic: RepairDiagnosticV1) -> dict[str, Any]:
    return {
        "source": diagnostic.source,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "path": diagnostic.path,
        "severity": diagnostic.severity,
        "metadata": dict(diagnostic.metadata),
    }


@dataclass(frozen=True)
class RepairAdvisoryV1:
    """Optional future AGI advisory overlay.

    Advisory data is explicitly non-authoritative: it cannot carry repair
    plans, write decisions, policy overrides, or success verdicts.
    """

    advisor_source: str
    message: str
    confidence: float = 0.0
    suggested_rules: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advisor_source", _require_non_empty("advisor_source", self.advisor_source))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(
            self,
            "suggested_rules",
            tuple(copy_valid_repair_advisory_suggested_rules(self.suggested_rules)),
        )
        object.__setattr__(self, "metadata", copy_valid_repair_advisory_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable non-authoritative advisory payload."""

        return {
            "advisor_source": self.advisor_source,
            "message": self.message,
            "confidence": float(self.confidence),
            "authoritative": False,
            "suggested_rules": [dict(item) for item in self.suggested_rules],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RepairReceiptV1:
    """Repair receipt projection for deterministic Director repairs."""

    receipt_id: str
    plan_id: str
    source_tool: str
    status: str
    authoritative: bool
    files_changed: tuple[str, ...] = ()
    before_hashes: Mapping[str, str] = field(default_factory=dict)
    after_hashes: Mapping[str, str] = field(default_factory=dict)
    round_number: int | None = None
    evidence_status: str = "missing_evidence"
    errors_before: int | None = None
    errors_after: int | None = None
    net_error_reduction: int | None = None
    authority_hash: str = ""
    projection_hash: str = ""
    revalidation_evidence: Mapping[str, Any] = field(default_factory=dict)
    verifier_command: tuple[str, ...] = ()
    verifier_exit_code: int | None = None
    diagnostics_before: tuple[Mapping[str, Any], ...] = ()
    diagnostics_after: tuple[Mapping[str, Any], ...] = ()
    resolved_diagnostic_ids: tuple[str, ...] = ()
    residual_diagnostic_ids: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    rule_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _require_non_empty("receipt_id", self.receipt_id))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        source_tool = _require_non_empty("source_tool", self.source_tool)
        object.__setattr__(self, "source_tool", source_tool)
        object.__setattr__(self, "rule_id", str(self.rule_id or "").strip() or source_tool)
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "files_changed", tuple(str(item) for item in self.files_changed))
        object.__setattr__(self, "before_hashes", dict(self.before_hashes or {}))
        object.__setattr__(self, "after_hashes", dict(self.after_hashes or {}))
        object.__setattr__(self, "round_number", None if self.round_number is None else max(0, int(self.round_number)))
        revalidation_evidence = _to_dict_copy(self.revalidation_evidence)
        verifier_command = _to_tuple_str_from_any(self.verifier_command or revalidation_evidence.get("command"))
        verifier_exit_code = _optional_non_negative_int(
            self.verifier_exit_code if self.verifier_exit_code is not None else revalidation_evidence.get("exit_code")
        )
        diagnostics_before = _to_tuple_mapping_from_any(
            self.diagnostics_before or revalidation_evidence.get("diagnostics_before")
        )
        diagnostics_after = _to_tuple_mapping_from_any(
            self.diagnostics_after or revalidation_evidence.get("diagnostics_after")
        )
        resolved_diagnostic_ids = _to_tuple_str_from_any(
            self.resolved_diagnostic_ids or revalidation_evidence.get("resolved_diagnostic_ids")
        )
        residual_diagnostic_ids = _to_tuple_str_from_any(
            self.residual_diagnostic_ids or revalidation_evidence.get("residual_diagnostic_ids")
        )
        evidence_status = str(self.evidence_status or "missing_evidence").strip() or "missing_evidence"
        if evidence_status == "missing_evidence" and revalidation_evidence.get("evidence_status"):
            evidence_status = str(revalidation_evidence.get("evidence_status") or "missing_evidence").strip()
        errors_before = _optional_non_negative_int(
            self.errors_before if self.errors_before is not None else revalidation_evidence.get("errors_before")
        )
        errors_after = _optional_non_negative_int(
            self.errors_after if self.errors_after is not None else revalidation_evidence.get("errors_after")
        )
        net_error_reduction = _optional_int(
            self.net_error_reduction
            if self.net_error_reduction is not None
            else revalidation_evidence.get("net_error_reduction")
        )
        if net_error_reduction is None and errors_before is not None and errors_after is not None:
            net_error_reduction = errors_before - errors_after

        has_native_revalidation = bool(
            verifier_command
            or verifier_exit_code is not None
            or diagnostics_before
            or diagnostics_after
            or resolved_diagnostic_ids
            or residual_diagnostic_ids
            or errors_before is not None
            or errors_after is not None
        )
        if revalidation_evidence or has_native_revalidation:
            revalidation_evidence = dict(revalidation_evidence)
            revalidation_evidence.setdefault("command", list(verifier_command))
            revalidation_evidence.setdefault("exit_code", verifier_exit_code)
            revalidation_evidence.setdefault("round_number", self.round_number)
            revalidation_evidence.setdefault("evidence_status", evidence_status)
            revalidation_evidence.setdefault("errors_before", errors_before)
            revalidation_evidence.setdefault("errors_after", errors_after)
            revalidation_evidence.setdefault("net_error_reduction", net_error_reduction)
            revalidation_evidence.setdefault("resolved_diagnostic_ids", list(resolved_diagnostic_ids))
            revalidation_evidence.setdefault("residual_diagnostic_ids", list(residual_diagnostic_ids))
            revalidation_evidence.setdefault("diagnostics_before", [dict(item) for item in diagnostics_before])
            revalidation_evidence.setdefault("diagnostics_after", [dict(item) for item in diagnostics_after])
            verifier_command = _to_tuple_str_from_any(revalidation_evidence.get("command"))
            verifier_exit_code = _optional_non_negative_int(revalidation_evidence.get("exit_code"))
            diagnostics_before = _to_tuple_mapping_from_any(revalidation_evidence.get("diagnostics_before"))
            diagnostics_after = _to_tuple_mapping_from_any(revalidation_evidence.get("diagnostics_after"))
            resolved_diagnostic_ids = _to_tuple_str_from_any(revalidation_evidence.get("resolved_diagnostic_ids"))
            residual_diagnostic_ids = _to_tuple_str_from_any(revalidation_evidence.get("residual_diagnostic_ids"))
            errors_before = _optional_non_negative_int(revalidation_evidence.get("errors_before"))
            errors_after = _optional_non_negative_int(revalidation_evidence.get("errors_after"))
            net_error_reduction = _optional_int(revalidation_evidence.get("net_error_reduction"))
            evidence_status = str(revalidation_evidence.get("evidence_status") or evidence_status).strip()
        object.__setattr__(
            self,
            "evidence_status",
            evidence_status or "missing_evidence",
        )
        object.__setattr__(self, "errors_before", errors_before)
        object.__setattr__(self, "errors_after", errors_after)
        object.__setattr__(self, "net_error_reduction", net_error_reduction)
        object.__setattr__(self, "authority_hash", str(self.authority_hash or "").strip())
        object.__setattr__(self, "projection_hash", str(self.projection_hash or "").strip())
        object.__setattr__(self, "revalidation_evidence", revalidation_evidence)
        object.__setattr__(self, "verifier_command", verifier_command)
        object.__setattr__(self, "verifier_exit_code", verifier_exit_code)
        object.__setattr__(self, "diagnostics_before", diagnostics_before)
        object.__setattr__(self, "diagnostics_after", diagnostics_after)
        object.__setattr__(self, "resolved_diagnostic_ids", resolved_diagnostic_ids)
        object.__setattr__(self, "residual_diagnostic_ids", residual_diagnostic_ids)
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "status": self.status,
            "authoritative": self.authoritative,
            "files_changed": list(self.files_changed),
            "before_hashes": dict(self.before_hashes),
            "after_hashes": dict(self.after_hashes),
            "round_number": self.round_number,
            "evidence_status": self.evidence_status,
            "errors_before": self.errors_before,
            "errors_after": self.errors_after,
            "net_error_reduction": self.net_error_reduction,
            "authority_hash": self.authority_hash,
            "projection_hash": self.projection_hash,
            "revalidation_evidence": dict(self.revalidation_evidence),
            "verifier_command": list(self.verifier_command),
            "verifier_exit_code": self.verifier_exit_code,
            "diagnostics_before": [dict(item) for item in self.diagnostics_before],
            "diagnostics_after": [dict(item) for item in self.diagnostics_after],
            "resolved_diagnostic_ids": list(self.resolved_diagnostic_ids),
            "residual_diagnostic_ids": list(self.residual_diagnostic_ids),
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
            "metadata": dict(self.metadata),
        }
