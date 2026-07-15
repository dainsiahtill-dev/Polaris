"""Public contracts for `roles.runtime` cell.

The contracts in this package define the stable boundary for role runtime
execution, status query, and event/result payloads.

This package is the lossless successor of the former ``contracts`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...public.contracts`` and ``from ...public.contracts import X``
keep resolving identically for all external importers. The one-time
FSM-factory registration that previously ran at module import runs here,
exactly once, at package import.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / typing names that
# were module-level attributes of the former ``contracts`` module. Keeping
# them bound here preserves the exact importable attribute surface
# (``contracts.Mapping``, ``contracts.hashlib``, ...) after the split.
import hashlib
import posixpath
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Mapping,
)
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.runtime.internal.agent_runtime_base import RoleAgent
from polaris.cells.roles.runtime.internal.protocol_fsm import (
    create_protocol_fsm,
)
from polaris.cells.roles.runtime.public.contracts._builtin_specs import (
    _asset_ref,
    _build_architect_runtime_spec,
    _build_chief_engineer_runtime_spec,
    _build_director_runtime_spec,
    _build_pm_runtime_spec,
    _build_qa_runtime_spec,
    _capability,
    _mount,
    _task_market_lifecycle_capabilities,
    get_builtin_role_runtime_spec,
)
from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
    AggregateChatChoiceV1,
    AggregateChatCompletionsCommandV1,
    AggregateChatCompletionsResultV1,
    AggregateChatMessageV1,
    AggregateCognitiveLedgerEntryV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    AggregateRuntimeAuditResultV1,
    AggregateRuntimeEntrypointCheckV1,
    AggregateRuntimeIntegrationV1,
    AggregateTakeoverDirectiveV1,
    AuditAggregateRuntimeIntegrationsQueryV1,
    BuildAggregateRolePlanQueryV1,
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    GetRoleRuntimeStatusQueryV1,
    IRoleRuntime,
    RoleExecutionResultV1,
    RoleRuntimeError,
    RoleTaskCompletedEventV1,
    RoleTaskStartedEventV1,
    StandardStreamEvent,
    StreamTurnOptions,
    _normalize_chat_messages,
)
from polaris.cells.roles.runtime.public.contracts._object_contracts import (
    AssembleRoleRuntimeChainCommandV1,
    ExecuteRoleCapabilityInvocationCommandV1,
    ExecuteRoleTaskMarketLifecycleCommandV1,
    InstantiateRoleRuntimeObjectCommandV1,
    RehydrateRoleHandoffCommandV1,
    RoleAssetMount,
    RoleAssetMountTable,
    RoleAssetRef,
    RoleCapabilityDecision,
    RoleCapabilityDescriptor,
    RoleCapabilityFingerprint,
    RoleCapabilityInvocation,
    RoleCapabilityInvocationResultV1,
    RoleCapabilityPorts,
    RoleHandoffRehydrationResultV1,
    RoleIdentity,
    RoleLedgerBinding,
    RoleProfileBinding,
    RoleRuntimeChainAssemblyResultV1,
    RoleRuntimeChainEnvelope,
    RoleRuntimeChainStepRef,
    RoleRuntimeObject,
    RoleRuntimeObjectResultV1,
    RoleRuntimeObjectSpec,
    RoleStateCommitReceipt,
    RoleStateCommitRequest,
    RoleTaskMarketBinding,
    RoleTaskMarketLifecycleResultV1,
    RoleTurnContext,
    RoleTurnEnvelope,
    _capability_required_asset_mount_names,
    _compute_role_capability_fingerprint,
    _derive_role_turn_context,
    _require_capability_ports_allow_role,
)

# Backward-compatible re-export of the foundation validation helpers.
# These were top-level attributes of the former ``contracts`` module; the
# re-export keeps them importable from this exact path so the public
# attribute surface stays lossless after the module->package split.
from polaris.cells.roles.runtime.public.contracts._validation import (
    _FORBIDDEN_ROLE_OBJECT_OWNER_CELLS,
    _TASK_MARKET_TASK_REF_PREFIX,
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
    _normalize_history,
    _normalize_optional_domain,
    _normalize_optional_string,
    _normalize_scope_path,
    _normalize_string_tuple,
    _normalize_unique_string_tuple,
    _path_is_within_scope,
    _require_non_empty,
    _require_ref_superset,
    _require_refs_namespace,
    _to_dict_copy,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.roles.shared_contracts import (
    AgentMessage,
    AgentStatus,
    MessageType,
    register_protocol_fsm_factory,
)

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
    "RehydrateRoleHandoffCommandV1",
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
    "RoleHandoffRehydrationResultV1",
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
    "TaskRuntimeExecutionAttemptIdentityV1",
    "create_protocol_fsm",
    "get_builtin_role_runtime_spec",
]


register_protocol_fsm_factory(create_protocol_fsm)
