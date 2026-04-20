"""Memory snapshot utilities for Harborpilot Loop."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from polaris.kernelone.fs.text_ops import _decode_text_bytes, write_json_atomic

logger = logging.getLogger(__name__)


def ensure_memory_dir(path: str) -> None:
    """Ensure memory directory exists (creates the directory itself, not just parent)."""
    if path:
        os.makedirs(path, exist_ok=True)


def read_memory_snapshot(path: str) -> dict[str, Any] | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        text = _decode_text_bytes(data)
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("read_memory_snapshot failed: path=%s error=%s", path, exc)
        return None


def write_memory_snapshot(path: str, data: dict[str, Any]) -> None:
    if not path:
        return
    try:
        write_json_atomic(path, data)
    except (RuntimeError, ValueError, OSError) as e:
        logger.debug("Failed to write memory snapshot: path=%s error=%s", path, e)


def get_memory_summary(snapshot: dict[str, Any] | None, max_chars: int) -> str:
    if not snapshot:
        return "none"
    lines = []
    if snapshot.get("last_run_at"):
        lines.append(f"- last_run_at: {snapshot['last_run_at']}")
    if snapshot.get("last_summary"):
        lines.append(f"- last_summary: {snapshot['last_summary']}")
    if snapshot.get("last_next_step"):
        lines.append(f"- last_next_step: {snapshot['last_next_step']}")
    if snapshot.get("last_log_path"):
        lines.append(f"- last_log_path: {snapshot['last_log_path']}")
    text = "\n".join(lines)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def write_loop_warning(log_path: str, message: str) -> None:
    if log_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"[WARN] {message}\n")
        except OSError as exc:
            logger.debug("write_loop_warning failed: path=%s error=%s", log_path, exc)
    # Always emit to stdout so the warning is visible even without a configured
    # log handler (e.g. bare script context or early-bootstrap failures).
    print(f"WARNING: {message}", flush=True)
    logger.warning("WARNING: %s", message)
