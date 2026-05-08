"""Text and JSON file utilities for polaris Loop."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from typing import Any, TextIO

from polaris.kernelone.constants import BAD_CHAR_THRESHOLD, DEFAULT_LOCK_TIMEOUT_SECONDS

from .fsync_mode import is_fsync_enabled
from .jsonl.locking import acquire_lock_fd, release_lock_fd

logger = logging.getLogger(__name__)

_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS = (0.025, 0.05, 0.1, 0.2)


def _require_utf8_encoding(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized != "utf-8":
        raise ValueError("write_text_atomic only allows UTF-8 text encoding")
    return "utf-8"


def _fsync_enabled() -> bool:
    return is_fsync_enabled()


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _fsync_parent_dir(path: str) -> None:
    if not _fsync_enabled():
        return
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(parent, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("Parent directory fsync skipped: path=%s error=%s", path, exc)
    finally:
        os.close(fd)


def write_text_atomic(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    lock_timeout_sec: float | None = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    if not path:
        return
    encoding = _require_utf8_encoding(encoding)
    ensure_parent_dir(path)
    lock_path = f"{path}.lock"
    fd = None
    if lock_timeout_sec is not None:
        fd = acquire_lock_fd(lock_path, timeout_sec=lock_timeout_sec)
        if fd is None:
            raise TimeoutError(f"Failed to acquire file lock for {path}")
    tmp_path = ""
    try:
        parent = os.path.dirname(path) or "."
        prefix = f".{os.path.basename(path)}."
        handle_fd, tmp_path = tempfile.mkstemp(
            prefix=prefix,
            suffix=".tmp",
            dir=parent,
            text=True,
        )
        with os.fdopen(handle_fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text or "")
            handle.flush()
            if _fsync_enabled():
                os.fsync(handle.fileno())
        replace_attempts = len(_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS) + 1
        for attempt in range(replace_attempts):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt >= replace_attempts - 1:
                    raise
                time.sleep(_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS[attempt])
        _fsync_parent_dir(path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as exc:
                logger.debug("Temporary file cleanup skipped: path=%s error=%s", tmp_path, exc)
        if fd is not None:
            release_lock_fd(fd, lock_path)


def append_text_atomic(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    lock_timeout_sec: float | None = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    """Append text with a path lock and explicit fsync semantics."""
    if not path:
        return
    encoding = _require_utf8_encoding(encoding)
    ensure_parent_dir(path)
    lock_path = f"{path}.lock"
    fd = None
    if lock_timeout_sec is not None:
        fd = acquire_lock_fd(lock_path, timeout_sec=lock_timeout_sec)
        if fd is None:
            raise TimeoutError(f"Failed to acquire file lock for {path}")
    try:
        with open(path, "a", encoding=encoding, newline="\n") as handle:
            handle.write(text or "")
            handle.flush()
            if _fsync_enabled():
                os.fsync(handle.fileno())
        _fsync_parent_dir(path)
    finally:
        if fd is not None:
            release_lock_fd(fd, lock_path)


def write_json_atomic(
    path: str,
    data: dict[str, Any],
    *,
    lock_timeout_sec: float | None = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    write_text_atomic(path, payload + "\n", lock_timeout_sec=lock_timeout_sec)


def open_text_log_append(path: str, *, newline: str | None = "\n") -> TextIO:
    """Open a UTF-8 append-only text log handle after ensuring parent directory exists."""
    ensure_parent_dir(path)
    return open(path, "a", encoding="utf-8", errors="ignore", newline=newline)


def is_run_artifact(rel_path: str) -> bool:
    lowered = rel_path.lower().replace("\\", "/")
    if lowered.endswith("director_result.json") or lowered.endswith("director.result.json"):
        return True
    if lowered.endswith("events.jsonl") or lowered.endswith("runtime.events.jsonl"):
        return True
    if lowered.endswith("trajectory.json"):
        return True
    if lowered.endswith("qa_response.md") or lowered.endswith("qa.review.md"):
        return True
    if lowered.endswith("planner_response.md") or lowered.endswith("planner.output.md"):
        return True
    if lowered.endswith("ollama_response.md") or lowered.endswith("director_llm.output.md"):
        return True
    if lowered.endswith("reviewer_response.md") or lowered.endswith("auditor.review.md"):
        return True
    return bool(lowered.endswith("runlog.md") or lowered.endswith("director.runlog.md"))


def _decode_text_bytes(data: bytes) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("utf-8", errors="replace")
    except LookupError:
        text = ""
    if text:
        bad = text.count("\ufffd")
        if bad / max(len(text), 1) < BAD_CHAR_THRESHOLD:
            return text
    for enc in ("utf-8-sig", "gbk", "cp936"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def read_file_safe(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return _decode_text_bytes(data)
    except OSError as exc:
        logger.debug("read_file_safe failed: path=%s error=%s", path, exc)
        return ""


def extract_field(text: str, patterns: list[str]) -> str:
    if not text:
        return ""
    for pattern in patterns:
        try:
            match = re.search(pattern, text, flags=re.MULTILINE)
        except re.error:
            match = None
        if match:
            return match.group(1).strip()
    return ""
