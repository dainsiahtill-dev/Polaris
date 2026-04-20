"""Projection-scoped constants (independent from legacy core package init).

此文件从 polaris.domain.director.constants 导入核心常量，
仅保留 DEFAULT_WORKSPACE 这个需要动态获取 (os.getcwd()) 的特殊常量。
"""

from __future__ import annotations

import os

# 从 domain.director.constants 导入通用常量，保持单一事实来源
from polaris.domain.director.constants import (
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    CHANNEL_FILES,
    DEFAULT_DIALOGUE,
    DEFAULT_DIRECTOR_LLM_EVENTS,
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    DEFAULT_ENGINE_STATUS,
    DEFAULT_OLLAMA,
    DEFAULT_PLANNER,
    DEFAULT_PM_LLM_EVENTS,
    DEFAULT_PM_LOG,
    DEFAULT_PM_OUT,
    DEFAULT_PM_REPORT,
    DEFAULT_PM_SUBPROCESS_LOG,
    DEFAULT_QA,
    DEFAULT_RUNLOG,
    DEFAULT_RUNTIME_EVENTS,
)

# Projection Cell 特有：动态工作目录（需要 os.getcwd()，无法在模块级定义）
DEFAULT_WORKSPACE: str = str(os.getcwd())

__all__ = [
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    "CHANNEL_FILES",
    "DEFAULT_DIALOGUE",
    "DEFAULT_DIRECTOR_LLM_EVENTS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DEFAULT_ENGINE_STATUS",
    "DEFAULT_OLLAMA",
    "DEFAULT_PLANNER",
    "DEFAULT_PM_LLM_EVENTS",
    "DEFAULT_PM_LOG",
    "DEFAULT_PM_OUT",
    "DEFAULT_PM_REPORT",
    "DEFAULT_PM_SUBPROCESS_LOG",
    "DEFAULT_QA",
    "DEFAULT_RUNLOG",
    "DEFAULT_RUNTIME_EVENTS",
    "DEFAULT_WORKSPACE",
]
