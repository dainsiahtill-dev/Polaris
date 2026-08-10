"""Communication, network, timeout, and authentication errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class CommunicationError(KernelOneError):
    """Communication-related errors.

    Raised when network or inter-process communication fails.

    Attributes:
        endpoint: The communication endpoint.
        protocol: The protocol used (http, websocket, grpc, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "COMMUNICATION_ERROR",
        endpoint: str = "",
        protocol: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, code=code, **kwargs)
        self.endpoint = endpoint
        self.protocol = protocol
        if endpoint:
            self.details["endpoint"] = endpoint
        if protocol:
            self.details["protocol"] = protocol


class NetworkError(CommunicationError):
    """Network connectivity error.

    Raised when network requests fail due to connectivity issues.

    Attributes:
        url: The URL that was being accessed.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="NETWORK_ERROR",
            endpoint=url,
            protocol="http",
            **kwargs,
        )
        self.url = url


class WebSocketSendError(CommunicationError):
    """WebSocket send failed.

    Raised when sending a WebSocket message fails.
    """

    def __init__(
        self,
        message: str,
        *,
        session_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="WEBSOCKET_SEND_ERROR",
            protocol="websocket",
            **kwargs,
        )
        if session_id:
            self.details["session_id"] = session_id


class TimeoutError(CommunicationError):
    """Operation timed out.

    Note: This shadows Python's built-in TimeoutError intentionally
    for unified error handling within KernelOne.

    Attributes:
        timeout_seconds: The configured timeout that was exceeded.
        operation: What was being performed when timeout occurred.
    """

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
        operation: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TIMEOUT_ERROR",
            **kwargs,
        )
        self.timeout_seconds = timeout_seconds
        self.operation = operation
        if timeout_seconds is not None:
            self.details["timeout_seconds"] = timeout_seconds
        if operation:
            self.details["operation"] = operation


class RateLimitError(CommunicationError):
    """Rate limit exceeded.

    Raised when API rate limits are hit.

    Attributes:
        retry_after: Seconds to wait before retrying.
        limit_type: Type of limit hit (requests, tokens, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        limit_type: str = "requests",
        **kwargs,
    ) -> None:
        super().__init__(message, code="RATE_LIMIT_ERROR", **kwargs)
        self.retry_after = retry_after
        self.limit_type = limit_type
        if retry_after is not None:
            self.details["retry_after"] = retry_after
        self.details["limit_type"] = limit_type


class CircuitBreakerOpenError(CommunicationError):
    """Circuit breaker is open.

    Raised when a circuit breaker has tripped and is refusing requests.

    Attributes:
        circuit_name: Name of the circuit breaker that is open.
        retry_after: Suggested seconds to wait before retrying.
    """

    def __init__(
        self,
        message: str = "Circuit breaker is open",
        *,
        circuit_name: str | None = None,
        retry_after: float | None = None,
        **kwargs,
    ) -> None:
        # Build detailed message if circuit_name is provided
        if message == "Circuit breaker is open" and circuit_name:
            message = f"Circuit breaker '{circuit_name}' is open"
            if retry_after is not None:
                message += f", retry after {retry_after:.1f}s"
        super().__init__(message, code="CIRCUIT_BREAKER_OPEN_ERROR", **kwargs)
        self.circuit_name = circuit_name
        self.retry_after = retry_after
        if circuit_name:
            self.details["circuit_name"] = circuit_name
        if retry_after is not None:
            self.details["retry_after"] = retry_after


class AuthenticationError(CommunicationError):
    """Authentication failed.

    Raised when API authentication fails.

    Attributes:
        provider: The provider that failed authentication.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="AUTHENTICATION_ERROR",
            **kwargs,
        )
        self.provider = provider
        if provider:
            self.details["provider"] = provider
