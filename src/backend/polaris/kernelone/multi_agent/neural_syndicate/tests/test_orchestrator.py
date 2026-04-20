"""Unit tests for Neural Syndicate orchestrator module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.kernelone.multi_agent.neural_syndicate.orchestrator import (
    BlackboardClient,
    MessageForwarder,
    OrchestratorAgent,
    TaskResult,
    TaskState,
    create_orchestrator,
)
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentMessage,
    Intent,
    MessageType,
    Performative,
)


class TestTaskState:
    """Tests for TaskState enum."""

    def test_all_states(self) -> None:
        """All task states should be defined."""
        expected = {"pending", "in_progress", "waiting_consensus", "completed", "failed", "partial"}
        actual = {s.value for s in TaskState}
        assert expected.issubset(actual)


class TestTaskResult:
    """Tests for TaskResult dataclass."""

    def test_create_task_result(self) -> None:
        """TaskResult should be created correctly."""
        result = TaskResult(
            task_id="task-123",
            state=TaskState.COMPLETED,
            primary_result={"analysis": "complete"},
            confidence=0.9,
        )
        assert result.task_id == "task-123"
        assert result.state == TaskState.COMPLETED
        assert result.primary_result == {"analysis": "complete"}
        assert result.confidence == 0.9

    def test_task_result_defaults(self) -> None:
        """TaskResult should have sensible defaults."""
        result = TaskResult(task_id="task-1", state=TaskState.FAILED)
        assert result.primary_result is None
        assert result.confidence == 0.0
        assert result.votes == ()
        assert result.errors == ()

    def test_task_result_is_frozen(self) -> None:
        """TaskResult should be frozen."""
        import dataclasses

        result = TaskResult(task_id="task-1", state=TaskState.PENDING)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.state = TaskState.COMPLETED  # type: ignore[misc]


class TestMessageForwarder:
    """Tests for MessageForwarder."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create a mock broker."""
        broker = MagicMock()
        broker.publish_to_receivers = AsyncMock(return_value=1)
        return broker

    @pytest.fixture
    def mock_router(self) -> MagicMock:
        """Create a mock router."""
        router = MagicMock()
        router.route = AsyncMock()
        return router

    @pytest.fixture
    def forwarder(self, mock_broker: MagicMock, mock_router: MagicMock) -> MessageForwarder:
        """Create a message forwarder."""
        return MessageForwarder(
            broker=mock_broker,
            router=mock_router,
            max_hop_limit=10,
        )

    @pytest.mark.asyncio
    async def test_forward_success(self, forwarder: MessageForwarder, mock_broker: MagicMock) -> None:
        """forward should publish forwarded message to next hop."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=10,
            hop_count=0,
        )

        result = await forwarder.forward(msg, "worker-2")

        assert result is True
        mock_broker.publish_to_receivers.assert_called_once()
        call_args = mock_broker.publish_to_receivers.call_args
        forwarded_msg = call_args[0][0]
        assert forwarded_msg.hop_count == 1
        assert call_args[0][1] == ("worker-2",)

    @pytest.mark.asyncio
    async def test_forward_expired_message(self, forwarder: MessageForwarder) -> None:
        """forward on expired message should return False."""
        expired = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=0,
        )

        result = await forwarder.forward(expired, "c")

        assert result is False

    @pytest.mark.asyncio
    async def test_forward_hop_limit_exceeded(self, forwarder: MessageForwarder) -> None:
        """forward should return False when hop limit exceeded."""
        # Set max_hop_limit to 1
        forwarder._max_hop_limit = 1

        # Create message with ttl=1, hop_count=1 so remaining = min(0, 1) = 0 <= 0
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=1,
            hop_count=1,
        )
        result = await forwarder.forward(msg, "c")
        # remaining = min(0, 1) = 0 <= 0, should fail
        assert result is False

    @pytest.mark.asyncio
    async def test_forward_via_routing(self, forwarder: MessageForwarder, mock_router: MagicMock) -> None:
        """forward_via_routing should use router to determine next hops."""
        from polaris.kernelone.multi_agent.neural_syndicate.protocol import RouteDecision, RoutingStrategy

        msg = AgentMessage(
            sender="orchestrator",
            receiver="",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=10,
        )
        mock_router.route.return_value = RouteDecision(
            receivers=("worker-1", "worker-2"),
            strategy=RoutingStrategy.CAPABILITY_MATCH,
        )

        count = await forwarder.forward_via_routing(msg)

        assert count == 2
        mock_router.route.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_forward_via_routing_expired(self, forwarder: MessageForwarder) -> None:
        """forward_via_routing on expired message should return 0."""
        expired = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=0,
        )

        count = await forwarder.forward_via_routing(expired)

        assert count == 0

    def test_get_stats(self, forwarder: MessageForwarder) -> None:
        """get_stats should return forwarder statistics."""
        stats = forwarder.get_stats()
        assert "messages_forwarded" in stats
        assert "messages_dead_lettered" in stats
        assert "max_hop_limit" in stats


class TestBlackboardClient:
    """Tests for BlackboardClient."""

    @pytest.fixture
    def blackboard(self) -> BlackboardClient:
        """Create a blackboard client with mock workspace."""
        with patch.object(BlackboardClient, "__init__", lambda self, workspace, session_id=None: None):
            bb = BlackboardClient.__new__(BlackboardClient)
            bb._workspace = "."
            bb._session_id = "test-session-123"
            bb._cache = {}
            bb._lock = asyncio.Lock()
            return bb

    @pytest.mark.asyncio
    async def test_put_and_get(self, blackboard: BlackboardClient) -> None:
        """put and get should store and retrieve values."""
        await blackboard.put("key1", "value1")
        result = await blackboard.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_get_default(self, blackboard: BlackboardClient) -> None:
        """get with missing key should return default."""
        result = await blackboard.get("nonexistent", default="default_value")
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_delete_existing(self, blackboard: BlackboardClient) -> None:
        """delete should return True for existing key."""
        await blackboard.put("key1", "value1")
        result = await blackboard.delete("key1")
        assert result is True
        assert await blackboard.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, blackboard: BlackboardClient) -> None:
        """delete should return False for nonexistent key."""
        result = await blackboard.delete("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_store_task_result(self, blackboard: BlackboardClient) -> None:
        """store_task_result should store result and add to set."""
        await blackboard.store_task_result(
            task_id="task-1",
            agent_id="worker-1",
            result={"analysis": "complete"},
        )

        results = await blackboard.get_task_results("task-1")
        assert len(results) == 1
        assert results[0]["agent_id"] == "worker-1"

    @pytest.mark.asyncio
    async def test_get_task_results_multiple_agents(self, blackboard: BlackboardClient) -> None:
        """get_task_results should aggregate results from multiple agents."""
        await blackboard.store_task_result("task-1", "worker-1", {"part": 1})
        await blackboard.store_task_result("task-1", "worker-2", {"part": 2})

        results = await blackboard.get_task_results("task-1")
        assert len(results) == 2

    def test_get_stats(self, blackboard: BlackboardClient) -> None:
        """get_stats should return blackboard statistics."""
        stats = blackboard.get_stats()
        assert stats["session_id"] == "test-session-123"
        assert "cache_size" in stats


class TestOrchestratorAgent:
    """Tests for OrchestratorAgent."""

    @pytest.fixture
    def mock_bus_port(self) -> MagicMock:
        """Create a mock bus port."""
        port = MagicMock()
        port.publish = MagicMock(return_value=True)
        port.poll_async = AsyncMock(return_value=None)
        port.ack = MagicMock()
        port.nack = MagicMock()
        return port

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create a mock broker."""
        broker = MagicMock()
        broker.broadcast = AsyncMock(return_value=2)
        broker.publish = AsyncMock(return_value=True)
        broker.publish_to_receivers = AsyncMock(return_value=1)
        return broker

    @pytest.fixture
    def orchestrator(self, mock_bus_port: MagicMock, mock_broker: MagicMock) -> OrchestratorAgent:
        """Create an orchestrator agent."""
        return OrchestratorAgent(
            agent_id="orchestrator-1",
            bus_port=mock_bus_port,
            broker=mock_broker,
            workers=["worker-1", "worker-2"],
            critics=["critic-1"],
        )

    def test_agent_type(self, orchestrator: OrchestratorAgent) -> None:
        """agent_type should return 'orchestrator'."""
        assert orchestrator.agent_type == "orchestrator"

    def test_capabilities(self, orchestrator: OrchestratorAgent) -> None:
        """capabilities should include orchestration intents."""
        caps = orchestrator.capabilities
        assert len(caps) == 1
        cap = caps[0]
        assert cap.name == "orchestration"
        assert Intent.EXECUTE_TASK in cap.intents
        assert Intent.COORDINATE in cap.intents
        assert Intent.DELEGATE in cap.intents
        assert Intent.VOTE in cap.intents

    def test_get_stats(self, orchestrator: OrchestratorAgent) -> None:
        """get_stats should return orchestrator statistics."""
        stats = orchestrator.get_stats()
        assert "agent_id" in stats
        assert stats["workers"] == ["worker-1", "worker-2"]
        assert stats["critics"] == ["critic-1"]
        assert "blackboard" in stats
        assert "active_tasks" in stats

    @pytest.mark.asyncio
    async def test_submit_task_no_workers(self) -> None:
        """submit_task with no workers should return failed result."""
        mock_port = MagicMock()
        mock_port.poll_async = AsyncMock(return_value=None)
        orch = OrchestratorAgent(
            agent_id="orchestrator-1",
            bus_port=mock_port,
            workers=[],  # No workers
        )

        result = await orch.submit_task(
            task="Test task",
            intent=Intent.EXECUTE_TASK,
        )

        assert result.state == TaskState.FAILED

    @pytest.mark.asyncio
    async def test_delegate_to_workers_broadcasts(
        self, orchestrator: OrchestratorAgent, mock_broker: MagicMock
    ) -> None:
        """_delegate_to_workers should broadcast to all workers."""
        await orchestrator._delegate_to_workers(
            task_id="task-1",
            task="Analyze codebase",
            intent=Intent.SEARCH_CODE,
            payload={"path": "./src"},
        )

        # Broker broadcast should have been called (it's an async method)
        # Cast to MagicMock since orchestrator._broker is typed as InMemoryBroker
        mock_broker = orchestrator._broker  # type: ignore[assignment]
        assert mock_broker.broadcast.call_count > 0

    @pytest.mark.asyncio
    async def test_handle_message_vote_response(self, orchestrator: OrchestratorAgent) -> None:
        """_handle_message with VOTE_RESPONSE should delegate to consensus engine."""
        vote_payload = {
            "request_id": "req-123",
            "voter": "critic-1",
            "choice": "approve",
            "confidence": 0.85,
        }
        msg = AgentMessage(
            sender="critic-1",
            receiver="orchestrator-1",
            performative=Performative.VOTE_RESPONSE,
            intent=Intent.VOTE,
            message_type=MessageType.VOTE,
            payload=vote_payload,
            correlation_id="req-123",
        )

        result = await orchestrator._handle_message(msg)
        # Should return None (handled)
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_message_inform_result(self, orchestrator: OrchestratorAgent) -> None:
        """_handle_message with INFORM should collect result."""
        # First add task to active tasks
        orchestrator._active_tasks["task-1"] = {
            "task": "Test",
            "intent": Intent.EXECUTE_TASK,
            "payload": {},
            "require_consensus": False,
            "correlation_id": "task-1",
            "submitted_at": "2024-01-01T00:00:00Z",
            "worker_results": [],
            "state": TaskState.IN_PROGRESS,
        }

        msg = AgentMessage(
            sender="worker-1",
            receiver="orchestrator-1",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
            payload={
                "task_id": "task-1",
                "result": {"analysis": "done"},
                "confidence": 0.9,
            },
        )

        result = await orchestrator._handle_message(msg)
        assert result is None
        assert len(orchestrator._active_tasks["task-1"]["worker_results"]) == 1


class TestCreateOrchestrator:
    """Tests for create_orchestrator factory."""

    def test_create_orchestrator_basic(self) -> None:
        """create_orchestrator should create configured orchestrator."""
        orch = create_orchestrator(
            agent_id="orch-1",
            workers=["w1", "w2"],
            critics=["c1"],
            workspace="",
        )

        assert orch.agent_id == "orch-1"
        assert orch._workers == ["w1", "w2"]
        assert orch._critics == ["c1"]

    def test_create_orchestrator_with_blackboard(self) -> None:
        """create_orchestrator should create blackboard when workspace provided."""
        orch = create_orchestrator(
            agent_id="orch-1",
            workspace="/tmp/test",
        )

        assert orch._blackboard is not None
        assert orch._blackboard._workspace == "/tmp/test"
