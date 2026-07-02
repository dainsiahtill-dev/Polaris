"""Tests for tool catalog consistency governance edge cases."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from typing import Any

from docs.governance.ci.scripts.run_tool_catalog_consistency_gate import (
    _check_yaml_profiles,
    _extract_tool_specs_from_registry,
)


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a UTF-8 Python source fixture."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _clear_fake_tool_registry_modules(monkeypatch: Any) -> None:
    """Remove cached polaris modules that would bypass a temp registry fixture."""
    module_names = (
        "polaris",
        "polaris.kernelone",
        "polaris.kernelone.tool_execution",
        "polaris.kernelone.tool_execution.tool_spec_registry",
    )
    for module_name in module_names:
        monkeypatch.delitem(sys.modules, module_name, raising=False)


def test_tool_specs_load_from_registry_backend_root(tmp_path: Path, monkeypatch: Any) -> None:
    """Tool specs must load from ToolSpecRegistry using the backend root path."""
    _clear_fake_tool_registry_modules(monkeypatch)
    _write_source(tmp_path, "polaris/__init__.py", "")
    _write_source(tmp_path, "polaris/kernelone/__init__.py", "")
    _write_source(tmp_path, "polaris/kernelone/tool_execution/__init__.py", "")
    _write_source(
        tmp_path,
        "polaris/kernelone/tool_execution/tool_spec_registry.py",
        """
class ToolSpecRegistry:
    @staticmethod
    def get_all_specs():
        return {"repo_tree": {"aliases": ["list_directory"]}}
""",
    )

    result = _extract_tool_specs_from_registry(tmp_path)

    assert result.error == ""
    assert result.specs == {"repo_tree": {"aliases": ["list_directory"]}}


def test_tool_specs_do_not_fallback_when_registry_import_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Registry import failures must be observable instead of using legacy text fallback."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "polaris.kernelone.tool_execution.tool_spec_registry":
            raise ImportError("registry unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = _extract_tool_specs_from_registry(tmp_path)

    assert result.specs == {}
    assert "registry unavailable" in result.error


def test_yaml_profiles_fail_closed_when_yaml_dependency_is_missing(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Missing PyYAML must be a structured gate issue, not a silent skip."""
    yaml_path = tmp_path / "core_roles.yaml"
    yaml_path.write_text("roles: []\n", encoding="utf-8")
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("yaml unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    issues = _check_yaml_profiles(yaml_path, set(), {})

    assert len(issues) == 1
    assert issues[0].category == "yaml_dependency_missing"
    assert issues[0].severity == "error"
    assert issues[0].file == str(yaml_path)


def test_yaml_profiles_report_parse_errors(tmp_path: Path) -> None:
    """Invalid YAML must become a structured gate issue."""
    yaml_path = tmp_path / "core_roles.yaml"
    yaml_path.write_text("roles:\n  - [unterminated\n", encoding="utf-8")

    issues = _check_yaml_profiles(yaml_path, set(), {})

    assert len(issues) == 1
    assert issues[0].category == "yaml_profile_parse_error"
    assert issues[0].severity == "error"
    assert issues[0].file == str(yaml_path)


def test_yaml_profiles_reports_aliases(tmp_path: Path) -> None:
    """Valid YAML profiles still report alias use as a warning."""
    yaml_path = tmp_path / "core_roles.yaml"
    yaml_path.write_text(
        """
roles:
  - role_id: director
    tool_policy:
      whitelist:
        - grep
""",
        encoding="utf-8",
    )

    issues = _check_yaml_profiles(yaml_path, {"repo_rg"}, {"grep": "repo_rg"})

    assert len(issues) == 1
    assert issues[0].category == "alias_in_whitelist"
    assert issues[0].severity == "warning"
    assert issues[0].evidence["canonical"] == "repo_rg"
