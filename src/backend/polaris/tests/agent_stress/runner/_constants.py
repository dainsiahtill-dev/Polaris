"""Shared constants for agent_stress AgentStressRunner package."""

# mypy: ignore-errors

from __future__ import annotations

from ..stress_path_policy import (
    default_stress_runtime_root,
    default_stress_workspace_base,
)

DEFAULT_STRESS_WORKSPACE = default_stress_workspace_base("tests-agent-stress")
DEFAULT_STRESS_RAMDISK = default_stress_runtime_root("tests-agent-stress-runtime")
MAX_NON_LLM_TIMEOUT_SECONDS = 120.0
DEFAULT_NON_LLM_TIMEOUT_SECONDS = 120.0
PROBE_MIGRATION_MESSAGE = (
    "[migration] `tests.agent_stress.runner` no longer supports `--probe-only/--json`.\n"
    "Please run probe via: python -m tests.agent_stress.probe"
)
