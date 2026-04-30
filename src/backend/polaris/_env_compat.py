"""Anti-Corruption Layer: POLARIS_* → KERNELONE_* env var normalization.

This module provides an early-initialization bridge that maps legacy
``POLARIS_*`` environment variables to their canonical ``KERNELONE_*``
counterparts.  It must be invoked **before** any ``polaris.kernelone``
import occurs, so that the kernel layer always sees the canonical prefix.

Design rationale (see AGENTS.md §4.2.1):
- KernelOne is product-agnostic; it must not know about Polaris branding.
- ``KERNELONE_*`` is the canonical env-var namespace for the kernel layer.
- ``POLARIS_*`` is accepted at the user-facing boundary (CLI, .env files,
  Docker compose, CI/CD scripts) for backward compatibility.
- This ACL guarantees that internal code only ever sees ``KERNELONE_*``.

Migration path:
1. Present: Both prefixes accepted; POLARIS_* triggers a deprecation notice.
2. Future (v2.0): POLARIS_* support will be removed entirely.
"""

from __future__ import annotations

import logging
import os
import warnings

logger = logging.getLogger(__name__)

# Mapping of legacy POLARIS_* names to canonical KERNELONE_* names.
# Only variables that actually exist in the environment are processed.
# If the canonical name is already set, the legacy value is ignored.
_POLARIS_TO_KERNELONE: dict[str, str] = {
    # ─── Workspace / runtime roots ──────────────────────────────────────────
    "POLARIS_WORKSPACE": "KERNELONE_WORKSPACE",
    "POLARIS_RUNTIME_ROOT": "KERNELONE_RUNTIME_ROOT",
    "POLARIS_RUNTIME_BASE": "KERNELONE_RUNTIME_BASE",
    "POLARIS_RUNTIME_CACHE_ROOT": "KERNELONE_RUNTIME_CACHE_ROOT",
    "POLARIS_RAMDISK_ROOT": "KERNELONE_RAMDISK_ROOT",
    "POLARIS_STATE_TO_RAMDISK": "KERNELONE_STATE_TO_RAMDISK",
    "POLARIS_HOME": "KERNELONE_HOME",
    # ─── Server / network ───────────────────────────────────────────────────
    "POLARIS_HOST": "KERNELONE_HOST",
    "POLARIS_BACKEND_PORT": "KERNELONE_BACKEND_PORT",
    "POLARIS_CORS_ORIGINS": "KERNELONE_CORS_ORIGINS",
    "POLARIS_BACKEND_TOKEN": "KERNELONE_TOKEN",
    # ─── Logging / tracing ──────────────────────────────────────────────────
    "POLARIS_LOG_LEVEL": "KERNELONE_LOG_LEVEL",
    "POLARIS_DEBUG_TRACING": "KERNELONE_DEBUG_TRACING",
    "POLARIS_JSON_LOG_PATH": "KERNELONE_JSON_LOG_PATH",
    # ─── LLM ────────────────────────────────────────────────────────────────
    "POLARIS_MODEL": "KERNELONE_MODEL",
    "POLARIS_LLM_PROVIDER": "KERNELONE_LLM_PROVIDER",
    "POLARIS_LLM_BASE_URL": "KERNELONE_LLM_BASE_URL",
    "POLARIS_LLM_API_KEY": "KERNELONE_LLM_API_KEY",
    "POLARIS_LLM_API_PATH": "KERNELONE_LLM_API_PATH",
    "POLARIS_LLM_TIMEOUT": "KERNELONE_LLM_TIMEOUT",
    "POLARIS_LLM_CONFIG": "KERNELONE_LLM_CONFIG",
    # ─── PM / Director ──────────────────────────────────────────────────────
    "POLARIS_PM_BACKEND": "KERNELONE_PM_BACKEND",
    "POLARIS_PM_MODEL": "KERNELONE_PM_MODEL",
    "POLARIS_PM_SHOW_OUTPUT": "KERNELONE_PM_SHOW_OUTPUT",
    "POLARIS_PM_RUNS_DIRECTOR": "KERNELONE_PM_RUNS_DIRECTOR",
    "POLARIS_PM_DIRECTOR_TIMEOUT": "KERNELONE_PM_DIRECTOR_TIMEOUT",
    "POLARIS_PM_DIRECTOR_ITERATIONS": "KERNELONE_PM_DIRECTOR_ITERATIONS",
    "POLARIS_PM_DIRECTOR_MATCH_MODE": "KERNELONE_PM_DIRECTOR_MATCH_MODE",
    "POLARIS_DIRECTOR_MODEL": "KERNELONE_DIRECTOR_MODEL",
    "POLARIS_DIRECTOR_ITERATIONS": "KERNELONE_DIRECTOR_ITERATIONS",
    "POLARIS_DIRECTOR_TYPE": "KERNELONE_DIRECTOR_TYPE",
    # ─── NATS ───────────────────────────────────────────────────────────────
    "POLARIS_NATS_ENABLED": "KERNELONE_NATS_ENABLED",
    "POLARIS_NATS_REQUIRED": "KERNELONE_NATS_REQUIRED",
    "POLARIS_NATS_URL": "KERNELONE_NATS_URL",
    "POLARIS_NATS_USER": "KERNELONE_NATS_USER",
    "POLARIS_NATS_PASSWORD": "KERNELONE_NATS_PASSWORD",
    "POLARIS_NATS_CONNECT_TIMEOUT": "KERNELONE_NATS_CONNECT_TIMEOUT",
    "POLARIS_NATS_RECONNECT_WAIT": "KERNELONE_NATS_RECONNECT_WAIT",
    "POLARIS_NATS_MAX_RECONNECT": "KERNELONE_NATS_MAX_RECONNECT",
    "POLARIS_NATS_STREAM_NAME": "KERNELONE_NATS_STREAM_NAME",
    "POLARIS_NATS_SERVER_BIN": "KERNELONE_NATS_SERVER_BIN",
    # ─── JSONL / I/O ────────────────────────────────────────────────────────
    "POLARIS_JSONL_LOCK_STALE_SEC": "KERNELONE_JSONL_LOCK_STALE_SEC",
    "POLARIS_JSONL_BUFFERED": "KERNELONE_JSONL_BUFFERED",
    "POLARIS_JSONL_FLUSH_INTERVAL": "KERNELONE_JSONL_FLUSH_INTERVAL",
    "POLARIS_JSONL_FLUSH_BATCH": "KERNELONE_JSONL_FLUSH_BATCH",
    "POLARIS_JSONL_MAX_BUFFER": "KERNELONE_JSONL_MAX_BUFFER",
    "POLARIS_JSONL_BUFFER_TTL": "KERNELONE_JSONL_BUFFER_TTL",
    "POLARIS_JSONL_MAX_PATHS": "KERNELONE_JSONL_MAX_PATHS",
    "POLARIS_JSONL_CLEANUP_INTERVAL": "KERNELONE_JSONL_CLEANUP_INTERVAL",
    "POLARIS_IO_FSYNC_MODE": "KERNELONE_IO_FSYNC_MODE",
    # ─── Context OS / Cognitive Runtime ─────────────────────────────────────
    "POLARIS_CONTEXT_OS_ENABLED": "KERNELONE_CONTEXT_OS_ENABLED",
    "POLARIS_COGNITIVE_RUNTIME_MODE": "KERNELONE_COGNITIVE_RUNTIME_MODE",
    # ─── Audit ──────────────────────────────────────────────────────────────
    "POLARIS_AUDIT_LLM_ENABLED": "KERNELONE_AUDIT_LLM_ENABLED",
    "POLARIS_AUDIT_LLM_PREFER_LOCAL_OLLAMA": "KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA",
    "POLARIS_AUDIT_LLM_ALLOW_REMOTE_FALLBACK": "KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK",
    "POLARIS_AUDIT_LLM_ROLE": "KERNELONE_AUDIT_LLM_ROLE",
    "POLARIS_AUDIT_LLM_TIMEOUT": "KERNELONE_AUDIT_LLM_TIMEOUT",
    "POLARIS_PROTOCOL_AUDIT": "KERNELONE_PROTOCOL_AUDIT",
    "POLARIS_REQUIRE_SIGNED_TOOL_TAGS": "KERNELONE_REQUIRE_SIGNED_TOOL_TAGS",
    # ─── Events / dedup ─────────────────────────────────────────────────────
    "POLARIS_RUNTIME_EVENT_DEDUP_WINDOW_SEC": "KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC",
    "POLARIS_LLM_EVENT_DEDUP_WINDOW_SEC": "KERNELONE_LLM_EVENT_DEDUP_WINDOW_SEC",
    # ─── Bus / messaging ────────────────────────────────────────────────────
    "POLARIS_MESSAGE_HANDLER_TIMEOUT_SECONDS": "KERNELONE_MESSAGE_HANDLER_TIMEOUT_SECONDS",
    # ─── Embedding ──────────────────────────────────────────────────────────
    "POLARIS_EMBEDDING_MODEL": "KERNELONE_EMBEDDING_MODEL",
    # ─── Trace / context ────────────────────────────────────────────────────
    "POLARIS_TRACE_ID": "KERNELONE_TRACE_ID",
    "POLARIS_RUN_ID": "KERNELONE_RUN_ID",
    "POLARIS_REQUEST_ID": "KERNELONE_REQUEST_ID",
    "POLARIS_WORKFLOW_ID": "KERNELONE_WORKFLOW_ID",
    "POLARIS_TASK_ID": "KERNELONE_TASK_ID",
    # ─── Token / auth ───────────────────────────────────────────────────────
    "POLARIS_TOKEN": "KERNELONE_TOKEN",
    "POLARIS_TOKEN_BUDGET": "KERNELONE_TOKEN_BUDGET",
    # ─── Self-upgrade ───────────────────────────────────────────────────────
    "POLARIS_SELF_UPGRADE_MODE": "KERNELONE_SELF_UPGRADE_MODE",
    # ─── Timeout ────────────────────────────────────────────────────────────
    "POLARIS_TIMEOUT": "KERNELONE_TIMEOUT",
    # ─── Version ────────────────────────────────────────────────────────────
    "POLARIS_VERSION": "KERNELONE_VERSION",
}


def normalize_env_prefix() -> None:
    """Map legacy ``POLARIS_*`` env vars to ``KERNELONE_*`` before kernel import.

    This function is idempotent: calling it multiple times is safe.
    It only copies values when the canonical ``KERNELONE_*`` key is **not**
    already present, ensuring explicit ``KERNELONE_*`` settings always win.
    """
    mapped: list[str] = []
    for polaris_key, kernelone_key in _POLARIS_TO_KERNELONE.items():
        polaris_value = os.environ.get(polaris_key)
        if polaris_value is None:
            continue
        kernelone_value = os.environ.get(kernelone_key)
        if kernelone_value is not None:
            # Canonical key already set; POLARIS_* value is ignored.
            continue
        os.environ[kernelone_key] = polaris_value
        mapped.append(polaris_key)

    if mapped:
        # Prominent console notice for operators (not just a Python warning).
        # This prints directly to stderr so it is visible even when logging
        # is not yet configured.
        import sys

        _msg = (
            "[ENV COMPAT] The following POLARIS_* environment variables are deprecated "
            "and will be removed in a future release. Please migrate to KERNELONE_*:\n"
        )
        for key in mapped:
            _msg += f"  {key}  ->  {_POLARIS_TO_KERNELONE[key]}\n"
        _msg += "See: https://github.com/anthropics/polaris/blob/main/docs/env-migration.md\n"
        print(_msg, file=sys.stderr)

        # Also emit standard Python DeprecationWarning for test frameworks
        # and IDE inspections.
        warnings.warn(
            f"POLARIS_* env vars are deprecated ({', '.join(mapped)}). "
            "Migrate to KERNELONE_* equivalents. "
            "POLARIS_* support will be removed in v2.0.",
            DeprecationWarning,
            stacklevel=2,
        )
