"""Unit tests for ShadowRecorder."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    Cassette,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
    HTTPExchange,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.recorder import (
    ShadowRecorder,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestShadowRecorder:
    """Tests for ShadowRecorder."""

    @pytest.mark.asyncio
    async def test_recorder_intercept_creates_entry(self, tmp_path: Path) -> None:
        """Test that intercept creates a cassette entry."""
        cassette = Cassette(
            cassette_id="test",
            cassette_dir=tmp_path,
            mode="record",
        )

        recorder = ShadowRecorder(cassette=cassette, auto_save=False)

        exchange = HTTPExchange(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            body=b'{"model": "gpt-4"}',
            response_status=200,
            response_headers={"Content-Type": "application/json"},
            response_body=b'{"choices": []}',
            latency_ms=150.0,
            response_object="mock_response",
        )

        should_proceed, response = await recorder.intercept(exchange)

        # Should return (False, original_response)
        assert not should_proceed
        assert response == "mock_response"

        # Should have created entry
        assert recorder.exchange_count == 1
        assert len(cassette.format.entries) == 1

        entry = cassette.format.entries[0]
        assert entry.request.method == "POST"
        assert entry.request.url == "https://api.openai.com/v1/chat/completions"
        assert entry.response.status_code == 200
        assert entry.latency_ms == 150.0

    @pytest.mark.asyncio
    async def test_recorder_auto_save(self, tmp_path: Path) -> None:
        """Test that auto_save saves cassette after each entry."""
        cassette_dir = tmp_path / "autosave"
        cassette_dir.mkdir(exist_ok=True)

        cassette = Cassette(
            cassette_id="autosave-test",
            cassette_dir=cassette_dir,
            mode="record",
        )

        recorder = ShadowRecorder(cassette=cassette, auto_save=True)

        exchange = HTTPExchange(
            method="GET",
            url="https://api.example.com",
            headers={},
            body=None,
            response_status=200,
            response_headers={},
            response_body=b"ok",
            latency_ms=50.0,
            response_object="mock",
        )

        await recorder.intercept(exchange)

        # Cassette should be saved
        assert cassette.exists()

        # Should be loadable
        loaded = Cassette(
            cassette_id="autosave-test",
            cassette_dir=cassette_dir,
        )
        loaded.load()
        assert len(loaded.format.entries) == 1

    @pytest.mark.asyncio
    async def test_recorder_multiple_exchanges(self, tmp_path: Path) -> None:
        """Test recording multiple exchanges."""
        cassette = Cassette(
            cassette_id="multi-test",
            cassette_dir=tmp_path,
        )

        recorder = ShadowRecorder(cassette=cassette, auto_save=False)

        for i in range(5):
            exchange = HTTPExchange(
                method="GET",
                url=f"https://api.example.com/{i}",
                headers={},
                body=None,
                response_status=200,
                response_headers={},
                response_body=f"response {i}".encode(),
                latency_ms=10.0 * (i + 1),
                response_object="mock",
            )
            await recorder.intercept(exchange)

        assert recorder.exchange_count == 5
        assert len(cassette.format.entries) == 5

        # Sequences should be 0-4
        for i, entry in enumerate(cassette.format.entries):
            assert entry.sequence == i

    @pytest.mark.asyncio
    async def test_recorder_body_handling(self, tmp_path: Path) -> None:
        """Test that recorder handles request/response bodies correctly."""
        cassette = Cassette(
            cassette_id="body-test",
            cassette_dir=tmp_path,
        )

        recorder = ShadowRecorder(cassette=cassette, auto_save=False)

        original_request_body = b'{"messages": [{"role": "user", "content": "Hello"}]}'
        original_response_body = b'{"reply": "Hi there!"}'

        exchange = HTTPExchange(
            method="POST",
            url="https://api.example.com/chat",
            headers={},
            body=original_request_body,
            response_status=200,
            response_headers={},
            response_body=original_response_body,
            latency_ms=100.0,
            response_object="mock",
        )

        await recorder.intercept(exchange)

        entry = cassette.format.entries[0]

        # Verify body stored correctly
        stored_request_body = entry.request.get_body_bytes()
        assert stored_request_body == original_request_body

        stored_response_body = entry.response.get_body_bytes()
        assert stored_response_body == original_response_body
