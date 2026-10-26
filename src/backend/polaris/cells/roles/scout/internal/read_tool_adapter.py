"""Real ReadToolPort: execute read tools via the canonical executor (UTF-8).

Read-only is enforced here: only tools whose ToolSpec category is ``read`` are
allowed through this adapter. Execution itself goes through the canonical
``AgentAccelToolExecutor`` (the same path the roles use), which loads its own
handler modules — we do NOT depend on ``ToolSpec.handler_module`` (empty for
builtin tools).

Authority
---------
The adapter NEVER invents read authority. When the host supplies an authority,
the executor it configures carries the host's committed token and the host's
tool whitelist VERBATIM, so the canonical read-scope gate applies exactly the
host's scope:

* a committed scope stays committed and is enforced by the canonical gate;
* an explicitly empty scope stays empty and fails closed there;
* a missing scope key keeps the legacy undefined scope.

The host's own executor is never closed by this adapter — the host owns its
lifecycle. Without an authority the adapter keeps the legacy behaviour of
owning an unscoped executor, reachable only by direct API callers that never
had a committed scope.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

if TYPE_CHECKING:
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1


class ReadOnlyViolationError(RuntimeError):
    """Raised when a non-read tool is requested through the scout adapter."""


# Public alias used in tests and plan code (ruff N818 requires *Error suffix on the canonical class).
ReadOnlyViolation = ReadOnlyViolationError


@runtime_checkable
class _ReadExecutor(Protocol):
    """The minimal executor surface this adapter uses."""

    def execute(self, tool_name: str, arguments: Any) -> dict[str, Any]: ...


class RegistryReadTool:
    """ReadToolPort backed by the canonical AgentAccelToolExecutor.

    The read-only gate is enforced against ``ToolSpecRegistry`` before any
    execution; the executor then runs the tool against the workspace. A single
    executor instance is reused across calls and closed via :meth:`close` —
    but only when this adapter owns it. A borrowed host executor outlives the
    adapter and is left untouched by :meth:`close`.
    """

    def __init__(
        self,
        workspace: str,
        timeout: int = 30,
        *,
        authority: ScoutReadAuthorityV1 | None = None,
    ) -> None:
        self._workspace = str(workspace or ".")
        self._timeout = int(timeout)
        self._authority = authority
        self._executor: _ReadExecutor | None = None
        self._owns_executor = False

    def _ensure_executor(self) -> _ReadExecutor:
        if self._executor is not None:
            return self._executor
        borrowed = self._borrow_executor()
        if borrowed is not None:
            self._executor = borrowed
            self._owns_executor = False
            return borrowed
        self._executor = self._build_owned_executor()
        self._owns_executor = True
        return self._executor

    def _build_owned_executor(self) -> AgentAccelToolExecutor:
        """Create the executor this adapter owns, carrying the host authority.

        The host's committed token and tool whitelist are copied verbatim — the
        canonical read-scope gate then applies the host's scope unchanged.
        """
        authority = self._authority
        if authority is None:
            return AgentAccelToolExecutor(self._workspace)
        return AgentAccelToolExecutor(
            self._workspace,
            allowed_tools=authority.allowed_tools,
            capability_token=dict(authority.capability_token),
        )

    def _borrow_executor(self) -> _ReadExecutor | None:
        """Adopt the host's executor for the lifetime of this adapter."""
        authority = self._authority
        if authority is None:
            return None
        borrowed: object = getattr(authority, "borrowed_executor", None)
        if borrowed is None or not hasattr(borrowed, "execute"):
            return None
        return cast(_ReadExecutor, borrowed)

    def _host_authorization_denial(self, tool: str, args: dict[str, Any]) -> str | None:
        """Ask the HOST whether this nested read is allowed. None means allowed.

        The executor-level whitelist is not the host's whole policy: it does
        not carry the host's BLACKLIST, nor the host's per-call checks. The
        Scout composite therefore asks the host's own authorizer for every
        nested read. Without an authorizer the read still runs, and the
        executor applies its own gate — but the caller is then responsible for
        supplying one, because a nested read is not a model-visible dispatch
        the host would otherwise have checked.
        """
        authority = self._authority
        if authority is None:
            return None
        authorizer = authority.tool_authorizer
        if authorizer is None:
            return None
        allowed, reason = authorizer(tool, dict(args))
        if allowed:
            return None
        return f"tool '{tool}' denied by the host tool policy: {reason}"

    def run(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a read tool and return a normalized, flat payload.

        Returns ``{"ok": True, **payload}`` on success (so ``results``/``hits``/
        ``entries``/``symbols`` are top-level), or ``{"ok": False, "error": ...}``
        on failure. Raises :class:`ReadOnlyViolationError` for non-read tools.
        """
        spec = ToolSpecRegistry.get(tool)
        if spec is None:
            raise ReadOnlyViolationError(f"unknown tool: {tool}")
        if not spec.is_read_tool():
            raise ReadOnlyViolationError(f"tool {tool!r} is not a read tool (categories={spec.categories})")

        denial = self._host_authorization_denial(tool, args)
        if denial is not None:
            return {"ok": False, "error": denial}

        executor = self._ensure_executor()
        try:
            outcome = executor.execute(tool, dict(args))
        except Exception as exc:  # noqa: BLE001 - fail-soft boundary for one read tool
            # A single buggy/unavailable handler must not abort the whole probe.
            # The executor normally returns error dicts, but handlers can still
            # raise (e.g. backend/library incompatibilities); contain it here.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if not outcome.get("ok", False):
            return {"ok": False, "error": outcome.get("error") or "tool execution failed"}

        payload = outcome.get("result")
        if isinstance(payload, dict):
            return {"ok": True, **payload}
        # Some handlers return their fields at the top level (no nested "result").
        flat = {k: v for k, v in outcome.items() if k != "ok"}
        return {"ok": True, **flat}

    def close(self) -> None:
        """Release the underlying executor, if one was created by this adapter.

        A borrowed host executor is NOT closed here: the host owns its
        lifecycle and may still need it.
        """
        executor = self._executor
        owns_executor = self._owns_executor
        self._executor = None
        self._owns_executor = False
        if executor is None or not owns_executor:
            return
        close_sync = getattr(executor, "close_sync", None)
        if not callable(close_sync):
            return
        # Releasing resources must not raise: a close failure cannot make an
        # otherwise completed read look failed.
        with contextlib.suppress(Exception):
            close_sync()
