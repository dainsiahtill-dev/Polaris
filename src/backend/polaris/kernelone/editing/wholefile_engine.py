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

            if i > 0:
                candidate = _clean_filename(lines[i - 1])
                if candidate and candidate not in chat_files and Path(candidate).name in chat_files:
                    candidate = Path(candidate).name
                if candidate:
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
