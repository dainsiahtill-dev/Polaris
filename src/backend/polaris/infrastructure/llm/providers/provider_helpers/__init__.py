"""Shared helpers for LLM provider implementations.

Eliminates duplicate retry-loop, health-check, and model-listing patterns
that were copy-pasted across anthropic_compat, openai_compat, kimi, and
gemini API providers.

IMPORTANT: This package contains a sync CircuitBreaker implementation.
For async LLM engine operations, see polaris/kernelone/llm/engine/resilience.py.

CircuitBreaker Intentional Separation:
1. AsyncCircuitBreaker (llm/kernelone/llm/engine/resilience.py):
   - For async LLM engine calls
   - Full HALF_OPEN state management with asyncio.Lock
   - Integrates with ResilienceManager for retry/timeout

2. SyncCircuitBreaker (this package, llm/providers/provider_helpers):
   - For sync provider HTTP operations
   - Simplified state machine with threading.RLock
   - Independent implementation optimized for blocking I/O

These are intentionally separate implementations optimized for their
respective execution models. Do NOT try to unify them.

------------------------------------------------------------------------
Lossless decomposition (god-module -> package)
------------------------------------------------------------------------
This package is the lossless successor of the former single-file
``provider_helpers`` module. It re-exports every previously-public (and
previously-private-but-imported) symbol from the same import path so that
``import ...provider_helpers`` and ``from ...provider_helpers import X``
keep resolving identically for all external importers, and so that the
provider test-suite's string-path monkeypatching of
``provider_helpers._blocking_http_post`` / ``_blocking_http_get`` /
``_close_and_create_session`` / ``requests`` / ``time`` / ``asyncio`` /
``LLMResponseParser`` continues to be observed losslessly.

The domain split lives in four sibling implementation modules:
  * ``_http_pooling``     — lightweight aiohttp session/pool emulation,
                            blocking/async HTTP pools, LRU stream-session
                            registry, sync ``requests``-based HTTP helpers.
  * ``_circuit_breaker``  — ``CircuitBreaker`` / ``CircuitOpenError``,
                            ``invoke_with_retry``, ``health_check_post``,
                            ``list_models_from_api``.
  * ``_stream_governance``— governed async provider streaming.
  * ``_core``             — ``build_chat_messages_payload`` and the
                            context-overflow self-heal helper.

Internal cross-module call sites that must observe package-level
monkeypatching reference the relevant symbol *through this package* at
call time (``_ph``), which is why ``_blocking_http_post`` /
``_blocking_http_get`` / ``_close_and_create_session`` are read off the
package namespace inside ``invoke_with_retry`` / ``health_check_post`` /
``list_models_from_api`` / ``_open_frozen_aiohttp_stream``.
"""

from __future__ import annotations

# Re-export stdlib / third-party / kernelone names that were module-level
# attributes of the former single-file module. Provider tests reference them
# as ``provider_helpers.requests``, ``provider_helpers.time``,
# ``provider_helpers.asyncio``, ``provider_helpers.LLMResponseParser``, etc.,
# and monkeypatch their attributes via dotted string paths, so they MUST remain
# attributes of this package (and point at the same shared module objects).
# Submodule imports follow after this block; E402 is expected and lossless.
# ruff: noqa: E402
import asyncio
import json
import logging
import random
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

import requests
from polaris.kernelone.common.clock import ClockPort, RealClock
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.llm.engine.contracts import get_physical_provider_dispatch_port
from polaris.kernelone.llm.response_parser import FinalizedResponse, LLMResponseParser
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    ModelInfo,
    ModelListResult,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Callable

    import aiohttp
    from polaris.kernelone.llm.engine.contracts import (
        AsyncPhysicalProviderDispatchPort,
        PhysicalProviderDispatchPort,
    )

logger = logging.getLogger(__name__)

# Implementation submodules (domain split of former single-file module).
from . import (
    _circuit_breaker as _circuit_breaker,
    _core as _core,
    _http_pooling as _http_pooling,
    _stream_governance as _stream_governance,
)
from ._circuit_breaker import (
    _CIRCUIT_BREAKER_LOCK,
    _CIRCUIT_BREAKER_REGISTRY,
    _RATE_LIMIT_MIN_RETRIES,
    CircuitBreaker,
    CircuitOpenError,
    _build_backoff_seconds,
    _GovernedHttpAttemptResult,
    _GovernedHttpResponseError,
    _http_response_is_ok,
    _parse_retry_after_seconds,
    _rate_limit_min_retries,
    _send_governed_http_attempt,
    get_circuit_breaker,
    health_check_post,
    invoke_with_retry,
    list_models_from_api,
)
from ._core import (
    _CONTEXT_OVERFLOW_RE,
    build_chat_messages_payload,
    shrink_max_tokens_for_context_overflow,
)
from ._http_pooling import (
    _BACKGROUND_TASKS,
    _BLOCKING_HTTP_POOL_LAZY,
    _MAX_HTTP_WORKERS,
    _MAX_SLEEP_WORKERS,
    _REAL_CLIENT_SESSION_TYPE,
    _SLEEP_POOL_LAZY,
    _STREAM_SESSION_CLEANUP_REGISTERED,
    _STREAM_SESSION_LOCK,
    _STREAM_SESSION_REGISTRY,
    HttpTimeout,
    _aiohttp_module,
    _blocking_http_get,
    _blocking_http_pool,
    _blocking_http_post,
    _blocking_sleep,
    _close_and_create_session,
    _close_session_if_possible,
    _do_requests_get,
    _do_requests_post,
    _ensure_aiohttp_imported,
    _get_blocking_http_pool,
    _get_blocking_sleep_pool,
    _get_http_pool,
    _get_sleep_pool,
    _is_reusable_stream_session,
    _LazyPool,
    _LightweightAiohttpModule,
    _LightweightClientSession,
    _LightweightClientTimeout,
    _LightweightTCPConnector,
    _LRUSessionRegistry,
    _raw_blocking_http_post,
    _register_stream_session_cleanup_once,
    _session_is_closed,
    _should_use_lightweight_stream_session_mode,
    _sleep_pool,
    _thaw_physical_dispatch_value,
    _track_background_task,
    close_stream_sessions,
    close_stream_sessions_sync,
    get_stream_session,
    iter_data_line_payloads,
)
from ._stream_governance import (
    _PROVIDER_STREAM_ERROR_BODY_MAX_BYTES,
    _STREAM_RETRY_DELAY_SEC,
    _STREAM_RETRY_MAX_ATTEMPTS,
    _aclose_stream_resisting_cancellation,
    _AsyncStreamGovernanceMode,
    _bounded_provider_stream_error_chunk_prefix,
    _build_async_stream_wire_request,
    _consume_data_line_response,
    _dispatch_async_stream_attempt,
    _is_retryable_network_error,
    _iterate_physical_stream_under_deadline,
    _open_frozen_aiohttp_stream,
    _ProviderStreamHttpError,
    _read_bounded_provider_stream_error_body,
    _resolve_async_stream_governance,
    _stream_deadline_remaining_seconds,
    _stream_timeout_budget_seconds,
    _validate_async_stream_governance,
    invoke_stream_with_retry,
    invoke_stream_with_retry_and_handler,
)


def _wire_cross_module_namespace() -> None:
    """Inject sibling symbols into each submodule globals for free-name lookup.

    Functions defined in submodules resolve free names via their module
    ``__dict__``. After the package re-exports every symbol, copy non-owned
    names into each submodule so cross-module calls remain lossless without
    rewriting call sites. Ownership is each submodule's ``__all__``.

    This mirrors the proven pattern in
    ``polaris/cells/roles/adapters/internal/director/quality_gate/__init__``.
    """
    import sys

    pkg = sys.modules[__name__]
    shared = {key: value for key, value in pkg.__dict__.items() if not key.startswith("__")}
    for mod in (
        _http_pooling,
        _circuit_breaker,
        _stream_governance,
        _core,
    ):
        owned = set(getattr(mod, "__all__", ()) or ())
        for key, value in shared.items():
            if key not in owned:
                mod.__dict__[key] = value


_wire_cross_module_namespace()


# Public surface (names intended for external import). Private symbols are
# intentionally NOT hidden because the provider modules and test-suite import
# many of them directly from this package; losslessness requires they remain
# accessible as package attributes (handled by the explicit re-exports above).
__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "HttpTimeout",
    "build_chat_messages_payload",
    "close_stream_sessions",
    "close_stream_sessions_sync",
    "get_circuit_breaker",
    "get_stream_session",
    "health_check_post",
    "invoke_stream_with_retry",
    "invoke_stream_with_retry_and_handler",
    "invoke_with_retry",
    "iter_data_line_payloads",
    "list_models_from_api",
    "shrink_max_tokens_for_context_overflow",
]
