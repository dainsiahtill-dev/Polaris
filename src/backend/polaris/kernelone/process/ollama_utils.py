from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from polaris.kernelone.llm.embedding import get_default_embedding_port
from polaris.kernelone.runtime.usage_metrics import TokenUsage, UsageContext, track_usage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants — single source of truth for all magic strings
# ---------------------------------------------------------------------------

_ENV_OLLAMA_HOST = "OLLAMA_HOST"
_DEFAULT_OLLAMA_HOST = "http://120.24.117.59:11434"
_DEFAULT_GENERATE_TIMEOUT_SECONDS = 300
_DEFAULT_EMBED_TIMEOUT_SECONDS = 30
_DEFAULT_EMBED_MODEL = "nomic-embed-text"

# ---------------------------------------------------------------------------
# ANSI / terminal cleaning — compiled once at import time
# ---------------------------------------------------------------------------

ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\][^\x1b\x07]*(?:\x07|\x1b\\)")
ANSI_OTHER_RE = re.compile(r"\x1b[@-_][0-?]*[ -/]*[@-~]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPINNER_ONLY_RE = re.compile(r"^[\s\u2800-\u28ff]+$")


def clean_terminal_output(text: str) -> str:
    """Strip ANSI escape sequences and non-printable control characters."""
    if not text:
        return text
    cleaned = text.replace("\r", "\n")
    cleaned = ANSI_OSC_RE.sub("", cleaned)
    cleaned = ANSI_CSI_RE.sub("", cleaned)
    cleaned = ANSI_OTHER_RE.sub("", cleaned)
    cleaned = CONTROL_RE.sub("", cleaned)
    return cleaned


def is_spinner_only(text: str) -> bool:
    """Return True when the text consists solely of spinner/braille noise."""
    if not text:
        return True
    return SPINNER_ONLY_RE.match(text.strip()) is not None


# ---------------------------------------------------------------------------
# Adapter Protocol and singleton registry
# ---------------------------------------------------------------------------


class KernelOllamaAdapter(Protocol):
    """Kernel port for Ollama HTTP calls implemented in infrastructure."""

    def generate(
        self,
        *,
        prompt: str,
        model: str,
        timeout_seconds: int,
        host: str,
    ) -> dict[str, Any]: ...

    def embed(
        self,
        *,
        text: str,
        model: str,
        timeout_seconds: int,
        host: str,
    ) -> list[float]: ...


_default_ollama_adapter: KernelOllamaAdapter | None = None
_default_ollama_adapter_lock = threading.RLock()


def set_default_ollama_adapter(adapter: KernelOllamaAdapter | None) -> None:
    """Register infrastructure adapter for Ollama network operations."""
    global _default_ollama_adapter
    with _default_ollama_adapter_lock:
        _default_ollama_adapter = adapter


def get_default_ollama_adapter() -> KernelOllamaAdapter:
    """Return registered infrastructure adapter; raise if not configured."""
    with _default_ollama_adapter_lock:
        if _default_ollama_adapter is None:
            raise RuntimeError(
                "Default KernelOllamaAdapter not configured. It must be injected by the bootstrap layer before use."
            )
        return _default_ollama_adapter


# ---------------------------------------------------------------------------
# Internal data structures (not API boundary contracts)
# ---------------------------------------------------------------------------


@dataclass
class OllamaMetadata:
    """Internal metadata for Ollama API responses.

    This is an internal data structure, NOT an API boundary contract.
    Using dataclass instead of TypedDict for proper default factory support.
    """

    done: bool = False
    done_reason: str | None = None
    prompt_eval_count: int = 0
    eval_count: int = 0
    truncated: bool = False
    finish_reason: str | None = None
    error: str = ""
    error_type: str = ""


@dataclass
class OllamaResponse:
    """Response from Ollama API with metadata."""

    output: str
    metadata: OllamaMetadata = field(default_factory=OllamaMetadata)

    def __str__(self) -> str:
        """Compatibility: return output as string for existing code."""
        return self.output


# ---------------------------------------------------------------------------
# Private pure-function helpers — no I/O, fully unit-testable without mocks
# ---------------------------------------------------------------------------


def _resolve_ollama_host() -> str:
    """Read Ollama host from environment; return documented default if absent."""
    return os.environ.get(_ENV_OLLAMA_HOST, _DEFAULT_OLLAMA_HOST)


def _resolve_generate_timeout(timeout: int) -> int:
    """Normalise caller-supplied timeout to a positive integer in seconds."""
    return int(timeout) if timeout > 0 else _DEFAULT_GENERATE_TIMEOUT_SECONDS


def _resolve_embed_timeout(timeout: int) -> int:
    """Normalise caller-supplied embedding timeout; enforce minimum of 1 s."""
    return max(1, int(timeout) if timeout > 0 else _DEFAULT_EMBED_TIMEOUT_SECONDS)


def _build_response_metadata(
    result: dict[str, Any],
    *,
    truncated: bool,
) -> OllamaMetadata:
    """Construct OllamaMetadata from a raw Ollama generate API response dict.

    Pure function: no I/O, no side effects.
    """
    done_reason: str | None = str(result.get("done_reason") or "").strip() or None
    return OllamaMetadata(
        done=bool(result.get("done", False)),
        done_reason=done_reason,
        prompt_eval_count=int(result.get("prompt_eval_count", 0) or 0),
        eval_count=int(result.get("eval_count", 0) or 0),
        truncated=truncated,
        finish_reason=done_reason,
    )


def _build_token_usage(
    result: dict[str, Any],
    *,
    prompt: str,
    content: str,
    ok: bool,
) -> TokenUsage:
    """Construct TokenUsage from raw Ollama result and call context.

    Pure function: no I/O, no side effects.
    """
    p_tokens = int(result.get("prompt_eval_count", 0) or 0)
    c_tokens = int(result.get("eval_count", 0) or 0)
    return TokenUsage(
        prompt_tokens=p_tokens,
        completion_tokens=c_tokens,
        total_tokens=p_tokens + c_tokens,
        estimated=not ok,
        prompt_chars=len(prompt),
        completion_chars=len(content),
    )


def _classify_ollama_error(exc: Exception) -> str:
    """Classify an Ollama exception for structured logging and metadata.

    Returns one of: "timeout", "connection", "model_not_found", "unknown".
    Pure function: no I/O.
    """
    exc_type = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in exc_type or "timeout" in msg:
        return "timeout"
    if "connection" in msg or "refused" in msg or "connect" in exc_type:
        return "connection"
    if "not found" in msg and "model" in msg:
        return "model_not_found"
    return "unknown"


def _maybe_track_usage(
    events_path: str,
    usage_ctx: UsageContext | None,
    model: str,
    usage: TokenUsage,
    duration_ms: int,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    """Emit a usage tracking event when both usage_ctx and events_path are set."""
    if usage_ctx and events_path:
        track_usage(
            events_path,
            usage_ctx,
            model,
            "ollama",
            usage,
            duration_ms,
            ok=ok,
            error=error,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def invoke_ollama(
    prompt: str,
    model: str,
    workspace: str = "",  # reserved: future per-workspace host/config override
    show_output: bool = False,
    timeout: int = 0,
    usage_ctx: UsageContext | None = None,
    events_path: str = "",
) -> OllamaResponse:
    """Call Ollama generate via the injected infrastructure adapter.

    Returns an OllamaResponse with the model's text in `output` and rich
    metadata in `metadata`.  Never raises: failures are reported via
    ``metadata["error"]`` and ``metadata["error_type"]`` so callers can
    handle them uniformly.

    Args:
        prompt: The text prompt to send.
        model: Ollama model identifier (e.g. "llama3").
        workspace: Reserved for future per-workspace configuration; currently
            unused but retained for call-site compatibility.
        show_output: When True, the response content is also emitted as an
            INFO log entry (useful for interactive CLI contexts).
        timeout: Request timeout in seconds; 0 uses the module default (300 s).
        usage_ctx: If provided together with `events_path`, a token-usage
            event is emitted after each call.
        events_path: Filesystem path for the events log; required for usage
            tracking.
    """
    _ = workspace  # intentionally unused; reserved for per-workspace config

    host = _resolve_ollama_host()
    timeout_seconds = _resolve_generate_timeout(timeout)
    model_name = str(model or "").strip()
    prompt_str = str(prompt or "")

    start_time = time.monotonic()
    try:
        result = get_default_ollama_adapter().generate(
            prompt=prompt_str,
            model=model_name,
            timeout_seconds=timeout_seconds,
            host=host,
        )

        content = str(result.get("response", "") or "")
        duration_ms = int((time.monotonic() - start_time) * 1000)

        done = bool(result.get("done", False))
        truncated = bool(not done and result.get("done_reason") is None)
        metadata = _build_response_metadata(result, truncated=truncated)
        usage = _build_token_usage(result, prompt=prompt_str, content=content, ok=True)
        _maybe_track_usage(events_path, usage_ctx, model, usage, duration_ms, ok=True)

        if show_output:
            logger.info("%s", content)

        return OllamaResponse(output=content, metadata=metadata)

    except (RuntimeError, ValueError) as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        err_kind = _classify_ollama_error(exc)
        log_level = logging.WARNING if err_kind == "timeout" else logging.ERROR
        logger.log(
            log_level,
            "Kernel Ollama generate failed: model=%s host=%s error_type=%s error=%s",
            model_name or "<default>",
            host,
            err_kind,
            exc,
        )
        empty_usage = _build_token_usage({}, prompt=prompt_str, content="", ok=False)
        _maybe_track_usage(
            events_path,
            usage_ctx,
            model,
            empty_usage,
            duration_ms,
            ok=False,
            error=str(exc),
        )
        return OllamaResponse(
            output="",
            metadata=OllamaMetadata(
                done=True,
                done_reason="error",
                truncated=False,
                error=str(exc),
                error_type=err_kind,
            ),
        )


def get_embedding(text: str, model: str, timeout: int = 30) -> list[float]:
    """Return a vector embedding via the injected adapter with fallback.

    Tries the registered KernelOllamaAdapter first; on failure falls back to
    the default KernelEmbeddingPort.  Returns an empty list when both paths
    fail, so callers can treat the result as optional.

    Args:
        text: The text to embed.
        model: Embedding model identifier.
        timeout: Request timeout in seconds; enforced to at least 1 s.
    """
    token = str(text or "").strip()
    if not token:
        return []

    host = _resolve_ollama_host()
    timeout_seconds = _resolve_embed_timeout(timeout)
    model_name = str(model or "").strip() or _DEFAULT_EMBED_MODEL

    try:
        return list(
            get_default_ollama_adapter().embed(
                text=token,
                model=model_name,
                timeout_seconds=timeout_seconds,
                host=host,
            )
            or []
        )
    except (RuntimeError, ValueError) as exc:
        err_kind = _classify_ollama_error(exc)
        log_level = logging.WARNING if err_kind == "timeout" else logging.ERROR
        logger.log(
            log_level,
            "Kernel Ollama embed failed, falling back to embedding port: model=%s host=%s error_type=%s error=%s",
            model_name,
            host,
            err_kind,
            exc,
        )

    try:
        return list(
            get_default_embedding_port().get_embedding(
                token,
                model=str(model or "").strip() or None,
            )
            or []
        )
    except (RuntimeError, ValueError) as fallback_exc:
        logger.error(
            "Kernel embedding fallback failed: model=%s error=%s",
            model_name,
            fallback_exc,
        )
        return []


__all__ = [
    "KernelOllamaAdapter",
    "OllamaMetadata",
    "OllamaResponse",
    "clean_terminal_output",
    "get_default_ollama_adapter",
    "get_embedding",
    "invoke_ollama",
    "is_spinner_only",
    "set_default_ollama_adapter",
]
