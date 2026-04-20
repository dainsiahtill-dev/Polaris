"""Context engine utilities.

Architecture note (P1-CTX-006 convergence):
    Token estimation uses polaris.kernelone.llm.engine.token_estimator.
    This module's _estimate_tokens delegates to it for consistency.
"""

import hashlib
import json
import logging
import os
from typing import Any

from polaris.kernelone.fs.text_ops import read_file_safe
from polaris.kernelone.llm.engine.token_estimator import estimate_tokens as _canonical_estimate_tokens

_logger = logging.getLogger(__name__)


def _hash_text(text: str) -> str:
    hasher = hashlib.sha1()
    hasher.update((text or "").encode("utf-8", errors="ignore"))
    return hasher.hexdigest()


def _estimate_tokens(text: str) -> int:
    """Estimate token count for text (P1-CTX-006 convergence: delegates to canonical)."""
    if not text:
        return 0
    return _canonical_estimate_tokens(text)


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (RuntimeError, ValueError) as exc:
        _logger.warning("kernelone.context.engine.utils.safe_json failed: %s", exc, exc_info=True)
        return "{}"


def _read_tail_lines(path: str, max_lines: int = 200) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            block = 4096
            data = b""
            while pos > 0 and data.count(b"\n") <= max_lines:
                read_size = block if pos >= block else pos
                pos -= read_size
                handle.seek(pos)
                data = handle.read(read_size) + data
    except (RuntimeError, ValueError) as exc:
        _logger.warning(
            "kernelone.context.engine.utils.read_tail_lines failed for %s: %s",
            path,
            exc,
            exc_info=True,
        )
        return []
    text = data.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        return lines[-max_lines:]
    return lines


def _read_slice_spec(path: str, spec: dict[str, Any]) -> tuple[str, list[int], str]:
    content = read_file_safe(path)
    if not content:
        return "", [0, 0], ""
    file_hash = _hash_text(content)
    lines = content.splitlines()
    start_line = 1
    end_line = len(lines)
    around = spec.get("around")
    radius = spec.get("radius")
    if isinstance(around, int):
        rad = int(radius or 80)
        start_line = max(1, around - rad)
        end_line = min(len(lines), around + rad)
    else:
        start_line = int(spec.get("start_line") or spec.get("line_start") or 1)
        end_line = int(spec.get("end_line") or spec.get("line_end") or len(lines))
        start_line = max(1, start_line)
        end_line = min(len(lines), end_line)
    sliced = lines[start_line - 1 : end_line]
    return "\n".join(sliced), [start_line, end_line], file_hash
