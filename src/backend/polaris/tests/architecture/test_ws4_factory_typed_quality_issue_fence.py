"""Architecture fence for WS4 typed QualityIssue in Factory repair.

Factory workspace-quality repair must not make the Director runtime infer typed
repair diagnostics solely from display strings. The production boundary is:
workspace scan emits typed issue evidence, Factory aligns that evidence to the
current display-error list with KernelOne helpers, then coverage / plan probes
consume the aligned payloads.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FACTORY_STAGE_EXECUTOR = (
    _BACKEND_ROOT / "polaris" / "cells" / "factory" / "pipeline" / "internal" / "factory_stage_executor"
)


def _parse_factory_stage_executor() -> ast.Module:
    paths = sorted(_FACTORY_STAGE_EXECUTOR.glob("*.py"))
    assert paths, f"Missing canonical Factory executor sources: {_FACTORY_STAGE_EXECUTOR}"
    body = []
    for path in paths:
        body.extend(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body)
    return ast.Module(body=body, type_ignores=[])


def _find_method(tree: ast.Module, method_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            return node
    raise AssertionError(f"{method_name} was not found in {_FACTORY_STAGE_EXECUTOR}")


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            names.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def _imported_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom | ast.Import):
            names.update(alias.asname or alias.name for alias in child.names)
    return names


def test_factory_issue_payload_helper_owns_typed_quality_issue_bridge() -> None:
    method = _find_method(_parse_factory_stage_executor(), "_workspace_quality_repair_issue_payloads")
    names = _called_names(method) | _imported_names(method)

    assert "scan_workspace_artifact_quality_evidence" in names
    assert "artifact_quality_issues_for_errors" in names
    assert "artifact_quality_issues_from_errors" in names


def test_factory_coverage_and_plan_probe_consume_issue_payload_helper() -> None:
    tree = _parse_factory_stage_executor()
    for method_name in (
        "_workspace_quality_repair_coverage_report",
        "_workspace_quality_repair_plan_probe_report",
    ):
        names = _called_names(_find_method(tree, method_name))
        assert "_workspace_quality_repair_issue_payloads" in names
        assert "artifact_quality_issues_from_errors" not in names


def test_factory_executor_does_not_import_private_artifact_quality_rebuilders() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(_FACTORY_STAGE_EXECUTOR.glob("*.py")))

    assert "_artifact_quality_issue_from_error" not in source
    assert "_artifact_quality_issues_from_errors" not in source


def test_quality_issue_fence_detects_active_mixin_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _BACKEND_ROOT / "polaris/cells/factory/pipeline/internal/factory_stage_executor/_mixin_02.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        return (
            source.replace("artifact_quality_issues_for_errors", "untyped_display_rebuilder")
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", mutated)
    with pytest.raises(AssertionError, match="artifact_quality_issues_for_errors"):
        test_factory_issue_payload_helper_owns_typed_quality_issue_bridge()
