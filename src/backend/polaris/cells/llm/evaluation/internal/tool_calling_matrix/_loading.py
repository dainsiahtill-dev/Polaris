"""Matrix-case loading from JSON fixtures and workspace materialization.

Holds the fixture-root path constants (``FIXTURES_ROOT``, ``CASES_ROOT``,
``WORKSPACES_ROOT``) and the loaders/materializers that read cases from the
fixture tree and copy per-case sandboxes into the runtime root.
"""

from __future__ import annotations

import json
from pathlib import Path

from polaris.kernelone.storage import resolve_runtime_path

from ..benchmark_loader import build_case_sandbox_key, copy_fixture_tree
from ._contracts import ToolCallingMatrixCase, _non_empty

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "tool_calling_matrix"
CASES_ROOT = FIXTURES_ROOT / "cases"
WORKSPACES_ROOT = FIXTURES_ROOT / "workspaces"


def load_tool_calling_matrix_case(path: str | Path) -> ToolCallingMatrixCase:
    """Load a single tool-calling matrix case from a JSON file.

    Args:
        path: Path to the JSON case file.

    Returns:
        A populated ToolCallingMatrixCase instance.

    Raises:
        ValueError: If the file does not contain a JSON object.
        FileNotFoundError: If the case file does not exist.

    Example:
        case = load_tool_calling_matrix_case(
            "/path/to/fixtures/cases/safe_001.json"
        )
    """
    candidate = Path(path)
    with open(candidate, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"matrix case must be a JSON object: {candidate}")
    return ToolCallingMatrixCase.from_dict(payload)


def load_builtin_tool_calling_matrix_cases(
    *,
    role: str | None = None,
    case_ids: list[str] | tuple[str, ...] | None = None,
) -> list[ToolCallingMatrixCase]:
    """Load all builtin tool-calling matrix cases from fixtures.

    Args:
        role: Optional role filter. Special values "all", "default", "matrix",
            "tool_calling_matrix", "benchmark" match all roles.
        case_ids: Optional list of specific case IDs to load.

    Returns:
        List of loaded ToolCallingMatrixCase instances, sorted by case_id.

    Example:
        # Load all cases
        all_cases = load_builtin_tool_calling_matrix_cases()

        # Load only director cases
        director_cases = load_builtin_tool_calling_matrix_cases(role="director")

        # Load specific cases
        specific = load_builtin_tool_calling_matrix_cases(
            case_ids=["safe_001", "tooling_002"]
        )
    """
    role_token = _non_empty(role).lower()
    selected_case_ids = {str(item).strip() for item in list(case_ids or ()) if str(item).strip()}
    # Separate exact IDs from prefix filters (prefixes end with "_")
    exact_case_ids: set[str] = set()
    prefix_filters: list[str] = []
    for case_id in selected_case_ids:
        if case_id.endswith("_"):
            prefix_filters.append(case_id)
        else:
            exact_case_ids.add(case_id)
    has_filter = bool(exact_case_ids or prefix_filters)
    cases: list[ToolCallingMatrixCase] = []
    for path in sorted(CASES_ROOT.glob("*.json")):
        case = load_tool_calling_matrix_case(path)
        # Filter: skip if case doesn't match any filter criteria
        if has_filter:
            exact_match = case.case_id in exact_case_ids if exact_case_ids else False
            prefix_match = any(case.case_id.startswith(p) for p in prefix_filters) if prefix_filters else False
            if not exact_match and not prefix_match:
                continue
        if (
            role_token
            and role_token not in {"all", "default", "matrix", "tool_calling_matrix", "benchmark"}
            and case.role != role_token
        ):
            continue
        cases.append(case)
    return cases


def resolve_case_fixture_dir(case: ToolCallingMatrixCase) -> Path | None:
    """Resolve the workspace fixture directory for a case.

    Args:
        case: The matrix case with optional workspace_fixture field.

    Returns:
        Path to the fixture directory, or None if no fixture specified.

    Raises:
        FileNotFoundError: If a fixture is specified but the directory does not exist.
    """
    token = _non_empty(case.workspace_fixture)
    if not token:
        return None
    candidate = WORKSPACES_ROOT / token
    if not candidate.is_dir():
        raise FileNotFoundError(f"workspace fixture not found for case {case.case_id}: {candidate}")
    return candidate


def materialize_case_workspace(
    *,
    benchmark_root: str,
    run_id: str,
    case: ToolCallingMatrixCase,
) -> str:
    """Create an isolated workspace sandbox for a case.

    Copies the case fixture to a unique runtime sandbox directory, or returns
    the benchmark root if no fixture exists.

    Args:
        benchmark_root: Workspace root used to resolve runtime sandbox paths.
        run_id: Unique identifier for this test run.
        case: The matrix case defining the fixture.

    Returns:
        Path to the materialized workspace directory.

    Example:
        sandbox = materialize_case_workspace(
            benchmark_root="/tmp/benchmark_root",
            run_id="run_123",
            case=case,
        )
        # Returns: <runtime>/llm_evaluations/run_123/sandboxes/<sandbox_key>
    """
    fixture_dir = resolve_case_fixture_dir(case)
    if fixture_dir is None:
        return str(Path(benchmark_root))

    sandbox_key = build_case_sandbox_key(case.case_id)
    target_dir = Path(resolve_runtime_path(benchmark_root, f"runtime/llm_evaluations/{run_id}/sandboxes/{sandbox_key}"))
    copy_fixture_tree(fixture_dir, target_dir)
    return str(target_dir)
