from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.bootstrap.governance import architecture_guard_cli as guard_module
from polaris.bootstrap.governance.architecture_guard_cli import (
    ExternalPluginArchitectureGuard,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _create_minimal_plugin(plugin_root: Path) -> None:
    _write_text(
        plugin_root / "plugin.yaml",
        """manifest_version: 1
plugin_id: vendor.example.demo
display_name: Demo Plugin
publisher: Example Inc
plugin_version: 0.1.0
cell_id: vendor.demo
cell_manifest: cell.yaml
sdk:
  version: ">=1.0.0,<2.0.0"
  entrypoint: plugin.main:register
  python: ">=3.12,<3.15"
runtime:
  process_model: isolated_process
  ipc: stdio_jsonrpc
  default_enabled: false
capabilities:
  tokens:
    - fs.read:workspace/docs/**
verification:
  verify_pack: generated/verify.pack.json
  tests:
    - tests/test_smoke.py
distribution:
  sbom: generated/sbom.json
  signature: generated/plugin.sig
""",
    )
    _write_text(
        plugin_root / "cell.yaml",
        """id: vendor.demo
title: Demo Plugin Cell
kind: capability
visibility: public
stateful: false
owner: external-vendor
purpose: Demo external cell plugin for governance checks.
owned_paths:
  - plugin/**
  - tests/**
  - generated/**
public_contracts:
  modules:
    - plugin.public.contracts
  commands: []
  queries: []
  events: []
  results: []
  errors: []
depends_on: []
state_owners: []
effects_allowed:
  - fs.read:workspace/docs/**
verification:
  tests:
    - tests/test_smoke.py
  gaps: []
""",
    )
    _write_text(
        plugin_root / "plugin" / "main.py",
        """from __future__ import annotations

from polaris.kernelone.sdk.runtime import PluginContext, PluginRegistration


def register(context: PluginContext) -> PluginRegistration:
    return PluginRegistration(
        plugin_id="vendor.example.demo",
        cell_id="vendor.demo",
        commands={},
        queries={},
        event_subscriptions=[],
    )
""",
    )
    _write_text(
        plugin_root / "plugin" / "public" / "contracts.py",
        "from __future__ import annotations\n",
    )
    _write_text(plugin_root / "tests" / "test_smoke.py", "def test_smoke() -> None:\n    assert True\n")
    _write_json(plugin_root / "generated" / "verify.pack.json", {"version": 1})
    _write_json(plugin_root / "generated" / "sbom.json", {"name": "demo"})
    _write_text(plugin_root / "generated" / "plugin.sig", "signature")


@pytest.mark.skipif(guard_module.yaml is None, reason="PyYAML unavailable")
def test_external_plugin_guard_passes_minimal_valid_package(tmp_path: Path) -> None:
    plugin_root = tmp_path / "vendor.example.demo"
    _create_minimal_plugin(plugin_root)

    guard = ExternalPluginArchitectureGuard(plugin_root=plugin_root, mode="hard-fail")
    report = guard.run()

    assert report.exit_code == 0
    assert report.issue_count == 0


@pytest.mark.skipif(guard_module.yaml is None, reason="PyYAML unavailable")
def test_external_plugin_guard_blocks_internal_imports(tmp_path: Path) -> None:
    plugin_root = tmp_path / "vendor.example.demo"
    _create_minimal_plugin(plugin_root)
    _write_text(
        plugin_root / "plugin" / "internal" / "service.py",
        """from __future__ import annotations

from polaris.cells.runtime.projection.internal.runtime_v2 import RuntimeEnvelopeV2
""",
    )

    guard = ExternalPluginArchitectureGuard(plugin_root=plugin_root, mode="hard-fail")
    report = guard.run()

    assert report.exit_code == 1
    assert any(
        issue.check_id == "import_fence.forbidden_internal_cell_import"
        for issue in report.issues
    )


@pytest.mark.skipif(guard_module.yaml is None, reason="PyYAML unavailable")
def test_external_plugin_guard_fail_on_new_respects_baseline(tmp_path: Path) -> None:
    plugin_root = tmp_path / "vendor.example.demo"
    _create_minimal_plugin(plugin_root)
    _write_text(
        plugin_root / "plugin" / "internal" / "legacy.py",
        """from __future__ import annotations

from polaris.application.utils import helper
""",
    )

    baseline = tmp_path / "baseline.json"
    bootstrap_guard = ExternalPluginArchitectureGuard(plugin_root=plugin_root, mode="audit-only")
    bootstrap_report = bootstrap_guard.run()
    bootstrap_guard.write_baseline(baseline, bootstrap_report)

    no_new_guard = ExternalPluginArchitectureGuard(
        plugin_root=plugin_root,
        mode="fail-on-new",
        baseline_path=baseline,
    )
    no_new_report = no_new_guard.run()
    assert no_new_report.exit_code == 0
    assert no_new_report.new_issue_count == 0

    _write_text(
        plugin_root / "plugin" / "internal" / "more_legacy.py",
        """from __future__ import annotations

from polaris.infrastructure.storage import LocalFileSystemAdapter
""",
    )

    new_violation_guard = ExternalPluginArchitectureGuard(
        plugin_root=plugin_root,
        mode="fail-on-new",
        baseline_path=baseline,
    )
    new_violation_report = new_violation_guard.run()
    assert new_violation_report.exit_code == 1
    assert new_violation_report.new_issue_count > 0

