"""Canonical runtime defaults shared across Polaris layers."""

from __future__ import annotations

import os
from typing import Final

from polaris.kernelone._runtime_config import get_workspace
from polaris.kernelone.runtime.channel_contracts import (
    CHANNEL_FILES,
    HISTORICAL_CHANNEL_FILES,
    NEW_CHANNEL_METADATA,
)
from polaris.kernelone.storage.io_paths import find_workspace_root

# ═══════════════════════════════════════════════════════════════════
# Director workflow path constants (inlined from
# polaris.domain.director.constants to break reverse dependency).
#
# Canonical source: polaris.domain.director.constants
# Keep in sync with any changes there.
# Channel mappings are imported from polaris.kernelone.runtime.channel_contracts
# so runtime/defaults and director/constants cannot drift.
# ═══════════════════════════════════════════════════════════════════

_RUNTIME_DIR: Final[str] = "runtime"
_CONTRACTS_DIR: Final[str] = "runtime/contracts"
_RESULTS_DIR: Final[str] = "runtime/results"
_LOGS_DIR: Final[str] = "runtime/logs"
_STATUS_DIR: Final[str] = "runtime/status"
_EVENTS_DIR: Final[str] = "runtime/events"

DEFAULT_PM_OUT: Final[str] = f"{_CONTRACTS_DIR}/pm_tasks.contract.json"
DEFAULT_PM_REPORT: Final[str] = f"{_RESULTS_DIR}/pm.report.md"
DEFAULT_PM_LOG: Final[str] = f"{_EVENTS_DIR}/pm.events.jsonl"
DEFAULT_PM_SUBPROCESS_LOG: Final[str] = f"{_LOGS_DIR}/pm.process.log"
DEFAULT_DIRECTOR_SUBPROCESS_LOG: Final[str] = f"{_LOGS_DIR}/director.process.log"
DEFAULT_DIRECTOR_STATUS: Final[str] = f"{_STATUS_DIR}/director.status.json"
DEFAULT_ENGINE_STATUS: Final[str] = f"{_STATUS_DIR}/engine.status.json"
DEFAULT_PLANNER: Final[str] = f"{_RESULTS_DIR}/planner.output.md"
DEFAULT_OLLAMA: Final[str] = f"{_RESULTS_DIR}/director_llm.output.md"
DEFAULT_RUNLOG: Final[str] = f"{_LOGS_DIR}/director.runlog.md"
DEFAULT_DIALOGUE: Final[str] = f"{_EVENTS_DIR}/dialogue.transcript.jsonl"
DEFAULT_PM_LLM_EVENTS: Final[str] = f"{_EVENTS_DIR}/pm.llm.events.jsonl"
DEFAULT_DIRECTOR_LLM_EVENTS: Final[str] = f"{_EVENTS_DIR}/director.llm.events.jsonl"
DEFAULT_RUNTIME_EVENTS: Final[str] = f"{_EVENTS_DIR}/runtime.events.jsonl"
DEFAULT_PLAN: Final[str] = f"{_CONTRACTS_DIR}/plan.md"
DEFAULT_GAP: Final[str] = f"{_CONTRACTS_DIR}/gap_report.md"
DEFAULT_QA: Final[str] = f"{_RESULTS_DIR}/qa.review.md"
DEFAULT_REQUIREMENTS: Final[str] = "workspace/docs/product/requirements.md"
AGENTS_DRAFT_REL: Final[str] = f"{_CONTRACTS_DIR}/agents.generated.md"
AGENTS_FEEDBACK_REL: Final[str] = f"{_CONTRACTS_DIR}/agents.feedback.md"
WORKSPACE_STATUS_REL: Final[str] = "workspace/meta/workspace_status.json"

# ═══════════════════════════════════════════════════════════════════
# KernelOne-native defaults
# ═══════════════════════════════════════════════════════════════════

DEFAULT_MODEL: Final[str] = "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest"


def resolve_default_workspace(start: str | None = None) -> str:
    """Resolve the effective workspace directory.

    Priority: ``KERNELONE_WORKSPACE`` env var (via ``_runtime_config``),
    then ``find_workspace_root`` walk-up, then *start* / ``cwd``.
    """
    explicit = get_workspace()
    if explicit:
        return os.path.abspath(explicit)
    start_path = str(start or os.getcwd())
    return find_workspace_root(start_path) or os.path.abspath(start_path)


_BOOTSTRAP_WORKSPACE = get_workspace()
DEFAULT_WORKSPACE: Final[str] = os.path.abspath(_BOOTSTRAP_WORKSPACE or os.getcwd())

__all__ = list(
    dict.fromkeys(
        [
            "AGENTS_DRAFT_REL",
            "AGENTS_FEEDBACK_REL",
            "CHANNEL_FILES",
            "HISTORICAL_CHANNEL_FILES",
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
