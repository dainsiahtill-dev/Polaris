"""LLMInvoker class assembly (mixins + construction)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.engine import AIExecutor

from ..event_emitter import LLMEventEmitter
from ..stream_engine import StreamEngine
from ..stream_handler import (
    normalize_stream_chunk,  # noqa: F401
)

if TYPE_CHECKING:
    pass

from ._binding import _InvokerBindingMixin
from ._call import _InvokerCallMixin
from ._stream import _InvokerStreamMixin
from ._structured import _InvokerStructuredMixin

logger = logging.getLogger(__name__)


class LLMInvoker(
    _InvokerBindingMixin,
    _InvokerCallMixin,
    _InvokerStructuredMixin,
    _InvokerStreamMixin,
):
    """Unified LLM invocation service.

    Consolidates functionality previously spread across call_sync.py,
    call_structured.py, and call_stream.py into a single service class.

    This class is the canonical role-kernel LLM invocation boundary.
    """

    __slots__ = (
        "_cache",
        "_enable_cache",
        "_event_emitter",
        "_executor",
        "_formatter",
        "_model_catalog",
        "_stream_engine",
        "workspace",
    )

    def __init__(
        self,
        workspace: str = "",
        enable_cache: bool = True,
        executor: Any | None = None,
    ) -> None:
        """Initialize the LLM invoker service.

        Args:
            workspace: Workspace path for context
            enable_cache: Whether to enable LLM response caching
            executor: Optional AIExecutor instance for DI (injected, not created inline).
                When provided, tests can inject mocks without patching.
                When None, creates a default AIExecutor instance.
        """
        self.workspace = workspace
        self._enable_cache = enable_cache
        self._cache = None  # Lazy load
        from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

        self._model_catalog = ModelCatalog(workspace=workspace or ".")
        self._formatter: Any = None  # ProviderFormatter for lazy serialization
        self._executor: Any | None = executor  # Injected executor for DI
        self._event_emitter = LLMEventEmitter(workspace=workspace)
        self._stream_engine = StreamEngine(
            workspace=workspace,
            get_executor=lambda: self._get_executor(),
            allow_native_tool_text_fallback_fn=lambda _ctx: False,
            emit_call_start_event=lambda **kwargs: self._emit_call_start_event(**kwargs),
            emit_call_error_event=lambda **kwargs: self._emit_call_error_event(**kwargs),
            emit_call_end_event=lambda **kwargs: self._emit_call_end_event(**kwargs),
            emit_call_retry_event=lambda **kwargs: self._emit_call_retry_event(**kwargs),
            # Phase 1 critical fix: stream the prepared messages through the
            # same context-snapshot store the sync path uses (executor:440).
            # Without this, every streamed invocation emits a call_start with
            # an empty context_snapshot_ref and the per-LLM context viewer
            # never shows data for Director multi-worker streams.
            #
            # Performance hardening (HIGH #2): must be an async coroutine so
            # ``StreamEngine`` can await it and the underlying disk write runs
            # in the thread pool via ``asyncio.to_thread`` instead of blocking
            # the event loop on every streamed LLM call.
            store_context_messages=(
                lambda ws, msgs, trace_id, call_id_value, provider_request=None: AIExecutor._store_context_messages(
                    workspace=ws,
                    messages=msgs,
                    trace_id=trace_id,
                    call_id=call_id_value,
                    provider_request=provider_request,
                )
            ),
        )

    def set_executor(self, executor: Any) -> None:
        """Set AIExecutor instance (for DI after construction)."""
        self._executor = executor

    def set_formatter(self, formatter: Any) -> None:
        """Set ProviderFormatter for lazy serialization."""
        self._formatter = formatter
