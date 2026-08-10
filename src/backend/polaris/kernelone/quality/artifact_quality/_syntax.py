"""Source-file syntax checks used by artifact quality scanning."""

from __future__ import annotations

import json
import os
import py_compile
import re
import shutil
import subprocess
from typing import Any

from polaris.kernelone.quality.artifact_quality._constants import (
    _TS_RETURN_OBJECT_OPEN_RE,
)


def _iter_typescript_return_object_bodies(text: str) -> list[str]:
    """Yield object-literal bodies of ``return { ... }`` with brace balancing.

    The historical regex used a non-greedy match ending at a line whose only
    content is ``};``. That pattern skips single-line returns such as
    ``return { x: this._x, y: this._y };`` and then consumes the next multi-line
    return, swallowing intervening method parameter types (``tick: Tick;``) and
    false-positive semicolon property findings. Brace balancing keeps each
    return object self-contained.
    """

    bodies: list[str] = []
    for match in _TS_RETURN_OBJECT_OPEN_RE.finditer(text):
        start = match.end()
        depth = 1
        index = start
        length = len(text)
        in_single = False
        in_double = False
        in_template = False
        in_line_comment = False
        in_block_comment = False
        while index < length and depth > 0:
            char = text[index]
            nxt = text[index + 1] if index + 1 < length else ""
            if in_line_comment:
                if char in "\r\n":
                    in_line_comment = False
                index += 1
                continue
            if in_block_comment:
                if char == "*" and nxt == "/":
                    in_block_comment = False
                    index += 2
                    continue
                index += 1
                continue
            if in_single:
                if char == "\\":
                    index += 2
                    continue
                if char == "'":
                    in_single = False
                index += 1
                continue
            if in_double:
                if char == "\\":
                    index += 2
                    continue
                if char == '"':
                    in_double = False
                index += 1
                continue
            if in_template:
                if char == "\\":
                    index += 2
                    continue
                if char == "`":
                    in_template = False
                index += 1
                continue
            if char == "/" and nxt == "/":
                in_line_comment = True
                index += 2
                continue
            if char == "/" and nxt == "*":
                in_block_comment = True
                index += 2
                continue
            if char == "'":
                in_single = True
                index += 1
                continue
            if char == '"':
                in_double = True
                index += 1
                continue
            if char == "`":
                in_template = True
                index += 1
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(text[start:index])
                    break
            index += 1
    return bodies


def _check_html_completeness(absolute_path: str) -> dict[str, Any]:
    """Detect structurally truncated HTML.

    An output-budget-truncated write produces a file that simply STOPS —
    missing ``</html>`` / unbalanced ``<script>`` tags (live factory-bench
    L2-11 r4: typing_test.html ended mid-function at line 198, no closing
    tags, and nothing in the chain noticed). Not a validator — only the
    truncation signature is checked.
    """
    with open(absolute_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lowered = text.lower()
    problems: list[str] = []
    if "<html" in lowered and "</html>" not in lowered:
        problems.append("missing </html> closing tag")
    open_scripts = len(re.findall(r"<script\b", lowered))
    close_scripts = lowered.count("</script>")
    if open_scripts > close_scripts:
        problems.append(f"{open_scripts - close_scripts} unclosed <script> tag(s)")
    if problems:
        return {"ok": False, "error": "truncated/incomplete HTML: " + "; ".join(problems)}
    return {"ok": True}


def _compress_node_syntax_error(raw_output: str, absolute_path: str) -> str:
    """Reduce `node --check` output to its actionable core.

    Keeps "<file>:<line>", the offending code line, the caret, and the
    SyntaxError message; drops the node stack frames and replaces the absolute
    path with the file name. A weak model repairing from this text needs the
    quoted line for a narrow edit_blocks match — the "at wrapSafe (node:...)"
    frames and absolute paths are pure distraction (live factory-bench L2-11
    r2: the repair turn failed an edit_blocks match against the noisy form).
    """
    text = str(raw_output or "").strip()
    if not text:
        return "syntax error"
    file_name = os.path.basename(absolute_path)
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("at ") or stripped.startswith("Node.js v"):
            continue
        lines.append(line.replace(absolute_path, file_name))
        if stripped.startswith(("SyntaxError", "Error")) and len(lines) > 1:
            break
    return "\n".join(lines).strip() or text[:200]


def check_source_file_syntax(absolute_path: str) -> dict[str, Any] | None:
    """Best-effort syntax validation for a materialized source file.

    Returns ``{'ok': False, 'error': ...}`` on syntax failure, ``{'ok': True}``
    on pass, ``None`` when no checker applies (unknown extension or checker
    tool unavailable). Single source of truth shared by the post-write tool
    diagnostic (A5) and the materialization artifact-quality scan, so a
    syntax-broken artifact that survives the turn deterministically enters the
    repair ladder (live factory-bench L2-10 r5: ``gfm: true;`` in app.js had
    its write-time diagnostic ignored and nothing downstream re-checked).
    """
    suffix = os.path.splitext(absolute_path)[1].lower()
    try:
        if suffix == ".py":
            try:
                py_compile.compile(absolute_path, doraise=True)
                return {"ok": True}
            except py_compile.PyCompileError as exc:
                message = str(exc.msg or exc).strip().splitlines()[-1]
                return {"ok": False, "error": message}
        if suffix in (".js", ".mjs", ".cjs"):
            node = shutil.which("node")
            if not node:
                return None
            proc = subprocess.run(
                [node, "--check", absolute_path], capture_output=True, text=True, timeout=20, check=False
            )
            if proc.returncode == 0:
                return {"ok": True}
            detail = _compress_node_syntax_error(proc.stderr or proc.stdout, absolute_path)
            return {"ok": False, "error": detail[:400]}
        if suffix == ".json":
            with open(absolute_path, encoding="utf-8") as fh:
                json.load(fh)
            return {"ok": True}
        if suffix in (".html", ".htm"):
            return _check_html_completeness(absolute_path)
        # R147: TypeScript was previously omitted, so post-write diagnostics and
        # materialization quality never saw TS1109/TS1005 failures (live r146
        # src/web.ts ``return,`` shipped with syntax_check=None). Delegate to
        # syntax_gate (tsc --noEmit) for definite parse-class diagnostics only.
        if suffix in (".ts", ".tsx"):
            from polaris.kernelone.quality.syntax_gate import check_file_syntax

            gate = check_file_syntax(absolute_path)
            if not gate.checked:
                return None
            if gate.ok:
                return {"ok": True}
            detail = str(gate.error or "TypeScript syntax error").strip()
            return {"ok": False, "error": detail[:400]}
    except (OSError, UnicodeDecodeError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"syntax check could not run: {exc}"}
    except ValueError as exc:  # json.JSONDecodeError
        return {"ok": False, "error": f"invalid JSON: {exc}"}
    return None
