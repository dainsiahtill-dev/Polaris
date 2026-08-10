"""AI Agent 专项压测主运行器

Usage:
    # 运行完整压测 (默认建议 3 轮一批，批后审计)
    python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --rounds 3

    # 仅运行角色探针（独立入口）
    python -m tests.agent_stress.probe

    # 从指定轮次恢复
    python -m tests.agent_stress.runner --resume-from 5

    # 指定项目池选择策略
    python -m tests.agent_stress.runner --strategy rotation

    # 只跑特定类别
    python -m tests.agent_stress.runner --category crud,security

This package is the lossless successor of the former ``runner`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...agent_stress.runner`` and ``from ...agent_stress.runner import X``
keep resolving identically for all external importers. The one-time
``ensure_backend_root_on_syspath()`` side effect that previously ran at
module import runs here, exactly once, at package import.
"""

# mypy: ignore-errors

from __future__ import annotations

# Backward-compatible re-export of stdlib / typing names that were
# module-level attributes of the former ``runner`` module.
import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path

from ..paths import ensure_backend_root_on_syspath

# Import-time side effect (exactly once, same order as former module).
ensure_backend_root_on_syspath()

from ..backend_bootstrap import (  # noqa: E402
    BackendBootstrapError,
    ManagedBackendSession,
    ensure_backend_session,
)
from ..backend_context import resolve_backend_context  # noqa: E402
from ..contracts import normalize_status  # noqa: E402
from ..engine import RoundResult, StageResult, StressEngine  # noqa: E402
from ..preflight import BackendPreflightProbe, BackendPreflightStatus  # noqa: E402
from ..probe import ProbeStatus, RoleAvailabilityProbe  # noqa: E402
from ..project_pool import (  # noqa: E402
    PROJECT_POOL,
    ProjectCategory,
    ProjectDefinition,
    select_stress_rounds,
    validate_round_sequence,
)
from ..stress_path_policy import (  # noqa: E402
    default_stress_runtime_root,
    default_stress_workspace_base,
    ensure_stress_workspace_path,
)
from ._cli import main  # noqa: E402
from ._constants import (  # noqa: E402
    DEFAULT_NON_LLM_TIMEOUT_SECONDS,
    DEFAULT_STRESS_RAMDISK,
    DEFAULT_STRESS_WORKSPACE,
    MAX_NON_LLM_TIMEOUT_SECONDS,
    PROBE_MIGRATION_MESSAGE,
)
from ._runner import AgentStressRunner  # noqa: E402

__all__ = [
    "DEFAULT_NON_LLM_TIMEOUT_SECONDS",
    "DEFAULT_STRESS_RAMDISK",
    "DEFAULT_STRESS_WORKSPACE",
    "MAX_NON_LLM_TIMEOUT_SECONDS",
    "PROBE_MIGRATION_MESSAGE",
    "PROJECT_POOL",
    "AgentStressRunner",
    "Any",
    "BackendBootstrapError",
    "BackendPreflightProbe",
    "BackendPreflightStatus",
    "ManagedBackendSession",
    "Path",
    "ProbeStatus",
    "ProjectCategory",
    "ProjectDefinition",
    "RoleAvailabilityProbe",
    "RoundResult",
    "StageResult",
    "StressEngine",
    "argparse",
    "asyncio",
    "datetime",
    "default_stress_runtime_root",
    "default_stress_workspace_base",
    "ensure_backend_root_on_syspath",
    "ensure_backend_session",
    "ensure_stress_workspace_path",
    "json",
    "main",
    "normalize_status",
    "os",
    "re",
    "resolve_backend_context",
    "resolve_runtime_path",
    "select_stress_rounds",
    "sys",
    "timezone",
    "traceback",
    "validate_round_sequence",
]
