from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..javascript_syntax import repair_javascript_export_contract_placeholders
from ..path_files import normalize_base_files_strict, normalize_repair_path_strict
from .constants import *  # noqa: F403
from .common import *  # noqa: F403

"""TypeScript syntax repair module: html_dom."""

def _build_html_typescript_module_script_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Plan HTML entrypoint quality repairs (TS module scripts + truncated HTML).

    Live L1-01 r154: deterministic TS-script rewrite alone left truncated HTML
    failing artifact quality, then quality_repair_llm_timeout. This plan closes
    incomplete HTML structure and rewrites TypeScript module scripts to compiled
    JavaScript entrypoints before any LLM repair ladder.
    """

    operations: list[RepairOperation] = []
    repaired: list[dict[str, str]] = []
    truncated_repairs: list[dict[str, object]] = []
    rewritten_paths: set[str] = set()

    for path in _parse_html_truncated_entrypoint_paths(diagnostics):
        original = str(base_files.get(path) or "")
        if not original:
            continue
        rewritten, meta = _repair_html_entrypoint_quality_text_with_metadata(
            original,
            base_files=base_files,
        )
        if rewritten == original:
            continue
        span_start = _common_prefix_len(original, rewritten)
        expected = original[span_start:]
        replacement = rewritten[span_start:]
        if not expected and not replacement:
            continue
        context_start = max(0, span_start - 240)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=span_start,
                span_end=len(original),
                expected=expected,
                replacement=replacement,
                before_hash=sha256_text(original),
                metadata={
                    **meta,
                    "repair_kind": "html_truncated_entrypoint_closure",
                    "edit_strategy": "span_text_replace",
                    "unique_context": original[context_start:],
                    "expected_context_before": original[context_start:span_start],
                },
            )
        )
        truncated_repairs.append({"file": path, **meta})
        rewritten_paths.add(path)
        script_rows = meta.get("scripts")
        for script in script_rows if isinstance(script_rows, Sequence) else ():
            if isinstance(script, Mapping):
                repaired.append(
                    {
                        "file": path,
                        "source": str(script.get("source") or ""),
                        "replacement": str(script.get("replacement") or ""),
                    }
                )

    for item in _parse_html_typescript_module_script_errors(diagnostics):
        path = item["file"]
        if path in rewritten_paths:
            continue
        source_ref = item["source"]
        original = str(base_files.get(path) or "")
        replacement = item.get("replacement") or _html_javascript_entrypoint_for_typescript_source(
            source_ref,
            base_files=base_files,
        )
        if not replacement:
            replacement = _html_compiled_javascript_entrypoint_for_script(source_ref, base_files=base_files)
        if not original or not replacement:
            continue
        reference_pattern = re.compile(
            rf"(?P<prefix>\bsrc\s*=\s*|\b(?:from|import)\s*)(?P<quote>['\"])"
            rf"{re.escape(source_ref)}(?P=quote)",
            re.IGNORECASE,
        )
        for match in reference_pattern.finditer(original):
            expected = str(match.group(0) or "")
            quote = str(match.group("quote") or '"')
            prefix = str(match.group("prefix") or "")
            start = match.start()
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=start,
                    span_end=start + len(expected),
                    expected=expected,
                    replacement=f"{prefix}{quote}{replacement}{quote}",
                    before_hash=sha256_text(original),
                    metadata={
                        "repair_kind": "html_typescript_module_script",
                        "source": source_ref,
                        "replacement": replacement,
                    },
                )
            )
            repaired.append({"file": path, "source": source_ref, "replacement": replacement})

    rule_id = "html.typescript_module_script"
    if (truncated_repairs and not repaired) or truncated_repairs:
        rule_id = "html.truncated_entrypoint_closure"
    return _repair_plan_or_none(
        rule_id=rule_id,
        source_tool=HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"scripts": repaired, "truncated_closures": truncated_repairs},
    )

def _build_typescript_html_container_selector_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if diagnostic.code == "html_container_contract_failed"
    )
    if not matched_diagnostics:
        return None

    html_ids = _html_container_ids(base_files)
    if not html_ids:
        return None

    operations: list[RepairOperation] = []
    repaired: list[dict[str, object]] = []
    for path, original in sorted(base_files.items()):
        if not path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
            continue
        text = str(original or "")
        for match in _TS_EXACT_HTML_ID_TOKEN_REGEX_RE.finditer(text):
            token_group = str(match.group("tokens") or "")
            tokens = _html_container_selector_tokens(token_group)
            if not tokens or not _html_ids_support_container_tokens(html_ids, tokens):
                continue
            flags = str(match.group("flags") or "")
            expected = str(match.group(0) or "")
            replacement = f"/id=[\"'][^\"']*({token_group})[^\"']*[\"']/{flags}"
            if expected == replacement:
                continue
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start(),
                    span_end=match.end(),
                    expected=expected,
                    replacement=replacement,
                    before_hash=sha256_text(text),
                    metadata={
                        "repair_kind": "typescript_html_container_selector",
                        "selector_tokens": tuple(tokens),
                        "html_ids": tuple(sorted(html_ids)),
                    },
                )
            )
            repaired.append({"file": path, "tokens": tuple(tokens), "html_ids": tuple(sorted(html_ids))})
            break

    return _repair_plan_or_none(
        rule_id="typescript.html_container_selector",
        source_tool=TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"selectors": repaired},
    )

def _build_typescript_dom_local_shim_cleanup_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_dom_local_shim_diagnostic(diagnostic, base_files=base_files):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path or path not in base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    cleaned_files: list[dict[str, object]] = []
    for path in sorted(diagnostics_by_path):
        original = str(base_files.get(path) or "")
        repaired, removed_symbols = _remove_typescript_local_dom_shims(original)
        if repaired == original or not removed_symbols:
            continue
        path_diagnostics = tuple(diagnostics_by_path[path])
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_dom_local_shim_cleanup",
                    "removed_symbols": tuple(removed_symbols),
                    "diagnostic_ids": tuple(diagnostic.diagnostic_id for diagnostic in path_diagnostics),
                },
            )
        )
        matched_diagnostics.extend(path_diagnostics)
        cleaned_files.append({"file": path, "removed_symbols": tuple(removed_symbols)})

    return _repair_plan_or_none(
        rule_id="typescript.dom_local_shim_cleanup",
        source_tool=TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        metadata={"cleaned_files": cleaned_files},
    )

def _build_javascript_typescript_annotation_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    if not any(
        _looks_like_javascript_typescript_annotation_error(diagnostic.raw or diagnostic.message)
        for diagnostic in diagnostics
    ):
        return None
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _javascript_annotation_candidate_paths(base_files, diagnostics):
        original = str(base_files.get(path) or "")
        repaired = _strip_typescript_annotations_from_javascript(original)
        repaired = repair_javascript_export_contract_placeholders(
            path=path,
            text=repaired,
            base_files={**base_files, path: repaired},
        )
        if repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "javascript_typescript_annotation_cleanup"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.javascript_annotation_cleanup",
        source_tool=JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )

def _build_typeorm_model_normalization_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _parse_undeclared_runtime_import_paths(diagnostics, package_name="typeorm"):
        original = str(base_files.get(path) or "")
        if not original:
            continue
        repaired = _normalize_undeclared_typeorm_model_source(original)
        if repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typeorm_model_normalization"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.typeorm_model_normalization",
        source_tool=TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )

def _parse_html_typescript_module_script_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for match in _HTML_TS_MODULE_SCRIPT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "source": str(match.group("src") or "").strip(),
                "replacement": "",
            }
            key = (item["file"], item["source"])
            if item["file"] and item["source"] and key not in seen:
                seen.add(key)
                parsed.append(item)
        for match in _HTML_COMPILED_JS_MISSING_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "source": str(match.group("src") or "").strip(),
                "replacement": str(match.group("emitted") or "").strip(),
            }
            key = (item["file"], item["source"])
            if item["file"] and item["source"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed

def _html_container_ids(base_files: Mapping[str, str]) -> set[str]:
    ids: set[str] = set()
    for path, content in base_files.items():
        if not path.endswith((".html", ".htm")):
            continue
        for match in _HTML_ID_ATTRIBUTE_RE.finditer(str(content or "")):
            value = str(match.group("id") or "").strip()
            if value:
                ids.add(value)
    return ids

def _html_container_selector_tokens(token_group: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in str(token_group or "").split("|"):
        token = raw.strip()
        if token and re.fullmatch(r"[A-Za-z0-9_-]+", token):
            tokens.append(token)
    return tuple(_dedupe_preserve_order(tokens))

def _html_ids_support_container_tokens(html_ids: set[str], tokens: Sequence[str]) -> bool:
    lowered_ids = {item.lower() for item in html_ids}
    for token in tokens:
        lowered_token = str(token or "").lower()
        if any(lowered_token in html_id and html_id != lowered_token for html_id in lowered_ids):
            return True
    return False

def _looks_like_javascript_typescript_annotation_error(error: object) -> bool:
    text = str(error or "")
    lowered = text.lower()
    return ".js:" in text and "syntaxerror: unexpected token ':'" in lowered

def _normalize_undeclared_typeorm_model_source(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if _TYPEORM_IMPORT_LINE_RE.match(raw_line) or _TS_DECORATOR_LINE_RE.match(raw_line):
            continue
        lines.append(_normalize_ts_class_field_initialization(raw_line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip() + "\n")

def _is_typescript_dom_local_shim_diagnostic(
    diagnostic: RepairDiagnostic,
    *,
    base_files: Mapping[str, str],
) -> bool:
    code = str(diagnostic.code or "").lower()
    if code not in {"typescript_ts2339", "typescript_ts2739", "typescript_ts2740", "typescript_ts2741"}:
        return False
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not path.endswith((".ts", ".tsx")):
        return False
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if _is_typescript_dom_local_shim_type_conflict(code=code, text=text):
        return True
    return _is_typescript_dom_local_shim_member_gap(code=code, text=text) and _typescript_base_files_have_dom_lib(
        base_files
    )

def _is_typescript_dom_local_shim_type_conflict(*, code: str, text: str) -> bool:
    if code not in {"typescript_ts2739", "typescript_ts2740", "typescript_ts2741"}:
        return False
    return "missing" in text and any(name.lower() in text for name in _TS_LOCAL_DOM_SHIM_NAMES)

def _is_typescript_dom_local_shim_member_gap(*, code: str, text: str) -> bool:
    if code != "typescript_ts2339":
        return False
    return ("property 'createelement'" in text and ("getelementbyid" in text or "document" in text)) or (
        "property 'queryselector'" in text and "htmlelement" in text
    )

__all__ = (
    "_build_html_typescript_module_script_plan",
    "_build_typescript_html_container_selector_plan",
    "_build_typescript_dom_local_shim_cleanup_plan",
    "_build_javascript_typescript_annotation_plan",
    "_build_typeorm_model_normalization_plan",
    "_parse_html_typescript_module_script_errors",
    "_html_container_ids",
    "_html_container_selector_tokens",
    "_html_ids_support_container_tokens",
    "_looks_like_javascript_typescript_annotation_error",
    "_normalize_undeclared_typeorm_model_source",
    "_is_typescript_dom_local_shim_diagnostic",
    "_is_typescript_dom_local_shim_type_conflict",
    "_is_typescript_dom_local_shim_member_gap",
)
