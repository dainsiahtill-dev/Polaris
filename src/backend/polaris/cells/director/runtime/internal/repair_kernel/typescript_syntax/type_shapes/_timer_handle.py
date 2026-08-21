"""Conservative DOM/Node timer-handle mismatch repair."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..constants import TYPESCRIPT_TIMER_HANDLE_SOURCE_TOOL

_TIMER_HANDLE_DIAGNOSTIC_RE = re.compile(
    r"Type\s+['\"](?:NodeJS\.)?Timeout['\"]\s+is\s+not\s+assignable\s+to\s+type\s+['\"]number['\"]",
    re.IGNORECASE,
)
_BROWSER_AUTHORITY_MARKERS = (
    "HTMLCanvasElement",
    "requestAnimationFrame",
    "document.",
    "window.",
)


def build_typescript_timer_handle_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Route a proven browser timer call through the DOM timer namespace.

    With both ``DOM`` and ``@types/node`` active, TypeScript can type
    ``globalThis.setTimeout`` as ``NodeJS.Timeout``. A browser scheduler that
    explicitly owns a numeric animation handle should use ``window.setTimeout``.
    The repair fails closed unless the exact TS2322 signature, diagnostic line,
    call token, and browser-only source evidence all agree.
    """

    normalized_base = {str(path): str(content) for path, content in base_files.items() if str(path)}
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        if not _TIMER_HANDLE_DIAGNOSTIC_RE.search(text):
            continue
        path = str(diagnostic.path or "").strip().replace("\\", "/")
        content = normalized_base.get(path, "")
        line_number = int(diagnostic.line or 0)
        if not path or not content or line_number <= 0:
            continue
        if not any(marker in content for marker in _BROWSER_AUTHORITY_MARKERS):
            continue
        lines = content.splitlines(keepends=True)
        if line_number > len(lines):
            continue
        line = lines[line_number - 1]
        token = "globalThis.setTimeout"
        relative_start = line.find(token)
        if relative_start < 0 or "(" not in line[relative_start + len(token) :]:
            continue
        span_start = sum(len(item) for item in lines[: line_number - 1]) + relative_start
        span_end = span_start + len(token)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=span_start,
                span_end=span_end,
                expected=token,
                replacement="window.setTimeout",
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_browser_timer_handle",
                    "diagnostic_line": line_number,
                    "timer_authority": "dom_window",
                },
            )
        )
        matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.timer_handle",
        source_tool=TYPESCRIPT_TIMER_HANDLE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"timer_handle_repairs": len(operations)},
    )
