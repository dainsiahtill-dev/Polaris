from __future__ import annotations

from typing import Any


def has_memory_refs(context: dict[str, Any] | None) -> bool:
    """Return whether a memory context includes durable evidence references."""
    if not isinstance(context, dict):
        return False

    run_id = str(context.get("run_id") or "").strip()
    if not run_id:
        return False

    ref_keys = (
        "event_seq",
        "event_id",
        "artifact",
        "artifact_path",
        "code_ref",
        "file_path",
        "path",
    )
    for key in ref_keys:
        if context.get(key):
            return True

    nested_refs = context.get("refs")
    if isinstance(nested_refs, dict):
        for key in ref_keys:
            if nested_refs.get(key):
                return True
    return False


__all__ = ["has_memory_refs"]
