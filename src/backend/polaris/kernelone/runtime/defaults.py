"""Canonical runtime defaults shared across Polaris layers."""

from __future__ import annotations

import os

from polaris.domain.director.constants import (
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    CHANNEL_FILES,
    DEFAULT_DIALOGUE,
    DEFAULT_DIRECTOR_LLM_EVENTS,
    DEFAULT_DIRECTOR_STATUS,
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    DEFAULT_ENGINE_STATUS,
    DEFAULT_GAP,
    DEFAULT_OLLAMA,
    DEFAULT_PLAN,
    DEFAULT_PLANNER,
    DEFAULT_PM_LLM_EVENTS,
    DEFAULT_PM_LOG,
    DEFAULT_PM_OUT,
    DEFAULT_PM_REPORT,
    DEFAULT_PM_SUBPROCESS_LOG,
    DEFAULT_QA,
    DEFAULT_REQUIREMENTS,
    DEFAULT_RUNLOG,
    DEFAULT_RUNTIME_EVENTS,
    NEW_CHANNEL_METADATA,
    WORKSPACE_STATUS_REL,
)
from polaris.kernelone._runtime_config import get_workspace
from polaris.kernelone.storage.io_paths import find_workspace_root

DEFAULT_MODEL = "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest"


def resolve_default_workspace(start: str | None = None) -> str:
    # Priority: KERNELONE_WORKSPACE env var (via _runtime_config), then KERNELONE_WORKSPACE fallback
    explicit = get_workspace()
    if explicit:
        return os.path.abspath(explicit)
    start_path = str(start or os.getcwd())
    return find_workspace_root(start_path) or os.path.abspath(start_path)


_BOOTSTRAP_WORKSPACE = get_workspace()
DEFAULT_WORKSPACE = os.path.abspath(_BOOTSTRAP_WORKSPACE or os.getcwd())

__all__ = list(
    dict.fromkeys(
        [
            "AGENTS_DRAFT_REL",
            "AGENTS_FEEDBACK_REL",
            "CHANNEL_FILES",
            "DEFAULT_DIALOGUE",
            "DEFAULT_DIRECTOR_LLM_EVENTS",
            "DEFAULT_DIRECTOR_STATUS",
            "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
            "DEFAULT_ENGINE_STATUS",
            "DEFAULT_GAP",
            "DEFAULT_MODEL",
            "DEFAULT_OLLAMA",
            "DEFAULT_PLAN",
            "DEFAULT_PLANNER",
            "DEFAULT_PM_LLM_EVENTS",
            "DEFAULT_PM_LOG",
            "DEFAULT_PM_OUT",
            "DEFAULT_PM_REPORT",
            "DEFAULT_PM_SUBPROCESS_LOG",
            "DEFAULT_QA",
            "DEFAULT_REQUIREMENTS",
            "DEFAULT_RUNLOG",
            "DEFAULT_RUNTIME_EVENTS",
            "DEFAULT_WORKSPACE",
            "NEW_CHANNEL_METADATA",
            "WORKSPACE_STATUS_REL",
            "resolve_default_workspace",
        ]
    )
)
