"""Canonical Factory/observer packages keep their real execution bodies and public identities."""

from __future__ import annotations

import importlib
import inspect
from importlib.machinery import PathFinder
from pathlib import Path

import pytest
from polaris.kernelone.platform_modules.registry import get_module

BACKEND_ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize(
    "relative",
    (
        "polaris/cells/factory/pipeline/internal/bench_gates",
        "polaris/cells/factory/pipeline/internal/factory_stage_executor",
        "polaris/tests/agent_stress/observer",
    ),
)
def test_normal_resolution_selects_the_canonical_package(relative: str) -> None:
    package = BACKEND_ROOT / relative
    spec = PathFinder.find_spec(package.name, [str(package.parent)])
    assert spec is not None
    assert Path(spec.origin or "").resolve() == package / "__init__.py"
    assert list(spec.submodule_search_locations or ()) == [str(package)]


def test_factory_facades_share_canonical_objects_and_registered_method_bodies() -> None:
    prefix = "polaris.cells.factory.pipeline.internal"
    bench = importlib.import_module(f"{prefix}.bench_gates")
    gates = importlib.import_module(f"{prefix}.bench_gates._gates")
    stage = importlib.import_module(f"{prefix}.factory_stage_executor")
    implementation = importlib.import_module(f"{prefix}.factory_stage_executor._executor")
    assert bench.build_real_run_gate is gates.build_real_run_gate
    assert bench.resolve_expected_llm_bindings is gates.resolve_expected_llm_bindings
    assert stage.OrchestrationStageExecutor is implementation.OrchestrationStageExecutor
    repo = BACKEND_ROOT.parents[1]
    owned = set(get_module("M06_director_multi_task").owner_paths) & set(
        get_module("M07_factory_stage_chain").owner_paths
    )
    for method in (
        "execute",
        "_resolve_director_binding_fanout",
        "_workspace_quality_repair_issue_payloads",
        "_run_workspace_quality_checks",
        "_active_director_task_barrier_result",
    ):
        source = inspect.getsourcefile(getattr(stage.OrchestrationStageExecutor, method))
        assert source is not None
        path = Path(source).resolve()
        stage_file = stage.__file__
        assert stage_file is not None
        assert path.parent == Path(stage_file).parent
        assert path.name.startswith("_mixin_")
        assert path.relative_to(repo).as_posix() in owned


def test_observer_facade_keeps_canonical_public_objects() -> None:
    observer = importlib.import_module("polaris.tests.agent_stress.observer")
    main = importlib.import_module("polaris.tests.agent_stress.observer.main")
    projection = importlib.import_module("polaris.tests.agent_stress.observer.projection")
    assert observer.main is main.main
    assert observer.build_observer_parser is main.build_observer_parser
    assert observer.RuntimeProjection is projection.RuntimeProjection
    assert callable(observer.observe_runner)
