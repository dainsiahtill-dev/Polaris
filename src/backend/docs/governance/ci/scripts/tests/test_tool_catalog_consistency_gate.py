"""Tests for tool catalog consistency governance edge cases."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

from docs.governance.ci.scripts.run_tool_catalog_consistency_gate import _check_yaml_profiles


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
