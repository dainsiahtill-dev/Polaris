from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]

ARCHIVE_CELL_CONTEXTS = (
    "polaris/cells/archive/factory_archive/generated/context.pack.json",
    "polaris/cells/archive/task_snapshot_archive/generated/context.pack.json",
    "polaris/cells/archive/run_archive/generated/context.pack.json",
)

ARCHIVE_TEMPLATE_DOCS = (
    "docs/templates/targets/storage_archive/archive/factory_archive/README.agent.md",
    "docs/templates/targets/storage_archive/archive/factory_archive/generated/context.pack.json",
    "docs/templates/targets/storage_archive/archive/task_snapshot_archive/README.agent.md",
    "docs/templates/targets/storage_archive/archive/task_snapshot_archive/generated/context.pack.json",
    "docs/templates/targets/storage_archive/archive/run_archive/README.agent.md",
    "docs/templates/targets/storage_archive/archive/run_archive/generated/context.pack.json",
)

STORAGE_ARCHIVE_TEMPLATE_README = "docs/templates/targets/storage_archive/README.md"

RETIRED_ARCHIVE_PATH_MARKERS = (
    "polaris/application/services/",
    "polaris/application/app/services/",
)


def _read_text(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text(encoding="utf-8")


def _load_json(relative_path: str) -> dict[str, Any]:
    payload = json.loads(_read_text(relative_path))
    assert isinstance(payload, dict), f"{relative_path} must contain a JSON object"
    return payload


def _retired_markers_in_text(text: str) -> list[str]:
    return [marker for marker in RETIRED_ARCHIVE_PATH_MARKERS if marker in text]


def test_archive_context_packs_use_current_cell_and_kernelone_paths() -> None:
    offenders: list[str] = []
    for relative_path in ARCHIVE_CELL_CONTEXTS:
        payload = _load_json(relative_path)
        searchable = json.dumps(
            {
                "owned_paths": payload.get("owned_paths", []),
                "hotspots": payload.get("hotspots", []),
            },
            ensure_ascii=False,
        )
        markers = _retired_markers_in_text(searchable)
        if markers:
            offenders.append(f"{relative_path}: {', '.join(markers)}")

    assert not offenders, (
        "Archive context packs must point agents at current polaris/cells and "
        f"kernelone/storage paths, not retired application services: {offenders}"
    )


def test_storage_archive_templates_do_not_reintroduce_archive_application_paths() -> None:
    offenders: list[str] = []
    for relative_path in ARCHIVE_TEMPLATE_DOCS:
        markers = _retired_markers_in_text(_read_text(relative_path))
        if markers:
            offenders.append(f"{relative_path}: {', '.join(markers)}")

    assert not offenders, (
        f"Archive target templates must not seed future agents with retired application service paths: {offenders}"
    )


def test_storage_archive_template_archive_rows_use_current_paths() -> None:
    offenders: list[str] = []
    for line_number, line in enumerate(_read_text(STORAGE_ARCHIVE_TEMPLATE_README).splitlines(), start=1):
        if "| `archive." not in line:
            continue
        markers = _retired_markers_in_text(line)
        if markers:
            offenders.append(f"{STORAGE_ARCHIVE_TEMPLATE_README}:{line_number}: {', '.join(markers)}")

    assert not offenders, (
        f"Archive rows in the storage_archive template README must reference current Cell/KernelOne paths: {offenders}"
    )
