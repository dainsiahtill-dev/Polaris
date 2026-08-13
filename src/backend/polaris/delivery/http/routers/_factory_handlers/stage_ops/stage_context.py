# ruff: noqa: E402, F403, F405
"""Factory stage-ops helpers — stage list / context / runtime-readiness gating.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module.

``factory.py``'s ``_rebind_helper_module`` rebinds these callables into the host
router namespace; the package ``__init__`` rewrites ``__module__`` so the rebind
treats them as package-owned. Cross-module free names are injected by
``_wire_cross_module_namespace``.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.delivery.http.routers._shared import ensure_required_roles_ready
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.llm.budget_policy import resolve_director_dispatch_timeout_seconds

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *
from ._common import _check_docs_ready


def _normalize_start_from(start_from: str, workspace: str) -> str:
    normalized = str(start_from or "auto").strip().lower()
    if normalized in {"resume_director", "director-only", "director_only"}:
        normalized = "director_resume"
    if normalized not in {"auto", "architect", "pm", "director_resume"}:
        normalized = "auto"
    if normalized != "auto":
        return normalized
    return "architect" if not _check_docs_ready(workspace) else "pm"


def _build_stage_list(start_from: str, run_director: bool) -> list[str]:
    del run_director
    normalized = str(start_from or "auto").strip().lower()
    if normalized == "architect":
        return [
            "docs_generation",
            "pm_planning",
            "chief_engineer_review",
            "director_dispatch",
            "quality_gate",
        ]
    if normalized == "pm":
        return [
            "pm_planning",
            "chief_engineer_review",
            "director_dispatch",
            "quality_gate",
        ]
    if normalized == "director_resume":
        return [
            "director_dispatch",
            "quality_gate",
        ]
    # auto is normalized before this point; fail closed to the canonical chain.
    return [
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def _required_ready_roles_for_stages(stages: list[str], *, qa_enabled: bool) -> list[str]:
    roles: list[str] = []
    for stage in stages:
        role = STAGE_TO_ROLE.get(str(stage or "").strip())
        if not role:
            continue
        # Factory CE review uses the local chief_engineer.blueprint service; it
        # must not be blocked by role-chat LLM readiness.
        if role == "chief_engineer":
            continue
        if role == "qa" and not qa_enabled:
            continue
        if role not in roles:
            roles.append(role)
    return roles


def _settings_qa_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "qa_enabled", True))


def _ensure_factory_runtime_ready(state: AppState, stages: list[str]) -> None:
    roles = _required_ready_roles_for_stages(stages, qa_enabled=_settings_qa_enabled(state.settings))
    if not roles:
        return
    live_check = os.environ.get("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }
    ensure_required_roles_ready(
        state,
        default_roles=roles,
        force_roles=roles,
        live_check=live_check,
    )


def _build_stage_context(
    stage: str,
    payload: FactoryStartRequest,
    state: AppState,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    metadata = dict(payload.metadata or {})
    metadata["factory_start_from"] = str(payload.start_from or "").strip().lower()
    context: dict[str, Any] = {
        "settings": getattr(state, "settings", None),
        "factory_run_id": str(run_id or "").strip(),
        "factory_start_from": metadata["factory_start_from"],
        "metadata": metadata,
    }
    _append_factory_deadline_context(context, metadata)
    if stage in {"docs_generation", "pm_planning"}:
        context["directive"] = payload.directive
    if stage == "chief_engineer_review":
        context["directive"] = payload.directive
    if stage == "director_dispatch":
        requested_execution_mode = str(payload.director_workflow_execution_mode or "").strip().lower()
        context["execution_mode"] = (
            requested_execution_mode
            if requested_execution_mode in {"serial", "parallel"}
            else getattr(state.settings, "director_execution_mode", "parallel")
        )
        context["max_workers"] = getattr(
            state.settings, "director_max_parallel_tasks", DEFAULT_DIRECTOR_MAX_PARALLELISM
        )
        context["director_dispatch_driver"] = "task-market"
        context["dispatch_mode"] = "mainline-full"
        if int(payload.director_iterations) > 0:
            context["director_max_rounds"] = int(payload.director_iterations)
        director_dispatch_timeout = resolve_director_dispatch_timeout_seconds()
        context["timeout"] = director_dispatch_timeout
        context["director_dispatch_timeout_seconds"] = director_dispatch_timeout
        context["llm_call_timeout_seconds"] = director_dispatch_timeout
        context["director_llm_timeout_seconds"] = director_dispatch_timeout
    if stage == "quality_gate":
        context["qa_target"] = payload.directive or "Quality gate"
    return context


__all__ = [
    "_build_stage_context",
    "_build_stage_list",
    "_ensure_factory_runtime_ready",
    "_normalize_start_from",
    "_required_ready_roles_for_stages",
    "_settings_qa_enabled",
]
