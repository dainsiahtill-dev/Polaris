"""Internal implementation module for the provider_helpers package (lossless split).

Owns: governed async provider streaming — ``invoke_stream_with_retry``,
``invoke_stream_with_retry_and_handler``, ``_validate_async_stream_governance``,
the bounded error-body reader, retryable-network-error classification, and the
async stream deadline machinery.

``_close_and_create_session`` is owned by ``_http_pooling`` but referenced
*through the package namespace* (``_ph``) inside ``_open_frozen_aiohttp_stream``
so that ``monkeypatch.setattr("...provider_helpers._close_and_create_session", ...)``
is observed losslessly after the split.

Static F821/F401 are expected and lossless; do not strip.
"""

# ruff: noqa: F401

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal, cast

from polaris.kernelone.llm.engine.contracts import get_physical_provider_dispatch_port

from ._http_pooling import (
    _ensure_aiohttp_imported,
    _thaw_physical_dispatch_value,
    iter_data_line_payloads,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping as _MappingT

    import aiohttp
    from polaris.kernelone.llm.engine.contracts import AsyncPhysicalProviderDispatchPort

logger = logging.getLogger(__name__)

# ============================================================================
# Network Jitter Retry for Async Stream Sessions
# ============================================================================
# Retry configuration for transient network errors (connection reset, timeout, etc.)
# Uses fixed delay: configured seconds between retries, max configured attempts

_STREAM_RETRY_DELAY_SEC: float = float(os.environ.get("KERNELONE_STREAM_RETRY_DELAY_SEC", "5.0"))
_STREAM_RETRY_MAX_ATTEMPTS: int = int(os.environ.get("KERNELONE_STREAM_RETRY_MAX_ATTEMPTS", "3"))
_PROVIDER_STREAM_ERROR_BODY_MAX_BYTES = 500


class _ProviderStreamHttpError(RuntimeError):
    """Carry one provider HTTP failure, including its bounded response body."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        error_body: str,
        body_truncated: bool = False,
    ) -> None:
        self.status_code = status_code
        self.error_body = error_body[:500]
        self.body_truncated = body_truncated or len(error_body) > 500
        detail = self.error_body if self.error_body else "(empty)"
        truncation = " [truncated]" if self.body_truncated else ""
        super().__init__(f"provider_stream_http_error:{status_code} from {url}: {detail}{truncation}")


def _bounded_provider_stream_error_chunk_prefix(
    chunk: bytes | bytearray | memoryview | str,
    max_bytes: int,
) -> bytes:
    """Copy at most ``max_bytes`` from one provider-controlled stream chunk."""

    if max_bytes <= 0:
        return b""
    if isinstance(chunk, str):
        return chunk[:max_bytes].encode("utf-8", errors="replace")[:max_bytes]
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        view = memoryview(chunk)
        if view.format != "B" or view.ndim != 1:
            view = view.cast("B")
        return bytes(view[:max_bytes])
    raise TypeError("provider_stream_error_body_chunk_not_bytes")


async def _read_bounded_provider_stream_error_body(
    response: aiohttp.ClientResponse,
) -> tuple[str, bool]:
    """Read at most one bounded provider error-body prefix."""

    read_limit = _PROVIDER_STREAM_ERROR_BODY_MAX_BYTES + 1
    content = response.content
    read = getattr(content, "read", None)
    if callable(read):
        raw_value = await read(read_limit)
        if not isinstance(raw_value, (bytes, bytearray, memoryview)):
            raise TypeError("provider_stream_error_body_read_not_bytes")
        raw = bytes(raw_value)
    else:
        buffer = bytearray()
        async for chunk in content:
            remaining = read_limit - len(buffer)
            buffer.extend(_bounded_provider_stream_error_chunk_prefix(chunk, remaining))
            if len(buffer) >= read_limit:
                break
        raw = bytes(buffer)
    truncated = len(raw) > _PROVIDER_STREAM_ERROR_BODY_MAX_BYTES
    bounded = raw[:_PROVIDER_STREAM_ERROR_BODY_MAX_BYTES]
    return bounded.decode("utf-8", errors="replace"), truncated


def _is_retryable_network_error(exc: BaseException) -> bool:
    """Determine if an exception is a transient network error suitable for retry.

    Args:
        exc: The exception to check.

    Returns:
        True if the error is retryable, False otherwise.
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc).lower()

    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        status_code = getattr(exc, "status", None)
    if isinstance(status_code, int):
        return status_code in {408, 425, 429} or 500 <= status_code < 600

    # aiohttp client errors that always indicate transport failures.
    retryable_errors = {
        "ClientConnectorError",
        "ClientOSError",
        "ClientSSLError",
        "ServerDisconnectedError",
    }

    # Connection-related errors from asyncio and socket layer
    connection_errors = {
        "ConnectionResetError",
        "ConnectionRefusedError",
        "ConnectionAbortedError",
        "BrokenPipeError",
        "TimeoutError",
    }

    if exc_type in retryable_errors:
        return True
    if exc_type in connection_errors:
        return True

    # Check for HTTP status codes in exception message (e.g., "429 Client Response: Too Many Requests")
    # These indicate server-side errors that may be transient
    status_indicators = ["408", "425", "429", "500", "501", "502", "503", "504"]
    for indicator in status_indicators:
        if indicator in exc_msg:
            return True

    # Check for common network error indicators in the message
    network_indicators = [
        "cannot connect",
        "connection refused",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "timed out",
        "ssl handshake",
        "network is unreachable",
        "no route to host",
        "temporary failure",
        "name or service not known",
        "getaddrinfo failed",
    ]

    return any(indicator in exc_msg for indicator in network_indicators)


_AsyncStreamGovernanceMode = Literal["legacy_ungoverned", "governed_required"]


def _validate_async_stream_governance(
    *,
    governance_mode: _AsyncStreamGovernanceMode,
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None,
) -> None:
    if governance_mode not in {"legacy_ungoverned", "governed_required"}:
        raise ValueError("async stream governance_mode is invalid")
    if governance_mode == "governed_required" and physical_dispatch_port is None:
        raise RuntimeError("governed async stream requires a physical dispatch port")


def _resolve_async_stream_governance(
    *,
    governance_mode: _AsyncStreamGovernanceMode,
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None,
) -> tuple[_AsyncStreamGovernanceMode, AsyncPhysicalProviderDispatchPort | None]:
    """Promote a request-scoped Factory binding to governed stream dispatch."""

    resolved_port = physical_dispatch_port
    resolved_mode = governance_mode
    if resolved_port is None:
        bound_port = get_physical_provider_dispatch_port()
        if bound_port is not None:
            if not hasattr(bound_port, "dispatch_stream_async"):
                raise RuntimeError("bound physical provider dispatch port does not support dispatch_stream_async")
            resolved_port = cast("AsyncPhysicalProviderDispatchPort", bound_port)
            resolved_mode = "governed_required"
    _validate_async_stream_governance(
        governance_mode=resolved_mode,
        physical_dispatch_port=resolved_port,
    )
    return resolved_mode, resolved_port


def _build_async_stream_wire_request(
    *,
    url: str,
    headers: Any,
    payload: Any,
    timeout_seconds: int,
) -> dict[str, Any]:
    from collections.abc import Mapping

    return {
        "endpoint": str(url),
        "headers": dict(headers),
        "body": dict(payload),
        "transport": {
            "kind": "aiohttp.ClientSession.post",
            "timeout_seconds": timeout_seconds if timeout_seconds > 0 else 60,
        },
    }


@asynccontextmanager
async def _open_frozen_aiohttp_stream(
    frozen_wire: Any,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """Open one response/session pair from the gate's immutable dispatch view."""

    from collections.abc import Mapping

    # Resolve _close_and_create_session through the package namespace so that
    # ``monkeypatch.setattr("...provider_helpers._close_and_create_session", ...)``
    # is observed losslessly after the split.
    import polaris.infrastructure.llm.providers.provider_helpers as _ph

    session: aiohttp.ClientSession | None = None
    try:
        session = await _ph._close_and_create_session(None)
        endpoint = str(frozen_wire["endpoint"])
        headers = _thaw_physical_dispatch_value(frozen_wire["headers"])
        body = _thaw_physical_dispatch_value(frozen_wire["body"])
        transport = frozen_wire["transport"]
        if not isinstance(headers, dict) or not isinstance(body, dict) or not isinstance(transport, Mapping):
            raise TypeError("frozen async HTTP dispatch view is malformed")
        timeout_total = int(transport["timeout_seconds"])
        timeout = _ensure_aiohttp_imported().ClientTimeout(total=timeout_total)
        async with session.post(
            endpoint,
            headers=headers,
            json=body,
            timeout=timeout,
        ) as response:
            yield response
    finally:
        if session is not None:
            from ._http_pooling import _close_session_if_possible

            await _close_session_if_possible(session)


def _dispatch_async_stream_attempt(
    *,
    wire_request: Any,
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None,
    consume: Callable[[aiohttp.ClientResponse], AsyncIterator[Any]],
) -> AsyncIterator[Any]:
    if physical_dispatch_port is not None:
        return physical_dispatch_port.dispatch_stream_async(
            wire_request=wire_request,
            open_stream=_open_frozen_aiohttp_stream,
            consume=consume,
        )

    async def _legacy_dispatch() -> AsyncIterator[Any]:
        async with _open_frozen_aiohttp_stream(wire_request) as response:
            async for item in consume(response):
                yield item

    return _legacy_dispatch()


async def _aclose_stream_resisting_cancellation(stream: AsyncIterator[Any]) -> bool:
    close = getattr(stream, "aclose", None)
    if not callable(close):
        return False
    close_task = asyncio.create_task(close())
    cancellation_observed = False
    while True:
        try:
            await asyncio.shield(close_task)
            return cancellation_observed
        except asyncio.CancelledError:
            if close_task.cancelled():
                raise
            cancellation_observed = True


async def _consume_data_line_response(
    response: aiohttp.ClientResponse,
    *,
    url: str,
) -> AsyncIterator[dict[str, Any]]:
    if not response.ok:
        error_body = ""
        body_truncated = False
        aiohttp_module = _ensure_aiohttp_imported()
        aiohttp_client_error = getattr(aiohttp_module, "ClientError", RuntimeError)
        body_read_errors = (aiohttp_client_error, OSError, RuntimeError, TypeError, ValueError)
        try:
            error_body, body_truncated = await _read_bounded_provider_stream_error_body(response)
            truncation = " [truncated]" if body_truncated else ""
            logger.warning(
                "[provider-helpers] HTTP %s from %s: %s%s",
                response.status,
                url,
                error_body if error_body else "(empty)",
                truncation,
            )
        except body_read_errors:
            logger.warning("[provider-helpers] HTTP %s from %s (failed to read body)", response.status, url)
        raise _ProviderStreamHttpError(
            status_code=int(response.status),
            url=url,
            error_body=error_body,
            body_truncated=body_truncated,
        )
    response.raise_for_status()
    content_type = str(response.headers.get("Content-Type", "") or "").lower()
    if "application/json" in content_type:
        payload_obj = await response.json()
        if isinstance(payload_obj, dict):
            yield payload_obj
            return
        raise RuntimeError(
            f"provider_stream_invalid_json: expected JSON object from {url}, got {type(payload_obj).__name__}"
        )

    decoded_event_count = 0
    async for data_str in iter_data_line_payloads(response.content):
        if data_str == "[DONE]":
            break
        try:
            payload_obj = json.loads(data_str)
        except (RuntimeError, ValueError) as exc:
            logger.debug("[provider-helpers] Failed to decode provider JSON payload from %s: %s", url, exc)
            continue
        if isinstance(payload_obj, dict):
            decoded_event_count += 1
            yield payload_obj
    if decoded_event_count == 0:
        raise RuntimeError(f"provider_stream_empty: no structured events decoded from streaming response {url}")


def _stream_timeout_budget_seconds(timeout_seconds: int | float) -> float:
    return max(0.001, float(timeout_seconds if timeout_seconds > 0 else 60))


def _stream_deadline_remaining_seconds(*, deadline: float, total_timeout_seconds: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError(f"provider_stream_timeout:{total_timeout_seconds:g}s")
    return remaining


async def _iterate_physical_stream_under_deadline(
    stream: AsyncIterator[Any],
    *,
    deadline: float,
    total_timeout_seconds: float,
) -> AsyncIterator[Any]:
    """Apply the provider deadline only while awaiting physical stream data.

    The Factory physical-attempt gate durably terminalizes the provider
    response before replaying its buffered items to the role runtime.  A
    timeout context spanning this helper's outward ``yield`` would therefore
    remain armed during post-terminal replay/backpressure and could cancel the
    consumer task after the physical attempt had already completed.  Keep the
    deadline around each physical ``anext`` only; downstream processing is not
    provider response time.
    """

    iterator = stream.__aiter__()
    while True:
        remaining_seconds = _stream_deadline_remaining_seconds(
            deadline=deadline,
            total_timeout_seconds=total_timeout_seconds,
        )
        try:
            async with asyncio.timeout(remaining_seconds):
                item = await anext(iterator)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError as exc:
            raise asyncio.TimeoutError(f"provider_stream_timeout:{total_timeout_seconds:g}s") from exc
        yield item


async def invoke_stream_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    *,
    max_attempts: int = _STREAM_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds: float = _STREAM_RETRY_DELAY_SEC,
    governance_mode: _AsyncStreamGovernanceMode = "legacy_ungoverned",
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """POST provider data-line streams with one physical gate per retry."""

    _, physical_dispatch_port = _resolve_async_stream_governance(
        governance_mode=governance_mode,
        physical_dispatch_port=physical_dispatch_port,
    )
    last_exc: BaseException | None = None
    total_timeout_seconds = _stream_timeout_budget_seconds(timeout_seconds)
    deadline = asyncio.get_running_loop().time() + total_timeout_seconds
    for attempt in range(1, max_attempts + 1):
        attempt_stream: AsyncIterator[Any] | None = None
        try:
            remaining_seconds = _stream_deadline_remaining_seconds(
                deadline=deadline,
                total_timeout_seconds=total_timeout_seconds,
            )
            wire_request = _build_async_stream_wire_request(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=max(1, int(remaining_seconds + 0.999)),
            )

            async def _consume(response: aiohttp.ClientResponse) -> AsyncIterator[dict[str, Any]]:
                physical_stream = _consume_data_line_response(response, url=url)
                async for item in _iterate_physical_stream_under_deadline(
                    physical_stream,
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                ):
                    yield item

            attempt_stream = _dispatch_async_stream_attempt(
                wire_request=wire_request,
                physical_dispatch_port=physical_dispatch_port,
                consume=_consume,
            )
            async for item in attempt_stream:
                yield item
            return
        except (asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except BaseException as exc:
            last_exc = exc
            if not _is_retryable_network_error(exc):
                raise
            if attempt < max_attempts:
                logger.warning(
                    "[provider-helpers] Network jitter detected (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    retry_delay_seconds,
                )
                remaining_seconds = _stream_deadline_remaining_seconds(
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                )
                await asyncio.sleep(min(retry_delay_seconds, remaining_seconds))
            else:
                logger.error(
                    "[provider-helpers] Network jitter retry exhausted (all %d attempts failed): %s",
                    max_attempts,
                    str(exc),
                )
        finally:
            if attempt_stream is not None:
                close_cancelled = await _aclose_stream_resisting_cancellation(attempt_stream)
                if close_cancelled:
                    raise asyncio.CancelledError

    if last_exc is not None:
        raise last_exc


async def invoke_stream_with_retry_and_handler(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    stream_handler: Callable[[aiohttp.ClientResponse], AsyncGenerator[Any, None]],
    *,
    max_attempts: int = _STREAM_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds: float = _STREAM_RETRY_DELAY_SEC,
    governance_mode: _AsyncStreamGovernanceMode = "legacy_ungoverned",
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None = None,
) -> AsyncGenerator[Any, None]:
    """POST custom provider streams with one physical gate per retry."""

    _, physical_dispatch_port = _resolve_async_stream_governance(
        governance_mode=governance_mode,
        physical_dispatch_port=physical_dispatch_port,
    )
    last_exc: BaseException | None = None
    total_timeout_seconds = _stream_timeout_budget_seconds(timeout_seconds)
    deadline = asyncio.get_running_loop().time() + total_timeout_seconds
    for attempt in range(1, max_attempts + 1):
        attempt_stream: AsyncIterator[Any] | None = None
        try:
            remaining_seconds = _stream_deadline_remaining_seconds(
                deadline=deadline,
                total_timeout_seconds=total_timeout_seconds,
            )
            wire_request = _build_async_stream_wire_request(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=max(1, int(remaining_seconds + 0.999)),
            )

            async def _consume(response: aiohttp.ClientResponse) -> AsyncIterator[Any]:
                response.raise_for_status()
                consumed = 0
                physical_stream = stream_handler(response)
                async for item in _iterate_physical_stream_under_deadline(
                    physical_stream,
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                ):
                    consumed += 1
                    yield item
                if consumed == 0:
                    raise RuntimeError(f"provider_stream_empty: custom handler decoded no events from {url}")

            attempt_stream = _dispatch_async_stream_attempt(
                wire_request=wire_request,
                physical_dispatch_port=physical_dispatch_port,
                consume=_consume,
            )
            async for item in attempt_stream:
                yield item
            return
        except (asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except BaseException as exc:
            last_exc = exc
            if not _is_retryable_network_error(exc):
                raise
            if attempt < max_attempts:
                logger.warning(
                    "[provider-helpers] Network jitter detected (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    retry_delay_seconds,
                )
                remaining_seconds = _stream_deadline_remaining_seconds(
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                )
                await asyncio.sleep(min(retry_delay_seconds, remaining_seconds))
            else:
                logger.error(
                    "[provider-helpers] Network jitter retry exhausted (all %d attempts failed): %s",
                    max_attempts,
                    str(exc),
                )
        finally:
            if attempt_stream is not None:
                close_cancelled = await _aclose_stream_resisting_cancellation(attempt_stream)
                if close_cancelled:
                    raise asyncio.CancelledError

    if last_exc is not None:
        raise last_exc


__all__ = [
    "_PROVIDER_STREAM_ERROR_BODY_MAX_BYTES",
    "_STREAM_RETRY_DELAY_SEC",
    "_STREAM_RETRY_MAX_ATTEMPTS",
    "_AsyncStreamGovernanceMode",
    "_ProviderStreamHttpError",
    "_aclose_stream_resisting_cancellation",
    "_bounded_provider_stream_error_chunk_prefix",
    "_build_async_stream_wire_request",
    "_consume_data_line_response",
    "_dispatch_async_stream_attempt",
    "_is_retryable_network_error",
    "_iterate_physical_stream_under_deadline",
    "_open_frozen_aiohttp_stream",
    "_read_bounded_provider_stream_error_body",
    "_resolve_async_stream_governance",
    "_stream_deadline_remaining_seconds",
    "_stream_timeout_budget_seconds",
    "_validate_async_stream_governance",
    "invoke_stream_with_retry",
    "invoke_stream_with_retry_and_handler",
]
