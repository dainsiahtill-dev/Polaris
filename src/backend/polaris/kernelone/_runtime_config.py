"""KernelOne runtime configuration.

This module provides the canonical runtime configuration for the KernelOne kernel layer.
It is product-agnostic: KernelOne itself knows nothing about Polaris branding.

Design principles:
1. Generic KERNELONE_* env vars are the canonical source.
2. POLARIS_* env vars are accepted as backward-compatible fallbacks
   (Polaris injects these; the bootstrap layer maps them).
3. Workspace metadata directory name is a logical prefix, not hardcoded,
   injected by the application layer.

This module MUST NOT import from Polaris-specific cells or application layers.
"""

from __future__ import annotations

import os

# ─── Logical prefix for workspace metadata directory ───────────────────────────
# This is the logical name used in the workspace layout, e.g. <workspace>/<PREFIX>/*.
# The bootstrap layer (Polaris-specific) injects the actual physical name.
# Default is ".polaris" for a clean Polaris-based deployment.
_WORKSPACE_METADATA_DIR_NAME: str = ".polaris"

# ─── Env var name mappings ───────────────────────────────────────────────────
# Maps a logical kernel var name -> (KERNELONE_NAME, POLARIS_FALLBACK, DEFAULT)
# Used by _resolve_with_fallback().
_ENV_MAPPINGS: dict[str, tuple[str, str, str]] = {
    # Workspace / runtime roots
    "workspace": (
        "KERNELONE_WORKSPACE",
        "POLARIS_WORKSPACE",
        "",
    ),
    "runtime_root": (
        "KERNELONE_RUNTIME_ROOT",
        "POLARIS_RUNTIME_ROOT",
        "",
    ),
    "runtime_base": (
        "KERNELONE_RUNTIME_BASE",
        "POLARIS_RUNTIME_BASE",
        "runtime",
    ),
    "runtime_cache_root": (
        "KERNELONE_RUNTIME_CACHE_ROOT",
        "POLARIS_RUNTIME_CACHE_ROOT",
        "",
    ),
    # Storage
    "home": (
        "KERNELONE_HOME",
        "POLARIS_HOME",
        "",
    ),
    "ramdisk_root": (
        "KERNELONE_RAMDISK_ROOT",
        "POLARIS_RAMDISK_ROOT",
        "",
    ),
    "state_to_ramdisk": (
        "KERNELONE_STATE_TO_RAMDISK",
        "POLARIS_STATE_TO_RAMDISK",
        "1",
    ),
    # Events / dedup
    "runtime_event_dedup_window_sec": (
        "KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC",
        "POLARIS_RUNTIME_EVENT_DEDUP_WINDOW_SEC",
        "1.5",
    ),
    "llm_event_dedup_window_sec": (
        "KERNELONE_LLM_EVENT_DEDUP_WINDOW_SEC",
        "POLARIS_LLM_EVENT_DEDUP_WINDOW_SEC",
        "1.5",
    ),
    # JSONL / fsync
    "jsonl_lock_stale_sec": (
        "KERNELONE_JSONL_LOCK_STALE_SEC",
        "POLARIS_JSONL_LOCK_STALE_SEC",
        "120",
    ),
    "jsonl_buffered": (
        "KERNELONE_JSONL_BUFFERED",
        "POLARIS_JSONL_BUFFERED",
        "1",
    ),
    "jsonl_flush_interval": (
        "KERNELONE_JSONL_FLUSH_INTERVAL",
        "POLARIS_JSONL_FLUSH_INTERVAL",
        "1.0",
    ),
    "jsonl_flush_batch": (
        "KERNELONE_JSONL_FLUSH_BATCH",
        "POLARIS_JSONL_FLUSH_BATCH",
        "50",
    ),
    "jsonl_max_buffer": (
        "KERNELONE_JSONL_MAX_BUFFER",
        "POLARIS_JSONL_MAX_BUFFER",
        "2000",
    ),
    "jsonl_buffer_ttl": (
        "KERNELONE_JSONL_BUFFER_TTL",
        "POLARIS_JSONL_BUFFER_TTL",
        "300",
    ),
    "jsonl_max_paths": (
        "KERNELONE_JSONL_MAX_PATHS",
        "POLARIS_JSONL_MAX_PATHS",
        "100",
    ),
    "io_fsync_mode": (
        "KERNELONE_IO_FSYNC_MODE",
        "POLARIS_IO_FSYNC_MODE",
        "strict",
    ),
    # Bus / messaging
    "message_handler_timeout_sec": (
        "KERNELONE_MESSAGE_HANDLER_TIMEOUT_SECONDS",
        "POLARIS_MESSAGE_HANDLER_TIMEOUT_SECONDS",
        "5.0",
    ),
    "nats_url": (
        "KERNELONE_NATS_URL",
        "POLARIS_NATS_URL",
        "nats://localhost:4222",
    ),
    # Trace / context
    "trace_id": (
        "KERNELONE_TRACE_ID",
        "POLARIS_TRACE_ID",
        "",
    ),
    "run_id": (
        "KERNELONE_RUN_ID",
        "POLARIS_RUN_ID",
        "",
    ),
    "request_id": (
        "KERNELONE_REQUEST_ID",
        "POLARIS_REQUEST_ID",
        "",
    ),
    "workflow_id": (
        "KERNELONE_WORKFLOW_ID",
        "POLARIS_WORKFLOW_ID",
        "",
    ),
    "task_id": (
        "KERNELONE_TASK_ID",
        "POLARIS_TASK_ID",
        "",
    ),
    # LLM concurrency / timeout
    "llm_max_concurrency": (
        "KERNELONE_LLM_MAX_CONCURRENCY",
        "POLARIS_LLM_MAX_CONCURRENCY",
        "100",
    ),
    "llm_invoke_timeout_sec": (
        "KERNELONE_LLM_INVOKE_TIMEOUT_SEC",
        "POLARIS_LLM_INVOKE_TIMEOUT_SEC",
        "30",
    ),
    "llm_stream_timeout_sec": (
        "KERNELONE_LLM_STREAM_TIMEOUT_SEC",
        "POLARIS_LLM_STREAM_TIMEOUT_SEC",
        "300",
    ),
    # Tool loop safety
    "tool_loop_read_file_content_chars": (
        "KERNELONE_TOOL_LOOP_READ_FILE_CONTENT_CHARS",
        "POLARIS_TOOL_LOOP_READ_FILE_CONTENT_CHARS",
        "16000",
    ),
    "tool_loop_read_file_headroom_ratio": (
        "KERNELONE_TOOL_LOOP_READ_FILE_HEADROOM_RATIO",
        "POLARIS_TOOL_LOOP_READ_FILE_HEADROOM_RATIO",
        "0.35",
    ),
    # Context OS / Cognitive Runtime
    "context_os_enabled": (
        "KERNELONE_CONTEXT_OS_ENABLED",
        "POLARIS_CONTEXT_OS_ENABLED",
        "1",
    ),
    "cognitive_runtime_mode": (
        "KERNELONE_COGNITIVE_RUNTIME_MODE",
        "POLARIS_COGNITIVE_RUNTIME_MODE",
        "shadow",
    ),
    # Embedding
    "embedding_model": (
        "KERNELONE_EMBEDDING_MODEL",
        "POLARIS_EMBEDDING_MODEL",
        "nomic-embed-text",
    ),
    # Audit
    "audit_hmac_key": (
        "KERNELONE_AUDIT_HMAC_KEY",
        "POLARIS_AUDIT_HMAC_KEY",
        "",
    ),
    "protocol_audit": (
        "KERNELONE_PROTOCOL_AUDIT",
        "POLARIS_PROTOCOL_AUDIT",
        "true",
    ),
    "require_signed_tool_tags": (
        "KERNELONE_REQUIRE_SIGNED_TOOL_TAGS",
        "POLARIS_REQUIRE_SIGNED_TOOL_TAGS",
        "",
    ),
    # Version tracking
    "version": (
        "KERNELONE_VERSION",
        "POLARIS_VERSION",
        "",
    ),
}

# Env vars that need float parsing
_FLOAT_VARS: set[str] = {
    "runtime_event_dedup_window_sec",
    "llm_event_dedup_window_sec",
    "jsonl_lock_stale_sec",
    "jsonl_flush_interval",
    "jsonl_buffer_ttl",
    "message_handler_timeout_sec",
    "llm_invoke_timeout_sec",
    "llm_stream_timeout_sec",
    "tool_loop_read_file_headroom_ratio",
}

# Env vars that need int parsing
_INT_VARS: set[str] = {
    "jsonl_flush_batch",
    "jsonl_max_buffer",
    "jsonl_max_paths",
    "llm_max_concurrency",
    "tool_loop_read_file_content_chars",
}

# Env vars that need bool parsing (truthy/falsy)
_BOOL_VARS: set[str] = {
    "jsonl_buffered",
    "state_to_ramdisk",
    "context_os_enabled",
    "protocol_audit",
}


def _resolve_with_fallback(name: str) -> str:
    """Resolve an env var using KERNELONE_* priority, POLARIS_* fallback."""
    mapping = _ENV_MAPPINGS.get(name)
    if mapping is None:
        return ""
    kern_name, hp_name, default = mapping
    # Priority: KERNELONE_* first, then POLARIS_*, then default
    value = os.environ.get(kern_name) or os.environ.get(hp_name) or default
    return str(value) if value is not None else ""


def resolve_env_str(name: str) -> str:
    """Resolve a string env var by logical name.

    Returns the string value, or "" if not set and no default.
    """
    return _resolve_with_fallback(name)


def resolve_env_float(name: str) -> float:
    """Resolve a float env var by logical name.

    Returns the parsed float, or 0.0 on parse failure.
    """
    raw = _resolve_with_fallback(name)
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def resolve_env_int(name: str) -> int:
    """Resolve an int env var by logical name.

    Returns the parsed int, or 0 on parse failure.
    """
    raw = _resolve_with_fallback(name)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def resolve_env_bool(name: str) -> bool:
    """Resolve a bool env var by logical name.

    Returns True unless the value is one of: "0", "false", "no", "off".
    """
    raw = _resolve_with_fallback(name).strip().lower()
    return raw not in ("0", "false", "no", "off")


# ─── Workspace metadata directory name ───────────────────────────────────────


def set_workspace_metadata_dir_name(name: str) -> None:
    """Inject the workspace metadata directory name from the bootstrap layer.

    This allows Polaris (or any product) to set its physical directory name
    (e.g. ".polaris") while KernelOne uses a logical name internally.

    Args:
        name: The physical directory name (e.g. ".polaris", ".polaris")
    """
    global _WORKSPACE_METADATA_DIR_NAME
    _WORKSPACE_METADATA_DIR_NAME = str(name).strip() or ".polaris"


def get_workspace_metadata_dir_name() -> str:
    """Return the workspace metadata directory logical name.

    This is the logical prefix used in the workspace layout taxonomy.
    The bootstrap layer injects the physical name via set_workspace_metadata_dir_name().
    """
    return _WORKSPACE_METADATA_DIR_NAME


def get_workspace_metadata_dir_default() -> str:
    """Return the default workspace metadata directory name when no injection occurred.

    This is the fallback used when the bootstrap layer has not injected a custom name.
    Returns ".polaris" for a Polaris-based deployment.
    """
    return ".polaris"


# ─── Convenience accessors for the most common vars ───────────────────────────


def get_workspace() -> str:
    """Return the configured workspace path, or empty string."""
    return resolve_env_str("workspace")


def get_runtime_root() -> str:
    """Return the configured runtime root, or empty string."""
    return resolve_env_str("runtime_root")


def get_runtime_base() -> str:
    """Return the configured runtime base, or 'runtime'."""
    return resolve_env_str("runtime_base") or "runtime"


def get_home() -> str:
    """Return the configured kernel home, or empty string."""
    return resolve_env_str("home")


def get_trace_id() -> str | None:
    """Return the trace ID from env, or None."""
    val = resolve_env_str("trace_id")
    return val if val else None


def get_run_id() -> str | None:
    """Return the run ID from env, or None."""
    val = resolve_env_str("run_id")
    return val if val else None


__all__ = [
    # Convenience accessors
    "get_home",
    "get_run_id",
    "get_runtime_base",
    "get_runtime_root",
    "get_trace_id",
    "get_workspace",
    "get_workspace_metadata_dir_default",
    # Injection point for bootstrap
    "get_workspace_metadata_dir_name",
    # Env var resolution
    "resolve_env_bool",
    "resolve_env_float",
    "resolve_env_int",
    "resolve_env_str",
    "set_workspace_metadata_dir_name",
]
