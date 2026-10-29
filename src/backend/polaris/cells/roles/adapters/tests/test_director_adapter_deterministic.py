"""Unit tests for DirectorAdapter pure logic (no I/O, no LLM).

Covers:
- _select_execution_strategy
- _apply_intelligent_correction
- _build_director_message
- _build_materialized_metadata
- _resolve_execution_backend_request
- get_capabilities / role_id
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.adapters.internal.director import (
    quality_gate as quality_gate_module,
)
from polaris.cells.roles.adapters.internal.director.adapter import (
    DirectorAdapter,
)
from polaris.cells.roles.adapters.internal.director.execute_method_repair_bridge import (
    run_python_runtime_smoke,
)
from polaris.cells.roles.adapters.public import service as roles_adapters_public_service
from polaris.cells.roles.adapters.public.contracts import RunDirectorMaterializationQualityRepairScheduleCommandV1
from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)

from ._execution_attempt_helpers import (
    _project_deferred_repair_results_for_test,
    _test_execution_attempt,
    _test_execution_attempt_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_go_materialization_quality_schedule(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str] | tuple[str, ...] = (),
    advisor_notes: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Run Go materialization repair through the typed roles adapter boundary."""

    workspace = Path(adapter.workspace)
    result = roles_adapters_public_service.run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task={},
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            advisor_notes=tuple(advisor_notes),
            execution_attempt=_test_execution_attempt(workspace, task_id),
        )
    )
    return _project_deferred_repair_results_for_test(
        workspace,
        [dict(item) for item in result.tool_results],
    )


def _make_adapter(tmp_path: Any, task_runtime: Any = None) -> DirectorAdapter:
    """Create a DirectorAdapter with mocked heavy dependencies."""
    workspace = Path(tmp_path)
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="director-adapter-pure-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    if task_runtime is None:
        adapter = DirectorAdapter(workspace=str(workspace))
    else:
        adapter = DirectorAdapter(workspace=str(workspace), task_runtime=task_runtime)
    return adapter


def _install_test_deferred_projection(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    workspace: Path,
) -> None:
    """Wrap one bridge so old planner tests consume deferred effects safely."""

    original = module.run_runtime_repair_with_director_tools

    def _run(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        task_id = str(kwargs.get("task_id") or "director-repair-test")
        if type(kwargs.get("execution_attempt")) is not TaskRuntimeExecutionAttemptIdentityV1:
            kwargs["execution_attempt"] = _test_execution_attempt(workspace, task_id)
        return _project_deferred_repair_results_for_test(
            workspace,
            original(*args, **kwargs),
        )

    monkeypatch.setattr(module, "run_runtime_repair_with_director_tools", _run)


def _install_all_test_deferred_projections(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    """Project every Director repair bridge only for isolated legacy tests."""

    from polaris.cells.roles.adapters.internal.director import (
        execute_method_repair_bridge,
        materialization_quality_callback_ports,
        post_execution_repair_bridge,
        runtime_repair_tool_adapter,
    )

    original = runtime_repair_tool_adapter.run_runtime_repair_with_director_tools

    def _run(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        task_id = str(kwargs.get("task_id") or "director-repair-test")
        if type(kwargs.get("execution_attempt")) is not TaskRuntimeExecutionAttemptIdentityV1:
            kwargs["execution_attempt"] = _test_execution_attempt(workspace, task_id)
        return _project_deferred_repair_results_for_test(
            workspace,
            original(*args, **kwargs),
        )

    for module in (
        runtime_repair_tool_adapter,
        execute_method_repair_bridge,
        materialization_quality_callback_ports,
        post_execution_repair_bridge,
        quality_gate_module,
    ):
        monkeypatch.setattr(module, "run_runtime_repair_with_director_tools", _run)


def _run_test_materialization_quality_repair_schedule(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run production planning and project its deferred effects in test scope."""

    workspace = Path(adapter.workspace)
    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=_test_execution_attempt(workspace, task_id),
    )
    return _project_deferred_repair_results_for_test(workspace, results), summary


class TestDeterministicPythonRuntimeSmoke:
    """Director surfaces runtime errors that py_compile misses.

    Live factory-bench L1-01 (2026-06-17, after symbol-coherence fix):
    qwen3.6-27b-int4 wrote calculator.py that imports cleanly and
    py_compiles, but its ``__main__`` block calls ``evaluate('1+2')``
    which raises ``ValueError`` at call time (the model's tokenizer
    stores ``value=float(text)`` for operator tokens — a model bug,
    but a real one the platform should surface). The post-write
    materialization quality gate currently relies on ``py_compile`` +
    ``scan_workspace_artifact_quality`` — neither catches call-time
    errors. A rigid ruler must run the code, not just parse it.

    Strategy: for each ``.py`` file with a ``__main__`` block, run it
    in a subprocess with a timeout. If exit code != 0 or the process
    is killed by the timeout, surface a runtime error so the
    materialization repair ladder can fix it (currently via the LLM
    repair path; the deterministic path is intentionally conservative
    because runtime fixes are project-specific).
    """

    def test_python_runtime_smoke_catches_call_time_error(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # A script with __main__ that crashes. py_compile will pass;
        # only running it surfaces the bug.
        (tmp_path / "calculator.py").write_text(
            "def evaluate(expr: str) -> float:\n"
            "    raise ValueError('broken tokenizer')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print(evaluate('1+2'))\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-1",
            all_affected_files=["calculator.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-1"),
            timeout_seconds=10.0,
        )

        assert errors == []
        assert len(tool_results) == 1
        request = tool_results[0]["result"]["deferred_request"]
        assert request.command.endswith("calculator.py")
        assert request.purpose.startswith("20_python_runtime_smoke_")

    def test_python_runtime_smoke_passes_clean_main(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "hello.py").write_text(
            "if __name__ == '__main__':\n    print('hello')\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-2",
            all_affected_files=["hello.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-2"),
            timeout_seconds=10.0,
        )

        assert errors == [], errors
        assert len(tool_results) == 1

    def test_python_runtime_smoke_sets_workspace_pythonpath_for_test_scripts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "guess_number.py").write_text(
            "def generate_target() -> int:\n    return 42\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_guess_number.py").write_text(
            "import guess_number\n\n"
            "def test_target() -> None:\n"
            "    assert guess_number.generate_target() == 42\n\n"
            "if __name__ == '__main__':\n"
            "    test_target()\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-test-import",
            all_affected_files=["tests/test_guess_number.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-test-import"),
            timeout_seconds=10.0,
        )

        assert errors == [], errors
        assert len(tool_results) == 2
        assert {item["result"]["purpose"].split("_")[0] for item in tool_results} == {"20", "30"}

    def test_python_runtime_smoke_runs_unittest_discover_for_touched_tests(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "weather.py").write_text("class Weather:\n    pass\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_weather.py").write_text(
            "import unittest\n"
            "from src.weather import Weather\n\n"
            "class WeatherTests(unittest.TestCase):\n"
            "    def test_weather_updates(self) -> None:\n"
            "        Weather().update(dt_s=1.0, is_day=True)\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-unittest-discover",
            all_affected_files=["tests/test_weather.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-unittest-discover"),
            timeout_seconds=10.0,
        )

        assert errors == []
        assert len(tool_results) == 1
        request = tool_results[0]["result"]["deferred_request"]
        assert "unittest discover" in request.command
        assert request.purpose == "30_python_unittest_discover"

    def test_python_runtime_smoke_skips_module_without_main(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Library file with no __main__ block — must not be executed
        # (calling it would hang on missing CLI args or do nothing
        # useful). Just skip.
        (tmp_path / "library.py").write_text(
            "def helper() -> int:\n    return 42\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-3",
            all_affected_files=["library.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-3"),
            timeout_seconds=10.0,
        )

        assert errors == [], errors
        assert tool_results == []

    def test_python_runtime_smoke_skips_non_python_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "readme.md").write_text("# title\n", encoding="utf-8")
        (tmp_path / "config.toml").write_text("x = 1\n", encoding="utf-8")

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-4",
            all_affected_files=["readme.md", "config.toml"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-4"),
            timeout_seconds=10.0,
        )

        assert errors == [], errors
        assert tool_results == []

    def test_python_runtime_smoke_terminates_hung_process(self, tmp_path: Any) -> None:
        """The smoke test must kill the child process after the
        configured timeout so a hung ``__main__`` does not leak
        past the Director turn boundary. After the L4-23 fix-#3
        boundary change, a long-running script is NOT a quality
        failure; the smoke only ensures the child is reaped so
        the next smoke call in the same turn is not contaminated.
        We verify the reap by re-running the smoke on the same
        file and expecting it to return within a small multiple
        of the timeout (i.e. it did not block on a leftover
        process).
        """
        adapter = _make_adapter(tmp_path)
        (tmp_path / "hung.py").write_text(
            "import time\nif __name__ == '__main__':\n    while True:\n        time.sleep(0.1)\n",
            encoding="utf-8",
        )

        # Long-running: the smoke must NOT flag this as a quality
        # failure (the L4-23 fix-#3 boundary). It must, however,
        # kill the child so subsequent smokes in the same turn
        # are not blocked by a leaked process.
        import time

        first_errors, first_tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-5",
            all_affected_files=["hung.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-5"),
            timeout_seconds=0.5,
        )
        assert first_errors == []
        assert len(first_tool_results) == 1
        started = time.monotonic()
        second_errors, second_tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-5b",
            all_affected_files=["hung.py"],
            context=_test_execution_attempt_context(tmp_path, "task-py-runtime-5b"),
            timeout_seconds=0.5,
        )
        elapsed = time.monotonic() - started
        assert second_errors == []
        assert len(second_tool_results) == 1
        assert elapsed < 5.0, f"second smoke took {elapsed:.2f}s -- leaked process"


# ---------------------------------------------------------------------------
# Materialized metadata
# ---------------------------------------------------------------------------
