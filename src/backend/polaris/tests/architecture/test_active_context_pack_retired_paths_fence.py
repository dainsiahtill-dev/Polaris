from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]

FOUNDATIONAL_CONTEXT_PACKS = (
    "polaris/cells/chief_engineer/blueprint/generated/context.pack.json",
    "polaris/cells/director/execution/generated/context.pack.json",
    "polaris/cells/orchestration/pm_planning/generated/context.pack.json",
    "polaris/cells/orchestration/pm_dispatch/generated/context.pack.json",
    "polaris/cells/orchestration/workflow_runtime/generated/context.pack.json",
    "polaris/cells/runtime/state_owner/generated/context.pack.json",
    "polaris/cells/policy/permission/generated/context.pack.json",
    "polaris/cells/policy/workspace_guard/generated/context.pack.json",
    "polaris/cells/storage/layout/generated/context.pack.json",
)

RETIRED_APPLICATION_MARKERS = (
    "polaris/application/services/",
    "polaris/application/app/services/",
    "polaris/application/orchestration/",
    "polaris/application/role_agent/",
    "polaris/application/storage_layout.py",
    "polaris/infrastructure/legacy_core/",
)


def _load_json(relative_path: str) -> dict[str, Any]:
    payload = json.loads((BACKEND_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{relative_path} must contain a JSON object"
    return payload


def _retired_markers_in_context_pack(payload: dict[str, Any]) -> list[str]:
    searchable = json.dumps(
        {
            "owned_paths": payload.get("owned_paths", []),
            "hotspots": payload.get("hotspots", []),
            "test_targets": payload.get("test_targets", []),
        },
        ensure_ascii=False,
    )
    return [marker for marker in RETIRED_APPLICATION_MARKERS if marker in searchable]


def _context_path_exists(relative_path: str) -> bool:
    if "*" in relative_path:
        return any((BACKEND_ROOT).glob(relative_path))
    return (BACKEND_ROOT / relative_path).exists()


def test_foundational_context_packs_do_not_reference_retired_application_paths() -> None:
    offenders: list[str] = []
    for relative_path in FOUNDATIONAL_CONTEXT_PACKS:
        markers = _retired_markers_in_context_pack(_load_json(relative_path))
        if markers:
            offenders.append(f"{relative_path}: {', '.join(markers)}")

    assert not offenders, (
        "Foundational Cell context packs must point agents at current Cell, "
        f"KernelOne, or delivery paths instead of retired application paths: {offenders}"
    )


def test_foundational_context_pack_paths_exist() -> None:
    offenders: list[str] = []
    for relative_path in FOUNDATIONAL_CONTEXT_PACKS:
        payload = _load_json(relative_path)
        for field in ("owned_paths", "hotspots", "test_targets"):
            values = payload.get(field, [])
            assert isinstance(values, list), f"{relative_path}.{field} must be a list"
            for value in values:
                candidate = str(value or "").strip()
                if candidate and not _context_path_exists(candidate):
                    offenders.append(f"{relative_path}.{field}: {candidate}")

    assert not offenders, f"Foundational context packs must only reference existing paths: {offenders}"
