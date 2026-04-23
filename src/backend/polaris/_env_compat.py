"""Anti-Corruption Layer: KERNELONE_* env var normalization.

This module provides an early-initialization bridge that maps legacy
``KERNELONE_*`` environment variables to their canonical ``KERNELONE_*``
counterparts.  It must be invoked **before** any ``polaris.kernelone``
import occurs, so that the kernel layer always sees the canonical prefix.

Design rationale (see AGENTS.md §4.2.1):
- KernelOne is product-agnostic; it must not know about Polaris branding.
- ``KERNELONE_*`` is the canonical env-var namespace for the kernel layer.
- ``KERNELONE_*`` is accepted at the user-facing boundary (CLI, .env files,
  Docker compose, CI/CD scripts) for backward compatibility.
- This ACL guarantees that internal code only ever sees ``KERNELONE_*``.

Migration path:
1. Present: Both prefixes accepted; KERNELONE_* triggers a deprecation notice.
2. Future (v2.0): KERNELONE_* support will be removed entirely.
"""

from __future__ import annotations

import logging
import os
import warnings

logger = logging.getLogger(__name__)

# Mapping of legacy KERNELONE_* names to canonical KERNELONE_* names.
# Only variables that actually exist in the environment are processed.
# If the canonical name is already set, the legacy value is ignored.
_KERNELONE_TO_KERNELONE: dict[str, str] = {
    # ─── Workspace / runtime roots ──────────────────────────────────────────
    "KERNELONE_WORKSPACE": "KERNELONE_WORKSPACE",
    "KERNELONE_RUNTIME_ROOT": "KERNELONE_RUNTIME_ROOT",
    "KERNELONE_RUNTIME_BASE": "KERNELONE_RUNTIME_BASE",
    "KERNELONE_RUNTIME_CACHE_ROOT": "KERNELONE_RUNTIME_CACHE_ROOT",
    "KERNELONE_RAMDISK_ROOT": "KERNELONE_RAMDISK_ROOT",
    "KERNELONE_STATE_TO_RAMDISK": "KERNELONE_STATE_TO_RAMDISK",
    "KERNELONE_HOME": "KERNELONE_HOME",
    # ─── Server / network ───────────────────────────────────────────────────
    "KERNELONE_HOST": "KERNELONE_HOST",
    "KERNELONE_BACKEND_PORT": "KERNELONE_BACKEND_PORT",
    "KERNELONE_CORS_ORIGINS": "KERNELONE_CORS_ORIGINS",
    "KERNELONE_BACKEND_TOKEN": "KERNELONE_BACKEND_TOKEN",
    # ─── Logging / tracing ──────────────────────────────────────────────────
    "KERNELONE_LOG_LEVEL": "KERNELONE_LOG_LEVEL",
    "KERNELONE_DEBUG_TRACING": "KERNELONE_DEBUG_TRACING",
    "KERNELONE_JSON_LOG_PATH": "KERNELONE_JSON_LOG_PATH",
    # ─── LLM ────────────────────────────────────────────────────────────────
    "KERNELONE_MODEL": "KERNELONE_MODEL",
    "KERNELONE_LLM_PROVIDER": "KERNELONE_LLM_PROVIDER",
    "KERNELONE_LLM_BASE_URL": "KERNELONE_LLM_BASE_URL",
    "KERNELONE_LLM_API_KEY": "KERNELONE_LLM_API_KEY",
    "KERNELONE_LLM_API_PATH": "KERNELONE_LLM_API_PATH",
    "KERNELONE_LLM_TIMEOUT": "KERNELONE_LLM_TIMEOUT",
    "KERNELONE_LLM_CONFIG": "KERNELONE_LLM_CONFIG",
    # ─── PM / Director ──────────────────────────────────────────────────────
    "KERNELONE_PM_BACKEND": "KERNELONE_PM_BACKEND",
    "KERNELONE_PM_MODEL": "KERNELONE_PM_MODEL",
    "KERNELONE_PM_SHOW_OUTPUT": "KERNELONE_PM_SHOW_OUTPUT",
    "KERNELONE_PM_RUNS_DIRECTOR": "KERNELONE_PM_RUNS_DIRECTOR",
    "KERNELONE_PM_DIRECTOR_TIMEOUT": "KERNELONE_PM_DIRECTOR_TIMEOUT",
    "KERNELONE_PM_DIRECTOR_ITERATIONS": "KERNELONE_PM_DIRECTOR_ITERATIONS",
    "KERNELONE_PM_DIRECTOR_MATCH_MODE": "KERNELONE_PM_DIRECTOR_MATCH_MODE",
    "KERNELONE_DIRECTOR_MODEL": "KERNELONE_DIRECTOR_MODEL",
    "KERNELONE_DIRECTOR_ITERATIONS": "KERNELONE_DIRECTOR_ITERATIONS",
    "KERNELONE_DIRECTOR_TYPE": "KERNELONE_DIRECTOR_TYPE",
    # ─── NATS ───────────────────────────────────────────────────────────────
    "KERNELONE_NATS_ENABLED": "KERNELONE_NATS_ENABLED",
    "KERNELONE_NATS_REQUIRED": "KERNELONE_NATS_REQUIRED",
    "KERNELONE_NATS_URL": "KERNELONE_NATS_URL",
    "KERNELONE_NATS_USER": "KERNELONE_NATS_USER",
    "KERNELONE_NATS_PASSWORD": "KERNELONE_NATS_PASSWORD",
    "KERNELONE_NATS_CONNECT_TIMEOUT": "KERNELONE_NATS_CONNECT_TIMEOUT",
    "KERNELONE_NATS_RECONNECT_WAIT": "KERNELONE_NATS_RECONNECT_WAIT",
    "KERNELONE_NATS_MAX_RECONNECT": "KERNELONE_NATS_MAX_RECONNECT",
    "KERNELONE_NATS_STREAM_NAME": "KERNELONE_NATS_STREAM_NAME",
    "KERNELONE_NATS_SERVER_BIN": "KERNELONE_NATS_SERVER_BIN",
    # ─── JSONL / I/O ────────────────────────────────────────────────────────
    "KERNELONE_JSONL_LOCK_STALE_SEC": "KERNELONE_JSONL_LOCK_STALE_SEC",
    "KERNELONE_JSONL_BUFFERED": "KERNELONE_JSONL_BUFFERED",
    "KERNELONE_JSONL_FLUSH_INTERVAL": "KERNELONE_JSONL_FLUSH_INTERVAL",
    "KERNELONE_JSONL_FLUSH_BATCH": "KERNELONE_JSONL_FLUSH_BATCH",
    "KERNELONE_JSONL_MAX_BUFFER": "KERNELONE_JSONL_MAX_BUFFER",
    "KERNELONE_JSONL_BUFFER_TTL": "KERNELONE_JSONL_BUFFER_TTL",
    "KERNELONE_JSONL_MAX_PATHS": "KERNELONE_JSONL_MAX_PATHS",
    "KERNELONE_JSONL_CLEANUP_INTERVAL": "KERNELONE_JSONL_CLEANUP_INTERVAL",
    "KERNELONE_IO_FSYNC_MODE": "KERNELONE_IO_FSYNC_MODE",
    # ─── Context OS / Cognitive Runtime ─────────────────────────────────────
    "KERNELONE_CONTEXT_OS_ENABLED": "KERNELONE_CONTEXT_OS_ENABLED",
    "KERNELONE_COGNITIVE_RUNTIME_MODE": "KERNELONE_COGNITIVE_RUNTIME_MODE",
    # ─── Audit ──────────────────────────────────────────────────────────────
    "KERNELONE_AUDIT_LLM_ENABLED": "KERNELONE_AUDIT_LLM_ENABLED",
    "KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA": "KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA",
    "KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK": "KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK",
    "KERNELONE_AUDIT_LLM_ROLE": "KERNELONE_AUDIT_LLM_ROLE",
    "KERNELONE_AUDIT_LLM_TIMEOUT": "KERNELONE_AUDIT_LLM_TIMEOUT",
    "KERNELONE_PROTOCOL_AUDIT": "KERNELONE_PROTOCOL_AUDIT",
    "KERNELONE_REQUIRE_SIGNED_TOOL_TAGS": "KERNELONE_REQUIRE_SIGNED_TOOL_TAGS",
    # ─── Events / dedup ─────────────────────────────────────────────────────
    "KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC": "KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC",
    "KERNELONE_LLM_EVENT_DEDUP_WINDOW_SEC": "KERNELONE_LLM_EVENT_DEDUP_WINDOW_SEC",
    # ─── Bus / messaging ────────────────────────────────────────────────────
    "KERNELONE_MESSAGE_HANDLER_TIMEOUT_SECONDS": "KERNELONE_MESSAGE_HANDLER_TIMEOUT_SECONDS",
    # ─── Embedding ──────────────────────────────────────────────────────────
    "KERNELONE_EMBEDDING_MODEL": "KERNELONE_EMBEDDING_MODEL",
    # ─── Trace / context ────────────────────────────────────────────────────
    "KERNELONE_TRACE_ID": "KERNELONE_TRACE_ID",
    "KERNELONE_RUN_ID": "KERNELONE_RUN_ID",
    "KERNELONE_REQUEST_ID": "KERNELONE_REQUEST_ID",
    "KERNELONE_WORKFLOW_ID": "KERNELONE_WORKFLOW_ID",
    "KERNELONE_TASK_ID": "KERNELONE_TASK_ID",
    # ─── Token / auth ───────────────────────────────────────────────────────
    "KERNELONE_TOKEN": "KERNELONE_TOKEN",
    "KERNELONE_TOKEN_BUDGET": "KERNELONE_TOKEN_BUDGET",
    # ─── Self-upgrade ───────────────────────────────────────────────────────
    "KERNELONE_SELF_UPGRADE_MODE": "KERNELONE_SELF_UPGRADE_MODE",
    # ─── Timeout ────────────────────────────────────────────────────────────
    "KERNELONE_TIMEOUT": "KERNELONE_TIMEOUT",
    # ─── Version ────────────────────────────────────────────────────────────
    "KERNELONE_VERSION": "KERNELONE_VERSION",
}


def normalize_env_prefix() -> None:
    """Map legacy ``KERNELONE_*`` env vars to ``KERNELONE_*`` before kernel import.

    This function is idempotent: calling it multiple times is safe.
    It only copies values when the canonical ``KERNELONE_*`` key is **not**
    already present, ensuring explicit ``KERNELONE_*`` settings always win.
    """
    mapped: list[str] = []
    for polaris_key, kernelone_key in _KERNELONE_TO_KERNELONE.items():
        polaris_value = os.environ.get(polaris_key)
        if polaris_value is None:
            continue
        kernelone_value = os.environ.get(kernelone_key)
        if kernelone_value is not None:
            # Canonical key already set; KERNELONE_* value is ignored.
            continue
        os.environ[kernelone_key] = polaris_value
        mapped.append(polaris_key)

    if mapped:
        # Prominent console notice for operators (not just a Python warning).
        # This prints directly to stderr so it is visible even when logging
        # is not yet configured.
        import sys

        _msg = (
            "[ENV COMPAT] The following KERNELONE_* environment variables are deprecated "
            "and will be removed in a future release. Please migrate to KERNELONE_*:\n"
        )
        for key in mapped:
            _msg += f"  {key}  ->  {_KERNELONE_TO_KERNELONE[key]}\n"
        _msg += "See: https://github.com/anthropics/polaris/blob/main/docs/env-migration.md\n"
        print(_msg, file=sys.stderr)

        # Also emit standard Python DeprecationWarning for test frameworks
        # and IDE inspections.
        warnings.warn(
            f"KERNELONE_* env vars are deprecated ({', '.join(mapped)}). "
            "Migrate to KERNELONE_* equivalents. "
            "KERNELONE_* support will be removed in v2.0.",
            DeprecationWarning,
            stacklevel=2,
        )
