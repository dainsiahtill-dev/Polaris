"""Director 业务常量。

定义 Director 工作流相关的路径和配置常量。

此模块从 polaris.kernelone.runtime.constants 迁移而来，
将 Polaris 业务语义与 KernelOne 技术层分离。

迁移历史:
     - 2026-03-27: 从 polaris.kernelone.runtime.constants 迁移
"""

from __future__ import annotations

from typing import Final

# ═══════════════════════════════════════════════════════════════════
# Director 工作流路径常量
# ═══════════════════════════════════════════════════════════════════

DIRECTOR_RUNTIME_DIR: Final[str] = "runtime"
"""运行时根目录"""

DIRECTOR_OUTPUT_DIR: Final[str] = "runtime/output"
"""Director 输出目录"""

DIRECTOR_CONTRACTS_DIR: Final[str] = "runtime/contracts"
"""契约目录"""

DIRECTOR_RESULTS_DIR: Final[str] = "runtime/results"
"""结果目录"""

DIRECTOR_LOGS_DIR: Final[str] = "runtime/logs"
"""日志目录"""

DIRECTOR_STATUS_DIR: Final[str] = "runtime/status"
"""状态目录"""

DIRECTOR_EVENTS_DIR: Final[str] = "runtime/events"
"""事件目录"""

# ═══════════════════════════════════════════════════════════════════
# 默认文件路径常量
# ═══════════════════════════════════════════════════════════════════

DEFAULT_PM_OUT: Final[str] = f"{DIRECTOR_CONTRACTS_DIR}/pm_tasks.contract.json"
"""PM 任务契约输出路径"""

DEFAULT_PM_REPORT: Final[str] = f"{DIRECTOR_RESULTS_DIR}/pm.report.md"
"""PM 报告路径"""

DEFAULT_PM_LOG: Final[str] = f"{DIRECTOR_EVENTS_DIR}/pm.events.jsonl"
"""PM 事件日志路径"""

DEFAULT_PM_SUBPROCESS_LOG: Final[str] = f"{DIRECTOR_LOGS_DIR}/pm.process.log"
"""PM 子进程日志路径"""

DEFAULT_DIRECTOR_SUBPROCESS_LOG: Final[str] = f"{DIRECTOR_LOGS_DIR}/director.process.log"
"""Director 子进程日志路径"""

DEFAULT_DIRECTOR_STATUS: Final[str] = f"{DIRECTOR_STATUS_DIR}/director.status.json"
"""Director 状态文件路径"""

DEFAULT_ENGINE_STATUS: Final[str] = f"{DIRECTOR_STATUS_DIR}/engine.status.json"
"""Engine 状态文件路径"""

DEFAULT_PLANNER: Final[str] = f"{DIRECTOR_RESULTS_DIR}/planner.output.md"
"""Planner 输出路径"""

DEFAULT_OLLAMA: Final[str] = f"{DIRECTOR_RESULTS_DIR}/director_llm.output.md"
"""LLM 输出路径"""

DEFAULT_RUNLOG: Final[str] = f"{DIRECTOR_LOGS_DIR}/director.runlog.md"
"""运行日志路径"""

DEFAULT_DIALOGUE: Final[str] = f"{DIRECTOR_EVENTS_DIR}/dialogue.transcript.jsonl"
"""对话记录路径"""

DEFAULT_PM_LLM_EVENTS: Final[str] = f"{DIRECTOR_EVENTS_DIR}/pm.llm.events.jsonl"
"""PM LLM 事件路径（已废弃，请使用 artifact_service.ARTIFACT_REGISTRY["audit.events.pm_llm"]）"""

DEFAULT_DIRECTOR_LLM_EVENTS: Final[str] = f"{DIRECTOR_EVENTS_DIR}/director.llm.events.jsonl"
"""Director LLM 事件路径（已废弃，请使用 artifact_service.ARTIFACT_REGISTRY["audit.events.director_llm"]）"""

DEFAULT_RUNTIME_EVENTS: Final[str] = f"{DIRECTOR_EVENTS_DIR}/runtime.events.jsonl"
"""运行时事件路径"""

# 生命周期文件
DEFAULT_DIRECTOR_LIFECYCLE: Final[str] = f"{DIRECTOR_RUNTIME_DIR}/DIRECTOR_LIFECYCLE.json"
"""Director 生命周期文件路径"""

# 契约相关
DEFAULT_PLAN: Final[str] = f"{DIRECTOR_CONTRACTS_DIR}/plan.md"
"""计划契约路径"""

DEFAULT_GAP: Final[str] = f"{DIRECTOR_CONTRACTS_DIR}/gap_report.md"
"""Gap 报告路径"""

DEFAULT_QA: Final[str] = f"{DIRECTOR_RESULTS_DIR}/qa.review.md"
"""QA 审查路径"""

DEFAULT_REQUIREMENTS: Final[str] = "workspace/docs/product/requirements.md"
"""需求文档路径"""

AGENTS_DRAFT_REL: Final[str] = f"{DIRECTOR_CONTRACTS_DIR}/agents.generated.md"
"""生成的 Agent 草稿路径"""

AGENTS_FEEDBACK_REL: Final[str] = f"{DIRECTOR_CONTRACTS_DIR}/agents.feedback.md"
"""Agent 反馈路径"""

WORKSPACE_STATUS_REL: Final[str] = "workspace/meta/workspace_status.json"
"""工作区状态路径"""

# ═══════════════════════════════════════════════════════════════════
# Director 阶段枚举
# ═══════════════════════════════════════════════════════════════════


class DirectorPhase:
    """Director 工作阶段枚举。

    定义 Director 工作流的各个阶段。
    """

    INIT: Final[str] = "init"
    """初始化阶段"""

    PLANNING: Final[str] = "planning"
    """规划阶段"""

    EXECUTING: Final[str] = "executing"
    """执行阶段"""

    REVIEWING: Final[str] = "reviewing"
    """审查阶段"""

    COMPLETING: Final[str] = "completing"
    """完成阶段"""

    FAILED: Final[str] = "failed"
    """失败阶段"""

    ALL: Final[tuple[str, ...]] = (INIT, PLANNING, EXECUTING, REVIEWING, COMPLETING, FAILED)
    """所有阶段列表"""


# ═══════════════════════════════════════════════════════════════════
# 通道文件映射
# ═══════════════════════════════════════════════════════════════════

CHANNEL_FILES: dict[str, str] = {
    # Legacy channels (still supported)
    "pm_report": DEFAULT_PM_REPORT,
    "pm_log": DEFAULT_PM_LOG,
    "pm_subprocess": DEFAULT_PM_SUBPROCESS_LOG,
    "pm_llm": DEFAULT_PM_LLM_EVENTS,
    "planner": DEFAULT_PLANNER,
    "ollama": DEFAULT_OLLAMA,
    "qa": DEFAULT_QA,
    "runlog": DEFAULT_RUNLOG,
    "dialogue": DEFAULT_DIALOGUE,
    "director_console": DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    "director_llm": DEFAULT_DIRECTOR_LLM_EVENTS,
    "engine_status": DEFAULT_ENGINE_STATUS,
    "runtime_events": DEFAULT_RUNTIME_EVENTS,
    # New unified channels (CanonicalLogEventV2)
    "system": "runtime/runs/{run_id}/logs/journal.norm.jsonl",
    "process": "runtime/runs/{run_id}/logs/journal.norm.jsonl",
    "llm": "runtime/runs/{run_id}/logs/journal.norm.jsonl",
}
"""通道文件映射表"""

NEW_CHANNEL_METADATA: dict[str, dict[str, str | list[str]]] = {
    "system": {
        "description": "System events (runtime, engine status, PM reports)",
        "severity_levels": ["debug", "info", "warn", "error", "critical"],
    },
    "process": {
        "description": "Process output (subprocess stdout/stderr)",
        "severity_levels": ["debug", "info", "warn", "error"],
    },
    "llm": {
        "description": "LLM interaction events",
        "severity_levels": ["debug", "info", "warn", "error"],
    },
}
"""新通道元数据映射"""


__all__ = [
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    # 通道映射
    "CHANNEL_FILES",
    "DEFAULT_DIALOGUE",
    "DEFAULT_DIRECTOR_LIFECYCLE",
    "DEFAULT_DIRECTOR_LLM_EVENTS",
    "DEFAULT_DIRECTOR_STATUS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DEFAULT_ENGINE_STATUS",
    "DEFAULT_GAP",
    "DEFAULT_OLLAMA",
    "DEFAULT_PLAN",
    "DEFAULT_PLANNER",
    "DEFAULT_PM_LLM_EVENTS",
    "DEFAULT_PM_LOG",
    # 默认文件路径
    "DEFAULT_PM_OUT",
    "DEFAULT_PM_REPORT",
    "DEFAULT_PM_SUBPROCESS_LOG",
    "DEFAULT_QA",
    "DEFAULT_REQUIREMENTS",
    "DEFAULT_RUNLOG",
    "DEFAULT_RUNTIME_EVENTS",
    "DIRECTOR_CONTRACTS_DIR",
    "DIRECTOR_EVENTS_DIR",
    "DIRECTOR_LOGS_DIR",
    "DIRECTOR_OUTPUT_DIR",
    "DIRECTOR_RESULTS_DIR",
    # 目录常量
    "DIRECTOR_RUNTIME_DIR",
    "DIRECTOR_STATUS_DIR",
    "NEW_CHANNEL_METADATA",
    "WORKSPACE_STATUS_REL",
    # 阶段枚举
    "DirectorPhase",
]
