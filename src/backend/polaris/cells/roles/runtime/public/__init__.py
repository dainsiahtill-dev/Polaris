"""Public boundary for `roles.runtime` cell.

Keep package import lightweight by lazily loading contract/service exports.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AgentMessage",
    "AgentState",
    "AgentStatus",
    "BaseEngine",
    "ContextRequest",
    "ContextResult",
    "EngineBudget",
    "EngineContext",
    "EngineRegistry",
    "EngineResult",
    "EngineStatus",
    "EngineStrategy",
    "ExecuteRoleSessionCommandV1",
    "ExecuteRoleTaskCommandV1",
    "FailureClass",
    "GetRoleRuntimeStatusQueryV1",
    "HybridEngine",
    "IRoleRuntime",
    "MessageType",
    "PathSecurityError",
    "PlanSolveEngine",
    "PromptFingerprint",
    "ProtocolBus",
    "ProtocolFSM",
    "ReActEngine",
    "RetryHint",
    "RoleAgent",
    "RoleContextGateway",
    "RoleContextPolicy",
    "RoleDataPolicy",
    "RoleDataStore",
    "RoleDataStoreError",
    "RoleExecutionKernel",
    "RoleExecutionMode",
    "RoleExecutionResultV1",
    "RoleLibraryPolicy",
    "RoleProfile",
    "RoleProfileRegistry",
    "RolePromptPolicy",
    "RoleRuntimeError",
    "RoleRuntimeService",
    "RoleSessionOrchestrator",
    "RoleSkillManager",
    "RoleTaskCompletedEventV1",
    "RoleTaskStartedEventV1",
    "RoleToolGateway",
    "RoleToolPolicy",
    "RoleTurnRequest",
    "RoleTurnResult",
    "SeqEventType",
    "SeqProgressDetector",
    "SeqState",
    "SequentialBudget",
    "SequentialConfig",
    "SequentialEngine",
    "SequentialMode",
    "SequentialStateProxy",
    "SequentialStats",
    "SequentialStatsResult",
    "SequentialTraceLevel",
    "SkillLoader",
    "StepDecision",
    "StepResult",
    "StepStatus",
    "TaskClassifier",
    "TerminationReason",
    "ToTEngine",
    "ToolAuthorizationError",
    "Worker",
    "WorkerConfig",
    "WorkerPool",
    "WorkerResult",
    "WorkerState",
    "WorkerTask",
    "classify_task",
    "create_engine_budget",
    "create_protocol_bus",
    "create_protocol_fsm",
    "create_role_skill_manager",
    "create_sequential_budget",
    "create_skill_loader",
    "create_worker_pool",
    "emit_seq_event",
    "execute_role_session_command",
    "execute_role_task_command",
    "get_engine",
    "get_engine_registry",
    "get_hybrid_engine",
    "get_seq_emitter",
    "get_task_classifier",
    "load_core_roles",
    "profile_from_dict",
    "profile_to_dict",
    "query_role_runtime_status",
    "register_engine",
    "registry",
    "should_enable_sequential",
    "stream_role_session_command",
]

_CONTRACTS_MODULE = "polaris.cells.roles.runtime.public.contracts"
_SERVICE_MODULE = "polaris.cells.roles.runtime.public.service"
_CONTRACT_EXPORTS = frozenset(
    {
        "ExecuteRoleSessionCommandV1",
        "ExecuteRoleTaskCommandV1",
        "GetRoleRuntimeStatusQueryV1",
        "IRoleRuntime",
        "RoleExecutionResultV1",
        "RoleRuntimeError",
        "RoleTaskCompletedEventV1",
        "RoleTaskStartedEventV1",
    }
)


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name = _CONTRACTS_MODULE if name in _CONTRACT_EXPORTS else _SERVICE_MODULE
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
