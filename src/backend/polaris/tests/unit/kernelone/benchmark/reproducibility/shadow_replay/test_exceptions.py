"""Tests for polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions."""

from __future__ import annotations

from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    CassetteFormatError,
    CassetteNotFoundError,
    SanitizationError,
    ShadowReplayError,
    UnrecordedRequestError,
)


class TestShadowReplayError:
    def test_is_exception(self) -> None:
        assert issubclass(ShadowReplayError, Exception)


class TestCassetteNotFoundError:
    def test_attributes(self) -> None:
        err = CassetteNotFoundError("abc", "/tmp/cass")
        assert err.cassette_id == "abc"
        assert err.cassette_dir == "/tmp/cass"
        assert "abc" in str(err)
        assert "/tmp/cass" in str(err)


class TestUnrecordedRequestError:
    def test_without_body_hash(self) -> None:
        err = UnrecordedRequestError("GET", "http://example.com")
        assert err.method == "GET"
        assert err.url == "http://example.com"
        assert err.body_hash is None
        assert "GET" in str(err)
        assert "example.com" in str(err)

    def test_with_body_hash(self) -> None:
        err = UnrecordedRequestError("POST", "http://api.com", "hash123")
        assert err.body_hash == "hash123"
        assert "hash123" in str(err)


class TestCassetteFormatError:
    def test_is_shadow_replay_error(self) -> None:
        assert issubclass(CassetteFormatError, ShadowReplayError)


class TestSanitizationError:
    def test_is_shadow_replay_error(self) -> None:
        assert issubclass(SanitizationError, ShadowReplayError)
