"""Role node protocols for Polaris orchestration.

Defines the interfaces that all role nodes must implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import argparse

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS


@runtime_checkable
class RoleNode(Protocol):
    """Protocol that all role nodes must implement.

    A role node is an independent execution unit that can be triggered
    by the orchestration coordinator. Each role has a specific responsibility
    in the project delivery workflow.
    """

    @property
    def role_name(self) -> str:
        """Return the name of the role (e.g., 'PM', 'ChiefEngineer')."""
        ...

    def can_handle(self, context: RoleContext) -> bool:
        """Check if this node can handle the given context.

        Args:
            context: The current execution context

        Returns:
            True if this node can process the context
        """
        ...

    def execute(self, context: RoleContext) -> RoleResult:
        """Execute the role's logic.

        Args:
            context: The current execution context

        Returns:
            RoleResult containing the execution outcome
        """
        ...

    def get_dependencies(self) -> list[str]:
        """Get list of roles that must complete before this role runs.

        Returns:
            List of role names that this role depends on
        """
        ...

    def get_trigger_conditions(self) -> list[str]:
        """Get the conditions that can trigger this role.

        Returns:
            List of trigger condition strings
        """
        ...


@dataclass
class RoleContext:
    """Context passed to role nodes during execution.

    This contains all the information a role needs to execute,
    including workspace path, arguments, previous results, etc.
    """

    # Workspace and paths
    workspace_full: str = ""
    cache_root_full: str = ""
    run_dir: str = ""
    run_id: str = ""
    pm_iteration: int = 1

    # Input artifacts
    requirements: str = ""
    plan_text: str = ""
    gap_report: str = ""
    last_qa: str = ""
    last_tasks: list[dict[str, Any]] = field(default_factory=list)

    # Previous role results
    pm_result: dict[str, Any] | None = None
    chief_engineer_result: dict[str, Any] | None = None
    director_result: dict[str, Any] | None = None
    qa_result: dict[str, Any] | None = None

    # Current state
    pm_state: dict[str, Any] = field(default_factory=dict)

    # Runtime
    args: argparse.Namespace | None = None
    events_path: str = ""
    dialogue_path: str = ""

    # Trigger info
    trigger: str = ""
    trigger_source: str = ""  # Which role/node triggered this

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Usage context for LLM calls
    usage_ctx: dict[str, Any] = field(default_factory=dict)

    def get_previous_result(self, role_name: str) -> dict[str, Any] | None:
        """Get the result from a previous role.

        Args:
            role_name: Name of the role to get result from

        Returns:
            The result dict or None if not available
        """
        role_lower = role_name.lower()
        return {
            "pm": self.pm_result,
            "chiefengineer": self.chief_engineer_result,
            "director": self.director_result,
            "qa": self.qa_result,
        }.get(role_lower)

    def get_tasks(self) -> list[dict[str, Any]]:
        """Get the current task list from available sources."""
        if self.last_tasks:
            return self.last_tasks
        pm_res = self.pm_result
        if pm_res and isinstance(pm_res, dict):
            tasks = pm_res.get("tasks")
            if isinstance(tasks, list):
                return tasks
        return []


@dataclass
class RoleResult:
    """Result returned by a role node after execution."""

    # Execution status
    success: bool = True
    exit_code: int = 0

    # Output artifacts
    tasks: list[dict[str, Any]] = field(default_factory=list)
    contract: dict[str, Any] | None = None
    blueprint: dict[str, Any] | None = None
    report: dict[str, Any] | None = None

    # Status updates for tasks
    status_updates: dict[str, str] = field(default_factory=dict)

    # Errors and warnings
    error: str = ""
    error_code: str = ""
    warnings: list[str] = field(default_factory=list)

    # Next steps
    next_role: str = ""  # Which role should run next
    continue_reason: str = ""

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Usage context for LLM calls
    usage_ctx: dict[str, Any] = field(default_factory=dict)

    # Execution metrics
    duration_ms: int = 0
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "tasks": self.tasks,
            "contract": self.contract,
            "blueprint": self.blueprint,
            "report": self.report,
            "status_updates": self.status_updates,
            "error": self.error,
            "error_code": self.error_code,
            "warnings": self.warnings,
            "next_role": self.next_role,
            "continue_reason": self.continue_reason,
            "metadata": self.metadata,
            "usage_ctx": self.usage_ctx,
            "duration_ms": self.duration_ms,
            "tokens_used": self.tokens_used,
        }


@dataclass
class OrchestrationState:
    """Current state of the orchestration coordinator."""

    # Phase tracking
    phase: str = "idle"  # idle, planning, chief_engineer, dispatching, director, qa, completed, failed

    # Role states
    role_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Execution tracking
    current_role: str = ""
    completed_roles: list[str] = field(default_factory=list)
    pending_roles: list[str] = field(default_factory=list)

    # Iteration info
    iteration: int = 0
    run_id: str = ""

    # Global state
    global_state: dict[str, Any] = field(default_factory=dict)

    def get_role_state(self, role_name: str) -> dict[str, Any]:
        """Get the state of a specific role."""
        return self.role_states.get(role_name, {})

    def set_role_state(self, role_name: str, state: dict[str, Any]) -> None:
        """Set the state of a specific role."""
        self.role_states[role_name] = state

    def is_role_completed(self, role_name: str) -> bool:
        """Check if a role has completed."""
        return role_name in self.completed_roles

    def is_role_running(self, role_name: str) -> bool:
        """Check if a role is currently running."""
        return self.current_role == role_name


@dataclass
class OrchestrationConfig:
    """Configuration for the orchestration coordinator."""

    # Execution modes
    director_execution_mode: str = "single"  # single, multi
    max_directors: int = 1
    scheduling_policy: str = "priority"  # fifo, priority, dag

    # Feature flags
    enable_chief_engineer: bool = True
    enable_integration_qa: bool = True
    enable_taskboard: bool = True

    # Retry settings
    max_retries: int = 3
    retry_delay_seconds: int = 5

    # Timeouts
    role_timeout_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> OrchestrationConfig:
        """Create config from command line arguments."""
        return cls(
            director_execution_mode=getattr(args, "director_execution_mode", "single"),
            max_directors=getattr(args, "max_directors", 1),
            scheduling_policy=getattr(args, "director_scheduling_policy", "priority"),
        )


__all__ = [
    "OrchestrationConfig",
    "OrchestrationState",
    "RoleContext",
    "RoleNode",
    "RoleResult",
]
