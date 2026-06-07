"""Public contracts for `roles.runtime` cell.

The contracts in this module define the stable boundary for role runtime
execution, status query, and event/result payloads.
"""

from __future__ import annotations

import hashlib
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
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "profile_ref", _require_non_empty("profile_ref", self.profile_ref))
        object.__setattr__(
            self,
            "tool_policy_ref",
            _require_non_empty("tool_policy_ref", self.tool_policy_ref),
        )
        object.__setattr__(
            self,
            "prompt_policy_ref",
            _require_non_empty("prompt_policy_ref", self.prompt_policy_ref),
        )
        object.__setattr__(self, "data_policy_ref", _require_non_empty("data_policy_ref", self.data_policy_ref))
        object.__setattr__(
            self,
            "profile_fingerprint",
            _require_non_empty("profile_fingerprint", self.profile_fingerprint),
        )
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))


@dataclass(frozen=True)
class RoleAssetRef:
    """Reference to an asset owned by another Cell public boundary."""

    asset_id: str
    owner_cell: str
    contract_name: str
    ref: str
    asset_kind: str = "asset"

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _require_non_empty("asset_id", self.asset_id))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(self, "contract_name", _require_non_empty("contract_name", self.contract_name))
        object.__setattr__(self, "ref", _require_non_empty("ref", self.ref))
        object.__setattr__(self, "asset_kind", _require_non_empty("asset_kind", self.asset_kind))


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
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(self, "contract_name", _require_non_empty("contract_name", self.contract_name))
        object.__setattr__(self, "effect", _require_non_empty("effect", self.effect))
        object.__setattr__(self, "allowed_roles", _normalize_string_tuple("allowed_roles", self.allowed_roles))
        object.__setattr__(self, "endpoint_ref", str(self.endpoint_ref or "").strip())
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
        if not self.fingerprint:
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
            object.__setattr__(self, "fingerprint", hashlib.sha256(content.encode("utf-8")).hexdigest())
        else:
            object.__setattr__(self, "fingerprint", _require_non_empty("fingerprint", self.fingerprint))


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
        object.__setattr__(self, "invocation_id", _require_non_empty("invocation_id", self.invocation_id))
        object.__setattr__(self, "capability_id", _require_non_empty("capability_id", self.capability_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "command_contract", _require_non_empty("command_contract", self.command_contract))
        object.__setattr__(self, "payload_ref", _require_non_empty("payload_ref", self.payload_ref))
        object.__setattr__(self, "fingerprint_ref", _require_non_empty("fingerprint_ref", self.fingerprint_ref))
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
        object.__setattr__(self, "evidence_refs", _normalize_string_tuple("evidence_refs", self.evidence_refs))
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
        object.__setattr__(self, "typed_input_ref", _require_non_empty("typed_input_ref", self.typed_input_ref))
        object.__setattr__(
            self,
            "context_snapshot_ref",
            _require_non_empty("context_snapshot_ref", self.context_snapshot_ref),
        )
        object.__setattr__(self, "handoff_refs", _normalize_string_tuple("handoff_refs", self.handoff_refs))
        object.__setattr__(self, "task_refs", _normalize_string_tuple("task_refs", self.task_refs))
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
        object.__setattr__(self, "turn_ledger_ref", _require_non_empty("turn_ledger_ref", self.turn_ledger_ref))
        object.__setattr__(self, "commit_contract", _require_non_empty("commit_contract", self.commit_contract))
        object.__setattr__(
            self,
            "runtime_receipt_contract",
            _require_non_empty("runtime_receipt_contract", self.runtime_receipt_contract),
        )
        object.__setattr__(self, "receipt_refs", _normalize_string_tuple("receipt_refs", self.receipt_refs))
        object.__setattr__(self, "commit_receipt_ref", _normalize_optional_string(self.commit_receipt_ref))


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
        object.__setattr__(self, "work_item_ref", _normalize_optional_string(self.work_item_ref))
        object.__setattr__(self, "lease_token_ref", _normalize_optional_string(self.lease_token_ref))


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
        if not isinstance(self.turn_context, RoleTurnContext):
            raise TypeError("turn_context must be a RoleTurnContext")
        invocations = tuple(self.capability_invocations)
        if any(not isinstance(item, RoleCapabilityInvocation) for item in invocations):
            raise TypeError("capability_invocations entries must be RoleCapabilityInvocation instances")
        if not isinstance(self.ledger_binding, RoleLedgerBinding):
            raise TypeError("ledger_binding must be a RoleLedgerBinding")
        if not isinstance(self.task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        object.__setattr__(self, "capability_invocations", invocations)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleStateCommitRequest:
    """Request to commit role-object turn state through existing kernel contracts."""

    request_id: str
    envelope: RoleTurnEnvelope
    changed_asset_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_non_empty("request_id", self.request_id))
        if not isinstance(self.envelope, RoleTurnEnvelope):
            raise TypeError("envelope must be a RoleTurnEnvelope")
        object.__setattr__(
            self,
            "changed_asset_refs",
            _normalize_string_tuple("changed_asset_refs", self.changed_asset_refs),
        )
        object.__setattr__(self, "evidence_refs", _normalize_string_tuple("evidence_refs", self.evidence_refs))
        object.__setattr__(self, "reason", str(self.reason or "").strip())


@dataclass(frozen=True)
class RoleStateCommitReceipt:
    """Receipt refs produced after role state commit reaches kernel/runtime stores."""

    request_id: str
    ok: bool
    commit_receipt_ref: str | None = None
    runtime_receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    turn_outcome_ref: str | None = None
    commit_contract: str = "CommitReceipt"
    runtime_receipt_contract: str = "RecordRuntimeReceiptCommandV1"
    status: str = "committed"
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_non_empty("request_id", self.request_id))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "commit_receipt_ref", _normalize_optional_string(self.commit_receipt_ref))
        object.__setattr__(
            self,
            "runtime_receipt_refs",
            _normalize_string_tuple("runtime_receipt_refs", self.runtime_receipt_refs),
        )
        object.__setattr__(self, "turn_outcome_ref", _normalize_optional_string(self.turn_outcome_ref))
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
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed commit receipt must include error_code or error_message")


@dataclass(frozen=True)
class RoleRuntimeObject:
    """Instantiated role object composed from refs, ports, and bindings only."""

    identity: RoleIdentity
    profile_binding: RoleProfileBinding
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
        if not isinstance(self.asset_mounts, RoleAssetMountTable):
            raise TypeError("asset_mounts must be a RoleAssetMountTable")
        if not isinstance(self.capability_ports, RoleCapabilityPorts):
            raise TypeError("capability_ports must be a RoleCapabilityPorts")
        if not isinstance(self.ledger_binding, RoleLedgerBinding):
            raise TypeError("ledger_binding must be a RoleLedgerBinding")
        if not isinstance(self.task_market_binding, RoleTaskMarketBinding):
            raise TypeError("task_market_binding must be a RoleTaskMarketBinding")
        if not isinstance(self.capability_fingerprint, RoleCapabilityFingerprint):
            raise TypeError("capability_fingerprint must be a RoleCapabilityFingerprint")
        if self.identity.role_id != self.capability_fingerprint.role_id:
            raise ValueError("identity.role_id must match capability_fingerprint.role_id")
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


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
    "AuditAggregateRuntimeIntegrationsQueryV1",
    "BuildAggregateRolePlanQueryV1",
    "ExecuteRoleSessionCommandV1",
    "ExecuteRoleTaskCommandV1",
    "GetRoleRuntimeStatusQueryV1",
    "IRoleRuntime",
    "MessageType",
    "RoleAgent",
    "RoleAssetMount",
    "RoleAssetMountTable",
    "RoleAssetRef",
    "RoleCapabilityDecision",
    "RoleCapabilityDescriptor",
    "RoleCapabilityFingerprint",
    "RoleCapabilityInvocation",
    "RoleCapabilityPorts",
    "RoleExecutionResultV1",
    "RoleIdentity",
    "RoleLedgerBinding",
    "RoleProfileBinding",
    "RoleRuntimeError",
    "RoleRuntimeObject",
    "RoleStateCommitReceipt",
    "RoleStateCommitRequest",
    "RoleTaskCompletedEventV1",
    "RoleTaskMarketBinding",
    "RoleTaskStartedEventV1",
    "RoleTurnContext",
    "RoleTurnEnvelope",
    # ── Stream Contract Types (Task #2) ───────────────────────────────────
    "StandardStreamEvent",
    "StreamTurnOptions",
    "create_protocol_fsm",
]


register_protocol_fsm_factory(create_protocol_fsm)
