"""Orchestration coordinator for Polaris.

This module provides the main coordinator that orchestrates all role nodes
(PM, ChiefEngineer, Director, QA) in a decoupled manner.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from polaris.delivery.cli.pm.nodes.protocols import (
    OrchestrationConfig,
    OrchestrationState,
    RoleContext,
    RoleNode,
    RoleResult,
)

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

# Trigger constants
TRIGGER_INIT = "init"
TRIGGER_PM_COMPLETE = "pm_complete"
TRIGGER_CE_COMPLETE = "ce_complete"
TRIGGER_DIRECTOR_COMPLETE = "director_complete"
TRIGGER_QA_COMPLETE = "qa_complete"
TRIGGER_MANUAL = "manual"
TRIGGER_ITERATION = "iteration"


class OrchestrationCoordinator:
    """Main coordinator for Polaris role orchestration.

    This coordinator manages the execution flow between role nodes,
    maintaining state and handling transitions between phases.
    """

    # Default role execution order
    DEFAULT_ROLE_SEQUENCE = [
        "PM",
        "ChiefEngineer",
        "Director",
        "QA",
    ]

    def __init__(
        self,
        workspace: str,
        config: OrchestrationConfig | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config or OrchestrationConfig()
        self._nodes: dict[str, RoleNode] = {}
        self._state = OrchestrationState()
        self._initialized = False

    @property
    def state(self) -> OrchestrationState:
        """Get current orchestration state."""
        return self._state

    def register_node(self, node: RoleNode) -> None:
        """Register a role node with the coordinator.

        Args:
            node: A role node implementation
        """
        self._nodes[node.role_name] = node

    def register_nodes(self, *nodes: RoleNode) -> None:
        """Register multiple role nodes at once.

        Args:
            nodes: Role node implementations
        """
        for node in nodes:
            self.register_node(node)

    def get_node(self, role_name: str) -> RoleNode | None:
        """Get a registered role node by name.

        Args:
            role_name: Name of the role

        Returns:
            The role node or None if not registered
        """
        return self._nodes.get(role_name)

    def initialize(self) -> bool:
        """Initialize the coordinator and all registered nodes.

        Returns:
            True if initialization succeeded
        """
        if self._initialized:
            return True

        # Initialize all registered nodes
        for name, node in self._nodes.items():
            try:
                if hasattr(node, "initialize"):
                    node.initialize()
            except (RuntimeError, ValueError) as e:
                logger.error("[coordinator] Failed to initialize node %s: %s", name, e)
                return False

        self._initialized = True
        self._state.phase = "initialized"
        return True

    def run_iteration(
        self,
        args: argparse.Namespace,
        iteration: int = 1,
    ) -> int:
        """Run a complete orchestration iteration.

        This is the main entry point for executing a full cycle
        of PM -> ChiefEngineer -> Director -> QA.

        Args:
            args: Command line arguments
            iteration: Current iteration number

        Returns:
            Exit code (0 = success, non-zero = failure)
        """
        if not self._initialized and not self.initialize():
            return 1

        run_id = f"pm-{iteration:05d}"
        self._state.run_id = run_id
        self._state.iteration = iteration
        self._state.phase = "started"

        # Build initial context
        context = self._build_initial_context(args, iteration, run_id)

        # Execute PM first
        pm_node = self._nodes.get("PM")
        if not pm_node:
            logger.error("[coordinator] ERROR: PM node not registered")
            return 1

        # Run PM
        self._state.current_role = "PM"
        pm_result = pm_node.execute(context)
        context.pm_result = pm_result.to_dict() if pm_result else None

        if not pm_result or not pm_result.success:
            logger.error("[coordinator] PM failed: %s", pm_result.error if pm_result else "unknown")
            self._state.phase = "failed"
            return pm_result.exit_code if pm_result else 1

        # Transition to ChiefEngineer if enabled
        if self.config.enable_chief_engineer:
            ce_node = self._nodes.get("ChiefEngineer")
            if ce_node:
                self._state.phase = "chief_engineer"
                self._state.current_role = "ChiefEngineer"

                ce_result = ce_node.execute(context)
                context.chief_engineer_result = ce_result.to_dict() if ce_result else None

                # Update tasks with CE output
                if ce_result and ce_result.tasks:
                    context.last_tasks = ce_result.tasks
            else:
                logger.warning("[coordinator] WARNING: ChiefEngineer node not registered")

        # Run Director dispatch (via PolarisEngine)
        director_node = self._nodes.get("Director")
        if director_node:
            self._state.phase = "dispatching"
            self._state.current_role = "Director"

            director_result = director_node.execute(context)
            context.director_result = director_result.to_dict() if director_result else None

            # Update tasks with Director output
            if director_result and director_result.tasks:
                context.last_tasks = director_result.tasks
        else:
            logger.warning("[coordinator] WARNING: Director node not registered")

        # Run Integration QA if enabled
        if self.config.enable_integration_qa:
            qa_node = self._nodes.get("QA")
            if qa_node:
                self._state.phase = "qa"
                self._state.current_role = "QA"

                qa_result = qa_node.execute(context)
                context.qa_result = qa_result.to_dict() if qa_result else None
            else:
                logger.warning("[coordinator] WARNING: QA node not registered")

        # Finalize
        self._state.phase = "completed"
        self._state.current_role = ""

        return 0

    def dispatch_role(
        self,
        role_name: str,
        context: RoleContext,
        trigger: str = TRIGGER_MANUAL,
    ) -> RoleResult:
        """Dispatch a specific role to execute.

        This can be used to trigger a role independently,
        not as part of the full iteration flow.

        Args:
            role_name: Name of the role to dispatch
            context: Execution context
            trigger: What triggered this dispatch

        Returns:
            The role's execution result
        """
        node = self._nodes.get(role_name)
        if not node:
            return RoleResult(
                success=False,
                error=f"Role node '{role_name}' not registered",
                error_code="ROLE_NOT_FOUND",
            )

        # Check dependencies
        deps = node.get_dependencies()
        for dep in deps:
            if not self._state.is_role_completed(dep):
                return RoleResult(
                    success=False,
                    error=f"Dependency '{dep}' not completed",
                    error_code="DEPENDENCY_NOT_MET",
                )

        # Execute
        self._state.current_role = role_name
        context.trigger = trigger
        context.trigger_source = "coordinator"

        result = node.execute(context)

        # Update state
        if result.success:
            self._state.completed_roles.append(role_name)

        return result

    def can_dispatch(self, role_name: str) -> bool:
        """Check if a role can be dispatched.

        Args:
            role_name: Name of the role

        Returns:
            True if the role can be executed
        """
        node = self._nodes.get(role_name)
        if not node:
            return False

        # Check dependencies
        deps = node.get_dependencies()
        return all(self._state.is_role_completed(dep) for dep in deps)

    def get_pending_roles(self) -> list[str]:
        """Get list of roles that can be executed next.

        Returns:
            List of role names
        """
        pending = []
        for name, node in self._nodes.items():
            if name in self._state.completed_roles:
                continue
            if self.can_dispatch(name):
                pending.append(name)
        return pending

    def reset(self) -> None:
        """Reset the coordinator state for a new run."""
        self._state = OrchestrationState()
        self._initialized = False

    def _build_initial_context(
        self,
        args: argparse.Namespace,
        iteration: int,
        run_id: str,
    ) -> RoleContext:
        """Build the initial context from args and workspace."""
        from polaris.delivery.cli.pm.orchestration_core import load_state_and_context

        # Get cache root
        ramdisk_root = getattr(args, "ramdisk_root", None)
        cache_root = ""
        try:
            from polaris.infrastructure.compat.io_utils import build_cache_root, resolve_ramdisk_root

            ramdisk = resolve_ramdisk_root(ramdisk_root)
            cache_root = build_cache_root(ramdisk, self.workspace) or ""
        except (RuntimeError, ValueError):
            logger.debug("DEBUG: coordinator.py:{318} {exc} (swallowed)")

        # Load context from orchestration core
        try:
            context_data = load_state_and_context(
                self.workspace,
                cache_root,
                args,
                iteration,
            )
        except (RuntimeError, ValueError) as e:
            logger.warning("[coordinator] Warning: failed to load context: %s", e)
            context_data = {}

        # Build RoleContext
        return RoleContext(
            workspace_full=self.workspace,
            cache_root_full=cache_root,
            run_id=run_id,
            pm_iteration=iteration,
            requirements=context_data.get("requirements", ""),
            plan_text=context_data.get("plan_text", ""),
            gap_report=context_data.get("gap_report", ""),
            last_qa=context_data.get("last_qa", ""),
            last_tasks=context_data.get("last_tasks", []),
            pm_state=context_data.get("pm_state", {}),
            args=args,
            events_path=context_data.get("events_path", ""),
            dialogue_path=context_data.get("dialogue_path", ""),
        )


def create_coordinator(
    workspace: str,
    config: OrchestrationConfig | None = None,
) -> OrchestrationCoordinator:
    """Factory function to create a fully configured coordinator.

    Args:
        workspace: Workspace path
        config: Optional configuration

    Returns:
        Configured OrchestrationCoordinator
    """
    coordinator = OrchestrationCoordinator(workspace, config)

    # Register PM node
    try:
        from polaris.delivery.cli.pm.nodes.pm_node import PMNode

        coordinator.register_node(PMNode())
    except ImportError as e:
        logger.warning("[coordinator] PM node not available: %s", e)

    # Register ChiefEngineer node
    try:
        from polaris.delivery.cli.pm.nodes.chief_engineer_node import ChiefEngineerNode

        coordinator.register_node(ChiefEngineerNode())
    except ImportError as e:
        logger.warning("[coordinator] ChiefEngineer node not available: %s", e)

    # Register Director node
    try:
        from polaris.delivery.cli.pm.nodes.director_node import DirectorNode

        coordinator.register_node(DirectorNode())
    except ImportError as e:
        logger.warning("[coordinator] Director node not available: %s", e)

    # Register QA node
    try:
        from polaris.delivery.cli.pm.nodes.qa_node import QANode

        coordinator.register_node(QANode())
    except ImportError as e:
        logger.warning("[coordinator] QA node not available: %s", e)

    return coordinator


__all__ = [
    "TRIGGER_CE_COMPLETE",
    "TRIGGER_DIRECTOR_COMPLETE",
    "TRIGGER_INIT",
    "TRIGGER_ITERATION",
    "TRIGGER_MANUAL",
    "TRIGGER_PM_COMPLETE",
    "TRIGGER_QA_COMPLETE",
    "OrchestrationCoordinator",
    "create_coordinator",
]
