from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairStrategyCatalogV1,
    query_director_repair_strategy_catalog,
)
from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge
from polaris.cells.roles.adapters.internal.director.execute_method import _execution_attempt_identity_from_context
from polaris.cells.roles.adapters.internal.director.post_execution_repair_bridge import (
    run_cpp_post_repairs_as_tool_results,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _deterministic_single_missing_python_module_alias_to_write_file,
    _deterministic_single_missing_quality_repair_to_write_file,
)
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    run_runtime_repair_with_director_tools,
)
from polaris.cells.roles.kernel.public import DeferredDirectorRepairRequestV1
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptIdentityV1,
    create_task_runtime_execution_attempt_authority,
)


def _typescript_import_specifier_source_tool() -> str:
    items = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1()).to_dict()["items"]
    return str(
        next(
            item["source_tool"]
            for item in items
            if item["source_tool"] == "deterministic_typescript_import_specifier_keyword_repair"
        )
    )


def _attempt(workspace: Path, *, task_id: str = "task-deferred-repair") -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace=workspace.resolve().as_posix(),
        task_id=71,
        external_task_id=task_id,
        session_id="session-deferred-repair",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-deferred-repair",
        lease_expires_at="2099-01-01T00:00:00Z",
    )


def _attempt_context(workspace: Path, *, task_id: str) -> dict[str, Any]:
    return {
        "task_runtime_execution_attempt_authority": create_task_runtime_execution_attempt_authority(
            _attempt(workspace, task_id=task_id)
        )
    }


def test_quality_requirements_repair_is_deferred_without_physical_write(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    source = workspace / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("import requests\n", encoding="utf-8")

    results = _deterministic_single_missing_quality_repair_to_write_file(
        SimpleNamespace(workspace=workspace),
        task_id="task-quality-requirements",
        repair_target_files=["requirements.txt"],
        artifact_quality_errors=["requirements.txt must declare requests"],
        context=_attempt_context(workspace, task_id="task-quality-requirements"),
        base_file_candidates=["src/main.py"],
    )

    assert not (workspace / "requirements.txt").exists()
    assert len(results) == 1
    assert results[0]["tool_name"] == "deferred_director_repair"
    payload = results[0]["result"]
    assert payload["status"] == "deferred_repair_effects_pending"
    assert payload["source_tool"] == "deterministic_runtime_dependency_repair"
    assert payload["allowed_paths"] == ["requirements.txt"]
    request = payload["deferred_request"]
    assert isinstance(request, DeferredDirectorRepairRequestV1)
    assert request.execution_attempt.external_task_id == "task-quality-requirements"
    assert [effect.target_path for effect in request.plan.effects if effect.contingency_kind == "forward"] == [
        "requirements.txt"
    ]


def test_quality_requirements_repair_without_attempt_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    source = workspace / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("import requests\n", encoding="utf-8")

    results = _deterministic_single_missing_quality_repair_to_write_file(
        SimpleNamespace(workspace=workspace),
        task_id="task-quality-no-attempt",
        repair_target_files=["requirements.txt"],
        artifact_quality_errors=["requirements.txt must declare requests"],
        context={},
        base_file_candidates=["src/main.py"],
    )

    assert not (workspace / "requirements.txt").exists()
    assert len(results) == 1
    assert results[0]["success"] is False
    assert results[0]["result"]["error_code"] == "deo_deferred_repair_attempt_required"


def test_quality_python_module_alias_repair_defers_governed_runtime_plan(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    source = workspace / "src" / "models" / "weather.py"
    source.parent.mkdir(parents=True)
    source.write_text("class Weather:\n    pass\n", encoding="utf-8")
    target = workspace / "src" / "weather.py"

    results = _deterministic_single_missing_python_module_alias_to_write_file(
        SimpleNamespace(workspace=workspace),
        task_id="task-quality-python-alias",
        repair_target_files=["src/weather.py"],
        artifact_quality_errors=["ModuleNotFoundError: No module named 'weather'"],
        context=_attempt_context(workspace, task_id="task-quality-python-alias"),
        base_file_candidates=["src/models/weather.py"],
    )

    assert not target.exists()
    assert len(results) == 1
    assert results[0]["tool_name"] == "deferred_director_repair"
    assert results[0]["success"] is True
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_python_missing_module_alias_repair"
    assert payload["allowed_paths"] == ["src/weather.py"]
    request = payload["deferred_request"]
    assert isinstance(request, DeferredDirectorRepairRequestV1)
    assert request.execution_attempt.external_task_id == "task-quality-python-alias"
    assert [effect.target_path for effect in request.plan.effects if effect.contingency_kind == "forward"] == [
        "src/weather.py"
    ]


def test_quality_python_module_alias_repair_rejects_ambiguous_workspace_candidates(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    for rel_path in ("src/models/weather.py", "src/legacy/weather.py"):
        source = workspace / rel_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("class Weather:\n    pass\n", encoding="utf-8")

    results = _deterministic_single_missing_python_module_alias_to_write_file(
        SimpleNamespace(workspace=workspace),
        task_id="task-quality-python-alias-ambiguous",
        repair_target_files=["src/weather.py"],
        artifact_quality_errors=["ModuleNotFoundError: No module named 'weather'"],
        context=_attempt_context(workspace, task_id="task-quality-python-alias-ambiguous"),
        base_file_candidates=[],
    )

    assert not (workspace / "src" / "weather.py").exists()
    assert len(results) == 1
    assert results[0]["success"] is False
    payload = results[0]["result"]
    assert payload["error_code"] == "python_module_alias_candidate_ambiguous"
    assert payload["candidate_paths"] == ["src/legacy/weather.py", "src/models/weather.py"]
    assert "deferred_request" not in payload


def test_cpp_post_repair_without_director_adapter_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    target = workspace / "src" / "engine" / "generator.cpp"
    target.parent.mkdir(parents=True)
    original = '#include "src/models/postcard.hpp"\n#include <string>\n'
    target.write_text(original, encoding="utf-8")

    results = run_cpp_post_repairs_as_tool_results(
        workspace,
        adapter=None,
        task_id="task-without-adapter",
    )

    assert target.read_text(encoding="utf-8") == original
    assert len(results) == 1
    assert results[0]["success"] is False
    payload = results[0]["result"]
    assert payload["ok"] is False
    assert payload["source_tool"] == "deterministic_cpp_post_repair"
    assert payload["error_code"] == "director_adapter_required_for_policy_gated_repair"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["direct_write_allowed"] is False
    assert payload["repair_kernel"]["writer_boundary"] == "director_tool_executor_required"


def test_runtime_repair_bridge_returns_one_deferred_request_without_executor_or_write(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    target = workspace / "src" / "models" / "Market.ts"
    target.parent.mkdir(parents=True)
    original = 'import {\n  Reputation,\n  export type ReputationTier,\n} from "./Reputation";\n'
    target.write_text(original, encoding="utf-8")

    class Adapter:
        pass

    adapter = Adapter()
    adapter.workspace = workspace

    assert "executor_factory" not in inspect.signature(run_runtime_repair_with_director_tools).parameters

    results = run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace,
        task_id="task-deferred-repair",
        source_tool=_typescript_import_specifier_source_tool(),
        execution_attempt=_attempt(workspace),
        base_files={"src/models/Market.ts": original},
        artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
        allowed_paths=("src/models/Market.ts",),
        max_rounds=1,
    )

    assert target.read_text(encoding="utf-8") == original
    assert len(results) == 1
    assert results[0]["tool_name"] == "deferred_director_repair"
    assert results[0]["success"] is True
    payload = results[0]["result"]
    assert payload["ok"] is True
    assert payload["status"] == "deferred_repair_effects_pending"
    assert payload["repair_applied"] is False
    request = payload["deferred_request"]
    assert type(request) is DeferredDirectorRepairRequestV1
    assert request.execution_attempt == _attempt(workspace)
    assert request.plan.source_tool == _typescript_import_specifier_source_tool()
    assert request.allowed_paths == ("src/models/Market.ts",)
    assert request.plan.effects[0].tool_name == "edit_file"
    assert request.plan.effects[0].target_path == "src/models/Market.ts"


def test_runtime_repair_bridge_rejects_multi_round_convergence_before_planning(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    results = run_runtime_repair_with_director_tools(
        object(),
        workspace_path=workspace,
        task_id="task-deferred-repair",
        source_tool=_typescript_import_specifier_source_tool(),
        execution_attempt=_attempt(workspace),
        base_files={"src/models/Market.ts": "export const market = true;\n"},
        convergence_verifier=lambda _value: True,
        max_rounds=2,
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    assert results[0]["result"]["error_code"] == "deo_multi_round_repair_requires_receipt_close"


def test_runtime_repair_bridge_binds_external_task_id_when_caller_passes_private_row_id(
    tmp_path: Path,
) -> None:
    """Live factory passes board/pm task ids; deferred request must bind external_task_id.

    R78: create_deferred with caller task_id raised
    ``task_id must match execution_attempt external_task_id`` into Director
    runtime (tools_executed=0). Private row id must be accepted as caller input
    but the typed request always binds external_task_id.
    """

    workspace = tmp_path.resolve()
    target = workspace / "src" / "models" / "Market.ts"
    target.parent.mkdir(parents=True)
    original = 'import {\n  Reputation,\n  export type ReputationTier,\n} from "./Reputation";\n'
    target.write_text(original, encoding="utf-8")
    attempt = _attempt(workspace, task_id="1")  # external_task_id="1", private task_id=71

    results = run_runtime_repair_with_director_tools(
        object(),
        workspace_path=workspace,
        task_id=str(attempt.task_id),  # private row id form used by some callers
        source_tool=_typescript_import_specifier_source_tool(),
        execution_attempt=attempt,
        base_files={"src/models/Market.ts": original},
        artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
        allowed_paths=("src/models/Market.ts",),
        max_rounds=1,
    )

    assert len(results) == 1
    assert results[0]["success"] is True
    request = results[0]["result"]["deferred_request"]
    assert type(request) is DeferredDirectorRepairRequestV1
    assert request.task_id == attempt.external_task_id == "1"
    assert request.execution_attempt.task_id == 71
    assert "must match execution_attempt" not in str(results[0])


@pytest.mark.parametrize("requested_task_id", ["TASK-1", "task-1"])
def test_runtime_repair_bridge_accepts_only_bound_task_aliases(
    tmp_path: Path,
    requested_task_id: str,
) -> None:
    workspace = tmp_path.resolve()
    attempt = _attempt(workspace, task_id="1")
    results = run_runtime_repair_with_director_tools(
        object(),
        workspace_path=workspace,
        task_id=requested_task_id,
        source_tool=_typescript_import_specifier_source_tool(),
        execution_attempt=attempt,
        base_files={
            "src/models/Market.ts": ('import {\n  Reputation,\n  export type ReputationTier,\n} from "./Reputation";\n')
        },
        artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
        allowed_paths=("src/models/Market.ts",),
        max_rounds=1,
    )

    assert len(results) == 1
    assert results[0]["success"] is True
    request = results[0]["result"]["deferred_request"]
    assert type(request) is DeferredDirectorRepairRequestV1
    assert request.task_id == attempt.external_task_id == "1"


def test_runtime_repair_bridge_mismatched_task_id_is_structured_failure_not_raise(
    tmp_path: Path,
) -> None:
    workspace = tmp_path.resolve()
    attempt = _attempt(workspace, task_id="task-external")
    results = run_runtime_repair_with_director_tools(
        object(),
        workspace_path=workspace,
        task_id="totally-unrelated",
        source_tool=_typescript_import_specifier_source_tool(),
        execution_attempt=attempt,
        base_files={"src/models/Market.ts": "export const market = true;\n"},
        artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
        max_rounds=1,
    )
    assert len(results) == 1
    assert results[0]["success"] is False
    assert results[0]["result"]["error_code"] == "deo_deferred_repair_task_mismatch"


def test_runtime_repair_bridge_has_no_physical_executor_or_synchronous_mutation_seam() -> None:
    source = inspect.getsource(run_runtime_repair_with_director_tools)

    assert "executor_factory" not in inspect.signature(run_runtime_repair_with_director_tools).parameters
    assert ".execute_tool(" not in source
    assert "run_director_repair(" not in source
    assert "run_director_repair_convergence(" not in source
    assert "writer=" not in source
    assert "editor=" not in source
    assert "deleter=" not in source


def test_execute_method_snapshots_exact_attempt_for_deferred_repair(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    authority = create_task_runtime_execution_attempt_authority(attempt)

    assert _execution_attempt_identity_from_context({"task_runtime_execution_attempt_authority": authority}) is attempt
    assert _execution_attempt_identity_from_context({}) is None


def test_post_execution_schedule_forwards_exact_execution_attempt(monkeypatch: Any, tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_go(adapter: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del adapter
        captured.update(kwargs)
        return []

    def _fake_schedule(*, runner_step_ids: tuple[str, ...], runner: Any, max_rounds: int) -> Any:
        assert max_rounds == 1
        assert "go.module_import" in runner_step_ids
        step = post_execution_repair_bridge.DirectorRepairPostExecutionStepV1(
            step_id="go.module_import",
            language="go",
            phase="post_materialization",
            priority=1,
            source_tool="deterministic_go_module_import_repair",
        )
        assert runner(step) == []
        return SimpleNamespace(tool_results=(), ordered_steps=(step,))

    monkeypatch.setattr(post_execution_repair_bridge, "_run_go_post_repairs", _fake_go)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_director_post_execution_repair_schedule_result",
        _fake_schedule,
    )

    results, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
    )

    assert results == []
    assert summary is None
    assert captured["execution_attempt"] is attempt


def test_live_cpp_post_execution_repair_is_deferred_without_physical_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_tool = "deterministic_cpp_include_path_repair"
    target = tmp_path / "src/engine.cpp"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('#include "missing.hpp"\n', encoding="utf-8")
    attempt = _attempt(tmp_path, task_id="task-cpp")
    captured: dict[str, Any] = {}
    sentinel = [{"tool_name": "deferred_director_repair", "result": {"deferred_request": object()}}]

    def _deferred_bridge(_adapter: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(post_execution_repair_bridge, "run_runtime_repair_with_director_tools", _deferred_bridge)
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *_args, **_kwargs: None,
    )

    result = post_execution_repair_bridge._run_cpp_runtime_repair(
        adapter,
        tmp_path,
        task_id=attempt.external_task_id,
        source_tool=source_tool,
        execution_attempt=attempt,
    )

    assert result is sentinel
    assert captured["source_tool"] == source_tool
    assert captured["execution_attempt"] is attempt
    assert captured["max_rounds"] == 1


def test_dead_java_direct_writer_helpers_are_removed() -> None:
    assert not hasattr(post_execution_repair_bridge, "_run_java_accessor_alias_runtime_repair")
    assert not hasattr(post_execution_repair_bridge, "_run_java_test_dependency_runtime_repair")
