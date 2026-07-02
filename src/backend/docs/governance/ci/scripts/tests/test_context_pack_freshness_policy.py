"""Tests for Context Pack freshness policy wiring."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_context_pack_freshness import ContextPackFreshnessChecker
from docs.governance.ci.scripts.context_pack_freshness_policy import (
    FRESHNESS_THRESHOLD_SECONDS,
    evaluate_context_pack_freshness,
)


def _write_catalog(workspace: Path, cells: list[dict[str, Any]]) -> None:
    """Write a cells.yaml catalog fixture."""
    catalog_path = workspace / "docs" / "graph" / "catalog" / "cells.yaml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(yaml.safe_dump({"cells": cells}, sort_keys=False), encoding="utf-8")


def _write_context_pack(workspace: Path, cell_id: str, payload: dict[str, Any], *, mtime: float) -> Path:
    """Write a context.pack.json fixture for a Cell."""
    pack_path = workspace / "polaris" / "cells" / cell_id.replace(".", "/") / "generated" / "context.pack.json"
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(pack_path, (mtime, mtime))
    return pack_path


def test_context_pack_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate entrypoints must match the policy."""
    now = time.time()
    _write_catalog(tmp_path, [{"id": "runtime.projection"}])
    _write_context_pack(
        tmp_path,
        "runtime.projection",
        {"cell_id": "runtime.projection"},
        mtime=now - 60,
    )

    policy = evaluate_context_pack_freshness(tmp_path, now=now)
    standalone = ContextPackFreshnessChecker(tmp_path).check_context_pack_freshness()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_context_pack_freshness()

    assert policy.passed is True
    assert aggregate.rule_id == policy.rule_id == standalone.rule_id
    assert aggregate.violations == list(policy.violations) == []
    assert standalone.violations == []


def test_context_pack_policy_reports_missing_stale_and_invalid(tmp_path: Path) -> None:
    """Missing, stale, and structurally invalid Context Packs are violations."""
    now = 1_700_000_000.0
    _write_catalog(
        tmp_path,
        [
            {"id": "fresh.cell"},
            {"id": "stale.cell"},
            {"id": "invalid.cell"},
            {"id": "missing.cell"},
        ],
    )
    _write_context_pack(tmp_path, "fresh.cell", {"cell_id": "fresh.cell"}, mtime=now - 60)
    _write_context_pack(
        tmp_path,
        "stale.cell",
        {"cell_id": "stale.cell"},
        mtime=now - FRESHNESS_THRESHOLD_SECONDS - 60,
    )
    _write_context_pack(tmp_path, "invalid.cell", {"name": "invalid.cell"}, mtime=now - 60)

    result = evaluate_context_pack_freshness(tmp_path, now=now)

    assert result.passed is False
    assert any("Missing context.pack.json: missing.cell" in violation for violation in result.violations)
    assert any("stale.cell: context.pack.json is stale" in violation for violation in result.violations)
    assert any("invalid.cell: Missing 'cell_id' or 'id' field" in violation for violation in result.violations)
    assert any("fresh.cell: context.pack.json is fresh" in evidence for evidence in result.evidence)


def test_context_pack_policy_reports_catalog_parse_errors(tmp_path: Path) -> None:
    """Malformed catalog YAML should be an observable hard failure."""
    catalog_path = tmp_path / "docs" / "graph" / "catalog" / "cells.yaml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text("cells: [", encoding="utf-8")

    result = evaluate_context_pack_freshness(tmp_path, now=1_700_000_000.0)

    assert result.passed is False
    assert any("Failed to parse cells.yaml" in violation for violation in result.violations)


def test_context_pack_policy_warns_on_empty_catalog(tmp_path: Path) -> None:
    """An empty catalog remains a warning-only no-op."""
    _write_catalog(tmp_path, [])

    result = evaluate_context_pack_freshness(tmp_path, now=1_700_000_000.0)

    assert result.passed is True
    assert result.warnings == ("No cells found in cells.yaml",)
