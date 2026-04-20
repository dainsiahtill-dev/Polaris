"""Helper utilities for orchestration module."""

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Environment variable constants
_DOCS_INIT_MODE_ENV = "POLARIS_DOCS_INIT_MODE"
_DOCS_INIT_MODES = {"auto", "strict"}
_DEFAULT_DOCS_INIT_MODE = "auto"
_PM_DOC_STAGE_MODE_ENV = "POLARIS_PM_DOC_STAGE_MODE"
_PM_DOC_STAGE_MODES = {"auto", "on", "off"}

# Path constants
_ARCHITECT_READY_REL = "runtime/state/architect.ready.json"
_ARCHITECT_DOCS_PIPELINE_REL = "runtime/contracts/architect.docs_pipeline.json"
_PM_DOCS_PROGRESS_REL = "runtime/state/pm.docs_progress.json"
_ZHONGSHU_BLUEPRINTS_ROOT_REL = "workspace/blueprints"
_ZHONGSHU_BLUEPRINTS_MANIFEST_REL = "workspace/blueprints/manifest.json"


def _resolve_docs_init_mode() -> str:
    """Resolve docs initialization mode from environment."""
    raw = str(os.environ.get(_DOCS_INIT_MODE_ENV, _DEFAULT_DOCS_INIT_MODE) or _DEFAULT_DOCS_INIT_MODE).strip().lower()
    if raw not in _DOCS_INIT_MODES:
        return _DEFAULT_DOCS_INIT_MODE
    return raw


def _resolve_pm_doc_stage_mode() -> str:
    """Resolve PM doc staging mode from environment."""
    raw = str(os.environ.get(_PM_DOC_STAGE_MODE_ENV, "auto") or "auto").strip().lower()
    if raw not in _PM_DOC_STAGE_MODES:
        return "auto"
    return raw


def _safe_int(value: Any, *, default: int = 0) -> int:
    """Safely parse integer from any value."""
    try:
        return int(value)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse int from %r, using default %d: %s",
            value,
            default,
            exc,
        )
        return default


def _read_text_utf8(path: str) -> str:
    """Read text file with UTF-8 encoding."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to read text from path %r: %s", path, exc)
        return ""


def _normalize_directive_text(text: str, *, max_chars: int) -> str:
    """Normalize directive text to maximum character length."""
    value = str(text or "")
    if max_chars > 0 and len(value) > max_chars:
        return value[:max_chars]
    return value


def _load_cli_directive(args: Any) -> str:
    """Load CLI directive from args (inline, file, or stdin)."""
    inline = str(getattr(args, "directive", "") or "")
    file_path = str(getattr(args, "directive_file", "") or "").strip()
    from_stdin = bool(getattr(args, "directive_stdin", False))
    max_chars_raw = getattr(args, "directive_max_chars", 200000)
    try:
        max_chars = int(max_chars_raw)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse directive_max_chars from %r, using default 200000: %s",
            max_chars_raw,
            exc,
        )
        max_chars = 200000
    max_chars = max(0, max_chars)

    if from_stdin:
        try:
            stdin_text = sys.stdin.read()
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to read directive from stdin: %s", exc)
            stdin_text = ""
        return _normalize_directive_text(stdin_text, max_chars=max_chars).strip()

    if file_path:
        return _normalize_directive_text(_read_text_utf8(file_path), max_chars=max_chars).strip()

    return _normalize_directive_text(inline, max_chars=max_chars).strip()


def _role_llm_docs_enabled() -> bool:
    """Check if role LLM docs generation is enabled."""
    token = str(os.environ.get("POLARIS_ARCHITECT_LLM_DOCS_MODE", "on") or "on").strip().lower()
    return token not in {"0", "false", "no", "off"}


def _role_llm_docs_required() -> bool:
    """Check if role LLM docs generation is required."""
    token = str(os.environ.get("POLARIS_ARCHITECT_LLM_DOCS_REQUIRED", "on") or "on").strip().lower()
    return token in {"1", "true", "yes", "on", "required", "strict"}


def _role_llm_fields_enabled() -> bool:
    """Check if role LLM fields generation is enabled."""
    token = str(os.environ.get("POLARIS_ARCHITECT_FIELDS_LLM_MODE", "on") or "on").strip().lower()
    return token not in {"0", "false", "no", "off"}


__all__ = [
    "_ARCHITECT_DOCS_PIPELINE_REL",
    # Constants
    "_ARCHITECT_READY_REL",
    "_PM_DOCS_PROGRESS_REL",
    "_ZHONGSHU_BLUEPRINTS_MANIFEST_REL",
    "_ZHONGSHU_BLUEPRINTS_ROOT_REL",
    "_load_cli_directive",
    "_normalize_directive_text",
    "_read_text_utf8",
    # Environment mode functions
    "_resolve_docs_init_mode",
    "_resolve_pm_doc_stage_mode",
    # Role LLM mode functions
    "_role_llm_docs_enabled",
    "_role_llm_docs_required",
    "_role_llm_fields_enabled",
    # Utility functions
    "_safe_int",
]
