"""Rollback-link builder for Chief Engineer blueprints.

Wraps the existing ``create_rollback_guard`` factory and emits a
:class:`RollbackLinkV1` summarizing the rollback strategy, marker path,
and preconditions. The strategy is selected from the workspace's
state:

  - ``.git`` present  -> ``git_revert``
  - otherwise         -> ``file_snapshot``

Preconditions are SATISFIED-state checks — each is listed only when it
currently holds, so a consumer reads the ABSENCE of a check as "not yet
satisfied" (a gate still to clear before rollback is safe):
  - "blueprint_persisted" — baseline; always listed.
  - "no_blocker_risks_open" — listed only when NO open blocker/critical risk.
  - "target_files_declared" — listed only when the blueprint declares targets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from polaris.cells.chief_engineer.blueprint.internal.quality_gate import (
    _coerce_risks,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    RiskSeverity,
    RiskStatus,
    RollbackLinkV1,
    RollbackStrategy,
)
from polaris.kernelone.storage import resolve_logical_path


def _has_git(workspace: str) -> bool:
    if not workspace:
        return False
    return os.path.isdir(os.path.join(workspace, ".git"))


def _marker_path(workspace: str, blueprint_id: str) -> str:
    base = Path(resolve_logical_path(workspace, "runtime/state/blueprints"))
    return str(base / f"{blueprint_id}.stash")


def _has_open_blocker(risks: Iterable[Any]) -> bool:
    for record in _coerce_risks(risks):
        if record.status == RiskStatus.OPEN and record.severity in {
            RiskSeverity.BLOCKER,
            RiskSeverity.CRITICAL,
        }:
            return True
    return False


def build_rollback_link(
    *,
    workspace: str,
    blueprint_id: str,
    blueprint: Mapping[str, Any] | None = None,
    risks: Iterable[Any] | None = None,
) -> RollbackLinkV1:
    """Build a :class:`RollbackLinkV1` for the given blueprint.

    Args:
        workspace: Root workspace path.
        blueprint_id: Owning blueprint id.
        blueprint: Blueprint payload (used to consult ``target_files`` and,
            when ``risks`` is not provided, the structured
            ``blueprint["risk_register"]`` records).
        risks: Optional iterable of risk records / dicts. When omitted,
            the function falls back to ``blueprint["risk_register"]`` only.

    Returns:
        A populated :class:`RollbackLinkV1`.
    """
    target_files: list[str] = []
    if isinstance(blueprint, Mapping):
        raw_targets = blueprint.get("target_files")
        if isinstance(raw_targets, (list, tuple)):
            for item in raw_targets:
                if isinstance(item, str) and item.strip():
                    target_files.append(item.strip())
                elif isinstance(item, Mapping):
                    for key in ("path", "file", "id"):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            target_files.append(value.strip())
                            break

    strategy = RollbackStrategy.GIT_REVERT if _has_git(workspace) else RollbackStrategy.FILE_SNAPSHOT
    marker_path = _marker_path(workspace, blueprint_id)

    # Determine preconditions. ``preconditions`` lists the safe-state checks
    # that CURRENTLY HOLD for this rollback — a condition is listed only when
    # it is satisfied (consistent with the always-satisfied "blueprint_persisted"
    # baseline). A consumer reads the ABSENCE of a check as "not yet satisfied".
    preconditions: list[str] = ["blueprint_persisted"]
    risk_source: Iterable[Any] = risks if risks is not None else ()
    if not risk_source and isinstance(blueprint, Mapping):
        embedded = blueprint.get("risk_register")
        if isinstance(embedded, (list, tuple)):
            risk_source = embedded
    if not _has_open_blocker(risk_source):
        preconditions.append("no_blocker_risks_open")
    if target_files:
        preconditions.append("target_files_declared")

    enabled = bool(blueprint_id and workspace and target_files)
    return RollbackLinkV1(
        enabled=enabled,
        strategy=strategy,
        marker_path=marker_path,
        preconditions=tuple(preconditions),
    )


__all__ = ["build_rollback_link"]
