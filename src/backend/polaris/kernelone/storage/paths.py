"""统一存储路径解析

所有存储路径解析必须使用此模块。

提供统一的路径解析函数，用于:
- runtime/signals/ - 角色信号
- runtime/artifacts/ - 工件存储
- runtime/sessions/ - 会话存储
- runtime/tasks/ - 任务板
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

WORKSPACE_SIGNALS: Final[str] = "runtime/signals"
WORKSPACE_ARTIFACTS: Final[str] = "runtime/artifacts"
WORKSPACE_SESSIONS: Final[str] = "runtime/sessions"
WORKSPACE_TASKS: Final[str] = "runtime/tasks"


def resolve_signal_path(
    workspace: str,
    role: str,
    stage: str,
) -> Path:
    """解析signal文件路径。

    Args:
        workspace: 工作区根路径
        role: 角色标识符
        stage: 阶段标识符

    Returns:
        signal文件完整路径: runtime/signals/{stage}.{role}.signals.json
    """
    return Path(workspace) / WORKSPACE_SIGNALS / f"{stage}.{role}.signals.json"


def resolve_artifact_path(
    workspace: str,
    artifact_id: str,
) -> Path:
    """解析artifact文件路径。

    Args:
        workspace: 工作区根路径
        artifact_id: 工件标识符

    Returns:
        artifact文件完整路径: runtime/artifacts/{artifact_id}
    """
    return Path(workspace) / WORKSPACE_ARTIFACTS / artifact_id


def resolve_session_path(
    workspace: str,
    session_id: str,
) -> Path:
    """解析session路径。

    Args:
        workspace: 工作区根路径
        session_id: 会话标识符

    Returns:
        session目录完整路径: runtime/sessions/{session_id}
    """
    return Path(workspace) / WORKSPACE_SESSIONS / session_id


def resolve_taskboard_path(
    workspace: str,
) -> Path:
    """解析taskboard路径。

    Args:
        workspace: 工作区根路径

    Returns:
        taskboard完整路径: runtime/tasks/taskboard.json
    """
    return Path(workspace) / WORKSPACE_TASKS / "taskboard.json"


def resolve_runtime_path(
    workspace: str,
    relative_path: str,
) -> Path:
    """解析runtime相对路径。

    Args:
        workspace: 工作区根路径
        relative_path: runtime目录下的相对路径

    Returns:
        完整的runtime路径
    """
    return Path(workspace) / "runtime" / relative_path


__all__ = [
    "resolve_artifact_path",
    "resolve_runtime_path",
    "resolve_session_path",
    "resolve_signal_path",
    "resolve_taskboard_path",
]
