from __future__ import annotations

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
