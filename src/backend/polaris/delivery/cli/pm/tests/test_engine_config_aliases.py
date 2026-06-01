from __future__ import annotations

from types import SimpleNamespace

from polaris.delivery.cli.pm.engine.core import EngineRuntimeConfig
from polaris.delivery.cli.pm.orchestration_engine import _merge_engine_config
from polaris.delivery.cli.pm.tasks import normalize_engine_config


def test_engine_runtime_config_honors_desktop_workflow_parallel_args() -> None:
    args = SimpleNamespace(
        director_workflow_execution_mode="parallel",
        director_max_parallel_tasks=3,
        director_scheduling_policy="dag",
    )

    config = EngineRuntimeConfig.from_sources(args, None)

    assert config.director_execution_mode == "multi"
    assert config.max_directors == 3
    assert config.scheduling_policy == "dag"


def test_engine_runtime_config_maps_desktop_serial_to_single() -> None:
    args = SimpleNamespace(
        director_workflow_execution_mode="serial",
        director_max_parallel_tasks=3,
    )

    config = EngineRuntimeConfig.from_sources(args, None)

    assert config.director_execution_mode == "single"
    assert config.max_directors == 1


def test_normalize_engine_config_accepts_workflow_payload_aliases() -> None:
    normalized = normalize_engine_config(
        {
            "director_workflow_execution_mode": "parallel",
            "director_max_parallel_tasks": 4,
            "scheduling_policy": "dag",
        }
    )

    assert normalized == {
        "director_execution_mode": "multi",
        "max_directors": 4,
        "scheduling_policy": "dag",
    }


def test_orchestration_merge_persists_parallel_as_multi() -> None:
    args = SimpleNamespace(
        director_workflow_execution_mode="parallel",
        director_max_parallel_tasks=3,
        director_scheduling_policy="dag",
    )

    merged = _merge_engine_config(None, args)

    assert merged == {
        "director_execution_mode": "multi",
        "max_directors": 3,
        "scheduling_policy": "dag",
    }
