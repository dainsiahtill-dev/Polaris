"""dom_runtime domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _base_file_from_runtime_path,
    _dedupe_diagnostics,
    _normalize_base_files,
    _normalize_repair_path,
)
from .constants import (
    _BROWSER_BOOTSTRAP_CALL_RE,
    _JS_DOM_GLOBAL_RUNTIME_RE,
    JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
)


def build_javascript_dom_global_runtime_guard_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Guard browser-only bootstrap calls when Node executes a browser bundle."""

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        for failure in _dom_global_runtime_failures(diagnostic, normalized_base):
            runtime_global = failure["global"]
            for path in _dom_global_source_candidates(failure["file"], normalized_base):
                if path in seen:
                    continue
                text = normalized_base.get(path)
                if text is None:
                    continue
                operation = _dom_global_guard_operation(
                    path=path,
                    text=text,
                    runtime_global=runtime_global,
                    diagnostic=diagnostic,
                )
                if operation is None:
                    continue
                operations.append(operation)
                matched.append(diagnostic)
                seen.add(path)
                break
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.dom_global_runtime_guard",
        source_tool=JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=_dedupe_diagnostics(matched),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "runtime_plan_scope": "browser_bootstrap_top_level_call_guard_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def _dom_global_runtime_failures(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    failures: list[dict[str, str]] = []
    if diagnostic.code == "javascript_dom_global_in_node_runtime":
        raw_path = str(diagnostic.path or "")
        runtime_global = str(diagnostic.metadata.get("runtime_global") or "").strip() or "document"
        rel_file = _base_file_from_runtime_path(raw_path, base_files)
        if raw_path or rel_file:
            failures.append({"file": rel_file or raw_path, "global": runtime_global})
    for match in _JS_DOM_GLOBAL_RUNTIME_RE.finditer(raw):
        raw_path = str(match.group("file") or "")
        rel_file = _base_file_from_runtime_path(raw_path, base_files)
        runtime_global = str(match.group("global") or "").strip() or "document"
        failures.append({"file": rel_file or raw_path, "global": runtime_global})
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for failure in failures:
        deduped[(failure["file"], failure["global"])] = failure
    return tuple(deduped.values())


def _dom_global_source_candidates(runtime_file: str, base_files: Mapping[str, str]) -> tuple[str, ...]:
    normalized_runtime = _normalize_repair_path(str(runtime_file or "").removeprefix("file://").replace("\\", "/"))
    candidates: list[str] = []
    if normalized_runtime.startswith("dist/") and normalized_runtime.endswith(".js"):
        stem = normalized_runtime.removeprefix("dist/").removesuffix(".js")
        candidates.extend(
            [
                f"src/{stem}.ts",
                f"src/{stem}.tsx",
                f"src/{stem}.js",
                f"src/{stem}.mjs",
                f"{stem}.ts",
                f"{stem}.js",
            ]
        )
    if normalized_runtime:
        candidates.append(normalized_runtime)
    candidates.extend(("src/web.ts", "src/web.js", "web.ts", "web.js", "src/main.ts", "src/main.js"))
    for path, text in base_files.items():
        if path in candidates:
            continue
        if not path.endswith((".ts", ".tsx", ".js", ".mjs")):
            continue
        if ("document" in text or "window" in text) and _BROWSER_BOOTSTRAP_CALL_RE.search(text):
            candidates.append(path)
    return tuple(dict.fromkeys(path for path in candidates if path in base_files))


def _dom_global_guard_operation(
    *,
    path: str,
    text: str,
    runtime_global: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if "document" not in text and "window" not in text:
        return None
    for match in reversed(tuple(_BROWSER_BOOTSTRAP_CALL_RE.finditer(text))):
        context_before = text[max(0, match.start() - 180) : match.start()]
        if "typeof document" in context_before or "typeof window" in context_before:
            continue
        indent = str(match.group("indent") or "")
        call = str(match.group("call") or "").strip()
        guard_global = "window" if runtime_global == "window" else "document"
        replacement = f'{indent}if (typeof {guard_global} !== "undefined") {{\n{indent}  {call}\n{indent}}}'
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=match.group(0),
            replacement=replacement,
            before_hash=sha256_text(text),
            metadata={
                "repair_kind": "javascript_dom_global_runtime_guard",
                "runtime_global": guard_global,
                "diagnostic_id": diagnostic.diagnostic_id,
                "edit_file_preferred": True,
                "unsafe_cases_fail_closed": True,
                "expected_context_before": text[max(0, match.start() - 160) : match.start()],
                "expected_context_after": text[match.end() : min(len(text), match.end() + 160)],
            },
        )
    return None
