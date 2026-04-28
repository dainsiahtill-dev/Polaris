"""Polaris AI Agent 专项压测框架

覆盖 Polaris 当前 Factory 主链的自动化多项目压测系统。

当前事实基线：
- 入口是 `python -m polaris.tests.agent_stress.runner`
- 主执行链由 Polaris 当前 Factory 编排决定，通常为 `Architect/Court -> PM -> Director -> QA`
- `Chief Engineer` 仅在 Polaris 当前运行路径按需插入
- 压测框架只通过 Polaris 对外 HTTP API 驱动，不直接调用内部 CLI

Usage:
    # 运行完整压测
    python -m polaris.tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --rounds 20

    # 仅运行角色探针（独立入口）
    python -m polaris.tests.agent_stress.probe

    # 默认自动启用人类观测窗口
    python -m polaris.tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --rounds 20

    # 从指定轮次恢复
    python -m polaris.tests.agent_stress.runner --resume-from 5
"""

from importlib import import_module
from typing import Any

__version__ = "1.0.0"
__all__ = [
    "PROJECT_POOL",
    "AgentStressRunner",
    "BackendPreflightProbe",
    "BackendPreflightStatus",
    "Enhancement",
    "ProbeStatus",
    "ProjectCategory",
    "ProjectDefinition",
    "RoleAvailabilityProbe",
    "RoundResult",
    "StressEngine",
    "select_stress_rounds",
    "validate_round_sequence",
]

_EXPORT_MAP = {
    "AgentStressRunner": ("polaris.tests.agent_stress.runner", "AgentStressRunner"),
    "RoleAvailabilityProbe": ("polaris.tests.agent_stress.probe", "RoleAvailabilityProbe"),
    "ProbeStatus": ("polaris.tests.agent_stress.probe", "ProbeStatus"),
    "StressEngine": ("polaris.tests.agent_stress.engine", "StressEngine"),
    "RoundResult": ("polaris.tests.agent_stress.engine", "RoundResult"),
    "PROJECT_POOL": ("polaris.tests.agent_stress.project_pool", "PROJECT_POOL"),
    "ProjectCategory": ("polaris.tests.agent_stress.project_pool", "ProjectCategory"),
    "ProjectDefinition": ("polaris.tests.agent_stress.project_pool", "ProjectDefinition"),
    "Enhancement": ("polaris.tests.agent_stress.project_pool", "Enhancement"),
    "select_stress_rounds": ("polaris.tests.agent_stress.project_pool", "select_stress_rounds"),
    "validate_round_sequence": ("polaris.tests.agent_stress.project_pool", "validate_round_sequence"),
    "BackendPreflightProbe": ("polaris.tests.agent_stress.preflight", "BackendPreflightProbe"),
    "BackendPreflightStatus": ("polaris.tests.agent_stress.preflight", "BackendPreflightStatus"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORT_MAP:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
