"""Tests for tool catalog consistency governance edge cases."""

from __future__ import annotations

import builtins
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest
from docs.governance.ci.scripts.run_tool_catalog_consistency_gate import (
    _check_yaml_profiles,
    _extract_tool_specs_from_registry,
)


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a UTF-8 Python source fixture."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@contextmanager
def _isolated_tool_registry_imports() -> Iterator[None]:
    """Contain the synthetic registry's package subtree and temporary import path."""
    root = "polaris.kernelone.tool_execution"

    def owned(name: str) -> bool:
        return name in {"polaris", "polaris.kernelone", root} or name.startswith(f"{root}.")

    modules = {name: module for name, module in sys.modules.copy().items() if owned(name)}
    path_object, path_contents = sys.path, list(sys.path)
    sys.path = list(path_contents)
    for name in modules:
        sys.modules.pop(name, None)
    try:
        yield
    finally:
        # New fixture children must disappear as well as its four entry modules.
        for name in list(sys.modules):
            if owned(name):
                sys.modules.pop(name, None)
        sys.modules.update(modules)
        path_object[:] = path_contents
        sys.path = path_object


def test_tool_specs_load_from_registry_backend_root(tmp_path: Path, monkeypatch: Any) -> None:
    """Tool specs must load from ToolSpecRegistry using the backend root path."""
    _write_source(tmp_path, "polaris/__init__.py", "")
    _write_source(tmp_path, "polaris/kernelone/__init__.py", "")
    _write_source(tmp_path, "polaris/kernelone/tool_execution/__init__.py", "")
    _write_source(tmp_path, "polaris/kernelone/tool_execution/fixture_helper.py", 'ALIASES = ["list_directory"]\n')
    _write_source(
        tmp_path,
        "polaris/kernelone/tool_execution/tool_spec_registry.py",
        """
from .fixture_helper import ALIASES

class ToolSpecRegistry:
    @staticmethod
    def get_all_specs():
        return {"repo_tree": {"aliases": ALIASES}}
""",
    )

    with _isolated_tool_registry_imports():
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

    with _isolated_tool_registry_imports():
        result = _extract_tool_specs_from_registry(tmp_path)

    assert result.specs == {}
    assert "registry unavailable" in result.error


@pytest.mark.parametrize("preloaded", (False, True))
@pytest.mark.parametrize(
    "fixture_test",
    (test_tool_specs_load_from_registry_backend_root, test_tool_specs_do_not_fallback_when_registry_import_fails),
)
def test_registry_fixture_restores_import_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_test: Callable[[Path, Any], None],
    preloaded: bool,
) -> None:
    """A registry fixture must not change another test's package cache or search path."""
    names = (
        "polaris",
        "polaris.kernelone",
        "polaris.kernelone.tool_execution",
        "polaris.kernelone.tool_execution.tool_spec_registry",
        "polaris.kernelone.tool_execution.original_child",
        "polaris.kernelone.tool_execution.fixture_helper",
    )
    if preloaded:
        for name in names[:-1]:
            module = ModuleType(name)
            module.__spec__ = ModuleSpec(name, loader=None, is_package=True)
            module.__path__ = [f"preexisting/{name}"]
            monkeypatch.setitem(sys.modules, name, module)
    unrelated = ModuleType("governance_unrelated_import_probe")
    monkeypatch.setitem(sys.modules, unrelated.__name__, unrelated)
    absent = object()
    before = {name: sys.modules.get(name, absent) for name in names}
    original_metadata = {
        name: (module.__spec__, getattr(module, "__path__", None), tuple(getattr(module, "__path__", ())))
        for name, module in before.items()
        if isinstance(module, ModuleType)
    }
    path_object, path_contents = sys.path, tuple(sys.path)

    with pytest.MonkeyPatch.context() as fixture_patch:
        fixture_test(tmp_path, fixture_patch)

    assert sys.path is path_object
    assert tuple(sys.path) == path_contents
    for name, expected_module in before.items():
        assert sys.modules.get(name, absent) is expected_module, f"fixture changed import cache entry {name}"
    for name, (spec, path, contents) in original_metadata.items():
        assert sys.modules[name].__spec__ is spec
        assert getattr(sys.modules[name], "__path__", None) is path
        assert tuple(getattr(sys.modules[name], "__path__", ())) == contents
    assert sys.modules[unrelated.__name__] is unrelated


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
