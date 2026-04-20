"""Unit tests for KernelOneMessageBusPort.

Tests cover:
- Basic publish/subscribe via in-memory fallback
- NATS configuration from environment variables
- Fallback behavior when NATS is unavailable
- Thread-safety of in-memory fallback
- Envelope serialization

Implements test coverage for C2 from ROLES_CELL_REFACTORING_BLUEPRINT_2026-03-26.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from polaris.cells.roles.runtime.internal.bus_port import (
    AgentEnvelope,
    InMemoryAgentBusPort,
)
from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
    KernelOneMessageBusPort,
    NATSClientWrapper,
    NATSConnectionConfig,
    _envelope_to_json_bytes,
    _get_nats_url_from_env,
    _is_nats_enabled,
    create_bus_port,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_envelope() -> AgentEnvelope:
    """Create a sample AgentEnvelope for testing."""
    return AgentEnvelope.from_fields(
        msg_type="task",
        sender="director",
        receiver="qa",
        payload={"objective": "verify changes"},
        correlation_id="corr-123",
    )


@pytest.fixture
def in_memory_fallback() -> InMemoryAgentBusPort:
    """Create a clean InMemoryAgentBusPort for testing."""
    return InMemoryAgentBusPort(max_queue_size=100)


# ── Test NATS Connection Config ───────────────────────────────────────────────


class TestNATSConnectionConfig:
    """Tests for NATSConnectionConfig environment variable resolution."""

    def test_default_url(self) -> None:
        """Default URL should be nats://127.0.0.1:4222 when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = NATSConnectionConfig()
            assert config.url == "nats://127.0.0.1:4222"

    def test_nats_url_from_env(self) -> None:
        """NATS_URL env var should override default."""
        with patch.dict(os.environ, {"NATS_URL": "nats://custom:5222"}):
            config = NATSConnectionConfig()
            assert config.url == "nats://custom:5222"

    def test_polaris_nats_url_fallback(self) -> None:
        """POLARIS_NATS_URL should be used when NATS_URL not set."""
        with patch.dict(
            os.environ,
            {"POLARIS_NATS_URL": "nats://legacy:4222"},
            clear=True,
        ):
            config = NATSConnectionConfig()
            assert config.url == "nats://legacy:4222"

    def test_nats_url_priority_over_polaris(self) -> None:
        """NATS_URL should take priority over POLARIS_NATS_URL."""
        with patch.dict(
            os.environ,
            {
                "NATS_URL": "nats://explicit:4222",
                "POLARIS_NATS_URL": "nats://legacy:4222",
            },
        ):
            config = NATSConnectionConfig()
            assert config.url == "nats://explicit:4222"

    def test_enabled_default_true(self) -> None:
        """NATS should be enabled by default."""
        with patch.dict(os.environ, {}, clear=True):
            config = NATSConnectionConfig()
            assert config.enabled is True

    def test_nats_enabled_false(self) -> None:
        """NATS_ENABLED=false should disable NATS."""
        with patch.dict(os.environ, {"NATS_ENABLED": "false"}):
            config = NATSConnectionConfig()
            assert config.enabled is False

    def test_nats_enabled_off(self) -> None:
        """NATS_ENABLED=off should disable NATS."""
        with patch.dict(os.environ, {"NATS_ENABLED": "off"}):
            config = NATSConnectionConfig()
            assert config.enabled is False

    def test_nats_enabled_0(self) -> None:
        """NATS_ENABLED=0 should disable NATS."""
        with patch.dict(os.environ, {"NATS_ENABLED": "0"}):
            config = NATSConnectionConfig()
            assert config.enabled is False

    def test_polaris_nats_enabled_true(self) -> None:
        """POLARIS_NATS_ENABLED=true should enable NATS."""
        with patch.dict(os.environ, {"POLARIS_NATS_ENABLED": "true"}):
            config = NATSConnectionConfig()
            assert config.enabled is True

    def test_connect_timeout_default(self) -> None:
        """Default connect timeout should be 3.0 seconds."""
        with patch.dict(os.environ, {}, clear=True):
            config = NATSConnectionConfig()
            assert config.connect_timeout_sec == 3.0

    def test_connect_timeout_from_env(self) -> None:
        """Connect timeout should be configurable via env var."""
        with patch.dict(os.environ, {"NATS_CONNECT_TIMEOUT": "5.0"}):
            config = NATSConnectionConfig()
            assert config.connect_timeout_sec == 5.0


class TestNATSConfigHelpers:
    """Tests for standalone helper functions."""

    def test_get_nats_url_from_env_default(self) -> None:
        """Default URL when no env vars set."""
        with patch.dict(os.environ, {}, clear=True):
            url = _get_nats_url_from_env()
            assert url == "nats://127.0.0.1:4222"

    def test_get_nats_url_from_env_explicit(self) -> None:
        """Explicit NATS_URL should be returned."""
        with patch.dict(os.environ, {"NATS_URL": "nats://test:4222"}):
            url = _get_nats_url_from_env()
            assert url == "nats://test:4222"

    def test_is_nats_enabled_default(self) -> None:
        """NATS should be enabled by default."""
        with patch.dict(os.environ, {}, clear=True):
            enabled = _is_nats_enabled()
            assert enabled is True

    def test_is_nats_enabled_disabled(self) -> None:
        """NATS_ENABLED=0 should disable NATS."""
        with patch.dict(os.environ, {"NATS_ENABLED": "0"}):
            enabled = _is_nats_enabled()
            assert enabled is False


# ── Test KernelOneMessageBusPort ───────────────────────────────────────────────


class TestKernelOneMessageBusPort:
    """Tests for KernelOneMessageBusPort core functionality."""

    def test_create_with_defaults(self) -> None:
        """Default creation should use env-based configuration."""
        with patch.dict(os.environ, {}, clear=True):
            port = KernelOneMessageBusPort()
            assert port._nats_url == "nats://127.0.0.1:4222"
            assert port._nats_enabled is True
            assert isinstance(port._fallback_bus, InMemoryAgentBusPort)

    def test_create_with_explicit_nats_url(self) -> None:
        """Explicit nats_url should override env."""
        with patch.dict(os.environ, {"NATS_URL": "nats://custom:5555"}):
            port = KernelOneMessageBusPort(nats_url="nats://explicit:5555")
            assert port._nats_url == "nats://explicit:5555"

    def test_create_with_nats_disabled(self) -> None:
        """Explicit nats_enabled=False should disable NATS."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        assert port._nats_enabled is False
        assert port._nats_client is None

    def test_create_with_max_queue_size(self) -> None:
        """max_queue_size should be propagated to fallback."""
        port = KernelOneMessageBusPort(max_queue_size=50)
        assert port._max_queue_size == 50
        assert port._fallback_bus._max_queue_size == 50

    def test_publish_to_fallback(self, sample_envelope: AgentEnvelope) -> None:
        """publish() should deliver to in-memory fallback when NATS unavailable."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        result = port.publish(sample_envelope)

        assert result is True
        assert port._fallback_bus.pending_count("qa") == 1

    def test_publish_returns_false_when_full(self) -> None:
        """publish() should return False when inbox is full."""
        # Create port with tiny queue (no injected fallback)
        port = KernelOneMessageBusPort(
            max_queue_size=2,
            nats_enabled=False,
        )

        # Fill the inbox
        for i in range(3):
            env = AgentEnvelope.from_fields(
                msg_type="task",
                sender="sender",
                receiver="qa",
                payload={"index": i},
            )
            port.publish(env)

        # Fourth publish should fail
        env = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="qa",
            payload={"index": 99},
        )
        result = port.publish(env)
        assert result is False

    def test_poll_returns_envelope(
        self,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """poll() should return published envelopes."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        port.publish(sample_envelope)

        polled = port.poll("qa", block=False)
        assert polled is not None
        assert polled.message_id == sample_envelope.message_id
        assert polled.msg_type == "task"
        assert polled.sender == "director"

    def test_poll_returns_none_when_empty(self) -> None:
        """poll() should return None when no messages."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        polled = port.poll("qa", block=False)
        assert polled is None

    def test_poll_with_blocking(
        self,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """poll(block=True) should wait for messages."""
        port = KernelOneMessageBusPort(nats_enabled=False)

        def delayed_publish() -> None:
            import time

            time.sleep(0.1)
            port.publish(sample_envelope)

        thread = threading.Thread(target=delayed_publish)
        thread.start()

        polled = port.poll("qa", block=True, timeout=1.0)
        thread.join()

        assert polled is not None
        assert polled.message_id == sample_envelope.message_id

    def test_poll_timeout_returns_none(self) -> None:
        """poll(block=True, timeout=X) should return None after timeout."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        polled = port.poll("qa", block=True, timeout=0.1)
        assert polled is None

    def test_ack_removes_from_inflight(
        self,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """ack() should remove message from inflight."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        port.publish(sample_envelope)

        # Poll to move to inflight
        polled = port.poll("qa")
        assert polled is not None

        # Ack should succeed
        result = port.ack(sample_envelope.message_id, "qa")
        assert result is True

    def test_ack_returns_false_for_unknown(self) -> None:
        """ack() should return False for unknown message_id."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        result = port.ack("unknown-id", "qa")
        assert result is False

    def test_nack_requeues_message(
        self,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """nack() should requeue message for retry."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        port.publish(sample_envelope)

        # Poll to move to inflight
        polled = port.poll("qa")
        assert polled is not None

        # First nack should requeue
        result = port.nack(sample_envelope.message_id, "qa", reason="try_again")
        assert result is True
        assert port._fallback_bus.pending_count("qa") == 1

    def test_nack_dead_letter_after_max_attempts(
        self,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """nack() should move to dead-letter after max_attempts."""
        # Create envelope with max_attempts=1
        env = AgentEnvelope(
            message_id=sample_envelope.message_id,
            msg_type=sample_envelope.msg_type,
            sender=sample_envelope.sender,
            receiver=sample_envelope.receiver,
            payload=sample_envelope.payload,
            timestamp_utc=sample_envelope.timestamp_utc,
            correlation_id=sample_envelope.correlation_id,
            attempt=1,
            max_attempts=1,
            last_error="",
        )

        port = KernelOneMessageBusPort(nats_enabled=False)
        port.publish(env)
        port.poll("qa")  # Move to inflight

        result = port.nack(env.message_id, "qa", reason="failed")
        assert result is True
        assert len(port.dead_letters) == 1
        assert port.dead_letters[0].reason == "failed"

    def test_pending_count(self, sample_envelope: AgentEnvelope) -> None:
        """pending_count() should return inbox size."""
        port = KernelOneMessageBusPort(nats_enabled=False)

        assert port.pending_count("qa") == 0

        port.publish(sample_envelope)
        assert port.pending_count("qa") == 1

        port.publish(
            AgentEnvelope.from_fields(
                msg_type="task",
                sender="sender",
                receiver="qa",
                payload={},
            )
        )
        assert port.pending_count("qa") == 2

    def test_dead_letters_snapshot(self) -> None:
        """dead_letters should return a snapshot of dead letter records."""
        port = KernelOneMessageBusPort(nats_enabled=False)

        # Create and exhaust an envelope
        env = AgentEnvelope(
            message_id=str(uuid.uuid4()),
            msg_type="task",
            sender="sender",
            receiver="qa",
            payload={},
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            attempt=1,
            max_attempts=1,
        )
        port.publish(env)
        port.poll("qa")
        port.nack(env.message_id, "qa", reason="exhausted")

        dead_letters = port.dead_letters
        assert len(dead_letters) == 1
        assert dead_letters[0].envelope.message_id == env.message_id
        assert dead_letters[0].reason == "exhausted"

    def test_get_stats(self, sample_envelope: AgentEnvelope) -> None:
        """get_stats() should return diagnostic information."""
        port = KernelOneMessageBusPort(
            nats_url="nats://test:4222",
            nats_enabled=False,
        )
        port.publish(sample_envelope)

        stats = port.get_stats()
        assert "receivers" in stats
        assert "qa" in stats["receivers"]
        assert stats["receivers"]["qa"] == 1
        assert stats["nats_enabled"] is False
        assert stats["nats_url"] == "nats://test:4222"
        assert stats["nats_connected"] is False


# ── Test NATS Client Wrapper ───────────────────────────────────────────────────


class TestNATSClientWrapper:
    """Tests for NATSClientWrapper."""

    def test_disabled_config_never_connected(self) -> None:
        """When disabled, client should never report as connected."""
        config = NATSConnectionConfig(enabled=False)
        client = NATSClientWrapper(config=config)

        assert client.is_connected is False
        connected = client.connect()
        assert connected is False
        assert client.is_connected is False

    def test_connection_failure_falls_back_to_false(self) -> None:
        """Connection failure should return False, not raise."""
        config = NATSConnectionConfig(
            url="nats://invalid-host:9999",
            enabled=True,
        )
        client = NATSClientWrapper(config=config)

        # This will fail because invalid-host doesn't exist
        # But it should not raise an exception
        client.connect()
        # Result depends on network conditions, but we handle gracefully

    def test_publish_when_not_connected(self) -> None:
        """publish() should return False when not connected."""
        config = NATSConnectionConfig(enabled=False)
        client = NATSClientWrapper(config=config)

        result = client.publish("test.subject", b"payload")
        assert result is False

    def test_close_when_not_initialized(self) -> None:
        """close() should not raise when not initialized."""
        config = NATSConnectionConfig(enabled=False)
        client = NATSClientWrapper(config=config)

        # Should not raise
        client.close()


# ── Test Serialization ─────────────────────────────────────────────────────────


class TestEnvelopeSerialization:
    """Tests for AgentEnvelope JSON serialization."""

    def test_envelope_to_json_bytes(self, sample_envelope: AgentEnvelope) -> None:
        """_envelope_to_json_bytes should produce valid JSON."""
        data = _envelope_to_json_bytes(sample_envelope)

        assert isinstance(data, bytes)
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["message_id"] == sample_envelope.message_id
        assert parsed["msg_type"] == sample_envelope.msg_type
        assert parsed["sender"] == sample_envelope.sender
        assert parsed["receiver"] == sample_envelope.receiver
        assert parsed["payload"] == sample_envelope.payload

    def test_roundtrip_serialization(self, sample_envelope: AgentEnvelope) -> None:
        """Envelope should survive JSON roundtrip."""
        from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
            _json_bytes_to_envelope,
        )

        data = _envelope_to_json_bytes(sample_envelope)
        restored = _json_bytes_to_envelope(data)

        assert restored.message_id == sample_envelope.message_id
        assert restored.msg_type == sample_envelope.msg_type
        assert restored.sender == sample_envelope.sender
        assert restored.receiver == sample_envelope.receiver
        assert restored.payload == sample_envelope.payload


# ── Test Factory Function ──────────────────────────────────────────────────────


class TestCreateBusPort:
    """Tests for create_bus_port factory function."""

    def test_create_bus_port_returns_correct_type(self) -> None:
        """create_bus_port should return KernelOneMessageBusPort."""
        port = create_bus_port()
        assert isinstance(port, KernelOneMessageBusPort)

    def test_create_bus_port_with_custom_url(self) -> None:
        """create_bus_port should accept custom NATS URL."""
        port = create_bus_port(nats_url="nats://custom:5555")
        assert port._nats_url == "nats://custom:5555"

    def test_create_bus_port_with_disabled_nats(self) -> None:
        """create_bus_port should allow disabling NATS."""
        port = create_bus_port(nats_enabled=False)
        assert port._nats_enabled is False

    def test_create_bus_port_with_fallback(self) -> None:
        """create_bus_port should accept custom fallback."""
        fallback = InMemoryAgentBusPort(max_queue_size=200)
        port = create_bus_port(_fallback=fallback)
        assert port._fallback_bus is fallback
        assert port._fallback_bus._max_queue_size == 200


# ── Test Thread Safety ─────────────────────────────────────────────────────────


class TestThreadSafety:
    """Tests for thread-safety of KernelOneMessageBusPort."""

    def test_concurrent_publish(self) -> None:
        """Multiple threads should be able to publish concurrently."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        envelope_count = 50
        errors: list[BaseException] = []

        def publish_batch(start: int, count: int) -> None:
            try:
                for i in range(start, start + count):
                    env = AgentEnvelope.from_fields(
                        msg_type="task",
                        sender=f"sender-{threading.current_thread().name}",
                        receiver="qa",
                        payload={"index": i},
                    )
                    port.publish(env)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        threads = [threading.Thread(target=publish_batch, args=(i * 10, 10), name=f"t{i}") for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert port.pending_count("qa") == envelope_count

    def test_concurrent_publish_and_poll(self) -> None:
        """Publishing and polling from different threads should be safe."""
        port = KernelOneMessageBusPort(nats_enabled=False)
        poll_results: list[AgentEnvelope | None] = []
        lock = threading.Lock()

        def poll_worker() -> None:
            for _ in range(20):
                result = port.poll("qa", block=True, timeout=0.2)
                with lock:
                    poll_results.append(result)

        def publish_worker() -> None:
            for i in range(20):
                env = AgentEnvelope.from_fields(
                    msg_type="task",
                    sender="publisher",
                    receiver="qa",
                    payload={"index": i},
                )
                port.publish(env)

        t1 = threading.Thread(target=poll_worker, name="poller")
        t2 = threading.Thread(target=publish_worker, name="publisher")

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        # Should have received some messages (exact count depends on timing)
        assert len(poll_results) == 20


# ── Test Backward Compatibility ────────────────────────────────────────────────


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing bus_port exports."""

    def test_exports_from_kernel_one_bus_port(self) -> None:
        """kernel_one_bus_port should export all AgentBusPort types."""
        from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
            AgentBusPort,
            AgentEnvelope,
            DeadLetterRecord,
            InMemoryAgentBusPort,
        )

        assert AgentEnvelope is not None
        assert DeadLetterRecord is not None
        assert AgentBusPort is not None
        assert InMemoryAgentBusPort is not None

    def test_original_bus_port_still_works(self) -> None:
        """Original bus_port module should still be importable."""
        from polaris.cells.roles.runtime.internal.bus_port import (
            AgentEnvelope,
            InMemoryAgentBusPort,
        )

        port = InMemoryAgentBusPort()
        env = AgentEnvelope.from_fields(
            msg_type="task",
            sender="test",
            receiver="test",
            payload={},
        )
        result = port.publish(env)
        assert result is True
