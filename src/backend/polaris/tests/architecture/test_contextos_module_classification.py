"""Governance tests for ContextOS hot-path and dormant module classification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import tomllib
import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
MANIFEST = REPO_ROOT / "src" / "backend" / "docs" / "governance" / "contextos_module_classification.json"
CATALOG = REPO_ROOT / "src" / "backend" / "docs" / "graph" / "catalog" / "cells.yaml"
PACKAGED_MANIFEST = (
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "kernelone"
    / "context"
    / "context_os"
    / "contextos_module_classification.json"
)
RUFF_CONFIG = REPO_ROOT / "ruff.toml"
CONTEXTOS_FULL_LANDING_CARD = (
    REPO_ROOT
    / "src"
    / "backend"
    / "docs"
    / "governance"
    / "templates"
    / "verification-cards"
    / "vc-20260614-contextos-full-landing.yaml"
)
ACTIVE_CONTEXTOS_LANDING_DOCS = (
    CONTEXTOS_FULL_LANDING_CARD,
    REPO_ROOT / "docs" / "blueprints" / "CONTEXTOS_FULL_LANDING_BLUEPRINT_20260614.md",
    REPO_ROOT / "docs" / "superpowers" / "plans" / "2026-06-14-contextos-full-landing.md",
)


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


def test_contextos_runtime_import_does_not_eagerly_load_dormant_modules() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dormant_modules = [
        str(item["module"])
        for item in payload.get("dormant_modules", [])
        if isinstance(item, dict) and item.get("module")
    ]
    script = f"""
import json
import sys

import polaris.kernelone.context.context_os.runtime  # noqa: F401

dormant = {json.dumps(dormant_modules)}
loaded = [name for name in dormant if name in sys.modules]
print(json.dumps(loaded, ensure_ascii=False))
raise SystemExit(1 if loaded else 0)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src" / "backend")
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stdout


def test_contextos_package_import_does_not_eagerly_load_dormant_modules() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dormant_modules = [
        str(item["module"])
        for item in payload.get("dormant_modules", [])
        if isinstance(item, dict) and item.get("module")
    ]
    script = f"""
import json
import sys

import polaris.kernelone.context.context_os  # noqa: F401

dormant = {json.dumps(dormant_modules)}
loaded = [name for name in dormant if name in sys.modules]
print(json.dumps(loaded, ensure_ascii=False))
raise SystemExit(1 if loaded else 0)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src" / "backend")
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stdout


def test_contextos_graph_declares_real_receipt_store_module() -> None:
    payload = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    kernelone_core = next(cell for cell in payload["cells"] if cell["id"] == "kernelone.core")
    modules = set(kernelone_core["current_modules"])

    assert "polaris.kernelone.context.receipt_store" in modules
    assert "polaris.kernelone.context.context_os.receipt_store" not in modules


def test_contextos_module_classification_has_no_packaged_manifest_copy() -> None:
    assert not PACKAGED_MANIFEST.exists()


def test_ruff_keeps_hot_governance_scripts_in_lint_scope() -> None:
    payload = tomllib.loads(RUFF_CONFIG.read_text(encoding="utf-8"))
    excludes = {str(item) for item in payload.get("exclude", [])}

    assert "src/backend/docs/governance/ci/scripts/**" not in excludes


def test_contextos_full_landing_card_uses_executable_replay_entrypoint() -> None:
    payload = yaml.safe_load(CONTEXTOS_FULL_LANDING_CARD.read_text(encoding="utf-8")) or {}
    commands = [
        str(item.get("command") or "")
        for item in payload.get("verification_plan", {}).get("integration_tests", [])
        if isinstance(item, dict)
    ]
    replay_commands = [
        command for command in commands if "contextos_replay" in command or "run_contextos_replay" in command
    ]

    assert replay_commands
    assert all("run_contextos_replay.py" not in command for command in replay_commands)
    assert any("python -m polaris.delivery.cli.tools.contextos_replay" in command for command in replay_commands)


def test_contextos_landing_docs_do_not_reference_missing_replay_script() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in ACTIVE_CONTEXTOS_LANDING_DOCS
        if "run_contextos_replay.py" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_contextos_module_classification_diagnostics_reports_missing_canonical_manifest(
    monkeypatch,
    tmp_path,
) -> None:
    from polaris.kernelone.context.context_os import module_classification

    monkeypatch.setattr(module_classification, "_default_manifest_path", lambda: tmp_path / "missing.json")

    diagnostics = module_classification.get_contextos_module_classification_diagnostics()

    assert diagnostics["state"] == "manifest_unavailable"
    assert diagnostics["ok"] is False
    assert diagnostics["details"]["manifest_path"] == str(tmp_path / "missing.json")
    assert diagnostics["evidence"] == [str(tmp_path / "missing.json")]
