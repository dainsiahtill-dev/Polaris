"""Unified Event Topic Constants for Polaris.

This module defines the canonical topic names for the Unified Event Pipeline (UEP v2.0).
All topics use dot notation for compatibility with CloudEvents and NATS best practices.

Architecture:
    - Topics are hierarchical: domain.event.category
    - Single source of truth for all event routing
    - Consumers subscribe by topic prefix for filtering

Usage:
    from polaris.kernelone.events.topics import (
        TOPIC_RUNTIME_STREAM,
        TOPIC_RUNTIME_LLM,
        TOPIC_RUNTIME_FINGERPRINT,
        TOPIC_RUNTIME_AUDIT,
    )
"""

from __future__ import annotations

# =============================================================================
# Runtime Event Topics (UEP v2.0)
# =============================================================================

# Stream events: content chunks, tool calls, completions, errors
TOPIC_RUNTIME_STREAM = "runtime.event.stream"

# LLM lifecycle events: call_start, call_end, call_error, call_retry
TOPIC_RUNTIME_LLM = "runtime.event.llm"

# Strategy fingerprint events: profiling, strategy markers
TOPIC_RUNTIME_FINGERPRINT = "runtime.event.fingerprint"

# Audit events: HMAC chain, compliance tracking
TOPIC_RUNTIME_AUDIT = "runtime.event.audit"

# =============================================================================
# Topic Groups for Subscription Filtering
# =============================================================================

# All UEP runtime topics (for broad subscription)
UEP_RUNTIME_TOPICS: set[str] = {
    TOPIC_RUNTIME_STREAM,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_FINGERPRINT,
    TOPIC_RUNTIME_AUDIT,
}

# Topics requiring persistence (journal/archive sinks)
UEP_PERSISTENCE_TOPICS: set[str] = {
    TOPIC_RUNTIME_STREAM,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_FINGERPRINT,
}

# Topics requiring HMAC chain verification
UEP_SECURE_TOPICS: set[str] = {
    TOPIC_RUNTIME_AUDIT,
}

# =============================================================================
# Topic-to-Category Mapping (for TypedEvent conversion)
# =============================================================================

UEP_TOPIC_TO_CATEGORY: dict[str, str] = {
    TOPIC_RUNTIME_STREAM: "tool",
    TOPIC_RUNTIME_LLM: "lifecycle",
    TOPIC_RUNTIME_FINGERPRINT: "context",
    TOPIC_RUNTIME_AUDIT: "audit",
}

__all__ = [
    "TOPIC_RUNTIME_AUDIT",
    "TOPIC_RUNTIME_FINGERPRINT",
    "TOPIC_RUNTIME_LLM",
    # Topic constants
    "TOPIC_RUNTIME_STREAM",
    "UEP_PERSISTENCE_TOPICS",
    # Topic groups
    "UEP_RUNTIME_TOPICS",
    "UEP_SECURE_TOPICS",
    # Mapping
    "UEP_TOPIC_TO_CATEGORY",
]
