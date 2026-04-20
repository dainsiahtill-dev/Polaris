from __future__ import annotations

from types import SimpleNamespace

import config
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.roles.runtime.public.service import SequentialMode


def test_sequential_config_respects_env_when_settings_lacks_seq_fields(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setenv("POLARIS_SEQ_ENABLED", "0")

    adapter = DirectorAdapter(workspace=str(tmp_path))
    assert adapter._get_sequential_config() is None


def test_sequential_config_reads_budget_and_mode_from_env_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setenv("POLARIS_SEQ_ENABLED", "1")
    monkeypatch.setenv("POLARIS_SEQ_DEFAULT_MODE", "required")
    monkeypatch.setenv("POLARIS_SEQ_DEFAULT_ROLES", "director,adaptive")
    monkeypatch.setenv("POLARIS_SEQ_MAX_STEPS", "5")
    monkeypatch.setenv("POLARIS_SEQ_MAX_TOOL_CALLS_TOTAL", "8")
    monkeypatch.setenv("POLARIS_SEQ_MAX_NO_PROGRESS_STEPS", "2")
    monkeypatch.setenv("POLARIS_SEQ_MAX_WALL_TIME_SECONDS", "45")
    monkeypatch.setenv("POLARIS_SEQ_TRACE_LEVEL", "detailed")

    adapter = DirectorAdapter(workspace=str(tmp_path))
    cfg = adapter._get_sequential_config()

    assert cfg is not None
    assert cfg["mode"] == SequentialMode.REQUIRED
    assert cfg["budget"].max_steps == 5
    assert cfg["budget"].max_tool_calls_total == 8
    assert cfg["budget"].max_no_progress_steps == 2
    assert cfg["budget"].max_wall_time_seconds == 45
    assert cfg["trace_level"].value == "detailed"


def test_sequential_mode_context_override_wins_over_default_mode(
    tmp_path,
    monkeypatch,
) -> None:
    settings = SimpleNamespace(
        seq_enabled=True,
        seq_default_mode="required",
        seq_default_roles="director,adaptive",
        seq_max_steps=4,
        seq_max_tool_calls_total=6,
        seq_max_no_progress_steps=2,
        seq_max_wall_time_seconds=30,
        seq_trace_level="summary",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    adapter = DirectorAdapter(workspace=str(tmp_path))
    cfg = adapter._get_sequential_config({"sequential_mode": "disabled"})

    assert cfg is not None
    assert cfg["mode"] == SequentialMode.DISABLED
