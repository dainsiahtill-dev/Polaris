from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from polaris.delivery.http.adapters.scripts_pm import ScriptsPMAdapter
from pytest import MonkeyPatch


def test_load_pm_module_ignores_package_level_placeholder(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    import polaris.delivery.cli.pm as pm_package

    monkeypatch.setattr(pm_package, "pm_integration", None, raising=False)

    module = ScriptsPMAdapter(tmp_path)._load_pm_module()

    assert isinstance(module, ModuleType)
    assert module.__name__ == "polaris.delivery.cli.pm.pm_integration"
    assert callable(getattr(module, "get_pm", None))


def test_get_pm_recovers_from_poisoned_module_cache(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    module_name = "polaris.delivery.cli.pm.pm_integration"
    poisoned_module = ModuleType(module_name)
    monkeypatch.setitem(sys.modules, module_name, poisoned_module)

    pm = ScriptsPMAdapter(tmp_path).get_pm()

    assert pm.__class__.__name__ == "PM"
    assert str(pm.workspace) == str(tmp_path.resolve())


def test_get_pm_falls_back_to_pm_class_when_get_pm_missing(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    class DummyPM:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

    class DummyModule:
        PM = DummyPM

    module = DummyModule()
    adapter = ScriptsPMAdapter(tmp_path)
    monkeypatch.setattr(adapter, "_load_pm_module", lambda: module)

    pm = adapter.get_pm()

    assert isinstance(pm, DummyPM)
    assert pm.workspace == str(tmp_path.resolve())
