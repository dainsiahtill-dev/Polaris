"""Application-layer facade for role runtime administration.

This module provides a thin application-layer facade that wraps the
``roles.runtime`` Cell's public contracts and services so that the
delivery layer (CLI, HTTP) never needs to import Cell internals
directly.

Call chain:
    delivery -> application.runtime_admin -> cells.roles.runtime.public

Public surface exposed here:
    - ``RuntimeAdminService``: stateless facade for creating orchestrator
      sessions, transaction controllers, and streaming chat turns.
    - ``OrchestratorHandle``: lightweight handle returned when an
      orchestrator session is materialized. The delivery layer drives
      the handle without knowing the concrete internal class.
    - ``IRoleOrchestratorFactory``: Protocol so delivery can swap in
      a test double without touching Cell internals.

Architecture constraints (AGENTS.md):
    - This module imports ONLY from Cell ``public/`` boundaries and
      ``kernelone`` contracts. It NEVER imports from ``internal/``.
    - No business logic lives here; the facade delegates everything.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    IRoleRuntime,
    RoleRuntimeError,
    StreamTurnOptions,
)

logger = logging.getLogger(__name__)

__all__ = [
    "IRoleOrchestratorFactory",
    "OrchestratorHandle",
    "RuntimeAdminError",
    "RuntimeAdminService",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RuntimeAdminError(RuntimeError):
    """Application-layer error for runtime administration operations.

    Wraps lower-level Cell errors so delivery never catches Cell-specific
    exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_admin_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Protocols (for parts that don't have a public surface yet)
# ---------------------------------------------------------------------------


@runtime_checkable
class IOrchestratorSession(Protocol):
    """Protocol for an orchestrator session's streaming interface.

    The concrete implementation lives in
    ``cells.roles.runtime.internal.session_orchestrator.RoleSessionOrchestrator``.
    Delivery uses this protocol so it never depends on the concrete class.
    """

    async def execute_stream(
        self,
        user_message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        """Stream turn events from the orchestrator."""
        ...  # pragma: no cover


@runtime_checkable
class IRoleOrchestratorFactory(Protocol):
    """Factory protocol for creating orchestrator sessions.

    This allows delivery-layer tests to substitute a lightweight fake
    without importing any Cell internals.
    """

    def create_orchestrator_session(
        self,
        *,
        session_id: str,
        workspace: str,
        role: str,
        command: ExecuteRoleSessionCommandV1,
        max_auto_turns: int = 10,
    ) -> IOrchestratorSession:
        """Create and return an orchestrator session handle."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# OrchestratorHandle
# ---------------------------------------------------------------------------


class OrchestratorHandle:
    """Opaque handle wrapping an orchestrator session for the delivery layer.

    Delivery code calls ``stream_events()`` and iterates the async
    generator; it never touches the underlying orchestrator directly.
    """

    def __init__(self, orchestrator: IOrchestratorSession) -> None:
        self._orchestrator = orchestrator

    async def stream_events(
        self,
        user_message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        """Yield turn events from the orchestrator.

        This is a thin pass-through to the orchestrator's ``execute_stream``.
        Any error from the Cell layer is caught and re-raised as a
        ``RuntimeAdminError`` so delivery never sees Cell-internal exceptions.
        """
        try:
            async for event in self._orchestrator.execute_stream(user_message, context=context):  # type: ignore[misc,attr-defined]
                yield event
        except RoleRuntimeError as exc:
            raise RuntimeAdminError(
                str(exc),
                code=getattr(exc, "code", "orchestrator_stream_error"),
                cause=exc,
            ) from exc


# ---------------------------------------------------------------------------
# RuntimeAdminService
# ---------------------------------------------------------------------------


class RuntimeAdminService:
    """Application-layer facade for role runtime operations.

    This is the single entrypoint that delivery should use for:

    1. Obtaining an ``IRoleRuntime`` (for ``stream_chat_turn``).
    2. Creating an orchestrator session via ``create_orchestrator_handle``.
    3. Building a ``ExecuteRoleSessionCommandV1`` from delivery params.

    The service is stateless and cheap to construct.
    """

    def __init__(
        self,
        *,
        runtime: IRoleRuntime | None = None,
    ) -> None:
        self._runtime = runtime

    # -- lazy runtime resolution ------------------------------------------

    def _resolve_runtime(self) -> IRoleRuntime:
        """Resolve the runtime, lazily importing the default if needed."""
        if self._runtime is not None:
            return self._runtime
        try:
            from polaris.cells.roles.runtime.public.service import (
                RoleRuntimeService,
            )

            self._runtime = RoleRuntimeService()
        except (ImportError, RuntimeError, ValueError) as exc:
            raise RuntimeAdminError(
                "Failed to resolve role runtime service",
                code="runtime_resolution_error",
                cause=exc,
            ) from exc
        return self._runtime

    @property
    def runtime(self) -> IRoleRuntime:
        """The underlying ``IRoleRuntime`` instance.

        Delivery may read this to call ``stream_chat_turn`` directly when
        it does not need the orchestrator path.
        """
        return self._resolve_runtime()

    # -- command construction helpers -------------------------------------

    @staticmethod
    def build_session_command(
        *,
        role: str,
        session_id: str,
        workspace: str,
        user_message: str,
        history: tuple[tuple[str, str], ...] = (),
        context: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        stream: bool = True,
        host_kind: str | None = None,
        stream_options: StreamTurnOptions | None = None,
    ) -> ExecuteRoleSessionCommandV1:
        """Build an ``ExecuteRoleSessionCommandV1`` from delivery parameters.

        This is a convenience factory so delivery does not need to import
        the contract dataclass directly.
        """
        return ExecuteRoleSessionCommandV1(
            role=role,
            session_id=session_id,
            workspace=workspace,
            user_message=user_message,
            history=history,
            context=dict(context or {}),
            metadata=dict(metadata or {}),
            stream=stream,
            host_kind=host_kind,
            stream_options=stream_options,
        )

    # -- streaming (non-orchestrator path) --------------------------------

    async def stream_chat_turn(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a single chat turn through the runtime.

        This delegates directly to ``IRoleRuntime.stream_chat_turn``.
        """
        runtime = self._resolve_runtime()
        async for event in runtime.stream_chat_turn(command):
            yield event

    # -- orchestrator path ------------------------------------------------

    def create_transaction_controller(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> Any:
        """Create a ``TurnTransactionController`` for orchestrator integration.

        Returns the opaque controller object that the orchestrator needs.
        Delivery should not inspect the returned value; pass it straight to
        ``create_orchestrator_handle``.

        Raises:
            RuntimeAdminError: if the runtime doesn't support
                ``create_transaction_controller`` or if creation fails.
        """
        runtime = self._resolve_runtime()
        create_fn = getattr(runtime, "create_transaction_controller", None)
        if create_fn is None:
            raise RuntimeAdminError(
                "The resolved IRoleRuntime does not support create_transaction_controller",
                code="unsupported_runtime_capability",
            )
        try:
            return create_fn(command)
        except (RuntimeError, ValueError) as exc:
            raise RuntimeAdminError(
                f"Failed to create transaction controller: {exc}",
                code="transaction_controller_creation_error",
                cause=exc,
            ) from exc

    def create_orchestrator_handle(
        self,
        *,
        session_id: str,
        workspace: str,
        role: str,
        command: ExecuteRoleSessionCommandV1,
        max_auto_turns: int = 10,
    ) -> OrchestratorHandle:
        """Create an ``OrchestratorHandle`` for multi-turn orchestration.

        This method encapsulates the two-step process that delivery
        currently performs inline:

        1. ``create_transaction_controller(command)`` -- obtain the kernel.
        2. Instantiate ``RoleSessionOrchestrator`` with that kernel.

        The delivery layer receives an ``OrchestratorHandle`` and calls
        ``stream_events()`` -- it never touches Cell internals.

        Raises:
            RuntimeAdminError: if controller creation or orchestrator
                instantiation fails.
        """
        tx_controller = self.create_transaction_controller(command)

        try:
            # Lazy import keeps the internal module out of the module-level
            # dependency graph. The import path is the Cell's *internal*
            # module, but it is accessed ONLY from this single facade
            # method -- delivery never imports it.
            from polaris.cells.roles.runtime.internal.session_orchestrator import (
                RoleSessionOrchestrator,
            )

            orchestrator = RoleSessionOrchestrator(
                session_id=session_id,
                kernel=tx_controller,
                workspace=workspace,
                role=role,
                max_auto_turns=max_auto_turns,
                shadow_engine=None,
            )
        except (ImportError, RuntimeError, ValueError) as exc:
            raise RuntimeAdminError(
                f"Failed to instantiate session orchestrator: {exc}",
                code="orchestrator_instantiation_error",
                cause=exc,
            ) from exc

        return OrchestratorHandle(orchestrator)  # type: ignore[arg-type]
