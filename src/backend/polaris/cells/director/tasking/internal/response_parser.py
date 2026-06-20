"""Pure parsing helpers for Director worker LLM responses.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 1). Contains no ``self`` state and depends only on the standard library so
it can never participate in the lazy-import circular dependency dance documented
in ``worker_executor`` (it MUST NOT import ``code_generation_engine`` or
``file_apply_service`` at module top).

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import re


def extract_files_from_response(response: str) -> list[dict[str, str]]:
    """Extract file paths and contents from LLM response."""
    files: list[dict[str, str]] = []
    seen: set[str] = set()

    def _append_file(path: str, content: str) -> None:
        normalized_path = str(path or "").strip().strip("`")
        normalized_content = str(content or "").strip()
        if not normalized_path or not normalized_content:
            return
        if normalized_path in seen:
            return
        seen.add(normalized_path)
        files.append({"path": normalized_path, "content": normalized_content})

    # Pattern 1: explicit file fences
    for file_path, content in re.findall(r"```file:\s*(.+?)\n(.*?)```", response, re.DOTALL):
        _append_file(file_path, content)

    # Pattern 2: heading + code block
    for file_path, content in re.findall(
        r"(?:^|\n)(?:###|##|#)\s*`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)`?\s*\n```[^\n]*\n(.*?)```",
        response,
        re.DOTALL,
    ):
        _append_file(file_path, content)

    # Pattern 3: File: path followed by fenced code block
    for file_path, content in re.findall(
        r"(?:^|\n)(?:File|Path)\s*:\s*([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)\s*\n```[^\n]*\n(.*?)```",
        response,
        re.DOTALL,
    ):
        _append_file(file_path, content)

    return files
