"""Regression tests for loop-director DirectorConfig construction (TS-2).

The CLI module ``polaris/delivery/cli/loop-director.py`` previously passed an
unsupported ``task_poll_interval`` kwarg to ``DirectorConfig(...)``, which would
raise ``TypeError`` the moment ``async_main`` ran.  These tests load the
hyphenated module by path (``python -m`` import is blocked by the dash) and
assert the config is now constructed with only fields the dataclass exposes.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_loop_director_module() -> ModuleType:
    """Load the hyphenated ``loop-director.py`` module by file path.

    Resolve the path relative to the ``polaris`` package (the shared ancestor of
    this test and the target module) so it stays correct regardless of repo depth.
    """
    polaris_root = Path(__file__).resolve().parents[1]
    module_path = polaris_root / "delivery" / "cli" / "loop-director.py"
    if not module_path.is_file():
        raise RuntimeError(f"loop-director.py not found at {module_path}")
    spec = importlib.util.spec_from_file_location("loop_director_config_construction", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_director_config_has_no_task_poll_interval_field() -> None:
    """DirectorConfig must not expose ``task_poll_interval`` (root cause guard)."""
    # Arrange
    loop_director = _load_loop_director_module()

    # Act
    field_names = {f.name for f in dataclasses.fields(loop_director.DirectorConfig)}

    # Assert
    assert "task_poll_interval" not in field_names
    assert {"workspace", "max_workers", "enable_nag", "enable_auto_compact", "token_budget"} <= field_names


@pytest.mark.asyncio
async def test_async_main_builds_director_config_without_type_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """async_main must construct DirectorConfig without raising TypeError."""
    # Arrange
    loop_director = _load_loop_director_module()
    captured: dict[str, Any] = {}

    async def _fake_run(self: Any, *, pm_task_path: str, iterations: int, timeout: int) -> dict[str, Any]:
        captured["config"] = self.config
        return {"success": True, "tasks_executed": 1, "files_created": [], "errors": []}

    monkeypatch.setattr(loop_director.DirectorV2Runner, "run", _fake_run)

    args = argparse.Namespace(
        workspace="/tmp/ws",
        pm_task_path=str(Path(__file__)),  # an existing file so the path guard passes
        iterations=1,
        timeout=600,
        director_result_path=None,
    )

    # Act
    exit_code = await loop_director.async_main(args)

    # Assert
    config = captured["config"]
    assert isinstance(config, loop_director.DirectorConfig)
    assert config.workspace == "/tmp/ws"
    assert config.token_budget is None
    assert exit_code == 0
