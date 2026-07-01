"""Migration-ledger invariants for the resident.autonomy Cell."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LEDGER_PATH = BACKEND_ROOT / "docs" / "migration" / "ledger.yaml"
RESIDENT_MIGRATION_ID = "mig-application-batch2b-resident"
RETIRED_EVIDENCE_SERVICE_TARGETS = {
    "polaris/cells/resident/autonomy/internal/evidence_bundle_service.py",
    "polaris/cells/resident/autonomy/internal/evidence_service.py",
}


def _load_resident_migration_unit() -> dict[str, Any]:
    """Return the resident.autonomy migration ledger unit."""
    payload = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, list):
        raise AssertionError("migration ledger must contain a units list")
    for unit in units:
        if isinstance(unit, dict) and unit.get("id") == RESIDENT_MIGRATION_ID:
            return unit
    raise AssertionError(f"missing migration ledger unit: {RESIDENT_MIGRATION_ID}")


def test_resident_autonomy_migration_ledger_matches_current_cell() -> None:
    """Resident autonomy ledger targets must describe the current Cell files."""
    unit = _load_resident_migration_unit()

    assert unit.get("status") == "verified"
    assert unit.get("current_impl_state") == "new_root_primary"
    assert not unit.get("blockers")
    assert not (BACKEND_ROOT / "polaris" / "application" / "resident").exists()

    target = unit.get("target")
    assert isinstance(target, dict)
    target_paths = target.get("target_paths")
    assert isinstance(target_paths, list)

    stale_targets = RETIRED_EVIDENCE_SERVICE_TARGETS.intersection(str(path) for path in target_paths)
    assert stale_targets == set()

    missing_targets = [path for path in target_paths if not (BACKEND_ROOT / str(path)).exists()]
    assert missing_targets == []
