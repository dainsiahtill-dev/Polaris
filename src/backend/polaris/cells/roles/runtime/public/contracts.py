"""Public contracts for `roles.runtime` cell.

The contracts in this module define the stable boundary for role runtime
execution, status query, and event/result payloads.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.runtime.internal.agent_runtime_base import RoleAgent
from polaris.cells.roles.runtime.internal.protocol_fsm import (
    create_protocol_fsm,
)
from polaris.kernelone.roles.shared_contracts import (
    AgentMessage,
    AgentStatus,
    MessageType,
    register_protocol_fsm_factory,
)

_FORBIDDEN_ROLE_OBJECT_OWNER_CELLS = frozenset(
    {
        "roles.runtime",
        "roles.adapters",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "kernelone.roles",
        "polaris.kernelone.roles",
    }
)
_TASK_MARKET_TASK_REF_PREFIX = "runtime.task_market:task:"


def _is_forbidden_role_object_owner_cell(owner_cell: str) -> bool:
    token = str(owner_cell or "").strip()
    return token in _FORBIDDEN_ROLE_OBJECT_OWNER_CELLS or token.startswith("polaris.kernelone.roles.")


def _is_forbidden_role_object_ref_namespace(ref: str) -> bool:
    namespace = str(ref or "").strip().split(":", 1)[0]
    return _is_forbidden_role_object_owner_cell(namespace)


def _is_roles_profile_ref_namespace(ref: str) -> bool:
    namespace = str(ref or "").strip().split(":", 1)[0]
    return namespace == "roles.profile"


def _has_ref_namespace(ref: str, namespace: str) -> bool:
    return str(ref or "").strip().split(":", 1)[0] == namespace


def _require_refs_namespace(name: str, refs: tuple[str, ...], namespace: str) -> None:
    if any(not _has_ref_namespace(ref, namespace) for ref in refs):
        raise ValueError(f"{name} must point to {namespace}")


def _normalize_scope_path(path: str) -> str:
    token = str(path or "").strip().replace("\\", "/")
    normalized = posixpath.normpath(token)
    if normalized == ".":
        return ""
    return normalized.lstrip("/")


def _path_is_within_scope(path: str, scopes: tuple[str, ...]) -> bool:
    if str(path or "").strip().replace("\\", "/").startswith("/"):
        return False
    normalized_path = _normalize_scope_path(path)
    if not normalized_path or normalized_path == ".." or normalized_path.startswith("../"):
        return False
    for scope in scopes:
        normalized_scope = _normalize_scope_path(scope).rstrip("/")
        if not normalized_scope or normalized_scope == ".." or normalized_scope.startswith("../"):
            continue
        if normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/"):
            return True
    return False


def _has_any_ref_namespace(ref: str, namespaces: tuple[str, ...]) -> bool:
    return any(_has_ref_namespace(ref, namespace) for namespace in namespaces)


def _asset_ref_namespace_matches_owner(
    *,
    owner_cell: str,
    ref: str,
    asset_kind: str,
    metadata: Mapping[str, Any],
) -> bool:
    ref_namespace = str(ref or "").strip().split(":", 1)[0]
    if ref_namespace == owner_cell:
        return True

    graph_source_ref = str(metadata.get("graph_source_ref", "")).strip()
    return (
        owner_cell == "context.catalog"
        and asset_kind == "constraint_topology"
        and ref_namespace == "docs.graph"
        and graph_source_ref.startswith("docs/graph/")
    )


def _capability_endpoint_matches_owner(owner_cell: str, endpoint_ref: str) -> bool:
    if not endpoint_ref:
        return True
    if endpoint_ref.startswith(f"{owner_cell}:"):
        return True
    return endpoint_ref.startswith(f"polaris.cells.{owner_cell}.public.")


def _is_task_market_task_ref(ref: str | None) -> bool:
    token = str(ref or "").strip()
    return token.startswith(_TASK_MARKET_TASK_REF_PREFIX) and bool(token[len(_TASK_MARKET_TASK_REF_PREFIX) :])


def _is_legacy_task_market_task_ref(ref: str | None) -> bool:
    return str(ref or "").strip().startswith("runtime.task_market:task-")


def _is_hex_sha256(value: str) -> bool:
    token = str(value or "").strip()
    return len(token) == 64 and all(char in "0123456789abcdef" for char in token.lower())


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _normalize_optional_domain(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    if not token:
        return None
    return token


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _normalize_history(history: Any) -> tuple[tuple[str, str], ...]:
    if history is None:
        return ()
    if isinstance(history, str | bytes):
        raise ValueError("history must be an iterable of (role, content) entries")

    try:
        iterator = iter(history)
    except TypeError as exc:
        raise ValueError("history must be an iterable of (role, content) entries") from exc

    normalized: list[tuple[str, str]] = []
    for index, item in enumerate(iterator):
        role = ""
        content = ""
        if isinstance(item, Mapping):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("message") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = str(item[0] or "").strip()
            content = str(item[1] or "").strip()

        if not role or not content:
            raise ValueError(f"history entries must provide non-empty role and content (index={index})")
        normalized.append((role, content))

    return tuple(normalized)


def _normalize_string_tuple(name: str, values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str | bytes):
        raise ValueError(f"{name} must be an iterable of strings, not a string")

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of strings") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(iterator):
        token = str(item or "").strip()
        if not token:
            raise ValueError(f"{name} entries must be non-empty strings (index={index})")
        if token not in seen:
            normalized.append(token)
            seen.add(token)
    return tuple(normalized)


def _normalize_unique_string_tuple(name: str, values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str | bytes):
        raise ValueError(f"{name} must be an iterable of strings, not a string")

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of strings") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(iterator):
        token = str(item or "").strip()
        if not token:
            raise ValueError(f"{name} entries must be non-empty strings (index={index})")
        if token in seen:
            raise ValueError(f"{name} must not contain duplicate refs")
        normalized.append(token)
        seen.add(token)
    return tuple(normalized)


def _require_ref_superset(message: str, container_refs: tuple[str, ...], required_refs: tuple[str, ...]) -> None:
    if not required_refs:
        return
    container = set(container_refs)
    missing_refs = tuple(ref for ref in required_refs if ref not in container)
    if missing_refs:
        raise ValueError(f"{message}: {', '.join(missing_refs)}")


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
        ):
            object.__setattr__(self, name, _require_non_empty(name, getattr(self, name)))
        work_item_ref = _normalize_optional_string(self.work_item_ref)
        lease_token_ref = _normalize_optional_string(self.lease_token_ref)
        active_refs = tuple(ref for ref in (work_item_ref, lease_token_ref) if ref)
        if any(not _has_ref_namespace(ref, "runtime.task_market") for ref in active_refs):
            raise ValueError("task-market binding refs must point to runtime.task_market")
        if _is_legacy_task_market_task_ref(work_item_ref):
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
        if _is_legacy_task_market_task_ref(work_item_ref):
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


def _asset_ref(
    *,
    asset_id: str,
    owner_cell: str,
    contract_name: str,
    ref: str,
    asset_kind: str,
    metadata: Mapping[str, Any] | None = None,
) -> RoleAssetRef:
    return RoleAssetRef(
        asset_id=asset_id,
        owner_cell=owner_cell,
        contract_name=contract_name,
        ref=ref,
        asset_kind=asset_kind,
        metadata=metadata or {},
    )


def _mount(
    mount_name: str,
    asset_ref: RoleAssetRef,
    *,
    access_mode: str = "read",
    metadata: Mapping[str, Any] | None = None,
) -> RoleAssetMount:
    return RoleAssetMount(
        mount_name=mount_name,
        asset_ref=asset_ref,
        access_mode=access_mode,
        metadata=metadata or {},
    )


def _capability(
    *,
    capability_id: str,
    owner_cell: str,
    contract_name: str,
    effect: str,
    allowed_roles: tuple[str, ...],
    endpoint_ref: str,
    metadata: Mapping[str, Any] | None = None,
) -> RoleCapabilityDescriptor:
    return RoleCapabilityDescriptor(
        capability_id=capability_id,
        owner_cell=owner_cell,
        contract_name=contract_name,
        effect=effect,
        allowed_roles=allowed_roles,
        endpoint_ref=endpoint_ref,
        metadata=metadata or {},
    )


def _task_market_lifecycle_capabilities(allowed_roles: tuple[str, ...]) -> tuple[RoleCapabilityDescriptor, ...]:
    return (
        _capability(
            capability_id="claim_task_market_work_item",
            owner_cell="runtime.task_market",
            contract_name="ClaimTaskWorkItemCommandV1",
            effect="task_market.claim",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.claim_work_item",
            metadata={"lifecycle_operation": "claim"},
        ),
        _capability(
            capability_id="renew_task_market_lease",
            owner_cell="runtime.task_market",
            contract_name="RenewTaskLeaseCommandV1",
            effect="task_market.lease",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.renew_task_lease",
            metadata={"lifecycle_operation": "lease"},
        ),
        _capability(
            capability_id="acknowledge_task_market_stage",
            owner_cell="runtime.task_market",
            contract_name="AcknowledgeTaskStageCommandV1",
            effect="task_market.ack",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.acknowledge_task_stage",
            metadata={"lifecycle_operation": "ack"},
        ),
        _capability(
            capability_id="fail_task_market_stage",
            owner_cell="runtime.task_market",
            contract_name="FailTaskStageCommandV1",
            effect="task_market.fail",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.fail_task_stage",
            metadata={"lifecycle_operation": "fail"},
        ),
        _capability(
            capability_id="requeue_task_market_work_item",
            owner_cell="runtime.task_market",
            contract_name="RequeueTaskCommandV1",
            effect="task_market.requeue",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.requeue_task",
            metadata={"lifecycle_operation": "requeue"},
        ),
    )


def _build_pm_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="pm",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "ProjectFunctionIndex",
                    _asset_ref(
                        asset_id="project-function-index",
                        owner_cell="context.catalog",
                        contract_name="SearchCellsQueryV1",
                        ref="context.catalog:project-function-index",
                        asset_kind="project_function_index",
                        metadata={
                            "derived_from": (
                                "context.catalog",
                                "runtime.task_runtime",
                                "runtime.task_market",
                                "runtime.projection",
                            )
                        },
                    ),
                ),
                _mount(
                    "TaskGraph",
                    _asset_ref(
                        asset_id="task-graph",
                        owner_cell="runtime.task_market",
                        contract_name="QueryTaskMarketStatusV1",
                        ref="runtime.task_market:task-graph",
                        asset_kind="task_graph",
                        metadata={"task_runtime_owner_cell": "runtime.task_runtime"},
                    ),
                ),
                _mount(
                    "RuntimeProjectionState",
                    _asset_ref(
                        asset_id="runtime-projection-state",
                        owner_cell="runtime.projection",
                        contract_name="RuntimeProjectionQueryV1",
                        ref="runtime.projection:runtime-status",
                        asset_kind="runtime_projection_state",
                    ),
                ),
                _mount(
                    "OpenLoopRegistry",
                    _asset_ref(
                        asset_id="open-loop-registry",
                        owner_cell="runtime.task_market",
                        contract_name="QueryTaskMarketStatusV1",
                        ref="runtime.task_market:open-loops",
                        asset_kind="open_loop_registry",
                        metadata={
                            "evidence_owner_cell": "audit.evidence",
                            "evidence_ref": "audit.evidence:open-loop-registry",
                        },
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="dispatch_task_to_market",
                    owner_cell="runtime.task_market",
                    contract_name="PublishTaskWorkItemCommandV1",
                    effect="task_market.publish",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.runtime.task_market.public.service.TaskMarketService.publish_work_item",
                    metadata={"target_stage": "pending_design"},
                ),
                _capability(
                    capability_id="evaluate_critical_path",
                    owner_cell="runtime.task_market",
                    contract_name="QueryTaskMarketStatusV1",
                    effect="task_market.read",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.runtime.task_market.public.service.TaskMarketService.query_status",
                    metadata={"requires_asset_mounts": ("TaskGraph", "RuntimeProjectionState")},
                ),
                _capability(
                    capability_id="project_runtime_status",
                    owner_cell="runtime.projection",
                    contract_name="RuntimeProjectionQueryV1",
                    effect="runtime_projection.read",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.runtime.projection.public.contracts.RuntimeProjectionQueryV1",
                    metadata={"requires_asset_mounts": ("TaskGraph", "RuntimeProjectionState")},
                ),
            )
        ),
        default_capability_id="dispatch_task_to_market",
        task_market_binding=RoleTaskMarketBinding(),
        metadata={"owner_cell": "roles.runtime", "business_role": "pm"},
    )


def _build_chief_engineer_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="chief_engineer",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "BlueprintDatabase",
                    _asset_ref(
                        asset_id="blueprint-database",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:runtime/blueprints",
                        asset_kind="blueprint_database",
                    ),
                ),
                _mount(
                    "ArchConstraintMemo",
                    _asset_ref(
                        asset_id="arch-constraint-memo",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:arch-constraint-memo",
                        asset_kind="arch_constraint_memo",
                        metadata={"governance_source_ref": "docs/graph/**"},
                    ),
                ),
                _mount(
                    "DiffMapArchive",
                    _asset_ref(
                        asset_id="diff-map-archive",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:diff-map-archive",
                        asset_kind="diff_map_archive",
                        metadata={
                            "requires_blueprint_ref": True,
                            "blueprint_id": "chief-engineer-runtime-blueprint",
                            "path": "runtime/blueprints/diff-map-archive",
                            "ref": "chief_engineer.blueprint:diff-map-archive:chief-engineer-runtime-blueprint",
                        },
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="generate_diff_specification",
                    owner_cell="chief_engineer.blueprint",
                    contract_name="GenerateTaskBlueprintCommandV1",
                    effect="blueprint.generate",
                    allowed_roles=("chief_engineer",),
                    endpoint_ref="polaris.cells.chief_engineer.blueprint.public.service.generate_task_blueprint",
                    metadata={"output_contract": "TaskBlueprintResultV1"},
                ),
                _capability(
                    capability_id="verify_ast_dependency",
                    owner_cell="code_intelligence.engine",
                    contract_name="VerifyAstDependencyQueryV1",
                    effect="code_intelligence.read",
                    allowed_roles=("chief_engineer",),
                    endpoint_ref="polaris.cells.code_intelligence.engine.public.service.verify_ast_dependency",
                    metadata={
                        "output_contract": "AstDependencyVerificationResultV1",
                        "implementation_port": "TreeSitterSymbolHandler.find_symbol",
                    },
                ),
                _capability(
                    capability_id="record_arch_memo",
                    owner_cell="chief_engineer.blueprint",
                    contract_name="GenerateTaskBlueprintCommandV1",
                    effect="blueprint.memo.record",
                    allowed_roles=("chief_engineer",),
                    endpoint_ref="polaris.cells.chief_engineer.blueprint.public.service.generate_task_blueprint",
                    metadata={"asset_mount": "ArchConstraintMemo"},
                ),
                *_task_market_lifecycle_capabilities(("chief_engineer",)),
            )
        ),
        default_capability_id="generate_diff_specification",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_design"),
        metadata={"owner_cell": "roles.runtime", "business_role": "chief_engineer"},
    )


def _build_architect_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="architect",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "ConstraintTopology",
                    _asset_ref(
                        asset_id="constraint-topology",
                        owner_cell="context.catalog",
                        contract_name="SearchCellsQueryV1",
                        ref="docs.graph:cells",
                        asset_kind="constraint_topology",
                        metadata={"graph_source_ref": "docs/graph/**", "target_cell": "architect.design"},
                    ),
                ),
                _mount(
                    "ContextBudgetProfile",
                    _asset_ref(
                        asset_id="context-budget-profile",
                        owner_cell="finops.budget_guard",
                        contract_name="GetBudgetStatusQueryV1",
                        ref="finops.budget_guard:context-budget-profile",
                        asset_kind="context_budget_profile",
                        metadata={"context_owner_cell": "context.engine"},
                    ),
                ),
                _mount(
                    "MutationBoundaryMap",
                    _asset_ref(
                        asset_id="mutation-boundary-map",
                        owner_cell="policy.workspace_guard",
                        contract_name="WorkspaceWriteGuardQueryV1",
                        ref="policy.workspace_guard:mutation-boundary-map",
                        asset_kind="mutation_boundary_map",
                        metadata={
                            "derived_from": ("docs/graph/**", "policy.workspace_guard", "policy.permission"),
                            "permission_owner_cell": "policy.permission",
                        },
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="allocate_context_token_budget",
                    owner_cell="finops.budget_guard",
                    contract_name="ReserveBudgetCommandV1",
                    effect="budget.reserve:context",
                    allowed_roles=("architect",),
                    endpoint_ref="polaris.cells.finops.budget_guard.public.service.reserve_budget",
                    metadata={"asset_mount": "ContextBudgetProfile"},
                ),
                _capability(
                    capability_id="intercept_illegal_mutations",
                    owner_cell="policy.workspace_guard",
                    contract_name="WorkspaceWriteGuardQueryV1",
                    effect="mutation.guard:workspace",
                    allowed_roles=("architect",),
                    endpoint_ref="polaris.cells.policy.workspace_guard.public.service.check_workspace_write_guard",
                    metadata={"asset_mount": "MutationBoundaryMap"},
                ),
                _capability(
                    capability_id="validate_cell_boundary_change",
                    owner_cell="architect.design",
                    contract_name="GenerateArchitectureDesignCommandV1",
                    effect="architect.validate_cell_boundary",
                    allowed_roles=("architect",),
                    endpoint_ref="polaris.cells.architect.design.public.service.generate_architecture_design",
                    metadata={
                        "requires_asset_mounts": ("ConstraintTopology", "MutationBoundaryMap"),
                        "permission_contract": "EvaluatePermissionCommandV1",
                    },
                ),
            )
        ),
        default_capability_id="intercept_illegal_mutations",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_architecture"),
        metadata={"owner_cell": "roles.runtime", "business_role": "architect"},
    )


def _build_qa_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="qa",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "TruthLog",
                    _asset_ref(
                        asset_id="truth-log",
                        owner_cell="audit.evidence",
                        contract_name="QueryEvidenceEventsV1",
                        ref="audit.evidence:runtime/evidence",
                        asset_kind="truth_log",
                        metadata={
                            "runtime_receipt_owner_cell": "factory.cognitive_runtime",
                            "runtime_receipt_ref": "factory.cognitive_runtime:receipt:truth-log",
                        },
                    ),
                ),
                _mount(
                    "RegressionTestRegistry",
                    _asset_ref(
                        asset_id="regression-test-registry",
                        owner_cell="qa.audit_verdict",
                        contract_name="RunQaAuditCommandV1",
                        ref="qa.audit_verdict:regression-test-registry",
                        asset_kind="regression_test_registry",
                        metadata={"verification_owner_cell": "factory.verification_guard"},
                    ),
                ),
                _mount(
                    "FailureSignalIndex",
                    _asset_ref(
                        asset_id="failure-signal-index",
                        owner_cell="qa.audit_verdict",
                        contract_name="RunQaAuditCommandV1",
                        ref="qa.audit_verdict:failure-signal-index",
                        asset_kind="failure_signal_index",
                        metadata={"evidence_owner_cell": "audit.evidence"},
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="invoke_container_pytest",
                    owner_cell="factory.verification_guard",
                    contract_name="VerifyCompletionCommandV1",
                    effect="process.spawn:qa/pytest",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.factory.verification_guard.public.service.verify_completion",
                    metadata={"output_contract": "VerifyCompletionResultV1"},
                ),
                _capability(
                    capability_id="parse_traceback_frames",
                    owner_cell="qa.audit_verdict",
                    contract_name="ParseTracebackFramesCommandV1",
                    effect="qa.failure_signal.parse",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.qa.audit_verdict.public.service.parse_traceback_frames",
                    metadata={"output_asset_mount": "FailureSignalIndex", "output_contract": "FailureSignalV1"},
                ),
                _capability(
                    capability_id="issue_audit_verdict",
                    owner_cell="qa.audit_verdict",
                    contract_name="RunQaAuditCommandV1",
                    effect="qa.verdict.issue",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.qa.audit_verdict.public.contracts.RunQaAuditCommandV1",
                    metadata={"output_contract": "QaAuditResultV1"},
                ),
                _capability(
                    capability_id="issue_visual_audit_verdict",
                    owner_cell="qa.audit_verdict",
                    contract_name="RunVisualQaAuditCommandV1",
                    effect="llm.invoke:vision",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.qa.audit_verdict.public.service.run_visual_qa_audit",
                    metadata={
                        "input_asset_mount": "TruthLog",
                        "model_capability_query": "CheckLlmModelCapabilityQueryV1",
                        "required_model_capability": "image_input",
                        "output_contract": "VisualQaAuditResultV1",
                    },
                ),
                *_task_market_lifecycle_capabilities(("qa",)),
            )
        ),
        default_capability_id="invoke_container_pytest",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_qa"),
        metadata={"owner_cell": "roles.runtime", "business_role": "qa"},
    )


def _build_director_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="director",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "ExecutionTask",
                    _asset_ref(
                        asset_id="director-execution-task",
                        owner_cell="runtime.task_market",
                        contract_name="ClaimTaskWorkItemCommandV1",
                        ref="runtime.task_market:director/execution-task",
                        asset_kind="task_market_work_item",
                        metadata={"status_contract": "QueryTaskMarketStatusV1"},
                    ),
                ),
                _mount(
                    "DirectorExecutionState",
                    _asset_ref(
                        asset_id="director-execution-state",
                        owner_cell="director.execution",
                        contract_name="GetDirectorTaskStatusQueryV1",
                        ref="director.execution:runtime/state",
                        asset_kind="director_execution_state",
                    ),
                ),
                _mount(
                    "DirectorEvidenceTrail",
                    _asset_ref(
                        asset_id="director-evidence-trail",
                        owner_cell="audit.evidence",
                        contract_name="AppendEvidenceEventCommandV1",
                        ref="audit.evidence:director-execution",
                        asset_kind="director_evidence_trail",
                        metadata={"query_contract": "QueryEvidenceEventsV1"},
                    ),
                    access_mode="write",
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="execute_director_task",
                    owner_cell="director.execution",
                    contract_name="ExecuteDirectorTaskCommandV1",
                    effect="process.spawn:director/*",
                    allowed_roles=("director",),
                    endpoint_ref="polaris.cells.director.execution.public.service.execute_director_task",
                    metadata={
                        "requires_asset_mounts": ("ExecutionTask", "DirectorExecutionState"),
                        "evidence_asset_mount": "DirectorEvidenceTrail",
                        "output_contract": "DirectorExecutionResultV1",
                    },
                ),
                *_task_market_lifecycle_capabilities(("director",)),
            )
        ),
        default_capability_id="execute_director_task",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_exec"),
        metadata={"owner_cell": "roles.runtime", "business_role": "director"},
    )


def get_builtin_role_runtime_spec(role_id: str) -> RoleRuntimeObjectSpec:
    """Return the built-in role runtime composition spec for a known role."""
    normalized = _require_non_empty("role_id", role_id).lower().replace("-", "_")
    aliases = {
        "project_manager": "pm",
        "ce": "chief_engineer",
        "chiefengineer": "chief_engineer",
        "architecture": "architect",
        "design_architect": "architect",
        "quality_assurance": "qa",
        "auditor": "qa",
        "director_execution": "director",
        "executor": "director",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "pm":
        return _build_pm_runtime_spec()
    if normalized == "chief_engineer":
        return _build_chief_engineer_runtime_spec()
    if normalized == "architect":
        return _build_architect_runtime_spec()
    if normalized == "qa":
        return _build_qa_runtime_spec()
    if normalized == "director":
        return _build_director_runtime_spec()
    raise KeyError(normalized)


@dataclass(frozen=True)
class ExecuteRoleTaskCommandV1:
    """Execute one role task under the runtime role kernel."""

    role: str
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    session_id: str | None = None
    domain: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    stream: bool = False
    host_kind: str | None = None  # Task #2: unified host protocol

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")


@dataclass(frozen=True)
class ExecuteRoleSessionCommandV1:
    """Execute one user turn on an existing role session."""

    role: str
    session_id: str
    workspace: str
    user_message: str
    run_id: str | None = None
    task_id: str | None = None
    domain: str | None = None
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = True
    stream_options: StreamTurnOptions | None = None
    host_kind: str | None = None  # Task #2: unified host protocol
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "user_message", _require_non_empty("user_message", self.user_message))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "history", _normalize_history(self.history))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.stream_options is not None and not isinstance(self.stream_options, StreamTurnOptions):
            raise TypeError("stream_options must be a StreamTurnOptions instance")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")


@dataclass(frozen=True)
class GetRoleRuntimeStatusQueryV1:
    """Query role runtime health/status for one workspace."""

    workspace: str
    role: str | None = None
    include_agent_health: bool = True
    include_queue: bool = True
    include_tools: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class BuildAggregateRolePlanQueryV1:
    """Build a deterministic role-lobe plan for an aggregate model wrapper.

    This is a query-only contract. It does not execute roles, call an LLM, or
    mutate runtime state; callers use the result to decide how to compose role
    turns behind a single external model-like interface.
    """

    workspace: str
    objective: str
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    failure_signals: tuple[str, ...] = field(default_factory=tuple)
    failure_evidence: Mapping[str, Any] = field(default_factory=dict)
    domain: str | None = None
    include_virtual_lobes: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "failure_signals", _normalize_string_tuple("failure_signals", self.failure_signals))
        object.__setattr__(self, "failure_evidence", _to_dict_copy(self.failure_evidence))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AuditAggregateRuntimeIntegrationsQueryV1:
    """Audit aggregate-model integrations against current runtime entrypoints."""

    workspace: str
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    include_virtual_lobes: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateRoleLobeV1:
    """One internal functional lobe of a Polaris aggregate role plan."""

    lobe_id: str
    title: str
    phase: str
    role_ids: tuple[str, ...]
    virtual_role_ids: tuple[str, ...]
    capability_refs: tuple[str, ...]
    attention_masks: tuple[str, ...]
    memory_triggers: tuple[str, ...]
    compute_tier: str
    handoff_keys: tuple[str, ...]
    takeover_triggers: tuple[str, ...]
    output_contract: str
    status: str = "active"
    missing_role_ids: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lobe_id", _require_non_empty("lobe_id", self.lobe_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "virtual_role_ids", _normalize_string_tuple("virtual_role_ids", self.virtual_role_ids))
        object.__setattr__(self, "capability_refs", _normalize_string_tuple("capability_refs", self.capability_refs))
        object.__setattr__(self, "attention_masks", _normalize_string_tuple("attention_masks", self.attention_masks))
        object.__setattr__(self, "memory_triggers", _normalize_string_tuple("memory_triggers", self.memory_triggers))
        object.__setattr__(self, "compute_tier", _require_non_empty("compute_tier", self.compute_tier))
        object.__setattr__(self, "handoff_keys", _normalize_string_tuple("handoff_keys", self.handoff_keys))
        object.__setattr__(
            self,
            "takeover_triggers",
            _normalize_string_tuple("takeover_triggers", self.takeover_triggers),
        )
        object.__setattr__(self, "output_contract", _require_non_empty("output_contract", self.output_contract))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "missing_role_ids", _normalize_string_tuple("missing_role_ids", self.missing_role_ids))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateCognitiveLedgerEntryV1:
    """One internal state handoff in the aggregate model plan."""

    sequence: int
    lobe_id: str
    phase: str
    compute_tier: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    handoff_to: tuple[str, ...]
    takeover_triggers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        object.__setattr__(self, "lobe_id", _require_non_empty("lobe_id", self.lobe_id))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "compute_tier", _require_non_empty("compute_tier", self.compute_tier))
        object.__setattr__(self, "reads", _normalize_string_tuple("reads", self.reads))
        object.__setattr__(self, "writes", _normalize_string_tuple("writes", self.writes))
        object.__setattr__(self, "handoff_to", _normalize_string_tuple("handoff_to", self.handoff_to))
        object.__setattr__(
            self,
            "takeover_triggers",
            _normalize_string_tuple("takeover_triggers", self.takeover_triggers),
        )


@dataclass(frozen=True)
class AggregateTakeoverDirectiveV1:
    """Planned internal lobe takeover for an observed failure signal."""

    trigger: str
    lobe_id: str
    compute_tier: str
    reason: str
    evidence_keys: tuple[str, ...]
    action_contract: str
    next_lobes: tuple[str, ...]
    status: str = "planned"

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger", _require_non_empty("trigger", self.trigger))
        object.__setattr__(self, "lobe_id", _require_non_empty("lobe_id", self.lobe_id))
        object.__setattr__(self, "compute_tier", _require_non_empty("compute_tier", self.compute_tier))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "evidence_keys", _normalize_string_tuple("evidence_keys", self.evidence_keys))
        object.__setattr__(self, "action_contract", _require_non_empty("action_contract", self.action_contract))
        object.__setattr__(self, "next_lobes", _normalize_string_tuple("next_lobes", self.next_lobes))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))


@dataclass(frozen=True)
class AggregateRuntimeEntrypointCheckV1:
    """Runtime-verifiable production entrypoint evidence for one integration."""

    entrypoint: str
    check_type: str
    ok: bool
    evidence: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entrypoint", _require_non_empty("entrypoint", self.entrypoint))
        object.__setattr__(self, "check_type", _require_non_empty("check_type", self.check_type))
        object.__setattr__(self, "evidence", _require_non_empty("evidence", self.evidence))
        if self.reason:
            object.__setattr__(self, "reason", str(self.reason).strip())


@dataclass(frozen=True)
class AggregateRuntimeIntegrationV1:
    """One auditable Polaris-unique technology mapped to runtime entrypoints."""

    tech_id: str
    title: str
    status: str
    priority: str
    production_entrypoints: tuple[str, ...]
    trigger_keys: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    runtime_effects: tuple[str, ...]
    benefit: str
    capability_refs: tuple[str, ...] = field(default_factory=tuple)
    entrypoint_checks: tuple[AggregateRuntimeEntrypointCheckV1, ...] = field(default_factory=tuple)
    entrypoints_verified: bool = False
    missing_entrypoints: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tech_id", _require_non_empty("tech_id", self.tech_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "priority", _require_non_empty("priority", self.priority))
        object.__setattr__(
            self,
            "production_entrypoints",
            _normalize_string_tuple("production_entrypoints", self.production_entrypoints),
        )
        object.__setattr__(self, "trigger_keys", _normalize_string_tuple("trigger_keys", self.trigger_keys))
        object.__setattr__(self, "evidence_keys", _normalize_string_tuple("evidence_keys", self.evidence_keys))
        object.__setattr__(self, "runtime_effects", _normalize_string_tuple("runtime_effects", self.runtime_effects))
        object.__setattr__(self, "benefit", _require_non_empty("benefit", self.benefit))
        object.__setattr__(self, "capability_refs", _normalize_string_tuple("capability_refs", self.capability_refs))
        object.__setattr__(self, "entrypoint_checks", tuple(self.entrypoint_checks))
        object.__setattr__(self, "entrypoints_verified", bool(self.entrypoints_verified))
        object.__setattr__(
            self,
            "missing_entrypoints",
            _normalize_string_tuple("missing_entrypoints", self.missing_entrypoints),
        )


@dataclass(frozen=True)
class AggregateRuntimeAuditResultV1:
    """Machine-readable aggregate runtime integration audit result."""

    ok: bool
    workspace: str
    aggregate_model_id: str
    integrations: tuple[AggregateRuntimeIntegrationV1, ...]
    wired_count: int
    available_count: int
    planned_bridge_count: int
    verified_entrypoint_count: int
    missing_entrypoint_count: int
    priority_wired: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self, "aggregate_model_id", _require_non_empty("aggregate_model_id", self.aggregate_model_id)
        )
        object.__setattr__(self, "integrations", tuple(self.integrations))
        if not self.integrations:
            raise ValueError("integrations must include at least one entry")
        if (
            self.wired_count < 0
            or self.available_count < 0
            or self.planned_bridge_count < 0
            or self.verified_entrypoint_count < 0
            or self.missing_entrypoint_count < 0
        ):
            raise ValueError("integration counts must be >= 0")
        object.__setattr__(self, "priority_wired", _normalize_string_tuple("priority_wired", self.priority_wired))
        object.__setattr__(self, "warnings", _normalize_string_tuple("warnings", self.warnings))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateRolePlanResultV1:
    """Query result for an aggregate role/lobe composition plan."""

    ok: bool
    workspace: str
    objective: str
    aggregate_model_id: str
    lobes: tuple[AggregateRoleLobeV1, ...]
    execution_order: tuple[str, ...]
    current_role_ids: tuple[str, ...]
    required_capability_refs: tuple[str, ...]
    runtime_integrations: tuple[AggregateRuntimeIntegrationV1, ...] = field(default_factory=tuple)
    cognitive_ledger: tuple[AggregateCognitiveLedgerEntryV1, ...] = field(default_factory=tuple)
    compute_policy: Mapping[str, Any] = field(default_factory=dict)
    takeover_directive: AggregateTakeoverDirectiveV1 | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(
            self, "aggregate_model_id", _require_non_empty("aggregate_model_id", self.aggregate_model_id)
        )
        object.__setattr__(self, "lobes", tuple(self.lobes))
        object.__setattr__(self, "execution_order", _normalize_string_tuple("execution_order", self.execution_order))
        object.__setattr__(self, "current_role_ids", _normalize_string_tuple("current_role_ids", self.current_role_ids))
        object.__setattr__(
            self,
            "required_capability_refs",
            _normalize_string_tuple("required_capability_refs", self.required_capability_refs),
        )
        object.__setattr__(self, "runtime_integrations", tuple(self.runtime_integrations))
        object.__setattr__(self, "cognitive_ledger", tuple(self.cognitive_ledger))
        object.__setattr__(self, "compute_policy", _to_dict_copy(self.compute_policy))
        object.__setattr__(self, "warnings", _normalize_string_tuple("warnings", self.warnings))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateChatMessageV1:
    """Chat message for the aggregate model wrapper."""

    role: str
    content: str
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "content", _require_non_empty("content", self.content))
        if self.name is not None:
            object.__setattr__(self, "name", _require_non_empty("name", self.name))


def _normalize_chat_messages(messages: Any) -> tuple[AggregateChatMessageV1, ...]:
    if messages is None:
        return ()
    if isinstance(messages, str | bytes):
        raise ValueError("messages must be an iterable of chat message entries")

    try:
        iterator = iter(messages)
    except TypeError as exc:
        raise ValueError("messages must be an iterable of chat message entries") from exc

    normalized: list[AggregateChatMessageV1] = []
    for index, item in enumerate(iterator):
        if isinstance(item, AggregateChatMessageV1):
            normalized.append(item)
            continue
        if isinstance(item, Mapping):
            normalized.append(
                AggregateChatMessageV1(
                    role=str(item.get("role") or "").strip(),
                    content=str(item.get("content") or "").strip(),
                    name=str(item.get("name")).strip() if item.get("name") is not None else None,
                )
            )
            continue
        raise ValueError(f"messages entries must be AggregateChatMessageV1 or mapping (index={index})")
    return tuple(normalized)


@dataclass(frozen=True)
class AggregateChatCompletionsCommandV1:
    """Single-model-shaped command for a Polaris aggregate LLM wrapper.

    `plan_only` is side-effect free. `single_turn` executes one selected
    concrete role. `lobe_chain` executes a bounded sequence of concrete roles
    selected from the aggregate lobe plan.
    """

    workspace: str
    messages: tuple[AggregateChatMessageV1, ...]
    model: str = "polaris.aggregate_llm.v1"
    domain: str | None = None
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    failure_signals: tuple[str, ...] = field(default_factory=tuple)
    failure_evidence: Mapping[str, Any] = field(default_factory=dict)
    execution_mode: str = "plan_only"
    session_id: str | None = None
    run_id: str | None = None
    include_virtual_lobes: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "messages", _normalize_chat_messages(self.messages))
        if not self.messages:
            raise ValueError("messages must include at least one chat message")
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "failure_signals", _normalize_string_tuple("failure_signals", self.failure_signals))
        object.__setattr__(self, "failure_evidence", _to_dict_copy(self.failure_evidence))
        mode = str(self.execution_mode or "").strip().lower()
        if mode not in {"plan_only", "single_turn", "lobe_chain"}:
            raise ValueError("execution_mode currently supports 'plan_only', 'single_turn', or 'lobe_chain'")
        object.__setattr__(self, "execution_mode", mode)
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateChatChoiceV1:
    """One chat-completions choice emitted by the aggregate model wrapper."""

    index: int
    message: AggregateChatMessageV1
    finish_reason: str = "stop"

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("index must be >= 0")
        object.__setattr__(self, "finish_reason", _require_non_empty("finish_reason", self.finish_reason))


@dataclass(frozen=True)
class AggregateChatCompletionsResultV1:
    """Chat-completions-shaped result for the Polaris aggregate model wrapper."""

    id: str
    object: str
    model: str
    choices: tuple[AggregateChatChoiceV1, ...]
    usage: Mapping[str, Any] = field(default_factory=dict)
    aggregate_plan: AggregateRolePlanResultV1 | None = None
    execution_result: RoleExecutionResultV1 | None = None
    execution_results: tuple[RoleExecutionResultV1, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_non_empty("id", self.id))
        object.__setattr__(self, "object", _require_non_empty("object", self.object))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "choices", tuple(self.choices))
        if not self.choices:
            raise ValueError("choices must include at least one choice")
        object.__setattr__(self, "usage", _to_dict_copy(self.usage))
        object.__setattr__(self, "execution_results", tuple(self.execution_results))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleTaskStartedEventV1:
    """Event emitted when role runtime starts a task."""

    event_id: str
    role: str
    task_id: str
    workspace: str
    started_at: str
    run_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class RoleTaskCompletedEventV1:
    """Event emitted when role runtime completes a task."""

    event_id: str
    role: str
    task_id: str
    workspace: str
    status: str
    completed_at: str
    run_id: str | None = None
    session_id: str | None = None
    output_summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class RoleExecutionResultV1:
    """Unified role execution result for task/session calls."""

    ok: bool
    status: str
    role: str
    workspace: str
    task_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    output: str = ""
    thinking: str | None = None
    tool_calls: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    # 完整回话历史 (role, content) 对列表 — 用于非流式模式下的 session 持久化
    turn_history: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "tool_calls", tuple(str(v) for v in self.tool_calls))
        object.__setattr__(self, "artifacts", tuple(str(v) for v in self.artifacts))
        object.__setattr__(self, "usage", _to_dict_copy(self.usage))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "turn_history", list(self.turn_history))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


# ── Stream contract types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamTurnOptions:
    """Options for streamed role chat turns (Task #2)."""

    stream: bool = True
    context: dict[str, Any] | None = None
    history_limit: int | None = None
    prompt_appendix: str | None = None


class StandardStreamEvent(dict):
    """Dict-subclass canonical stream event for the contracts layer (Task #2).

    Mirrors the dataclass in ``console_protocol`` but as a dict so callers
    that expect ``isinstance(result, dict)`` receive a compatible type.
    """

    def __init__(
        self,
        type: str = "",
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            type=str(type),
            data=dict(data) if data else {},
            metadata=dict(metadata) if metadata else {},
        )

    @property
    def event_type(self) -> str:
        return self["type"]

    @property
    def event_data(self) -> dict[str, Any]:
        return self["data"]


class RoleRuntimeError(RuntimeError):
    """Structured runtime contract error for roles.runtime."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "role_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_message = _require_non_empty("message", message)
        super().__init__(normalized_message)
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


@runtime_checkable
class IRoleRuntime(Protocol):
    """Public role runtime interface.

    Notes:
    - `execute_role` is retained as a compatibility method for older callsites.
    - New code should use `execute_role_task` or `execute_role_session`.
    """

    async def execute_role_task(
        self,
        command: ExecuteRoleTaskCommandV1,
    ) -> RoleExecutionResultV1:
        """Execute one task command."""

    async def execute_role_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1:
        """Execute one session-turn command."""

    async def get_runtime_status(
        self,
        query: GetRoleRuntimeStatusQueryV1,
    ) -> Mapping[str, Any]:
        """Return runtime status snapshot."""

    async def build_aggregate_role_plan(
        self,
        query: BuildAggregateRolePlanQueryV1,
    ) -> AggregateRolePlanResultV1:
        """Return a query-only aggregate role/lobe composition plan."""

    async def audit_aggregate_runtime_integrations(
        self,
        query: AuditAggregateRuntimeIntegrationsQueryV1,
    ) -> AggregateRuntimeAuditResultV1:
        """Return runtime integration audit for aggregate-model technology."""

    async def chat_completions(
        self,
        command: AggregateChatCompletionsCommandV1,
    ) -> AggregateChatCompletionsResultV1:
        """Return a model-shaped aggregate chat completion."""

    async def execute_role(
        self,
        role_id: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Compatibility method for pre-contract callsites."""

    def stream_chat_turn(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream role chat turn events as an async iterator."""


__all__ = [
    # ── Cross-Cell Agent Types ───────────────────────────────────────────────
    # These types are the stable public contract for the agent runtime.
    # qa.audit_verdict (and future cross-cell callers) import from here
    # instead of roles.runtime.public.service, which pulls in 8+ internal modules.
    "AgentMessage",
    "AgentStatus",
    # ── Execution Contracts ────────────────────────────────────────────────
    "AggregateChatChoiceV1",
    "AggregateChatCompletionsCommandV1",
    "AggregateChatCompletionsResultV1",
    "AggregateChatMessageV1",
    "AggregateCognitiveLedgerEntryV1",
    "AggregateRoleLobeV1",
    "AggregateRolePlanResultV1",
    "AggregateRuntimeAuditResultV1",
    "AggregateRuntimeEntrypointCheckV1",
    "AggregateRuntimeIntegrationV1",
    "AggregateTakeoverDirectiveV1",
    "AssembleRoleRuntimeChainCommandV1",
    "AuditAggregateRuntimeIntegrationsQueryV1",
    "BuildAggregateRolePlanQueryV1",
    "ExecuteRoleCapabilityInvocationCommandV1",
    "ExecuteRoleSessionCommandV1",
    "ExecuteRoleTaskCommandV1",
    "ExecuteRoleTaskMarketLifecycleCommandV1",
    "GetRoleRuntimeStatusQueryV1",
    "IRoleRuntime",
    "InstantiateRoleRuntimeObjectCommandV1",
    "MessageType",
    "RoleAgent",
    "RoleAssetMount",
    "RoleAssetMountTable",
    "RoleAssetRef",
    "RoleCapabilityDecision",
    "RoleCapabilityDescriptor",
    "RoleCapabilityFingerprint",
    "RoleCapabilityInvocation",
    "RoleCapabilityInvocationResultV1",
    "RoleCapabilityPorts",
    "RoleExecutionResultV1",
    "RoleIdentity",
    "RoleLedgerBinding",
    "RoleProfileBinding",
    "RoleRuntimeChainAssemblyResultV1",
    "RoleRuntimeChainEnvelope",
    "RoleRuntimeChainStepRef",
    "RoleRuntimeError",
    "RoleRuntimeObject",
    "RoleRuntimeObjectResultV1",
    "RoleRuntimeObjectSpec",
    "RoleStateCommitReceipt",
    "RoleStateCommitRequest",
    "RoleTaskCompletedEventV1",
    "RoleTaskMarketBinding",
    "RoleTaskMarketLifecycleResultV1",
    "RoleTaskStartedEventV1",
    "RoleTurnContext",
    "RoleTurnEnvelope",
    # ── Stream Contract Types (Task #2) ───────────────────────────────────
    "StandardStreamEvent",
    "StreamTurnOptions",
    "create_protocol_fsm",
    "get_builtin_role_runtime_spec",
]


register_protocol_fsm_factory(create_protocol_fsm)
