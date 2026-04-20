"""Role Framework Base - 角色基类

定义所有角色必须实现的通用接口。

设计约束：
- KernelOne 通用角色框架，不嵌入特定产品命名
- metadata_root 由子类或装配层注入，不在此模块硬编码特定值
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

#: Default metadata root. Subclasses or assembly layer should override via
#: constructor. KERNELONE_METADATA_ROOT env var takes precedence.
_DEFAULT_METADATA_ROOT = os.environ.get(
    "KERNELONE_METADATA_ROOT",
    ".polaris/metadata",
)


class RoleState(Enum):
    """角色状态"""

    UNINITIALIZED = auto()
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    ERROR = auto()
    STOPPED = auto()


class RoleCapability(Enum):
    """角色能力标识"""

    STATUS = auto()  # 支持状态查询
    TASKS = auto()  # 支持任务管理
    DOCUMENTS = auto()  # 支持文档管理
    EXECUTE = auto()  # 支持执行操作
    QUERY = auto()  # 支持查询操作


@dataclass
class RoleInfo:
    """角色信息"""

    name: str
    version: str
    description: str
    capabilities: list[RoleCapability] = field(default_factory=list)


class RoleBase(ABC):
    """角色基类

    所有角色都必须继承此类。
    提供统一的接口规范，支持自动化的CLI/FastAPI/TUI生成。

    Args:
        workspace: 工作区根路径
        role_name: 角色标识名
        metadata_root: 元数据目录根（可选，默认从 KERNELONE_METADATA_ROOT
            环境变量读取，兜底为 ".polaris/metadata"）
    """

    def __init__(
        self,
        workspace: str,
        role_name: str,
        *,
        metadata_root: str | None = None,
    ) -> None:
        self.workspace = Path(workspace).absolute()
        self.role_name = role_name
        self._metadata_root = metadata_root or _DEFAULT_METADATA_ROOT
        self._state = RoleState.UNINITIALIZED
        self._state_listeners: list[Callable[[RoleState], None]] = []

    # ===== 状态管理 =====

    @property
    def state(self) -> RoleState:
        """获取当前状态"""
        return self._state

    def _set_state(self, new_state: RoleState) -> None:
        """设置状态并通知监听器"""
        self._state = new_state
        for listener in self._state_listeners:
            listener(new_state)

    def add_state_listener(self, listener: Callable[[RoleState], None]) -> None:
        """添加状态监听器"""
        self._state_listeners.append(listener)

    # ===== 核心接口 (必须实现) =====

    @abstractmethod
    def get_info(self) -> RoleInfo:
        """获取角色信息"""
        pass

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """获取状态信息

        Returns:
            必须包含: name, version, state
            可选包含: stats, health, metrics等
        """
        pass

    @abstractmethod
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        pass

    @abstractmethod
    def initialize(self, **kwargs) -> dict[str, Any]:
        """初始化角色

        Returns:
            初始化结果，包含success, message等
        """
        pass

    # ===== 能力检查 =====

    def has_capability(self, capability: RoleCapability) -> bool:
        """检查是否支持某项能力"""
        return capability in self.get_info().capabilities

    def require_capability(self, capability: RoleCapability) -> None:
        """要求必须具备某项能力，否则抛出异常"""
        if not self.has_capability(capability):
            raise RuntimeError(f"Role {self.role_name} does not support {capability}")

    # ===== 通用工具方法 =====

    def ensure_workspace(self) -> Path:
        """确保工作区存在"""
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace

    def get_data_dir(self) -> Path:
        """获取数据目录（由 metadata_root 决定根路径）"""
        data_dir = self.workspace / self._metadata_root / self.role_name
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    def get_log_path(self) -> Path:
        """获取日志文件路径"""
        return self.get_data_dir() / "logs" / f"{self.role_name}.log"
