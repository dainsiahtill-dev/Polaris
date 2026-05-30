from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_FENCE = ("```", "```")


def _clean_filename(raw: str) -> str:
    name = raw.strip()
    name = name.strip("*").rstrip(":").strip("`").lstrip("#").strip()
    if len(name) > 250:
        return ""
    return name


def _is_plausible_filename(candidate: str, chat_files: Sequence[str]) -> bool:
    if not candidate:
        return False
    if candidate in chat_files or Path(candidate).name in chat_files:
        return True
    if any(marker in candidate for marker in ("{", "}", "<", ">", "|")):
        return False
    if candidate.startswith(("-", "#", "//", "/*", "*")):
        return False
    path = Path(candidate)
    return bool(path.suffix or "/" in candidate or "\\" in candidate)


def _extract_fence_filename(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith(DEFAULT_FENCE[0]):
        return ""
    info = stripped[len(DEFAULT_FENCE[0]) :].strip()
    if not info:
        return ""
    lowered = info.lower()
    if lowered.startswith("file:"):
        return _clean_filename(info.split(":", 1)[1])
    if lowered.startswith("path="):
        return _clean_filename(info.split("=", 1)[1])
    return ""


def extract_wholefile_blocks(
    content: str,
    *,
    inchat_files: Sequence[str],
    fence: tuple[str, str] = DEFAULT_FENCE,
) -> list[tuple[str, str]]:
    """Extract whole-file blocks as (path, full_content)."""
    if not content.strip():
        return []

    lines = content.splitlines(keepends=True)
    chat_files = list(inchat_files)
    edits: list[tuple[str, str, str]] = []  # (path, source, content)

    saw_name: str | None = None
    file_name: str | None = None
    file_source: str | None = None
    block_lines: list[str] = []

    for i, line in enumerate(lines):
        if line.startswith(fence[0]) or line.startswith(fence[1]):
            if file_name is not None:
                edits.append((file_name, file_source or "unknown", "".join(block_lines)))
                saw_name = None
                file_name = None
                file_source = None
                block_lines = []
                continue

            fenced_name = _extract_fence_filename(line)
            if fenced_name and _is_plausible_filename(fenced_name, chat_files):
                file_name = fenced_name
                file_source = "block"
                continue

            if i > 0:
                candidate = _clean_filename(lines[i - 1])
                if candidate and candidate not in chat_files and Path(candidate).name in chat_files:
                    candidate = Path(candidate).name
                if candidate and _is_plausible_filename(candidate, chat_files):
                    file_name = candidate
                    file_source = "block"

            if not file_name:
                if saw_name:
                    file_name = saw_name
                    file_source = "saw"
                elif len(chat_files) == 1:
                    file_name = chat_files[0]
                    file_source = "chat"
            continue

        if file_name is not None:
            block_lines.append(line)
            continue

        for word in line.strip().split():
            token = word.rstrip(".:,;!")
            for chat_file in chat_files:
                if token == f"`{chat_file}`":
                    saw_name = chat_file

    if file_name is not None:
        edits.append((file_name, file_source or "unknown", "".join(block_lines)))

    refined: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in ("block", "saw", "chat"):
        for name, src, body in edits:
            if src != source or name in seen:
                continue
            seen.add(name)
            refined.append((name, body))

    return refined
