"""Correlation rules for linking audit events into causal chains."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.audit.contracts import KernelAuditEvent

logger = logging.getLogger(__name__)


class CorrelationType(str, Enum):
    CAUSAL = "causal"
    ENABLING = "enabling"
    CORRELATED = "correlated"
    MITIGATION = "mitigation"


@dataclass
class CorrelationRule:
    name: str
    description: str
    correlation_type: CorrelationType
    weight: float = 1.0
    resolution_hint: str = ""

    def matches(self, event: KernelAuditEvent) -> bool:
        raise NotImplementedError


@dataclass
class EventTypeMatchRule(CorrelationRule):
    trigger_event_type: str | None = None
    trigger_result: str | None = None

    def matches(self, event: KernelAuditEvent) -> bool:
        if self.trigger_event_type and event.event_type.value != self.trigger_event_type:
            return False
        return not (self.trigger_result and event.action.get("result") != self.trigger_result)


# Built-in rules registry
BUILTIN_RULES: list[CorrelationRule] = []


def _init_builtin_rules() -> None:
    global BUILTIN_RULES
    BUILTIN_RULES = [
        # SECURITY_VIOLATION is always root cause
        EventTypeMatchRule(
            name="security_violation_root",
            description="Security violation is always root cause",
            correlation_type=CorrelationType.CAUSAL,
            weight=1.0,
            trigger_event_type="security_violation",
            resolution_hint="SECURITY_VIOLATION detected. Review security logs and authorization configuration immediately.",
        ),
        # TOOL_EXECUTION failure + TASK_FAILED
        EventTypeMatchRule(
            name="tool_then_task_failed",
            description="Tool execution failure precedes task failure",
            correlation_type=CorrelationType.CAUSAL,
            weight=0.9,
            trigger_event_type="tool_execution",
            trigger_result="failure",
            resolution_hint="Tool execution failed. Check tool configuration, npx allowlist, and execution permissions.",
        ),
        # POLICY_CHECK blocked
        EventTypeMatchRule(
            name="policy_blocked",
            description="Policy check blocked operation",
            correlation_type=CorrelationType.CAUSAL,
            weight=0.95,
            trigger_event_type="policy_check",
            resolution_hint="Policy blocked the operation. Review policy configuration for this workspace.",
        ),
        # LLM_CALL failure
        EventTypeMatchRule(
            name="llm_call_failure",
            description="LLM call failed",
            correlation_type=CorrelationType.CAUSAL,
            weight=0.8,
            trigger_event_type="llm_call",
            trigger_result="failure",
            resolution_hint="LLM call failed. Check model availability, API key validity, and provider logs.",
        ),
        # VERIFICATION failure
        EventTypeMatchRule(
            name="verification_failure",
            description="Operation verification failed",
            correlation_type=CorrelationType.CORRELATED,
            weight=0.7,
            trigger_event_type="verification",
            trigger_result="failure",
            resolution_hint="Verification failed. Check constraint configuration and preconditions.",
        ),
    ]


_init_builtin_rules()


def get_rules() -> list[CorrelationRule]:
    return list(BUILTIN_RULES)
