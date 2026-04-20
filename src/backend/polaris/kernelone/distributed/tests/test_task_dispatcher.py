"""Tests for the Distributed Task Dispatcher."""

from __future__ import annotations

from datetime import datetime

import pytest
from polaris.kernelone.distributed.task_dispatcher import (
    DispatchResult,
    DistributedTaskDispatcher,
    NodeInfo,
    NodeRegistry,
    NodeStatus,
    TaskPacket,
)
from polaris.kernelone.workflow.task_queue import Task


@pytest.fixture
def registry() -> NodeRegistry:
    """Create a fresh NodeRegistry for each test."""
    return NodeRegistry()


@pytest.fixture
def dispatcher(registry: NodeRegistry) -> DistributedTaskDispatcher:
    """Create a DistributedTaskDispatcher with the given registry."""
    return DistributedTaskDispatcher(registry)


@pytest.fixture
def sample_task() -> Task:
    """Create a sample task for testing."""
    return Task(
        task_id="task-001",
        task_queue="test-queue",
        payload={"data": "test"},
        created_at=datetime.now(),
        metadata={"source": "test"},
        priority=1,
    )


@pytest.fixture
def sample_nodes() -> list[NodeInfo]:
    """Create a list of sample nodes for testing."""
    return [
        NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=0,
            metadata={"region": "us-east"},
        ),
        NodeInfo(
            node_id="node-2",
            address="node2.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=2,
            metadata={"region": "us-west"},
        ),
        NodeInfo(
            node_id="node-3",
            address="node3.example.com:8080",
            status=NodeStatus.BUSY,
            capacity=4,
            current_load=4,
            metadata={"region": "eu-west"},
        ),
    ]


class TestNodeRegistry:
    """Tests for NodeRegistry functionality."""

    @pytest.mark.asyncio
    async def test_register_node(self, registry: NodeRegistry) -> None:
        """Test node registration."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
        )
        await registry.register_node(node)

        result = await registry.get_node("node-1")
        assert result is not None
        assert result.node_id == "node-1"
        assert result.address == "node1.example.com:8080"
        assert result.status == NodeStatus.AVAILABLE

    @pytest.mark.asyncio
    async def test_unregister_node(self, registry: NodeRegistry) -> None:
        """Test node unregistration."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
        )
        await registry.register_node(node)
        await registry.unregister_node("node-1")

        result = await registry.get_node("node-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_available_nodes(self, registry: NodeRegistry, sample_nodes: list[NodeInfo]) -> None:
        """Test filtering available nodes."""
        for node in sample_nodes:
            await registry.register_node(node)

        available = await registry.get_available_nodes()
        assert len(available) == 2
        assert all(n.status == NodeStatus.AVAILABLE for n in available)
        # Should be sorted by load (node-1 has load 0, node-2 has load 2)
        assert available[0].node_id == "node-1"
        assert available[1].node_id == "node-2"

    @pytest.mark.asyncio
    async def test_update_node_status(self, registry: NodeRegistry) -> None:
        """Test updating node status."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
        )
        await registry.register_node(node)
        await registry.update_node_status("node-1", NodeStatus.BUSY)

        result = await registry.get_node("node-1")
        assert result is not None
        assert result.status == NodeStatus.BUSY

    @pytest.mark.asyncio
    async def test_update_node_load(self, registry: NodeRegistry) -> None:
        """Test updating node load."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=0,
        )
        await registry.register_node(node)
        await registry.update_node_load("node-1", 2)

        result = await registry.get_node("node-1")
        assert result is not None
        assert result.current_load == 2

    @pytest.mark.asyncio
    async def test_list_all_nodes(self, registry: NodeRegistry, sample_nodes: list[NodeInfo]) -> None:
        """Test listing all registered nodes."""
        for node in sample_nodes:
            await registry.register_node(node)

        all_nodes = await registry.list_all_nodes()
        assert len(all_nodes) == 3


class TestDistributedTaskDispatcher:
    """Tests for DistributedTaskDispatcher functionality."""

    @pytest.mark.asyncio
    async def test_dispatch_to_available_node(
        self, registry: NodeRegistry, dispatcher: DistributedTaskDispatcher, sample_task: Task
    ) -> None:
        """Test dispatching a task to an available node."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=0,
        )
        await registry.register_node(node)

        result = await dispatcher.dispatch(sample_task, ["node-1"])

        assert result.success is True
        assert result.node_id == "node-1"
        assert result.task_id == sample_task.task_id

    @pytest.mark.asyncio
    async def test_dispatch_no_available_node(
        self, registry: NodeRegistry, dispatcher: DistributedTaskDispatcher, sample_task: Task
    ) -> None:
        """Test dispatch fails when no node is available."""
        result = await dispatcher.dispatch(sample_task, ["node-1"])

        assert result.success is False
        assert result.node_id == ""
        assert result.error is not None
        assert "No available node" in result.error

    @pytest.mark.asyncio
    async def test_dispatch_selects_from_target_list(
        self, registry: NodeRegistry, dispatcher: DistributedTaskDispatcher, sample_task: Task
    ) -> None:
        """Test that dispatch selects from the target node list."""
        node1 = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=0,
        )
        node2 = NodeInfo(
            node_id="node-2",
            address="node2.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=0,
        )
        await registry.register_node(node1)
        await registry.register_node(node2)

        result = await dispatcher.dispatch(sample_task, ["node-2"])

        assert result.success is True
        assert result.node_id == "node-2"

    @pytest.mark.asyncio
    async def test_dispatch_updates_node_load(
        self, registry: NodeRegistry, dispatcher: DistributedTaskDispatcher, sample_task: Task
    ) -> None:
        """Test that dispatch updates the node's current load."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=0,
        )
        await registry.register_node(node)

        await dispatcher.dispatch(sample_task, ["node-1"])

        updated = await registry.get_node("node-1")
        assert updated is not None
        assert updated.current_load == 1

    @pytest.mark.asyncio
    async def test_dispatch_broadcast(
        self, registry: NodeRegistry, dispatcher: DistributedTaskDispatcher, sample_task: Task
    ) -> None:
        """Test broadcasting a task to all available nodes."""
        nodes = [
            NodeInfo(
                node_id=f"node-{i}",
                address=f"node{i}.example.com:8080",
                status=NodeStatus.AVAILABLE,
                capacity=4,
                current_load=0,
            )
            for i in range(1, 4)
        ]
        for node in nodes:
            await registry.register_node(node)

        results = await dispatcher.dispatch_broadcast(sample_task)

        assert len(results) == 3
        assert all(r.success for r in results)
        assert {r.node_id for r in results} == {"node-1", "node-2", "node-3"}

    @pytest.mark.asyncio
    async def test_dispatch_broadcast_no_nodes(
        self, registry: NodeRegistry, dispatcher: DistributedTaskDispatcher, sample_task: Task
    ) -> None:
        """Test broadcast fails gracefully when no nodes available."""
        results = await dispatcher.dispatch_broadcast(sample_task)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "No available nodes" in results[0].error


class TestTaskPacket:
    """Tests for TaskPacket serialization."""

    def test_task_packet_creation(self) -> None:
        """Test TaskPacket creation with all fields."""
        packet = TaskPacket(
            task_id="task-001",
            task_type="test-queue",
            payload={"key": "value"},
            source_node="source-1",
            priority=5,
        )
        assert packet.task_id == "task-001"
        assert packet.task_type == "test-queue"
        assert packet.payload == {"key": "value"}
        assert packet.source_node == "source-1"
        assert packet.priority == 5

    def test_task_packet_immutable(self) -> None:
        """Test that TaskPacket is immutable."""
        packet = TaskPacket(
            task_id="task-001",
            task_type="test-queue",
            payload={},
            source_node="source-1",
        )
        with pytest.raises(AttributeError):
            packet.task_id = "modified"  # type: ignore


class TestNodeInfo:
    """Tests for NodeInfo dataclass."""

    def test_node_info_creation(self) -> None:
        """Test NodeInfo creation with all fields."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
            current_load=1,
            metadata={"region": "us-east"},
        )
        assert node.node_id == "node-1"
        assert node.address == "node1.example.com:8080"
        assert node.status == NodeStatus.AVAILABLE
        assert node.capacity == 4
        assert node.current_load == 1
        assert node.metadata == {"region": "us-east"}

    def test_node_info_immutable(self) -> None:
        """Test that NodeInfo is immutable."""
        node = NodeInfo(
            node_id="node-1",
            address="node1.example.com:8080",
            status=NodeStatus.AVAILABLE,
            capacity=4,
        )
        with pytest.raises(AttributeError):
            node.status = NodeStatus.BUSY  # type: ignore


class TestDispatchResult:
    """Tests for DispatchResult dataclass."""

    def test_dispatch_result_success(self) -> None:
        """Test successful dispatch result."""
        result = DispatchResult(
            task_id="task-001",
            node_id="node-1",
            success=True,
        )
        assert result.task_id == "task-001"
        assert result.node_id == "node-1"
        assert result.success is True
        assert result.error is None

    def test_dispatch_result_failure(self) -> None:
        """Test failed dispatch result."""
        result = DispatchResult(
            task_id="task-001",
            node_id="node-1",
            success=False,
            error="Connection refused",
        )
        assert result.success is False
        assert result.error == "Connection refused"
