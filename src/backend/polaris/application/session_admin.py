"""Application-layer facade for session lifecycle administration.

This module provides a thin application-layer facade that wraps the
``roles.session`` Cell's public contracts and services so that the
delivery layer (CLI, HTTP) never needs to import Cell internals
directly.

Call chain::

    delivery -> application.session_admin -> cells.roles.session.public

Public surface exposed here:

- ``SessionAdminService``: stateless facade for creating, listing,
  retrieving, and updating role sessions.
- ``SessionSummary``: lightweight, frozen snapshot of a session
  returned to delivery. Delivery never receives the internal ORM
  ``Conversation`` object.
- ``SessionListResult``: paginated list result container.
- ``ISessionServiceFactory``: Protocol so delivery can swap in a
  test double without touching Cell internals.

Architecture constraints (AGENTS.md):

- This module imports ONLY from Cell ``public/`` boundaries.  It
  NEVER imports from ``internal/`` at module level.
- No business logic lives here; the facade delegates everything.
- All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.session.public.contracts import (
    CreateRoleSessionCommandV1,
    RoleSessionError,
    SessionState,
    UpdateRoleSessionCommandV1,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ISessionServiceFactory",
    "SessionAdminError",
    "SessionAdminService",
    "SessionListResult",
    "SessionSummary",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SessionAdminError(RuntimeError):
    """Application-layer error for session administration operations.

    Wraps lower-level Cell errors so delivery never catches Cell-specific
    exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "session_admin_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Value objects returned to delivery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Lightweight, frozen snapshot of a role session.

    Delivery receives this instead of the internal ORM model.
    """

    session_id: str
    role: str
    state: str
    host_kind: str
    session_type: str
    attachment_mode: str
    workspace: str | None = None
    title: str | None = None
    context_config: Mapping[str, Any] = field(default_factory=dict)
    capability_profile: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class SessionListResult:
    """Paginated list of session summaries."""

    items: tuple[SessionSummary, ...]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ISessionServiceFactory(Protocol):
    """Factory protocol for obtaining session-service instances.

    This allows delivery-layer tests to substitute a lightweight fake
    without importing any Cell internals.
    """

    def create_session_service(
        self,
        *,
        workspace: str | None = None,
    ) -> Any:
        """Create and return a session-service instance."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _conversation_to_summary(conversation: Any) -> SessionSummary:
    """Map an internal ``Conversation`` ORM object to a ``SessionSummary``.

    This is the *only* place in this module that touches the shape of
    the internal model.  If the ORM schema changes, only this function
    needs updating.
    """
    import json as _json

    def _safe_json_loads(raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return dict(_json.loads(str(raw)))
        except (ValueError, TypeError):
            return {}

    return SessionSummary(
        session_id=str(getattr(conversation, "id", "")),
        role=str(getattr(conversation, "role", "")),
        state=str(getattr(conversation, "state", SessionState.ACTIVE.value)),
        host_kind=str(getattr(conversation, "host_kind", "")),
        session_type=str(getattr(conversation, "session_type", "")),
        attachment_mode=str(getattr(conversation, "attachment_mode", "")),
        workspace=getattr(conversation, "workspace", None),
        title=getattr(conversation, "title", None),
        context_config=_safe_json_loads(getattr(conversation, "context_config", None)),
        capability_profile=_safe_json_loads(
            getattr(conversation, "capability_profile", None),
        ),
        created_at=(
            str(getattr(conversation, "created_at", ""))
            if getattr(conversation, "created_at", None) is not None
            else None
        ),
        updated_at=(
            str(getattr(conversation, "updated_at", ""))
            if getattr(conversation, "updated_at", None) is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# SessionAdminService
# ---------------------------------------------------------------------------


class SessionAdminService:
    """Application-layer facade for role session lifecycle operations.

    This is the single entrypoint that delivery should use for:

    1. Creating a new role session.
    2. Retrieving a session by ID.
    3. Listing sessions with optional filters.
    4. Updating a session (title, state, config).

    The service is stateless and cheap to construct.  A fresh
    ``RoleSessionService`` is obtained per operation via lazy import so
    the Cell internal module stays out of the module-level dependency
    graph.
    """

    def __init__(
        self,
        *,
        workspace: str | None = None,
    ) -> None:
        self._workspace = workspace

    # -- lazy service resolution -------------------------------------------

    def _make_service(self) -> Any:
        """Lazily import and instantiate ``RoleSessionService``.

        The import is deferred so this facade module never pulls in
        SQLAlchemy or other heavy Cell internals at import time.

        Raises:
            SessionAdminError: if the service cannot be instantiated.
        """
        try:
            from polaris.cells.roles.session.internal.role_session_service import (
                RoleSessionService,
            )

            return RoleSessionService(workspace=self._workspace)
        except (ImportError, RuntimeError, ValueError) as exc:
            raise SessionAdminError(
                "Failed to instantiate RoleSessionService",
                code="service_resolution_error",
                cause=exc,
            ) from exc

    # -- create ------------------------------------------------------------

    def create_session(
        self,
        command: CreateRoleSessionCommandV1,
    ) -> SessionSummary:
        """Create a new role session.

        Args:
            command: Validated creation command from the delivery layer.

        Returns:
            ``SessionSummary`` snapshot of the newly created session.

        Raises:
            SessionAdminError: if session creation fails.
        """
        svc = self._make_service()
        try:
            conversation = svc.create_session(
                role=command.role,
                host_kind=command.host_kind,
                workspace=command.workspace or self._workspace,
                session_type=command.session_type,
                attachment_mode=command.attachment_mode,
                title=command.title,
                context_config=dict(command.context_config) if command.context_config else None,
                capability_profile=(dict(command.capability_profile) if command.capability_profile else None),
            )
            return _conversation_to_summary(conversation)
        except RoleSessionError as exc:
            raise SessionAdminError(
                str(exc),
                code=getattr(exc, "code", "session_create_error"),
                cause=exc,
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise SessionAdminError(
                f"Unexpected error creating session: {exc}",
                code="session_create_unexpected",
                cause=exc,
            ) from exc
        finally:
            _close_svc(svc)

    # -- get by id ---------------------------------------------------------

    def get_session(self, session_id: str) -> SessionSummary | None:
        """Retrieve a single session by ID.

        Args:
            session_id: The unique session identifier.

        Returns:
            ``SessionSummary`` if found, ``None`` otherwise.

        Raises:
            SessionAdminError: if the underlying service call fails.
        """
        if not session_id or not session_id.strip():
            raise SessionAdminError(
                "session_id must be a non-empty string",
                code="invalid_session_id",
            )

        svc = self._make_service()
        try:
            conversation = svc.get_session(session_id.strip())
            if conversation is None:
                return None
            return _conversation_to_summary(conversation)
        except RoleSessionError as exc:
            raise SessionAdminError(
                str(exc),
                code=getattr(exc, "code", "session_get_error"),
                cause=exc,
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise SessionAdminError(
                f"Unexpected error retrieving session {session_id}: {exc}",
                code="session_get_unexpected",
                cause=exc,
            ) from exc
        finally:
            _close_svc(svc)

    # -- list --------------------------------------------------------------

    def list_sessions(
        self,
        *,
        role: str | None = None,
        host_kind: str | None = None,
        workspace: str | None = None,
        session_type: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SessionListResult:
        """List sessions with optional filters.

        Args:
            role: Filter by role identifier.
            host_kind: Filter by host kind.
            workspace: Filter by workspace path.
            session_type: Filter by session type.
            state: Filter by session state (``SessionState`` value).
            limit: Maximum number of results (clamped to 1..200).
            offset: Pagination offset (min 0).

        Returns:
            ``SessionListResult`` with matching sessions.

        Raises:
            SessionAdminError: if the query fails.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        svc = self._make_service()
        try:
            conversations = svc.get_sessions(
                role=role,
                host_kind=host_kind,
                workspace=workspace,
                session_type=session_type,
                state=state,
                limit=limit,
                offset=offset,
            )
            summaries = tuple(_conversation_to_summary(c) for c in conversations)
            return SessionListResult(
                items=summaries,
                total=len(summaries),
                limit=limit,
                offset=offset,
            )
        except RoleSessionError as exc:
            raise SessionAdminError(
                str(exc),
                code=getattr(exc, "code", "session_list_error"),
                cause=exc,
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise SessionAdminError(
                f"Unexpected error listing sessions: {exc}",
                code="session_list_unexpected",
                cause=exc,
            ) from exc
        finally:
            _close_svc(svc)

    # -- update ------------------------------------------------------------

    def update_session(
        self,
        command: UpdateRoleSessionCommandV1,
    ) -> SessionSummary | None:
        """Update an existing session.

        Args:
            command: Validated update command from the delivery layer.

        Returns:
            Updated ``SessionSummary`` if the session was found and
            updated, ``None`` if the session does not exist.

        Raises:
            SessionAdminError: if the update fails.
        """
        svc = self._make_service()
        try:
            conversation = svc.update_session(
                session_id=command.session_id,
                title=command.title,
                context_config=(dict(command.context_config) if command.context_config else None),
                capability_profile=(dict(command.capability_profile) if command.capability_profile else None),
                state=command.state,
            )
            if conversation is None:
                return None
            return _conversation_to_summary(conversation)
        except RoleSessionError as exc:
            raise SessionAdminError(
                str(exc),
                code=getattr(exc, "code", "session_update_error"),
                cause=exc,
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise SessionAdminError(
                f"Unexpected error updating session {command.session_id}: {exc}",
                code="session_update_unexpected",
                cause=exc,
            ) from exc
        finally:
            _close_svc(svc)

    # -- convenience builders ----------------------------------------------

    @staticmethod
    def build_create_command(
        *,
        role: str,
        workspace: str | None = None,
        host_kind: str = "electron_workbench",
        session_type: str = "workbench",
        attachment_mode: str = "isolated",
        title: str | None = None,
        context_config: Mapping[str, Any] | None = None,
        capability_profile: Mapping[str, Any] | None = None,
    ) -> CreateRoleSessionCommandV1:
        """Build a ``CreateRoleSessionCommandV1`` from delivery parameters.

        This is a convenience factory so delivery does not need to import
        the contract dataclass directly.
        """
        return CreateRoleSessionCommandV1(
            role=role,
            workspace=workspace,
            host_kind=host_kind,
            session_type=session_type,
            attachment_mode=attachment_mode,
            title=title,
            context_config=dict(context_config or {}),
            capability_profile=dict(capability_profile or {}),
        )

    @staticmethod
    def build_update_command(
        *,
        session_id: str,
        title: str | None = None,
        context_config: Mapping[str, Any] | None = None,
        capability_profile: Mapping[str, Any] | None = None,
        state: str | None = None,
    ) -> UpdateRoleSessionCommandV1:
        """Build an ``UpdateRoleSessionCommandV1`` from delivery parameters.

        This is a convenience factory so delivery does not need to import
        the contract dataclass directly.
        """
        return UpdateRoleSessionCommandV1(
            session_id=session_id,
            title=title,
            context_config=dict(context_config) if context_config is not None else None,
            capability_profile=(dict(capability_profile) if capability_profile is not None else None),
            state=state,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _close_svc(svc: Any) -> None:
    """Safely close a ``RoleSessionService`` if it has a ``close`` method."""
    close_fn = getattr(svc, "close", None)
    if close_fn is not None:
        try:
            close_fn()
        except (RuntimeError, ValueError):
            logger.debug("Failed to close session service", exc_info=True)
