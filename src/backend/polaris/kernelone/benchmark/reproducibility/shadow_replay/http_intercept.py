"""HTTP interception layer for Chronos Mirror.

Provides non-invasive patching of httpx.AsyncClient.send() to intercept
all HTTP traffic within a session.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class HTTPExchange:
    """Captured HTTP request-response exchange."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    response_status: int
    response_headers: dict[str, str]
    response_body: bytes | None
    latency_ms: float
    response_object: httpx.Response | None = None


# Global patch state
_patch_state: dict[str, Any] = {}


def _get_original_send() -> Callable[..., Awaitable[httpx.Response]] | None:
    """Get the original httpx.AsyncClient.send method."""
    return _patch_state.get("original_send")  # type: ignore[return-value]


def _set_original_send(send: Callable[..., Awaitable[httpx.Response]]) -> None:
    """Set the original httpx.AsyncClient.send method."""
    _patch_state["original_send"] = send


# Interceptor callback type
# Returns: (should_proceed, response_or_None)
# - should_proceed=True, response=None: make real HTTP call, interceptor will handle response
# - should_proceed=False, response=MockResponse: return this response directly
InterceptorCallback = Callable[["HTTPExchange"], Awaitable[tuple[bool, "httpx.Response | None"]]]


def _get_interceptor() -> InterceptorCallback | None:
    """Get the interceptor callback."""
    return _patch_state.get("interceptor")  # type: ignore[return-value]


def _set_interceptor(cb: InterceptorCallback | None) -> None:
    """Set the interceptor callback."""
    _patch_state["interceptor"] = cb


def _is_patched() -> bool:
    """Check if currently patched."""
    return "_shadow_patched" in _patch_state


async def _patched_send(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
    """Patched httpx.AsyncClient.send that intercepts requests.

    This is the core interception point. When patched, all httpx.AsyncClient
    calls go through here.

    The interceptor decides whether to:
    1. Proceed with real HTTP call (recording mode)
    2. Short-circuit and return mock response (replay mode)
    """
    # Capture request details
    method = request.method
    url = str(request.url)
    headers = dict(request.headers)

    # Read body
    body = None
    if request.content:
        body = request.content

    # Track timing
    start = time.monotonic()

    # Check if interceptor is set
    interceptor = _get_interceptor()

    # Build minimal exchange for interceptor decision
    # We need to build this before calling interceptor so it can decide
    exchange = HTTPExchange(
        method=method,
        url=url,
        headers=headers,
        body=body,
        response_status=0,
        response_headers={},
        response_body=None,
        latency_ms=0.0,
        response_object=None,
    )

    if interceptor is not None:
        # Let interceptor decide whether to proceed
        should_proceed, response_or_none = await interceptor(exchange)

        if not should_proceed and response_or_none is not None:
            # Replay mode: short-circuit, return mocked response
            exchange.latency_ms = (time.monotonic() - start) * 1000
            return response_or_none

        # Recording mode: proceed with real HTTP call
        original = _get_original_send()
        if original is None:
            raise RuntimeError("No original send method found")

        if inspect.ismethod(original):
            response = await original(self, request, **kwargs)
        else:
            response = await original(self, request, **kwargs)

        latency_ms = (time.monotonic() - start) * 1000

        # Build full exchange with response details
        response_headers = dict(response.headers)
        # Read content BEFORE wrapping in exchange so stream isn't consumed
        response_body = response.content if hasattr(response, "content") else None
        status_code = response.status_code

        # Update exchange with full details
        exchange.response_status = status_code
        exchange.response_headers = response_headers
        exchange.response_body = response_body
        exchange.latency_ms = latency_ms
        exchange.response_object = response

        # Call interceptor with full exchange (for recording)
        should_proceed2, response_or_none2 = await interceptor(exchange)

        if not should_proceed2 and response_or_none2 is not None:
            return response_or_none2

        return response

    # Not intercepted - call original
    original = _get_original_send()
    if original is not None:
        if inspect.ismethod(original):
            return await original(self, request, **kwargs)
        else:
            return await original(self, request, **kwargs)

    raise RuntimeError("No original send method found")


async def apply_http_patch() -> None:
    """Apply HTTP interception patch to httpx.AsyncClient.

    After this, all httpx.AsyncClient.send() calls go through _patched_send.
    """
    if _is_patched():
        logger.debug("[ShadowReplay] Already patched")
        return

    # Save original
    original = httpx.AsyncClient.send
    _set_original_send(original)

    # Apply patch
    httpx.AsyncClient.send = _patched_send  # type: ignore[method-assign]
    _patch_state["_shadow_patched"] = True

    logger.debug("[ShadowReplay] Patched httpx.AsyncClient.send()")


async def remove_http_patch() -> None:
    """Remove HTTP interception patch."""
    if not _is_patched():
        return

    # Restore original
    original = _get_original_send()
    if original is not None:
        httpx.AsyncClient.send = original  # type: ignore[assignment]

    # Clear state
    _patch_state.clear()

    logger.debug("[ShadowReplay] Restored httpx.AsyncClient.send()")


def set_interceptor(cb: InterceptorCallback | None) -> None:
    """Set the interceptor callback.

    Args:
        cb: Async function that receives exchange and returns (should_proceed, response).
            - should_proceed=True: make real HTTP call
            - should_proceed=False, response=MockResponse: return this response directly
            - In record mode: cb returns (True, None) to record and return real response
            - In replay mode: cb returns (False, MockResponse) to short-circuit
    """
    _set_interceptor(cb)


def clear_interceptor() -> None:
    """Clear the interceptor callback."""
    _set_interceptor(None)
