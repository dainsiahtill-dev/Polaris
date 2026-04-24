"""Role Agent Base - Core architecture for autonomous role agents.

This module provides the foundation for making PM, Director, QA etc.
true autonomous agents with their own:
- Lifecycle management (start/run/stop)
- Persistent memory (task history, context)
- Toolbox (role-specific tools)
- Message queue (inter-agent communication via KernelOne Bus Port)
- Context compression (universal capability for all roles)

Message queue migration (2026-03-22):
  The old file-system inbox/inflight/dead_letter implementation
  (`MessageQueue`, using shutil.move / os.listdir) has been removed and
  replaced with `InMemoryAgentBusPort` from `bus_port`. The external
  interface (`RoleAgent.message_queue`) now returns an `AgentBusProxy`
  that preserves the same send/receive/ack/nack/peek/pending_count API
  so callers (standalone_runner, service.py) require no changes.

  Gap: Full KernelOne cross-process Bus integration (NATS topic routing)
  is tracked in cell.yaml verification.gaps.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.runtime.internal.bus_port import (
    AgentBusPort,
    AgentEnvelope,
    InMemoryAgentBusPort,
)
from polaris.kernelone.common.clock import ClockPort, RealClock
from polaris.kernelone.context.compaction import (
    CompactSnapshot,
    RoleContextCompressor,
    RoleContextIdentity,
)
from polaris.kernelone.fs.text_ops import open_text_log_append, write_text_atomic
from polaris.kernelone.storage import (
    StorageRoots,
    resolve_storage_roots,
)

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


def _safe_parse_agent_status(raw: Any) -> AgentStatus:
    """Parse persisted status defensively and degrade unknown values to idle."""
    token = str(raw or "").strip().lower()
    for candidate in AgentStatus:
        if candidate.value == token:
            return candidate
    return AgentStatus.IDLE


class MessageType(Enum):
    TASK = "task"
    RESULT = "result"
    EVENT = "event"
    COMMAND = "command"
    HEARTBEAT = "heartbeat"
    SHUTDOWN = "shutdown"


@dataclass
class AgentMessage:
    """Message passed between agents."""

    id: str
    type: MessageType
    sender: str
    receiver: str
    payload: dict[str, Any]
    timestamp: str
    correlation_id: str | None = None

    @classmethod
    def create(
        cls,
        msg_type: MessageType,
        sender: str,
        receiver: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> AgentMessage:
        return cls(
            id=str(uuid.uuid4()),
            type=msg_type,
            sender=sender,
            receiver=receiver,
            payload=payload,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id,
        )


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class AgentState:
    """Persistent state for an agent."""

    status: AgentStatus = AgentStatus.IDLE
    current_task_id: str | None = None
    consecutive_failures: int = 0
    total_tasks_processed: int = 0
    last_activity: str | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentBusProxy:
    """Proxy that bridges AgentMessage ↔ InMemoryAgentBusPort.

    Preserves the same public interface as the removed file-system
    `MessageQueue` so that all call-sites (standalone_runner, service.py,
    RoleAgent.message_queue) require no changes.

    Backed by `AgentBusPort` (default: InMemoryAgentBusPort).
    A different implementation can be injected via the `bus` parameter
    for testing or future KernelOne transport integration.
    """

    def __init__(
        self,
        agent_name: str,
        bus: AgentBusPort | None = None,
    ) -> None:
        self.agent_name = agent_name
        self._bus: AgentBusPort = bus if bus is not None else InMemoryAgentBusPort()

    # ------------------------------------------------------------------
    # send / receive (original MessageQueue public interface)
    # ------------------------------------------------------------------

    def send(self, message: AgentMessage) -> bool:
        """Publish message to receiver's inbox via bus port."""
        envelope = AgentEnvelope.from_fields(
            msg_type=message.type.value,
            sender=message.sender,
            receiver=message.receiver,
            payload=message.payload,
            message_id=message.id,
            correlation_id=message.correlation_id,
        )
        return self._bus.publish(envelope)

    def receive(
        self,
        block: bool = False,
        timeout: float = 1.0,
        auto_ack: bool = True,
    ) -> AgentMessage | None:
        """Poll next message for this agent from bus port."""
        env = self._bus.poll(self.agent_name, block=block, timeout=timeout)
        if env is None:
            return None

        try:
            msg_type = MessageType(env.msg_type)
        except ValueError:
            # Unknown type — nack and skip; this is observable via logs
            self._bus.nack(
                env.message_id,
                self.agent_name,
                reason=f"unknown_msg_type:{env.msg_type}",
                requeue=False,
            )
            return None

        message = AgentMessage(
            id=env.message_id,
            type=msg_type,
            sender=env.sender,
            receiver=env.receiver,
            payload=env.payload,
            timestamp=env.timestamp_utc,
            correlation_id=env.correlation_id,
        )

        if auto_ack:
            self.ack(message.id)

        return message

    def ack(self, message_id: str, *, receipt_path: str | None = None) -> bool:
        """Acknowledge message — removed from inflight.

        `receipt_path` is accepted for API compatibility but ignored;
        the bus port tracks inflight by message_id.
        """
        return self._bus.ack(str(message_id or ""), self.agent_name)

    def nack(
        self,
        message_id: str,
        *,
        reason: str = "",
        receipt_path: str | None = None,
        requeue: bool = True,
    ) -> bool:
        """Negative-acknowledge — requeue or dead-letter via bus port."""
        return self._bus.nack(
            str(message_id or ""),
            self.agent_name,
            reason=reason,
            requeue=requeue,
        )

    def peek(self) -> list[AgentMessage]:
        """Peek at inbox — returns a shallow copy without consuming messages.

        This method implements an atomic drain-and-requeue pattern:
        1. Drain all messages from inbox to inflight (via poll)
        2. Attempt to re-publish all messages
        3. If ALL succeed: return the message list
        4. If ANY fails: rollback by requeuing ALL inflight messages

        This ensures no messages are lost if publish fails.
        """
        result: list[AgentMessage] = []

        # Phase 1: Drain all messages from inbox to inflight (consume)
        while True:
            env = self._bus.poll(self.agent_name)
            if env is None:
                break
            try:
                msg_type = MessageType(env.msg_type)
            except ValueError:
                # Skip malformed messages (nack them to dead-letter)
                self._bus.nack(
                    env.message_id,
                    self.agent_name,
                    reason=f"unknown_msg_type:{env.msg_type}",
                    requeue=False,
                )
                continue
            result.append(
                AgentMessage(
                    id=env.message_id,
                    type=msg_type,
                    sender=env.sender,
                    receiver=env.receiver,
                    payload=env.payload,
                    timestamp=env.timestamp_utc,
                    correlation_id=env.correlation_id,
                )
            )

        # Phase 2: Attempt to re-publish all messages (atomic)
        failed = False
        for msg in result:
            env = AgentEnvelope.from_fields(
                msg_type=msg.type.value,
                sender=msg.sender,
                receiver=msg.receiver,
                payload=msg.payload,
                message_id=msg.id,
                correlation_id=msg.correlation_id,
            )
            if not self._bus.publish(env):
                # Publish failed — mark for rollback
                failed = True
                logger.warning(
                    "agent_bus_proxy.peek: publish failed for message_id=%s, rolling back all inflight messages",
                    msg.id,
                )
                break

        # Phase 3: If any publish failed, rollback ALL inflight messages
        if failed:
            requeued_count = self._bus.requeue_all_inflight(self.agent_name)
            logger.info(
                "agent_bus_proxy.peek: rolled back %d messages to inbox",
                requeued_count,
            )
            # Return empty list since peek failed
            return []

        return result

    def pending_count(self) -> int:
        """Return count of pending messages in inbox."""
        return self._bus.pending_count(self.agent_name)


class AgentMemory:
    """Persistent memory for an agent.

    Stores:
    - Task history (what tasks were processed)
    - Context snapshots
    - Decision logs
    - Reflections/insights
    """

    def __init__(self, workspace: str, agent_name: str) -> None:
        self.workspace = workspace
        self.agent_name = agent_name
        # Use runtime path for agent memory (not in target workspace)
        roots = resolve_storage_roots(workspace)
        self._memory_dir = os.path.join(roots.runtime_root, "memory", agent_name)
        self._history_file = os.path.join(self._memory_dir, "history.jsonl")
        self._state_file = os.path.join(self._memory_dir, "state.json")
        self._snapshot_file = os.path.join(self._memory_dir, "snapshot.json")
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        os.makedirs(self._memory_dir, exist_ok=True)

    def append_history(self, entry: dict[str, Any]) -> None:
        """Append an entry to task history."""
        entry["timestamp"] = datetime.now().isoformat()
        entry["agent"] = self.agent_name

        handle = open_text_log_append(self._history_file)
        try:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        finally:
            handle.close()

    def get_history(
        self,
        limit: int = 100,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get task history, optionally filtered by task_id."""
        if not os.path.exists(self._history_file):
            return []

        entries = []
        try:
            with open(self._history_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if task_id is None or entry.get("task_id") == task_id:
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue

            return entries[-limit:]
        except (RuntimeError, ValueError) as exc:
            logger.warning("[%s] Failed to read task history, returning empty: %s", self.agent_name, exc)
            return []

    def get_task_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get summaries of all processed tasks."""
        history = self.get_history(limit=limit * 2)

        summaries = []
        seen_tasks: set[str] = set()

        for entry in history:
            task_id = entry.get("task_id")
            if task_id and task_id not in seen_tasks:
                seen_tasks.add(task_id)
                summaries.append(
                    {
                        "task_id": task_id,
                        "title": entry.get("title", "Unknown"),
                        "status": entry.get("status", "unknown"),
                        "result": entry.get("result", {}),
                        "timestamp": entry.get("timestamp"),
                        "error": entry.get("error"),
                    }
                )

                if len(summaries) >= limit:
                    break

        return summaries

    def save_state(self, state: AgentState) -> None:
        """Save agent state."""
        data = {
            "status": state.status.value,
            "current_task_id": state.current_task_id,
            "consecutive_failures": state.consecutive_failures,
            "total_tasks_processed": state.total_tasks_processed,
            "last_activity": state.last_activity,
            "last_error": state.last_error,
            "metadata": state.metadata,
            "updated_at": datetime.now().isoformat(),
        }

        write_text_atomic(self._state_file, json.dumps(data, ensure_ascii=False, indent=2))

    def load_state(self) -> AgentState | None:
        """Load agent state from disk."""
        if not os.path.exists(self._state_file):
            return None

        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)

            return AgentState(
                status=_safe_parse_agent_status(data.get("status", "idle")),
                current_task_id=data.get("current_task_id"),
                consecutive_failures=data.get("consecutive_failures", 0),
                total_tasks_processed=data.get("total_tasks_processed", 0),
                last_activity=data.get("last_activity"),
                last_error=data.get("last_error"),
                metadata=data.get("metadata", {}),
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("[%s] Failed to load agent state from disk, returning None: %s", self.agent_name, exc)
            return None

    def load_snapshot(self) -> dict[str, Any]:
        """Load memory snapshot."""
        if not os.path.exists(self._snapshot_file):
            return {}

        try:
            with open(self._snapshot_file, encoding="utf-8") as f:
                return json.load(f)
        except (RuntimeError, ValueError) as exc:
            logger.warning("[%s] Failed to load memory snapshot, returning empty: %s", self.agent_name, exc)
            return {}

    def save_snapshot(self, data: dict[str, Any]) -> None:
        """Save memory snapshot (complement of load_snapshot)."""
        try:
            write_text_atomic(
                self._snapshot_file,
                json.dumps(data, ensure_ascii=False, indent=2),
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("[%s] Failed to save memory snapshot: %s", self.agent_name, exc)


@dataclass
class ToolSpec:
    """Runtime validation schema for tool registration and invocation."""

    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: set[str] = field(default_factory=set)
    allow_additional: bool = False
    permission_level: str = "read"
    risk_level: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "required": sorted(self.required),
            "allow_additional": bool(self.allow_additional),
            "permission_level": self.permission_level,
            "risk_level": self.risk_level,
        }


class Toolbox:
    """Role-specific tool registry with validation-enforced call contracts."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._tools: dict[str, Callable] = {}
        self._tool_metadata: dict[str, dict[str, Any]] = {}
        self._tool_specs: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        *,
        spec: ToolSpec | None = None,
    ) -> None:
        """Register a tool with validation schema."""
        tool_name = str(name or "").strip().lower()
        if not tool_name:
            raise ValueError("Tool name is required")
        if not callable(func):
            raise ValueError(f"Tool '{tool_name}' is not callable")

        resolved_spec = spec or self._build_spec_from_registration(func, parameters or {})
        self._tools[tool_name] = func
        self._tool_specs[tool_name] = resolved_spec
        self._tool_metadata[tool_name] = {
            "description": description,
            "parameters": parameters or {},
            "spec": resolved_spec.to_dict(),
        }

    def call(self, name: str, **kwargs) -> Any:
        """Call a registered tool after schema validation."""
        tool_name = str(name or "").strip().lower()
        if tool_name not in self._tools:
            raise ValueError(f"Tool '{name}' not found in {self.agent_name} toolbox")

        spec = self._tool_specs.get(tool_name)
        normalized_kwargs = self._validate_tool_call(tool_name, kwargs, spec)
        return self._tools[tool_name](**normalized_kwargs)

    def list_tools(self) -> dict[str, dict[str, Any]]:
        """List all registered tools."""
        return dict(self._tool_metadata)

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return str(name or "").strip().lower() in self._tools

    def _build_spec_from_registration(
        self,
        func: Callable,
        parameters: dict[str, Any],
    ) -> ToolSpec:
        schema: dict[str, dict[str, Any]] = {}
        required: set[str] = set()
        allow_additional = False
        try:
            signature = inspect.signature(func)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to inspect signature for tool registration: %s", exc)
            signature = None

        if signature is not None:
            for param_name, param in signature.parameters.items():
                if param_name == "self":
                    continue
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    allow_additional = True
                    continue
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    continue
                inferred: dict[str, Any] = {
                    "type": self._annotation_to_type(param.annotation),
                }
                if param.default is inspect.Parameter.empty:
                    inferred["required"] = True
                    required.add(param_name)
                else:
                    inferred["default"] = param.default
                schema[param_name] = inferred

        for param_name, config in (parameters or {}).items():
            name = str(param_name or "").strip()
            if not name:
                continue
            field_cfg = schema.get(name, {})
            if isinstance(config, dict):
                normalized_cfg = dict(config)
                declared_type = normalized_cfg.get("type")
                if declared_type is not None:
                    field_cfg["type"] = str(declared_type).strip().lower() or field_cfg.get("type", "any")
                if "default" in normalized_cfg:
                    field_cfg["default"] = normalized_cfg.get("default")
                if bool(normalized_cfg.get("required")):
                    field_cfg["required"] = True
                    required.add(name)
                if "min" in normalized_cfg:
                    field_cfg["min"] = normalized_cfg.get("min")
                if "max" in normalized_cfg:
                    field_cfg["max"] = normalized_cfg.get("max")
                if "max_length" in normalized_cfg:
                    field_cfg["max_length"] = normalized_cfg.get("max_length")
                if "enum" in normalized_cfg and isinstance(normalized_cfg.get("enum"), list):
                    field_cfg["enum"] = [str(item) for item in normalized_cfg.get("enum", [])]
            else:
                field_cfg.setdefault("type", "any")
                field_cfg.setdefault("description", str(config or ""))
            schema[name] = field_cfg

        return ToolSpec(
            schema=schema,
            required=required,
            allow_additional=allow_additional,
            permission_level="execute" if "command" in schema else "read",
            risk_level="high" if "command" in schema else "low",
        )

    def _validate_tool_call(
        self,
        name: str,
        kwargs: dict[str, Any],
        spec: ToolSpec | None,
    ) -> dict[str, Any]:
        if spec is None:
            return dict(kwargs or {})

        payload = dict(kwargs or {})
        schema_keys = set(spec.schema.keys())
        unknown = [key for key in payload if key not in schema_keys]
        if unknown and not spec.allow_additional:
            raise ValueError(f"Tool '{name}' received unknown arguments: {', '.join(sorted(unknown))}")

        normalized: dict[str, Any] = {}
        for key, field_cfg in spec.schema.items():
            required = bool(field_cfg.get("required")) or key in spec.required
            if key not in payload:
                if "default" in field_cfg:
                    normalized[key] = field_cfg.get("default")
                    continue
                if required:
                    raise ValueError(f"Tool '{name}' missing required argument '{key}'")
                continue
            value = payload.get(key)
            expected = str(field_cfg.get("type") or "any").strip().lower()
            if expected == "integer" and isinstance(value, str):
                stripped = value.strip()
                if stripped and stripped.lstrip("-").isdigit():
                    value = int(stripped)
            elif expected == "number" and isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    with contextlib.suppress(ValueError):
                        value = float(stripped)
            elif expected == "boolean" and isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on"}:
                    value = True
                elif lowered in {"false", "0", "no", "off"}:
                    value = False
            self._validate_field_type(name, key, value, field_cfg)
            if "enum" in field_cfg:
                enum_values = [str(item) for item in field_cfg.get("enum") or []]
                if enum_values and str(value) not in enum_values:
                    raise ValueError(f"Tool '{name}' argument '{key}' must be one of: {', '.join(enum_values)}")
            if isinstance(value, str) and "max_length" in field_cfg:
                max_length = int(field_cfg.get("max_length") or 0)
                if max_length > 0 and len(value) > max_length:
                    raise ValueError(f"Tool '{name}' argument '{key}' exceeds max_length={max_length}")
            if isinstance(value, (int, float)):
                min_val = field_cfg.get("min")
                if min_val is not None and value < float(min_val):
                    raise ValueError(f"Tool '{name}' argument '{key}' must be >= {min_val}")
                max_val = field_cfg.get("max")
                if max_val is not None and value > float(max_val):
                    raise ValueError(f"Tool '{name}' argument '{key}' must be <= {max_val}")
            if key == "timeout":
                timeout_value = int(value) if isinstance(value, (int, float)) else 0
                if timeout_value < 1 or timeout_value > 600:
                    raise ValueError("Tool timeout must be in range [1, 600]")
            normalized[key] = value

        if spec.allow_additional:
            for key, value in payload.items():
                if key not in normalized:
                    normalized[key] = value

        return normalized

    @staticmethod
    def _annotation_to_type(annotation: Any) -> str:
        if annotation is inspect.Parameter.empty:
            return "any"
        if annotation in {str}:
            return "string"
        if annotation in {int}:
            return "integer"
        if annotation in {float}:
            return "number"
        if annotation in {bool}:
            return "boolean"
        origin = getattr(annotation, "__origin__", None)
        if origin in {list, list}:
            return "array"
        if origin in {dict, dict}:
            return "object"
        return "any"

    @staticmethod
    def _validate_field_type(
        tool_name: str,
        key: str,
        value: Any,
        field_cfg: dict[str, Any],
    ) -> None:
        expected = str(field_cfg.get("type") or "any").strip().lower()
        if expected in {"any", ""}:
            return
        if expected == "string" and not isinstance(value, str):
            raise ValueError(f"Tool '{tool_name}' argument '{key}' must be string")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"Tool '{tool_name}' argument '{key}' must be integer")
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"Tool '{tool_name}' argument '{key}' must be number")
        if expected == "boolean" and not isinstance(value, bool):
            raise ValueError(f"Tool '{tool_name}' argument '{key}' must be boolean")
        if expected == "object" and not isinstance(value, dict):
            raise ValueError(f"Tool '{tool_name}' argument '{key}' must be object")
        if expected == "array" and not isinstance(value, list):
            raise ValueError(f"Tool '{tool_name}' argument '{key}' must be array")


class RoleAgent(ABC):
    """Abstract base class for Role Agents.

    Subclasses must implement:
    - setup_toolbox(): Register role-specific tools
    - handle_message(): Process incoming messages
    - run_cycle(): Main processing loop

    Lifecycle:
    1. __init__ - Initialize (no I/O)
    2. initialize() - Load state, setup toolbox
    3. start() - Begin processing loop
    4. run() - Main loop (calls run_cycle)
    5. stop() - Graceful shutdown
    """

    def __init__(
        self,
        workspace: str,
        agent_name: str,
        enable_context_compression: bool = True,
        context_compressor_config: dict[str, Any] | None = None,
        clock: ClockPort | None = None,
    ) -> None:
        self.workspace = workspace
        self.agent_name = agent_name
        self.status = AgentStatus.IDLE

        self._storage: StorageRoots | None = None
        self._memory: AgentMemory | None = None
        self._toolbox: Toolbox | None = None
        self._backend_tool_runtime = None
        self._message_queue: AgentBusProxy | None = None
        self._state: AgentState | None = None

        # Injected time provider (defaults to real wall-clock in production).
        self._clock: ClockPort = clock if clock is not None else RealClock()

        # Context compression - universal capability for all roles
        self._enable_context_compression = enable_context_compression
        self._context_compressor: RoleContextCompressor | None = None
        self._context_compressor_config = context_compressor_config or {}

        self._running = False
        self._paused = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

        self._callbacks: dict[str, list[Callable]] = {
            "on_status_change": [],
            "on_message": [],
            "on_error": [],
        }

    @property
    def storage(self) -> StorageRoots:
        """Get storage roots (lazy init)."""
        if self._storage is not None:
            return self._storage
        with self._lock:
            if self._storage is None:
                self._storage = resolve_storage_roots(self.workspace)
            return self._storage

    @property
    def memory(self) -> AgentMemory:
        """Get agent memory (lazy init)."""
        if self._memory is not None:
            return self._memory
        with self._lock:
            if self._memory is None:
                self._memory = AgentMemory(self.workspace, self.agent_name)
            return self._memory

    @property
    def toolbox(self) -> Toolbox:
        """Get agent toolbox (lazy init).

        Uses a temp variable to ensure atomic assignment: self._toolbox is only
        set after both setup_toolbox() and _register_backend_tools() succeed,
        preventing partial-initialisation state from leaking to other threads.
        """
        if self._toolbox is not None:
            return self._toolbox
        with self._lock:
            if self._toolbox is None:
                tmp = Toolbox(self.agent_name)
                self.setup_toolbox_into(tmp)
                self._register_backend_tools(tmp)
                self._toolbox = tmp  # only assign when fully initialised
            return self._toolbox

    @property
    def message_queue(self) -> AgentBusProxy:
        """Get message bus proxy (lazy init)."""
        if self._message_queue is not None:
            return self._message_queue
        with self._lock:
            if self._message_queue is None:
                self._message_queue = AgentBusProxy(self.agent_name)
            return self._message_queue

    @property
    def context_compressor(self) -> RoleContextCompressor | None:
        """Get context compressor (lazy init).

        Universal context compression capability available to all roles.
        Returns None if context compression is disabled.
        """
        if not self._enable_context_compression:
            return None
        if self._context_compressor is not None:
            return self._context_compressor
        with self._lock:
            if self._context_compressor is None:
                self._context_compressor = RoleContextCompressor(
                    workspace=self.workspace, role_name=self.agent_name, **self._context_compressor_config
                )
            return self._context_compressor

    def compact_context(
        self,
        messages: list[dict[str, Any]],
        identity: RoleContextIdentity | None = None,
        force: bool = False,
        focus: str = "",
    ) -> tuple[list[dict[str, Any]], CompactSnapshot | None]:
        """Compact conversation context using the role's compressor.

        Universal method available to all roles for context compression.

        Args:
            messages: List of conversation messages
            identity: Role context identity (auto-created if None)
            force: Force compression regardless of token threshold
            focus: Optional focus area for compression

        Returns:
            Tuple of (compressed_messages, snapshot)
        """
        compressor = self.context_compressor
        if compressor is None:
            # Context compression disabled, return unchanged
            return messages, None

        # Auto-create identity if not provided
        if identity is None:
            current_task = self._state.current_task_id if self._state else None
            identity = RoleContextIdentity.from_role_state(
                role_name=self.agent_name,
                goal=current_task or "ongoing_operations",
                scope=[],
                current_task_id=current_task,
                metadata={"auto_created": True},
            )

        return compressor.compact_if_needed(messages, identity, force_compact=force, focus=focus)

    def create_context_identity(
        self,
        goal: str,
        scope: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        current_phase: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> RoleContextIdentity:
        """Create a role context identity for context compression.

        Helper method for roles to easily create identities.

        Args:
            goal: Current goal/task objective
            scope: Working scope (files, paths, domains)
            acceptance_criteria: List of acceptance criteria
            current_phase: Current execution phase
            metadata: Additional role-specific metadata

        Returns:
            RoleContextIdentity instance
        """
        current_task = self._state.current_task_id if self._state else None
        return RoleContextIdentity(
            role_id=current_task or f"{self.agent_name}_{int(time.time())}",
            role_type=self.agent_name,
            goal=goal,
            acceptance_criteria=acceptance_criteria or [],
            scope=scope or [],
            current_phase=current_phase,
            metadata=metadata or {},
        )

    def register_callback(self, event: str, callback: Callable) -> None:
        """Register a callback for events."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _get_backend_tool_runtime(self):
        """Get shared backend tool runtime (lazy init)."""
        if self._backend_tool_runtime is not None:
            return self._backend_tool_runtime
        with self._lock:
            if self._backend_tool_runtime is not None:
                return self._backend_tool_runtime
            try:
                from polaris.kernelone.tool_execution.runtime_executor import BackendToolRuntime
            except (RuntimeError, ValueError) as exc:
                logger.debug("[%s] BackendToolRuntime not available (import failed): %s", self.agent_name, exc)
                return None
            try:
                self._backend_tool_runtime = BackendToolRuntime(self.workspace)
            except (RuntimeError, ValueError) as exc:
                logger.warning("[%s] Failed to initialize BackendToolRuntime: %s", self.agent_name, exc)
                return None
            return self._backend_tool_runtime

    def _register_backend_tools(self, tb: Toolbox) -> None:
        """Register shared backend tools for all role agents."""
        runtime = self._get_backend_tool_runtime()
        if runtime is None:
            return
        try:
            handlers = runtime.list_tools()
        except (RuntimeError, ValueError) as exc:
            logger.warning("[%s] Failed to list backend tools: %s", self.agent_name, exc)
            return
        if not handlers:
            return

        for tool_name in sorted(handlers.keys()):
            if tb.has_tool(tool_name):
                continue

            def _tool_wrapper(_tool: str = tool_name, **kwargs: Any) -> Any:
                return runtime.invoke(_tool, kwargs)

            tb.register(
                tool_name,
                _tool_wrapper,
                description=f"Backend tool '{tool_name}'",
                parameters={
                    "args": "Optional raw argument list",
                    "cwd": "Optional workspace-relative working directory",
                    "timeout": "Optional timeout in seconds",
                },
            )

    def _emit(self, event: str, *args, **kwargs) -> None:
        """Emit an event to callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except (RuntimeError, ValueError) as e:
                logger.error("[%s] Callback error: %s", self.agent_name, e)

    @abstractmethod
    def setup_toolbox(self) -> None:
        """Register role-specific tools. Override in subclass.

        Access the toolbox via ``self._toolbox`` if needed, but prefer
        calling ``self._toolbox.register(...)`` directly.  Do NOT call
        ``self.toolbox`` (the property) — it is not yet assigned at this
        point in the initialisation sequence.
        """
        pass

    def setup_toolbox_into(self, toolbox: Toolbox) -> None:
        """Internal dispatch called by the toolbox property initialiser.

        Sets ``self._toolbox`` to the temp instance so that ``setup_toolbox``
        implementations that call ``self._toolbox.register(...)`` still work,
        then delegates to ``setup_toolbox()``.
        """
        self._toolbox = toolbox
        self.setup_toolbox()

    @abstractmethod
    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Handle incoming message. Override in subclass.

        Returns optional response message.
        """
        pass

    @abstractmethod
    def run_cycle(self) -> bool:
        """Main processing cycle. Override in subclass.

        Returns True if work was done, False if idle.
        """
        pass

    def initialize(self) -> None:
        """Initialize agent: load state, setup toolbox."""
        with self._lock:
            self._set_status(AgentStatus.STARTING)

            saved_state = self.memory.load_state()
            if saved_state:
                self._state = saved_state
            else:
                self._state = AgentState()

            self._set_status(AgentStatus.IDLE)

            snapshot = self.memory.load_snapshot()
            if snapshot:
                self._load_snapshot(snapshot)

            self._emit("on_status_change", self.status)

    def _load_snapshot(self, snapshot: dict[str, Any]) -> None:  # noqa: B027
        """Load data from snapshot. Override in subclass."""
        pass

    def _set_status(self, status: AgentStatus) -> None:
        """Set agent status."""
        self.status = status
        if self._state:
            self._state.status = status
            self._state.last_activity = datetime.now().isoformat()
            self.memory.save_state(self._state)
        self._emit("on_status_change", status)

    def start(self) -> None:
        """Start agent processing loop."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._stop_event.clear()
            self._set_status(AgentStatus.RUNNING)

            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    # Exponential backoff configuration for error recovery
    # P1-003: Prevent fast failure loops with configurable backoff
    MAX_CONSECUTIVE_ERRORS = 10  # Threshold to trigger graceful stop
    INITIAL_BACKOFF_SECONDS = 1.0  # Starting backoff delay
    MAX_BACKOFF_SECONDS = 60.0  # Cap backoff at 60 seconds
    BACKOFF_MULTIPLIER = 2.0  # Exponential factor

    def _run_loop(self) -> None:
        """Main run loop with exponential backoff on consecutive errors.

        Error recovery strategy:
        - Consecutive errors trigger exponential backoff: 1s -> 2s -> 4s ... -> 60s (capped)
        - After MAX_CONSECUTIVE_ERRORS (10) consecutive failures, agent stops gracefully
        - Successful cycles reset the backoff to INITIAL_BACKOFF_SECONDS

        This prevents fast failure loops (CPU spinning) while allowing recovery
        from transient errors without immediate shutdown.
        """
        heartbeat_interval = 5.0
        last_heartbeat = self._clock.time()
        consecutive_errors = 0  # Track errors for exponential backoff
        current_backoff = self.INITIAL_BACKOFF_SECONDS

        while self._running:
            if self._paused:
                self._clock.sleep(0.5)
                continue

            try:
                work_done = self.run_cycle()

                if self._state:
                    # Reset consecutive failure counter on any successful cycle.
                    # This preserves the "consecutive" semantics: the counter only
                    # matters while failures are uninterrupted.
                    self._state.consecutive_failures = 0

                    if work_done:
                        # Track completed task count and record activity timestamp.
                        self._state.total_tasks_processed += 1
                        self._state.last_activity = datetime.now().isoformat()
                        self.memory.save_state(self._state)

                # Reset backoff on successful cycle
                consecutive_errors = 0
                current_backoff = self.INITIAL_BACKOFF_SECONDS

                if not work_done:
                    self._clock.sleep(0.1)

                now = self._clock.time()
                if now - last_heartbeat >= heartbeat_interval:
                    self._heartbeat()
                    last_heartbeat = now

            except (RuntimeError, ValueError) as e:
                logger.error("[%s] Cycle error: %s", self.agent_name, e)
                self._set_status(AgentStatus.ERROR)
                if self._state:
                    self._state.last_error = str(e)
                    self._state.consecutive_failures += 1

                self._emit("on_error", e)

                # Increment error counter and apply exponential backoff
                consecutive_errors += 1

                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    # Threshold exceeded: graceful stop
                    logger.error(
                        "[%s] Max consecutive errors (%d) reached. Stopping agent gracefully.",
                        self.agent_name,
                        self.MAX_CONSECUTIVE_ERRORS,
                    )
                    self._running = False
                    break

                # Calculate exponential backoff: 1, 2, 4, 8, 16, 32, 60(cap)
                sleep_duration = min(
                    current_backoff,
                    self.MAX_BACKOFF_SECONDS,
                )
                logger.warning(
                    "[%s] Error %d/%d, backing off %.1f seconds before retry.",
                    self.agent_name,
                    consecutive_errors,
                    self.MAX_CONSECUTIVE_ERRORS,
                    sleep_duration,
                )
                self._clock.sleep(sleep_duration)

                # Exponential increase for next backoff
                current_backoff = min(
                    current_backoff * self.BACKOFF_MULTIPLIER,
                    self.MAX_BACKOFF_SECONDS,
                )

            if self._stop_event.is_set():
                break

        self._set_status(AgentStatus.STOPPED)

    def _heartbeat(self) -> None:
        """Send heartbeat message."""
        if self._state:
            self._state.last_activity = datetime.now().isoformat()
            self.memory.save_state(self._state)

    def pause(self) -> None:
        """Pause agent processing."""
        self._paused = True
        self._set_status(AgentStatus.PAUSED)

    def resume(self) -> None:
        """Resume agent processing."""
        self._paused = False
        self._set_status(AgentStatus.RUNNING)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop agent gracefully."""
        with self._lock:
            if not self._running:
                return

            self._set_status(AgentStatus.STOPPING)
            self._running = False
            self._stop_event.set()

            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=timeout)

            self._set_status(AgentStatus.STOPPED)

            if self._state:
                self.memory.save_state(self._state)

    def get_state(self) -> AgentState | None:
        """Get current agent state."""
        return self._state

    def get_status(self) -> dict[str, Any]:
        """Get agent status summary."""
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "state": {
                "current_task_id": self._state.current_task_id if self._state else None,
                "consecutive_failures": self._state.consecutive_failures if self._state else 0,
                "total_tasks_processed": self._state.total_tasks_processed if self._state else 0,
                "last_activity": self._state.last_activity if self._state else None,
                "last_error": self._state.last_error if self._state else None,
            },
            "pending_messages": self.message_queue.pending_count() if self._message_queue else 0,
            "tools": list(self.toolbox.list_tools().keys()) if self._toolbox else [],
        }


def create_agent_service(
    agent_class: type,
    workspace: str,
    agent_name: str,
) -> RoleAgent:
    """Factory function to create and initialize an agent service."""
    agent = agent_class(workspace, agent_name)
    agent.initialize()
    return agent
