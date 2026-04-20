"""Omniscient Audit Schemas — Pydantic v2 event definitions.

Schema versioning follows CloudEvents spec:
- version: str — schema version for evolution
- schema_uri: str — pointer to schema definition
- All events are immutable (frozen=True)

Event hierarchy:
    AuditEvent (base)
    ├── LLMEvent — LLM interaction audit
    ├── ToolEvent — Tool/function call audit
    ├── DialogueEvent — Role communication audit
    ├── ContextEvent — Context management audit
    └── TaskEvent — Task orchestration audit
"""

from __future__ import annotations

from polaris.kernelone.audit.omniscient.schemas.base import (
    AuditEvent,
    AuditPriority,
    EventDomain,
)
from polaris.kernelone.audit.omniscient.schemas.context_event import ContextEvent
from polaris.kernelone.audit.omniscient.schemas.dialogue_event import DialogueEvent
from polaris.kernelone.audit.omniscient.schemas.llm_event import LLMEvent
from polaris.kernelone.audit.omniscient.schemas.task_event import TaskEvent
from polaris.kernelone.audit.omniscient.schemas.tool_event import ToolEvent

__all__ = [
    "AuditEvent",
    "AuditPriority",
    "ContextEvent",
    "DialogueEvent",
    "EventDomain",
    "LLMEvent",
    "TaskEvent",
    "ToolEvent",
]
