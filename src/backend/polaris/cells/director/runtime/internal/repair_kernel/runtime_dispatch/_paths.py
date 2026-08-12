"""Path normalization helpers for runtime_dispatch."""

from __future__ import annotations

from collections.abc import Mapping


def _normalize_runtime_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        _normalize_runtime_repair_path(str(path or "")): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_runtime_repair_path(str(path or ""))
    }


def _normalize_runtime_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
