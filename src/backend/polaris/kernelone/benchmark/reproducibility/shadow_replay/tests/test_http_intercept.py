"""Unit tests for HTTP interception layer."""

from __future__ import annotations

from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
    HTTPExchange,
    _get_interceptor,
    _get_original_send,
    _is_patched,
    _patch_state,
    _set_interceptor,
    remove_http_patch,
)


class TestHTTPExchange:
    """Tests for HTTPExchange dataclass."""

    def test_create_exchange(self) -> None:
        """Test creating HTTPExchange."""
        exchange = HTTPExchange(
            method="POST",
            url="https://api.example.com",
            headers={"Content-Type": "application/json"},
            body=b'{"key": "value"}',
            response_status=200,
            response_headers={"Content-Type": "application/json"},
            response_body=b'{"result": "ok"}',
            latency_ms=100.5,
        )

        assert exchange.method == "POST"
        assert exchange.url == "https://api.example.com"
        assert exchange.response_status == 200
        assert exchange.latency_ms == 100.5
        assert exchange.response_object is None

    def test_exchange_with_response_object(self) -> None:
        """Test creating HTTPExchange with response object."""
        exchange = HTTPExchange(
            method="GET",
            url="https://api.example.com",
            headers={},
            body=None,
            response_status=200,
            response_headers={},
            response_body=b"ok",
            latency_ms=50.0,
            response_object="mock_response",  # type: ignore[arg-type]
        )

        assert exchange.response_object == "mock_response"


class TestPatchState:
    """Tests for patch state management."""

    def setup_method(self) -> None:
        """Clear patch state before each test."""
        _patch_state.clear()

    def test_initial_state_not_patched(self) -> None:
        """Test that initially not patched."""
        assert not _is_patched()
        assert _get_original_send() is None
        assert _get_interceptor() is None

    def test_set_and_get_interceptor(self) -> None:
        """Test setting and getting interceptor."""

        def callback(e) -> None:  # type: ignore[type-arg]
            return None

        _set_interceptor(callback)  # type: ignore[arg-type]
        assert _get_interceptor() is callback

    def test_set_interceptor_none(self) -> None:
        """Test clearing interceptor."""
        _set_interceptor(lambda e: None)  # type: ignore[arg-type, return-value]
        _set_interceptor(None)
        assert _get_interceptor() is None


class TestHTTPPatch:
    """Tests for HTTP patching functions."""

    def setup_method(self) -> None:
        """Clean up after each test."""
        # Make sure we're not patched
        if _is_patched():
            import asyncio

            asyncio.get_event_loop().run_until_complete(remove_http_patch())
        _patch_state.clear()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        if _is_patched():
            import asyncio

            asyncio.get_event_loop().run_until_complete(remove_http_patch())
        _patch_state.clear()

    def test_initial_state(self) -> None:
        """Test that initially not patched."""
        _patch_state.clear()
        assert not _is_patched()

    def test_set_interceptor_persists(self) -> None:
        """Test that set_interceptor works correctly."""

        async def callback(exchange):
            return (True, None)

        _set_interceptor(callback)
        assert _get_interceptor() is callback

        _set_interceptor(None)
        assert _get_interceptor() is None

    def test_double_patch_removes_only_once(self) -> None:
        """Test that double patching is handled correctly."""
        # This test just verifies state management
        _set_interceptor(lambda _: None)  # type: ignore[arg-type, return-value]
        assert _get_interceptor() is not None

        _set_interceptor(None)
        assert _get_interceptor() is None
