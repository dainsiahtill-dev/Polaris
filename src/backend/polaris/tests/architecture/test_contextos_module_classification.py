"""Governance tests for ContextOS hot-path and dormant module classification."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
MANIFEST = REPO_ROOT / "src" / "backend" / "docs" / "governance" / "contextos_module_classification.json"


def test_contextos_dormant_modules_are_explicitly_classified() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dormant = {
        str(item["module"]): item
        for item in payload.get("dormant_modules", [])
        if isinstance(item, dict) and item.get("module")
    }

    expected = {
        "polaris.kernelone.context.context_os.adaptive_weights",
        "polaris.kernelone.context.context_os.budget_optimizer",
        "polaris.kernelone.context.context_os.multi_resolution_store",
        "polaris.kernelone.context.context_os.predictive",
    }

    assert expected <= set(dormant)
    for module in expected:
        item = dormant[module]
        assert item["status"] == "dormant"
        assert str(item.get("activation_gate") or "").strip()
        assert (REPO_ROOT / item["path"]).exists()


def test_contextos_hot_path_modules_are_explicitly_classified() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    hot_path = {
        str(item["module"]): item
        for item in payload.get("hot_path_modules", [])
        if isinstance(item, dict) and item.get("module")
    }

    expected = {
        "polaris.kernelone.context.truth_log_service",
        "polaris.kernelone.context.working_state_manager",
        "polaris.kernelone.context.projection_engine",
        "polaris.kernelone.context.context_os.runtime.engine",
        "polaris.kernelone.context.chunks.assembler",
    }

    assert expected <= set(hot_path)
    for module in expected:
        item = hot_path[module]
        assert item["status"] == "hot_path"
        assert (REPO_ROOT / item["path"]).exists()
