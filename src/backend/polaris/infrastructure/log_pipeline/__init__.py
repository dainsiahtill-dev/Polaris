"""Log Pipeline - Unified Log Ingestion and Processing.

This module provides a unified pipeline for all log events:
- Ingest: Collect from all sources (subprocess, emit_event, etc.)
- Normalize: Convert to CanonicalLogEventV2 schema
- Deduplicate: Remove duplicate events via fingerprint
- Persist: Write to三层 files (raw, norm, enriched)
- Publish: Emit to realtime subscribers
- Enrich: Async LLM enhancement of events

Usage:
    from polaris.infrastructure.log_pipeline import LogEventWriter

    writer = LogEventWriter(workspace=".", run_id="run-123")
    writer.write_event(channel="system", message="Task started", actor="PM")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.infrastructure.realtime.process_local.log_fanout import (
    LOG_REALTIME_FANOUT,
    RealtimeLogFanout,
    RealtimeLogSubscription,
)

# Re-export adapters
from .adapters import (
    adapt_emit_dialogue,
    adapt_emit_event,
    adapt_emit_llm_event,
    get_legacy_channel_path,
)

# Re-export key types
from .canonical_event import (
    LEGACY_CHANNEL_MAPPING,
    CanonicalLogEventV2,
    LogChannel,
    LogDomain,
    LogEnrichmentV1,
    LogKind,
    LogSeverity,
    normalize_legacy_event,
)

# Re-export enrichment
from .enrichment import (
    EnrichmentConfig,
    EnrichmentWorkerPool,
    LLMEnrichmentWorker,
    get_enrichment_pool,
)

# Re-export query service
from .query import LogQuery, LogQueryResult, LogQueryService, get_query_service

# Re-export run context
from .run_context import (
    ActiveRunContext,
    RunContextManager,
    get_active_run_context,
    resolve_current_run_id,
    resolve_current_workspace,
    set_active_run_context,
)

# Re-export writer
from .writer import LogEventWriter, get_writer

__all__ = [
    # Legacy mapping
    "LEGACY_CHANNEL_MAPPING",
    "LOG_REALTIME_FANOUT",
    # Run context
    "ActiveRunContext",
    # Core model
    "CanonicalLogEventV2",
    # Enrichment
    "EnrichmentConfig",
    "EnrichmentWorkerPool",
    "LLMEnrichmentWorker",
    "LogChannel",
    "LogDomain",
    "LogEnrichmentV1",
    # Writer
    "LogEventWriter",
    "LogKind",
    "LogQuery",
    "LogQueryResult",
    # Query
    "LogQueryService",
    "LogSeverity",
    # Realtime fanout
    "RealtimeLogFanout",
    "RealtimeLogSubscription",
    "RunContextManager",
    # Legacy adapters
    "adapt_emit_dialogue",
    "adapt_emit_event",
    "adapt_emit_llm_event",
    "get_active_run_context",
    "get_enrichment_pool",
    "get_legacy_channel_path",
    "get_query_service",
    "get_writer",
    "normalize_legacy_event",
    "resolve_current_run_id",
    "resolve_current_workspace",
    "set_active_run_context",
]


# Version guard for type checking
CANONICAL_LOG_EVENT_V2_GUARD = True
