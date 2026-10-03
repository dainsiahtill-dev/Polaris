"""Pure, non-authoritative projection of admitted write scope for model guidance."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.kernelone.quality.scope_authority import path_matches_any_declared_scope_candidate


def _paths(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(type(item) is not str for item in value):
        raise ValueError("task_write_guidance_paths_invalid")
    result: list[str] = []
    for item in value:
        path = item.strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if not path or path.startswith(("/", "~")) or ":" in path or ".." in path.split("/"):
            raise ValueError("task_write_guidance_path_unsafe")
        if path not in result:
            result.append(path)
    return result


def project_task_write_guidance(
    envelope: Mapping[str, Any],
    declared_targets: Sequence[str],
) -> dict[str, Any]:
    """Separate effect targets from inventory without granting any capability.

    A present empty authorization denies every write. Malformed authority must
    not be replaced by declared inventory. Runtime guards remain authoritative.
    """

    authorization = envelope.get("authorization")
    if not isinstance(authorization, Mapping) or "allowed_write_paths" not in authorization:
        raise ValueError("task_write_guidance_authorization_missing")
    allowed = _paths(authorization["allowed_write_paths"])
    targets = _paths(list(declared_targets))
    writes = [path for path in targets if allowed and path_matches_any_declared_scope_candidate(path, allowed)]
    required = _paths(authorization.get("required_write_targets", []))
    if any(not allowed or not path_matches_any_declared_scope_candidate(path, allowed) for path in required):
        raise ValueError("task_write_guidance_required_effect_outside_scope")
    return {
        "schema_version": "task.write_guidance.v1",
        "write_targets": list(dict.fromkeys([*writes, *required])),
        "reference_only_targets": [path for path in targets if path not in writes],
        "inventory_is_not_write_authority": True,
    }


def render_task_write_guidance(guidance: Mapping[str, Any]) -> str:
    """Stable prompt evidence, independently comparable with the final request."""

    return "TASK WRITE GUIDANCE: " + json.dumps(dict(guidance), ensure_ascii=False, sort_keys=True)
