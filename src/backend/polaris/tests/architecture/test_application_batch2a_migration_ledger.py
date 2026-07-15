"""Migration-ledger invariants for application cellization batch 2A."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LEDGER_PATH = BACKEND_ROOT / "docs" / "migration" / "ledger.yaml"
RETIREMENT_EVIDENCE_PATH = BACKEND_ROOT / "docs" / "governance" / "audits" / "application_batch2a_retired_targets.yaml"
BATCH2A_MIGRATION_ID = "mig-application-batch2a"


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


def _load_retirement_evidence() -> dict[str, dict[str, Any]]:
    """Return explicit removed-target evidence indexed by repository path."""

    payload = yaml.safe_load(RETIREMENT_EVIDENCE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload.get("schema_version") == "polaris.migration.retirement_evidence.v1"
    assert payload.get("migration_id") == BATCH2A_MIGRATION_ID

    records = payload.get("records")
    assert isinstance(records, list) and records
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        assert isinstance(record, dict)
        path = str(record.get("path") or "").strip()
        assert path and path not in indexed
        indexed[path] = record
    return indexed


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

    retirement_evidence = _load_retirement_evidence()
    retired_paths = set(retirement_evidence)

    missing_targets = {str(path) for path in target_paths if not (BACKEND_ROOT / str(path)).exists()}
    missing_impl_paths = {str(path) for path in actual_impl_paths if not (BACKEND_ROOT / str(path)).exists()}
    assert missing_targets == retired_paths
    assert missing_impl_paths == retired_paths

    for path, record in retirement_evidence.items():
        assert record.get("status") == "removed"
        assert str(record.get("removed_at") or "").strip()
        assert str(record.get("removal_commit") or "").strip()
        assert str(record.get("reason") or "").strip()
        assert not (BACKEND_ROOT / path).exists()

        replacement_paths = record.get("replacement_paths")
        assert isinstance(replacement_paths, list) and replacement_paths
        missing_replacements = [
            replacement for replacement in replacement_paths if not (BACKEND_ROOT / str(replacement)).is_file()
        ]
        assert missing_replacements == []

    source_refs = unit.get("source_refs")
    assert isinstance(source_refs, list)
    still_present_sources = [
        source.get("path")
        for source in source_refs
        if isinstance(source, dict) and (BACKEND_ROOT / str(source.get("path") or "")).exists()
    ]
    assert still_present_sources == []
