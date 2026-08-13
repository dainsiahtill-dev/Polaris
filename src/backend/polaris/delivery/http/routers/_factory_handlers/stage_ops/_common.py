# ruff: noqa: E402, F403, F405
"""Factory stage-ops helpers — shared low-level utilities.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module. These primitives are shared across
the director-resume, quality-gate-rework, artifact, and run-summary helpers.

This package is the lossless successor of the former ``stage_ops`` module.
``polaris.delivery.http.routers._factory_handlers.stage_ops`` (the package
``__init__``) re-exports every previously-public (and
previously-private-but-imported) symbol from the same import path so that
``from ...stage_ops import X`` keeps resolving identically for all external
importers, and so that ``factory.py``'s ``_rebind_helper_module`` continues to
rebind these callables into the host router namespace (where unit tests
monkeypatch them).

Sibling helpers reference each other via the package namespace. After the
package re-exports every symbol, ``_wire_cross_module_namespace`` injects
non-owned names into each submodule's globals so cross-module calls remain
lossless without rewriting call sites.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from polaris.cells.factory.pipeline.public import FactoryRunService
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.quality import task_identifier_token_aliases
from polaris.kernelone.storage import resolve_logical_path

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *


def _check_docs_ready(workspace: str) -> bool:
    """Check whether required docs are already present."""
    workspace_path = Path(workspace)
    docs_to_check = [
        workspace_path / "SPEC.md",
        workspace_path / "requirements.md",
        workspace_path / "docs" / "SPEC.md",
        workspace_path / "docs" / "requirements.md",
    ]
    return any(doc.exists() for doc in docs_to_check)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pm_plan_task_count(workspace: str) -> int:
    payload = _load_json_object(Path(resolve_runtime_path(workspace, "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    return len(tasks) if isinstance(tasks, list) else 0


def _pm_plan_task_ids(workspace: str) -> tuple[str, ...]:
    """Return canonical PM task ids that Director-local rework may reopen."""

    payload = _load_json_object(Path(resolve_runtime_path(workspace, "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return ()
    task_ids: list[str] = []
    seen: set[str] = set()
    for item in tasks:
        task_id = _resolve_task_identifier(item)
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        task_ids.append(task_id)
    return tuple(task_ids)


def _resolve_task_identifier(*sources: Any) -> str:
    """Return the first display-stable task identifier from known payload shapes.

    The alias helper is used only to validate that a candidate participates in
    the same identifier space as ScopeAuthority owner routing. The returned
    value intentionally remains the original display token.
    """

    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _TASK_IDENTIFIER_KEYS:
            value = str(source.get(key) or "").strip()
            if value and task_identifier_token_aliases(value):
                return value
    return ""


def _json_payload(data: Any) -> str:
    payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    return json.dumps(payload, ensure_ascii=False)


def _write_json_text_atomic(path: Path, payload: Any, *, trailing_newline: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if trailing_newline:
        text += "\n"
    write_text_atomic(str(path), text)


def _resolve_runtime_path(workspace: str, relative_path: str) -> Path:
    rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if rel == "docs" or rel.startswith("docs/"):
        rel = f"workspace/{rel}"
    elif rel.startswith(("tasks/", "dispatch/")):
        rel = f"runtime/{rel}"
    resolved = resolve_logical_path(str(workspace), rel)
    return Path(resolved).resolve()


def _read_json_artifact(workspace: str, relative_path: str) -> dict[str, Any]:
    target = _resolve_runtime_path(workspace, relative_path)
    if not target.exists() or not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (RuntimeError, ValueError):
        logger.debug("Failed to read JSON artifact: workspace=%s path=%s", workspace, relative_path)
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _resolve_loop_max_cycles() -> int:
    raw = os.getenv("KERNELONE_FACTORY_LOOP_MAX_CYCLES", str(_DEFAULT_LOOP_MAX_CYCLES))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_LOOP_MAX_CYCLES
    return max(1, min(value, 200))


def _resolve_loop_stall_threshold() -> int:
    raw = os.getenv("KERNELONE_FACTORY_LOOP_STALL_THRESHOLD", str(_DEFAULT_LOOP_STALL_THRESHOLD))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_LOOP_STALL_THRESHOLD
    return max(1, min(value, 20))


def _resolve_quality_rework_max_cycles() -> int:
    raw = os.getenv("KERNELONE_FACTORY_QUALITY_REWORK_MAX_CYCLES", str(_DEFAULT_QUALITY_REWORK_MAX_CYCLES))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_QUALITY_REWORK_MAX_CYCLES
    return max(1, min(value, 20))


def _safe_events_tail_limit(limit: int) -> int:
    return max(0, min(int(limit), 1000))


async def _guard_automatic_router_mutation(
    *,
    service: FactoryRunService,
    run_id: str,
    current_run: Any,
    operation: str,
) -> Any:
    return await service.assert_automatic_router_mutation_allowed(
        run_id,
        operation=operation,
        current_run=current_run,
    )


# FactoryStartRequest is re-exported by this module for lossless surface parity
# with the former monolith (which imported it at module scope).
__all__ = [
    "FactoryStartRequest",
    "_check_docs_ready",
    "_guard_automatic_router_mutation",
    "_json_payload",
    "_load_json_object",
    "_pm_plan_task_count",
    "_pm_plan_task_ids",
    "_read_json_artifact",
    "_resolve_loop_max_cycles",
    "_resolve_loop_stall_threshold",
    "_resolve_quality_rework_max_cycles",
    "_resolve_runtime_path",
    "_resolve_task_identifier",
    "_safe_events_tail_limit",
    "_write_json_text_atomic",
]
