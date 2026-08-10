"""Environment-prep catalog, refresh requirements, plan, and receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import RepairReceiptV1
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_str,
)


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
