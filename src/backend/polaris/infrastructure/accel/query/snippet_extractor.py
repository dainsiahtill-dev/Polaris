from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import MAX_FILE_SIZE_BYTES, MAX_SYNTAX_UNIT_LINES

if TYPE_CHECKING:
    from pathlib import Path

_TRUNCATION_MARKER = "\n...\n"


def _safe_int(value: Any, default_value: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default_value)


def _score_symbol_row(row: dict[str, Any], task_tokens: list[str]) -> int:
    low_tokens = [str(token).lower() for token in task_tokens if str(token).strip()]
    if not low_tokens:
        return 0
    search_parts = [
        str(row.get("symbol", "")),
        str(row.get("qualified_name", "")),
        str(row.get("signature", "")),
        str(row.get("scope", "")),
        str(row.get("kind", "")),
    ]
    relation_targets = row.get("relation_targets", [])
    if isinstance(relation_targets, list):
        search_parts.extend(str(item) for item in relation_targets[:12])
    text = " ".join(search_parts).lower()
    return sum(1 for token in low_tokens if token in text)


def _select_focus_symbol(
    symbol_rows: list[dict[str, Any]],
    task_tokens: list[str],
) -> dict[str, Any] | None:
    if not symbol_rows:
        return None
    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for row in symbol_rows:
        score = _score_symbol_row(row, task_tokens)
        line_start = _safe_int(row.get("line_start"), _safe_int(row.get("start_line"), 1))
        line_end = _safe_int(row.get("line_end"), _safe_int(row.get("end_line"), line_start))
        line_span = max(1, line_end - line_start + 1)
        ranked.append((score, -line_span, line_start, row))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return dict(ranked[0][3])


def _compute_line_window_from_symbol(
    row: dict[str, Any] | None,
    *,
    total_lines: int,
    snippet_radius: int,
) -> tuple[int, int, str]:
    if row:
        line_start = _safe_int(row.get("line_start"), _safe_int(row.get("start_line"), 1))
        line_end = _safe_int(row.get("line_end"), _safe_int(row.get("end_line"), line_start))
        line_start = max(1, min(total_lines, line_start))
        line_end = max(line_start, min(total_lines, line_end))
        span = max(1, line_end - line_start + 1)
        if span <= MAX_SYNTAX_UNIT_LINES:
            return line_start, line_end, "syntax_unit"

        focus_line = line_start
        return (
            max(1, focus_line - snippet_radius),
            min(total_lines, focus_line + snippet_radius),
            "syntax_unit_oversized_fallback",
        )
    return 1, min(total_lines, 1 + snippet_radius), "fallback"


def _truncate_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    if max_chars <= len(_TRUNCATION_MARKER) + 16:
        return content[:max_chars]
    head = int((max_chars - len(_TRUNCATION_MARKER)) * 0.65)
    tail = max_chars - len(_TRUNCATION_MARKER) - head
    return f"{content[:head]}{_TRUNCATION_MARKER}{content[-tail:]}"


def extract_snippet(
    project_dir: Path,
    rel_path: str,
    task_tokens: list[str],
    symbol_rows: list[dict[str, Any]],
    snippet_radius: int,
    max_chars: int,
) -> dict[str, Any] | None:
    file_path = project_dir / rel_path
    if not file_path.exists():
        return None

    # Check file size to prevent blocking on large files
    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            return {
                "path": rel_path,
                "start_line": 1,
                "end_line": 1,
                "line_start": 1,
                "line_end": 1,
                "symbol": "",
                "reason": "file_too_large",
                "content": f"[File too large: {file_size:,} bytes, skipping to prevent blocking]",
            }
    except OSError:
        return None

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "path": rel_path,
            "start_line": 1,
            "end_line": 1,
            "line_start": 1,
            "line_end": 1,
            "symbol": "",
            "reason": "read_error",
            "content": f"[Error reading file: {exc}]",
        }

    lines = text.splitlines()
    if not lines:
        return None

    focus_line = 1
    focus_symbol = ""
    focus_signature = ""
    focus_kind = ""
    focus_qn = ""
    reason = "symbol_or_token_focus"

    selected_symbol = _select_focus_symbol(symbol_rows, task_tokens)
    if selected_symbol is not None:
        focus_symbol = str(selected_symbol.get("symbol", ""))
        focus_signature = str(selected_symbol.get("signature", ""))
        focus_kind = str(selected_symbol.get("kind", ""))
        focus_qn = str(selected_symbol.get("qualified_name", ""))
        start_line, end_line, reason = _compute_line_window_from_symbol(
            selected_symbol,
            total_lines=len(lines),
            snippet_radius=snippet_radius,
        )
        focus_line = start_line
    else:
        lowered_tokens = [token.lower() for token in task_tokens]
        for idx, line in enumerate(lines, start=1):
            low_line = line.lower()
            if any(token in low_line for token in lowered_tokens):
                focus_line = idx
                break
        start_line = max(1, focus_line - snippet_radius)
        end_line = min(len(lines), focus_line + snippet_radius)

    excerpt_lines = lines[start_line - 1 : end_line]
    content = _truncate_content("\n".join(excerpt_lines), max_chars=max_chars)

    return {
        "path": rel_path,
        "start_line": start_line,
        "end_line": end_line,
        "line_start": start_line,
        "line_end": end_line,
        "symbol": focus_symbol,
        "signature": focus_signature,
        "kind": focus_kind,
        "qualified_name": focus_qn,
        "reason": reason,
        "content": content,
    }
