"""Public contracts for the `director.runtime` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
    copy_valid_repair_advisory_metadata,
    copy_valid_repair_advisory_suggested_rules,
)


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _to_tuple_str(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


def _default_repairer_module_name(language: str) -> str:
    normalized_language = "".join(
        char if char.isalnum() or char == "_" else "_" for char in str(language or "unknown").lower()
    ).strip("_")
    return f"polaris.cells.director.runtime.internal.repair_kernel.{normalized_language or 'unknown'}_runtime"


_ADAPTER_RECEIPT_PROJECTION_SCHEMA_VERSION = "director.repair_adapter_receipt_projection.v1"
_DEFAULT_ADAPTER_RECEIPT_AUTHORITY = "non_authoritative_adapter_projection"
_DEFAULT_ADAPTER_RECEIPT_MIGRATION_BLOCKER = "adapter_projection_not_authoritative_receipt"
_CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION = _ADAPTER_RECEIPT_PROJECTION_SCHEMA_VERSION
_DEFAULT_CALLBACK_RECEIPT_AUTHORITY = _DEFAULT_ADAPTER_RECEIPT_AUTHORITY
_ALLOWED_CALLBACK_RECEIPT_AUTHORITIES = {
    _DEFAULT_CALLBACK_RECEIPT_AUTHORITY,
    "non_authoritative_callback_receipt_projection",
    "non_authoritative_callback_projection",
    "non_authoritative_adapter_projection",
}
_DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER = _DEFAULT_ADAPTER_RECEIPT_MIGRATION_BLOCKER


def _optional_non_empty_str(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_tuple_str_from_any(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        items = (value,)
    else:
        try:
            items = tuple(value)
        except TypeError:
            items = (value,)
    return tuple(str(item) for item in items if str(item or "").strip())


def _to_tuple_mapping_from_any(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        items = (value,)
    else:
        try:
            items = tuple(value)
        except TypeError:
            return ()
    return tuple(_to_dict_copy(item) for item in items if isinstance(item, Mapping))


def _strict_bool_claim(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"true", "1", "yes", "on"}


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


@dataclass(frozen=True)
class QueryDirectorRepairEnvironmentPrepCatalogV1:
    """Query shape for the runtime-owned environment prep catalog."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairEnvironmentPrepCatalogResultV1:
    """Read-only projection of deterministic environment prep command templates."""

    items: tuple[Mapping[str, Any], ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.environment_prep_catalog.v1"
    source: str = "director.runtime.repair_kernel.environment"
    access: str = "read_only"
    owner_cell: str = "director.runtime"
    runner_binding_owner: str = "roles.adapters"
    writes_allowed: bool = False
    authoritative_repair: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(_to_dict_copy(item) for item in self.items))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "authoritative_repair", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "runner_binding_owner": self.runner_binding_owner,
            "writes_allowed": False,
            "authoritative_repair": False,
            "agi_execution_authority": False,
            "summary": dict(self.summary),
            "items": [dict(item) for item in self.items],
        }


@dataclass(frozen=True)
class QueryDirectorRepairEnvironmentRefreshRequirementsV1:
    """Query shape for revalidation environment preparation requirements."""

    receipts: tuple[RepairReceiptV1, ...] = ()
    workspace: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "workspace", str(self.workspace or "").strip())


@dataclass(frozen=True)
class DirectorRepairEnvironmentRefreshRequirementV1:
    """Runtime-owned environment preparation requirement before revalidation."""

    ecosystem: str
    package_manager: str
    manifest: str
    command: tuple[str, ...]
    reason: str
    receipt_id: str = ""
    lockfile: str = ""
    manifest_after_hash: str = ""
    lockfile_after_hash: str = ""
    freshness_key: str = ""
    writes_allowed: bool = False
    authoritative_repair: bool = False
    schema_version: str = "director.environment_refresh_requirement.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "ecosystem", _require_non_empty("ecosystem", self.ecosystem))
        object.__setattr__(self, "package_manager", _require_non_empty("package_manager", self.package_manager))
        object.__setattr__(self, "manifest", _require_non_empty("manifest", self.manifest))
        object.__setattr__(self, "lockfile", str(self.lockfile or "").strip())
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "receipt_id", str(self.receipt_id or "").strip())
        object.__setattr__(self, "manifest_after_hash", str(self.manifest_after_hash or "").strip())
        object.__setattr__(self, "lockfile_after_hash", str(self.lockfile_after_hash or "").strip())
        object.__setattr__(self, "freshness_key", str(self.freshness_key or "").strip())
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "authoritative_repair", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ecosystem": self.ecosystem,
            "package_manager": self.package_manager,
            "manifest": self.manifest,
            "lockfile": self.lockfile,
            "command": list(self.command),
            "reason": self.reason,
            "receipt_id": self.receipt_id,
            "manifest_after_hash": self.manifest_after_hash,
            "lockfile_after_hash": self.lockfile_after_hash,
            "freshness_key": self.freshness_key,
            "writes_allowed": False,
            "authoritative_repair": False,
        }


@dataclass(frozen=True)
class DirectorRepairEnvironmentPrepPlanV1:
    """Runtime-owned environment preparation plan passed to adapter verifier."""

    plan_id: str
    ecosystem: str
    package_manager: str
    manifest: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 120
    lockfile: str = ""
    freshness_key: str = ""
    source_receipt_id: str = ""
    policy: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    requirement: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.environment_prep_plan.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "ecosystem", _require_non_empty("ecosystem", self.ecosystem))
        object.__setattr__(self, "package_manager", _require_non_empty("package_manager", self.package_manager))
        object.__setattr__(self, "manifest", _require_non_empty("manifest", self.manifest))
        object.__setattr__(self, "lockfile", str(self.lockfile or "").strip())
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "cwd", str(self.cwd or ".").strip() or ".")
        object.__setattr__(self, "timeout_seconds", max(1, int(self.timeout_seconds)))
        object.__setattr__(self, "freshness_key", str(self.freshness_key or "").strip())
        object.__setattr__(self, "source_receipt_id", str(self.source_receipt_id or "").strip())
        object.__setattr__(self, "policy", _to_dict_copy(self.policy))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "requirement", _to_dict_copy(self.requirement))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "ecosystem": self.ecosystem,
            "package_manager": self.package_manager,
            "manifest": self.manifest,
            "lockfile": self.lockfile,
            "command": list(self.command),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "freshness_key": self.freshness_key,
            "source_receipt_id": self.source_receipt_id,
            "policy": dict(self.policy),
            "metadata": dict(self.metadata),
            "requirement": dict(self.requirement),
        }


@dataclass(frozen=True)
class DirectorRepairEnvironmentPrepReceiptV1:
    """Adapter-provided evidence for one environment prep plan."""

    plan_id: str
    ecosystem: str
    package_manager: str
    command: tuple[str, ...]
    exit_code: int | None
    status: str
    duration_ms: int | None = None
    manifest: str = ""
    lockfile: str = ""
    manifest_hash_before: str = ""
    manifest_hash_after: str = ""
    lockfile_hash_before: str = ""
    lockfile_hash_after: str = ""
    stdout_ref: str = ""
    stderr_ref: str = ""
    freshness_key: str = ""
    skipped_reason: str = ""
    error_code: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.environment_prep_receipt.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "ecosystem", _require_non_empty("ecosystem", self.ecosystem))
        object.__setattr__(self, "package_manager", _require_non_empty("package_manager", self.package_manager))
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "duration_ms", None if self.duration_ms is None else max(0, int(self.duration_ms)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "ecosystem": self.ecosystem,
            "package_manager": self.package_manager,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "manifest": self.manifest,
            "lockfile": self.lockfile,
            "manifest_hash_before": self.manifest_hash_before,
            "manifest_hash_after": self.manifest_hash_after,
            "lockfile_hash_before": self.lockfile_hash_before,
            "lockfile_hash_after": self.lockfile_hash_after,
            "stdout_ref": self.stdout_ref,
            "stderr_ref": self.stderr_ref,
            "freshness_key": self.freshness_key,
            "skipped_reason": self.skipped_reason,
            "error_code": self.error_code,
            "authoritative_repair": False,
            "metadata": dict(self.metadata),
        }


def _to_environment_prep_receipt_tuple_from_any(value: Any) -> tuple[DirectorRepairEnvironmentPrepReceiptV1, ...]:
    if value is None:
        return ()
    if isinstance(value, DirectorRepairEnvironmentPrepReceiptV1 | Mapping):
        items = (value,)
    else:
        try:
            items = tuple(value)
        except TypeError:
            return ()

    receipts: list[DirectorRepairEnvironmentPrepReceiptV1] = []
    for item in items:
        if isinstance(item, DirectorRepairEnvironmentPrepReceiptV1):
            receipts.append(item)
        elif isinstance(item, Mapping):
            allowed_fields = DirectorRepairEnvironmentPrepReceiptV1.__dataclass_fields__.keys()
            receipts.append(
                DirectorRepairEnvironmentPrepReceiptV1(
                    **{key: value for key, value in dict(item).items() if key in allowed_fields}
                )
            )
    return tuple(receipts)


@dataclass(frozen=True)
class DirectorRepairEnvironmentRefreshRequirementsResultV1:
    """Runtime-owned read-only projection of environment prep requirements."""

    items: tuple[DirectorRepairEnvironmentRefreshRequirementV1, ...] = ()
    plans: tuple[DirectorRepairEnvironmentPrepPlanV1, ...] = ()
    schema_version: str = "director.environment_refresh_requirements.v1"
    source: str = "director.runtime.repair_kernel.environment"
    access: str = "read_only"
    owner_cell: str = "director.runtime"
    runner_binding_owner: str = "roles.adapters"
    writes_allowed: bool = False
    authoritative_repair: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "plans", tuple(self.plans or ()))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "authoritative_repair", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "runner_binding_owner": self.runner_binding_owner,
            "writes_allowed": False,
            "authoritative_repair": False,
            "agi_execution_authority": False,
            "items": [item.to_dict() for item in self.items],
            "plans": [plan.to_dict() for plan in self.plans],
            "summary": {
                "requirement_count": len(self.items),
                "plan_count": len(self.plans),
                "ecosystems": sorted({item.ecosystem for item in self.items}),
                "manifests": sorted({item.manifest for item in self.items}),
                "adapter_runner_binding_only": True,
                "llm_generated_commands_allowed": False,
            },
        }


@dataclass(frozen=True)
class PlanDirectorRepairCommandV1:
    """Command shape for generic Director Runtime repair planning."""

    source_tool: str
    base_files: Mapping[str, str] = field(default_factory=dict)
    artifact_quality_errors: tuple[str, ...] = ()
    diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    mode: str = "commit"
    deterministic_only: bool = True
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_errors", tuple(str(item) for item in self.artifact_quality_errors))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RunDirectorRepairCommandV1:
    """Command shape for generic Director Runtime repair execution."""

    task_id: str
    workspace: str
    source_tool: str
    base_files: Mapping[str, str] = field(default_factory=dict)
    artifact_quality_errors: tuple[str, ...] = ()
    diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    mode: str = "commit"
    deterministic_only: bool = True
    allowed_paths: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_errors", tuple(str(item) for item in self.artifact_quality_errors))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "allowed_paths", tuple(str(item) for item in self.allowed_paths))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RunDirectorRepairConvergenceCommandV1:
    """Command shape for public typed Director Runtime repair convergence."""

    task_id: str
    workspace: str
    source_tools: tuple[str, ...]
    artifact_quality_errors: tuple[str, ...]
    base_files: Mapping[str, str]
    allowed_paths: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    mode: str = "commit"
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        source_tools = _to_tuple_str(list(self.source_tools))
        if not source_tools:
            raise ValueError("source_tools must include at least one repair source tool")
        object.__setattr__(self, "source_tools", source_tools)
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "allowed_paths", tuple(str(item) for item in self.allowed_paths))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "mode", str(self.mode or "commit").strip() or "commit")
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairVerifierSnapshotInputV1:
    """Adapter-supplied verifier snapshot for a convergence round."""

    residual_artifact_quality_errors: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    raw_output_ref: str | None = None
    environment_prep_receipts: tuple[DirectorRepairEnvironmentPrepReceiptV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "residual_artifact_quality_errors",
            _to_tuple_str(list(self.residual_artifact_quality_errors)),
        )
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "raw_output_ref", str(self.raw_output_ref or "").strip() or None)
        object.__setattr__(
            self,
            "environment_prep_receipts",
            _to_environment_prep_receipt_tuple_from_any(self.environment_prep_receipts),
        )
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "residual_artifact_quality_errors": list(self.residual_artifact_quality_errors),
            "command": list(self.command),
            "exit_code": self.exit_code,
            "raw_output_ref": self.raw_output_ref,
            "environment_prep_receipt_count": len(self.environment_prep_receipts),
            "environment_prep_receipts": [receipt.to_dict() for receipt in self.environment_prep_receipts],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairConvergenceVerifierRequestV1:
    """Context passed to an adapter-supplied convergence verifier callback."""

    task_id: str
    workspace: str
    round_number: int
    source_tools: tuple[str, ...]
    receipts: tuple[RepairReceiptV1, ...] = ()
    environment_prep_plans: tuple[DirectorRepairEnvironmentPrepPlanV1, ...] = ()
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "round_number", max(0, int(self.round_number)))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "environment_prep_plans", tuple(self.environment_prep_plans or ()))
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "round_number": self.round_number,
            "source_tools": list(self.source_tools),
            "receipt_count": len(self.receipts),
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "environment_prep_plan_count": len(self.environment_prep_plans),
            "environment_prep_plans": [plan.to_dict() for plan in self.environment_prep_plans],
            "max_rounds": self.max_rounds,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairConvergenceRoundResultV1:
    """Public projection for one Director Runtime convergence round."""

    round_number: int
    status: str
    schedule: Mapping[str, Any] = field(default_factory=dict)
    diagnostics_before: tuple[RepairDiagnosticV1, ...] = ()
    diagnostics_after: tuple[RepairDiagnosticV1, ...] = ()
    receipts: tuple[RepairReceiptV1, ...] = ()
    revalidation_evidence: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "round_number", max(0, int(self.round_number)))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "schedule", _to_dict_copy(self.schedule))
        object.__setattr__(self, "diagnostics_before", tuple(self.diagnostics_before or ()))
        object.__setattr__(self, "diagnostics_after", tuple(self.diagnostics_after or ()))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "revalidation_evidence", _to_dict_copy(self.revalidation_evidence))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "status": self.status,
            "schedule": dict(self.schedule),
            "errors_before": len(self.diagnostics_before),
            "errors_after": len(self.diagnostics_after),
            "net_error_reduction": len(self.diagnostics_before) - len(self.diagnostics_after),
            "diagnostics_before": [diagnostic.__dict__ for diagnostic in self.diagnostics_before],
            "diagnostics_after": [diagnostic.__dict__ for diagnostic in self.diagnostics_after],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "revalidation_evidence": dict(self.revalidation_evidence),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairConvergenceResultV1:
    """Result shape for public typed Director Runtime repair convergence."""

    ok: bool
    converged: bool
    status: str
    final_diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    receipts: tuple[RepairReceiptV1, ...] = ()
    rounds: tuple[DirectorRepairConvergenceRoundResultV1, ...] = ()
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = "director.repair_convergence_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "adapter_supplied_verifier_callback_no_command_execution"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "converged", bool(self.converged))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "final_diagnostics", tuple(self.final_diagnostics or ()))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "rounds", tuple(self.rounds or ()))
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed DirectorRepairConvergenceResultV1 must include error_code or error_message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "converged": self.converged,
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "max_rounds": self.max_rounds,
            "round_count": len(self.rounds),
            "receipt_count": len(self.receipts),
            "final_error_count": len(self.final_diagnostics),
            "final_diagnostics": [diagnostic.__dict__ for diagnostic in self.final_diagnostics],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "rounds": [round_result.to_dict() for round_result in self.rounds],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairRevalidationInputV1:
    """Adapter-supplied post-check evidence for a Director repair run."""

    residual_artifact_quality_errors: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    raw_output_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "residual_artifact_quality_errors",
            _to_tuple_str(list(self.residual_artifact_quality_errors)),
        )
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "raw_output_ref", str(self.raw_output_ref or "").strip() or None)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "residual_artifact_quality_errors": list(self.residual_artifact_quality_errors),
            "command": list(self.command),
            "exit_code": self.exit_code,
            "raw_output_ref": self.raw_output_ref,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairRevalidationRequestV1:
    """Context passed to a local revalidator after runtime repair execution."""

    task_id: str
    workspace: str
    source_tool: str
    receipt_id: str
    plan_id: str
    files_changed: tuple[str, ...] = ()
    before_hashes: Mapping[str, str] = field(default_factory=dict)
    after_hashes: Mapping[str, str] = field(default_factory=dict)
    diagnostics_before: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "receipt_id", _require_non_empty("receipt_id", self.receipt_id))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "files_changed", tuple(str(item) for item in self.files_changed))
        object.__setattr__(self, "before_hashes", dict(self.before_hashes or {}))
        object.__setattr__(self, "after_hashes", dict(self.after_hashes or {}))
        object.__setattr__(self, "diagnostics_before", tuple(_to_dict_copy(item) for item in self.diagnostics_before))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "source_tool": self.source_tool,
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "files_changed": list(self.files_changed),
            "before_hashes": dict(self.before_hashes),
            "after_hashes": dict(self.after_hashes),
            "diagnostics_before": [dict(item) for item in self.diagnostics_before],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class QueryDirectorRepairStrategyCatalogV1:
    """Query shape for the Director deterministic repair strategy catalog."""

    include_items: bool = True
    max_items: int = 500

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))
        object.__setattr__(self, "max_items", max(0, min(int(self.max_items), 1000)))


@dataclass(frozen=True)
class DirectorRepairStrategyCatalogResultV1:
    """Read-only projection of hard-coded Director repair strategies."""

    schema_version: str
    source: str
    access: str
    agi_execution_authority: bool
    director_tool_execution_required: bool
    owner_cell: str = "director.runtime"
    execution_boundary: str = "director_authorized_tools_only"
    chain: str = "PM → Chief Engineer → Director"
    unknown_source_tool_policy: str = "fail_closed_high_risk"
    items: tuple[Mapping[str, Any], ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "chain", _require_non_empty("chain", self.chain))
        object.__setattr__(
            self,
            "unknown_source_tool_policy",
            _require_non_empty("unknown_source_tool_policy", self.unknown_source_tool_policy),
        )
        object.__setattr__(self, "items", tuple(dict(item or {}) for item in self.items))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable catalog payload."""

        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "agi_execution_authority": self.agi_execution_authority,
            "director_tool_execution_required": self.director_tool_execution_required,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "chain": self.chain,
            "unknown_source_tool_policy": self.unknown_source_tool_policy,
            "items": [dict(item) for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairCoverageV1:
    """Query shape for read-only deterministic repair diagnostic coverage."""

    artifact_quality_errors: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))


@dataclass(frozen=True)
class DirectorRepairDiagnosticCoverageV1:
    """Public coverage projection for one repair diagnostic."""

    diagnostic: Mapping[str, Any]
    known_rule_matched: bool
    executable_runtime_plan_matched: bool = False
    metadata_only_match: bool = False
    matched_rule_ids: tuple[str, ...] = ()
    matched_source_tools: tuple[str, ...] = ()
    runtime_plan_rule_ids: tuple[str, ...] = ()
    archetypes: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    language: str = "unknown"
    diagnostic_archetype: str = "unknown"
    diagnostic_phase: str = "unknown"
    diagnostic_language: str = "unknown"
    diagnostic_code: str = "unknown"
    archetype_suggestion: str = "unknown"
    phase_suggestion: str = "unknown"
    suggested_rule_family: str = "unknown"
    reserved_slot_available: bool = False
    slot_status: str = "reserved_slot_missing"
    reserved_language_slot_matched: bool = False
    reserved_language_slot: Mapping[str, Any] = field(default_factory=dict)
    reserved_repairer_module: str = ""
    reserved_slot_registration_policy: str = ""
    recommended_next_owner: str = ""
    recommended_route: str = "llm_repair"
    handoff_recommendation: str = ""
    llm_advisory_recommended: bool = False
    agi_advisory_recommended: bool = False
    authoritative_rule_registration_allowed: bool = False
    recommended_registration_path: str = ""
    coverage_status: str = "coverage_gap"

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _to_dict_copy(self.diagnostic))
        object.__setattr__(self, "known_rule_matched", bool(self.known_rule_matched))
        object.__setattr__(self, "executable_runtime_plan_matched", bool(self.executable_runtime_plan_matched))
        object.__setattr__(self, "metadata_only_match", bool(self.metadata_only_match))
        object.__setattr__(self, "matched_rule_ids", _to_tuple_str(list(self.matched_rule_ids)))
        object.__setattr__(self, "matched_source_tools", _to_tuple_str(list(self.matched_source_tools)))
        object.__setattr__(self, "runtime_plan_rule_ids", _to_tuple_str(list(self.runtime_plan_rule_ids)))
        object.__setattr__(self, "archetypes", _to_tuple_str(list(self.archetypes)))
        object.__setattr__(self, "phases", _to_tuple_str(list(self.phases)))
        object.__setattr__(self, "languages", _to_tuple_str(list(self.languages)))
        object.__setattr__(self, "diagnostic_archetype", str(self.diagnostic_archetype or "unknown").strip())
        object.__setattr__(self, "diagnostic_phase", str(self.diagnostic_phase or "unknown").strip())
        object.__setattr__(self, "diagnostic_language", str(self.diagnostic_language or "unknown").strip())
        language = str(self.language or "").strip()
        if not language or language == "unknown":
            language = self.diagnostic_language
        object.__setattr__(self, "language", language)
        object.__setattr__(
            self,
            "diagnostic_code",
            str(self.diagnostic_code or self.diagnostic.get("code") or "unknown").strip(),
        )
        object.__setattr__(
            self,
            "archetype_suggestion",
            str(self.archetype_suggestion or self.diagnostic_archetype or "unknown").strip(),
        )
        object.__setattr__(
            self,
            "phase_suggestion",
            str(self.phase_suggestion or self.diagnostic_phase or "unknown").strip(),
        )
        object.__setattr__(self, "suggested_rule_family", str(self.suggested_rule_family or "unknown").strip())
        object.__setattr__(self, "reserved_slot_available", bool(self.reserved_slot_available))
        object.__setattr__(self, "slot_status", str(self.slot_status or "reserved_slot_missing").strip())
        object.__setattr__(self, "reserved_language_slot_matched", bool(self.reserved_language_slot_matched))
        object.__setattr__(self, "reserved_language_slot", _to_dict_copy(self.reserved_language_slot))
        object.__setattr__(self, "reserved_repairer_module", str(self.reserved_repairer_module or "").strip())
        object.__setattr__(
            self,
            "reserved_slot_registration_policy",
            str(self.reserved_slot_registration_policy or "").strip(),
        )
        object.__setattr__(self, "recommended_next_owner", str(self.recommended_next_owner or "").strip())
        object.__setattr__(self, "recommended_route", str(self.recommended_route or "llm_repair").strip())
        object.__setattr__(self, "handoff_recommendation", str(self.handoff_recommendation or "").strip())
        object.__setattr__(self, "llm_advisory_recommended", bool(self.llm_advisory_recommended))
        object.__setattr__(self, "agi_advisory_recommended", bool(self.agi_advisory_recommended))
        object.__setattr__(self, "authoritative_rule_registration_allowed", False)
        object.__setattr__(
            self,
            "recommended_registration_path",
            str(self.recommended_registration_path or "").strip(),
        )
        object.__setattr__(self, "coverage_status", str(self.coverage_status or "coverage_gap").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostic": dict(self.diagnostic),
            "known_rule_matched": self.known_rule_matched,
            "executable_runtime_plan_matched": self.executable_runtime_plan_matched,
            "metadata_only_match": self.metadata_only_match,
            "matched_rule_ids": list(self.matched_rule_ids),
            "matched_source_tools": list(self.matched_source_tools),
            "runtime_plan_rule_ids": list(self.runtime_plan_rule_ids),
            "archetypes": list(self.archetypes),
            "phases": list(self.phases),
            "languages": list(self.languages),
            "language": self.language,
            "diagnostic_archetype": self.diagnostic_archetype,
            "diagnostic_phase": self.diagnostic_phase,
            "diagnostic_language": self.diagnostic_language,
            "diagnostic_code": self.diagnostic_code,
            "archetype_suggestion": self.archetype_suggestion,
            "phase_suggestion": self.phase_suggestion,
            "suggested_rule_family": self.suggested_rule_family,
            "reserved_slot_available": self.reserved_slot_available,
            "slot_status": self.slot_status,
            "reserved_language_slot_matched": self.reserved_language_slot_matched,
            "reserved_language_slot": dict(self.reserved_language_slot),
            "reserved_repairer_module": self.reserved_repairer_module,
            "reserved_slot_registration_policy": self.reserved_slot_registration_policy,
            "recommended_next_owner": self.recommended_next_owner,
            "recommended_route": self.recommended_route,
            "handoff_recommendation": self.handoff_recommendation,
            "llm_advisory_recommended": self.llm_advisory_recommended,
            "agi_advisory_recommended": self.agi_advisory_recommended,
            "authoritative_rule_registration_allowed": False,
            "recommended_registration_path": self.recommended_registration_path,
            "coverage_status": self.coverage_status,
        }


@dataclass(frozen=True)
class DirectorRepairCoverageReportV1:
    """Public read-only coverage report for repair diagnostics."""

    schema_version: str
    source: str
    access: str
    total_diagnostics: int
    covered_diagnostic_count: int
    uncovered_diagnostic_count: int
    executable_runtime_plan_diagnostic_count: int = 0
    metadata_only_diagnostic_count: int = 0
    items: tuple[DirectorRepairDiagnosticCoverageV1, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_coverage_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "total_diagnostics", max(0, int(self.total_diagnostics)))
        object.__setattr__(self, "covered_diagnostic_count", max(0, int(self.covered_diagnostic_count)))
        object.__setattr__(self, "uncovered_diagnostic_count", max(0, int(self.uncovered_diagnostic_count)))
        object.__setattr__(
            self,
            "executable_runtime_plan_diagnostic_count",
            max(0, int(self.executable_runtime_plan_diagnostic_count)),
        )
        object.__setattr__(
            self,
            "metadata_only_diagnostic_count",
            max(0, int(self.metadata_only_diagnostic_count)),
        )
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "director_tool_execution_required", False)

    def to_dict(self) -> dict[str, Any]:
        coverage_gaps = [_public_coverage_gap_payload(item) for item in self.items if not item.known_rule_matched]
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "director_tool_execution_required": False,
            "total_diagnostics": self.total_diagnostics,
            "covered_diagnostic_count": self.covered_diagnostic_count,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "coverage_gap_count": len(coverage_gaps),
            "rule_discovery_required": bool(coverage_gaps),
            "coverage_gap_languages": sorted(
                {str(gap.get("diagnostic_language") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_archetypes": sorted(
                {str(gap.get("diagnostic_archetype") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_diagnostic_codes": sorted(
                {str(gap.get("diagnostic_code") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_handoff_recommendations": sorted(
                {str(gap.get("handoff_recommendation") or "coverage_triage_required") for gap in coverage_gaps}
            ),
            "coverage_gap_recommended_routes": sorted(
                {str(gap.get("recommended_route") or "llm_repair") for gap in coverage_gaps}
            ),
            "coverage_gap_slot_statuses": sorted(
                {str(gap.get("slot_status") or "reserved_slot_missing") for gap in coverage_gaps}
            ),
            "executable_runtime_plan_diagnostic_count": self.executable_runtime_plan_diagnostic_count,
            "metadata_only_diagnostic_count": self.metadata_only_diagnostic_count,
            "items": [item.to_dict() for item in self.items],
            "uncovered_diagnostics": [dict(item.diagnostic) for item in self.items if not item.known_rule_matched],
            "coverage_gaps": coverage_gaps,
        }


def _public_coverage_gap_payload(item: DirectorRepairDiagnosticCoverageV1) -> dict[str, Any]:
    return {
        "diagnostic": dict(item.diagnostic),
        "diagnostic_id": str(item.diagnostic.get("diagnostic_id") or ""),
        "known_rule_matched": False,
        "executable_runtime_plan_matched": False,
        "metadata_only_match": False,
        "language": item.language,
        "diagnostic_language": item.diagnostic_language,
        "diagnostic_code": item.diagnostic_code,
        "diagnostic_phase": item.diagnostic_phase,
        "diagnostic_archetype": item.diagnostic_archetype,
        "phase_suggestion": item.phase_suggestion,
        "archetype_suggestion": item.archetype_suggestion,
        "suggested_rule_family": item.suggested_rule_family,
        "reserved_slot_available": item.reserved_slot_available,
        "slot_status": item.slot_status,
        "reserved_language_slot_matched": item.reserved_language_slot_matched,
        "reserved_language_slot": dict(item.reserved_language_slot),
        "reserved_repairer_module": item.reserved_repairer_module,
        "reserved_slot_registration_policy": item.reserved_slot_registration_policy,
        "recommended_next_owner": item.recommended_next_owner,
        "recommended_route": item.recommended_route,
        "handoff_recommendation": item.handoff_recommendation,
        "llm_advisory_recommended": item.llm_advisory_recommended,
        "agi_advisory_recommended": item.agi_advisory_recommended,
        "authoritative_rule_registration_allowed": False,
        "recommended_registration_path": item.recommended_registration_path,
        "missing_capability": "deterministic_repair_rule",
        "audit_reason": "known_rule_matched=false",
        "coverage_status": "coverage_gap",
    }


@dataclass(frozen=True)
class AttachDirectorRepairRevalidationEvidenceV1:
    """Command shape for projecting post-check evidence onto repair receipts."""

    summary: Mapping[str, Any]
    residual_artifact_quality_errors: tuple[str, ...] = ()
    command: tuple[str, ...] = ("materialization_quality_revalidation",)
    exit_code: int | None = None
    round_number: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(
            self,
            "residual_artifact_quality_errors",
            _to_tuple_str(list(self.residual_artifact_quality_errors)),
        )
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "round_number", None if self.round_number is None else max(0, int(self.round_number)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairRevalidationProjectionResultV1:
    """Result for repair receipt revalidation projection."""

    schema_version: str
    source: str
    access: str
    summary: Mapping[str, Any]
    owner_cell: str = "director.runtime"
    execution_boundary: str = "receipt_revalidation_projection_no_writes_no_registration"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class ProjectDirectorRepairKernelSummaryV1:
    """Command shape for projecting repair tool results into kernel receipts."""

    stage: str
    tool_results: tuple[Mapping[str, Any], ...] = ()
    artifact_quality_errors: tuple[str, ...] = ()
    mode: str = "commit"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _require_non_empty("stage", self.stage))
        object.__setattr__(self, "tool_results", tuple(_to_dict_copy(item) for item in self.tool_results))
        object.__setattr__(
            self,
            "artifact_quality_errors",
            _to_tuple_str(list(self.artifact_quality_errors)),
        )
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "tool_results": [dict(item) for item in self.tool_results],
            "artifact_quality_errors": list(self.artifact_quality_errors),
            "mode": self.mode,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairKernelSummaryProjectionResultV1:
    """Read-only result for repair receipt summary projection."""

    schema_version: str
    source: str
    access: str
    summary: Mapping[str, Any]
    owner_cell: str = "director.runtime"
    execution_boundary: str = "repair_kernel_summary_projection_no_writes_no_registration"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairLanguageSlotsV1:
    """Query shape for reserved deterministic repair language slots."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairLanguageSlotV1:
    """Public projection of one future language repair extension slot."""

    language: str
    aliases: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = ()
    file_names: tuple[str, ...] = ()
    diagnostic_sources: tuple[str, ...] = ()
    preferred_archetypes: tuple[str, ...] = ()
    repairer_module: str = ""
    implementation_status: str = "reserved_only"
    registration_policy: str = "bench_verified_rule_required"
    authoritative_source_tools: tuple[str, ...] = ()
    executable_runtime_source_tools: tuple[str, ...] = ()
    notes: str = ""
    slot_owner_cell: str = "director.runtime"
    bench_evidence_required: bool = True
    rule_authoring_status: str = "reserved_only"
    next_action: str = "add_bench_verified_rule_metadata_then_runtime_binding"

    def __post_init__(self) -> None:
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "aliases", _to_tuple_str(list(self.aliases)))
        object.__setattr__(self, "file_extensions", _to_tuple_str(list(self.file_extensions)))
        object.__setattr__(self, "file_names", _to_tuple_str(list(self.file_names)))
        object.__setattr__(self, "diagnostic_sources", _to_tuple_str(list(self.diagnostic_sources)))
        object.__setattr__(self, "preferred_archetypes", _to_tuple_str(list(self.preferred_archetypes)))
        object.__setattr__(
            self,
            "repairer_module",
            _require_non_empty(
                "repairer_module",
                self.repairer_module or _default_repairer_module_name(self.language),
            ),
        )
        object.__setattr__(
            self, "implementation_status", _require_non_empty("implementation_status", self.implementation_status)
        )
        object.__setattr__(
            self,
            "registration_policy",
            _require_non_empty("registration_policy", self.registration_policy),
        )
        object.__setattr__(self, "authoritative_source_tools", _to_tuple_str(list(self.authoritative_source_tools)))
        object.__setattr__(
            self,
            "executable_runtime_source_tools",
            _to_tuple_str(list(self.executable_runtime_source_tools)),
        )
        object.__setattr__(self, "notes", str(self.notes or "").strip())
        object.__setattr__(self, "slot_owner_cell", _require_non_empty("slot_owner_cell", self.slot_owner_cell))
        object.__setattr__(self, "bench_evidence_required", bool(self.bench_evidence_required))
        object.__setattr__(
            self,
            "rule_authoring_status",
            _require_non_empty("rule_authoring_status", self.rule_authoring_status),
        )
        object.__setattr__(self, "next_action", _require_non_empty("next_action", self.next_action))

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "aliases": list(self.aliases),
            "file_extensions": list(self.file_extensions),
            "file_names": list(self.file_names),
            "diagnostic_sources": list(self.diagnostic_sources),
            "preferred_archetypes": list(self.preferred_archetypes),
            "repairer_module": self.repairer_module,
            "implementation_status": self.implementation_status,
            "registration_policy": self.registration_policy,
            "authoritative_source_tools": list(self.authoritative_source_tools),
            "executable_runtime_source_tools": list(self.executable_runtime_source_tools),
            "notes": self.notes,
            "slot_owner_cell": self.slot_owner_cell,
            "bench_evidence_required": self.bench_evidence_required,
            "rule_authoring_status": self.rule_authoring_status,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class DirectorRepairLanguageSlotsResultV1:
    """Read-only catalog of reserved language repair extension slots."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairLanguageSlotV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_language_slots_no_rule_registration"
    authoritative_rule_registration: bool = False
    agi_execution_authority: bool = False
    writes_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "authoritative_rule_registration", False)
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "authoritative_rule_registration": False,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "items": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairPostExecutionScheduleV1:
    """Query shape for the runtime-owned post-execution repair schedule catalog."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairPostExecutionStepV1:
    """Public projection of one post-execution repair scheduling step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    source_tool_kind: str = "executable_runtime"
    executable_runtime_source_tool: bool = True
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _require_non_empty("step_id", self.step_id))
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        source_tool_kind = _require_non_empty("source_tool_kind", self.source_tool_kind)
        object.__setattr__(self, "source_tool_kind", source_tool_kind)
        object.__setattr__(self, "executable_runtime_source_tool", source_tool_kind == "executable_runtime")
        object.__setattr__(self, "depends_on", _to_tuple_str(list(self.depends_on)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "language": self.language,
            "phase": self.phase,
            "priority": self.priority,
            "source_tool": self.source_tool,
            "source_tool_kind": self.source_tool_kind,
            "executable_runtime_source_tool": self.executable_runtime_source_tool,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class DirectorRepairPostExecutionScheduleResultV1:
    """Read-only runtime-owned post-execution repair schedule catalog."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairPostExecutionStepV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_post_execution_schedule_no_runner_binding"
    runner_binding_owner: str = "roles.adapters"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "runner_binding_owner": self.runner_binding_owner,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "items": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class DirectorRepairCallbackReceiptProjectionV1:
    """Non-authoritative callback receipt projection for migration schedules."""

    schema_version: str = _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION
    projection_id: str | None = None
    receipt_authority: str = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
    schedule_kind: str | None = None
    step_id: str | None = None
    source_tool: str | None = None
    scheduled_source_tool: str | None = None
    scheduled_source_tool_kind: str | None = None
    scheduled_source_tool_executable_runtime: bool = False
    callback_source_tool: str | None = None
    adapter_source_tool: str | None = None
    round_number: int | None = None
    tool_name: str | None = None
    touched_path: str | None = None
    touched_paths: tuple[str, ...] = ()
    convergence_status: str | None = None
    convergence_stopped_reason: str | None = None
    scheduler_rounds_run: int | None = None
    max_rounds: int | None = None
    projection_only: bool = True
    typed_receipt_path_available: bool = False
    authoritative: bool = False
    migration_blocker: str = _DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER
    revalidation_evidence_present: bool = False
    revalidation_command: tuple[str, ...] = ()
    revalidation_exit_code: int | None = None
    revalidation_residual_count: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "schema_version",
            _require_non_empty("schema_version", self.schema_version or _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION),
        )
        object.__setattr__(self, "projection_id", _optional_non_empty_str(self.projection_id))
        receipt_authority = _optional_non_empty_str(self.receipt_authority)
        if receipt_authority not in _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES:
            receipt_authority = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
        object.__setattr__(self, "receipt_authority", receipt_authority)
        object.__setattr__(self, "schedule_kind", _optional_non_empty_str(self.schedule_kind))
        object.__setattr__(self, "step_id", _optional_non_empty_str(self.step_id))
        object.__setattr__(self, "source_tool", _optional_non_empty_str(self.source_tool))
        object.__setattr__(self, "scheduled_source_tool", _optional_non_empty_str(self.scheduled_source_tool))
        object.__setattr__(self, "scheduled_source_tool_kind", _optional_non_empty_str(self.scheduled_source_tool_kind))
        object.__setattr__(
            self,
            "scheduled_source_tool_executable_runtime",
            bool(self.scheduled_source_tool_executable_runtime),
        )
        object.__setattr__(self, "callback_source_tool", _optional_non_empty_str(self.callback_source_tool))
        adapter_source_tool = _optional_non_empty_str(self.adapter_source_tool) or _optional_non_empty_str(
            self.callback_source_tool
        )
        object.__setattr__(self, "adapter_source_tool", adapter_source_tool)
        object.__setattr__(self, "round_number", _optional_non_negative_int(self.round_number))
        object.__setattr__(self, "tool_name", _optional_non_empty_str(self.tool_name))
        object.__setattr__(self, "touched_path", _optional_non_empty_str(self.touched_path))
        touched_paths = _to_tuple_str_from_any(self.touched_paths)
        if self.touched_path and self.touched_path not in touched_paths:
            touched_paths = (self.touched_path, *touched_paths)
        object.__setattr__(self, "touched_paths", touched_paths)
        object.__setattr__(self, "convergence_status", _optional_non_empty_str(self.convergence_status))
        object.__setattr__(
            self,
            "convergence_stopped_reason",
            _optional_non_empty_str(self.convergence_stopped_reason),
        )
        object.__setattr__(self, "scheduler_rounds_run", _optional_non_negative_int(self.scheduler_rounds_run))
        object.__setattr__(self, "max_rounds", _optional_non_negative_int(self.max_rounds))
        claimed_typed_receipt_path_available = _strict_bool_claim(self.typed_receipt_path_available)
        object.__setattr__(self, "projection_only", True)
        object.__setattr__(self, "typed_receipt_path_available", False)
        object.__setattr__(self, "authoritative", False)
        object.__setattr__(
            self,
            "migration_blocker",
            _optional_non_empty_str(self.migration_blocker) or _DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER,
        )
        object.__setattr__(self, "revalidation_evidence_present", bool(self.revalidation_evidence_present))
        object.__setattr__(self, "revalidation_command", _to_tuple_str_from_any(self.revalidation_command))
        object.__setattr__(self, "revalidation_exit_code", _optional_non_negative_int(self.revalidation_exit_code))
        object.__setattr__(
            self,
            "revalidation_residual_count",
            _optional_non_negative_int(self.revalidation_residual_count),
        )
        metadata = _to_dict_copy(self.metadata)
        if claimed_typed_receipt_path_available:
            metadata.setdefault("claimed_typed_receipt_path_available", True)
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "receipt_authority": self.receipt_authority,
            "schedule_kind": self.schedule_kind,
            "step_id": self.step_id,
            "source_tool": self.source_tool,
            "scheduled_source_tool": self.scheduled_source_tool,
            "scheduled_source_tool_kind": self.scheduled_source_tool_kind,
            "scheduled_source_tool_executable_runtime": self.scheduled_source_tool_executable_runtime,
            "callback_source_tool": self.callback_source_tool,
            "adapter_source_tool": self.adapter_source_tool,
            "round_number": self.round_number,
            "tool_name": self.tool_name,
            "touched_path": self.touched_path,
            "touched_paths": list(self.touched_paths),
            "convergence_status": self.convergence_status,
            "convergence_stopped_reason": self.convergence_stopped_reason,
            "scheduler_rounds_run": self.scheduler_rounds_run,
            "max_rounds": self.max_rounds,
            "projection_only": True,
            "typed_receipt_path_available": False,
            "authoritative": False,
            "migration_blocker": self.migration_blocker,
            "revalidation_evidence_present": self.revalidation_evidence_present,
            "revalidation_command": list(self.revalidation_command),
            "revalidation_exit_code": self.revalidation_exit_code,
            "revalidation_residual_count": self.revalidation_residual_count,
            "metadata": dict(self.metadata),
        }


def _callback_receipt_projection_v1(
    value: DirectorRepairCallbackReceiptProjectionV1 | Mapping[str, Any],
) -> DirectorRepairCallbackReceiptProjectionV1:
    if isinstance(value, DirectorRepairCallbackReceiptProjectionV1):
        return value
    payload = dict(value or {})
    known_fields = DirectorRepairCallbackReceiptProjectionV1.__dataclass_fields__
    constructor_payload = {key: payload[key] for key in known_fields if key in payload}
    extra_fields = {key: payload[key] for key in payload if key not in known_fields}
    metadata = dict(constructor_payload.get("metadata") or {})
    if extra_fields:
        metadata.setdefault("extra_projection_fields", extra_fields)
    constructor_payload["metadata"] = metadata
    return DirectorRepairCallbackReceiptProjectionV1(**constructor_payload)


@dataclass(frozen=True)
class DirectorRepairPostExecutionScheduleRunResultV1:
    """Projection-only result from running post-execution callback schedule."""

    schema_version: str
    source: str
    ordered_steps: tuple[DirectorRepairPostExecutionStepV1, ...] = ()
    tool_results: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    receipt_projections: tuple[DirectorRepairCallbackReceiptProjectionV1, ...] = field(default_factory=tuple)
    summary: Mapping[str, Any] = field(default_factory=dict)
    max_rounds: int = 1
    rounds_run: int = 0
    convergence_status: str = "not_run"
    stopped_reason: str = "not_run"
    owner_cell: str = "director.runtime"
    runner_binding_owner: str = "roles.adapters"
    adapter_callback_bridge: bool = False
    adapter_projection_bridge: bool = True
    typed_receipt_path_available: bool = False
    authoritative_receipts_allowed: bool = False
    projection_only: bool = True
    receipt_authority: str = _DEFAULT_ADAPTER_RECEIPT_AUTHORITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item) for item in (self.tool_results or ())))
        object.__setattr__(
            self,
            "receipt_projections",
            tuple(_callback_receipt_projection_v1(item) for item in (self.receipt_projections or ())),
        )
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "max_rounds", max(0, int(self.max_rounds)))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(
            self,
            "convergence_status",
            _require_non_empty("convergence_status", self.convergence_status),
        )
        object.__setattr__(self, "stopped_reason", _require_non_empty("stopped_reason", self.stopped_reason))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "adapter_callback_bridge", False)
        object.__setattr__(self, "adapter_projection_bridge", True)
        object.__setattr__(self, "typed_receipt_path_available", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(self, "projection_only", True)
        receipt_authority = _optional_non_empty_str(self.receipt_authority)
        if receipt_authority not in _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES:
            receipt_authority = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
        object.__setattr__(self, "receipt_authority", receipt_authority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "owner_cell": self.owner_cell,
            "runner_binding_owner": self.runner_binding_owner,
            "adapter_callback_bridge": False,
            "adapter_projection_bridge": True,
            "ordered_steps": [item.to_dict() for item in self.ordered_steps],
            "tool_results": [dict(item) for item in self.tool_results],
            "receipt_projections": [item.to_dict() for item in self.receipt_projections],
            "summary": dict(self.summary),
            "max_rounds": self.max_rounds,
            "rounds_run": self.rounds_run,
            "convergence_status": self.convergence_status,
            "stopped_reason": self.stopped_reason,
            "typed_receipt_path_available": False,
            "authoritative_receipts_allowed": False,
            "projection_only": True,
            "receipt_authority": self.receipt_authority,
        }


@dataclass(frozen=True)
class QueryDirectorRepairMaterializationQualityScheduleV1:
    """Query shape for the runtime-owned materialization-quality repair schedule catalog."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairMaterializationQualityStepV1:
    """Public projection of one materialization-quality repair scheduling step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    source_tool_kind: str = "callback_schedule_label"
    executable_runtime_source_tool: bool = False
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _require_non_empty("step_id", self.step_id))
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        source_tool_kind = _require_non_empty("source_tool_kind", self.source_tool_kind)
        object.__setattr__(self, "source_tool_kind", source_tool_kind)
        object.__setattr__(self, "executable_runtime_source_tool", source_tool_kind == "executable_runtime")
        object.__setattr__(self, "depends_on", _to_tuple_str(list(self.depends_on)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "language": self.language,
            "phase": self.phase,
            "priority": self.priority,
            "source_tool": self.source_tool,
            "source_tool_kind": self.source_tool_kind,
            "executable_runtime_source_tool": self.executable_runtime_source_tool,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class DirectorRepairMaterializationQualityScheduleResultV1:
    """Read-only runtime-owned materialization-quality repair schedule catalog."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairMaterializationQualityStepV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_materialization_quality_schedule_no_runner_binding"
    runner_binding_owner: str = "roles.adapters"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "runner_binding_owner": self.runner_binding_owner,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "items": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class DirectorRepairMaterializationQualityScheduleRunResultV1:
    """Projection-only result from running materialization-quality callback schedule."""

    schema_version: str
    source: str
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...] = ()
    tool_results: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    receipt_projections: tuple[DirectorRepairCallbackReceiptProjectionV1, ...] = field(default_factory=tuple)
    summary: Mapping[str, Any] = field(default_factory=dict)
    max_rounds: int = 1
    rounds_run: int = 0
    convergence_status: str = "not_run"
    stopped_reason: str = "not_run"
    owner_cell: str = "director.runtime"
    runner_binding_owner: str = "roles.adapters"
    adapter_callback_bridge: bool = False
    adapter_projection_bridge: bool = True
    typed_receipt_path_available: bool = False
    authoritative_receipts_allowed: bool = False
    projection_only: bool = True
    receipt_authority: str = _DEFAULT_ADAPTER_RECEIPT_AUTHORITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item) for item in (self.tool_results or ())))
        object.__setattr__(
            self,
            "receipt_projections",
            tuple(_callback_receipt_projection_v1(item) for item in (self.receipt_projections or ())),
        )
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "max_rounds", max(0, int(self.max_rounds)))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(
            self,
            "convergence_status",
            _require_non_empty("convergence_status", self.convergence_status),
        )
        object.__setattr__(self, "stopped_reason", _require_non_empty("stopped_reason", self.stopped_reason))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "adapter_callback_bridge", False)
        object.__setattr__(self, "adapter_projection_bridge", True)
        object.__setattr__(self, "typed_receipt_path_available", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(self, "projection_only", True)
        receipt_authority = _optional_non_empty_str(self.receipt_authority)
        if receipt_authority not in _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES:
            receipt_authority = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
        object.__setattr__(self, "receipt_authority", receipt_authority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "owner_cell": self.owner_cell,
            "runner_binding_owner": self.runner_binding_owner,
            "adapter_callback_bridge": False,
            "adapter_projection_bridge": True,
            "ordered_steps": [item.to_dict() for item in self.ordered_steps],
            "tool_results": [dict(item) for item in self.tool_results],
            "receipt_projections": [item.to_dict() for item in self.receipt_projections],
            "summary": dict(self.summary),
            "max_rounds": self.max_rounds,
            "rounds_run": self.rounds_run,
            "convergence_status": self.convergence_status,
            "stopped_reason": self.stopped_reason,
            "typed_receipt_path_available": False,
            "authoritative_receipts_allowed": False,
            "projection_only": True,
            "receipt_authority": self.receipt_authority,
        }


@dataclass(frozen=True)
class QueryDirectorRepairAdvisoryPolicyV1:
    """Query shape for the non-authoritative AGI repair advisory policy."""

    include_field_lists: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_field_lists", bool(self.include_field_lists))


@dataclass(frozen=True)
class DirectorRepairAdvisoryPolicyResultV1:
    """Read-only policy projection for future AGI repair advisory overlays."""

    schema_version: str
    source: str
    access: str
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_advisory_no_writes_no_registration"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    authoritative_receipts_allowed: bool = False
    allowed_suggested_rule_fields: tuple[str, ...] = ()
    forbidden_metadata_fields: tuple[str, ...] = ()
    forbidden_suggested_rule_fields: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(
            self, "allowed_suggested_rule_fields", _to_tuple_str(list(self.allowed_suggested_rule_fields))
        )
        object.__setattr__(self, "forbidden_metadata_fields", _to_tuple_str(list(self.forbidden_metadata_fields)))
        object.__setattr__(
            self,
            "forbidden_suggested_rule_fields",
            _to_tuple_str(list(self.forbidden_suggested_rule_fields)),
        )
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "allowed_suggested_rule_fields": list(self.allowed_suggested_rule_fields),
            "forbidden_metadata_fields": list(self.forbidden_metadata_fields),
            "forbidden_suggested_rule_fields": list(self.forbidden_suggested_rule_fields),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairAdvisoryValidationV1:
    """Read-only query for validating a future AGI repair advisory payload."""

    advisor_source: str
    message: str
    confidence: float = 0.0
    suggested_rules: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advisor_source", _require_non_empty("advisor_source", self.advisor_source))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "confidence", max(0.0, min(float(self.confidence), 1.0)))
        object.__setattr__(self, "suggested_rules", tuple(dict(item or {}) for item in self.suggested_rules))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairAdvisoryValidationResultV1:
    """Read-only validation result for non-authoritative AGI repair advisory payloads."""

    schema_version: str
    source: str
    access: str
    ok: bool
    normalized_advisory: Mapping[str, Any] | None = None
    errors: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_advisory_validation_no_writes_no_registration"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    authoritative_receipts_allowed: bool = False
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(
            self,
            "normalized_advisory",
            dict(self.normalized_advisory) if self.normalized_advisory is not None else None,
        )
        object.__setattr__(self, "errors", _to_tuple_str(list(self.errors)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "ok": self.ok,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "normalized_advisory": dict(self.normalized_advisory) if self.normalized_advisory is not None else None,
            "errors": list(self.errors),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class CompareDirectorRepairShadowRunV1:
    """Read-only command for deterministic repair receipt projection comparison."""

    baseline_tool_results: tuple[Mapping[str, Any], ...] = ()
    kernel_receipts: tuple[RepairReceiptV1, ...] = ()
    comparison_mode: str = "independent_shadow_run"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "baseline_tool_results", tuple(dict(item or {}) for item in self.baseline_tool_results)
        )
        object.__setattr__(self, "kernel_receipts", tuple(self.kernel_receipts or ()))
        comparison_mode = str(self.comparison_mode or "").strip() or "independent_shadow_run"
        if comparison_mode not in {"independent_shadow_run", "receipt_projection_self_check"}:
            raise ValueError("comparison_mode must be independent_shadow_run or receipt_projection_self_check")
        object.__setattr__(self, "comparison_mode", comparison_mode)


@dataclass(frozen=True)
class DirectorRepairShadowComparisonResultV1:
    """Public read-only result for deterministic repair dark-launch comparison."""

    schema_version: str
    source: str
    access: str
    matched: bool
    baseline_source_tools: tuple[str, ...] = ()
    kernel_source_tools: tuple[str, ...] = ()
    baseline_paths: tuple[str, ...] = ()
    kernel_paths: tuple[str, ...] = ()
    missing_paths_in_kernel: tuple[str, ...] = ()
    extra_paths_in_kernel: tuple[str, ...] = ()
    missing_source_tools_in_kernel: tuple[str, ...] = ()
    extra_source_tools_in_kernel: tuple[str, ...] = ()
    comparison_mode: str = "independent_shadow_run"
    independent_shadow_required: bool = True
    independent_shadow_satisfied: bool = True
    cutover_ready: bool = False
    cutover_blockers: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_shadow_comparison_no_writes"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "matched", bool(self.matched))
        object.__setattr__(self, "baseline_source_tools", _to_tuple_str(list(self.baseline_source_tools)))
        object.__setattr__(self, "kernel_source_tools", _to_tuple_str(list(self.kernel_source_tools)))
        object.__setattr__(self, "baseline_paths", _to_tuple_str(list(self.baseline_paths)))
        object.__setattr__(self, "kernel_paths", _to_tuple_str(list(self.kernel_paths)))
        object.__setattr__(self, "missing_paths_in_kernel", _to_tuple_str(list(self.missing_paths_in_kernel)))
        object.__setattr__(self, "extra_paths_in_kernel", _to_tuple_str(list(self.extra_paths_in_kernel)))
        object.__setattr__(
            self,
            "missing_source_tools_in_kernel",
            _to_tuple_str(list(self.missing_source_tools_in_kernel)),
        )
        object.__setattr__(self, "extra_source_tools_in_kernel", _to_tuple_str(list(self.extra_source_tools_in_kernel)))
        comparison_mode = str(self.comparison_mode or "").strip() or "independent_shadow_run"
        object.__setattr__(self, "comparison_mode", comparison_mode)
        object.__setattr__(self, "independent_shadow_required", True)
        object.__setattr__(self, "independent_shadow_satisfied", bool(self.independent_shadow_satisfied))
        object.__setattr__(self, "cutover_ready", bool(self.cutover_ready))
        object.__setattr__(self, "cutover_blockers", _to_tuple_str(list(self.cutover_blockers)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "matched": self.matched,
            "baseline_source_tools": list(self.baseline_source_tools),
            "kernel_source_tools": list(self.kernel_source_tools),
            "baseline_paths": list(self.baseline_paths),
            "kernel_paths": list(self.kernel_paths),
            "missing_paths_in_kernel": list(self.missing_paths_in_kernel),
            "extra_paths_in_kernel": list(self.extra_paths_in_kernel),
            "missing_source_tools_in_kernel": list(self.missing_source_tools_in_kernel),
            "extra_source_tools_in_kernel": list(self.extra_source_tools_in_kernel),
            "comparison_mode": self.comparison_mode,
            "independent_shadow_required": self.independent_shadow_required,
            "independent_shadow_satisfied": self.independent_shadow_satisfied,
            "cutover_ready": self.cutover_ready,
            "cutover_blockers": list(self.cutover_blockers),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvaluateDirectorRepairCutoverReadinessV1:
    """Read-only command for requiring repeated independent shadow success before cutover."""

    comparisons: tuple[DirectorRepairShadowComparisonResultV1, ...] = ()
    required_successful_runs: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparisons", tuple(self.comparisons or ()))
        required = int(self.required_successful_runs or 0)
        object.__setattr__(self, "required_successful_runs", max(1, required))


@dataclass(frozen=True)
class DirectorRepairCutoverReadinessResultV1:
    """Public read-only gate result for deterministic repair cutover readiness."""

    schema_version: str
    source: str
    access: str
    cutover_ready: bool
    required_successful_runs: int
    comparison_count: int
    successful_comparison_count: int
    cutover_blockers: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_cutover_gate_no_writes"
    writes_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "cutover_ready", bool(self.cutover_ready))
        object.__setattr__(self, "required_successful_runs", max(1, int(self.required_successful_runs or 0)))
        object.__setattr__(self, "comparison_count", max(0, int(self.comparison_count or 0)))
        object.__setattr__(
            self,
            "successful_comparison_count",
            max(0, int(self.successful_comparison_count or 0)),
        )
        object.__setattr__(self, "cutover_blockers", _to_tuple_str(list(self.cutover_blockers)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "writes_allowed": False,
            "cutover_ready": self.cutover_ready,
            "required_successful_runs": self.required_successful_runs,
            "comparison_count": self.comparison_count,
            "successful_comparison_count": self.successful_comparison_count,
            "cutover_blockers": list(self.cutover_blockers),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ProjectDirectorRepairMetricsV1:
    """Read-only command for projecting deterministic repair health metrics."""

    receipts: tuple[RepairReceiptV1, ...] = ()
    coverage_reports: tuple[DirectorRepairCoverageReportV1, ...] = ()
    schedule_run_summaries: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "coverage_reports", tuple(self.coverage_reports or ()))
        object.__setattr__(
            self,
            "schedule_run_summaries",
            tuple(dict(item or {}) for item in self.schedule_run_summaries),
        )


@dataclass(frozen=True)
class DirectorRepairMetricsResultV1:
    """Public read-only metrics projection for repair kernel health."""

    schema_version: str
    source: str
    access: str
    receipt_count: int
    applied_receipt_count: int
    failed_receipt_count: int
    ineffective_receipt_count: int
    success_rate: float
    average_convergence_rounds: float
    uncovered_diagnostic_count: int
    coverage_gap_count: int
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_metrics_projection_no_writes"
    advisory_only: bool = True
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "receipt_count", max(0, int(self.receipt_count)))
        object.__setattr__(self, "applied_receipt_count", max(0, int(self.applied_receipt_count)))
        object.__setattr__(self, "failed_receipt_count", max(0, int(self.failed_receipt_count)))
        object.__setattr__(self, "ineffective_receipt_count", max(0, int(self.ineffective_receipt_count)))
        object.__setattr__(self, "success_rate", max(0.0, min(1.0, float(self.success_rate))))
        object.__setattr__(self, "average_convergence_rounds", max(0.0, float(self.average_convergence_rounds)))
        object.__setattr__(self, "uncovered_diagnostic_count", max(0, int(self.uncovered_diagnostic_count)))
        object.__setattr__(self, "coverage_gap_count", max(0, int(self.coverage_gap_count)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "advisory_only", True)
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "advisory_only": True,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "receipt_count": self.receipt_count,
            "applied_receipt_count": self.applied_receipt_count,
            "failed_receipt_count": self.failed_receipt_count,
            "ineffective_receipt_count": self.ineffective_receipt_count,
            "success_rate": self.success_rate,
            "average_convergence_rounds": self.average_convergence_rounds,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "coverage_gap_count": self.coverage_gap_count,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairResultV1:
    """Result shape for Director Runtime repair execution."""

    ok: bool
    receipts: tuple[RepairReceiptV1, ...] = ()
    residual_diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "residual_diagnostics", tuple(self.residual_diagnostics or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed DirectorRepairResultV1 must include error_code or error_message")


@dataclass(frozen=True)
class DirectorRepairPatchSummaryV1:
    """Public per-file patch projection for repair planning."""

    path: str
    operation_ids: tuple[str, ...]
    before_hash: str
    after_hash: str
    changed: bool
    content_after: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_non_empty("path", self.path).replace("\\", "/"))
        object.__setattr__(self, "operation_ids", _to_tuple_str(list(self.operation_ids)))
        object.__setattr__(self, "before_hash", _require_non_empty("before_hash", self.before_hash))
        object.__setattr__(self, "after_hash", _require_non_empty("after_hash", self.after_hash))
        object.__setattr__(self, "changed", bool(self.changed))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable patch summary."""

        return {
            "path": self.path,
            "operation_ids": list(self.operation_ids),
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "changed": self.changed,
            "content_after": self.content_after,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairCompositionIssueV1:
    """Public fail-closed patch composition issue."""

    code: str
    message: str
    path: str | None = None
    operation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_non_empty("code", self.code))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "path", str(self.path).replace("\\", "/") if self.path is not None else None)
        object.__setattr__(self, "operation_ids", _to_tuple_str(list(self.operation_ids)))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable composition issue."""

        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "operation_ids": list(self.operation_ids),
        }


@dataclass(frozen=True)
class DirectorRepairPlanSummaryV1:
    """Public repair plan summary without exposing internal kernel classes."""

    plan_id: str
    rule_id: str
    source_tool: str
    mode: str
    risk_level: str
    diagnostic_count: int
    operation_count: int
    advisor_note_count: int = 0
    agi_execution_authority: bool = False
    advisory_authoritative: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "rule_id", _require_non_empty("rule_id", self.rule_id))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        object.__setattr__(self, "risk_level", _require_non_empty("risk_level", self.risk_level))
        object.__setattr__(self, "diagnostic_count", max(0, int(self.diagnostic_count)))
        object.__setattr__(self, "operation_count", max(0, int(self.operation_count)))
        object.__setattr__(self, "advisor_note_count", max(0, int(self.advisor_note_count)))
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "advisory_authoritative", False)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable plan summary."""

        return {
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "mode": self.mode,
            "risk_level": self.risk_level,
            "diagnostic_count": self.diagnostic_count,
            "operation_count": self.operation_count,
            "advisor_note_count": self.advisor_note_count,
            "agi_execution_authority": False,
            "advisory_authoritative": False,
        }


@dataclass(frozen=True)
class DirectorRepairCompositionSummaryV1:
    """Public repair composition summary without exposing PatchComposer types."""

    ok: bool
    patches: tuple[DirectorRepairPatchSummaryV1, ...] = ()
    issues: tuple[DirectorRepairCompositionIssueV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "patches", tuple(self.patches or ()))
        object.__setattr__(self, "issues", tuple(self.issues or ()))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable composition summary."""

        return {
            "ok": self.ok,
            "patch_count": len(self.patches),
            "issue_count": len(self.issues),
            "changed_paths": [patch.path for patch in self.patches if patch.changed],
            "patches": [patch.to_dict() for patch in self.patches],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class DirectorRepairPlanningResultV1:
    """Public deterministic repair planning result."""

    ok: bool
    planned: bool
    source_tool: str
    diagnostic_count: int
    plan_summary: DirectorRepairPlanSummaryV1 | None = None
    composition_summary: DirectorRepairCompositionSummaryV1 = field(
        default_factory=lambda: DirectorRepairCompositionSummaryV1(ok=False)
    )
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = "director.repair_planning_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "director_authorized_tools_only"
    agi_execution_authority: bool = False
    advisory_authoritative: bool = False
    director_tool_execution_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "planned", bool(self.planned))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "diagnostic_count", max(0, int(self.diagnostic_count)))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "advisory_authoritative", False)
        object.__setattr__(self, "director_tool_execution_required", True)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable planning payload."""

        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "planned": self.planned,
            "source_tool": self.source_tool,
            "diagnostic_count": self.diagnostic_count,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "advisory_authoritative": False,
            "director_tool_execution_required": True,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "plan_summary": self.plan_summary.to_dict() if self.plan_summary is not None else None,
            "composition_summary": self.composition_summary.to_dict(),
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
        }


@dataclass(frozen=True)
class QueryDirectorRepairPlanProbeV1:
    """Read-only probe that verifies coverage matches can produce concrete repair plans."""

    artifact_quality_errors: tuple[str, ...]
    base_files: Mapping[str, str] = field(default_factory=dict)
    source_tools: tuple[str, ...] = ()
    mode: str = "shadow"
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "mode", str(self.mode or "shadow").strip() or "shadow")
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairPlanProbeItemV1:
    """One source-tool planning probe result for a covered diagnostic subset."""

    source_tool: str
    status: str
    planning_result: DirectorRepairPlanningResultV1
    matched_diagnostic_ids: tuple[str, ...] = ()
    matched_diagnostic_count: int = 0
    patch_count: int = 0
    changed_paths: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "matched_diagnostic_ids", _to_tuple_str(list(self.matched_diagnostic_ids)))
        object.__setattr__(self, "matched_diagnostic_count", max(0, int(self.matched_diagnostic_count)))
        object.__setattr__(self, "patch_count", max(0, int(self.patch_count)))
        object.__setattr__(self, "changed_paths", _to_tuple_str(list(self.changed_paths)))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_tool": self.source_tool,
            "status": self.status,
            "matched_diagnostic_ids": list(self.matched_diagnostic_ids),
            "matched_diagnostic_count": self.matched_diagnostic_count,
            "patch_count": self.patch_count,
            "changed_paths": list(self.changed_paths),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "planning_result": self.planning_result.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairPlanProbeResultV1:
    """Coverage plus planning proof for task-boundary repair selection."""

    status: str
    coverage_report: DirectorRepairCoverageReportV1
    items: tuple[DirectorRepairPlanProbeItemV1, ...] = ()
    plannable_source_tools: tuple[str, ...] = ()
    covered_unplannable_source_tools: tuple[str, ...] = ()
    covered_unplannable_diagnostics: tuple[Mapping[str, Any], ...] = ()
    uncovered_diagnostics: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.repair_plan_probe_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_plan_probe_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "plannable_source_tools", _to_tuple_str(list(self.plannable_source_tools)))
        object.__setattr__(
            self,
            "covered_unplannable_source_tools",
            _to_tuple_str(list(self.covered_unplannable_source_tools)),
        )
        object.__setattr__(
            self,
            "covered_unplannable_diagnostics",
            tuple(_to_dict_copy(item) for item in self.covered_unplannable_diagnostics),
        )
        object.__setattr__(
            self,
            "uncovered_diagnostics",
            tuple(_to_dict_copy(item) for item in self.uncovered_diagnostics),
        )
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "director_tool_execution_required", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "director_tool_execution_required": False,
            "coverage_report": self.coverage_report.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "plannable_source_tools": list(self.plannable_source_tools),
            "covered_unplannable_source_tools": list(self.covered_unplannable_source_tools),
            "covered_unplannable_diagnostic_count": len(self.covered_unplannable_diagnostics),
            "covered_unplannable_diagnostics": [dict(item) for item in self.covered_unplannable_diagnostics],
            "coverage_gap_count": len(self.uncovered_diagnostics),
            "uncovered_diagnostics": [dict(item) for item in self.uncovered_diagnostics],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorInterfaceDiscrepancyReceiptV1:
    """Canonical receipt for task-boundary interface discrepancies."""

    task_id: str
    status: str = "semantic_discrepancy_triage_required"
    source: str = "director.runtime.interface_discrepancy"
    plan_probe_status: str = ""
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    source_tools: tuple[str, ...] = ()
    recommended_owner: str = "chief_engineer"
    recommended_route: str = "pending_design_interface_contract"
    triage_policy: str = "ce_contract_if_missing_else_director_local_repair"
    macro_blueprint_regeneration_allowed: bool = False
    task_interface_contract_present: bool = False
    llm_fallback_blocked: bool = True
    director_retry_allowed: bool = False
    reason: str = "coverage_matched_but_unplannable"
    interface_delta: Mapping[str, Any] = field(default_factory=dict)
    triage_summary: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.interface_discrepancy_receipt.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "plan_probe_status", str(self.plan_probe_status or "").strip())
        object.__setattr__(
            self,
            "diagnostics",
            tuple(dict(item) for item in self.diagnostics if isinstance(item, Mapping)),
        )
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(
            self,
            "recommended_owner",
            str(self.recommended_owner or "chief_engineer").strip() or "chief_engineer",
        )
        object.__setattr__(
            self,
            "recommended_route",
            str(self.recommended_route or "pending_design_interface_contract").strip()
            or "pending_design_interface_contract",
        )
        object.__setattr__(self, "triage_policy", str(self.triage_policy or "").strip())
        object.__setattr__(
            self,
            "macro_blueprint_regeneration_allowed",
            bool(self.macro_blueprint_regeneration_allowed),
        )
        object.__setattr__(
            self,
            "task_interface_contract_present",
            bool(self.task_interface_contract_present),
        )
        object.__setattr__(self, "director_retry_allowed", bool(self.director_retry_allowed))
        object.__setattr__(
            self,
            "llm_fallback_blocked",
            bool(self.llm_fallback_blocked) and not bool(self.director_retry_allowed),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "interface_delta", _to_dict_copy(self.interface_delta))
        object.__setattr__(self, "triage_summary", _to_dict_copy(self.triage_summary))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        task_id: str = "",
    ) -> DirectorInterfaceDiscrepancyReceiptV1:
        task = str(value.get("task_id") or task_id or "unknown-task").strip()
        source_tools = value.get("source_tools") or value.get("covered_unplannable_source_tools") or ()
        diagnostics = value.get("diagnostics") or value.get("covered_unplannable_diagnostics") or ()
        return cls(
            task_id=task,
            status=str(value.get("status") or "semantic_discrepancy_triage_required"),
            source=str(value.get("source") or value.get("route") or "director.runtime.interface_discrepancy"),
            plan_probe_status=str(value.get("plan_probe_status") or ""),
            diagnostics=tuple(item for item in diagnostics if isinstance(item, Mapping))
            if isinstance(diagnostics, (list, tuple))
            else (),
            source_tools=(
                _to_tuple_str(list(source_tools))
                if isinstance(source_tools, (list, tuple))
                else ()
            ),
            recommended_owner=str(value.get("recommended_owner") or "chief_engineer"),
            recommended_route=str(value.get("recommended_route") or "pending_design_interface_contract"),
            triage_policy=str(value.get("triage_policy") or "ce_contract_if_missing_else_director_local_repair"),
            macro_blueprint_regeneration_allowed=bool(value.get("macro_blueprint_regeneration_allowed")),
            task_interface_contract_present=bool(value.get("task_interface_contract_present")),
            llm_fallback_blocked=bool(value.get("llm_fallback_blocked", True)),
            director_retry_allowed=bool(value.get("director_retry_allowed")),
            reason=str(value.get("reason") or "coverage_matched_but_unplannable"),
            interface_delta=_to_dict_copy(
                value.get("interface_delta") if isinstance(value.get("interface_delta"), Mapping) else {}
            ),
            triage_summary=_to_dict_copy(
                value.get("triage_summary") if isinstance(value.get("triage_summary"), Mapping) else {}
            ),
            metadata=_to_dict_copy(value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "status": self.status,
            "source": self.source,
            "plan_probe_status": self.plan_probe_status,
            "covered_unplannable": self.reason == "coverage_matched_but_unplannable",
            "diagnostics": [dict(item) for item in self.diagnostics],
            "source_tools": list(self.source_tools),
            "covered_unplannable_source_tools": list(self.source_tools),
            "covered_unplannable_diagnostic_count": len(self.diagnostics),
            "recommended_owner": self.recommended_owner,
            "recommended_route": self.recommended_route,
            "triage_policy": self.triage_policy,
            "macro_blueprint_regeneration_allowed": self.macro_blueprint_regeneration_allowed,
            "task_interface_contract_present": self.task_interface_contract_present,
            "llm_fallback_blocked": self.llm_fallback_blocked,
            "director_retry_allowed": self.director_retry_allowed,
            "reason": self.reason,
            "interface_delta": dict(self.interface_delta),
            "triage_summary": dict(self.triage_summary),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RunDirectorTaskBoundaryQualityLoopCommandV1:
    """Command shape for validating a complete CE task boundary through runtime repair convergence."""

    task_id: str
    workspace: str
    artifact_quality_errors: tuple[str, ...]
    base_files: Mapping[str, str]
    allowed_paths: tuple[str, ...] = ()
    source_tools: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    mode: str = "commit"
    max_rounds: int = 3
    task_interface_contract: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "allowed_paths", _to_tuple_str(list(self.allowed_paths)))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "mode", str(self.mode or "commit").strip() or "commit")
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "task_interface_contract", _to_dict_copy(self.task_interface_contract))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorTaskBoundaryQualityResultV1:
    """Result for the task-boundary quality loop consumed by QA and Factory validation."""

    task_id: str
    ok: bool
    status: str
    plan_probe: DirectorRepairPlanProbeResultV1
    convergence_result: DirectorRepairConvergenceResultV1 | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = "director.task_boundary_quality_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "runtime_plan_probe_then_convergence_with_adapter_effects"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed DirectorTaskBoundaryQualityResultV1 must include error_code or error_message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "ok": self.ok,
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "plan_probe": self.plan_probe.to_dict(),
            "convergence_result": self.convergence_result.to_dict() if self.convergence_result is not None else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }


class DirectorRuntimeError(RuntimeError):
    """Structured public error for director.runtime."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "AttachDirectorRepairRevalidationEvidenceV1",
    "CompareDirectorRepairShadowRunV1",
    "DirectorInterfaceDiscrepancyReceiptV1",
    "DirectorRepairAdvisoryPolicyResultV1",
    "DirectorRepairAdvisoryValidationResultV1",
    "DirectorRepairCallbackReceiptProjectionV1",
    "DirectorRepairCompositionIssueV1",
    "DirectorRepairCompositionSummaryV1",
    "DirectorRepairConvergenceResultV1",
    "DirectorRepairConvergenceRoundResultV1",
    "DirectorRepairConvergenceVerifierRequestV1",
    "DirectorRepairCoverageReportV1",
    "DirectorRepairCutoverReadinessResultV1",
    "DirectorRepairDiagnosticCoverageV1",
    "DirectorRepairKernelSummaryProjectionResultV1",
    "DirectorRepairLanguageSlotV1",
    "DirectorRepairLanguageSlotsResultV1",
    "DirectorRepairMaterializationQualityScheduleResultV1",
    "DirectorRepairMaterializationQualityScheduleRunResultV1",
    "DirectorRepairMaterializationQualityStepV1",
    "DirectorRepairMetricsResultV1",
    "DirectorRepairPatchSummaryV1",
    "DirectorRepairPlanProbeItemV1",
    "DirectorRepairPlanProbeResultV1",
    "DirectorRepairPlanSummaryV1",
    "DirectorRepairPlanningResultV1",
    "DirectorRepairPostExecutionScheduleResultV1",
    "DirectorRepairPostExecutionScheduleRunResultV1",
    "DirectorRepairPostExecutionStepV1",
    "DirectorRepairResultV1",
    "DirectorRepairRevalidationInputV1",
    "DirectorRepairRevalidationProjectionResultV1",
    "DirectorRepairRevalidationRequestV1",
    "DirectorRepairShadowComparisonResultV1",
    "DirectorRepairStrategyCatalogResultV1",
    "DirectorRepairVerifierSnapshotInputV1",
    "DirectorRuntimeError",
    "DirectorTaskBoundaryQualityResultV1",
    "EvaluateDirectorRepairCutoverReadinessV1",
    "PlanDirectorRepairCommandV1",
    "ProjectDirectorRepairKernelSummaryV1",
    "ProjectDirectorRepairMetricsV1",
    "QueryDirectorRepairAdvisoryPolicyV1",
    "QueryDirectorRepairAdvisoryValidationV1",
    "QueryDirectorRepairCoverageV1",
    "QueryDirectorRepairLanguageSlotsV1",
    "QueryDirectorRepairMaterializationQualityScheduleV1",
    "QueryDirectorRepairPlanProbeV1",
    "QueryDirectorRepairPostExecutionScheduleV1",
    "QueryDirectorRepairStrategyCatalogV1",
    "RepairAdvisoryV1",
    "RepairDiagnosticV1",
    "RepairReceiptV1",
    "RunDirectorRepairCommandV1",
    "RunDirectorRepairConvergenceCommandV1",
    "RunDirectorTaskBoundaryQualityLoopCommandV1",
]
