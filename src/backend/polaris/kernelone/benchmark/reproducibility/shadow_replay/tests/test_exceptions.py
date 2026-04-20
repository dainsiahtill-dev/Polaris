"""Unit tests for exceptions."""

from __future__ import annotations

from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    CassetteNotFoundError,
    ShadowReplayError,
    UnrecordedRequestError,
)


class TestExceptions:
    """Tests for exception classes."""

    def test_shadow_replay_error(self) -> None:
        """Test base exception."""
        error = ShadowReplayError("test message")
        assert str(error) == "test message"
        assert isinstance(error, Exception)

    def test_cassette_not_found_error(self) -> None:
        """Test CassetteNotFoundError."""
        error = CassetteNotFoundError(
            cassette_id="my-cassette",
            cassette_dir="/tmp/cassettes",
        )
        assert error.cassette_id == "my-cassette"
        assert error.cassette_dir == "/tmp/cassettes"
        assert "my-cassette" in str(error)
        assert "/tmp/cassettes" in str(error)

    def test_unrecorded_request_error(self) -> None:
        """Test UnrecordedRequestError."""
        error = UnrecordedRequestError(
            method="POST",
            url="https://api.example.com",
            body_hash="abc123",
        )
        assert error.method == "POST"
        assert error.url == "https://api.example.com"
        assert error.body_hash == "abc123"
        assert "POST" in str(error)
        assert "api.example.com" in str(error)
        assert "abc123" in str(error)

    def test_unrecorded_request_error_without_hash(self) -> None:
        """Test UnrecordedRequestError without body hash."""
        error = UnrecordedRequestError(
            method="GET",
            url="https://api.example.com",
        )
        assert error.body_hash is None
        assert "body_hash" not in str(error)
