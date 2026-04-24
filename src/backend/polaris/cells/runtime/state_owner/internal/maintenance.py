from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING, Literal

from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage.io_paths import (
    normalize_artifact_rel_path,
    resolve_artifact_path,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterable

RuntimeClearScope = Literal["pm", "director", "dialogue", "all"]

_PM_CLEAR_REL_PATHS: list[str] = [
    "runtime/logs/pm.process.log",
    "runtime/events/pm.llm.events.jsonl",
    "runtime/results/pm.report.md",
    "runtime/events/pm.events.jsonl",
]

_DIRECTOR_CLEAR_REL_PATHS: list[str] = [
    "runtime/logs/director.process.log",
    "runtime/events/director.llm.events.jsonl",
    "runtime/logs/director.runlog.md",
]

_DIALOGUE_CLEAR_REL_PATHS: list[str] = [
    "runtime/events/dialogue.transcript.jsonl",
    "runtime/events/runtime.events.jsonl",
]

_ALL_CLEAR_REL_PATHS: list[str] = sorted(
    set(
        _PM_CLEAR_REL_PATHS
        + _DIRECTOR_CLEAR_REL_PATHS
        + _DIALOGUE_CLEAR_REL_PATHS
        + [
            "runtime/results/planner.output.md",
            "runtime/results/director_llm.output.md",
            "runtime/results/qa.review.md",
        ]
    )
)

_RESET_FILE_REL_PATHS: list[str] = sorted(
    {
            *_ALL_CLEAR_REL_PATHS,
            "runtime/contracts/pm_tasks.contract.json",
            "runtime/state/pm.state.json",
            "runtime/events/pm.task_history.events.jsonl",
            "runtime/results/director.result.json",
            "runtime/status/director.status.json",
            "runtime/trajectory.json",
            "runtime/state/assignee_routing.state.json",
            "runtime/state/assignee_execution.state.json",
            "runtime/policy/director.policy.json",
            "runtime/contracts/agents.generated.md",
            "runtime/contracts/agents.feedback.md",
            "runtime/control/pm.stop.flag",
            "runtime/control/director.stop.flag",
            "runtime/memory/last_state.json",
        }
)

_RESET_DIR_REL_PATHS: list[str] = [
    "runtime/runs",
    "runtime/artifacts/runs",
    "runtime/evidence",
    "runtime/memory",
]

_SCOPE_TO_REL_PATHS: dict[RuntimeClearScope, list[str]] = {
    "pm": _PM_CLEAR_REL_PATHS,
    "director": _DIRECTOR_CLEAR_REL_PATHS,
    "dialogue": _DIALOGUE_CLEAR_REL_PATHS,
    "all": _ALL_CLEAR_REL_PATHS,
}


def _candidate_paths_for_rel(workspace: str, cache_root: str, rel_path: str) -> set[str]:
    normalized = normalize_artifact_rel_path(rel_path)
    candidates: set[str] = set()
    try:
        primary = resolve_artifact_path(workspace, cache_root, normalized)
        if primary:
            candidates.add(os.path.abspath(primary))
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to resolve artifact path: {e}")
    return {path for path in candidates if path}


def _truncate_or_remove_file(path: str, *, hard_delete: bool) -> bool:
    if not os.path.isfile(path):
        return False
    if hard_delete:
        try:
            os.remove(path)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.warning("Runtime maintenance step failed (file remove %s): %s", path, exc)
            return False
    try:
        write_text_atomic(path, "", encoding="utf-8")
        return True
    except (RuntimeError, ValueError) as exc:
        logger.warning("Runtime maintenance step failed (file truncate %s): %s", path, exc)
        return False


def _remove_directory(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path)
        return True
    except (RuntimeError, ValueError) as exc:
        logger.warning("Runtime maintenance step failed (dir remove %s): %s", path, exc)
        return False


def clear_runtime_scope(
    workspace: str,
    cache_root: str,
    scope: RuntimeClearScope,
) -> dict[str, object]:
    rel_paths = _SCOPE_TO_REL_PATHS.get(scope, [])
    return _clear_rel_paths(workspace, cache_root, rel_paths, hard_delete=False)


def reset_runtime_records(workspace: str, cache_root: str) -> dict[str, object]:
    files_result = _clear_rel_paths(
        workspace,
        cache_root,
        _RESET_FILE_REL_PATHS,
        hard_delete=True,
    )

    cleared_dirs: list[str] = []
    failed_dirs: list[str] = []
    for rel_path in _RESET_DIR_REL_PATHS:
        for candidate in sorted(_candidate_paths_for_rel(workspace, cache_root, rel_path)):
            if not os.path.isdir(candidate):
                continue
            if _remove_directory(candidate):
                cleared_dirs.append(candidate)
            else:
                failed_dirs.append(candidate)

    cleared_paths_raw = files_result.get("cleared_paths", [])
    failed_paths_raw = files_result.get("failed_paths", [])
    cleared_count_raw = files_result.get("cleared_count", 0)
    failed_count_raw = files_result.get("failed_count", 0)

    # Ensure proper types for list operations with explicit type handling
    cleared_paths_list: list[str] = list(cleared_paths_raw) if isinstance(cleared_paths_raw, (list, tuple)) else []
    failed_paths_list: list[str] = list(failed_paths_raw) if isinstance(failed_paths_raw, (list, tuple)) else []
    cleared_count_val: int = int(cleared_count_raw) if isinstance(cleared_count_raw, (int, float, str)) else 0
    failed_count_val: int = int(failed_count_raw) if isinstance(failed_count_raw, (int, float, str)) else 0

    return {
        "cleared_paths": sorted(set(cleared_paths_list + cleared_dirs)),
        "failed_paths": sorted(set(failed_paths_list + failed_dirs)),
        "cleared_count": cleared_count_val + len(set(cleared_dirs)),
        "failed_count": failed_count_val + len(set(failed_dirs)),
    }


def _clear_rel_paths(
    workspace: str,
    cache_root: str,
    rel_paths: Iterable[str],
    *,
    hard_delete: bool,
) -> dict[str, object]:
    cleared: list[str] = []
    failed: list[str] = []

    for rel_path in rel_paths:
        candidates = _candidate_paths_for_rel(workspace, cache_root, rel_path)
        for candidate in sorted(candidates):
            if os.path.isfile(candidate):
                if _truncate_or_remove_file(candidate, hard_delete=hard_delete):
                    cleared.append(candidate)
                else:
                    failed.append(candidate)
            elif os.path.isdir(candidate):
                if _remove_directory(candidate):
                    cleared.append(candidate)
                else:
                    failed.append(candidate)

    unique_cleared = sorted(set(cleared))
    unique_failed = sorted({path for path in failed if path not in unique_cleared})
    return {
        "cleared_paths": unique_cleared,
        "failed_paths": unique_failed,
        "cleared_count": len(unique_cleared),
        "failed_count": len(unique_failed),
    }
