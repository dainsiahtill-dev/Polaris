"""Patch composition for Director Repair Kernel plans."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .contracts import (
    ComposedPatch,
    CompositionIssue,
    CompositionResult,
    RepairOperation,
    sha256_text,
)

_TEXT_KINDS = {"text_replace"}
_JSON_KINDS = {"json_set", "json_delete"}
_WRITE_KINDS = {"write_file"}
_SUPPORTED_KINDS = _TEXT_KINDS | _JSON_KINDS | _WRITE_KINDS | {"observation"}


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
                issues.append(
                    CompositionIssue(
                        code="unsupported_operation",
                        message=f"Unsupported repair operation kind: {operation.kind}",
                        path=operation.path,
                        operation_ids=(operation.operation_id,),
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
            content_before = str(base_files.get(path, ""))
            operation_kinds = {operation.kind for operation in path_operations}
            if len(operation_kinds & _WRITE_KINDS) and len(path_operations) > 1:
                issues.append(
                    CompositionIssue(
                        code="write_file_conflict",
                        message="write_file cannot be composed with other operations for the same path.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
                    )
                )
                continue
            if operation_kinds & _TEXT_KINDS and operation_kinds & _JSON_KINDS:
                issues.append(
                    CompositionIssue(
                        code="mixed_patch_kinds",
                        message="Text and JSON operations cannot be composed for the same path.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
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
            elif operation_kinds <= _WRITE_KINDS:
                patch, patch_issues = self._compose_write(path, content_before, path_operations)
            else:
                patch = None
                patch_issues = [
                    CompositionIssue(
                        code="unsupported_operation_mix",
                        message="Unsupported repair operation mix.",
                        path=path,
                        operation_ids=tuple(op.operation_id for op in path_operations),
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
            ),
            [],
        )

    def _compose_write(
        self,
        path: str,
        content_before: str,
        operations: Sequence[RepairOperation],
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
