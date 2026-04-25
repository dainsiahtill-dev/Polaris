"""Unit tests for KernelOneBusPortAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.adapters.kernelone.bus_adapter import KernelOneBusPortAdapter
from polaris.kernelone.ports.bus_port import AgentEnvelope, DeadLetterRecord, IBusPort


class TestKernelOneBusPortAdapter:
    """Tests for KernelOneBusPortAdapter."""

    @pytest.fixture
    def mock_impl(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def adapter(self, mock_impl: MagicMock) -> KernelOneBusPortAdapter:
        with patch(
            "polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort",
            return_value=mock_impl,
        ):
            return KernelOneBusPortAdapter()

    def test_is_instance_of_ibus_port(self, adapter: KernelOneBusPortAdapter) -> None:
        assert isinstance(adapter, IBusPort)

    def test_publish_delegates(self, adapter: KernelOneBusPortAdapter, mock_impl: MagicMock) -> None:
        envelope = AgentEnvelope.from_fields(
            msg_type="task",
            sender="a",
            receiver="b",
            payload={"key": "value"},
        )
        mock_impl.publish.return_value = True
        result = adapter.publish(envelope)
        mock_impl.publish.assert_called_once_with(envelope)
        assert result is True

    def test_poll_delegates(self, adapter: KernelOneBusPortAdapter, mock_impl: MagicMock) -> None:
        envelope = AgentEnvelope.from_fields(
            msg_type="task",
            sender="a",
            receiver="b",
            payload={},
        )
        mock_impl.poll.return_value = envelope
        result = adapter.poll("b", block=True, timeout=2.0)
        mock_impl.poll.assert_called_once_with("b", block=True, timeout=2.0)
        assert result is envelope

    @pytest.mark.asyncio
    async def test_poll_async_delegates(
        self,
        adapter: KernelOneBusPortAdapter,
        mock_impl: MagicMock,
    ) -> None:
        envelope = AgentEnvelope.from_fields(
            msg_type="task",
            sender="a",
            receiver="b",
            payload={},
        )
        import asyncio

        mock_impl.poll_async.return_value = asyncio.Future()
        mock_impl.poll_async.return_value.set_result(envelope)
        result = await adapter.poll_async("b", block=False, timeout=1.0)
        mock_impl.poll_async.assert_called_once_with("b", block=False, timeout=1.0)
        assert result is envelope

    def test_ack_delegates(self, adapter: KernelOneBusPortAdapter, mock_impl: MagicMock) -> None:
        mock_impl.ack.return_value = True
        result = adapter.ack("msg-1", "b")
        mock_impl.ack.assert_called_once_with("msg-1", "b")
        assert result is True

    def test_nack_delegates(self, adapter: KernelOneBusPortAdapter, mock_impl: MagicMock) -> None:
        mock_impl.nack.return_value = True
        result = adapter.nack("msg-1", "b", reason="fail", requeue=False)
        mock_impl.nack.assert_called_once_with("msg-1", "b", reason="fail", requeue=False)
        assert result is True

    def test_pending_count_delegates(self, adapter: KernelOneBusPortAdapter, mock_impl: MagicMock) -> None:
        mock_impl.pending_count.return_value = 5
        result = adapter.pending_count("b")
        mock_impl.pending_count.assert_called_once_with("b")
        assert result == 5

    def test_requeue_all_inflight_delegates(
        self,
        adapter: KernelOneBusPortAdapter,
        mock_impl: MagicMock,
    ) -> None:
        mock_impl.requeue_all_inflight.return_value = 3
        result = adapter.requeue_all_inflight("b")
        mock_impl.requeue_all_inflight.assert_called_once_with("b")
        assert result == 3

    def test_dead_letters_property(self, adapter: KernelOneBusPortAdapter, mock_impl: MagicMock) -> None:
        record = DeadLetterRecord(
            envelope=AgentEnvelope.from_fields(
                msg_type="task",
                sender="a",
                receiver="b",
                payload={},
            ),
            reason="timeout",
        )
        mock_impl.dead_letters = [record]
        result = adapter.dead_letters
        assert result == [record]
