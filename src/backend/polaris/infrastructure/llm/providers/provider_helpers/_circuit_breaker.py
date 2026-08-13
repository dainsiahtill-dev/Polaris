"""Internal implementation module for the provider_helpers package (lossless split).

Owns: the sync ``CircuitBreaker`` + ``CircuitOpenError``, the breaker registry,
``invoke_with_retry`` / ``health_check_post`` / ``list_models_from_api``, and the
governed HTTP attempt helpers.

The internal HTTP entry points (``_blocking_http_post`` / ``_blocking_http_get``)
are owned by ``_http_pooling`` but are referenced *through the package namespace*
(``_ph``) at call time so that the very common provider test-suite patch
``monkeypatch.setattr("...provider_helpers._blocking_http_post", ...)`` is
observed losslessly after the god-module was decomposed into a package.

Static F821/F401 are expected and lossless; do not strip.
"""

# ruff: noqa: F401

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import TYPE_CHECKING, Any, cast

import requests
from polaris.kernelone.common.clock import ClockPort, RealClock
from polaris.kernelone.llm.engine.contracts import get_physical_provider_dispatch_port
from polaris.kernelone.llm.response_parser import LLMResponseParser
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, Usage

from ._core import shrink_max_tokens_for_context_overflow
from ._http_pooling import HttpTimeout

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.kernelone.llm.engine.contracts import PhysicalProviderDispatchPort

    from ._http_pooling import HttpTimeout as _HttpTimeout

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised when requests are short-circuited by an open circuit breaker."""


class CircuitBreaker:
    """Thread-safe circuit breaker for provider HTTP calls."""

    def __init__(self, *, failure_threshold: int = 5, recovery_timeout_seconds: float = 60.0) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        # 0.0 is a coherent config — "immediately eligible for one half-open
        # trial" (the breaker still enforces single-trial recovery and reopens
        # on a half-open failure). The floor only guards against negatives; an
        # over-aggressive 1.0 floor silently defeated a 0-second recovery and
        # its state-machine tests. No production caller passes < 60.0.
        self.recovery_timeout_seconds = max(0.0, float(recovery_timeout_seconds))
        self._lock = threading.RLock()
        self._failure_count = 0
        self._state = "closed"  # closed | open | half_open
        self._opened_at = 0.0

    def before_call(self) -> None:
        with self._lock:
            if self._state != "open":
                return
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.recovery_timeout_seconds:
                self._state = "half_open"
                return
            raise CircuitOpenError(f"circuit_open:{int(self.recovery_timeout_seconds - elapsed)}s_remaining")

    def on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._opened_at = 0.0

    def on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            should_open = self._state == "half_open" or self._failure_count >= self.failure_threshold
            if should_open:
                self._state = "open"
                self._opened_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout_seconds,
            }


_CIRCUIT_BREAKER_REGISTRY: dict[str, CircuitBreaker] = {}
_CIRCUIT_BREAKER_LOCK = threading.RLock()


def get_circuit_breaker(
    key: str,
    *,
    failure_threshold: int = 5,
    recovery_timeout_seconds: float = 60.0,
) -> CircuitBreaker:
    normalized_key = str(key or "").strip().lower() or "default"
    with _CIRCUIT_BREAKER_LOCK:
        breaker = _CIRCUIT_BREAKER_REGISTRY.get(normalized_key)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout_seconds=recovery_timeout_seconds,
            )
            _CIRCUIT_BREAKER_REGISTRY[normalized_key] = breaker
        return breaker


def _build_backoff_seconds(
    *,
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    exp_delay = base_delay_seconds * (2 ** max(0, attempt - 1))
    bounded = min(max_delay_seconds, max(base_delay_seconds, exp_delay))
    # Add small jitter to reduce synchronized retry storms.
    jitter = random.uniform(0.0, bounded * 0.2)
    return bounded + jitter


_RATE_LIMIT_MIN_RETRIES = 4


def _rate_limit_min_retries() -> int:
    """429 retry budget, env-tunable for high-saturation conditions.

    Defaults to ``_RATE_LIMIT_MIN_RETRIES``. Raise
    ``KERNELONE_LLM_RATE_LIMIT_MAX_RETRIES`` to ride out longer shared-provider
    saturation (at the cost of slower calls while the provider rate limits).
    """
    raw = os.environ.get("KERNELONE_LLM_RATE_LIMIT_MAX_RETRIES", "").strip()
    if not raw:
        return _RATE_LIMIT_MIN_RETRIES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _RATE_LIMIT_MIN_RETRIES
    return value if value >= 0 else _RATE_LIMIT_MIN_RETRIES


def _parse_retry_after_seconds(response: Any) -> float | None:
    """Best-effort parse of a 429 ``Retry-After`` header into seconds.

    Honors the integer/float-seconds form (the common case for LLM gateways).
    Returns ``None`` when the header is absent or non-numeric, letting the
    caller fall back to exponential backoff.
    """
    headers_value = getattr(response, "headers", None)
    if headers_value is None or not hasattr(headers_value, "get"):
        return None
    raw = str(headers_value.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class _GovernedHttpResponseError(RuntimeError):
    """Carry one non-success physical HTTP response through the attempt gate."""

    def __init__(self, response: Any) -> None:
        self.response = response
        status_code = getattr(response, "status_code", "unknown")
        super().__init__(f"governed HTTP response status {status_code}")


class _GovernedHttpAttemptResult:
    """Parsed result of one successful governed physical HTTP attempt."""

    __slots__ = ("data", "finalized", "latency_ms", "output", "response", "usage")

    def __init__(
        self,
        *,
        response: Any,
        data: dict[str, Any],
        latency_ms: int,
        output: str,
        finalized: Any,
        usage: Usage,
    ) -> None:
        self.response = response
        self.data = data
        self.latency_ms = latency_ms
        self.output = output
        self.finalized = finalized
        self.usage = usage


def _http_response_is_ok(response: Any) -> bool:
    response_ok = getattr(response, "ok", None)
    if response_ok is not None:
        return bool(response_ok)
    status_code = getattr(response, "status_code", None)
    return isinstance(status_code, int) and status_code < 400


def _send_governed_http_attempt(
    frozen: Any,
    *,
    prompt: str,
    clock: ClockPort,
    start: float,
    extract_output: Callable[[dict[str, Any]], str],
    usage_from_response: Callable[[str, str, dict[str, Any]], Usage],
) -> _GovernedHttpAttemptResult:
    """Send and parse once inside the physical-attempt lifecycle boundary."""

    # Resolve the raw HTTP entry point through the package namespace so that
    # ``monkeypatch.setattr("...provider_helpers._raw_blocking_http_post", ...)``
    # (object-attr form, used in test_provider_helpers_retry) is observed
    # losslessly after the god-module was split into this package.
    import polaris.infrastructure.llm.providers.provider_helpers as _ph

    from ._http_pooling import _thaw_physical_dispatch_value

    response = _ph._raw_blocking_http_post(
        str(frozen["endpoint"]),
        _thaw_physical_dispatch_value(frozen["headers"]),
        _thaw_physical_dispatch_value(frozen["body"]),
        frozen["transport"]["timeout"],
    )
    if not _http_response_is_ok(response):
        raise _GovernedHttpResponseError(response)
    data: dict[str, Any] = response.json()
    # Preserve the legacy latency boundary: JSON parsing is included, while
    # provider extraction, semantic finalization, and usage projection are not.
    latency_ms = int((clock.time() - start) * 1000)
    output = extract_output(data)
    finalized = LLMResponseParser.finalize_response(data, visible_text=output)
    usage = usage_from_response(prompt, finalized.output, data)
    return _GovernedHttpAttemptResult(
        response=response,
        data=data,
        latency_ms=latency_ms,
        output=output,
        finalized=finalized,
        usage=usage,
    )


def invoke_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: HttpTimeout,
    retries: int,
    prompt: str,
    extract_output: Callable[[dict[str, Any]], str],
    usage_from_response: Callable[[str, str, dict[str, Any]], Usage],
    *,
    circuit_breaker: CircuitBreaker | None = None,
    circuit_key: str | None = None,
    backoff_base_seconds: float = 0.5,
    backoff_max_seconds: float = 30.0,
    clock: ClockPort | None = None,
    physical_dispatch_port: PhysicalProviderDispatchPort | None = None,
) -> InvokeResult:
    """POST *url* with JSON *payload*, retrying up to *retries* times on failure.

    Args:
        clock: Optional injected clock for testability. Defaults to RealClock.
    """
    # Resolve the HTTP entry point through the package namespace so that
    # ``monkeypatch.setattr("...provider_helpers._blocking_http_post", ...)``
    # (used pervasively across the provider test-suite) is observed losslessly
    # after the god-module was split into this package.
    import polaris.infrastructure.llm.providers.provider_helpers as _ph

    _clock: ClockPort = clock if clock is not None else RealClock()
    resolved_physical_dispatch_port = physical_dispatch_port
    if resolved_physical_dispatch_port is None:
        bound_port = get_physical_provider_dispatch_port()
        if bound_port is not None:
            if not hasattr(bound_port, "dispatch_sync"):
                raise RuntimeError("bound physical provider dispatch port does not support dispatch_sync")
            resolved_physical_dispatch_port = cast("PhysicalProviderDispatchPort", bound_port)
    attempt = 0
    retries = max(0, int(retries))
    breaker = circuit_breaker or get_circuit_breaker(
        circuit_key or f"invoke:{url}",
    )

    start = _clock.time()
    overflow_heal_attempts = 0
    rate_limit_attempt = 0
    while True:
        try:
            breaker.before_call()
        except CircuitOpenError as exc:
            usage = Usage.estimate(prompt, "")
            return InvokeResult(
                ok=False,
                output="",
                latency_ms=int((_clock.time() - start) * 1000),
                usage=usage,
                error=str(exc),
            )

        try:
            # _blocking_http_post offloads to a ThreadPoolExecutor when an event
            # loop is running, preventing event-loop blocking.
            governed_result: _GovernedHttpAttemptResult | None = None
            if resolved_physical_dispatch_port is None:
                response = _ph._blocking_http_post(url, headers, payload, timeout)
            else:
                try:
                    governed_result = resolved_physical_dispatch_port.dispatch_sync(
                        wire_request={
                            "endpoint": url,
                            "headers": headers,
                            "body": payload,
                            "transport": {
                                "kind": "requests.post",
                                "timeout": timeout,
                            },
                        },
                        send=lambda frozen: _send_governed_http_attempt(
                            frozen,
                            prompt=prompt,
                            clock=_clock,
                            start=start,
                            extract_output=extract_output,
                            usage_from_response=usage_from_response,
                        ),
                    )
                    response = governed_result.response
                except _GovernedHttpResponseError as governed_failure:
                    # Reuse the exact response object that crossed the wire. The
                    # gate has already recorded this physical attempt as failed;
                    # the helper now applies its existing body/status/Retry-After,
                    # overflow-heal, backoff, and breaker policy without a second
                    # POST or a reconstructed response.
                    response = governed_failure.response
            response_ok = _http_response_is_ok(response)
            if not response_ok:
                # Log response body for error debugging before raising
                error_body = ""
                try:
                    error_body = response.text
                    logger.warning(
                        "[provider-helpers] HTTP %s from %s: %s",
                        response.status_code,
                        url,
                        error_body[:500] if error_body else "(empty)",
                    )
                except (RuntimeError, ValueError):
                    logger.warning(
                        "[provider-helpers] HTTP %s from %s (failed to read body)", response.status_code, url
                    )
                if getattr(response, "status_code", None) == 400 and overflow_heal_attempts < 3:
                    healed = shrink_max_tokens_for_context_overflow(payload, error_body)
                    if not healed and overflow_heal_attempts > 0 and "maximum context length" in (error_body or ""):
                        # The server-reported input is a synthetic lower bound
                        # (window+1-output); when shrinking to it still
                        # overflows, halve instead of trusting the number.
                        try:
                            current_max = int(payload.get("max_tokens") or 0)
                        except (TypeError, ValueError):
                            current_max = 0
                        if current_max > 128:
                            payload["max_tokens"] = max(64, current_max // 2)
                            logger.warning(
                                "[provider-helpers] context overflow heal #%s: halving max_tokens -> %s",
                                overflow_heal_attempts + 1,
                                payload["max_tokens"],
                            )
                            healed = True
                    if healed:
                        overflow_heal_attempts += 1
                        continue
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and 500 <= status_code < 600:
                    breaker.on_failure()
                    latency_ms = int((_clock.time() - start) * 1000)
                    usage = Usage.estimate(prompt, "")
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=usage,
                        error=(
                            f"{status_code} Server Error from {url}: {error_body[:500] if error_body else '(empty)'}"
                        ),
                    )
                if isinstance(status_code, int) and status_code == 429:
                    # Rate limiting (429) is transient, NOT a hard client error:
                    # retry with backoff (honoring Retry-After) instead of failing
                    # the call. The provider's generic ``retries`` budget defaults
                    # to 0, which cannot ride out shared-provider saturation, so
                    # 429 gets its own minimum budget. Live defect: kimi-for-coding
                    # returned persistent 429 under concurrent factory_bench load
                    # and every CE / Director LLM call failed on the first 429,
                    # materializing 0 source files (prod=0) — a provider-resilience
                    # gap, not a generation-quality gap.
                    #
                    # Critically, a 429 must NOT trip the circuit breaker: rate
                    # limiting is upstream back-pressure, not a service outage.
                    # Counting each retry as a breaker failure opened the SHARED
                    # circuit after a handful of 429s, after which every subsequent
                    # call (CE + Director) fast-failed with ``circuit_open`` for the
                    # whole recovery window — turning transient saturation into a
                    # total outage (prod=0). Retry/backoff is the correct response
                    # to 429, so we deliberately do not call breaker.on_failure().
                    rate_limit_attempt += 1
                    if rate_limit_attempt > max(retries, _rate_limit_min_retries()):
                        latency_ms = int((_clock.time() - start) * 1000)
                        usage = Usage.estimate(prompt, "")
                        return InvokeResult(
                            ok=False,
                            output="",
                            latency_ms=latency_ms,
                            usage=usage,
                            error=(
                                f"429 Rate limited by {url} after "
                                f"{rate_limit_attempt - 1} retries: "
                                f"{error_body[:300] if error_body else '(empty)'}"
                            ),
                        )
                    retry_after = _ph._parse_retry_after_seconds(response)
                    delay = (
                        retry_after
                        if retry_after is not None
                        else _build_backoff_seconds(
                            attempt=rate_limit_attempt,
                            base_delay_seconds=backoff_base_seconds,
                            max_delay_seconds=backoff_max_seconds,
                        )
                    )
                    _clock.sleep(delay)
                    continue
                if isinstance(status_code, int) and 400 <= status_code < 500:
                    breaker.on_failure()
                    latency_ms = int((_clock.time() - start) * 1000)
                    usage = Usage.estimate(prompt, "")
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=usage,
                        error=f"{status_code} Client Error from {url}: {error_body[:500] if error_body else '(empty)'}",
                    )
                response.raise_for_status()
            if governed_result is None:
                data = response.json()
                latency_ms = int((_clock.time() - start) * 1000)
                output = extract_output(data)
                finalized = LLMResponseParser.finalize_response(data, visible_text=output)
                usage = usage_from_response(prompt, finalized.output, data)
            else:
                data = governed_result.data
                latency_ms = governed_result.latency_ms
                output = governed_result.output
                finalized = governed_result.finalized
                usage = governed_result.usage
            # Canonical reasoning-aware finalization (DEFECT 2 SSoT): one funnel
            # recovers a content:null reasoning-model answer (qwen3.6/MiniMax-M3)
            # or fails closed on a mid-reasoning truncation, instead of silently
            # reporting an empty result. ``output`` is this provider's own content
            # extraction; the reasoning/finish_reason channels come from ``data``.
            breaker.on_success()  # transport succeeded even if the visible output was empty
            if not finalized.ok:
                return InvokeResult(
                    ok=False,
                    output="",
                    latency_ms=latency_ms,
                    usage=usage,
                    error=finalized.error,
                    raw=data,
                    thinking=finalized.thinking,
                )
            if finalized.thinking and not output.strip():
                logger.info(
                    "[provider-helpers] recovered answer from reasoning channel (reasoning_chars=%d)",
                    len(finalized.thinking),
                )
            return InvokeResult(
                ok=True,
                output=finalized.output,
                latency_ms=latency_ms,
                usage=usage,
                raw=data,
                thinking=finalized.thinking,
            )

        except (
            requests.RequestException,
            requests.Timeout,
            requests.ConnectionError,
            requests.HTTPError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            breaker.on_failure()
            attempt += 1
            if attempt > retries:
                latency_ms = int((_clock.time() - start) * 1000)
                usage = Usage.estimate(prompt, "")
                return InvokeResult(
                    ok=False,
                    output="",
                    latency_ms=latency_ms,
                    usage=usage,
                    error=str(exc),
                )

            delay = _build_backoff_seconds(
                attempt=attempt,
                base_delay_seconds=backoff_base_seconds,
                max_delay_seconds=backoff_max_seconds,
            )

            # Use injected clock for deterministic testability.
            # In production (RealClock), this delegates to time.sleep().
            _clock.sleep(delay)
        except (KeyboardInterrupt, SystemExit):
            raise


def health_check_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: HttpTimeout,
) -> HealthResult:
    """POST-based health check with standard error classification.

    Uses _blocking_http_post so the blocking requests.post() does not freeze
    the asyncio event loop when called from async contexts.
    """
    # Resolve through the package namespace for lossless monkeypatching.
    import polaris.infrastructure.llm.providers.provider_helpers as _ph

    start = time.time()
    try:
        response = _ph._blocking_http_post(url, headers, payload, timeout)
        latency_ms = int((time.time() - start) * 1000)
        status_code = int(getattr(response, "status_code", 0) or 0)

        if status_code == 401:
            return HealthResult(
                ok=False, latency_ms=latency_ms, error="Authentication failed: please check your API key"
            )
        if status_code == 404:
            return HealthResult(
                ok=False, latency_ms=latency_ms, error="API endpoint not found: please check api_path configuration"
            )
        if status_code == 429:
            retry_after = ""
            headers_value = getattr(response, "headers", None)
            if headers_value is not None and hasattr(headers_value, "get"):
                retry_after = str(headers_value.get("Retry-After") or "").strip()
            message = "Rate limited by provider: HTTP 429 Too Many Requests"
            if retry_after:
                message = f"{message}; retry after {retry_after} seconds"
            return HealthResult(ok=False, latency_ms=latency_ms, error=message)
        if status_code >= 400:
            response_text = str(getattr(response, "text", "") or "").strip().replace("\n", " ")
            if response_text:
                response_text = response_text[:300]
            message = f"Provider health check failed: HTTP {status_code}"
            if response_text:
                message = f"{message}: {response_text}"
            return HealthResult(ok=False, latency_ms=latency_ms, error=message)

        response.raise_for_status()
        return HealthResult(ok=True, latency_ms=latency_ms)
    except requests.exceptions.ConnectionError:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(
            ok=False, latency_ms=latency_ms, error="Network connection failed: please check your network and base_url"
        )
    except requests.exceptions.Timeout:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(
            ok=False, latency_ms=latency_ms, error="Request timeout: the server took too long to respond"
        )
    except requests.exceptions.HTTPError as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=f"HTTP health check failed: {exc}")
    except requests.exceptions.RequestException as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=f"Provider request failed: {exc}")
    except (RuntimeError, ValueError) as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))


def list_models_from_api(
    url: str,
    headers: dict[str, str],
    timeout: HttpTimeout,
    data_key: str = "data",
) -> ModelListResult:
    """GET-based model listing with standard response parsing.

    Uses _blocking_http_get so the blocking requests.get() does not freeze
    the asyncio event loop when called from async contexts.
    """
    # Resolve through the package namespace for lossless monkeypatching.
    import polaris.infrastructure.llm.providers.provider_helpers as _ph

    try:
        response = _ph._blocking_http_get(url, headers, timeout)
        response.raise_for_status()
        payload = response.json()
        models: list[ModelInfo] = []
        items = payload.get(data_key) if isinstance(payload, dict) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    model_id = str(item.get("id") or item.get("name") or "").strip()
                    if model_id:
                        models.append(ModelInfo(id=model_id, raw=item))
        return ModelListResult(ok=True, supported=True, models=models)
    except (RuntimeError, ValueError) as exc:
        return ModelListResult(ok=False, supported=True, models=[], error=str(exc))


__all__ = [
    "_CIRCUIT_BREAKER_LOCK",
    "_CIRCUIT_BREAKER_REGISTRY",
    "_RATE_LIMIT_MIN_RETRIES",
    "CircuitBreaker",
    "CircuitOpenError",
    "_GovernedHttpAttemptResult",
    "_GovernedHttpResponseError",
    "_build_backoff_seconds",
    "_http_response_is_ok",
    "_parse_retry_after_seconds",
    "_rate_limit_min_retries",
    "_send_governed_http_attempt",
    "get_circuit_breaker",
    "health_check_post",
    "invoke_with_retry",
    "list_models_from_api",
]
