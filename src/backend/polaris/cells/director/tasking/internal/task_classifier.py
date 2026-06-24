"""Compatibility helpers backed by the canonical task execution profile.

Historically this module carried independent heuristics for task dispatch and
tech-stack inference. It now delegates to ``resolve_director_execution_profile``
so execution branching, prompt guidance, temperature, and audit metadata share
one classifier.

All text operations MUST explicitly use UTF-8 encoding when file I/O is
involved. This module performs no file I/O.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.director.tasking.internal.execution_profile import resolve_director_execution_profile
from polaris.cells.director.tasking.public.contracts import TaskExecutionProfileV1
from polaris.domain.entities import Task


def _metadata(task: Task) -> dict[str, Any]:
    metadata = getattr(task, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _path_list(metadata: dict[str, Any], key: str) -> list[str] | None:
    raw_value = metadata.get(key)
    if not isinstance(raw_value, list):
        return None
    return [str(item) for item in raw_value]


def _profile_for_task(task: Task) -> TaskExecutionProfileV1:
    metadata = _metadata(task)
    return resolve_director_execution_profile(
        subject=str(getattr(task, "subject", "") or ""),
        description=str(getattr(task, "description", "") or ""),
        metadata=metadata,
        target_files=_path_list(metadata, "target_files"),
        scope_paths=_path_list(metadata, "scope_paths"),
        workspace=str(getattr(task, "workspace", "") or ""),
    )


def classify_task(task: Task) -> str:
    """Return legacy dispatch type from the canonical execution profile."""

    return _profile_for_task(task).dispatch_type


def extract_tech_stack(task: Task) -> dict[str, str]:
    """Return legacy tech-stack shape from the canonical execution profile."""

    profile = _profile_for_task(task)
    tech_stack = {
        "language": "unknown" if profile.language == "generic" else profile.language,
        "project_type": profile.project_type,
    }
    if profile.framework:
        tech_stack["framework"] = profile.framework
    return tech_stack
