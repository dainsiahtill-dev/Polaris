from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..contracts import RepairOperation, RepairPlan


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }


def _composition_operations_for_rust_line_suggestions(plan: RepairPlan) -> tuple[RepairOperation, ...]:
    return _composition_operations_for_rust_unique_context(plan)


def _composition_operations_for_rust_trait_imports(plan: RepairPlan) -> tuple[RepairOperation, ...]:
    return _composition_operations_for_rust_unique_context(plan)


def _composition_operations_for_rust_unique_context(plan: RepairPlan) -> tuple[RepairOperation, ...]:
    operations: list[RepairOperation] = []
    for operation in plan.operations:
        metadata = dict(operation.metadata)
        if metadata.get("unique_context") is True:
            metadata["unique_context"] = str(operation.expected or "")
        operations.append(replace(operation, metadata=metadata))
    return tuple(operations)


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return ""
    if any(part == ".." for part in normalized.split("/")):
        return ""
    return normalized
