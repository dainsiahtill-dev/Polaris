# P0-005: Event Bus Architecture Convergence Plan

**Status**: Implemented (Documentation + Constants)
**Date**: 2026-04-05
**Owner**: KernelOne Architecture Team

## Executive Summary

This document addresses P0-005 (Event Bus Architecture Convergence) and P0-007
(KernelOne→Cells Cross-layer Imports). The decision is to **document and maintain
separation** rather than merge the 7 event bus implementations.

## 1. The 7 Event Bus Implementations

The codebase contains 7 intentionally-separate event bus implementations:

| # | Implementation | Location | Category | Purpose |
|---|---------------|----------|----------|---------|
| 1 | MessageBus | `kernelone/events/message_bus.py` | async | Actor model pub/sub |
| 2 | EventRegistry | `kernelone/events/typed/registry.py` | async | Typed event system |
| 3 | TypedEventBusAdapter | `kernelone/events/typed/bus_adapter.py` | bridge | MessageBus ↔ EventRegistry |
| 4 | InMemoryAgentBusPort | `cells/roles/runtime/internal/bus_port.py` | sync | Thread-safe in-memory queue |
| 5 | KernelOneMessageBusPort | `cells/roles/runtime/internal/kernel_one_bus_port.py` | sync | NATS-backed distributed |
| 6 | InMemoryBroker / NATSBroker | `kernelone/agent_runtime/neural_syndicate/` | broker | ACL-layer Neural Syndicate |
| 7 | UEPEventPublisher | `kernelone/events/uep_publisher.py` | external | UEP v2.0 ingestion |

### 1.1 Why Not Unified?

These implementations serve **different architectural layers**:

- **Async implementations** (1-2): Workflow/Turn engine communication, event sourcing
- **Sync implementations** (4-5): Roles runtime, thread-safe agent messaging
- **Bridge implementations** (3): Migration path between async and typed systems
- **Broker implementations** (6): ACL-layer messaging for Neural Syndicate
- **External implementations** (7): External event ingestion

Unifying them would violate **Single Responsibility Principle** and create
a God Object anti-pattern.

### 1.2 Shared Protocol: IAgentBusPort

All sync implementations conform to the `IAgentBusPort` Protocol:

```python
# polaris/kernelone/agent_runtime/bus_port.py
@runtime_checkable
class IAgentBusPort(Protocol):
    def publish(self, envelope: AgentEnvelope) -> bool: ...
    def poll(self, receiver: str, *, block: bool = False, timeout: float = 1.0) -> AgentEnvelope | None: ...
    async def poll_async(self, receiver: str, *, block: bool = False, timeout: float = 1.0) -> AgentEnvelope | None: ...
    def ack(self, message_id: str, receiver: str) -> bool: ...
    def nack(self, message_id: str, receiver: str, *, reason: str = "", requeue: bool = True) -> bool: ...
    def pending_count(self, receiver: str) -> int: ...
    def requeue_all_inflight(self, receiver: str) -> int: ...
    @property
    def dead_letters(self) -> list[DeadLetterRecord]: ...
```

## 2. P0-007: KernelOne→Cells Cross-layer Imports

### 2.1 Issue Analysis

The Neural Syndicate components (`polaris/kernelone/agent_runtime/neural_syndicate/`)
import Cells implementations at module level:

| File | Line | Import | Status |
|------|------|--------|--------|
| `nats_broker.py` | 101 | `KernelOneMessageBusPort` | Lazy (in `__init__`) |
| `broker.py` | 171 | `InMemoryAgentBusPort` | Lazy (in `__init__`) |
| `base_agent.py` | 157 | `InMemoryAgentBusPort` | Lazy (in `__init__`) |

### 2.2 Current Mitigation

All imports are **lazy** (inside `__init__`):

```python
# base_agent.py (line 155-158)
if bus_port is None:
    # Lazy import to maintain KernelOne → Cells fence
    from polaris.cells.roles.runtime.internal.bus_port import InMemoryAgentBusPort
    bus_port = InMemoryAgentBusPort()
```

### 2.3 Verification Results

```
NATSBroker import OK
InMemoryBroker import OK
BaseAgent import OK
```

**No circular dependency detected.**

### 2.4 Architecture Decision

The lazy import pattern is **acceptable** because:

1. It maintains the import fence direction (KernelOne → Cells only)
2. Cells do NOT import from Neural Syndicate
3. Circular dependency risk is eliminated
4. Default fallback is provided without requiring Cells at import time

## 3. Deliverables

### 3.1 Created Files

| File | Purpose |
|------|---------|
| `polaris/kernelone/events/bus_constants.py` | Unified constants for all 7 bus implementations |

### 3.2 Constants Provided

```python
# Bus implementation identifiers
BUS_IMPL_MESSAGE_BUS = "polaris.kernelone.events.message_bus.MessageBus"
BUS_IMPL_EVENT_REGISTRY = "polaris.kernelone.events.typed.registry.EventRegistry"
BUS_IMPL_TYPED_ADAPTER = "polaris.kernelone.events.typed.bus_adapter.TypedEventBusAdapter"
BUS_IMPL_IN_MEMORY_PORT = "polaris.cells.roles.runtime.internal.bus_port.InMemoryAgentBusPort"
BUS_IMPL_NATS_PORT = "polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort"
BUS_IMPL_IN_MEMORY_BROKER = "polaris.kernelone.agent_runtime.neural_syndicate.broker.InMemoryBroker"
BUS_IMPL_NATS_BROKER = "polaris.kernelone.agent_runtime.neural_syndicate.nats_broker.NATSBroker"
BUS_IMPL_UEP_PUBLISHER = "polaris.kernelone.events.uep_publisher.UEPEventPublisher"

# Bus categories
BUS_CATEGORY_SYNC = "sync"
BUS_CATEGORY_ASYNC = "async"
BUS_CATEGORY_BRIDGE = "bridge"
BUS_CATEGORY_BROKER = "broker"
BUS_CATEGORY_EXTERNAL = "external"

# Protocol defaults
DEFAULT_MAX_QUEUE_SIZE = 512
DEFAULT_MAX_DEAD_LETTERS = 256
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_POLL_TIMEOUT = 1.0
DEFAULT_POLL_INTERVAL = 0.05

# Event type mappings for bridges
TYPED_EVENT_TO_MESSAGE_TYPE: dict[str, str]
MESSAGE_TYPE_TO_TYPED_EVENT: dict[str, str]
```

## 4. Usage Guidelines

### 4.1 Choosing a Bus Implementation

| Use Case | Recommended Implementation |
|----------|----------------------------|
| Actor model communication | `MessageBus` |
| Typed observability events | `EventRegistry` |
| Migrating from MessageBus to Typed | `TypedEventBusAdapter` |
| Thread-safe agent messaging (sync) | `InMemoryAgentBusPort` |
| Cross-process agent messaging | `KernelOneMessageBusPort` |
| Neural Syndicate (in-memory) | `InMemoryBroker` |
| Neural Syndicate (cross-process) | `NATSBroker` |
| External event ingestion | `UEPEventPublisher` |

### 4.2 Bridging Between Implementations

Use `TypedEventBusAdapter` for bridging:

```python
from polaris.kernelone.events.typed.bus_adapter import TypedEventBusAdapter
from polaris.kernelone.events.message_bus import MessageBus
from polaris.kernelone.events.typed.registry import EventRegistry

# Create adapter
adapter = TypedEventBusAdapter(
    message_bus=MessageBus(),
    event_registry=EventRegistry(),
    dual_write=True,  # Emit to both systems
)

# Emit to both
await adapter.emit_to_both(my_typed_event)
```

## 5. Gap Analysis

### 5.1 Governance Gaps (Documented)

| Gap | Description | Tracking |
|-----|-------------|----------|
| NATS Transport | Full NATS integration for cross-process messaging | cell.yaml |
| Topic Routing | KernelOne-level topic routing not implemented | TBD |
| Event Schema Registry | Centralized event schema validation | TBD |

### 5.2 Not Applicable

| Concern | Resolution |
|---------|------------|
| Circular dependencies | Mitigated via lazy imports |
| Tool definition fragmentation | P0-006 addressed in `constants.py` |
| Metric naming | Out of scope for this fix |

## 6. Verification

### 6.1 Import Tests

```bash
python -c "from polaris.kernelone.agent_runtime.neural_syndicate.broker import NATSBroker"
python -c "from polaris.kernelone.agent_runtime.neural_syndicate.broker import InMemoryBroker"
python -c "from polaris.kernelone.agent_runtime.neural_syndicate.base_agent import BaseAgent"
```

### 6.2 Ruff Check

```bash
ruff check polaris/kernelone/events/bus_constants.py --fix
```

### 6.3 Mypy Check

```bash
mypy polaris/kernelone/events/bus_constants.py --follow-imports=skip --ignore-missing-imports
```

## 7. Future Considerations

### 7.1 Potential Refinements (Not Required)

1. **EventBusPort Protocol**: Define a Protocol that all bus implementations conform to
2. **Unified Metrics**: Standardize metrics naming across implementations
3. **Dead Letter UI**: Dashboard for dead letter inspection

### 7.2 Out of Scope

- Merging the 7 implementations into one class hierarchy
- Adding message routing (would require architectural redesign)
- Cross-cell event aggregation (violates Cell boundaries)

## 8. Change Log

| Date | Change |
|------|--------|
| 2026-04-05 | Initial document created |
| 2026-04-05 | `bus_constants.py` created with constants |
| 2026-04-05 | P0-007 lazy imports verified working |
