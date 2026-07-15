"""Unit contracts for UnifiedBenchmarkRunner orchestration and judging.

Pytest must not execute benchmark or matrix scoring. The suite-level regression
contracts therefore inspect the runner's exception boundary structurally while
the agentic-eval CLI remains the only scoring executor.

The contracts cover two confirmed defects:

* A per-case ``Exception`` must become a failed ``BenchmarkRunResult`` and the
  suite loop must continue appending results.
* ``BaseException`` interruptions such as ``KeyboardInterrupt`` must propagate.
* ``_run_single_case`` must pass the materialized workspace's real file listing
  into the judge so ``no_hallucinated_paths`` has ground-truth known paths.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.benchmark.unified_runner import UnifiedBenchmarkRunner


def _make_case(case_id: str, *, validators: tuple[str, ...] = ()) -> UnifiedBenchmarkCase:
    """Build a minimal benchmark case targeting the QA role."""
    return UnifiedBenchmarkCase(
        case_id=case_id,
        role="qa",
        title=case_id,
        prompt="inspect the workspace",
        judge=JudgeConfig(score_threshold=0.5, validators=validators),
    )


def _run_suite_case_loop() -> ast.For:
    """Return the AST loop that owns per-case execution and result collection."""
    source = textwrap.dedent(inspect.getsource(UnifiedBenchmarkRunner.run_suite))
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if any(
            isinstance(candidate, ast.Attribute) and candidate.attr == "_run_single_case"
            for candidate in ast.walk(node)
        ):
            return node

    raise AssertionError("run_suite no longer contains a per-case execution loop")


def _per_case_try(case_loop: ast.For) -> ast.Try:
    """Return the try statement guarding one case in the suite loop."""
    for node in ast.walk(case_loop):
        if not isinstance(node, ast.Try):
            continue
        if any(
            isinstance(candidate, ast.Attribute) and candidate.attr == "_run_single_case"
            for candidate in ast.walk(node)
        ):
            return node
    raise AssertionError("run_suite no longer guards per-case execution")


def test_run_suite_isolates_per_case_exceptions_without_pytest_scoring() -> None:
    """The suite loop must convert ordinary case failures and continue collection."""
    case_loop = _run_suite_case_loop()
    guarded_call = _per_case_try(case_loop)

    exception_handlers = [
        handler
        for handler in guarded_call.handlers
        if isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
    ]
    assert len(exception_handlers) == 1
    handler = exception_handlers[0]
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "BenchmarkRunResult"
        for node in ast.walk(handler)
    )

    guarded_index = case_loop.body.index(guarded_call)
    append_indices = [
        index
        for index, statement in enumerate(case_loop.body)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "results"
            and node.func.attr == "append"
            for node in ast.walk(statement)
        )
    ]
    assert append_indices
    assert min(append_indices) > guarded_index


def test_run_suite_does_not_catch_base_exception_interruptions() -> None:
    """The per-case boundary must preserve cancellation and operator interrupts."""
    guarded_call = _per_case_try(_run_suite_case_loop())
    caught_names = {handler.type.id for handler in guarded_call.handlers if isinstance(handler.type, ast.Name)}

    assert caught_names == {"Exception"}
    assert all(handler.type is not None for handler in guarded_call.handlers)
    assert issubclass(KeyboardInterrupt, BaseException)
    assert not issubclass(KeyboardInterrupt, Exception)


def test_hallucinated_path_validator_receives_known_paths(tmp_path: Path) -> None:
    """The no_hallucinated_paths validator must run against real workspace files.

    Before the fix, ``_run_single_case`` judged without ``workspace_files``, so
    the validator short-circuited and a fabricated path passed vacuously.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    runner = UnifiedBenchmarkRunner()
    judge = UnifiedJudge()

    fabricated_case = _make_case("case-fabricated", validators=("no_hallucinated_paths",))
    grounded_case = _make_case("case-grounded", validators=("no_hallucinated_paths",))

    workspace_files = runner.list_workspace_files(str(tmp_path))
    assert "src/main.py" in workspace_files

    fabricated_observed = ObservedBenchmarkRun(
        case_id=fabricated_case.case_id,
        role=fabricated_case.role,
        workspace=str(tmp_path),
        output="The fix lives in lib/totally_fake_module.py and config/ghost.yaml",
    )
    grounded_observed = ObservedBenchmarkRun(
        case_id=grounded_case.case_id,
        role=grounded_case.role,
        workspace=str(tmp_path),
        output="The entry point is src/main.py",
    )

    fabricated_verdict = judge.judge(fabricated_case, fabricated_observed, workspace_files=workspace_files)
    grounded_verdict = judge.judge(grounded_case, grounded_observed, workspace_files=workspace_files)

    fabricated_check = next(c for c in fabricated_verdict.checks if c.code == "validator:no_hallucinated_paths")
    grounded_check = next(c for c in grounded_verdict.checks if c.code == "validator:no_hallucinated_paths")

    assert fabricated_check.passed is False
    assert grounded_check.passed is True

    vacuous_verdict = judge.judge(fabricated_case, fabricated_observed)
    vacuous_check = next(c for c in vacuous_verdict.checks if c.code == "validator:no_hallucinated_paths")
    assert vacuous_check.passed is True
