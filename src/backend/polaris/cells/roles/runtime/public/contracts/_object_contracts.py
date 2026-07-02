"""Role-object composition contracts for roles.runtime.

The cohesive role-object composition core: the refs/ports/bindings/
envelope dataclasses and the ``RoleRuntimeObject`` composition contract,
plus ``RoleRuntimeObjectSpec`` (the reusable composition spec) and its
private composition helpers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.roles.runtime.public.contracts._validation import (
    _asset_ref_namespace_matches_owner,
    _capability_endpoint_matches_owner,
    _has_any_ref_namespace,
    _has_ref_namespace,
    _is_forbidden_role_object_owner_cell,
    _is_forbidden_role_object_ref_namespace,
    _is_hex_sha256,
    _is_retired_task_market_task_ref,
    _is_roles_profile_ref_namespace,
    _is_task_market_task_ref,
    _normalize_optional_string,
    _normalize_string_tuple,
    _normalize_unique_string_tuple,
    _path_is_within_scope,
    _require_non_empty,
    _require_ref_superset,
    _require_refs_namespace,
    _to_dict_copy,
)


@dataclass(frozen=True)
class RoleIdentity:
    """Stable identity for one instantiated role runtime object."""

    role_id: str
    run_id: str | None
    task_id: str | None
    session_id: str | None
    workspace: str
    host_kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "run_id", _normalize_optional_string(self.run_id))
        object.__setattr__(self, "task_id", _normalize_optional_string(self.task_id))
        object.__setattr__(self, "session_id", _normalize_optional_string(self.session_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "host_kind", _require_non_empty("host_kind", self.host_kind))


@dataclass(frozen=True)
class RoleProfileBinding:
    """Binding to `roles.profile` state without copying profile-owned truth."""

    role_id: str
    profile_ref: str
    tool_policy_ref: str
    prompt_policy_ref: str
    data_policy_ref: str
    profile_fingerprint: str
    owner_cell: str = "roles.profile"

    def __post_init__(self) -> None:
        role_id = _require_non_empty("role_id", self.role_id)
        profile_ref = _require_non_empty("profile_ref", self.profile_ref)
        tool_policy_ref = _require_non_empty("tool_policy_ref", self.tool_policy_ref)
        prompt_policy_ref = _require_non_empty("prompt_policy_ref", self.prompt_policy_ref)
        data_policy_ref = _require_non_empty("data_policy_ref", self.data_policy_ref)
        profile_fingerprint = _require_non_empty("profile_fingerprint", self.profile_fingerprint)
        owner_cell = _require_non_empty("owner_cell", self.owner_cell)

        if owner_cell != "roles.profile":
            raise ValueError("profile binding owner_cell must be roles.profile")
        refs = (profile_ref, tool_policy_ref, prompt_policy_ref, data_policy_ref)
        if any(not _is_roles_profile_ref_namespace(ref) for ref in refs):
            raise ValueError("profile binding refs must point to roles.profile")

        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "profile_ref", profile_ref)
        object.__setattr__(self, "tool_policy_ref", tool_policy_ref)
        object.__setattr__(self, "prompt_policy_ref", prompt_policy_ref)
        object.__setattr__(self, "data_policy_ref", data_policy_ref)
        object.__setattr__(self, "profile_fingerprint", profile_fingerprint)
        object.__setattr__(self, "owner_cell", owner_cell)


@dataclass(frozen=True)
class RoleAssetRef:
    """Reference to an asset owned by another Cell public boundary."""

    asset_id: str
    owner_cell: str
    contract_name: str
    ref: str
    asset_kind: str = "asset"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _require_non_empty("asset_id", self.asset_id))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(self, "contract_name", _require_non_empty("contract_name", self.contract_name))
        object.__setattr__(self, "ref", _require_non_empty("ref", self.ref))
        object.__setattr__(self, "asset_kind", _require_non_empty("asset_kind", self.asset_kind))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleAssetMount:
    """One named mount in a runtime object asset table."""

    mount_name: str
    asset_ref: RoleAssetRef
    access_mode: str = "read"
    required: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_name", _require_non_empty("mount_name", self.mount_name))
        if not isinstance(self.asset_ref, RoleAssetRef):
            raise TypeError("asset_ref must be a RoleAssetRef")
        access_mode = _require_non_empty("access_mode", self.access_mode).lower()
        if access_mode not in {"read", "write", "read_write", "execute"}:
            raise ValueError("access_mode must be one of: read, write, read_write, execute")
        object.__setattr__(self, "access_mode", access_mode)
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleAssetMountTable:
    """Immutable table of mounted asset references for one role object."""

    mounts: tuple[RoleAssetMount, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        normalized = tuple(self.mounts)
        seen: set[str] = set()
        for mount in normalized:
            if not isinstance(mount, RoleAssetMount):
                raise TypeError("mounts entries must be RoleAssetMount instances")
            owner_cell = mount.asset_ref.owner_cell
            if _is_forbidden_role_object_owner_cell(owner_cell):
                raise ValueError(
                    f"asset mount {mount.mount_name!r} must be owned by a business or platform state Cell; "
                    f"got {owner_cell!r}"
                )
            asset_ref = mount.asset_ref.ref
            if _is_forbidden_role_object_ref_namespace(asset_ref):
                raise ValueError(
                    f"asset mount {mount.mount_name!r} asset ref must point to the real owner Cell; got {asset_ref!r}"
                )
            if not _asset_ref_namespace_matches_owner(
                owner_cell=owner_cell,
                ref=asset_ref,
                asset_kind=mount.asset_ref.asset_kind,
                metadata=mount.asset_ref.metadata,
            ):
                raise ValueError(
                    f"asset mount {mount.mount_name!r} asset ref namespace must match owner_cell {owner_cell!r}; "
                    f"got {asset_ref!r}"
                )
            if (
                (mount.mount_name == "DiffMapArchive" or mount.asset_ref.asset_kind == "diff_map_archive")
                and bool(mount.asset_ref.metadata.get("requires_blueprint_ref"))
                and not {"blueprint_id", "path", "ref"}.issubset(mount.asset_ref.metadata)
            ):
                raise ValueError("DiffMapArchive asset mount must include blueprint_id, path, and ref metadata")
            if mount.mount_name == "OpenLoopRegistry" or mount.asset_ref.asset_kind == "open_loop_registry":
                evidence_ref = str(mount.asset_ref.metadata.get("evidence_ref") or "").strip()
                if mount.asset_ref.metadata.get("evidence_owner_cell") != "audit.evidence" or not _has_ref_namespace(
                    evidence_ref,
                    "audit.evidence",
                ):
                    raise ValueError("OpenLoopRegistry asset mount must include audit.evidence evidence_ref metadata")
            if mount.mount_name == "TruthLog" or mount.asset_ref.asset_kind == "truth_log":
                runtime_receipt_ref = str(mount.asset_ref.metadata.get("runtime_receipt_ref") or "").strip()
                if mount.asset_ref.metadata.get("runtime_receipt_owner_cell") != "factory.cognitive_runtime" or not (
                    _has_ref_namespace(runtime_receipt_ref, "factory.cognitive_runtime")
                ):
                    raise ValueError(
                        "TruthLog asset mount must include factory.cognitive_runtime runtime_receipt_ref metadata"
                    )
            key = mount.mount_name
            if key in seen:
                raise ValueError(f"duplicate asset mount: {key}")
            seen.add(key)
        object.__setattr__(self, "mounts", normalized)

    def get(self, mount_name: str) -> RoleAssetMount:
        key = _require_non_empty("mount_name", mount_name)
        for mount in self.mounts:
            if mount.mount_name == key:
                return mount
        raise KeyError(key)


@dataclass(frozen=True)
class RoleCapabilityDescriptor:
    """Public-contract port descriptor for a role-owned capability call."""

    capability_id: str
    owner_cell: str
    contract_name: str
    effect: str
    allowed_roles: tuple[str, ...] = field(default_factory=tuple)
    endpoint_ref: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _require_non_empty("capability_id", self.capability_id))
        owner_cell = _require_non_empty("owner_cell", self.owner_cell)
        if _is_forbidden_role_object_owner_cell(owner_cell):
            raise ValueError(
                "capability owner_cell must be a target public Cell, not a role runtime/kernel/profile/session owner"
            )
        object.__setattr__(self, "owner_cell", owner_cell)
        object.__setattr__(self, "contract_name", _require_non_empty("contract_name", self.contract_name))
        object.__setattr__(self, "effect", _require_non_empty("effect", self.effect))
        object.__setattr__(self, "allowed_roles", _normalize_string_tuple("allowed_roles", self.allowed_roles))
        endpoint_ref = str(self.endpoint_ref or "").strip()
        if not _capability_endpoint_matches_owner(owner_cell, endpoint_ref):
            raise ValueError("endpoint_ref must point to owner_cell public contract")
        object.__setattr__(self, "endpoint_ref", endpoint_ref)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleCapabilityPorts:
    """Immutable capability port table for one role object."""

    capabilities: tuple[RoleCapabilityDescriptor, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        normalized = tuple(self.capabilities)
        seen: set[str] = set()
        for capability in normalized:
            if not isinstance(capability, RoleCapabilityDescriptor):
                raise TypeError("capabilities entries must be RoleCapabilityDescriptor instances")
            owner_cell = capability.owner_cell
            if _is_forbidden_role_object_owner_cell(owner_cell):
                raise ValueError(
                    f"capability {capability.capability_id!r} must be owned by a target public Cell; got {owner_cell!r}"
                )
            if not capability.allowed_roles:
                raise ValueError(f"capability {capability.capability_id!r} must declare allowed_roles")
            key = capability.capability_id
            if key in seen:
                raise ValueError(f"duplicate capability: {key}")
            seen.add(key)
        object.__setattr__(self, "capabilities", normalized)

    def get(self, capability_id: str) -> RoleCapabilityDescriptor:
        key = _require_non_empty("capability_id", capability_id)
        for capability in self.capabilities:
            if capability.capability_id == key:
                return capability
        raise KeyError(key)


def _require_capability_ports_allow_role(role_id: str, capability_ports: RoleCapabilityPorts) -> None:
    for capability in capability_ports.capabilities:
        if role_id not in capability.allowed_roles:
            raise ValueError(f"capability {capability.capability_id!r} is not allowed for role {role_id!r}")


def _compute_role_capability_fingerprint(
    *,
    role_id: str,
    capability_id: str,
    effect: str,
    tool: str,
    policy_fingerprint: str,
    profile_fingerprint: str,
) -> str:
    content = "\n".join(
        (
            role_id,
            capability_id,
            effect,
            tool,
            policy_fingerprint,
            profile_fingerprint,
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RoleCapabilityFingerprint:
    """Auditable fingerprint for role + capability + effect + tool + policy."""

    role_id: str
    capability_id: str
    effect: str
    tool: str
    policy_fingerprint: str
    profile_fingerprint: str
    fingerprint: str = ""

    def __post_init__(self) -> None:
        role_id = _require_non_empty("role_id", self.role_id)
        capability_id = _require_non_empty("capability_id", self.capability_id)
        effect = _require_non_empty("effect", self.effect)
        tool = _require_non_empty("tool", self.tool)
        policy_fingerprint = _require_non_empty("policy_fingerprint", self.policy_fingerprint)
        profile_fingerprint = _require_non_empty("profile_fingerprint", self.profile_fingerprint)
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "policy_fingerprint", policy_fingerprint)
        object.__setattr__(self, "profile_fingerprint", profile_fingerprint)
        expected_fingerprint = _compute_role_capability_fingerprint(
            role_id=role_id,
            capability_id=capability_id,
            effect=effect,
            tool=tool,
            policy_fingerprint=policy_fingerprint,
            profile_fingerprint=profile_fingerprint,
        )
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", expected_fingerprint)
        else:
            fingerprint = _require_non_empty("fingerprint", self.fingerprint)
            if not _is_hex_sha256(fingerprint):
                raise ValueError("fingerprint must be a 64-character hex capability fingerprint")
            if fingerprint != expected_fingerprint:
                raise ValueError("fingerprint must match role, capability, effect, tool, policy, and profile fields")
            object.__setattr__(self, "fingerprint", fingerprint)


@dataclass(frozen=True)
class RoleCapabilityInvocation:
    """One capability call request, carrying refs instead of payload ownership."""

    invocation_id: str
    capability_id: str
    role_id: str
    command_contract: str
    payload_ref: str
    fingerprint_ref: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        invocation_id = _require_non_empty("invocation_id", self.invocation_id)
        capability_id = _require_non_empty("capability_id", self.capability_id)
        role_id = _require_non_empty("role_id", self.role_id)
        command_contract = _require_non_empty("command_contract", self.command_contract)
        payload_ref = _require_non_empty("payload_ref", self.payload_ref)
        fingerprint_ref = _require_non_empty("fingerprint_ref", self.fingerprint_ref)

        if not _has_any_ref_namespace(payload_ref, ("roles.runtime", "runtime.task_market")):
            raise ValueError("payload_ref must point to roles.runtime or runtime.task_market")
        if not _is_hex_sha256(fingerprint_ref):
            raise ValueError("fingerprint_ref must be a 64-character hex capability fingerprint")

        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "command_contract", command_contract)
        object.__setattr__(self, "payload_ref", payload_ref)
        object.__setattr__(self, "fingerprint_ref", fingerprint_ref)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleCapabilityDecision:
    """Structured sandbox decision for one capability invocation."""

    invocation_id: str
    capability_id: str
    role_id: str
    allowed: bool
    reason: str = ""
    denial_code: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "invocation_id", _require_non_empty("invocation_id", self.invocation_id))
        object.__setattr__(self, "capability_id", _require_non_empty("capability_id", self.capability_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "denial_code", _normalize_optional_string(self.denial_code))
        evidence_refs = _normalize_string_tuple("evidence_refs", self.evidence_refs)
        _require_refs_namespace("evidence_refs", evidence_refs, "audit.evidence")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        if not self.allowed and not (self.reason or self.denial_code):
            raise ValueError("denied capability decision must include reason or denial_code")


@dataclass(frozen=True)
class RoleTurnContext:
    """Typed turn context refs for current input, context, handoff, and task state."""

    typed_input_ref: str
    context_snapshot_ref: str
    handoff_refs: tuple[str, ...] = field(default_factory=tuple)
    task_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        typed_input_ref = _require_non_empty("typed_input_ref", self.typed_input_ref)
        context_snapshot_ref = _require_non_empty("context_snapshot_ref", self.context_snapshot_ref)
        handoff_refs = _normalize_string_tuple("handoff_refs", self.handoff_refs)
        task_refs = _normalize_string_tuple("task_refs", self.task_refs)

        if not _has_any_ref_namespace(typed_input_ref, ("roles.runtime", "runtime.task_market")):
            raise ValueError("typed_input_ref must point to roles.runtime or runtime.task_market")
        if not _has_any_ref_namespace(context_snapshot_ref, ("context.engine", "roles.session")):
            raise ValueError("context_snapshot_ref must point to context.engine or roles.session")
        _require_refs_namespace("handoff_refs", handoff_refs, "factory.cognitive_runtime")
        _require_refs_namespace("task_refs", task_refs, "runtime.task_market")
        if any(not _is_task_market_task_ref(ref) for ref in task_refs):
            raise ValueError("task_refs must use runtime.task_market:task:<task_id>")

        object.__setattr__(self, "typed_input_ref", typed_input_ref)
        object.__setattr__(self, "context_snapshot_ref", context_snapshot_ref)
        object.__setattr__(self, "handoff_refs", handoff_refs)
        object.__setattr__(self, "task_refs", task_refs)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleLedgerBinding:
    """Refs to kernel ledger/commit and Cognitive Runtime receipt contracts."""

    turn_ledger_ref: str
    commit_contract: str = "CommitReceipt"
    runtime_receipt_contract: str = "RecordRuntimeReceiptCommandV1"
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    commit_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        turn_ledger_ref = _require_non_empty("turn_ledger_ref", self.turn_ledger_ref)
        commit_contract = _require_non_empty("commit_contract", self.commit_contract)
        runtime_receipt_contract = _require_non_empty(
            "runtime_receipt_contract",
            self.runtime_receipt_contract,
        )
        receipt_refs = _normalize_string_tuple("receipt_refs", self.receipt_refs)
        commit_receipt_ref = _normalize_optional_string(self.commit_receipt_ref)

        if not _has_ref_namespace(turn_ledger_ref, "roles.kernel"):
            raise ValueError("turn_ledger_ref must point to roles.kernel")
        if commit_receipt_ref and not _has_ref_namespace(commit_receipt_ref, "roles.kernel"):
            raise ValueError("commit_receipt_ref must point to roles.kernel")
        if any(not _has_ref_namespace(ref, "factory.cognitive_runtime") for ref in receipt_refs):
            raise ValueError("receipt_refs must point to factory.cognitive_runtime")

        object.__setattr__(self, "turn_ledger_ref", turn_ledger_ref)
        object.__setattr__(self, "commit_contract", commit_contract)
        object.__setattr__(self, "runtime_receipt_contract", runtime_receipt_contract)
        object.__setattr__(self, "receipt_refs", receipt_refs)
        object.__setattr__(self, "commit_receipt_ref", commit_receipt_ref)


@dataclass(frozen=True)
class RoleTaskMarketBinding:
    """Public task-market lifecycle contract names and active work refs."""

    publish_contract: str = "PublishTaskWorkItemCommandV1"
    claim_contract: str = "ClaimTaskWorkItemCommandV1"
    lease_contract: str = "RenewTaskLeaseCommandV1"
    ack_contract: str = "AcknowledgeTaskStageCommandV1"
    fail_contract: str = "FailTaskStageCommandV1"
    requeue_contract: str = "RequeueTaskCommandV1"
    dead_letter_contract: str = "MoveTaskToDeadLetterCommandV1"
    work_item_ref: str | None = None
    lease_token_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "publish_contract",
            "claim_contract",
            "lease_contract",
            "ack_contract",
            "fail_contract",
            "requeue_contract",
            "dead_letter_contract",
        ):
            object.__setattr__(self, name, _require_non_empty(name, getattr(self, name)))
        work_item_ref = _normalize_optional_string(self.work_item_ref)
        lease_token_ref = _normalize_optional_string(self.lease_token_ref)
        active_refs = tuple(ref for ref in (work_item_ref, lease_token_ref) if ref)
        if any(not _has_ref_namespace(ref, "runtime.task_market") for ref in active_refs):
            raise ValueError("task-market binding refs must point to runtime.task_market")
        if _is_retired_task_market_task_ref(work_item_ref):
            raise ValueError("work_item_ref active task refs must use runtime.task_market:task:<task_id>")
        object.__setattr__(self, "work_item_ref", work_item_ref)
        object.__setattr__(self, "lease_token_ref", lease_token_ref)


@dataclass(frozen=True)
class RoleTurnEnvelope:
    """Single typed turn envelope consumed by runtime/kernel boundaries."""

    identity: RoleIdentity
    profile_binding: RoleProfileBinding
    turn_context: RoleTurnContext
    capability_invocations: tuple[RoleCapabilityInvocation, ...]
    ledger_binding: RoleLedgerBinding
    task_market_binding: RoleTaskMarketBinding
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RoleIdentity):
            raise TypeError("identity must be a RoleIdentity")
        if not isinstance(self.profile_binding, RoleProfileBinding):
            raise TypeError("profile_binding must be a RoleProfileBinding")
        if self.identity.role_id != self.profile_binding.role_id:
            raise ValueError("identity.role_id must match profile_binding.role_id")
        if not isinstance(self.turn_context, RoleTurnContext):
            raise TypeError("turn_context must be a RoleTurnContext")
        invocations = tuple(self.capability_invocations)
        if any(not isinstance(item, RoleCapabilityInvocation) for item in invocations):
            raise TypeError("capability_invocations entries must be RoleCapabilityInvocation instances")
        allowed_payload_refs = (self.turn_context.typed_input_ref, *self.turn_context.task_refs)
        seen_invocation_ids: set[str] = set()
        for invocation in invocations:
            if invocation.invocation_id in seen_invocation_ids:
                raise ValueError(f"duplicate capability invocation: {invocation.invocation_id}")
            seen_invocation_ids.add(invocation.invocation_id)
            if invocation.role_id != self.identity.role_id:
                raise ValueError("capability_invocations role_id must match identity.role_id")
            if invocation.payload_ref not in allowed_payload_refs:
                raise ValueError("capability_invocations payload_ref must match turn_context")
        if not isinstance(self.ledger_binding, RoleLedgerBinding):
            raise TypeError("ledger_binding must be a RoleLedgerBinding")
        if not isinstance(self.task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        work_item_ref = self.task_market_binding.work_item_ref
        if (
            work_item_ref
            and _is_task_market_task_ref(work_item_ref)
            and work_item_ref not in self.turn_context.task_refs
        ):
            raise ValueError("task_market_binding.work_item_ref must be listed in turn_context.task_refs")
        object.__setattr__(self, "capability_invocations", invocations)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleStateCommitRequest:
    """Request to commit role-object turn state through existing kernel contracts."""

    request_id: str
    envelope: RoleTurnEnvelope
    changed_asset_refs: tuple[str, ...]
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    allowed_scope_paths: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    require_change_validation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_non_empty("request_id", self.request_id))
        if not isinstance(self.envelope, RoleTurnEnvelope):
            raise TypeError("envelope must be a RoleTurnEnvelope")
        changed_asset_refs = _normalize_string_tuple("changed_asset_refs", self.changed_asset_refs)
        changed_files = _normalize_string_tuple("changed_files", self.changed_files)
        allowed_scope_paths = _normalize_string_tuple("allowed_scope_paths", self.allowed_scope_paths)
        evidence_refs = _normalize_string_tuple("evidence_refs", self.evidence_refs)
        if not (changed_asset_refs or changed_files or evidence_refs):
            raise ValueError("role state commit must include changed_asset_refs, changed_files, or evidence_refs")
        if any(ref not in self.envelope.turn_context.task_refs for ref in changed_asset_refs):
            raise ValueError("changed_asset_refs must be listed in turn_context task refs")
        _require_refs_namespace("evidence_refs", evidence_refs, "audit.evidence")
        if changed_files and not allowed_scope_paths:
            raise ValueError("allowed_scope_paths must be provided when changed_files are present")
        if any(not _path_is_within_scope(path, allowed_scope_paths) for path in changed_files):
            raise ValueError("changed_files must be within allowed_scope_paths")
        object.__setattr__(self, "changed_asset_refs", changed_asset_refs)
        object.__setattr__(self, "changed_files", changed_files)
        object.__setattr__(self, "allowed_scope_paths", allowed_scope_paths)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "require_change_validation", bool(self.require_change_validation))


@dataclass(frozen=True)
class RoleStateCommitReceipt:
    """Receipt refs produced after role state commit reaches kernel/runtime stores."""

    request_id: str
    ok: bool
    commit_receipt_ref: str | None = None
    change_set_validation_ref: str | None = None
    runtime_receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    handoff_pack_refs: tuple[str, ...] = field(default_factory=tuple)
    turn_outcome_ref: str | None = None
    commit_contract: str = "CommitReceipt"
    runtime_receipt_contract: str = "RecordRuntimeReceiptCommandV1"
    status: str = "committed"
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_non_empty("request_id", self.request_id))
        object.__setattr__(self, "ok", bool(self.ok))
        commit_receipt_ref = _normalize_optional_string(self.commit_receipt_ref)
        change_set_validation_ref = _normalize_optional_string(self.change_set_validation_ref)
        runtime_receipt_refs = _normalize_unique_string_tuple(
            "runtime_receipt_refs",
            self.runtime_receipt_refs,
        )
        handoff_pack_refs = _normalize_unique_string_tuple("handoff_pack_refs", self.handoff_pack_refs)
        turn_outcome_ref = _normalize_optional_string(self.turn_outcome_ref)

        if commit_receipt_ref and not _has_ref_namespace(commit_receipt_ref, "roles.kernel"):
            raise ValueError("commit_receipt_ref must point to roles.kernel")
        if change_set_validation_ref and not _has_ref_namespace(
            change_set_validation_ref,
            "factory.cognitive_runtime",
        ):
            raise ValueError("change_set_validation_ref must point to factory.cognitive_runtime")
        if any(not _has_ref_namespace(ref, "factory.cognitive_runtime") for ref in runtime_receipt_refs):
            raise ValueError("runtime_receipt_refs must point to factory.cognitive_runtime")
        if any(not _has_ref_namespace(ref, "factory.cognitive_runtime") for ref in handoff_pack_refs):
            raise ValueError("handoff_pack_refs must point to factory.cognitive_runtime")
        if turn_outcome_ref and not _has_ref_namespace(turn_outcome_ref, "roles.kernel"):
            raise ValueError("turn_outcome_ref must point to roles.kernel")

        object.__setattr__(self, "commit_receipt_ref", commit_receipt_ref)
        object.__setattr__(self, "change_set_validation_ref", change_set_validation_ref)
        object.__setattr__(self, "runtime_receipt_refs", runtime_receipt_refs)
        object.__setattr__(self, "handoff_pack_refs", handoff_pack_refs)
        object.__setattr__(self, "turn_outcome_ref", turn_outcome_ref)
        object.__setattr__(self, "commit_contract", _require_non_empty("commit_contract", self.commit_contract))
        object.__setattr__(
            self,
            "runtime_receipt_contract",
            _require_non_empty("runtime_receipt_contract", self.runtime_receipt_contract),
        )
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "error_code", _normalize_optional_string(self.error_code))
        object.__setattr__(self, "error_message", _normalize_optional_string(self.error_message))
        if self.ok and not self.commit_receipt_ref:
            raise ValueError("successful commit receipt must include commit_receipt_ref")
        if self.ok and not self.runtime_receipt_refs:
            raise ValueError("successful commit receipt must include runtime_receipt_refs")
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed commit receipt must include error_code or error_message")


@dataclass(frozen=True)
class RehydrateRoleHandoffCommandV1:
    """Request to rehydrate a typed handoff pack through Cognitive Runtime."""

    identity: RoleIdentity
    handoff_ref: str
    target_role: str
    turn_context: RoleTurnContext
    target_session_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RoleIdentity):
            raise TypeError("identity must be a RoleIdentity")
        if not isinstance(self.turn_context, RoleTurnContext):
            raise TypeError("turn_context must be a RoleTurnContext")
        handoff_ref = _require_non_empty("handoff_ref", self.handoff_ref)
        if not _has_ref_namespace(handoff_ref, "factory.cognitive_runtime"):
            raise ValueError("handoff_ref must point to factory.cognitive_runtime")
        if handoff_ref not in self.turn_context.handoff_refs:
            raise ValueError("handoff_ref must be listed in turn_context.handoff_refs")
        object.__setattr__(self, "handoff_ref", handoff_ref)
        object.__setattr__(self, "target_role", _require_non_empty("target_role", self.target_role))
        target_session_id = _normalize_optional_string(self.target_session_id)
        object.__setattr__(self, "target_session_id", target_session_id or None)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleHandoffRehydrationResultV1:
    """Refs-only result of rehydrating a Cognitive Runtime handoff pack."""

    ok: bool
    handoff_ref: str
    target_role: str
    rehydration_ref: str | None = None
    target_session_id: str | None = None
    context_override: Mapping[str, Any] = field(default_factory=dict)
    metadata_patch: Mapping[str, Any] = field(default_factory=dict)
    runtime_receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    episode_refs: tuple[str, ...] = field(default_factory=tuple)
    source_spans: tuple[str, ...] = field(default_factory=tuple)
    status: str = "rehydrated"
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        handoff_ref = _require_non_empty("handoff_ref", self.handoff_ref)
        if not _has_ref_namespace(handoff_ref, "factory.cognitive_runtime"):
            raise ValueError("handoff_ref must point to factory.cognitive_runtime")
        rehydration_ref = _normalize_optional_string(self.rehydration_ref)
        if rehydration_ref and not _has_ref_namespace(rehydration_ref, "factory.cognitive_runtime"):
            raise ValueError("rehydration_ref must point to factory.cognitive_runtime")
        runtime_receipt_refs = _normalize_unique_string_tuple("runtime_receipt_refs", self.runtime_receipt_refs)
        artifact_refs = _normalize_unique_string_tuple("artifact_refs", self.artifact_refs)
        episode_refs = _normalize_unique_string_tuple("episode_refs", self.episode_refs)
        source_spans = _normalize_unique_string_tuple("source_spans", self.source_spans)
        _require_refs_namespace("runtime_receipt_refs", runtime_receipt_refs, "factory.cognitive_runtime")
        _require_refs_namespace("artifact_refs", artifact_refs, "roles.session")
        _require_refs_namespace("episode_refs", episode_refs, "roles.session")

        object.__setattr__(self, "handoff_ref", handoff_ref)
        object.__setattr__(self, "target_role", _require_non_empty("target_role", self.target_role))
        target_session_id = _normalize_optional_string(self.target_session_id)
        object.__setattr__(self, "target_session_id", target_session_id or None)
        object.__setattr__(self, "rehydration_ref", rehydration_ref)
        object.__setattr__(self, "context_override", _to_dict_copy(self.context_override))
        object.__setattr__(self, "metadata_patch", _to_dict_copy(self.metadata_patch))
        object.__setattr__(self, "runtime_receipt_refs", runtime_receipt_refs)
        object.__setattr__(self, "artifact_refs", artifact_refs)
        object.__setattr__(self, "episode_refs", episode_refs)
        object.__setattr__(self, "source_spans", source_spans)
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "error_code", _normalize_optional_string(self.error_code))
        object.__setattr__(self, "error_message", _normalize_optional_string(self.error_message))
        if self.ok and not self.rehydration_ref:
            raise ValueError("successful handoff rehydration result must include rehydration_ref")
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed handoff rehydration result must include error_code or error_message")


@dataclass(frozen=True)
class RoleRuntimeChainStepRef:
    """One refs-only step in a multi-role runtime execution chain."""

    role_id: str
    stage: str
    capability_id: str
    capability_fingerprint_ref: str
    owner_cell: str
    command_contract: str
    result_ref: str
    task_ref: str | None = None
    work_item_ref: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    handoff_refs: tuple[str, ...] = field(default_factory=tuple)
    status: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        role_id = _require_non_empty("role_id", self.role_id)
        stage = _require_non_empty("stage", self.stage)
        capability_id = _require_non_empty("capability_id", self.capability_id)
        capability_fingerprint_ref = _require_non_empty(
            "capability_fingerprint_ref",
            self.capability_fingerprint_ref,
        )
        if not _has_ref_namespace(capability_fingerprint_ref, "roles.runtime"):
            raise ValueError("capability_fingerprint_ref must point to roles.runtime")
        owner_cell = _require_non_empty("owner_cell", self.owner_cell)
        if _is_forbidden_role_object_owner_cell(owner_cell):
            raise ValueError(
                "chain step owner_cell must be a target public Cell, not a role runtime/kernel/profile/session owner"
            )
        command_contract = _require_non_empty("command_contract", self.command_contract)
        result_ref = _require_non_empty("result_ref", self.result_ref)
        if not _has_ref_namespace(result_ref, owner_cell):
            raise ValueError("result_ref must point to owner_cell")
        task_ref = _normalize_optional_string(self.task_ref)
        work_item_ref = _normalize_optional_string(self.work_item_ref)
        if not (task_ref or work_item_ref):
            raise ValueError("chain step must include task_ref or work_item_ref")
        if task_ref and not _is_task_market_task_ref(task_ref):
            raise ValueError("task_ref must use runtime.task_market:task:<task_id>")
        if _is_retired_task_market_task_ref(work_item_ref):
            raise ValueError("work_item_ref active task refs must use runtime.task_market:task:<task_id>")
        _require_refs_namespace(
            "chain step task/work item refs",
            tuple(ref for ref in (task_ref, work_item_ref) if ref),
            "runtime.task_market",
        )
        evidence_refs = _normalize_string_tuple("evidence_refs", self.evidence_refs)
        receipt_refs = _normalize_string_tuple("receipt_refs", self.receipt_refs)
        handoff_refs = _normalize_string_tuple("handoff_refs", self.handoff_refs)
        _require_refs_namespace("evidence_refs", evidence_refs, "audit.evidence")
        _require_refs_namespace("receipt_refs", receipt_refs, "factory.cognitive_runtime")
        _require_refs_namespace("handoff_refs", handoff_refs, "factory.cognitive_runtime")

        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "capability_fingerprint_ref", capability_fingerprint_ref)
        object.__setattr__(self, "owner_cell", owner_cell)
        object.__setattr__(self, "command_contract", command_contract)
        object.__setattr__(self, "result_ref", result_ref)
        object.__setattr__(self, "task_ref", task_ref)
        object.__setattr__(self, "work_item_ref", work_item_ref)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "receipt_refs", receipt_refs)
        object.__setattr__(self, "handoff_refs", handoff_refs)
        object.__setattr__(self, "status", str(self.status or "").strip())
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleRuntimeChainEnvelope:
    """Typed refs-only envelope for an audited multi-role execution chain."""

    chain_id: str
    workspace: str
    run_id: str
    task_id: str
    steps: tuple[RoleRuntimeChainStepRef, ...]
    turn_ledger_ref: str
    task_market_refs: tuple[str, ...] = field(default_factory=tuple)
    audit_evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    runtime_projection_refs: tuple[str, ...] = field(default_factory=tuple)
    capability_fingerprint_refs: tuple[str, ...] = field(default_factory=tuple)
    handoff_refs: tuple[str, ...] = field(default_factory=tuple)
    runtime_receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", _require_non_empty("chain_id", self.chain_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        steps = tuple(self.steps)
        if not steps:
            raise ValueError("steps must include at least one RoleRuntimeChainStepRef")
        if any(not isinstance(step, RoleRuntimeChainStepRef) for step in steps):
            raise TypeError("steps entries must be RoleRuntimeChainStepRef instances")
        object.__setattr__(self, "steps", steps)
        turn_ledger_ref = _require_non_empty("turn_ledger_ref", self.turn_ledger_ref)
        if not _has_ref_namespace(turn_ledger_ref, "roles.kernel"):
            raise ValueError("turn_ledger_ref must point to roles.kernel")
        task_market_refs = _normalize_string_tuple("task_market_refs", self.task_market_refs)
        audit_evidence_refs = _normalize_string_tuple("audit_evidence_refs", self.audit_evidence_refs)
        runtime_projection_refs = _normalize_string_tuple(
            "runtime_projection_refs",
            self.runtime_projection_refs,
        )
        capability_fingerprint_refs = _normalize_string_tuple(
            "capability_fingerprint_refs",
            self.capability_fingerprint_refs,
        )
        handoff_refs = _normalize_string_tuple("handoff_refs", self.handoff_refs)
        runtime_receipt_refs = _normalize_string_tuple("runtime_receipt_refs", self.runtime_receipt_refs)

        _require_refs_namespace("task_market_refs", task_market_refs, "runtime.task_market")
        _require_refs_namespace("audit_evidence_refs", audit_evidence_refs, "audit.evidence")
        _require_refs_namespace("runtime_projection_refs", runtime_projection_refs, "runtime.projection")
        _require_refs_namespace("capability_fingerprint_refs", capability_fingerprint_refs, "roles.runtime")
        _require_refs_namespace("handoff_refs", handoff_refs, "factory.cognitive_runtime")
        _require_refs_namespace("runtime_receipt_refs", runtime_receipt_refs, "factory.cognitive_runtime")

        object.__setattr__(self, "turn_ledger_ref", turn_ledger_ref)
        object.__setattr__(self, "task_market_refs", task_market_refs)
        object.__setattr__(self, "audit_evidence_refs", audit_evidence_refs)
        object.__setattr__(self, "runtime_projection_refs", runtime_projection_refs)
        object.__setattr__(self, "capability_fingerprint_refs", capability_fingerprint_refs)
        object.__setattr__(self, "handoff_refs", handoff_refs)
        object.__setattr__(self, "runtime_receipt_refs", runtime_receipt_refs)
        step_task_market_refs = tuple(
            ref
            for step in steps
            for ref in (
                step.task_ref,
                step.work_item_ref,
            )
            if ref
        )
        _require_ref_superset(
            "task_market_refs must include step task/work item refs",
            task_market_refs,
            step_task_market_refs,
        )
        _require_ref_superset(
            "audit_evidence_refs must include step evidence refs",
            audit_evidence_refs,
            tuple(ref for step in steps for ref in step.evidence_refs),
        )
        _require_ref_superset(
            "capability_fingerprint_refs must include step capability fingerprint refs",
            capability_fingerprint_refs,
            tuple(step.capability_fingerprint_ref for step in steps),
        )
        _require_ref_superset(
            "handoff_refs must include step handoff refs",
            handoff_refs,
            tuple(ref for step in steps for ref in step.handoff_refs),
        )
        _require_ref_superset(
            "runtime_receipt_refs must include step receipt refs",
            runtime_receipt_refs,
            tuple(ref for step in steps for ref in step.receipt_refs),
        )
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AssembleRoleRuntimeChainCommandV1:
    """Assemble a refs-only runtime chain envelope from completed role steps."""

    chain_id: str
    workspace: str
    run_id: str
    task_id: str
    steps: tuple[RoleRuntimeChainStepRef, ...]
    turn_ledger_ref: str
    runtime_projection_refs: tuple[str, ...] = field(default_factory=tuple)
    audit_evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    required_roles: tuple[str, ...] = ("pm", "chief_engineer", "director", "qa")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", _require_non_empty("chain_id", self.chain_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        steps = tuple(self.steps)
        if not steps:
            raise ValueError("steps must include at least one RoleRuntimeChainStepRef")
        if any(not isinstance(step, RoleRuntimeChainStepRef) for step in steps):
            raise TypeError("steps entries must be RoleRuntimeChainStepRef instances")
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "turn_ledger_ref", _require_non_empty("turn_ledger_ref", self.turn_ledger_ref))
        object.__setattr__(
            self,
            "runtime_projection_refs",
            _normalize_string_tuple("runtime_projection_refs", self.runtime_projection_refs),
        )
        object.__setattr__(
            self,
            "audit_evidence_refs",
            _normalize_string_tuple("audit_evidence_refs", self.audit_evidence_refs),
        )
        object.__setattr__(self, "required_roles", _normalize_string_tuple("required_roles", self.required_roles))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleRuntimeChainAssemblyResultV1:
    """Result of assembling a refs-only multi-role runtime chain envelope."""

    ok: bool
    chain_ref: str
    chain: RoleRuntimeChainEnvelope | None = None
    missing_roles: tuple[str, ...] = field(default_factory=tuple)
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "chain_ref", _require_non_empty("chain_ref", self.chain_ref))
        if self.chain is not None and not isinstance(self.chain, RoleRuntimeChainEnvelope):
            raise TypeError("chain must be a RoleRuntimeChainEnvelope when provided")
        object.__setattr__(self, "missing_roles", _normalize_string_tuple("missing_roles", self.missing_roles))
        object.__setattr__(self, "error_code", _normalize_optional_string(self.error_code))
        object.__setattr__(self, "error_message", _normalize_optional_string(self.error_message))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.ok and self.chain is None:
            raise ValueError("successful chain assembly must include chain")
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed chain assembly must include error_code or error_message")


@dataclass(frozen=True)
class RoleRuntimeObject:
    """Instantiated role object composed from refs, ports, and bindings only."""

    identity: RoleIdentity
    profile_binding: RoleProfileBinding
    turn_context: RoleTurnContext
    asset_mounts: RoleAssetMountTable
    capability_ports: RoleCapabilityPorts
    ledger_binding: RoleLedgerBinding
    task_market_binding: RoleTaskMarketBinding
    capability_fingerprint: RoleCapabilityFingerprint
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RoleIdentity):
            raise TypeError("identity must be a RoleIdentity")
        if not isinstance(self.profile_binding, RoleProfileBinding):
            raise TypeError("profile_binding must be a RoleProfileBinding")
        if self.identity.role_id != self.profile_binding.role_id:
            raise ValueError("identity.role_id must match profile_binding.role_id")
        if not isinstance(self.turn_context, RoleTurnContext):
            raise TypeError("turn_context must be a RoleTurnContext")
        if not isinstance(self.asset_mounts, RoleAssetMountTable):
            raise TypeError("asset_mounts must be a RoleAssetMountTable")
        if not isinstance(self.capability_ports, RoleCapabilityPorts):
            raise TypeError("capability_ports must be a RoleCapabilityPorts")
        _require_capability_ports_allow_role(self.identity.role_id, self.capability_ports)
        if not isinstance(self.ledger_binding, RoleLedgerBinding):
            raise TypeError("ledger_binding must be a RoleLedgerBinding")
        if not isinstance(self.task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        work_item_ref = self.task_market_binding.work_item_ref
        if (
            work_item_ref
            and _is_task_market_task_ref(work_item_ref)
            and work_item_ref not in self.turn_context.task_refs
        ):
            raise ValueError("task_market_binding.work_item_ref must be listed in turn_context.task_refs")
        if not isinstance(self.capability_fingerprint, RoleCapabilityFingerprint):
            raise TypeError("capability_fingerprint must be a RoleCapabilityFingerprint")
        if self.identity.role_id != self.capability_fingerprint.role_id:
            raise ValueError("identity.role_id must match capability_fingerprint.role_id")
        try:
            mounted_capability = self.capability_ports.get(self.capability_fingerprint.capability_id)
        except KeyError as exc:
            raise ValueError("capability_fingerprint.capability_id must be mounted in capability_ports") from exc
        if self.capability_fingerprint.effect != mounted_capability.effect:
            raise ValueError("capability_fingerprint.effect must match mounted capability effect")
        mounted_tool = (
            mounted_capability.endpoint_ref or f"{mounted_capability.owner_cell}:{mounted_capability.contract_name}"
        )
        if self.capability_fingerprint.tool != mounted_tool:
            raise ValueError("capability_fingerprint.tool must match mounted capability tool")
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


def _derive_role_turn_context(identity: RoleIdentity) -> RoleTurnContext:
    run_token = identity.run_id or "run"
    task_token = identity.task_id or "task"
    session_token = identity.session_id or f"{identity.role_id}:{run_token}"
    task_refs = (f"runtime.task_market:task:{identity.task_id}",) if identity.task_id else ()
    return RoleTurnContext(
        typed_input_ref=f"roles.runtime:typed-input:{identity.role_id}:{run_token}:{task_token}",
        context_snapshot_ref=f"roles.session:context-snapshot:{session_token}",
        task_refs=task_refs,
        metadata={
            "source": "roles.runtime.spec.instantiate",
            "role_id": identity.role_id,
            "run_id": identity.run_id,
            "task_id": identity.task_id,
            "session_id": identity.session_id,
        },
    )


@dataclass(frozen=True)
class InstantiateRoleRuntimeObjectCommandV1:
    """Instantiate one stateful role runtime object from public profile bindings."""

    role_id: str
    workspace: str
    host_kind: str
    turn_ledger_ref: str
    policy_fingerprint: str
    run_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    capability_id: str | None = None
    task_market_binding: RoleTaskMarketBinding | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "host_kind", _require_non_empty("host_kind", self.host_kind))
        object.__setattr__(self, "turn_ledger_ref", _require_non_empty("turn_ledger_ref", self.turn_ledger_ref))
        object.__setattr__(
            self,
            "policy_fingerprint",
            _require_non_empty("policy_fingerprint", self.policy_fingerprint),
        )
        object.__setattr__(self, "run_id", _normalize_optional_string(self.run_id))
        object.__setattr__(self, "task_id", _normalize_optional_string(self.task_id))
        object.__setattr__(self, "session_id", _normalize_optional_string(self.session_id))
        object.__setattr__(self, "capability_id", _normalize_optional_string(self.capability_id))
        if self.task_market_binding is not None and not isinstance(self.task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleRuntimeObjectResultV1:
    """Structured result for role runtime object instantiation."""

    ok: bool
    role_id: str
    runtime_object: RoleRuntimeObject | None = None
    profile_ref: str = ""
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        if self.runtime_object is not None and not isinstance(self.runtime_object, RoleRuntimeObject):
            raise TypeError("runtime_object must be a RoleRuntimeObject when provided")
        object.__setattr__(self, "profile_ref", str(self.profile_ref or "").strip())
        object.__setattr__(self, "error_code", _normalize_optional_string(self.error_code))
        object.__setattr__(self, "error_message", _normalize_optional_string(self.error_message))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.ok and self.runtime_object is None:
            raise ValueError("successful runtime object result must include runtime_object")
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed runtime object result must include error_code or error_message")


@dataclass(frozen=True)
class ExecuteRoleTaskMarketLifecycleCommandV1:
    """Execute one task-market lifecycle operation through a role binding."""

    runtime_object: RoleRuntimeObject
    operation: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_object, RoleRuntimeObject):
            raise TypeError("runtime_object must be a RoleRuntimeObject")
        operation = _require_non_empty("operation", self.operation).lower().replace("-", "_")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleTaskMarketLifecycleResultV1:
    """Structured result for a role-bound task-market lifecycle operation."""

    ok: bool
    role_id: str
    operation: str
    command_contract: str
    owner_cell: str = "runtime.task_market"
    task_id: str = ""
    status: str = ""
    result_ref: str | None = None
    lease_token_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "operation", _require_non_empty("operation", self.operation).lower())
        object.__setattr__(self, "command_contract", _require_non_empty("command_contract", self.command_contract))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "status", str(self.status or "").strip())
        object.__setattr__(self, "result_ref", _normalize_optional_string(self.result_ref))
        object.__setattr__(self, "lease_token_ref", _normalize_optional_string(self.lease_token_ref))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "error_code", _normalize_optional_string(self.error_code))
        object.__setattr__(self, "error_message", _normalize_optional_string(self.error_message))
        if self.owner_cell != "runtime.task_market":
            raise ValueError("task-market lifecycle result owner_cell must be runtime.task_market")
        if self.result_ref and not self.result_ref.startswith("runtime.task_market:"):
            raise ValueError("task-market lifecycle result_ref must point to runtime.task_market")
        if self.lease_token_ref and not self.lease_token_ref.startswith("runtime.task_market:"):
            raise ValueError("task-market lifecycle lease_token_ref must point to runtime.task_market")
        if self.ok and not self.result_ref:
            raise ValueError("successful task-market lifecycle result must include a runtime.task_market result_ref")
        if self.ok and self.operation in {"claim", "lease"} and not self.lease_token_ref:
            raise ValueError(
                "successful claim/lease task-market lifecycle result must include a runtime.task_market lease_token_ref"
            )
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed task-market lifecycle result must include error_code or error_message")


@dataclass(frozen=True)
class ExecuteRoleCapabilityInvocationCommandV1:
    """Execute one mounted role capability through its public contract port."""

    runtime_object: RoleRuntimeObject
    invocation: RoleCapabilityInvocation
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_object, RoleRuntimeObject):
            raise TypeError("runtime_object must be a RoleRuntimeObject")
        if not isinstance(self.invocation, RoleCapabilityInvocation):
            raise TypeError("invocation must be a RoleCapabilityInvocation")
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleCapabilityInvocationResultV1:
    """Structured result for a role capability RPC/API port invocation."""

    ok: bool
    invocation_id: str
    role_id: str
    capability_id: str
    command_contract: str
    allowed: bool
    payload_ref: str = ""
    owner_cell: str = ""
    result_ref: str | None = None
    task_id: str = ""
    status: str = ""
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        ok = bool(self.ok)
        invocation_id = _require_non_empty("invocation_id", self.invocation_id)
        role_id = _require_non_empty("role_id", self.role_id)
        capability_id = _require_non_empty("capability_id", self.capability_id)
        command_contract = _require_non_empty("command_contract", self.command_contract)
        allowed = bool(self.allowed)
        payload_ref = str(self.payload_ref or "").strip()
        owner_cell = str(self.owner_cell or "").strip()
        result_ref = _normalize_optional_string(self.result_ref)
        task_id = str(self.task_id or "").strip()
        status = str(self.status or "").strip()
        evidence_refs = _normalize_unique_string_tuple("evidence_refs", self.evidence_refs)
        metadata = _to_dict_copy(self.metadata)
        error_code = _normalize_optional_string(self.error_code)
        error_message = _normalize_optional_string(self.error_message)

        if owner_cell and _is_forbidden_role_object_owner_cell(owner_cell):
            raise ValueError("capability invocation result owner_cell must be a target public Cell")
        if result_ref:
            if not owner_cell:
                raise ValueError("result_ref requires owner_cell")
            if not _has_ref_namespace(result_ref, owner_cell):
                raise ValueError("result_ref must point to owner_cell")
        if (
            payload_ref
            and payload_ref != result_ref
            and not _has_any_ref_namespace(
                payload_ref,
                ("roles.runtime", "runtime.task_market"),
            )
        ):
            raise ValueError("payload_ref must point to roles.runtime or runtime.task_market")
        _require_refs_namespace("evidence_refs", evidence_refs, "audit.evidence")
        if not ok and allowed:
            raise ValueError("failed capability invocation result must set allowed=False")

        object.__setattr__(self, "ok", ok)
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "command_contract", command_contract)
        object.__setattr__(self, "allowed", allowed)
        object.__setattr__(self, "payload_ref", payload_ref)
        object.__setattr__(self, "owner_cell", owner_cell)
        object.__setattr__(self, "result_ref", result_ref)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "error_message", error_message)
        if self.ok and not self.allowed:
            raise ValueError("successful capability invocation result must be allowed")
        if self.ok and not self.owner_cell:
            raise ValueError("successful capability invocation result must include owner_cell")
        if self.ok and not self.result_ref:
            raise ValueError("successful capability invocation result must include result_ref")
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed capability invocation result must include error_code or error_message")


def _capability_required_asset_mount_names(capability: RoleCapabilityDescriptor) -> tuple[str, ...]:
    mount_names: list[str] = []
    for key in (
        "requires_asset_mounts",
        "asset_mount",
        "input_asset_mount",
        "output_asset_mount",
        "evidence_asset_mount",
    ):
        value = capability.metadata.get(key)
        if value is None:
            continue
        tokens: tuple[str, ...]
        if isinstance(value, str):
            tokens = (value,)
        elif isinstance(value, (list, tuple, set, frozenset)):
            tokens = tuple(str(item or "") for item in value)
        else:
            tokens = (str(value),)
        for token in tokens:
            mount_name = token.strip()
            if mount_name and mount_name not in mount_names:
                mount_names.append(mount_name)
    return tuple(mount_names)


@dataclass(frozen=True)
class RoleRuntimeObjectSpec:
    """Reusable runtime-object composition spec for one business role.

    The spec is owned by `roles.runtime` as composition metadata only. It
    mounts refs to assets and public contracts owned by their authoritative
    Cells; it does not copy or mutate foreign Cell state.
    """

    role_id: str
    asset_mounts: RoleAssetMountTable
    capability_ports: RoleCapabilityPorts
    default_capability_id: str
    task_market_binding: RoleTaskMarketBinding = field(default_factory=RoleTaskMarketBinding)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        role_id = _require_non_empty("role_id", self.role_id)
        object.__setattr__(self, "role_id", role_id)
        if not isinstance(self.asset_mounts, RoleAssetMountTable):
            raise TypeError("asset_mounts must be a RoleAssetMountTable")
        if not isinstance(self.capability_ports, RoleCapabilityPorts):
            raise TypeError("capability_ports must be a RoleCapabilityPorts")
        default_capability_id = _require_non_empty("default_capability_id", self.default_capability_id)
        self.capability_ports.get(default_capability_id)
        object.__setattr__(self, "default_capability_id", default_capability_id)
        _require_capability_ports_allow_role(role_id, self.capability_ports)
        mounted_asset_names = {mount.mount_name for mount in self.asset_mounts.mounts}
        for capability in self.capability_ports.capabilities:
            for mount_name in _capability_required_asset_mount_names(capability):
                if mount_name not in mounted_asset_names:
                    raise ValueError(
                        f"capability {capability.capability_id!r} requires missing asset mount {mount_name!r}"
                    )
        if not isinstance(self.task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def instantiate(
        self,
        *,
        identity: RoleIdentity,
        profile_binding: RoleProfileBinding,
        ledger_binding: RoleLedgerBinding,
        policy_fingerprint: str,
        capability_id: str | None = None,
        tool: str | None = None,
        turn_context: RoleTurnContext | None = None,
        task_market_binding: RoleTaskMarketBinding | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RoleRuntimeObject:
        """Instantiate a `RoleRuntimeObject` from this spec and runtime bindings."""
        if not isinstance(identity, RoleIdentity):
            raise TypeError("identity must be a RoleIdentity")
        if not isinstance(profile_binding, RoleProfileBinding):
            raise TypeError("profile_binding must be a RoleProfileBinding")
        if not isinstance(ledger_binding, RoleLedgerBinding):
            raise TypeError("ledger_binding must be a RoleLedgerBinding")
        if turn_context is not None and not isinstance(turn_context, RoleTurnContext):
            raise TypeError("turn_context must be a RoleTurnContext")
        if task_market_binding is not None and not isinstance(task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        if identity.role_id != self.role_id:
            raise ValueError("identity.role_id must match spec.role_id")
        if profile_binding.role_id != self.role_id:
            raise ValueError("profile_binding.role_id must match spec.role_id")

        resolved_capability_id = _normalize_optional_string(capability_id) or self.default_capability_id
        capability = self.capability_ports.get(resolved_capability_id)
        if self.role_id not in capability.allowed_roles:
            raise ValueError(f"capability {resolved_capability_id!r} is not allowed for role {self.role_id!r}")
        resolved_tool = str(
            tool or capability.endpoint_ref or f"{capability.owner_cell}:{capability.contract_name}"
        ).strip()

        runtime_metadata = _to_dict_copy(self.metadata)
        runtime_metadata.update(_to_dict_copy(metadata))

        return RoleRuntimeObject(
            identity=identity,
            profile_binding=profile_binding,
            turn_context=turn_context or _derive_role_turn_context(identity),
            asset_mounts=self.asset_mounts,
            capability_ports=self.capability_ports,
            ledger_binding=ledger_binding,
            task_market_binding=task_market_binding or self.task_market_binding,
            capability_fingerprint=RoleCapabilityFingerprint(
                role_id=self.role_id,
                capability_id=resolved_capability_id,
                effect=capability.effect,
                tool=resolved_tool,
                policy_fingerprint=_require_non_empty("policy_fingerprint", policy_fingerprint),
                profile_fingerprint=profile_binding.profile_fingerprint,
            ),
            metadata=runtime_metadata,
        )
