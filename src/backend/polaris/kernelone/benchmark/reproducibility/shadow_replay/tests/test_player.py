"""Unit tests for ShadowPlayer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    Cassette,
    HTTPRequest,
    HTTPResponse,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    UnrecordedRequestError,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
    HTTPExchange,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.player import (
    MockResponse,
    ShadowPlayer,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestMockResponse:
    """Tests for MockResponse."""

    def test_basic_attributes(self) -> None:
        """Test basic MockResponse attributes."""
        response = MockResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=b'{"result": "ok"}',
        )

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "application/json"
        assert response.content == b'{"result": "ok"}'
        assert response.text == '{"result": "ok"}'

    def test_json_parsing(self) -> None:
        """Test JSON parsing."""
        response = MockResponse(
            status_code=200,
            headers={},
            content=b'{"choices": [{"message": {"content": "Hello"}}]}',
        )

        data = response.json()
        assert data["choices"][0]["message"]["content"] == "Hello"

    def test_is_stream_consumed(self) -> None:
        """Test stream consumption tracking."""
        response = MockResponse(
            status_code=200,
            headers={},
            content=b"ok",
        )

        # httpx.Response initializes is_stream_consumed to True
        # Our MockResponse inherits this behavior
        assert response.is_stream_consumed is True
        # But we can set it
        response.is_stream_consumed = False
        assert not response.is_stream_consumed

    def test_repr(self) -> None:
        """Test string representation."""
        response = MockResponse(
            status_code=404,
            headers={"Content-Type": "text/plain"},
            content=b"Not Found",
        )

        repr_str = repr(response)
        assert "404" in repr_str
        assert "MockResponse" in repr_str


class TestShadowPlayer:
    """Tests for ShadowPlayer."""

    @pytest.mark.asyncio
    async def test_player_finds_entry(self, tmp_path: Path) -> None:
        """Test that player finds matching entry."""
        cassette = Cassette(
            cassette_id="test",
            cassette_dir=tmp_path,
        )

        # Add entry
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            headers={},
            body=b'{"model": "gpt-4"}',
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b'{"choices": [{"message": {"content": "Hi"}}]}',
        )
        cassette.add_entry(request=request, response=response)

        player = ShadowPlayer(cassette=cassette, strict=False)
        exchange = HTTPExchange(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            headers={},
            body=b'{"model": "gpt-4"}',
            response_status=0,
            response_headers={},
            response_body=None,
            latency_ms=0.0,
        )

        should_proceed, mock_resp = await player.intercept(exchange)

        assert not should_proceed
        assert mock_resp is not None
        assert mock_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_player_strict_raises_error(self, tmp_path: Path) -> None:
        """Test that strict mode raises UnrecordedRequestError."""
        cassette = Cassette(
            cassette_id="test",
            cassette_dir=tmp_path,
        )

        # Empty cassette - no entries

        player = ShadowPlayer(cassette=cassette, strict=True)
        exchange = HTTPExchange(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            headers={},
            body=b'{"model": "gpt-4"}',
            response_status=0,
            response_headers={},
            response_body=None,
            latency_ms=0.0,
        )

        with pytest.raises(UnrecordedRequestError) as exc_info:
            await player.intercept(exchange)

        assert exc_info.value.method == "POST"
        assert exc_info.value.url == "https://api.openai.com/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_player_non_strict_returns_404(self, tmp_path: Path) -> None:
        """Test that non-strict mode returns 404 for missing entries."""
        cassette = Cassette(
            cassette_id="test",
            cassette_dir=tmp_path,
        )

        player = ShadowPlayer(cassette=cassette, strict=False)
        exchange = HTTPExchange(
            method="GET",
            url="https://api.example.com",
            headers={},
            body=None,
            response_status=0,
            response_headers={},
            response_body=None,
            latency_ms=0.0,
        )

        should_proceed, mock_resp = await player.intercept(exchange)

        assert not should_proceed
        assert mock_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_player_body_hash_matching(self, tmp_path: Path) -> None:
        """Test that body hash is used for precise matching."""
        cassette = Cassette(
            cassette_id="test",
            cassette_dir=tmp_path,
        )

        # Add entry with specific body
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=b'{"id": 1}',
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b'{"result": "one"}',
        )
        cassette.add_entry(request=request, response=response)

        # Add another with different body
        request2 = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=b'{"id": 2}',
        )
        response2 = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b'{"result": "two"}',
        )
        cassette.add_entry(request=request2, response=response2)

        player = ShadowPlayer(cassette=cassette, strict=False)

        # Exchange with body matching second entry
        exchange = HTTPExchange(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=b'{"id": 2}',
            response_status=0,
            response_headers={},
            response_body=None,
            latency_ms=0.0,
        )

        _should_proceed, mock_resp = await player.intercept(exchange)

        # Should return second entry's response
        assert mock_resp.content == b'{"result": "two"}'

    @pytest.mark.asyncio
    async def test_player_playback_count(self, tmp_path: Path) -> None:
        """Test that playback count is tracked."""
        cassette = Cassette(
            cassette_id="test",
            cassette_dir=tmp_path,
        )

        # Add entries
        for i in range(3):
            request = HTTPRequest.from_raw(
                method="GET",
                url=f"https://api.example.com/{i}",
                headers={},
                body=None,
            )
            response = HTTPResponse.from_raw(
                status_code=200,
                headers={},
                body=f'{{"id": {i}}}'.encode(),
            )
            cassette.add_entry(request=request, response=response)

        player = ShadowPlayer(cassette=cassette, strict=False)

        assert player.playback_count == 0
        assert player.miss_count == 0

        # Replay all 3
        for i in range(3):
            exchange = HTTPExchange(
                method="GET",
                url=f"https://api.example.com/{i}",
                headers={},
                body=None,
                response_status=0,
                response_headers={},
                response_body=None,
                latency_ms=0.0,
            )
            await player.intercept(exchange)

        assert player.playback_count == 3
        assert player.miss_count == 0
