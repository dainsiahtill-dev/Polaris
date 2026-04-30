"""Tests for polaris.delivery.ws.endpoints.signature_utils."""

from __future__ import annotations

from collections import deque

import pytest
from polaris.delivery.ws.endpoints.signature_utils import (
    filter_status_payload_by_roles,
    remember_stream_signature,
    status_signature,
    stream_seen,
    stream_signature,
)


class TestStatusSignature:
    def test_basic_dict(self) -> None:
        payload = {"key": "value", "num": 123}
        sig = status_signature(payload)
        assert isinstance(sig, str)
        assert "key" in sig

    def test_deterministic(self) -> None:
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        assert status_signature(p1) == status_signature(p2)

    def test_unicode(self) -> None:
        payload = {"msg": "你好"}
        sig = status_signature(payload)
        assert "你好" in sig

    def test_non_serializable_fallback(self) -> None:
        payload = {"key": {1, 2, 3}}
        # TypeError is raised for non-serializable objects
        with pytest.raises(TypeError):
            status_signature(payload)


class TestFilterStatusPayloadByRoles:
    def test_no_roles(self) -> None:
        payload = {"pm_status": "active", "director_status": "idle"}
        result = filter_status_payload_by_roles(payload, set())
        assert result == payload

    def test_pm_role_only(self) -> None:
        payload = {"pm_status": "active", "director_status": "idle", "other": "data"}
        result = filter_status_payload_by_roles(payload, {"pm"})
        assert result["pm_status"] == "active"
        assert result["director_status"] is None
        assert result["other"] == "data"

    def test_director_role_only(self) -> None:
        payload = {"pm_status": "active", "director_status": "idle"}
        result = filter_status_payload_by_roles(payload, {"director"})
        assert result["pm_status"] is None
        assert result["director_status"] == "idle"

    def test_both_roles(self) -> None:
        payload = {"pm_status": "active", "director_status": "idle"}
        result = filter_status_payload_by_roles(payload, {"pm", "director"})
        assert result["pm_status"] == "active"
        assert result["director_status"] == "idle"


class TestStreamSignature:
    def test_with_event_id(self) -> None:
        payload = {"event_id": "evt-123"}
        sig = stream_signature(channel="system", line="test", payload=payload)
        assert sig == "system:event:evt-123"

    def test_with_run_id_and_seq(self) -> None:
        payload = {"run_id": "run-1", "seq": 5}
        sig = stream_signature(channel="llm", line="test", payload=payload)
        assert sig == "llm:run:run-1:seq:5"

    def test_without_ids(self) -> None:
        sig = stream_signature(channel="system", line="hello world", payload={})
        assert sig == "system:line:hello world"

    def test_long_line_truncated(self) -> None:
        long_line = "x" * 1000
        sig = stream_signature(channel="system", line=long_line, payload=None)
        assert len(sig) < 600
        assert sig.endswith("x" * 512)

    def test_none_payload(self) -> None:
        sig = stream_signature(channel="process", line="test", payload=None)
        assert sig == "process:line:test"

    def test_empty_line(self) -> None:
        sig = stream_signature(channel="system", line="", payload=None)
        assert sig == "system:line:"


class TestStreamSeen:
    def test_not_seen(self) -> None:
        assert stream_seen(set(), "sig1") is False

    def test_seen(self) -> None:
        assert stream_seen({"sig1"}, "sig1") is True

    def test_empty_signature(self) -> None:
        assert stream_seen(set(), "") is False


class TestRememberStreamSignature:
    def test_adds_signature(self) -> None:
        signatures = set()
        order = deque()
        remember_stream_signature(signatures, order, "sig1")
        assert "sig1" in signatures
        assert "sig1" in order

    def test_deduplicates(self) -> None:
        signatures = set()
        order = deque()
        remember_stream_signature(signatures, order, "sig1")
        remember_stream_signature(signatures, order, "sig1")
        assert len(order) == 1

    def test_bounded_memory(self) -> None:
        signatures = set()
        order = deque()
        for i in range(140):
            remember_stream_signature(signatures, order, f"sig{i}", max_size=5)
        # max(128, 5) = 128, so limit is 128
        assert len(order) == 128
        assert len(signatures) == 128
        assert "sig0" not in signatures
        assert "sig139" in signatures

    def test_bounded_memory_large_max_size(self) -> None:
        signatures = set()
        order = deque()
        for i in range(10):
            remember_stream_signature(signatures, order, f"sig{i}", max_size=200)
        assert len(order) == 10
        assert len(signatures) == 10

    def test_empty_signature_ignored(self) -> None:
        signatures = set()
        order = deque()
        remember_stream_signature(signatures, order, "")
        assert len(signatures) == 0
