"""Edit-block heuristics for filesystem handlers.

Sibling module for ``filesystem.py``: the weak-model edit-block affordances —
payload normalization, marker detection, whole-file placeholder/prefix
replacement detection, line-range -> SEARCH/REPLACE block synthesis, and the
JSON-in-blocks recognizer. Imports the leaf ``filesystem_guards`` (destructive
shrink) and ``filesystem_io`` (path resolution) siblings; neither imports this
module, so the handler import graph stays acyclic.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_guards import (
    _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO,
    _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES,
    _destructive_shrink_error,
)
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_io import (
    _not_found_error,
    _resolve_workspace_rel,
)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def _is_placeholder_search_text(search_text: str) -> bool:
    """Return true when SEARCH is a model shorthand for a placeholder file."""
    lines = [line.strip() for line in str(search_text or "").splitlines() if line.strip()]
    if not lines or len(lines) > 3:
        return False
    compact = " ".join(lines).lower()
    if len(compact) > 180:
        return False
    return bool(
        re.search(
            r"\b(todo|fixme|placeholder|not\s+implemented|implement(?:ation)?|stub|scaffold)\b",
            compact,
        )
    )


def _looks_like_complete_file_replacement(replace_text: str, rel: str) -> bool:
    """Conservatively detect a complete file body rather than a tiny fragment."""
    content = str(replace_text or "").strip()
    if len(content) < 200:
        return False
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    suffix = os.path.splitext(rel)[1].lower()
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return bool(
            re.search(
                r"^\s*(import|export|type|interface|class|function|const|let|var)\b",
                content,
                flags=re.MULTILINE,
            )
        )
    if suffix in {".py", ".pyw"}:
        return bool(re.search(r"^\s*(from|import|class|def)\b", content, flags=re.MULTILINE))
    return True


def _should_use_whole_file_placeholder_replacement(
    *,
    search_text: str,
    replace_text: str,
    rel: str,
    block_count: int,
) -> bool:
    """Allow a controlled whole-file replacement for common LLM edit-block shorthand."""
    return (
        block_count == 1
        and _is_placeholder_search_text(search_text)
        and _looks_like_complete_file_replacement(replace_text, rel)
    )


def _normalize_edit_block_text(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _drop_final_content_line(text: str) -> str:
    candidate = text[:-1] if text.endswith("\n") else text
    if "\n" not in candidate:
        return ""
    head, tail = candidate.rsplit("\n", 1)
    if not tail.strip():
        return ""
    return f"{head}\n"


def _prefix_search_candidates(search_text: str) -> list[str]:
    normalized = _normalize_edit_block_text(search_text)
    variants = [normalized]
    marker_index = normalized.find("[truncated]")
    if marker_index >= 0:
        variants.append(normalized[:marker_index])

    candidates: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        for candidate in (variant, _drop_final_content_line(variant)):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def _has_sufficient_whole_file_prefix_evidence(prefix: str) -> bool:
    stripped = prefix.strip()
    if len(stripped) < 200:
        return False
    non_empty_lines = [line for line in prefix.splitlines() if line.strip()]
    return len(non_empty_lines) >= 8


def _should_use_whole_file_prefix_replacement(
    *,
    current_text: str,
    search_text: str,
    replace_text: str,
    rel: str,
    block_count: int,
) -> bool:
    """Allow whole-file replacement when SEARCH is a verified file-prefix snapshot."""
    suffix = os.path.splitext(rel)[1].lower()
    if suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".pyw"}:
        return False
    if block_count != 1 or not _looks_like_complete_file_replacement(replace_text, rel):
        return False

    current = _normalize_edit_block_text(current_text).lstrip("﻿")
    for candidate in _prefix_search_candidates(search_text):
        prefix = candidate.lstrip("﻿")
        if _has_sufficient_whole_file_prefix_evidence(prefix) and current.startswith(prefix):
            return True
    return False


def _normalize_block_input(text: Any) -> str:
    """Make weak-model edit payloads parseable.

    Two common low-precision-model artifacts are repaired:
    - the whole payload wrapped in a Markdown code fence (```/```python);
    - real newlines collapsed into literal ``\\n``/``\\t`` escapes (single-line JSON).
    """
    if not isinstance(text, str):
        return ""
    s = text
    stripped = s.strip()
    if stripped.startswith("```"):
        fence_lines = stripped.splitlines()
        if fence_lines and fence_lines[0].lstrip().startswith("```"):
            fence_lines = fence_lines[1:]
        if fence_lines and fence_lines[-1].strip().startswith("```"):
            fence_lines = fence_lines[:-1]
        s = "\n".join(fence_lines)
    # Only unescape when the payload has no real newlines but does carry escaped ones —
    # never mangle a payload that already contains genuine newlines (e.g. literal "\n"
    # inside a string the model intends to write).
    if "\n" not in s and "\\n" in s:
        s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return s


def _has_search_replace_markers(text: str) -> bool:
    """True when the text already looks like SEARCH/REPLACE block(s)."""
    if not text:
        return False
    return any(re.match(r"^\s*<{3,}\s*(SEARCH|ORIGINAL|SOURCE)\b", line) for line in text.splitlines())


def _synthesize_blocks_from_update_markers(blocks_text: str, default_file: str | None) -> str | None:
    """Convert weak-model ``<<<<<<< UPDATE`` blocks into canonical SEARCH/REPLACE.

    Live factory-bench capture: qwen emitted a conflict-marker-like edit:
    ``<<<<<<< UPDATE file.py`` / ``=======`` / ``>>>>>>> UPDATE``. The search and
    replacement bodies are usable; only the marker names are wrong.
    """
    lines = str(blocks_text or "").splitlines(keepends=True)
    synthesized: list[str] = []
    index = 0
    saw_update = False
    while index < len(lines):
        header = lines[index].strip()
        match = re.match(r"^<{3,}\s*UPDATE(?:\s+(.+?))?\s*$", header)
        if not match:
            index += 1
            continue
        saw_update = True
        target_file = (match.group(1) or str(default_file or "")).strip()
        if not target_file:
            return None
        index += 1

        search_lines: list[str] = []
        while index < len(lines) and not re.match(r"^={4,}\s*$", lines[index].strip()):
            search_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            return None
        index += 1

        replace_lines: list[str] = []
        while index < len(lines) and not re.match(r"^>{3,}\s*UPDATE\s*$", lines[index].strip()):
            replace_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            return None
        index += 1

        synthesized.append(
            f"<<<< SEARCH:{target_file}\n{''.join(search_lines)}====\n{''.join(replace_lines)}>>>> REPLACE\n"
        )
    if not saw_update or not synthesized:
        return None
    return "".join(synthesized)


def _unwrap_weak_replace_marker(blocks_text: str, default_file: str | None) -> tuple[str | None, str | None]:
    """Extract whole-file replacement content from weak ``<<<<<<< REPLACE[:file]`` wrappers."""

    lines = str(blocks_text or "").splitlines(keepends=True)
    if not lines:
        return None, None
    header = lines[0].rstrip("\r\n")
    match = re.match(
        r"^<{3,}\s*REPLACE(?:\[\s*:?\s*([^\]]+)\]|\s+(\S+))?\s*(.*)$",
        header,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    target_file = (match.group(1) or match.group(2) or str(default_file or "")).strip()
    inline_body = str(match.group(3) or "")
    body_lines: list[str] = []
    if inline_body:
        body_lines.append(inline_body + "\n")
    body_lines.extend(lines[1:])
    while body_lines and re.match(r"^>{3,}\s*REPLACE\s*$", body_lines[-1].strip(), flags=re.IGNORECASE):
        body_lines.pop()
    replacement = "".join(body_lines)
    if not target_file or not replacement.strip():
        return None, None
    return target_file, replacement


def _coerce_line_no(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _synthesize_line_range_block(
    self: AgentAccelToolExecutor,
    file: str | None,
    start: Any,
    end: Any,
    replacement: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    """Build a SEARCH/REPLACE block from a line range (weak-model affordance).

    Low-precision models reliably express edits as "replace lines start..end of FILE
    with NEW CODE" but cannot reproduce exact SEARCH text. We read the EXACT current
    lines ourselves (so the SEARCH text is guaranteed to match) and hand the synthesized
    block to the normal validation/apply path. Returns (blocks_text, None) on success or
    (None, error_dict) otherwise.
    """
    if not file:
        return None, {"ok": False, "error": "line-range edit requires a 'file' argument."}
    rel, resolve_error = _resolve_workspace_rel(self, str(file))
    if rel is None:
        return None, (resolve_error or {"ok": False, "error": f"Invalid path: {file}"})
    if not self._kernel_fs.workspace_exists(rel):
        return None, _not_found_error(self, str(file))
    if not self._kernel_fs.workspace_is_file(rel):
        return None, {"ok": False, "error": f"Path is not a file: {file}"}
    try:
        content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, {"ok": False, "error": f"Failed to read {file}: {exc}"}

    lines = content.splitlines(keepends=True)
    total = len(lines)
    start_no = _coerce_line_no(start)
    end_no = _coerce_line_no(end)
    if start_no is None or end_no is None:
        return None, {"ok": False, "error": "line-range edit requires integer start and end line numbers."}
    if start_no < 1 or end_no < 1 or start_no > end_no or start_no > total:
        return None, {
            "ok": False,
            "error": f"Invalid line range [{start_no},{end_no}] for {file} (file has {total} lines).",
        }
    end_no = min(end_no, total)
    search_text = "".join(lines[start_no - 1 : end_no])

    repl = "" if replacement is None else str(replacement)
    if not repl.strip():
        return None, {
            "ok": False,
            "error": (
                f"line-range edit for {file}[{start_no}:{end_no}] has no replacement code. "
                "Provide the new code for those lines via 'replace' (or 'new_text'/'content')."
            ),
            "suggestion": "Pass the actual replacement source for the range; an empty replacement is rejected.",
        }
    # Preserve the trailing-newline shape of the replaced slice.
    if search_text.endswith("\n") and not repl.endswith("\n"):
        repl = repl + "\n"
    removed_lines = end_no - start_no + 1
    added_lines = len(repl.splitlines())
    if total > removed_lines and removed_lines <= 3 and _looks_like_complete_file_replacement(repl, rel):
        return None, {
            "ok": False,
            "error": (
                f"line-range edit for {file}[{start_no}:{end_no}] looks like a whole-file replacement, "
                f"but the selected range covers only {removed_lines} of {total} lines."
            ),
            "suggestion": (
                f"If replacing the full existing file, use start=1 and end={total}; otherwise set "
                "replace to only the new source for the selected line range. Use write_file when the "
                "intent is a whole-file overwrite."
            ),
            "error_type": "line_range_whole_file_mismatch",
            "retryable": True,
        }
    if (
        removed_lines >= _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES
        and added_lines <= removed_lines * _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO
    ):
        return None, _destructive_shrink_error(
            f"{file}[{start_no}:{end_no}]",
            removed_lines,
            added_lines,
            tool_hint=(
                "Narrow start/end to ONLY the lines you are actually changing (a bug fix is "
                "usually < 30 lines) and keep all surrounding code intact. Make several small "
                "line-range edits if multiple spots need changes."
            ),
        )
    # The block parser recognizes the divider/terminator only as a FULL line
    # (``^={4,9}\s*$`` / ``^>{4,9}\s*REPLACE``). When the replaced range includes
    # the file's LAST line and that line has no trailing newline, ``search_text``
    # (and the replacement body) do not end on their own line, so ``====`` / the
    # terminator get glued onto the final content line and the whole block is
    # silently dropped. Append a delimiter newline so each body ends on its own
    # line. This newline is a pure delimiter: ``_synthesize_line_range_search``
    # below strips it back off at apply time so the bytes matched against / written
    # to the file are unchanged.
    search_body = search_text if search_text.endswith("\n") else search_text + "\n"
    repl_body = repl if repl.endswith("\n") else repl + "\n"
    block = f"<<<< SEARCH:{file}\n{search_body}====\n{repl_body}>>>> REPLACE\n"
    return block, None


def _strip_eof_delimiter_newline(search_text: str, replace_text: str, content: str) -> tuple[str, str]:
    """Undo the delimiter newline a synthesized EOF block carries.

    ``_synthesize_line_range_block`` appends a trailing ``\\n`` to the SEARCH (and
    REPLACE) body so the ``====`` divider / ``>>>> REPLACE`` terminator land on
    their own line even when the replaced range ends at a file whose last line has
    no trailing newline. That newline is a pure parse delimiter, so it must be
    removed before matching/writing or the SEARCH would no longer match the on-disk
    slice (and a spurious trailing newline would be written). Trim a single trailing
    ``\\n`` from both bodies only when the SEARCH does not match the content as-is but
    its newline-trimmed form does — i.e. the genuine EOF case — leaving normal
    newline-terminated edits untouched.
    """
    if not search_text.endswith("\n"):
        return search_text, replace_text
    if search_text in content:
        return search_text, replace_text
    trimmed_search = search_text[:-1]
    if trimmed_search and trimmed_search in content:
        trimmed_replace = replace_text[:-1] if replace_text.endswith("\n") else replace_text
        return trimmed_search, trimmed_replace
    return search_text, replace_text


_JSON_EDIT_FILE_KEYS = (
    "file",
    "path",
    "file_path",
    "filepath",
    "filePath",
    "filename",
    "target_file",
    "target_path",
    "targetFile",
    "targetPath",
)
_JSON_EDIT_REPLACE_KEYS = (
    "replace",
    "new_text",
    "newText",
    "new_content",
    "newContent",
    "new_code",
    "newCode",
    "replacement",
    "replacement_text",
    "replacementText",
    "code",
    "content",
)


def _synthesize_whole_file_replacement_block(
    self: AgentAccelToolExecutor,
    file: str | None,
    replacement: Any,
    *,
    force: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    if not file:
        return None, None
    rel, resolve_error = _resolve_workspace_rel(self, str(file))
    if rel is None:
        return None, resolve_error or {"ok": False, "error": f"Invalid path: {file}"}
    if not self._kernel_fs.workspace_exists(rel):
        return None, None
    if not self._kernel_fs.workspace_is_file(rel):
        return None, {"ok": False, "error": f"Path is not a file: {file}"}
    repl = "" if replacement is None else str(replacement)
    if not force and not _looks_like_complete_file_replacement(repl, rel):
        return None, None
    try:
        content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, {"ok": False, "error": f"Failed to read {file}: {exc}"}
    total = len(content.splitlines(keepends=True))
    if total < 1:
        return None, None
    return _synthesize_line_range_block(self, file, 1, total, repl)


def _synthesize_blocks_from_json_payload(
    self: AgentAccelToolExecutor,
    blocks_text: str,
    default_file: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Recognize a JSON-encoded line-range edit (list or object) inside ``blocks``.

    Live capture (phase1smoke6, qwen3.6): the model emitted a fully-correct
    STRUCTURED edit as JSON inside the blocks argument —
    ``[{"start_line":1019,"end_line":1020,"file":"django/db/models/expressions.py",
    "replace":"..."}]`` — and the prose guard rejected it. Punishing structure
    is link-level self-harm: normalize it into synthesized SEARCH/REPLACE blocks
    through the SAME line-range path (shrink gate included).

    Returns ``(blocks, None)`` on success, ``(None, error)`` when the payload is
    line-range-shaped but invalid, and ``(None, None)`` when the payload is not
    JSON-edit shaped at all (caller falls through to the normal parser).
    """
    candidate = blocks_text.strip()
    # Live shape #4 (factory-bench L1-05): a leading YAML-ish label before the
    # JSON — 'blocks: [ {"path": ..., "start": 1, ...} ]'. Strip one leading
    # `word:` tag when JSON follows so the structured intent is recognized.
    label_match = re.match(r"^[A-Za-z_]{1,16}\s*:\s*(?=[\[{])", candidate)
    if label_match:
        candidate = candidate[label_match.end() :].strip()
    if not candidate or candidate[0] not in "[{":
        return None, None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None, None
    items: list[Any] = parsed if isinstance(parsed, list) else [parsed]
    if not items:
        return None, None
    synthesized: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            return None, None
        start = item.get("start", item.get("start_line"))
        end = item.get("end", item.get("end_line"))
        if start is None or end is None:
            # Nested-parameter-name shape (factory-bench L1-01 live capture):
            # [{"blocks": "<payload>"}] — the model wrapped the ARGUMENT NAME
            # inside the JSON. Unwrap single-item payloads and let the caller
            # re-enter the normal pipeline (marker parse / prose guard).
            inner = item.get("blocks")
            if len(items) == 1 and isinstance(inner, str) and inner.strip():
                return None, {"__unwrap_blocks__": inner, "__unwrap_file__": item.get("file") or default_file}
            item_file = next(
                (str(item[key]) for key in _JSON_EDIT_FILE_KEYS if item.get(key)),
                None,
            ) or (str(default_file) if default_file else None)
            replacement = next(
                (item[key] for key in _JSON_EDIT_REPLACE_KEYS if item.get(key) is not None),
                None,
            )
            block, err = _synthesize_whole_file_replacement_block(self, item_file, replacement)
            if err is not None:
                return None, err
            if block is not None:
                synthesized.append(block)
                continue
            return None, None
        item_file = next(
            (str(item[key]) for key in _JSON_EDIT_FILE_KEYS if item.get(key)),
            None,
        ) or (str(default_file) if default_file else None)
        replacement = next(
            (item[key] for key in _JSON_EDIT_REPLACE_KEYS if item.get(key) is not None),
            None,
        )
        block, err = _synthesize_line_range_block(self, item_file, start, end, replacement)
        if err is not None:
            return None, err
        synthesized.append(block or "")
    return "".join(synthesized), None
