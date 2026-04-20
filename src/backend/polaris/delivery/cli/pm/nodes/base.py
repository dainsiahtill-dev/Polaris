"""Base class for role nodes.

Provides common functionality for all role implementations.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.pm.nodes.protocols import (
    RoleContext,
    RoleResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class BaseRoleNode(ABC):
    """Base class for all role nodes.

    Provides common functionality like timing, error handling,
    and result construction.
    """

    def __init__(self) -> None:
        self._start_time: float = 0
        self._end_time: float = 0

    @property
    @abstractmethod
    def role_name(self) -> str:
        """Return the name of the role."""
        ...

    def can_handle(self, context: RoleContext) -> bool:
        """Default implementation: can always handle if triggered."""
        return True

    @abstractmethod
    def _execute_impl(self, context: RoleContext) -> RoleResult:
        """Implement the actual role logic.

        Args:
            context: The execution context

        Returns:
            The role result
        """
        ...

    def execute(self, context: RoleContext) -> RoleResult:
        """Execute the role with common pre/post processing.

        This method handles timing, error catching, and common
        result construction. Subclasses should implement _execute_impl.
        """
        self._start_time = time.time()

        try:
            result = self._execute_impl(context)
            self._end_time = time.time()
            result.duration_ms = int((self._end_time - self._start_time) * 1000)
            return result

        except (RuntimeError, ValueError) as e:
            self._end_time = time.time()
            return RoleResult(
                success=False,
                exit_code=1,
                error=str(e),
                error_code="ROLE_EXECUTION_ERROR",
                duration_ms=int((self._end_time - self._start_time) * 1000),
            )

    def get_dependencies(self) -> list[str]:
        """Default: no dependencies."""
        return []

    def get_trigger_conditions(self) -> list[str]:
        """Default: respond to any trigger."""
        return ["*"]

    def _create_result(
        self,
        *,
        success: bool = True,
        exit_code: int = 0,
        tasks: list[dict[str, Any]] | None = None,
        contract: dict[str, Any] | None = None,
        blueprint: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
        status_updates: dict[str, str] | None = None,
        error: str = "",
        error_code: str = "",
        warnings: list[str] | None = None,
        next_role: str = "",
        continue_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RoleResult:
        """Helper to create a RoleResult with defaults."""
        return RoleResult(
            success=success,
            exit_code=exit_code,
            tasks=tasks or [],
            contract=contract,
            blueprint=blueprint,
            report=report,
            status_updates=status_updates or {},
            error=error,
            error_code=error_code,
            warnings=warnings or [],
            next_role=next_role,
            continue_reason=continue_reason,
            metadata=metadata or {},
        )

    def _create_error_result(
        self,
        error: str,
        error_code: str = "ROLE_ERROR",
        exit_code: int = 1,
    ) -> RoleResult:
        """Helper to create an error RoleResult."""
        return RoleResult(
            success=False,
            exit_code=exit_code,
            error=error,
            error_code=error_code,
        )

    def _create_success_result(
        self,
        *,
        tasks: list[dict[str, Any]] | None = None,
        next_role: str = "",
        continue_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RoleResult:
        """Helper to create a success RoleResult."""
        return RoleResult(
            success=True,
            exit_code=0,
            tasks=tasks or [],
            next_role=next_role,
            continue_reason=continue_reason,
            metadata=metadata or {},
        )


class SequentialRoleNode(BaseRoleNode):
    """A role node that executes a sequence of sub-operations.

    Useful for roles that need to run multiple steps in order.
    """

    def __init__(self) -> None:
        super().__init__()
        self._steps: list[tuple[str, Callable[..., Any]]] = []

    def add_step(self, name: str, handler: Callable[..., Any]) -> None:
        """Add a step to the execution sequence.

        Args:
            name: Step name for logging
            handler: Callable that takes context and returns result
        """
        self._steps.append((name, handler))

    def _execute_impl(self, context: RoleContext) -> RoleResult:
        """Execute steps sequentially."""
        current_context = context
        all_tasks: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        metadata: dict[str, Any] = {"steps": []}

        for step_name, handler in self._steps:
            step_start = time.time()
            try:
                step_result = handler(current_context)

                if isinstance(step_result, RoleResult):
                    if not step_result.success:
                        return step_result

                    # Collect outputs
                    if step_result.tasks:
                        all_tasks.extend(step_result.tasks)
                    if step_result.warnings:
                        all_warnings.extend(step_result.warnings)
                    if step_result.metadata:
                        metadata["steps"].append(
                            {
                                "name": step_name,
                                "success": True,
                                "duration_ms": int((time.time() - step_start) * 1000),
                            }
                        )
                else:
                    metadata["steps"].append(
                        {
                            "name": step_name,
                            "success": False,
                            "error": "Step did not return RoleResult",
                            "duration_ms": int((time.time() - step_start) * 1000),
                        }
                    )

            except (RuntimeError, ValueError) as e:
                metadata["steps"].append(
                    {
                        "name": step_name,
                        "success": False,
                        "error": str(e),
                        "duration_ms": int((time.time() - step_start) * 1000),
                    }
                )
                return self._create_error_result(
                    error=f"Step '{step_name}' failed: {e}",
                    error_code="STEP_EXECUTION_ERROR",
                )

        return self._create_success_result(
            tasks=all_tasks,
            metadata=metadata,
        )


__all__ = [
    "BaseRoleNode",
    "SequentialRoleNode",
]
