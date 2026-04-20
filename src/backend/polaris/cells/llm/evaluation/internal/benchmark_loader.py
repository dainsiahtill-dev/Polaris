"""Fixture loading and sandbox materialization for role agentic benchmarks."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from polaris.kernelone.storage import resolve_runtime_path

from .benchmark_models import AgenticBenchmarkCase

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "agentic_benchmark"
CASES_ROOT = FIXTURES_ROOT / "cases"
WORKSPACES_ROOT = FIXTURES_ROOT / "workspaces"
_SANDBOX_KEY_PREFIX_LEN = 40
_SANDBOX_HASH_LEN = 12
_SANDBOX_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "*.pyc",
    "*.pyo",
)


def build_case_sandbox_key(case_id: str) -> str:
    token = str(case_id or "").strip() or "case"
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", token).strip("-_.") or "case"
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:_SANDBOX_HASH_LEN]
    prefix = normalized[:_SANDBOX_KEY_PREFIX_LEN].rstrip("-_.") or "case"
    return f"{prefix}-{digest}"


def copy_fixture_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=_SANDBOX_COPY_IGNORE)


def load_agentic_benchmark_case(path: str | Path) -> AgenticBenchmarkCase:
    candidate = Path(path)
    with open(candidate, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark case must be a JSON object: {candidate}")
    return AgenticBenchmarkCase.from_dict(payload)


def load_builtin_agentic_benchmark_cases(
    *,
    role: str | None = None,
    case_ids: list[str] | tuple[str, ...] | None = None,
) -> list[AgenticBenchmarkCase]:
    role_token = str(role or "").strip().lower()
    selected_case_ids = {str(item).strip() for item in list(case_ids or ()) if str(item).strip()}
    cases: list[AgenticBenchmarkCase] = []
    for path in sorted(CASES_ROOT.glob("*.json")):
        case = load_agentic_benchmark_case(path)
        if selected_case_ids and case.case_id not in selected_case_ids:
            continue
        if role_token and role_token not in {"all", "default", "benchmark", "agentic"} and case.role != role_token:
            continue
        cases.append(case)
    return cases


def resolve_case_fixture_dir(case: AgenticBenchmarkCase) -> Path | None:
    token = str(case.workspace_fixture or "").strip()
    if not token:
        return None
    candidate = WORKSPACES_ROOT / token
    if not candidate.is_dir():
        raise FileNotFoundError(f"workspace fixture not found for case {case.case_id}: {candidate}")
    return candidate


def materialize_case_workspace(
    *,
    base_workspace: str,
    run_id: str,
    case: AgenticBenchmarkCase,
) -> str:
    fixture_dir = resolve_case_fixture_dir(case)
    if fixture_dir is None:
        return str(Path(base_workspace))

    sandbox_key = build_case_sandbox_key(case.case_id)
    target_dir = Path(resolve_runtime_path(base_workspace, f"runtime/llm_evaluations/{run_id}/sandboxes/{sandbox_key}"))
    copy_fixture_tree(fixture_dir, target_dir)
    return str(target_dir)


def list_workspace_files(root: str | Path) -> list[str]:
    workspace = Path(root)
    if not workspace.is_dir():
        return []
    results: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        results.append(path.relative_to(workspace).as_posix())
    return results
