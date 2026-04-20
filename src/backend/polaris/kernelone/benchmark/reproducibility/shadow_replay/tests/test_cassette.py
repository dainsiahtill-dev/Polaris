"""Unit tests for cassette data structures."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING

from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    CASSETTE_VERSION,
    Cassette,
    CassetteEntry,
    CassetteFormat,
    HTTPRequest,
    HTTPResponse,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestHTTPRequest:
    """Tests for HTTPRequest dataclass."""

    def test_from_raw_with_body(self) -> None:
        """Test creating HTTPRequest from raw bytes."""
        body = b'{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}'
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test", "Content-Type": "application/json"},
            body=body,
        )

        assert request.method == "POST"
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert "Authorization" in request.headers
        # Body hash should be SHA256 of body
        expected_hash = hashlib.sha256(body).hexdigest()[:32]
        assert request.body_hash == expected_hash
        # Full body stored as base64
        assert request.body is not None
        assert base64.b64decode(request.body.encode()) == body

    def test_from_raw_without_body(self) -> None:
        """Test creating HTTPRequest without body."""
        request = HTTPRequest.from_raw(
            method="GET",
            url="https://api.example.com/data",
            headers={},
            body=None,
        )

        assert request.method == "GET"
        assert request.body_hash == ""
        assert request.body is None

    def test_get_body_bytes(self) -> None:
        """Test decoding stored body."""
        original = b"Hello, World!"
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=original,
        )

        decoded = request.get_body_bytes()
        assert decoded == original

    def test_body_preview_truncated(self) -> None:
        """Test that large bodies store preview but not full content."""
        large_body = b"x" * 2000
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=large_body,
            max_body_size=1000,  # Force truncation
        )

        # Should have preview
        assert request.body_preview is not None
        assert len(request.body_preview) <= 500
        # Full body should not be stored due to size limit
        assert request.body is None


class TestHTTPResponse:
    """Tests for HTTPResponse dataclass."""

    def test_from_raw_with_body(self) -> None:
        """Test creating HTTPResponse from raw bytes."""
        body = b'{"choices": [{"message": {"content": "Hello!"}}]}'
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=body,
            tokens_used=50,
        )

        assert response.status_code == 200
        assert "Content-Type" in response.headers
        assert response.tokens_used == 50
        # Full body stored
        assert response.body is not None
        assert base64.b64decode(response.body.encode()) == body

    def test_from_raw_without_body(self) -> None:
        """Test creating HTTPResponse without body."""
        response = HTTPResponse.from_raw(
            status_code=204,
            headers={},
            body=None,
        )

        assert response.status_code == 204
        assert response.body_hash == ""
        assert response.body is None

    def test_get_body_bytes(self) -> None:
        """Test decoding stored response body."""
        original = b'{"result": "ok"}'
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=original,
        )

        decoded = response.get_body_bytes()
        assert decoded == original


class TestCassetteEntry:
    """Tests for CassetteEntry."""

    def test_to_dict_roundtrip(self) -> None:
        """Test serialization and deserialization."""
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=b"test",
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b'{"ok": true}',
        )

        entry = CassetteEntry(
            sequence=0,
            timestamp="2026-04-04T12:00:00Z",
            request=request,
            response=response,
            latency_ms=100.0,
        )

        # Serialize
        data = entry.to_dict()

        # Deserialize
        restored = CassetteEntry.from_dict(data)

        assert restored.sequence == 0
        assert restored.timestamp == "2026-04-04T12:00:00Z"
        assert restored.request.method == "POST"
        assert restored.response.status_code == 200
        assert restored.latency_ms == 100.0


class TestCassetteFormat:
    """Tests for CassetteFormat."""

    def test_find_entry_exact_match(self) -> None:
        """Test finding entry with exact body hash match."""
        format = CassetteFormat(
            cassette_id="test",
            created_at="2026-04-04T12:00:00Z",
            mode="record",
        )

        # Add entries
        request1 = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={},
            body=b"body1",
        )
        response1 = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b"resp1",
        )
        format.add_entry(
            CassetteEntry(
                sequence=0,
                timestamp="2026-04-04T12:00:00Z",
                request=request1,
                response=response1,
            )
        )

        # Find with exact body hash
        body_hash = hashlib.sha256(b"body1").hexdigest()[:32]
        found = format.find_entry("POST", "https://api.example.com", body_hash)
        assert found is not None
        assert found.sequence == 0

        # Find without body hash (should still find)
        found = format.find_entry("POST", "https://api.example.com", None)
        assert found is not None

        # Not found with wrong body hash
        found = format.find_entry("POST", "https://api.example.com", "wronghash")
        assert found is None

    def test_find_entry_url_mismatch(self) -> None:
        """Test that URL must match for find."""
        format = CassetteFormat(
            cassette_id="test",
            created_at="2026-04-04T12:00:00Z",
            mode="record",
        )

        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com/endpoint1",
            headers={},
            body=b"body",
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b"resp",
        )
        format.add_entry(
            CassetteEntry(
                sequence=0,
                timestamp="2026-04-04T12:00:00Z",
                request=request,
                response=response,
            )
        )

        # Same method, different URL
        found = format.find_entry("POST", "https://api.example.com/endpoint2", None)
        assert found is None

    def test_to_jsonl_format(self) -> None:
        """Test JSONL serialization format."""
        format = CassetteFormat(
            cassette_id="test-cassette",
            created_at="2026-04-04T12:00:00Z",
            mode="both",
        )

        request = HTTPRequest.from_raw(
            method="GET",
            url="https://api.example.com",
            headers={},
            body=None,
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b"body",
        )
        format.add_entry(
            CassetteEntry(
                sequence=0,
                timestamp="2026-04-04T12:00:00Z",
                request=request,
                response=response,
            )
        )

        jsonl = format.to_jsonl()
        lines = jsonl.strip().split("\n")

        # First line is header
        header = json.loads(lines[0])
        assert header["cassette_id"] == "test-cassette"
        assert header["version"] == CASSETTE_VERSION

        # Second line is entry
        entry = json.loads(lines[1])
        assert entry["sequence"] == 0


class TestCassette:
    """Tests for Cassette high-level interface."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Test that save and load preserve data."""
        cassette = Cassette(
            cassette_id="roundtrip-test",
            cassette_dir=tmp_path,
            mode="record",
        )

        # Add entry
        request = HTTPRequest.from_raw(
            method="POST",
            url="https://api.example.com",
            headers={"Authorization": "Bearer secret"},
            body=b'{"key": "value"}',
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b'{"result": "ok"}',
        )
        cassette.add_entry(request=request, response=response)
        cassette.save()

        # Load in new instance
        loaded = Cassette(
            cassette_id="roundtrip-test",
            cassette_dir=tmp_path,
            mode="replay",
        )
        loaded.load()

        assert len(loaded.format.entries) == 1
        assert loaded.format.entries[0].request.method == "POST"
        assert loaded.format.entries[0].response.status_code == 200

    def test_add_entry_increments_sequence(self, tmp_path: Path) -> None:
        """Test that entries get sequential sequence numbers."""
        cassette = Cassette(
            cassette_id="seq-test",
            cassette_dir=tmp_path,
        )

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
                body=b"ok",
            )
            cassette.add_entry(request=request, response=response)

        assert cassette.format.entries[0].sequence == 0
        assert cassette.format.entries[1].sequence == 1
        assert cassette.format.entries[2].sequence == 2

    def test_clear_removes_entries(self, tmp_path: Path) -> None:
        """Test that clear removes all entries."""
        cassette = Cassette(
            cassette_id="clear-test",
            cassette_dir=tmp_path,
        )

        # Add entry
        request = HTTPRequest.from_raw(
            method="GET",
            url="https://api.example.com",
            headers={},
            body=None,
        )
        response = HTTPResponse.from_raw(
            status_code=200,
            headers={},
            body=b"ok",
        )
        cassette.add_entry(request=request, response=response)
        assert len(cassette.format.entries) == 1

        # Clear
        cassette.clear()
        assert len(cassette.format.entries) == 0
