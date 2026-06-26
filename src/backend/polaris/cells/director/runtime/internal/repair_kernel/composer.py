"""Patch composition for Director Repair Kernel plans."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .contracts import (
    FILE_ABSENT_HASH,
    ComposedPatch,
    CompositionIssue,
    CompositionResult,
    RepairOperation,
    sha256_text,
)

_TEXT_KINDS = {"text_replace"}
_JSON_KINDS = {"json_set", "json_delete"}
_RESERVED_STRUCTURED_KINDS = {"toml_set", "toml_delete", "yaml_set", "yaml_delete"}
_WRITE_KINDS = {"write_file"}
_DELETE_KINDS = {"delete_file"}
_SUPPORTED_KINDS = _TEXT_KINDS | _JSON_KINDS | _RESERVED_STRUCTURED_KINDS | _WRITE_KINDS | _DELETE_KINDS | {"observation"}


class PatchComposer:
    """Compose file operations into one final patch per file."""

    def compose(
        self,
        base_files: Mapping[str, str],
        operations: Sequence[RepairOperation],
    ) -> CompositionResult:
        issues: list[CompositionIssue] = []
        grouped: dict[str, list[RepairOperation]] = defaultdict(list)
        for operation in operations:
            if operation.kind not in _SUPPORTED_KINDS:
                structured_metadata = _structured_operation_issue_metadata((operation,), reserved=False)
                issues.append(
                    CompositionIssue(
                        code="unsupported_operation",
                        message=f"Unsupported repair operation kind: {operation.kind}",
                        path=operation.path,
                        operation_ids=(operation.operation_id,),
                        metadata=structured_metadata,
                    )
                )
                continue
            if operation.kind == "observation":
                continue
            normalized_path = _normalize_path(operation.path)
            if not normalized_path:
                issues.append(
                    CompositionIssue(
                        code="invalid_path",
                        message="Repair operation path is empty or unsafe.",
                        path=operation.path,
                        operation_ids=(operation.operation_id,),
                    )
                )
                continue
            grouped[normalized_path].append(operation)

        patches: list[ComposedPatch] = []
        for path, path_operations in sorted(grouped.items()):
            file_existed_before = path in base_files
            content_before = str(base_files.get(path, ""))
            operation_kinds = {operation.kind for operation in path_operations}
            if len(operation_kinds & _WRITE_KINDS) and len(path_operations) > 1:
                issues.append(
                    CompositionIssue(
                        code="write_file_conflict",
                        message="write_file cannot be composed with other operations for the same path.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
                        metadata=_structured_operation_issue_metadata(path_operations, reserved=True),
                    )
                )
                continue
            if len(operation_kinds & _DELETE_KINDS) and len(path_operations) > 1:
                issues.append(
                    CompositionIssue(
                        code="delete_file_conflict",
                        message="delete_file cannot be composed with other operations for the same path.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
                        metadata=_structured_operation_issue_metadata(path_operations, reserved=True),
                    )
                )
                continue
            if operation_kinds & _TEXT_KINDS and operation_kinds & (_JSON_KINDS | _RESERVED_STRUCTURED_KINDS):
                issues.append(
                    CompositionIssue(
                        code="mixed_patch_kinds",
                        message="Text and structured operations cannot be composed for the same path.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
                        metadata=_structured_operation_issue_metadata(path_operations, reserved=True),
                    )
                )
                continue
            before_hash_issue = _check_before_hash(path, content_before, path_operations)
            if before_hash_issue is not None:
                issues.append(before_hash_issue)
                continue
            if operation_kinds <= _TEXT_KINDS:
                patch, patch_issues = self._compose_text(path, content_before, path_operations)
            elif operation_kinds <= _JSON_KINDS:
                patch, patch_issues = self._compose_json(path, content_before, path_operations)
            elif operation_kinds <= _RESERVED_STRUCTURED_KINDS:
                patch = None
                patch_issues = [
                    CompositionIssue(
                        code="reserved_structured_operation",
                        message="TOML/YAML structured repair operation is reserved but has no executable composer.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
                        metadata=_structured_operation_issue_metadata(path_operations, reserved=True),
                    )
                ]
            elif operation_kinds <= _WRITE_KINDS:
                patch, patch_issues = self._compose_write(
                    path,
                    content_before,
                    path_operations,
                    file_existed_before=file_existed_before,
                )
            elif operation_kinds <= _DELETE_KINDS:
                patch, patch_issues = self._compose_delete(
                    path,
                    content_before,
                    path_operations,
                    file_existed_before=file_existed_before,
                )
            else:
                patch = None
                structured_metadata = {}
                if operation_kinds & _RESERVED_STRUCTURED_KINDS:
                    structured_metadata = _structured_operation_issue_metadata(path_operations, reserved=True)
                patch_issues = [
                    CompositionIssue(
                        code="unsupported_operation_mix",
                        message="Unsupported repair operation mix.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
                        metadata=structured_metadata,
                    )
                ]
            issues.extend(patch_issues)
            if patch is not None:
                patches.append(patch)

        if issues:
            return CompositionResult(ok=False, patches=(), issues=tuple(issues))
        return CompositionResult(ok=True, patches=tuple(patches), issues=())

    def _compose_text(
        self,
        path: str,
        content_before: str,
        operations: Sequence[RepairOperation],
    ) -> tuple[ComposedPatch | None, list[CompositionIssue]]:
        issues: list[CompositionIssue] = []
        ordered = sorted(
            operations,
            key=lambda op: -1 if op.span_start is None else int(op.span_start),
            reverse=True,
        )
        content_after = content_before
        previous_start: int | None = None
        unique_context_operation_ids: list[str] = []
        for operation in ordered:
            if operation.span_start is None or operation.span_end is None:
                issues.append(
                    CompositionIssue(
                        code="missing_text_span",
                        message="Text repair requires span_start and span_end.",
                        path=path,
                        operation_ids=(operation.operation_id,),
                    )
                )
                continue
            start = int(operation.span_start)
            end = int(operation.span_end)
            if start < 0 or end < start or end > len(content_before):
                issues.append(
                    CompositionIssue(
                        code="invalid_text_span",
                        message="Text repair span is outside file bounds.",
                        path=path,
                        operation_ids=(operation.operation_id,),
                    )
                )
                continue
            if previous_start is not None and end > previous_start:
                issues.append(
                    CompositionIssue(
                        code="overlapping_text_spans",
                        message="Text repair spans overlap and cannot be safely composed.",
                        path=path,
                        operation_ids=(operation.operation_id,),
                    )
                )
                continue
            expected = operation.expected
            if expected is not None and content_before[start:end] != expected:
                issues.append(
                    CompositionIssue(
                        code="text_precondition_failed",
                        message="Text repair expected content does not match span.",
                        path=path,
                        operation_ids=(operation.operation_id,),
                    )
                )
                continue
            context_issue, context_checked = _check_unique_context(path, content_before, operation, start, end)
            if context_checked:
                unique_context_operation_ids.append(operation.operation_id)
            if context_issue is not None:
                issues.append(context_issue)
                continue
            content_after = content_after[:start] + str(operation.replacement or "") + content_after[end:]
            previous_start = start
        if issues:
            return None, issues
        return (
            ComposedPatch(
                path=path,
                content_before=content_before,
                content_after=content_after,
                operation_ids=tuple(op.operation_id for op in operations),
                metadata={
                    "large_file_safe": True,
                    "span_based": True,
                    "unique_context_checked": bool(unique_context_operation_ids),
                    "unique_context_operation_ids": unique_context_operation_ids,
                    "write_file_reason": "",
                },
            ),
            [],
        )

    def _compose_json(
        self,
        path: str,
        content_before: str,
        operations: Sequence[RepairOperation],
    ) -> tuple[ComposedPatch | None, list[CompositionIssue]]:
        try:
            payload: Any = json.loads(content_before or "{}")
        except json.JSONDecodeError as exc:
            return None, [
                CompositionIssue(
                    code="json_parse_failed",
                    message=str(exc),
                    path=path,
                    operation_ids=tuple(op.operation_id for op in operations),
                )
            ]
        payload = deepcopy(payload)
        issues: list[CompositionIssue] = []
        for operation in operations:
            if not operation.json_path:
                issues.append(
                    CompositionIssue(
                        code="missing_json_path",
                        message="JSON repair requires a json_path.",
                        path=path,
                        operation_ids=(operation.operation_id,),
                    )
                )
                continue
            if operation.kind == "json_set":
                issue_code = _json_set(payload, operation.json_path, operation.value)
            elif operation.kind == "json_delete":
                issue_code = _json_delete(payload, operation.json_path)
            else:
                issue_code = "unsupported_json_operation"
            if issue_code:
                issues.append(
                    CompositionIssue(
                        code=issue_code,
                        message="JSON repair path cannot be safely applied.",
                        path=path,
                        operation_ids=(operation.operation_id,),
                    )
                )
        if issues:
            return None, issues
        content_after = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return (
            ComposedPatch(
                path=path,
                content_before=content_before,
                content_after=content_after,
                operation_ids=tuple(op.operation_id for op in operations),
                metadata={
                    "large_file_safe": False,
                    "span_based": False,
                    "structured_operation": "json",
                    "unique_context_checked": False,
                    "write_file_reason": "structured_json_serialization",
                },
            ),
            [],
        )

    def _compose_write(
        self,
        path: str,
        content_before: str,
        operations: Sequence[RepairOperation],
        *,
        file_existed_before: bool,
    ) -> tuple[ComposedPatch | None, list[CompositionIssue]]:
        operation = operations[0]
        if operation.content is None:
            return None, [
                CompositionIssue(
                    code="missing_write_content",
                    message="write_file repair requires content.",
                    path=path,
                    operation_ids=(operation.operation_id,),
                )
            ]
        return (
            ComposedPatch(
                path=path,
                content_before=content_before,
                content_after=operation.content,
                operation_ids=(operation.operation_id,),
                exists_before=file_existed_before,
                exists_after=True,
                metadata={
                    **dict(operation.metadata or {}),
                    "large_file_safe": False,
                    "span_based": False,
                    "unique_context_checked": False,
                    "write_file_reason": _write_file_reason(operation=operation, content_before=content_before),
                    "created_file": not file_existed_before,
                    "deleted_file": False,
                    "created_or_deleted": "created" if not file_existed_before else "",
                },
            ),
            [],
        )

    def _compose_delete(
        self,
        path: str,
        content_before: str,
        operations: Sequence[RepairOperation],
        *,
        file_existed_before: bool,
    ) -> tuple[ComposedPatch | None, list[CompositionIssue]]:
        operation = operations[0]
        if not file_existed_before:
            return None, [
                CompositionIssue(
                    code="delete_file_missing_base_file",
                    message="delete_file repair requires the file to exist in base_files.",
                    path=path,
                    operation_ids=(operation.operation_id,),
                    metadata={"delete_file_requires_base_file": True},
                )
            ]
        return (
            ComposedPatch(
                path=path,
                content_before=content_before,
                content_after="",
                operation_ids=(operation.operation_id,),
                after_hash=FILE_ABSENT_HASH,
                exists_before=True,
                exists_after=False,
                metadata={
                    **dict(operation.metadata or {}),
                    "large_file_safe": False,
                    "span_based": False,
                    "unique_context_checked": False,
                    "write_file_reason": "",
                    "created_file": False,
                    "deleted_file": True,
                    "created_or_deleted": "deleted",
                    "rollback_restore_strategy": "write_file_full_restore",
                },
            ),
            [],
        )


def _normalize_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _check_before_hash(
    path: str,
    content_before: str,
    operations: Sequence[RepairOperation],
) -> CompositionIssue | None:
    current_hash = sha256_text(content_before)
    mismatched = [op.operation_id for op in operations if op.before_hash and op.before_hash != current_hash]
    if not mismatched:
        return None
    return CompositionIssue(
        code="before_hash_mismatch",
        message="Repair operation before_hash does not match current file content.",
        path=path,
        operation_ids=tuple(mismatched),
    )


def _check_unique_context(
    path: str,
    content: str,
    operation: RepairOperation,
    start: int,
    end: int,
) -> tuple[CompositionIssue | None, bool]:
    before = _metadata_text(operation.metadata, "expected_context_before", "context_before")
    after = _metadata_text(operation.metadata, "expected_context_after", "context_after")
    unique_context = _metadata_text(operation.metadata, "unique_context")
    if not before and not after and not unique_context:
        return None, False

    if before:
        before_start = start - len(before)
        if before_start < 0 or content[before_start:start] != before:
            return (
                CompositionIssue(
                    code="text_context_before_mismatch",
                    message="Text repair expected_context_before does not match span.",
                    path=path,
                    operation_ids=(operation.operation_id,),
                    metadata={"unique_context_checked": True},
                ),
                True,
            )
    if after and content[end : end + len(after)] != after:
        return (
            CompositionIssue(
                code="text_context_after_mismatch",
                message="Text repair expected_context_after does not match span.",
                path=path,
                operation_ids=(operation.operation_id,),
                metadata={"unique_context_checked": True},
            ),
            True,
        )

    span_text = content[start:end]
    probe = unique_context or f"{before}{span_text}{after}"
    if not probe:
        return (
            CompositionIssue(
                code="missing_unique_text_context",
                message="Text repair context metadata did not produce a unique probe.",
                path=path,
                operation_ids=(operation.operation_id,),
                metadata={"unique_context_checked": True},
            ),
            True,
        )

    match_count, probe_start = _unique_occurrence_count_limited(content, probe)
    if match_count != 1:
        return (
            CompositionIssue(
                code="text_context_not_unique",
                message="Text repair context must identify exactly one location.",
                path=path,
                operation_ids=(operation.operation_id,),
                metadata={
                    "unique_context_checked": True,
                    "match_count": match_count,
                    "match_count_limited": True,
                },
            ),
            True,
        )

    probe_end = probe_start + len(probe)
    if unique_context:
        span_inside_probe = probe_start <= start and end <= probe_end
    else:
        span_inside_probe = probe_start == start - len(before) and probe_end == end + len(after)
    if not span_inside_probe:
        return (
            CompositionIssue(
                code="text_context_span_mismatch",
                message="Text repair unique context does not anchor the requested span.",
                path=path,
                operation_ids=(operation.operation_id,),
                metadata={"unique_context_checked": True},
            ),
            True,
        )

    return None, True


def _unique_occurrence_count_limited(content: str, probe: str) -> tuple[int, int]:
    """Return 0, 1, or 2 for none, unique, or multiple without scanning past the second match."""
    if not probe:
        return 0, -1
    first = content.find(probe)
    if first < 0:
        return 0, -1
    if content.find(probe, first + 1) >= 0:
        return 2, first
    return 1, first


def _metadata_text(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return ""


def _structured_operation_issue_metadata(
    operations: Sequence[RepairOperation],
    *,
    reserved: bool,
) -> dict[str, Any]:
    formats = sorted(
        {
            structured_format
            for operation in operations
            if (structured_format := _structured_format_for_kind(operation.kind))
        }
    )
    if not formats:
        return {}
    structured_format = formats[0] if len(formats) == 1 else "mixed"
    return {
        "structured_operation_reserved": reserved,
        "structured_format": structured_format,
        "structured_formats": formats,
        "languages": formats,
        "requires_parser": True,
        "parser_available": False,
        "format_preservation_unproven": True,
        "manual_runtime_rule_required": True,
        "executable_structured_composer": False,
        "write_file_fallback_allowed": False,
        "write_file_reason": "reserved_structured_serialization_requires_parser",
    }


def _structured_format_for_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized.startswith("toml_"):
        return "toml"
    if normalized.startswith(("yaml_", "yml_")):
        return "yaml"
    return ""


def _write_file_reason(*, operation: RepairOperation, content_before: str) -> str:
    reason = _metadata_text(operation.metadata, "write_file_reason")
    if reason:
        return reason
    if content_before == "":
        return "new_file_or_empty_file"
    return "fallback_whole_file_repair"


def _json_set(payload: Any, path: tuple[str, ...], value: Any) -> str | None:
    current = payload
    for part in path[:-1]:
        if not isinstance(current, dict):
            return "json_path_parent_not_object"
        if part not in current:
            current[part] = {}
        elif not isinstance(current[part], dict):
            return "json_path_parent_not_object"
        current = current[part]
    if not isinstance(current, dict):
        return "json_path_parent_not_object"
    current[path[-1]] = value
    return None


def _json_delete(payload: Any, path: tuple[str, ...]) -> str | None:
    current = payload
    for part in path[:-1]:
        if not isinstance(current, dict) or part not in current:
            return "json_path_not_found"
        current = current.get(part)
    if not isinstance(current, dict):
        return "json_path_parent_not_object"
    if path[-1] not in current:
        return "json_path_not_found"
    current.pop(path[-1])
    return None
