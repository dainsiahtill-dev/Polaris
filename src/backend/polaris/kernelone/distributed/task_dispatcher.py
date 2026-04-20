"""Distributed Task Dispatcher - Multi-node task distribution for distributed execution.

This module provides the core components for distributing tasks across multiple compute nodes:
- NodeRegistry: Tracks available compute nodes and their status
- DistributedTaskDispatcher: Dispatches tasks to selected nodes with serialization support
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.workflow.task_queue import Task

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    """Status of a compute node in the distributed cluster."""

    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"


@dataclass(frozen=True)
class NodeInfo:
    """Information about a compute node.

    Attributes:
        node_id: Unique identifier for the node.
        address: Network address of the node (e.g., "node1.example.com:8080").
        status: Current operational status of the node.
        capacity: Maximum concurrent task capacity of the node.
        current_load: Number of tasks currently executing on the node.
        metadata: Additional node-specific information (e.g., region, tags).
    """

    node_id: str
    address: str
    status: NodeStatus
    capacity: int
    current_load: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchResult:
    """Result of a task dispatch operation.

    Attributes:
        task_id: Identifier of the dispatched task.
        node_id: Identifier of the target node.
        success: Whether the dispatch succeeded.
        error: Error message if dispatch failed.
    """

    task_id: str
    node_id: str
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class TaskPacket:
    """Serialized task for network transmission.

    Attributes:
        task_id: Unique task identifier.
        task_type: Type/category of the task for routing.
        payload: Serialized task data.
        source_node: Identifier of the originating node.
        priority: Task priority for scheduling.
    """

    task_id: str
    task_type: str
    payload: dict[str, Any]
    source_node: str
    priority: int = 0


class NodeRegistry:
    """Registry of available compute nodes.

    This class maintains the authoritative list of nodes in the distributed
    cluster and their current status. Thread-safe for concurrent access.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, NodeInfo] = {}
        self._lock = asyncio.Lock()
        logger.info("NodeRegistry initialized")

    async def register_node(self, node: NodeInfo) -> None:
        """Register a new compute node.

        Args:
            node: Node information to register.
        """
        async with self._lock:
            self._nodes[node.node_id] = node
            logger.info("Registered node %s at %s", node.node_id, node.address)

    async def unregister_node(self, node_id: str) -> None:
        """Unregister a compute node.

        Args:
            node_id: Identifier of the node to unregister.
        """
        async with self._lock:
            if node_id in self._nodes:
                del self._nodes[node_id]
                logger.info("Unregistered node %s", node_id)

    async def get_available_nodes(self) -> list[NodeInfo]:
        """Get all nodes with available status.

        Returns:
            List of available NodeInfo objects, sorted by load (ascending).
        """
        async with self._lock:
            available = [
                node
                for node in self._nodes.values()
                if node.status == NodeStatus.AVAILABLE and node.current_load < node.capacity
            ]
            return sorted(available, key=lambda n: n.current_load)

    async def update_node_status(self, node_id: str, status: NodeStatus) -> None:
        """Update the status of a registered node.

        Args:
            node_id: Identifier of the node to update.
            status: New status for the node.
        """
        async with self._lock:
            if node_id not in self._nodes:
                logger.warning("Attempted to update status for unknown node %s", node_id)
                return
            node = self._nodes[node_id]
            self._nodes[node_id] = NodeInfo(
                node_id=node.node_id,
                address=node.address,
                status=status,
                capacity=node.capacity,
                current_load=node.current_load,
                metadata=node.metadata,
            )
            logger.debug("Updated node %s status to %s", node_id, status.value)

    async def update_node_load(self, node_id: str, current_load: int) -> None:
        """Update the current load of a registered node.

        Args:
            node_id: Identifier of the node to update.
            current_load: New load value.
        """
        async with self._lock:
            if node_id not in self._nodes:
                logger.warning("Attempted to update load for unknown node %s", node_id)
                return
            node = self._nodes[node_id]
            self._nodes[node_id] = NodeInfo(
                node_id=node.node_id,
                address=node.address,
                status=node.status,
                capacity=node.capacity,
                current_load=current_load,
                metadata=node.metadata,
            )
            logger.debug("Updated node %s load to %d", node_id, current_load)

    async def get_node(self, node_id: str) -> NodeInfo | None:
        """Get information about a specific node.

        Args:
            node_id: Identifier of the node.

        Returns:
            NodeInfo if found, None otherwise.
        """
        async with self._lock:
            return self._nodes.get(node_id)

    async def list_all_nodes(self) -> list[NodeInfo]:
        """List all registered nodes.

        Returns:
            List of all NodeInfo objects.
        """
        async with self._lock:
            return list(self._nodes.values())


class DistributedTaskDispatcher:
    """Dispatches tasks to multiple nodes in a distributed cluster.

    This class handles task serialization, node selection, and result aggregation
    for distributed task execution.

    TODO(TD-008): Currently only implements local task dispatch with node registry.
    Real network transmission (e.g., HTTP/gRPC/WebSocket) to remote nodes is not
    implemented. The dispatch() and dispatch_broadcast() methods only update
    local node load tracking but do not actually send TaskPackets over the network.
    """

    def __init__(self, registry: NodeRegistry) -> None:
        """Initialize the dispatcher.

        Args:
            registry: NodeRegistry instance for node tracking.
        """
        self._registry = registry
        self._local_node_id = "local"
        logger.info("DistributedTaskDispatcher initialized")

    def _serialize_task(self, task: Task) -> TaskPacket:
        """Serialize a task for network transmission.

        Args:
            task: Task object to serialize.

        Returns:
            TaskPacket ready for transmission.
        """
        return TaskPacket(
            task_id=task.task_id,
            task_type=task.task_queue,
            payload=task.payload,
            source_node=self._local_node_id,
            priority=task.priority,
        )

    async def _select_node(self, target_nodes: list[str]) -> NodeInfo | None:
        """Select the best available node from target list.

        Prefers nodes with lower current load to balance distribution.

        Args:
            target_nodes: List of node IDs to select from.

        Returns:
            Selected NodeInfo or None if no suitable node found.
        """
        all_available = await self._registry.get_available_nodes()
        for node_id in target_nodes:
            for node in all_available:
                if node.node_id == node_id:
                    return node
        return None

    async def dispatch(self, task: Task, target_nodes: list[str]) -> DispatchResult:
        """Dispatch a single task to a target node.

        Args:
            task: Task to dispatch.
            target_nodes: Preferred list of node IDs (first available is used).

        Returns:
            DispatchResult indicating success or failure.
        """
        node = await self._select_node(target_nodes)
        if node is None:
            logger.error("No available node for task %s", task.task_id)
            return DispatchResult(
                task_id=task.task_id,
                node_id="",
                success=False,
                error="No available node found",
            )

        task_packet = self._serialize_task(task)
        logger.info(
            "Dispatching task %s to node %s (packet: %s)",
            task.task_id,
            node.node_id,
            task_packet,
        )

        try:
            # TODO(TD-008): Network transmission not implemented.
            # Here we would send task_packet to node.address over HTTP/gRPC/WebSocket
            # and await confirmation of receipt before updating load.
            await self._registry.update_node_load(node.node_id, node.current_load + 1)
            return DispatchResult(
                task_id=task.task_id,
                node_id=node.node_id,
                success=True,
            )
        except (RuntimeError, ValueError) as e:
            logger.exception("Failed to dispatch task %s", task.task_id)
            return DispatchResult(
                task_id=task.task_id,
                node_id=node.node_id,
                success=False,
                error=str(e),
            )

    async def dispatch_broadcast(self, task: Task) -> list[DispatchResult]:
        """Broadcast a task to all available nodes.

        Args:
            task: Task to broadcast.

        Returns:
            List of DispatchResult for each node attempt.
        """
        available_nodes = await self._registry.get_available_nodes()
        if not available_nodes:
            logger.warning("No available nodes for broadcast of task %s", task.task_id)
            return [
                DispatchResult(
                    task_id=task.task_id,
                    node_id="",
                    success=False,
                    error="No available nodes",
                )
            ]

        results: list[DispatchResult] = []
        for node in available_nodes:
            task_packet = self._serialize_task(task)
            logger.info(
                "Broadcasting task %s to node %s (packet: %s)",
                task.task_id,
                node.node_id,
                task_packet,
            )
            try:
                await self._registry.update_node_load(node.node_id, node.current_load + 1)
                results.append(
                    DispatchResult(
                        task_id=task.task_id,
                        node_id=node.node_id,
                        success=True,
                    )
                )
            except (RuntimeError, ValueError) as e:
                logger.exception("Failed to broadcast to node %s", node.node_id)
                results.append(
                    DispatchResult(
                        task_id=task.task_id,
                        node_id=node.node_id,
                        success=False,
                        error=str(e),
                    )
                )

        return results
