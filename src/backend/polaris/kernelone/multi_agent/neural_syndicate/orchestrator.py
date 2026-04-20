"""Neural Syndicate Orchestrator - Multi-Agent Coordination and Message Forwarding.

This module provides:

1. **MessageForwarder**: Handles message TTL decrement and republishing for
   multi-hop message delivery. Prevents infinite loops with strict TTL enforcement.

2. **OrchestratorAgent**: Coordinates multiple worker agents, manages task
   decomposition, result aggregation, and self-correction via critic agents.

3. **BlackboardClient**: Integrates with RoleSessionArtifactService to provide
   shared context storage accessible by all agents.

Design decisions:
- Message forwarding is handled by the orchestrator, not individual agents
- Each task gets a unique task_id for tracking across agents
- Blackboard uses the existing RoleSessionArtifactService for storage
- OpenTelemetry traces are propagated via existing AuditContext patterns

Usage:
    # Create orchestrator with workers and critics
    orchestrator = OrchestratorAgent(
        agent_id="orchestrator-1",
        bus_port=bus_port,
        workers=["worker-1", "worker-2", "worker-3"],
        critics=["critic-1", "critic-2"],
    )
    await orchestrator.start()

    # Submit a task
    task_id = await orchestrator.submit_task(
        task="Analyze the codebase structure",
        intent=Intent.SEARCH_CODE,
    )
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass  # No runtime imports needed

from polaris.kernelone.constants import (
    ORCHESTRATOR_DEFAULT_CONFIDENCE_THRESHOLD,
    ORCHESTRATOR_DEFAULT_MAX_DELEGATION_DEPTH,
    ORCHESTRATOR_DEFAULT_TASK_TIMEOUT_SECONDS,
)
from polaris.kernelone.multi_agent.neural_syndicate.base_agent import BaseAgent
from polaris.kernelone.multi_agent.neural_syndicate.broker import InMemoryBroker
from polaris.kernelone.multi_agent.neural_syndicate.consensus import (
    ConsensusEngine,
    ConsensusResult,
)
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    ConsensusResponse,
    Intent,
    MessageType,
    Performative,
)
from polaris.kernelone.multi_agent.neural_syndicate.trace_context import (
    propagate_trace_context,
)
from polaris.kernelone.multi_agent.resource_quota import (
    QuotaExceededError,
    ResourceQuotaManager,
    create_resource_quota_manager,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Task State
# ═══════════════════════════════════════════════════════════════════════════


class TaskState(str, Enum):
    """Task execution state."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_CONSENSUS = "waiting_consensus"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # Some workers completed, some failed


@dataclass(frozen=True)
class TaskResult:
    """Result of a multi-agent task execution.

    Attributes:
        task_id: Unique task identifier
        state: Final task state
        primary_result: The primary result from the winning agent
        confidence: Confidence score (0.0 to 1.0)
        votes: All votes from critic agents
        elapsed_seconds: Time taken to complete
        errors: Any errors encountered
    """

    task_id: str
    state: TaskState
    primary_result: dict[str, Any] | None = None
    confidence: float = 0.0
    votes: tuple[ConsensusResponse, ...] = field(default_factory=tuple)
    elapsed_seconds: float = 0.0
    errors: tuple[str, ...] = field(default_factory=tuple)


# ═══════════════════════════════════════════════════════════════════════════
# Artifact Service Port (KernelOne Layer Contract)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Artifact:
    """An artifact stored in the blackboard."""

    id: str
    type: str
    content: str
    metadata: dict[str, Any]
    created_at: str
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class ArtifactServicePort(Protocol):
    """Port for artifact storage service.

    This protocol defines the interface for artifact storage, allowing
    different implementations to be injected without violating layer boundaries.
    """

    def write_artifact(
        self,
        session_id: str,
        artifact_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        """Write an artifact to storage."""

    def list_artifacts(
        self,
        session_id: str,
        artifact_type: str | None = None,
    ) -> list[Artifact]:
        """List artifacts in a session."""


# ═══════════════════════════════════════════════════════════════════════════
# Message Forwarder
# ═══════════════════════════════════════════════════════════════════════════


class MessageForwarder:
    """Handles message TTL decrement and republishing for multi-hop delivery.

    The forwarder is responsible for:
    1. Receiving messages that need to be forwarded to other agents
    2. Decrementing TTL and updating hop_count
    3. Republishing to the next hop via the broker
    4. Dead-lettering messages when TTL is exhausted

    Thread safety:
        - Uses asyncio.Lock for thread-safe forwarding
    """

    def __init__(
        self,
        broker: InMemoryBroker,
        router: Any,  # MessageRouter
        *,
        max_hop_limit: int = 10,
    ) -> None:
        """Initialize the message forwarder.

        Args:
            broker: Message broker for publishing forwarded messages
            router: Message router for determining next hop
            max_hop_limit: Maximum hop limit for forwarded messages
        """
        self._broker = broker
        self._router = router
        self._max_hop_limit = max(1, int(max_hop_limit))
        self._lock = asyncio.Lock()

        # Statistics
        self._messages_forwarded: int = 0
        self._messages_dead_lettered: int = 0

        logger.info(
            "MessageForwarder initialized (max_hop_limit=%d)",
            self._max_hop_limit,
        )

    async def forward(self, message: AgentMessage, next_hop: str) -> bool:
        """Forward a message to the next hop agent.

        This method:
        1. Checks if the message has remaining hops
        2. Creates a forwarded copy with decremented TTL
        3. Publishes to the next hop

        Args:
            message: The message to forward
            next_hop: The next agent to forward to

        Returns:
            True if forwarding succeeded, False otherwise
        """
        if message.is_expired:
            logger.info(
                "MessageForwarder: dropping expired message_id=%s (ttl=%d hops=%d)",
                message.message_id,
                message.ttl,
                message.hop_count,
            )
            self._messages_dead_lettered += 1
            return False

        async with self._lock:
            try:
                # Create forwarded message
                forwarded = message.with_forward(next_hop)

                # Propagate trace context for distributed tracing
                forwarded = propagate_trace_context(message, forwarded)

                # Cap hop limit
                remaining = min(forwarded.remaining_hops, self._max_hop_limit)
                if remaining <= 0:
                    logger.info(
                        "MessageForwarder: hop limit exceeded for message_id=%s",
                        message.message_id,
                    )
                    self._messages_dead_lettered += 1
                    return False

                # Republish to next hop
                success = await self._broker.publish_to_receivers(
                    forwarded,
                    (next_hop,),
                )

                if success:
                    self._messages_forwarded += 1
                    logger.debug(
                        "MessageForwarder: forwarded message_id=%s to %s (remaining_hops=%d)",
                        message.message_id,
                        next_hop,
                        remaining,
                    )

                return success > 0

            except ValueError as exc:
                # Message was expired (TTL <= 0)
                logger.info(
                    "MessageForwarder: cannot forward expired message_id=%s: %s",
                    message.message_id,
                    exc,
                )
                self._messages_dead_lettered += 1
                return False

    async def forward_via_routing(
        self,
        message: AgentMessage,
    ) -> int:
        """Forward a message using route decision.

        Uses the router to determine next hops and forwards accordingly.

        Args:
            message: The message to forward

        Returns:
            Number of successful forwards
        """
        if message.is_expired:
            self._messages_dead_lettered += 1
            return 0

        # Get routing decision
        decision = await self._router.route(message)

        if not decision.receivers:
            logger.warning(
                "MessageForwarder: no receivers for message_id=%s",
                message.message_id,
            )
            return 0

        # Forward to all receivers
        success_count = 0
        for receiver in decision.receivers:
            if await self.forward(message, receiver):
                success_count += 1

        return success_count

    def get_stats(self) -> dict[str, Any]:
        """Get forwarder statistics.

        Returns:
            Dictionary with forwarding stats
        """
        return {
            "messages_forwarded": self._messages_forwarded,
            "messages_dead_lettered": self._messages_dead_lettered,
            "max_hop_limit": self._max_hop_limit,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Blackboard Client
# ═══════════════════════════════════════════════════════════════════════════


class BlackboardClient:
    """Shared context storage for multi-agent collaboration.

    The blackboard provides:
    - Shared key-value storage for agent communication
    - Artifact storage for large data (code, documents, etc.)
    - Task result aggregation

    Uses ArtifactServicePort for artifact persistence (injected dependency).
    """

    def __init__(
        self,
        workspace: str,
        session_id: str | None = None,
        artifact_service: ArtifactServicePort | None = None,
    ) -> None:
        """Initialize the blackboard client.

        Args:
            workspace: Workspace path
            session_id: Session ID (auto-generated if None)
            artifact_service: Optional artifact service implementing ArtifactServicePort.
                If not provided, artifact operations will be disabled.
        """
        self._workspace = workspace
        self._session_id = session_id or f"syndicate_{uuid.uuid4().hex[:8]}"
        self._artifact_service: ArtifactServicePort | None = artifact_service

        # In-memory cache for fast access
        self._cache: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        logger.info(
            "BlackboardClient initialized (session_id=%s, has_artifact_service=%s)",
            self._session_id,
            artifact_service is not None,
        )

    @property
    def session_id(self) -> str:
        """Get the session ID."""
        return self._session_id

    async def put(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store a value in the blackboard.

        Args:
            key: The key to store under
            value: The value to store
            ttl_seconds: Optional TTL (not implemented yet)
        """
        async with self._lock:
            self._cache[key] = value

        logger.debug("BlackboardClient: put key=%s", key)

    async def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from the blackboard.

        Args:
            key: The key to retrieve
            default: Default value if key not found

        Returns:
            The stored value or default
        """
        async with self._lock:
            return self._cache.get(key, default)

    async def delete(self, key: str) -> bool:
        """Delete a value from the blackboard.

        Args:
            key: The key to delete

        Returns:
            True if the key was deleted, False if not found
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
        return False

    async def store_artifact(
        self,
        artifact_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a large artifact (code, document, etc.).

        Args:
            artifact_type: Type of artifact (code, document, plan, etc.)
            content: The artifact content
            metadata: Optional metadata

        Returns:
            The artifact ID

        Raises:
            RuntimeError: If no artifact service is configured
        """
        if self._artifact_service is None:
            raise RuntimeError(
                "BlackboardClient: no artifact service configured. "
                "Pass an ArtifactServicePort implementation to enable artifact storage."
            )
        artifact = self._artifact_service.write_artifact(
            session_id=self._session_id,
            artifact_type=artifact_type,
            content=content,
            metadata=metadata,
        )

        # Also cache a reference
        await self.put(f"artifact:{artifact.id}", artifact.to_dict())

        return artifact.id

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """Retrieve an artifact by ID.

        Args:
            artifact_id: The artifact ID

        Returns:
            The artifact dict or None if not found
        """
        # Check cache first
        cached = await self.get(f"artifact:{artifact_id}")
        if cached:
            return cached

        # Search in artifacts if service is available
        if self._artifact_service is None:
            return None

        artifacts = self._artifact_service.list_artifacts(
            session_id=self._session_id,
        )

        for artifact in artifacts:
            if artifact.id == artifact_id:
                return artifact.to_dict()

        return None

    async def list_artifacts(
        self,
        artifact_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all artifacts in this session.

        Args:
            artifact_type: Optional filter by type

        Returns:
            List of artifact dicts (empty list if no service configured)
        """
        if self._artifact_service is None:
            return []
        artifacts = self._artifact_service.list_artifacts(
            session_id=self._session_id,
            artifact_type=artifact_type,
        )
        return [a.to_dict() for a in artifacts]

    async def store_task_result(
        self,
        task_id: str,
        agent_id: str,
        result: dict[str, Any],
    ) -> None:
        """Store a task result from an agent.

        Args:
            task_id: The task ID
            agent_id: The agent that produced the result
            result: The result data
        """
        key = f"task_result:{task_id}:{agent_id}"
        await self.put(key, result)

        # Also add to task's result set
        set_key = f"task_results:{task_id}"
        async with self._lock:
            if set_key not in self._cache:
                self._cache[set_key] = []
            self._cache[set_key].append(
                {
                    "agent_id": agent_id,
                    "result": result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    async def get_task_results(self, task_id: str) -> list[dict[str, Any]]:
        """Get all results for a task.

        Args:
            task_id: The task ID

        Returns:
            List of result dicts from all agents
        """
        set_key = f"task_results:{task_id}"
        async with self._lock:
            return list(self._cache.get(set_key, []))

    def get_stats(self) -> dict[str, Any]:
        """Get blackboard statistics.

        Returns:
            Dictionary with storage stats
        """
        return {
            "session_id": self._session_id,
            "cache_size": len(self._cache),
            "keys": list(self._cache.keys()),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Orchestrator Agent
# ═══════════════════════════════════════════════════════════════════════════


class OrchestratorAgent(BaseAgent):
    """Orchestrator agent for coordinating multiple worker agents.

    The orchestrator:
    1. Receives tasks from clients or other agents
    2. Decomposes tasks and delegates to worker agents
    3. Collects results and optionally runs consensus via critics
    4. Returns aggregated results with confidence scores

    Usage:
        orchestrator = OrchestratorAgent(
            agent_id="orchestrator-1",
            bus_port=bus_port,
            workers=["worker-1", "worker-2"],
            critics=["critic-1"],
        )
        await orchestrator.start()

        result = await orchestrator.submit_task(
            task="Analyze repo structure",
            intent=Intent.SEARCH_CODE,
        )
    """

    def __init__(
        self,
        agent_id: str,
        bus_port: Any = None,  # AgentBusPort
        broker: InMemoryBroker | None = None,
        workers: list[str] | None = None,
        critics: list[str] | None = None,
        *,
        blackboard: BlackboardClient | None = None,
        quota_manager: ResourceQuotaManager | None = None,
        task_timeout: float = ORCHESTRATOR_DEFAULT_TASK_TIMEOUT_SECONDS,
        max_delegation_depth: int = ORCHESTRATOR_DEFAULT_MAX_DELEGATION_DEPTH,
        confidence_threshold: float = ORCHESTRATOR_DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        """Initialize the orchestrator agent.

        Args:
            agent_id: Unique agent identifier
            bus_port: AgentBusPort for message transport
            broker: Message broker (creates default if None)
            workers: List of worker agent IDs
            critics: List of critic agent IDs for consensus
            blackboard: Shared blackboard client
            quota_manager: Resource quota manager for 100+ agent support
            task_timeout: Maximum time for task execution
            max_delegation_depth: Maximum delegation depth
            confidence_threshold: Minimum confidence to accept result
        """
        self._workers = list(workers) if workers else []
        self._critics = list(critics) if critics else []
        self._blackboard = blackboard
        self._broker = broker or InMemoryBroker(bus_port=bus_port)
        self._task_timeout = max(1.0, float(task_timeout))
        self._max_delegation_depth = max(1, int(max_delegation_depth))
        self._confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))

        # Resource quota manager for 100+ concurrent agent support
        self._quota_manager = quota_manager or create_resource_quota_manager(
            max_concurrent_agents=100,
        )

        # Consensus engine
        self._consensus_engine: ConsensusEngine | None = None
        if self._critics:
            self._consensus_engine = ConsensusEngine(
                critic_agents=self._critics,
                broker=self._broker,
            )

        # Active tasks
        self._active_tasks: dict[str, dict[str, Any]] = {}
        self._task_results: dict[str, TaskResult] = {}
        self._lock = asyncio.Lock()

        # Reference to bus_port for super().__init__
        super().__init__(
            agent_id=agent_id,
            bus_port=bus_port,
        )

    @property
    def agent_type(self) -> str:
        return "orchestrator"

    @property
    def capabilities(self) -> list[AgentCapability]:
        """Orchestrator supports all common intents."""
        return [
            AgentCapability(
                name="orchestration",
                intents=[
                    Intent.EXECUTE_TASK,
                    Intent.COORDINATE,
                    Intent.COLLABORATE,
                    Intent.DELEGATE,
                    Intent.VOTE,
                    Intent.REACH_CONSENSUS,
                ],
                description="Orchestrator agent for multi-agent coordination",
                version="1.0.0",
            ),
        ]

    async def submit_task(
        self,
        task: str,
        intent: Intent,
        payload: dict[str, Any] | None = None,
        *,
        require_consensus: bool = False,
        correlation_id: str | None = None,
    ) -> TaskResult:
        """Submit a task for execution by worker agents.

        Args:
            task: Task description
            intent: Task intent type
            payload: Additional task payload
            require_consensus: Whether to require critic consensus
            correlation_id: Optional correlation ID for tracking

        Returns:
            TaskResult with execution results
        """
        task_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        correlation_id = correlation_id or task_id

        logger.info(
            "OrchestratorAgent: submitting task_id=%s task=%s intent=%s",
            task_id,
            task[:50],
            intent.value,
        )

        # Store task in active tasks
        async with self._lock:
            self._active_tasks[task_id] = {
                "task": task,
                "intent": intent,
                "payload": payload or {},
                "require_consensus": require_consensus,
                "correlation_id": correlation_id,
                "submitted_at": start_time.isoformat(),
                "worker_results": [],
                "state": TaskState.PENDING,
            }

        try:
            # Delegate to workers
            await self._delegate_to_workers(
                task_id=task_id,
                task=task,
                intent=intent,
                payload=payload,
            )

            # Wait for results or timeout
            result = await self._wait_for_results(
                task_id=task_id,
                timeout=self._task_timeout,
            )

            # Run consensus if required
            if require_consensus and self._consensus_engine:
                consensus_result = await self._run_consensus(
                    task_id=task_id,
                    topic=f"Task result assessment: {task[:50]}",
                )
                result = self._merge_with_consensus(result, consensus_result)

            return result

        except asyncio.TimeoutError:
            logger.warning("OrchestratorAgent: task_id=%s timed out", task_id)
            return TaskResult(
                task_id=task_id,
                state=TaskState.FAILED,
                errors=("Task timed out",),
                elapsed_seconds=self._task_timeout,
            )
        finally:
            async with self._lock:
                self._active_tasks.pop(task_id, None)

    async def _delegate_to_workers(
        self,
        task_id: str,
        task: str,
        intent: Intent,
        payload: dict[str, Any] | None,
    ) -> None:
        """Delegate a task to available workers.

        Args:
            task_id: The task ID
            task: Task description
            intent: Task intent
            payload: Additional payload
        """
        if not self._workers:
            logger.warning("OrchestratorAgent: no workers available for task_id=%s", task_id)
            return

        # Distribute to all workers (simple broadcast strategy)
        message = AgentMessage(
            sender=self.agent_id,
            receiver="",  # Broadcast
            performative=Performative.REQUEST,
            intent=intent,
            message_type=MessageType.TASK,
            payload={
                "task_id": task_id,
                "task": task,
                "payload": payload or {},
            },
            correlation_id=task_id,
            # TTL accounts for: delegation hops * (forward + reply) + broker overhead
            ttl=max(3, self._max_delegation_depth * 2 + 1),
        )

        # Publish to workers via broker
        await self._broker.broadcast(message)

        # Update task state
        async with self._lock:
            if task_id in self._active_tasks:
                self._active_tasks[task_id]["state"] = TaskState.IN_PROGRESS

    async def _wait_for_results(
        self,
        task_id: str,
        timeout: float,
    ) -> TaskResult:
        """Wait for worker results or timeout.

        Args:
            task_id: The task ID
            timeout: Maximum wait time

        Returns:
            TaskResult with collected results
        """
        start_time = datetime.now(timezone.utc)
        deadline = start_time.timestamp() + timeout

        while datetime.now(timezone.utc).timestamp() < deadline:
            async with self._lock:
                if task_id in self._task_results:
                    return self._task_results[task_id]

                task_info = self._active_tasks.get(task_id, {})
                worker_results = task_info.get("worker_results", [])

                # Check if we have enough results
                if len(worker_results) >= len(self._workers):
                    break

            await asyncio.sleep(0.1)

        # Collect final results
        async with self._lock:
            task_info = self._active_tasks.get(task_id, {})
            worker_results = task_info.get("worker_results", [])

            elapsed = datetime.now(timezone.utc).timestamp() - start_time.timestamp()

            if not worker_results:
                return TaskResult(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    errors=("No results received",),
                    elapsed_seconds=elapsed,
                )

            # Aggregate results (simple: take first with highest confidence)
            best_result = max(
                worker_results,
                key=lambda r: r.get("confidence", 0.5),
            )

            return TaskResult(
                task_id=task_id,
                state=TaskState.COMPLETED,
                primary_result=best_result.get("result"),
                confidence=best_result.get("confidence", 0.5),
                elapsed_seconds=elapsed,
            )

    async def _run_consensus(
        self,
        task_id: str,
        topic: str,
    ) -> ConsensusResult | None:
        """Run consensus among critic agents.

        Args:
            task_id: The task ID
            topic: Consensus topic

        Returns:
            ConsensusResult or None
        """
        if not self._consensus_engine:
            return None

        try:
            async with self._lock:
                task_info = self._active_tasks.get(task_id, {})
                worker_results = task_info.get("worker_results", [])

            options = ["approve", "reject", "needs_work"]
            metadata = {
                "task_id": task_id,
                "results": worker_results,
            }

            result = await asyncio.wait_for(
                self._consensus_engine.request_consensus(
                    topic=topic,
                    options=options,
                    metadata=metadata,
                    initial_proposer=self.agent_id,  # Ensure VOTE_RESPONSE goes back to orchestrator
                ),
                timeout=self._task_timeout / 2,
            )

            return result

        except asyncio.TimeoutError:
            logger.warning("OrchestratorAgent: consensus timed out for task_id=%s", task_id)
            return None
        except (RuntimeError, ValueError):
            logger.exception("OrchestratorAgent: consensus error for task_id=%s", task_id)
            return None

    def _merge_with_consensus(
        self,
        result: TaskResult,
        consensus_result: ConsensusResult | None,
    ) -> TaskResult:
        """Merge task result with consensus result.

        Args:
            result: The original task result
            consensus_result: The consensus result

        Returns:
            Merged TaskResult
        """
        if not consensus_result:
            return result

        final_state = TaskState.COMPLETED
        if not consensus_result.reached:
            final_state = TaskState.PARTIAL

        return TaskResult(
            task_id=result.task_id,
            state=final_state,
            primary_result=result.primary_result,
            confidence=consensus_result.confidence if consensus_result.reached else result.confidence * 0.5,
            votes=consensus_result.votes,
            elapsed_seconds=result.elapsed_seconds,
        )

    async def _handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Handle incoming messages.

        For RESULT messages, collect worker results.
        For VOTE_RESPONSE messages, delegate to consensus engine.
        """
        # Handle VOTE_RESPONSE - delegate to consensus engine
        if message.performative == Performative.VOTE_RESPONSE:
            if self._consensus_engine:
                await self._consensus_engine.handle_vote_response_message(message)
            return None

        # Handle RESULT messages - collect worker results
        if message.performative == Performative.INFORM:
            payload = message.payload

            # Check if this is a task result
            task_id = payload.get("task_id") or payload.get("correlation_id")
            if not task_id:
                return None

            # Store the result
            async with self._lock:
                if task_id in self._active_tasks:
                    self._active_tasks[task_id]["worker_results"].append(
                        {
                            "agent_id": message.sender,
                            "result": payload.get("result", payload),
                            "confidence": payload.get("confidence", 0.5),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )

                    # Check if all workers have responded
                    if len(self._active_tasks[task_id]["worker_results"]) >= len(self._workers):
                        self._active_tasks[task_id]["state"] = TaskState.COMPLETED

            return None

        return None

    async def spawn_agents(
        self,
        agent_count: int,
        agent_prefix: str = "agent",
        quota: dict[str, Any] | None = None,
    ) -> list[str]:
        """Spawn multiple agents and allocate quotas for them.

        This method supports batch spawning of 100+ concurrent agents
        with proper resource quota management.

        Args:
            agent_count: Number of agents to spawn
            agent_prefix: Prefix for agent IDs (e.g., "worker" -> "worker-0")
            quota: Resource quota per agent. Uses default if None.

        Returns:
            List of spawned agent IDs

        Raises:
            QuotaExceededError: If quota limits prevent spawning all agents
        """
        spawned_ids: list[str] = []

        async with self._lock:
            for _i in range(agent_count):
                agent_id = f"{agent_prefix}-{uuid.uuid4().hex[:8]}"

                # Allocate quota for the agent
                try:
                    await self._quota_manager.allocate(agent_id, quota)
                    spawned_ids.append(agent_id)

                    # Add to workers list
                    if agent_id not in self._workers:
                        self._workers.append(agent_id)

                except QuotaExceededError:
                    # Rollback already spawned agents
                    for spawned_id in spawned_ids:
                        await self._quota_manager.release(spawned_id)
                        if spawned_id in self._workers:
                            self._workers.remove(spawned_id)
                    raise

        logger.info(
            "OrchestratorAgent: spawned %d agents (prefix=%s)",
            len(spawned_ids),
            agent_prefix,
        )

        return spawned_ids

    async def release_agent(self, agent_id: str) -> bool:
        """Release an agent's quota and remove it from the worker pool.

        Args:
            agent_id: Agent ID to release

        Returns:
            True if release succeeded
        """
        try:
            await self._quota_manager.release(agent_id)
            async with self._lock:
                if agent_id in self._workers:
                    self._workers.remove(agent_id)
            logger.info("OrchestratorAgent: released agent=%s", agent_id)
            return True
        except (RuntimeError, ValueError):
            logger.exception("OrchestratorAgent: failed to release agent=%s", agent_id)
            return False

    def get_quota_stats(self) -> dict[str, Any]:
        """Get resource quota statistics.

        Returns:
            Dictionary with quota utilization metrics
        """
        return {
            "quota_utilization": asyncio.run(self._quota_manager.get_utilization()),
            "quota_stats": asyncio.run(self._quota_manager.get_stats()),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get orchestrator statistics.

        Returns:
            Dictionary with orchestrator stats
        """
        return {
            **super().get_stats(),
            "workers": self._workers,
            "critics": self._critics,
            "active_tasks": len(self._active_tasks),
            "completed_tasks": len(self._task_results),
            "blackboard": self._blackboard.get_stats() if self._blackboard else None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Convenience Factory
# ═══════════════════════════════════════════════════════════════════════════


def create_orchestrator(
    agent_id: str,
    bus_port: Any = None,
    workers: list[str] | None = None,
    critics: list[str] | None = None,
    workspace: str = "",
    quota_manager: ResourceQuotaManager | None = None,
) -> OrchestratorAgent:
    """Create a fully configured orchestrator agent.

    Args:
        agent_id: Unique agent identifier
        bus_port: AgentBusPort for message transport
        workers: List of worker agent IDs
        critics: List of critic agent IDs
        workspace: Workspace path for blackboard
        quota_manager: Resource quota manager (creates default if None)

    Returns:
        Configured OrchestratorAgent instance
    """
    # Create blackboard if workspace provided
    blackboard = None
    if workspace:
        blackboard = BlackboardClient(workspace=workspace)

    return OrchestratorAgent(
        agent_id=agent_id,
        bus_port=bus_port,
        workers=workers,
        critics=critics,
        blackboard=blackboard,
        quota_manager=quota_manager,
    )


__all__ = [
    "Artifact",
    "ArtifactServicePort",
    "BlackboardClient",
    "MessageForwarder",
    "OrchestratorAgent",
    "TaskResult",
    "TaskState",
    "create_orchestrator",
]
