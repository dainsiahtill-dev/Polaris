from __future__ import annotations

from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

"""Shared TypeScript repair helpers: plan_ops."""


def _line_start_offsets(lines: Sequence[str]) -> list[int]:
    offsets: list[int] = [0]
    current = 0
    for line in lines:
        current += len(line)
        offsets.append(current)
    return offsets


def _repair_plan_or_none(
    *,
    rule_id: str,
    source_tool: str,
    operations: Sequence[RepairOperation],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    risk_level: str = "low",
    metadata: Mapping[str, object] | None = None,
) -> RepairPlan | None:
    if not operations:
        return None
    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level=risk_level,
        priority=1,
        metadata=dict(metadata or {}),
    )


def _apply_single_text_operation(content: str, operation: RepairOperation) -> str:
    if operation.span_start is None or operation.span_end is None:
        return content
    return content[: operation.span_start] + str(operation.replacement or "") + content[operation.span_end :]


def _text_replace_operations_from_repair(
    *,
    path: str,
    original: str,
    repaired: str,
    metadata: Mapping[str, object],
) -> tuple[RepairOperation, ...]:
    before_hash = sha256_text(original)
    operations: list[RepairOperation] = []
    original_lines = original.splitlines(keepends=True)
    repaired_lines = repaired.splitlines(keepends=True)
    original_offsets = _line_start_offsets(original_lines)
    matcher = SequenceMatcher(a=original_lines, b=repaired_lines, autojunk=False)
    for tag, start_line, end_line, replacement_start, replacement_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = original_offsets[start_line]
        end = original_offsets[end_line]
        expected = "".join(original_lines[start_line:end_line])
        operation_metadata = dict(metadata)
        if not expected:
            operation_metadata["expected_context_before"] = "".join(original_lines[max(0, start_line - 2) : start_line])
            operation_metadata["expected_context_after"] = "".join(
                original_lines[start_line : min(len(original_lines), start_line + 2)]
            )
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="".join(repaired_lines[replacement_start:replacement_end]),
                before_hash=before_hash,
                metadata=operation_metadata,
            )
        )
    return tuple(operations)


__all__ = (
    "_apply_single_text_operation",
    "_line_start_offsets",
    "_repair_plan_or_none",
    "_text_replace_operations_from_repair",
)
