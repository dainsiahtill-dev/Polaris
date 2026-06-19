"""Tests for Nat-JetStream publication safety helpers.

Covers:
- Security validation functions: subject, workspace key, payload size, timestamp

SECURITY TESTS:
- S1: Payload size limits
- S2: Schema validation
- S3: Replay attack protection (timestamp freshness, event ID deduplication)
- S4: Cryptographically random consumer names
- S5: Subject pattern validation and sanitization
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
from polaris.delivery.http.routers import jetstream_utils
from polaris.delivery.http.routers.jetstream_utils import (
    generate_event_signature,
    publish_to_jetstream,
    validate_event_timestamp,
    validate_payload_size,
    validate_subject,
    validate_workspace_key,
    verify_event_signature,
)

# =============================================================================
# Security Validation Tests
# =============================================================================


class TestSecurityValidation:
    """Tests for security validation functions."""

    # S5: Subject pattern validation

    def test_valid_subjects(self) -> None:
        """Verify valid subject patterns are accepted."""
        valid_subjects = [
            "hp.runtime.my-workspace.event.factory",
            "events.test123",
            "a",
            "a.b.c",
            "test-workspace_123.run",
        ]
        for subject in valid_subjects:
            assert validate_subject(subject) is True, f"Subject should be valid: {subject}"

    def test_invalid_subjects(self) -> None:
        """Verify invalid subject patterns are rejected."""
        invalid_subjects = [
            "",  # empty
            "../../../etc/passwd",  # path traversal
            "subject\x00null",  # null byte injection
            "subject with spaces",  # spaces not allowed
            "subject;DROP TABLE",  # command injection attempt
            "subject$(whoami)",  # command substitution
            "a" * 201,  # too long (max 200 chars after first)
            "../workspace",  # path traversal
        ]
        for subject in invalid_subjects:
            assert validate_subject(subject) is False, f"Subject should be rejected: {subject}"

    # S4: Workspace key validation

    def test_valid_workspace_keys(self) -> None:
        """Verify valid workspace key formats are accepted."""
        valid_keys = [
            "my-workspace",
            "test123",
            "a",
            "workspace-with-123-dashes",
        ]
        for key in valid_keys:
            assert validate_workspace_key(key) is True, f"Workspace key should be valid: {key}"

    def test_invalid_workspace_keys(self) -> None:
        """Verify invalid workspace key formats are rejected."""
        invalid_keys = [
            "",  # empty
            "workspace.with.dots",  # dots not allowed
            "workspace_underscore",  # underscores not allowed
            "workspace with spaces",  # spaces not allowed
            "a" * 65,  # too long (max 64 chars)
            "../../etc",  # path traversal
        ]
        for key in invalid_keys:
            assert validate_workspace_key(key) is False, f"Workspace key should be rejected: {key}"

    # S2: Payload size validation

    def test_valid_payload_sizes(self) -> None:
        """Verify valid payload sizes are accepted."""
        from polaris.delivery.http.routers.jetstream_utils import MAX_PAYLOAD_SIZE

        # Small payload
        assert validate_payload_size({"key": "value"}) is True

        # Empty payload
        assert validate_payload_size({}) is True

        # Exactly at limit
        large_payload = "x" * MAX_PAYLOAD_SIZE
        assert validate_payload_size(large_payload) is True

        # Bytes at limit
        assert validate_payload_size(b"x" * MAX_PAYLOAD_SIZE) is True

    def test_oversized_payload_rejected(self) -> None:
        """Verify oversized payloads are rejected."""
        from polaris.delivery.http.routers.jetstream_utils import MAX_PAYLOAD_SIZE

        # Dict that's too large when serialized
        oversized = {"data": "x" * (MAX_PAYLOAD_SIZE + 1)}
        assert validate_payload_size(oversized) is False

        # String that's too large
        assert validate_payload_size("x" * (MAX_PAYLOAD_SIZE + 1)) is False

        # Bytes that are too large
        assert validate_payload_size(b"x" * (MAX_PAYLOAD_SIZE + 1)) is False

    # S3: Event timestamp freshness validation

    def test_fresh_timestamps_accepted(self) -> None:
        """Verify fresh timestamps are accepted."""
        # Current time
        now = datetime.now(timezone.utc).isoformat()
        assert validate_event_timestamp(now) is True

        # Recent timestamp (5 minutes ago)
        recent = datetime.fromtimestamp(time.time() - 300, tz=timezone.utc).isoformat()
        assert validate_event_timestamp(recent) is True

    def test_stale_timestamps_rejected(self) -> None:
        """Verify stale timestamps (replay window exceeded) are rejected."""
        from polaris.delivery.http.routers.jetstream_utils import MAX_REPLAY_WINDOW_SECONDS

        # Timestamp older than replay window
        old_ts = datetime.fromtimestamp(
            time.time() - MAX_REPLAY_WINDOW_SECONDS - 60,
            tz=timezone.utc,
        ).isoformat()
        assert validate_event_timestamp(old_ts) is False

    def test_missing_timestamp_allowed(self) -> None:
        """Verify missing timestamps are allowed for backward compatibility."""
        assert validate_event_timestamp(None) is True

    def test_non_utc_timestamps_allowed(self) -> None:
        """Verify non-UTC timestamps are allowed for compatibility."""
        # Non-UTC timestamps pass through for backward compatibility
        assert validate_event_timestamp("2026-05-01T12:00:00+08:00") is True

    # S1: Event signature generation and verification

    def test_signature_generation_and_verification(self) -> None:
        """Verify event signatures can be generated and verified."""
        event_id = "evt-123"
        timestamp = "2026-05-01T12:00:00Z"
        payload = {"key": "value"}

        signature = generate_event_signature(event_id, timestamp, payload)

        # Signature should be a hex string
        assert len(signature) == 64  # SHA256 hex = 64 chars

        # Same inputs should produce same signature
        assert signature == generate_event_signature(event_id, timestamp, payload)

        # Different inputs should produce different signature
        assert signature != generate_event_signature("evt-456", timestamp, payload)

        # Verification should succeed for valid signature
        assert verify_event_signature(event_id, timestamp, payload, signature) is True

    def test_signature_verification_fails_on_tampering(self) -> None:
        """Verify signature verification fails when payload is tampered."""
        event_id = "evt-123"
        timestamp = "2026-05-01T12:00:00Z"
        original_payload = {"key": "value"}
        tampered_payload = {"key": "tampered"}

        signature = generate_event_signature(event_id, timestamp, original_payload)

        # Verification should fail with tampered payload
        assert verify_event_signature(event_id, timestamp, tampered_payload, signature) is False


class TestNatJetStreamPublication:
    """Tests for the unified Nat-JetStream publication helper."""

    @pytest.mark.asyncio
    async def test_invalid_subject_is_rejected_before_publish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Subject injection attempts must not reach the messaging client."""

        async def fail_if_called() -> object:
            raise AssertionError("messaging client should not be requested for invalid subjects")

        monkeypatch.setattr(jetstream_utils, "get_default_client", fail_if_called, raising=False)

        assert await publish_to_jetstream("../bad", {"event": "x"}) is False

    @pytest.mark.asyncio
    async def test_missing_jetstream_client_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Publication failure is explicit when Nat-JetStream is unavailable."""
        import polaris.infrastructure.messaging as messaging

        async def no_client() -> None:
            return None

        monkeypatch.setattr(messaging, "get_default_client", no_client)

        assert await publish_to_jetstream("hp.runtime.test.event", {"event": "x"}) is False

    @pytest.mark.asyncio
    async def test_publish_timeout_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nat-JetStream publish timeouts must not escape the bool-returning helper."""
        import polaris.infrastructure.messaging as messaging

        class TimeoutClient:
            jetstream = object()

            async def publish_js(self, **_: object) -> None:
                raise asyncio.TimeoutError("publish timed out")

        async def timeout_client() -> TimeoutClient:
            return TimeoutClient()

        monkeypatch.setattr(messaging, "get_default_client", timeout_client)

        assert await publish_to_jetstream("hp.runtime.test.event", {"event": "x"}) is False
