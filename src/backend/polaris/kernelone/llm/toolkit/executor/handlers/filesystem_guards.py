"""Filesystem pre/post-write content guards and the post-write syntax gate.

Leaf module for ``filesystem.py``: pure content-shape guards (empty write,
edit-fragment write, destructive shrink) plus the zero-LLM post-write syntax
gate. No dependency on the other ``filesystem_*`` sibling modules, so it sits at
the foundation of the handler import graph.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Post-write syntax gate (Phase-1 A5, factory-bench L2-09 live evidence:
# 167 lines of working snake-game JS killed by one `;` where a `,` belonged —
# object literal at game.js:54. Zero-LLM checkers run AFTER a successful
# write; the diagnostic rides back in the tool RESULT so the model fixes it
# next turn. Writes are never blocked (progressive drafts stay legal) and
# checker selection is extension-driven — no project-specific logic (§8).
# ---------------------------------------------------------------------------


def _syntax_check_file(absolute_path: str) -> dict[str, Any] | None:
    """Delegate to the kernelone.quality single source of truth (shared with
    the materialization artifact-quality scan)."""
    from polaris.kernelone.quality import check_source_file_syntax

    return check_source_file_syntax(absolute_path)


def attach_post_write_syntax_check(result: dict[str, Any], absolute_path: str) -> dict[str, Any]:
    """Attach syntax diagnostics to a SUCCESSFUL write/edit result."""
    if not result.get("ok"):
        return result
    check = _syntax_check_file(absolute_path)
    if check is None:
        return result
    if check.get("ok"):
        result["syntax_check"] = "passed"
        return result
    result["syntax_check"] = "failed"
    error_text = str(check.get("error", ""))
    result["syntax_error"] = error_text
    if _looks_like_output_truncation(error_text):
        # The write was cut by the model's own output-token limit — a rewrite
        # at the same limit truncates at the same place forever (live
        # factory-bench L2-11 r6: index.html rewritten three times, 6.8-7.8KB,
        # every copy truncated). Only appending the remainder converges.
        result["suggestion"] = (
            "The file was CUT OFF by the output limit — do NOT rewrite it. "
            "Call append_to_file with ONLY the remaining content, continuing "
            f"exactly after the file's current end: {error_text}"
        )
    else:
        result["suggestion"] = (
            "The file was written BUT it has a syntax error — fix it now with a "
            "narrow edit_blocks line-range edit before doing anything else: "
            f"{error_text}"
        )
    return result


def _looks_like_output_truncation(error_text: str) -> bool:
    """Truncation signatures from the kernelone.quality SSOT checker."""
    lowered = str(error_text or "").lower()
    return (
        "unexpected end of input" in lowered
        or "truncated/incomplete html" in lowered
        or "was never closed" in lowered
        or "unexpected eof" in lowered
    )


# Destructive-shrink gate (Phase-1 A4 slice; run20 539→32-line overwrite,
# phase1smoke5 live: 1403 lines of django expressions.py replaced by 17).
# A bug fix is a NARROW edit; deleting a large block and writing back a
# fraction of it guts real files while still "applying" cleanly. Thresholds
# mirror the failure labeler's destructive_overwrite definition so the gate
# and the metric agree. fail-closed: the model gets a retryable teaching
# error telling it to narrow the range.
_DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES = 100
_DESTRUCTIVE_SHRINK_MAX_ADD_RATIO = 0.4

# Wall 2 (2026-06-15): a write_file whose ``content`` is blank/whitespace on a
# content-bearing target silently produced a 0-byte file that passed as a
# successful write — .css/.html were never syntax-gated and an empty .js passes
# the bracket check — so the step died ``director_no_materialized_changes`` with
# NO recovery. Guard these extensions; the teaching error is recognised by
# ``contract_guards`` as an argument-shape failure so the escalation/re-ask
# ladder forces a real-content write instead of dead-lettering.
_EMPTY_WRITE_GUARD_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyw",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".css",
        ".scss",
        ".less",
        ".html",
        ".htm",
        ".md",
        ".json",
        ".vue",
        ".svelte",
    }
)
# Files that may LEGITIMATELY be empty — never flag these.
_EMPTY_WRITE_SENTINEL_BASENAMES: frozenset[str] = frozenset({"__init__.py", "py.typed", ".gitkeep"})


def is_empty_write_content_violation(rel: str, content: str) -> bool:
    """True when a blank write to a content-bearing target is a Wall-2 violation.

    The weak Director narrates the file body in prose/reasoning and emits
    ``write_file`` with an empty ``content`` argument; that 0-byte write was
    being accepted as authoritative. Sentinel files (``__init__.py`` /
    ``py.typed`` / ``.gitkeep``) may legitimately be empty.
    """
    if content.strip():
        return False
    normalized = rel.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if basename in _EMPTY_WRITE_SENTINEL_BASENAMES:
        return False
    return any(normalized.endswith(ext) for ext in _EMPTY_WRITE_GUARD_EXTENSIONS)


def is_blank_sentinel_write(rel: str, content: str) -> bool:
    """True when a blank write targets a legitimately-empty sentinel file.

    An empty ``__init__.py`` / ``py.typed`` / ``.gitkeep`` is valid and often
    REQUIRED (the Python package marker). The PreWriteGuard's EmptyCode syntax
    check must skip these blanks, otherwise the marker never lands, the
    materialization quality gate reports it "missing", and the weak Director
    burns its budget in a repair read-loop and dead-letters (factory-bench
    L4-19: empty ``backend/__init__.py`` blocked -> 0/3 successes). A NON-empty
    sentinel still validates normally.
    """
    if content.strip():
        return False
    basename = rel.replace("\\", "/").rsplit("/", 1)[-1]
    return basename in _EMPTY_WRITE_SENTINEL_BASENAMES


# Line-anchored insertion directives a weak model sometimes emits as the ENTIRE
# write_file content — treating a full-file write like an incremental edit. The
# canonical live failure (L2-08 world-clock, 2026-06-16): app.js written as
# "// 第 70 行之后添加\n}" — a 28-byte syntactically-broken stub that the syntax
# gate rejects but the Director kept re-emitting (6x SyntaxError, no convergence).
_EDIT_FRAGMENT_DIRECTIVE_RE = re.compile(
    r"第\s*\d+\s*行\s*(之后|之前|后面|前面|后|前)?\s*(添加|插入|修改|替换)"
    r"|在\s*第?\s*\d+\s*行"
    r"|(?:insert|add|append|replace|change|modify)\b[^\n]{0,30}\bline\s+\d+"
    r"|after\s+line\s+\d+"
    r"|line\s+\d+\b[^\n]{0,20}(?:添加|插入|insert|add|append)",
    re.IGNORECASE,
)


def is_edit_fragment_write_violation(rel: str, content: str) -> bool:
    """True when write_file content is an edit fragment, not a complete file.

    Weak models sometimes emit a line-anchored insertion directive (e.g.
    ``// 第 70 行之后添加\\n}``) as the ENTIRE write_file content, treating a
    full-file write like an incremental edit — producing a broken stub. Catch
    only the unambiguous case: short content for a code target whose body is
    dominated by such a directive. A real file body is far longer and is not an
    insertion instruction; a false positive is recoverable (the Director simply
    re-emits the full content), so the guard favours catching the failure.
    """
    text = (content or "").strip()
    if not text or len(text) > 400:
        return False
    normalized = rel.replace("\\", "/")
    if not any(normalized.endswith(ext) for ext in _EMPTY_WRITE_GUARD_EXTENSIONS):
        return False
    return _EDIT_FRAGMENT_DIRECTIVE_RE.search(text) is not None


def _destructive_shrink_error(target: str, removed_lines: int, added_lines: int, *, tool_hint: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"Destructive shrink rejected: this edit would replace {removed_lines} lines of "
            f"{target} with only {added_lines} line(s). Large deletions disguised as edits "
            "destroy working code."
        ),
        "suggestion": tool_hint,
        "error_type": "destructive_shrink",
        "retryable": True,
    }


# ---------------------------------------------------------------------------
# JS/TS block-comment glob hygiene (shared by AgentAccel + Director DEO writes)
# ---------------------------------------------------------------------------
# LLM comments often write glob examples like ``src/**/*.ts``. Inside a block
# comment the substring ``*/`` terminates the comment early (TS1109 cascade).
# R146: Director execution_tools historically bypassed this sanitizer, so
# DEO-path writes shipped unparseable TS and failed real_run_gate build.

_JS_TS_BLOCK_COMMENT_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})
_BLOCK_COMMENT_GLOB_CLOSURE_RE = re.compile(r"\*\*/(?=[A-Za-z0-9_*.[{])")
_BLOCK_COMMENT_GLOB_FOLLOW_RE = re.compile(r"[A-Za-z0-9_*.[{]")
# Weak models often write ``return,`` / ``break,`` / ``continue,`` instead of
# statement terminators (r146 L1-01 src/web.ts:72 → TS1109 Expression expected).
_JS_TS_CONTROL_FLOW_COMMA_RE = re.compile(r"^(\s*)(return|break|continue)\s*,\s*$")


def _find_js_ts_block_comment_close(text: str, start: int = 0) -> int:
    """Find a real ``*/`` closer, skipping ``**/`` glob fragments."""

    close_index = text.find("*/", start)
    while close_index >= 0:
        next_char = text[close_index + 2 : close_index + 3]
        if (
            close_index > 0
            and text[close_index - 1] == "*"
            and next_char
            and _BLOCK_COMMENT_GLOB_FOLLOW_RE.match(next_char)
        ):
            close_index = text.find("*/", close_index + 2)
            continue
        return close_index
    return -1


def sanitize_js_ts_block_comment_glob_closures(rel: str, text: str) -> tuple[str, bool]:
    """Keep glob examples in JS/TS block comments from closing the comment.

    Returns ``(sanitized_text, changed)``. Paths outside JS/TS extensions are
    returned unchanged.
    """

    if Path(str(rel or "")).suffix.lower() not in _JS_TS_BLOCK_COMMENT_EXTENSIONS:
        return text, False
    if "**/" not in text or "/*" not in text:
        return text, False

    changed = False
    in_block_comment = False
    sanitized_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        cursor = 0
        pieces: list[str] = []
        while cursor < len(line):
            if not in_block_comment:
                block_start = line.find("/*", cursor)
                if block_start < 0:
                    pieces.append(line[cursor:])
                    cursor = len(line)
                    continue
                pieces.append(line[cursor : block_start + 2])
                cursor = block_start + 2
                in_block_comment = True

            close_index = _find_js_ts_block_comment_close(line, cursor)
            segment_end = close_index if close_index >= 0 else len(line)
            segment = line[cursor:segment_end]
            repaired = _BLOCK_COMMENT_GLOB_CLOSURE_RE.sub("** /", segment)
            changed = changed or repaired != segment
            pieces.append(repaired)
            if close_index < 0:
                cursor = len(line)
                continue
            pieces.append("*/")
            cursor = close_index + 2
            in_block_comment = False
        sanitized_lines.append("".join(pieces))
    return "".join(sanitized_lines), changed


def sanitize_js_ts_control_flow_statement_commas(rel: str, text: str) -> tuple[str, bool]:
    """Rewrite bare ``return,`` / ``break,`` / ``continue,`` to statement terminators.

    Only whole-line control-flow keywords with a trailing comma are rewritten so
    object-literal / expression commas stay untouched.
    """

    if Path(str(rel or "")).suffix.lower() not in _JS_TS_BLOCK_COMMENT_EXTENSIONS:
        return text, False
    if not re.search(r"\b(?:return|break|continue)\s*,", text):
        return text, False

    changed = False
    repaired_lines: list[str] = []
    for line in str(text or "").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        match = _JS_TS_CONTROL_FLOW_COMMA_RE.match(body)
        if match is None:
            repaired_lines.append(line)
            continue
        repaired_lines.append(f"{match.group(1)}{match.group(2)};{newline}")
        changed = True
    return "".join(repaired_lines), changed


def sanitize_js_ts_write_hygiene(rel: str, text: str) -> tuple[str, dict[str, bool]]:
    """Apply shared JS/TS write hygiene. Returns ``(text, flags)``."""

    flags: dict[str, bool] = {
        "block_comment_glob_sanitized": False,
        "control_flow_comma_sanitized": False,
    }
    text, flags["block_comment_glob_sanitized"] = sanitize_js_ts_block_comment_glob_closures(rel, text)
    text, flags["control_flow_comma_sanitized"] = sanitize_js_ts_control_flow_statement_commas(rel, text)
    return text, flags
