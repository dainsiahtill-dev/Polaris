"""Migration-ledger invariants for application cellization batch 2A."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LEDGER_PATH = BACKEND_ROOT / "docs" / "migration" / "ledger.yaml"
BATCH2A_MIGRATION_ID = "mig-application-batch2a"
RETIRED_TARGET_PATHS = {
    "polaris/cells/director/execution/internal/director_logic_rules.py",
    "polaris/cells/roles/runtime/internal/skill_loader.py",
    "polaris/cells/roles/runtime/internal/tui_console.py",
    "polaris/cells/roles/runtime/internal/standalone_entry.py",
}


def _load_batch2a_unit() -> dict[str, Any]:
    """Return the batch-2A migration ledger unit."""
    payload = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, list):
        raise AssertionError("migration ledger must contain a units list")
    for unit in units:
        if isinstance(unit, dict) and unit.get("id") == BATCH2A_MIGRATION_ID:
            return unit
    raise AssertionError(f"missing migration ledger unit: {BATCH2A_MIGRATION_ID}")


def test_application_batch2a_migration_ledger_matches_current_cells() -> None:
    """Batch-2A ledger facts must point to current Cell/KernelOne targets."""
    unit = _load_batch2a_unit()

    assert unit.get("status") == "verified"
    assert unit.get("current_impl_state") == "new_root_primary"
    assert not unit.get("blockers")

    target = unit.get("target")
    assert isinstance(target, dict)
    target_paths = target.get("target_paths")
    actual_impl_paths = target.get("actual_impl_paths")
    assert isinstance(target_paths, list)
    assert isinstance(actual_impl_paths, list)

    stale_targets = RETIRED_TARGET_PATHS.intersection(str(path) for path in target_paths)
    assert stale_targets == set()

    missing_targets = [path for path in target_paths if not (BACKEND_ROOT / str(path)).exists()]
    assert missing_targets == []

    source_refs = unit.get("source_refs")
    assert isinstance(source_refs, list)
    still_present_sources = [
        source.get("path")
        for source in source_refs
        if isinstance(source, dict) and (BACKEND_ROOT / str(source.get("path") or "")).exists()
    ]
    assert still_present_sources == []
