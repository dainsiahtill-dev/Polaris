"""Blueprint command/result/query DTOs for chief_engineer.blueprint."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from polaris.cells.chief_engineer.blueprint.public.contracts._behavior import (
    ChiefEngineerSharedBehaviorContractV1,
)
from polaris.cells.chief_engineer.blueprint.public.contracts._completion import (
    ProjectCompletionContractV1,
    VerificationCommandAuthorityV1,
)
from polaris.cells.chief_engineer.blueprint.public.contracts._helpers import (
    _PROVENANCE_BLUEPRINT_SCHEMA,
    _PROVENANCE_HASH_SCHEME,
    _PROVENANCE_SNAPSHOT_SCHEMA,
    _json_safe_mapping,
    _normalize_relative_portfolio_path,
    _relative_path_tuple,
    _require_completion_token,
    _require_non_empty,
    _require_provenance_blueprint_id,
    _require_provenance_identity,
    _require_provenance_path,
    _require_provenance_sha256,
    _require_safe_filename_token,
    _strict_provenance_target_paths,
    _strict_unique_string_tuple,
    _string_tuple,
    _to_dict_copy,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)


@dataclass(frozen=True)
class GenerateTaskBlueprintCommandV1:
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    llm_blueprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "constraints", _to_dict_copy(self.constraints))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "llm_blueprint", _to_dict_copy(self.llm_blueprint))


@dataclass(frozen=True)
class ChiefEngineerPortfolioTaskV1:
    """Authoritative PM task facts supplied to a project-level CE portfolio.

    The Chief Engineer may advise on these facts but cannot add target or scope
    paths. Paths are normalized to workspace-relative POSIX form so downstream
    scope intersections remain deterministic across operating systems.
    """

    task_id: str
    objective: str
    target_files: tuple[str, ...]
    scope_paths: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    entrypoint_targets: tuple[str, ...] = field(default_factory=tuple)
    topology_authority: Literal["pm", "chief_engineer"] = "pm"
    required_source_kinds: tuple[str, ...] = field(default_factory=tuple)
    primary_language: str = ""
    allowed_source_suffixes: tuple[str, ...] = field(default_factory=tuple)
    entrypoint_kind_authority: str = ""
    delivery_depth_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = _require_non_empty("task_id", self.task_id)
        dependencies = _strict_unique_string_tuple("dependencies", self.dependencies)
        if task_id in dependencies:
            raise ValueError("dependencies must not contain the task's own task_id")

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(
            self,
            "target_files",
            _relative_path_tuple("target_files", self.target_files, require_items=True),
        )
        object.__setattr__(self, "scope_paths", _relative_path_tuple("scope_paths", self.scope_paths))
        object.__setattr__(self, "dependencies", dependencies)
        entrypoint_targets = _relative_path_tuple("entrypoint_targets", self.entrypoint_targets)
        unknown_entrypoint_targets = sorted(set(entrypoint_targets) - set(self.target_files))
        if unknown_entrypoint_targets:
            raise ValueError(f"entrypoint_targets must be exact PM target_files; unknown={unknown_entrypoint_targets}")
        object.__setattr__(self, "entrypoint_targets", entrypoint_targets)
        topology_authority = str(self.topology_authority or "").strip()
        if topology_authority not in {"pm", "chief_engineer"}:
            raise ValueError("topology_authority must be 'pm' or 'chief_engineer'")
        required_source_kinds = _strict_unique_string_tuple(
            "required_source_kinds",
            self.required_source_kinds,
        )
        if topology_authority == "chief_engineer" and not required_source_kinds:
            raise ValueError("chief_engineer topology authority requires required_source_kinds")
        if topology_authority == "pm" and required_source_kinds:
            raise ValueError("PM topology authority must not declare delegated required_source_kinds")
        primary_language = str(self.primary_language or "").strip().lower()
        allowed_source_suffixes = _strict_unique_string_tuple(
            "allowed_source_suffixes",
            tuple(str(value or "").strip().lower() for value in self.allowed_source_suffixes),
        )
        if any(not suffix.startswith(".") or "/" in suffix or "\\" in suffix for suffix in allowed_source_suffixes):
            raise ValueError("allowed_source_suffixes must contain normalized file suffixes")
        if topology_authority == "chief_engineer" and (not primary_language or not allowed_source_suffixes):
            raise ValueError("chief_engineer topology authority requires immutable language suffix authority")
        entrypoint_kind_authority = str(self.entrypoint_kind_authority or "").strip().lower()
        if entrypoint_kind_authority and entrypoint_kind_authority not in {"cli", "web", "api", "library"}:
            raise ValueError("entrypoint_kind_authority must be cli, web, api, library, or empty")
        object.__setattr__(self, "topology_authority", topology_authority)
        object.__setattr__(self, "required_source_kinds", required_source_kinds)
        object.__setattr__(self, "primary_language", primary_language)
        object.__setattr__(self, "allowed_source_suffixes", allowed_source_suffixes)
        object.__setattr__(self, "entrypoint_kind_authority", entrypoint_kind_authority)
        object.__setattr__(
            self,
            "delivery_depth_contract",
            _to_dict_copy(self.delivery_depth_contract),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized PM-authoritative task projection."""

        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "target_files": list(self.target_files),
            "scope_paths": list(self.scope_paths),
            "dependencies": list(self.dependencies),
            "entrypoint_targets": list(self.entrypoint_targets),
            "topology_authority": self.topology_authority,
            "required_source_kinds": list(self.required_source_kinds),
            "primary_language": self.primary_language,
            "allowed_source_suffixes": list(self.allowed_source_suffixes),
            "entrypoint_kind_authority": self.entrypoint_kind_authority,
            "delivery_depth_contract": dict(self.delivery_depth_contract),
        }


_PORTFOLIO_AUTHORITY_CARRIER_SEAL = object()
_PORTFOLIO_AUTHORITY_SIGNING_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True, init=False)
class _ChiefEngineerPortfolioAuthorityCarrierV1:
    """Opaque, one-run authority issued only by the Factory owner path.

    This type is intentionally absent from the public Cell exports.  Its
    constructor requires a module-private identity seal, so public callers
    cannot replace committed PM/catalog/verifier evidence with self-consistent
    hashes.  The CE owner still revalidates mutable catalog state immediately
    before persistence.
    """

    workspace: str
    run_id: str
    project_id: str
    pm_stage_event_id: str
    pm_contract_hash: str
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...]
    catalog_snapshot: Mapping[str, Any]
    catalog_snapshot_hash: str
    catalog_version: str
    catalog_receipt_hash: str
    verifier_policy_hash: str
    verifier_policy_snapshot: Mapping[str, Any]
    verifier_policy_snapshot_hash: str
    verifier_policy_receipt_hash: str
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...]
    authority_signature: str

    def __init__(
        self,
        *,
        _seal: object,
        workspace: str,
        run_id: str,
        project_id: str,
        pm_stage_event_id: str,
        pm_contract_hash: str,
        tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
        catalog_snapshot: Mapping[str, Any],
        catalog_snapshot_hash: str,
        verifier_policy_hash: str,
        verifier_policy_snapshot: Mapping[str, Any],
        verifier_policy_snapshot_hash: str,
        verification_command_authority: tuple[VerificationCommandAuthorityV1, ...],
    ) -> None:
        if _seal is not _PORTFOLIO_AUTHORITY_CARRIER_SEAL:
            raise TypeError("Chief Engineer portfolio authority carrier is owner-issued only")
        normalized_workspace = _require_non_empty("workspace", workspace)
        normalized_run_id = _require_completion_token("run_id", run_id)
        normalized_project_id = _require_completion_token("project_id", project_id)
        normalized_pm_event = _require_completion_token("pm_stage_event_id", pm_stage_event_id)
        normalized_pm_hash = _require_provenance_sha256("pm_contract_hash", pm_contract_hash)
        normalized_catalog = deepcopy(dict(catalog_snapshot))
        normalized_catalog_hash = _require_provenance_sha256("catalog_snapshot_hash", catalog_snapshot_hash)
        if project_completion_catalog_snapshot_hash(normalized_catalog) != normalized_catalog_hash:
            raise ValueError("catalog_snapshot_hash does not bind catalog_snapshot")
        normalized_policy = deepcopy(dict(verifier_policy_snapshot))
        normalized_policy_hash = _require_provenance_sha256("verifier_policy_hash", verifier_policy_hash)
        normalized_policy_snapshot_hash = _require_provenance_sha256(
            "verifier_policy_snapshot_hash", verifier_policy_snapshot_hash
        )
        if project_completion_verifier_policy_snapshot_hash(normalized_policy) != normalized_policy_snapshot_hash:
            raise ValueError("verifier_policy_snapshot_hash does not bind verifier_policy_snapshot")
        if normalized_policy.get("policy_hash") != normalized_policy_hash:
            raise ValueError("verifier_policy_snapshot policy_hash does not match verifier_policy_hash")
        if normalized_policy.get("schema_version") != "evidence_policy.v1" or (
            normalized_policy.get("source") != "control_plane.verifier_policy.evidence_policy_compiler"
        ):
            raise ValueError("verifier_policy_snapshot is not an evidence policy compiler snapshot")
        required_modalities = normalized_policy.get("required_evidence_modalities")
        if not isinstance(required_modalities, list) or "command" not in required_modalities:
            raise ValueError("verifier_policy_snapshot must require command evidence")
        normalized_tasks = tuple(tasks)
        if not normalized_tasks or any(type(task) is not ChiefEngineerPortfolioTaskV1 for task in normalized_tasks):
            raise TypeError("authority carrier tasks must be exact ChiefEngineerPortfolioTaskV1 values")
        normalized_commands = tuple(verification_command_authority)
        if not normalized_commands or any(
            type(item) is not VerificationCommandAuthorityV1 for item in normalized_commands
        ):
            raise TypeError("authority carrier commands must be exact VerificationCommandAuthorityV1 values")
        task_ids = {task.task_id for task in normalized_tasks}
        if any(item.task_id not in task_ids for item in normalized_commands):
            raise ValueError("verification command authority task_id is outside carrier tasks")
        catalog_receipt_hash = _portfolio_authority_receipt_hash(
            domain="catalog",
            workspace=normalized_workspace,
            run_id=normalized_run_id,
            project_id=normalized_project_id,
            pm_stage_event_id=normalized_pm_event,
            pm_contract_hash=normalized_pm_hash,
            evidence_hash=normalized_catalog_hash,
        )
        verifier_policy_receipt_hash = _portfolio_authority_receipt_hash(
            domain="verifier_policy",
            workspace=normalized_workspace,
            run_id=normalized_run_id,
            project_id=normalized_project_id,
            pm_stage_event_id=normalized_pm_event,
            pm_contract_hash=normalized_pm_hash,
            evidence_hash=normalized_policy_snapshot_hash,
        )
        for name, value in (
            ("workspace", normalized_workspace),
            ("run_id", normalized_run_id),
            ("project_id", normalized_project_id),
            ("pm_stage_event_id", normalized_pm_event),
            ("pm_contract_hash", normalized_pm_hash),
            ("tasks", normalized_tasks),
            ("catalog_snapshot", normalized_catalog),
            ("catalog_snapshot_hash", normalized_catalog_hash),
            ("catalog_version", f"sha256:{normalized_catalog_hash}"),
            ("catalog_receipt_hash", catalog_receipt_hash),
            ("verifier_policy_hash", normalized_policy_hash),
            ("verifier_policy_snapshot", normalized_policy),
            ("verifier_policy_snapshot_hash", normalized_policy_snapshot_hash),
            ("verifier_policy_receipt_hash", verifier_policy_receipt_hash),
            ("verification_command_authority", normalized_commands),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "authority_signature", _portfolio_authority_carrier_signature(self))


def _portfolio_authority_receipt_hash(
    *,
    domain: str,
    workspace: str,
    run_id: str,
    project_id: str,
    pm_stage_event_id: str,
    pm_contract_hash: str,
    evidence_hash: str,
) -> str:
    seed = {
        "schema_version": "polaris.ce_portfolio_authority_receipt.v1",
        "domain": domain,
        "workspace": str(Path(workspace).resolve()),
        "run_id": run_id,
        "project_id": project_id,
        "pm_stage_event_id": pm_stage_event_id,
        "pm_contract_hash": pm_contract_hash,
        "evidence_hash": evidence_hash,
    }
    return hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _issue_chief_engineer_portfolio_authority_carrier(
    **kwargs: Any,
) -> _ChiefEngineerPortfolioAuthorityCarrierV1:
    """Private Factory-owner bridge; deliberately not exported by the Cell."""

    return _ChiefEngineerPortfolioAuthorityCarrierV1(_seal=_PORTFOLIO_AUTHORITY_CARRIER_SEAL, **kwargs)


def _portfolio_authority_carrier_signature(carrier: _ChiefEngineerPortfolioAuthorityCarrierV1) -> str:
    seed = {
        "schema_version": "polaris.ce_portfolio_authority_carrier.v1",
        "workspace": str(Path(carrier.workspace).resolve()),
        "run_id": carrier.run_id,
        "project_id": carrier.project_id,
        "pm_stage_event_id": carrier.pm_stage_event_id,
        "pm_contract_hash": carrier.pm_contract_hash,
        "tasks": [task.to_dict() for task in carrier.tasks],
        "catalog_snapshot_hash": carrier.catalog_snapshot_hash,
        "catalog_version": carrier.catalog_version,
        "catalog_receipt_hash": carrier.catalog_receipt_hash,
        "verifier_policy_hash": carrier.verifier_policy_hash,
        "verifier_policy_snapshot_hash": carrier.verifier_policy_snapshot_hash,
        "verifier_policy_receipt_hash": carrier.verifier_policy_receipt_hash,
        "verification_command_authority_hashes": sorted(
            item.authority_hash for item in carrier.verification_command_authority
        ),
    }
    encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_PORTFOLIO_AUTHORITY_SIGNING_KEY, encoded, hashlib.sha256).hexdigest()


def _verify_chief_engineer_portfolio_authority_carrier(
    carrier: _ChiefEngineerPortfolioAuthorityCarrierV1,
) -> bool:
    """Verify opaque owner signature without exposing signing material."""

    if type(carrier) is not _ChiefEngineerPortfolioAuthorityCarrierV1:
        return False
    return hmac.compare_digest(carrier.authority_signature, _portfolio_authority_carrier_signature(carrier))


@dataclass(frozen=True)
class BuildChiefEngineerBlueprintPortfolioCommandV1:
    """Build one CE portfolio using an opaque Factory-issued authority carrier."""

    workspace: str
    run_id: str
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...]
    authority_carrier: object | None = field(default=None, repr=False)
    llm_blueprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tasks, (list, tuple)):
            raise TypeError("tasks must be a list or tuple of ChiefEngineerPortfolioTaskV1")
        if not self.tasks:
            raise ValueError("tasks must contain at least one task")

        tasks: list[ChiefEngineerPortfolioTaskV1] = []
        seen_task_ids: set[str] = set()
        for index, task in enumerate(self.tasks):
            if type(task) is not ChiefEngineerPortfolioTaskV1:
                raise TypeError(f"tasks[{index}] must be exact ChiefEngineerPortfolioTaskV1")
            if task.task_id in seen_task_ids:
                raise ValueError(f"duplicate task_id in portfolio command: {task.task_id}")
            seen_task_ids.add(task.task_id)
            tasks.append(task)

        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_completion_token("run_id", self.run_id))
        llm_blueprint = _to_dict_copy(self.llm_blueprint)
        if llm_blueprint:
            if type(self.authority_carrier) is not _ChiefEngineerPortfolioAuthorityCarrierV1:
                raise TypeError("advisory portfolio requires exact Factory-issued authority_carrier")
            carrier = self.authority_carrier
            if carrier.workspace != self.workspace or carrier.run_id != self.run_id or carrier.tasks != tuple(tasks):
                raise ValueError("authority_carrier identity does not match portfolio command")
        elif self.authority_carrier is not None:
            raise ValueError("offline diagnostic portfolio must not carry execution authority")
        object.__setattr__(self, "tasks", tuple(tasks))
        object.__setattr__(self, "llm_blueprint", llm_blueprint)


@dataclass(frozen=True, slots=True)
class QueryProjectCompletionContractV1:
    """Read one persisted CE-owned completion contract by exact authority identity."""

    workspace: str
    project_id: str
    run_id: str
    contract_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "project_id", _require_completion_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_completion_token("run_id", self.run_id))
        object.__setattr__(
            self,
            "contract_hash",
            _require_provenance_sha256("contract_hash", self.contract_hash),
        )


@dataclass(frozen=True)
class ChiefEngineerProjectInterfaceContractV1:
    """Shared task/file ownership and advisory project interface declarations."""

    contract_id: str
    contract_ref: str
    contract_hash: str
    task_file_ownership: Mapping[str, tuple[str, ...]]
    file_task_ownership: Mapping[str, tuple[str, ...]]
    provider_declarations: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    consumer_declarations: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        contract_id = _require_safe_filename_token("contract_id", self.contract_id)
        contract_ref = _require_non_empty("contract_ref", self.contract_ref)
        path_part, separator, fragment = contract_ref.partition("#")
        if separator != "#" or fragment != "project_interface_contract":
            raise ValueError("contract_ref must target the project_interface_contract fragment")
        normalized_path = _normalize_relative_portfolio_path("contract_ref", path_part)
        normalized_ref = f"{normalized_path}#project_interface_contract"

        if not isinstance(self.task_file_ownership, Mapping):
            raise TypeError("task_file_ownership must be a mapping")
        if not isinstance(self.file_task_ownership, Mapping):
            raise TypeError("file_task_ownership must be a mapping")

        task_file_ownership: dict[str, tuple[str, ...]] = {}
        expected_file_owners: dict[str, list[str]] = {}
        for raw_task_id, raw_paths in self.task_file_ownership.items():
            task_id = _require_non_empty("task_file_ownership task_id", str(raw_task_id))
            if task_id in task_file_ownership:
                raise ValueError(f"duplicate task_file_ownership task_id: {task_id}")
            paths = _relative_path_tuple(
                f"task_file_ownership[{task_id!r}]",
                raw_paths,
                require_items=True,
            )
            task_file_ownership[task_id] = paths
            for path in paths:
                expected_file_owners.setdefault(path, []).append(task_id)

        file_task_ownership: dict[str, tuple[str, ...]] = {}
        for raw_path, raw_task_ids in self.file_task_ownership.items():
            path = _normalize_relative_portfolio_path("file_task_ownership path", str(raw_path))
            if path in file_task_ownership:
                raise ValueError(f"duplicate normalized file_task_ownership path: {path}")
            file_task_ownership[path] = _strict_unique_string_tuple(
                f"file_task_ownership[{path!r}]",
                raw_task_ids,
                require_items=True,
            )

        expected_reverse = {path: tuple(task_ids) for path, task_ids in expected_file_owners.items()}
        if file_task_ownership != expected_reverse:
            raise ValueError("file_task_ownership must be the exact reverse of task_file_ownership")

        def normalize_declarations(name: str, value: Any) -> tuple[dict[str, Any], ...]:
            if not isinstance(value, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple of mappings")
            declarations: list[dict[str, Any]] = []
            for index, declaration in enumerate(value):
                if not isinstance(declaration, Mapping):
                    raise TypeError(f"{name}[{index}] must be a mapping")
                declarations.append(_json_safe_mapping(declaration))
            return tuple(declarations)

        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "contract_ref", normalized_ref)
        object.__setattr__(self, "contract_hash", _require_non_empty("contract_hash", self.contract_hash))
        object.__setattr__(self, "task_file_ownership", task_file_ownership)
        object.__setattr__(self, "file_task_ownership", file_task_ownership)
        object.__setattr__(
            self,
            "provider_declarations",
            normalize_declarations("provider_declarations", self.provider_declarations),
        )
        object.__setattr__(
            self,
            "consumer_declarations",
            normalize_declarations("consumer_declarations", self.consumer_declarations),
        )

    def to_reference(self) -> dict[str, str]:
        """Return the stable shared interface-contract identity."""

        return {
            "schema_version": "chief_engineer.project_interface_contract.reference.v1",
            "project_interface_contract_id": self.contract_id,
            "project_interface_contract_ref": self.contract_ref,
            "project_interface_contract_hash": self.contract_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical shared project interface contract."""

        return {
            "schema_version": "chief_engineer.project_interface_contract.v1",
            "project_interface_contract_id": self.contract_id,
            "project_interface_contract_ref": self.contract_ref,
            "project_interface_contract_hash": self.contract_hash,
            "task_file_ownership": {task_id: list(paths) for task_id, paths in self.task_file_ownership.items()},
            "file_task_ownership": {path: list(task_ids) for path, task_ids in self.file_task_ownership.items()},
            "provider_declarations": [_json_safe_mapping(declaration) for declaration in self.provider_declarations],
            "consumer_declarations": [_json_safe_mapping(declaration) for declaration in self.consumer_declarations],
            "ownership_authority": "pm_authoritative_tasks",
            "interface_declaration_authority": "chief_engineer_advisory_only",
            "authoritative": False,
        }


@dataclass(frozen=True)
class ChiefEngineerBlueprintPortfolioV1:
    """Immutable project-level CE advice with task-scoped canonical overlays."""

    portfolio_id: str
    workspace: str
    run_id: str
    portfolio_path: str
    portfolio_hash: str
    task_ids: tuple[str, ...]
    task_overlays: Mapping[str, Mapping[str, Any]]
    scope_advisory: Mapping[str, Mapping[str, Any]]
    project_interface_contract: ChiefEngineerProjectInterfaceContractV1
    project_interface_contract_ref: str
    project_interface_contract_hash: str
    project_completion_contract: ProjectCompletionContractV1 | None
    project_completion_contract_ref: str | None
    project_completion_contract_hash: str | None
    risk_flags: tuple[str, ...]
    llm_blueprint_consumed: bool
    usage_mode: Literal["advisory_overlay", "offline_diagnostic_only"]
    shared_behavior_contract: ChiefEngineerSharedBehaviorContractV1 | None = None
    shared_behavior_contract_ref: str | None = None
    shared_behavior_contract_hash: str | None = None
    authority: Literal["advisory_only"] = "advisory_only"
    immutable: bool = True
    handoff_ready: Literal[False] = False
    execution_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        portfolio_id = _require_safe_filename_token("portfolio_id", self.portfolio_id)
        portfolio_hash = _require_non_empty("portfolio_hash", self.portfolio_hash)

        portfolio_path = _normalize_relative_portfolio_path("portfolio_path", self.portfolio_path)
        expected_path = f"runtime/blueprints/{portfolio_id}.json"
        if portfolio_path != expected_path:
            raise ValueError(f"portfolio_path must equal {expected_path!r}")

        task_ids = _strict_unique_string_tuple("task_ids", self.task_ids, require_items=True)
        if len(task_ids) != len(self.task_ids):
            raise ValueError("task_ids must not contain duplicates")
        if not isinstance(self.task_overlays, Mapping):
            raise TypeError("task_overlays must be a mapping keyed by task_id")
        if not isinstance(self.scope_advisory, Mapping):
            raise TypeError("scope_advisory must be a mapping keyed by task_id")

        expected_task_ids = set(task_ids)
        if set(self.task_overlays) != expected_task_ids:
            raise ValueError("task_overlays keys must exactly match task_ids")
        if set(self.scope_advisory) != expected_task_ids:
            raise ValueError("scope_advisory keys must exactly match task_ids")
        if not isinstance(self.project_interface_contract, ChiefEngineerProjectInterfaceContractV1):
            raise TypeError("project_interface_contract must be ChiefEngineerProjectInterfaceContractV1")
        if set(self.project_interface_contract.task_file_ownership) != expected_task_ids:
            raise ValueError("project_interface_contract task ownership must exactly match task_ids")

        interface_ref = _require_non_empty(
            "project_interface_contract_ref",
            self.project_interface_contract_ref,
        )
        interface_hash = _require_non_empty(
            "project_interface_contract_hash",
            self.project_interface_contract_hash,
        )
        if interface_ref != self.project_interface_contract.contract_ref:
            raise ValueError("project_interface_contract_ref must match the shared contract")
        if interface_hash != self.project_interface_contract.contract_hash:
            raise ValueError("project_interface_contract_hash must match the shared contract")

        if not isinstance(self.llm_blueprint_consumed, bool):
            raise TypeError("llm_blueprint_consumed must be a bool")
        expected_usage_mode = "advisory_overlay" if self.llm_blueprint_consumed else "offline_diagnostic_only"
        if self.usage_mode != expected_usage_mode:
            raise ValueError(f"usage_mode must be {expected_usage_mode!r}")

        completion_contract = self.project_completion_contract
        completion_ref = self.project_completion_contract_ref
        completion_hash = self.project_completion_contract_hash
        behavior_contract = self.shared_behavior_contract
        behavior_ref = self.shared_behavior_contract_ref
        behavior_hash = self.shared_behavior_contract_hash
        if self.llm_blueprint_consumed:
            if type(completion_contract) is not ProjectCompletionContractV1:
                raise TypeError("advisory portfolio requires exact ProjectCompletionContractV1")
            completion_contract_v1 = cast(ProjectCompletionContractV1, completion_contract)
            expected_completion_ref = f"{portfolio_path}#project_completion_contract"
            if completion_ref != expected_completion_ref:
                raise ValueError("project_completion_contract_ref must target the portfolio contract fragment")
            if completion_hash != completion_contract_v1.contract_hash:
                raise ValueError("project_completion_contract_hash must match the project completion contract")
            if completion_contract_v1.run_id != self.run_id:
                raise ValueError("project completion contract run_id must match portfolio run_id")
            if completion_contract_v1.covered_task_ids != tuple(sorted(task_ids)):
                raise ValueError("project completion contract must cover the exact portfolio task set")
            behavior_values = (behavior_contract, behavior_ref, behavior_hash)
            if any(value is not None for value in behavior_values):
                if not all(value is not None for value in behavior_values):
                    raise ValueError("shared behavior contract identity must be complete when present")
                if type(behavior_contract) is not ChiefEngineerSharedBehaviorContractV1:
                    raise TypeError("shared_behavior_contract must be exact ChiefEngineerSharedBehaviorContractV1")
                behavior_contract_v1 = cast(ChiefEngineerSharedBehaviorContractV1, behavior_contract)
                expected_behavior_ref = f"{portfolio_path}#shared_behavior_contract"
                if behavior_ref != expected_behavior_ref:
                    raise ValueError("shared_behavior_contract_ref must target the portfolio contract fragment")
                if behavior_hash != behavior_contract_v1.contract_hash:
                    raise ValueError("shared_behavior_contract_hash must match the shared behavior contract")
                if set(behavior_contract_v1.task_bindings) != expected_task_ids:
                    raise ValueError("shared behavior task bindings must cover the exact portfolio task set")
        elif any(
            value is not None
            for value in (
                completion_contract,
                completion_ref,
                completion_hash,
                behavior_contract,
                behavior_ref,
                behavior_hash,
            )
        ):
            raise ValueError("offline diagnostic portfolio cannot bind completion or behavior contracts")

        task_overlays: dict[str, dict[str, Any]] = {}
        scope_advisory: dict[str, dict[str, Any]] = {}
        required_overlay_keys = {
            "authority",
            "construction_plan",
            "execution_authorized",
            "handoff_ready",
            "llm_blueprint_consumed",
            "portfolio_hash",
            "portfolio_id",
            "portfolio_path",
            "project_interface_contract_hash",
            "project_interface_contract_ref",
            "project_completion_contract_hash",
            "project_completion_contract_ref",
            "shared_behavior_contract_hash",
            "shared_behavior_contract_ref",
            "reference",
            "risk_flags",
            "scope_for_apply",
            "usage_mode",
        }
        for task_id in task_ids:
            overlay = self.task_overlays[task_id]
            if not isinstance(overlay, Mapping):
                raise TypeError(f"task_overlays[{task_id!r}] must be a mapping")
            if set(overlay) != required_overlay_keys:
                raise ValueError(f"task_overlays[{task_id!r}] does not match the canonical portfolio overlay shape")
            construction_plan = overlay.get("construction_plan")
            if not isinstance(construction_plan, Mapping):
                raise TypeError(f"task_overlays[{task_id!r}].construction_plan must be a mapping")
            if overlay.get("portfolio_id") != portfolio_id:
                raise ValueError(f"task_overlays[{task_id!r}] portfolio_id binding mismatch")
            if overlay.get("portfolio_path") != portfolio_path:
                raise ValueError(f"task_overlays[{task_id!r}] portfolio_path binding mismatch")
            if overlay.get("portfolio_hash") != portfolio_hash:
                raise ValueError(f"task_overlays[{task_id!r}] portfolio_hash binding mismatch")
            if overlay.get("project_interface_contract_ref") != interface_ref:
                raise ValueError(f"task_overlays[{task_id!r}] interface ref binding mismatch")
            if overlay.get("project_interface_contract_hash") != interface_hash:
                raise ValueError(f"task_overlays[{task_id!r}] interface hash binding mismatch")
            if overlay.get("project_completion_contract_ref") != completion_ref:
                raise ValueError(f"task_overlays[{task_id!r}] completion ref binding mismatch")
            if overlay.get("project_completion_contract_hash") != completion_hash:
                raise ValueError(f"task_overlays[{task_id!r}] completion hash binding mismatch")
            if overlay.get("shared_behavior_contract_ref") != behavior_ref:
                raise ValueError(f"task_overlays[{task_id!r}] behavior ref binding mismatch")
            if overlay.get("shared_behavior_contract_hash") != behavior_hash:
                raise ValueError(f"task_overlays[{task_id!r}] behavior hash binding mismatch")
            if overlay.get("authority") != "advisory_only":
                raise ValueError(f"task_overlays[{task_id!r}] authority must be advisory_only")
            if overlay.get("llm_blueprint_consumed") is not self.llm_blueprint_consumed:
                raise ValueError(f"task_overlays[{task_id!r}] LLM consumption binding mismatch")
            if overlay.get("usage_mode") != self.usage_mode:
                raise ValueError(f"task_overlays[{task_id!r}] usage_mode binding mismatch")
            if overlay.get("handoff_ready") is not False:
                raise ValueError(f"task_overlays[{task_id!r}] cannot declare handoff readiness")
            if overlay.get("execution_authorized") is not False:
                raise ValueError(f"task_overlays[{task_id!r}] cannot authorize execution")

            reference = overlay.get("reference")
            if not isinstance(reference, Mapping):
                raise TypeError(f"task_overlays[{task_id!r}].reference must be a mapping")
            expected_reference = {
                "schema_version": "chief_engineer.blueprint_portfolio.task_reference.v1",
                "task_id": task_id,
                "portfolio_id": portfolio_id,
                "portfolio_path": portfolio_path,
                "portfolio_hash": portfolio_hash,
                "project_interface_contract_ref": interface_ref,
                "project_interface_contract_hash": interface_hash,
                "project_completion_contract_ref": completion_ref,
                "project_completion_contract_hash": completion_hash,
                "shared_behavior_contract_ref": behavior_ref,
                "shared_behavior_contract_hash": behavior_hash,
            }
            if dict(reference) != expected_reference:
                raise ValueError(f"task_overlays[{task_id!r}].reference binding mismatch")

            task_overlays[task_id] = {
                "construction_plan": _json_safe_mapping(construction_plan),
                "scope_for_apply": list(
                    _relative_path_tuple(
                        f"task_overlays[{task_id!r}].scope_for_apply",
                        overlay.get("scope_for_apply"),
                    )
                ),
                "risk_flags": list(
                    _strict_unique_string_tuple(
                        f"task_overlays[{task_id!r}].risk_flags",
                        overlay.get("risk_flags"),
                    )
                ),
                "portfolio_id": portfolio_id,
                "portfolio_path": portfolio_path,
                "portfolio_hash": portfolio_hash,
                "project_interface_contract_ref": interface_ref,
                "project_interface_contract_hash": interface_hash,
                "project_completion_contract_ref": completion_ref,
                "project_completion_contract_hash": completion_hash,
                "shared_behavior_contract_ref": behavior_ref,
                "shared_behavior_contract_hash": behavior_hash,
                "reference": expected_reference,
                "llm_blueprint_consumed": self.llm_blueprint_consumed,
                "usage_mode": self.usage_mode,
                "authority": "advisory_only",
                "handoff_ready": False,
                "execution_authorized": False,
            }

            advisory = self.scope_advisory[task_id]
            if not isinstance(advisory, Mapping):
                raise TypeError(f"scope_advisory[{task_id!r}] must be a mapping")
            scope_advisory[task_id] = _json_safe_mapping(advisory)

        if self.authority != "advisory_only":
            raise ValueError("authority must be 'advisory_only'")
        if self.immutable is not True:
            raise ValueError("immutable portfolios cannot be disabled")
        if self.handoff_ready is not False:
            raise ValueError("portfolio cannot declare handoff readiness")
        if self.execution_authorized is not False:
            raise ValueError("portfolio cannot authorize execution")

        object.__setattr__(self, "portfolio_id", portfolio_id)
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "portfolio_path", portfolio_path)
        object.__setattr__(self, "portfolio_hash", portfolio_hash)
        object.__setattr__(self, "task_ids", task_ids)
        object.__setattr__(self, "task_overlays", task_overlays)
        object.__setattr__(self, "scope_advisory", scope_advisory)
        object.__setattr__(self, "project_interface_contract_ref", interface_ref)
        object.__setattr__(self, "project_interface_contract_hash", interface_hash)
        object.__setattr__(self, "project_completion_contract_ref", completion_ref)
        object.__setattr__(self, "project_completion_contract_hash", completion_hash)
        object.__setattr__(self, "shared_behavior_contract_ref", behavior_ref)
        object.__setattr__(self, "shared_behavior_contract_hash", behavior_hash)
        object.__setattr__(self, "risk_flags", _strict_unique_string_tuple("risk_flags", self.risk_flags))

    def to_reference(self) -> dict[str, Any]:
        """Return the durable identity needed by downstream projections."""

        context = {
            "schema_version": "chief_engineer.blueprint_portfolio.reference.v1",
            "portfolio_id": self.portfolio_id,
            "portfolio_path": self.portfolio_path,
            "portfolio_hash": self.portfolio_hash,
            "project_interface_contract_ref": self.project_interface_contract_ref,
            "project_interface_contract_hash": self.project_interface_contract_hash,
            "project_completion_contract_ref": self.project_completion_contract_ref,
            "project_completion_contract_hash": self.project_completion_contract_hash,
            "shared_behavior_contract_ref": self.shared_behavior_contract_ref,
            "shared_behavior_contract_hash": self.shared_behavior_contract_hash,
        }
        return context

    @property
    def reference(self) -> dict[str, Any]:
        """Return a fresh immutable-portfolio reference mapping."""

        return self.to_reference()

    def to_task_blueprint_context(self) -> dict[str, Any]:
        """Return canonical evidence fields for ``generate_task_blueprint`` context."""

        context = {
            "blueprint_portfolio_ref": self.portfolio_path,
            "blueprint_portfolio_hash": self.portfolio_hash,
            "project_interface_contract_ref": self.project_interface_contract_ref,
            "project_interface_contract_hash": self.project_interface_contract_hash,
            "project_interface_contract": self.project_interface_contract.to_dict(),
            "project_completion_contract_ref": self.project_completion_contract_ref,
            "project_completion_contract_hash": self.project_completion_contract_hash,
            "project_completion_contract": (
                self.project_completion_contract.to_dict() if self.project_completion_contract is not None else None
            ),
        }
        if self.shared_behavior_contract is not None:
            context.update(
                {
                    "shared_behavior_contract_ref": self.shared_behavior_contract_ref,
                    "shared_behavior_contract_hash": self.shared_behavior_contract_hash,
                    "shared_behavior_contract": self.shared_behavior_contract.to_dict(),
                }
            )
        return context

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON payload persisted by the public service."""

        return {
            "schema_version": "chief_engineer.blueprint_portfolio.v1",
            "kind": "chief_engineer_blueprint_portfolio",
            "portfolio_id": self.portfolio_id,
            "portfolio_path": self.portfolio_path,
            "portfolio_hash": self.portfolio_hash,
            "workspace": self.workspace,
            "run_id": self.run_id,
            "task_ids": list(self.task_ids),
            "task_overlays": {task_id: _json_safe_mapping(self.task_overlays[task_id]) for task_id in self.task_ids},
            "scope_advisory": {task_id: _json_safe_mapping(self.scope_advisory[task_id]) for task_id in self.task_ids},
            "project_interface_contract": self.project_interface_contract.to_dict(),
            "project_interface_contract_ref": self.project_interface_contract_ref,
            "project_interface_contract_hash": self.project_interface_contract_hash,
            "project_completion_contract": (
                self.project_completion_contract.to_dict() if self.project_completion_contract is not None else None
            ),
            "project_completion_contract_ref": self.project_completion_contract_ref,
            "project_completion_contract_hash": self.project_completion_contract_hash,
            "shared_behavior_contract": (
                self.shared_behavior_contract.to_dict() if self.shared_behavior_contract is not None else None
            ),
            "shared_behavior_contract_ref": self.shared_behavior_contract_ref,
            "shared_behavior_contract_hash": self.shared_behavior_contract_hash,
            "risk_flags": list(self.risk_flags),
            "llm_blueprint_consumed": self.llm_blueprint_consumed,
            "usage_mode": self.usage_mode,
            "authority": self.authority,
            "authoritative": False,
            "mainline_authority": False,
            "handoff_ready": self.handoff_ready,
            "execution_authorized": self.execution_authorized,
            "contract_authority": "pm_authoritative_tasks",
            "immutable": self.immutable,
            "reference": self.to_reference(),
        }


@dataclass(frozen=True)
class GetBlueprintStatusQueryV1:
    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class QueryBlueprintProvenanceV1:
    """Validate one already-captured blueprint mapping without storage access."""

    blueprint: Mapping[str, Any]
    expected_pm_task: Mapping[str, Any]
    expected_factory_run_id: str
    expected_task_id: str
    expected_blueprint_id: str
    expected_logical_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.blueprint, Mapping):
            raise TypeError("blueprint must be a mapping")
        if not isinstance(self.expected_pm_task, Mapping) or not self.expected_pm_task:
            raise ValueError("expected_pm_task must be a non-empty mapping")
        object.__setattr__(self, "blueprint", deepcopy(dict(self.blueprint)))
        object.__setattr__(self, "expected_pm_task", deepcopy(dict(self.expected_pm_task)))
        object.__setattr__(
            self,
            "expected_factory_run_id",
            _require_provenance_identity("expected_factory_run_id", self.expected_factory_run_id),
        )
        object.__setattr__(
            self,
            "expected_task_id",
            _require_provenance_identity("expected_task_id", self.expected_task_id),
        )
        blueprint_id = _require_provenance_blueprint_id("expected_blueprint_id", self.expected_blueprint_id)
        object.__setattr__(self, "expected_blueprint_id", blueprint_id)
        object.__setattr__(
            self,
            "expected_logical_path",
            _require_provenance_path("expected_logical_path", self.expected_logical_path),
        )


@dataclass(frozen=True)
class TaskBlueprintGeneratedEventV1:
    event_id: str
    task_id: str
    workspace: str
    blueprint_path: str
    generated_at: str
    risk_level: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "blueprint_path", _require_non_empty("blueprint_path", self.blueprint_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class ArchitectureDecisionV1:
    """Structured Chief Engineer architecture or dependency decision."""

    concern: str
    decision: str
    selected_libraries: tuple[str, ...] = field(default_factory=tuple)
    options_considered: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    constraints: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "medium"
    evidence: Mapping[str, Any] = field(default_factory=dict)
    decision_status: str = "decision"
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "concern", _require_non_empty("concern", self.concern))
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "selected_libraries", _string_tuple(self.selected_libraries))
        object.__setattr__(self, "options_considered", _string_tuple(self.options_considered))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "constraints", _string_tuple(self.constraints))
        risk_level = str(self.risk_level or "medium").strip().lower() or "medium"
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "evidence", _json_safe_mapping(self.evidence))
        decision_status = str(self.decision_status or "decision").strip().lower() or "decision"
        object.__setattr__(self, "decision_status", decision_status)
        object.__setattr__(self, "source", str(self.source or "").strip())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ArchitectureDecisionV1:
        """Build a decision contract from a persisted JSON payload."""

        evidence = _json_safe_mapping(payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {})
        decision_status = str(
            payload.get("decision_status")
            or payload.get("status")
            or ("guidance" if evidence.get("guidance_only") is True else "decision")
        ).strip()
        return cls(
            concern=str(payload.get("concern") or payload.get("area") or "").strip(),
            decision=str(payload.get("decision") or payload.get("selected") or "").strip(),
            selected_libraries=_string_tuple(
                payload.get("selected_libraries") or payload.get("libraries") or payload.get("technologies")
            ),
            options_considered=_string_tuple(
                payload.get("options_considered") or payload.get("options") or payload.get("alternatives")
            ),
            rationale=str(payload.get("rationale") or payload.get("reason") or "").strip(),
            constraints=_string_tuple(payload.get("constraints")),
            risk_level=str(payload.get("risk_level") or "medium").strip(),
            evidence=evidence,
            decision_status=decision_status,
            source=str(payload.get("source") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe architecture decision payload."""

        return {
            "schema_version": "chief_engineer.architecture_decision.v1",
            "concern": self.concern,
            "decision": self.decision,
            "selected_libraries": list(self.selected_libraries),
            "options_considered": list(self.options_considered),
            "rationale": self.rationale,
            "constraints": list(self.constraints),
            "risk_level": self.risk_level,
            "evidence": _json_safe_mapping(self.evidence),
            "decision_status": self.decision_status,
            "source": self.source,
        }


@dataclass(frozen=True)
class TaskBlueprintResultV1:
    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    blueprint_hash: str = ""
    summary: str = ""
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    # D-05: Rich blueprint fields for Director context injection
    target_files: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    execution_checklist: tuple[str, ...] = field(default_factory=tuple)
    scope_paths: tuple[str, ...] = field(default_factory=tuple)
    objective: str = ""
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    architecture_decisions: tuple[ArchitectureDecisionV1, ...] = field(default_factory=tuple)
    selected_libraries: tuple[str, ...] = field(default_factory=tuple)
    # Existing target file export summaries so downstream Director tasks
    # (e.g. test generation) know the actual API of files created by earlier tasks.
    existing_target_files: tuple[dict[str, str], ...] = field(default_factory=tuple)
    module_interface_contract: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if self.blueprint_id is not None:
            object.__setattr__(self, "blueprint_id", str(self.blueprint_id).strip() or None)
        object.__setattr__(self, "blueprint_hash", str(self.blueprint_hash or "").strip())
        object.__setattr__(self, "recommendations", tuple(str(v) for v in self.recommendations))
        object.__setattr__(self, "risks", tuple(str(v) for v in self.risks))
        object.__setattr__(self, "target_files", tuple(str(v) for v in self.target_files))
        object.__setattr__(self, "acceptance_criteria", tuple(str(v) for v in self.acceptance_criteria))
        object.__setattr__(self, "execution_checklist", tuple(str(v) for v in self.execution_checklist))
        object.__setattr__(self, "scope_paths", tuple(str(v) for v in self.scope_paths))
        object.__setattr__(self, "objective", str(self.objective or "").strip())
        object.__setattr__(self, "dependencies", tuple(str(v) for v in self.dependencies))
        decisions: list[ArchitectureDecisionV1] = []
        for item in self.architecture_decisions:
            if isinstance(item, ArchitectureDecisionV1):
                decisions.append(item)
            elif isinstance(item, Mapping):
                decisions.append(ArchitectureDecisionV1.from_mapping(item))
        object.__setattr__(self, "architecture_decisions", tuple(decisions))
        object.__setattr__(self, "selected_libraries", _string_tuple(self.selected_libraries))
        object.__setattr__(
            self,
            "existing_target_files",
            tuple(dict(item) for item in self.existing_target_files if isinstance(item, Mapping)),
        )
        object.__setattr__(self, "module_interface_contract", _to_dict_copy(self.module_interface_contract))


@dataclass(frozen=True)
class TaskBlueprintProvenanceSnapshotV1:
    """Pure provenance verdict for one exact Chief Engineer blueprint payload."""

    logical_path: str
    factory_run_id: str
    task_id: str
    blueprint_id: str
    embedded_blueprint_hash: str
    recomputed_blueprint_hash: str
    matches: bool
    pm_contract_hash: str
    recomputed_pm_contract_hash: str
    pm_task_canonical_hash: str
    target_files: tuple[str, ...] = field(default_factory=tuple)
    blueprint_schema_version: str = "chief_engineer.blueprint.v1"
    schema_version: str = "chief_engineer.blueprint_provenance.v1"
    hash_scheme: str = "chief_engineer.blueprint_hash.v1"

    def __post_init__(self) -> None:
        logical_path = _require_provenance_path("logical_path", self.logical_path)
        factory_run_id = _require_provenance_identity("factory_run_id", self.factory_run_id)
        task_id = _require_provenance_identity("task_id", self.task_id)
        blueprint_id = _require_provenance_blueprint_id("blueprint_id", self.blueprint_id)
        if logical_path != f"runtime/blueprints/{blueprint_id}.json":
            raise ValueError("logical_path must match blueprint_id")
        object.__setattr__(self, "logical_path", logical_path)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "blueprint_id", blueprint_id)
        for field_name in (
            "embedded_blueprint_hash",
            "recomputed_blueprint_hash",
            "pm_contract_hash",
            "recomputed_pm_contract_hash",
            "pm_task_canonical_hash",
        ):
            object.__setattr__(self, field_name, _require_provenance_sha256(field_name, getattr(self, field_name)))
        if type(self.matches) is not bool:
            raise TypeError("matches must be a bool")
        if self.blueprint_schema_version != _PROVENANCE_BLUEPRINT_SCHEMA:
            raise ValueError(f"blueprint_schema_version must equal {_PROVENANCE_BLUEPRINT_SCHEMA!r}")
        if self.schema_version != _PROVENANCE_SNAPSHOT_SCHEMA:
            raise ValueError(f"schema_version must equal {_PROVENANCE_SNAPSHOT_SCHEMA!r}")
        if self.hash_scheme != _PROVENANCE_HASH_SCHEME:
            raise ValueError(f"hash_scheme must equal {_PROVENANCE_HASH_SCHEME!r}")
        object.__setattr__(
            self,
            "target_files",
            _strict_provenance_target_paths("target_files", self.target_files, require_list=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "blueprint_schema_version": self.blueprint_schema_version,
            "hash_scheme": self.hash_scheme,
            "logical_path": self.logical_path,
            "factory_run_id": self.factory_run_id,
            "task_id": self.task_id,
            "blueprint_id": self.blueprint_id,
            "embedded_blueprint_hash": self.embedded_blueprint_hash,
            "recomputed_blueprint_hash": self.recomputed_blueprint_hash,
            "matches": self.matches,
            "pm_contract_hash": self.pm_contract_hash,
            "recomputed_pm_contract_hash": self.recomputed_pm_contract_hash,
            "pm_task_canonical_hash": self.pm_task_canonical_hash,
            "target_files": list(self.target_files),
        }


class ChiefEngineerBlueprintErrorV1(RuntimeError):  # noqa: N818
    """Raised when `chief_engineer.blueprint` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "chief_engineer_blueprint_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Public v1 alias retained for import stability; external consumers may still use the unversioned name.
ChiefEngineerBlueprintError = ChiefEngineerBlueprintErrorV1
