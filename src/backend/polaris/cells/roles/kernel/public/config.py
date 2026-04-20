"""Kernel Configuration - 可外部化配置

提供 Kernel 级别的可配置参数，支持:
- 环境变量覆盖
- Profile 级别覆盖
- 程序化配置注入

Usage:
    # 环境变量方式
    export KERNELONE_MAX_RETRIES=5
    export KERNELONE_RETRY_DELAY=2.0
    export POLARIS_QUALITY_THRESHOLD=70.0

    # 程序化方式
    config = KernelConfig(max_retries=5, retry_delay=2.0, quality_threshold=70.0)
    kernel = RoleExecutionKernel(workspace=".", config=config)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field


def _parse_int_env(var_name: str, default: int) -> int:
    """解析整数环境变量，处理空值和无效值"""
    raw = os.getenv(var_name, "")
    if not raw:
        return default
    try:
        return int(float(raw))  # 支持 "4.9" -> 4
    except (ValueError, TypeError):
        return default


def _parse_float_env(var_name: str, default: float) -> float:
    """解析浮点环境变量，处理空值和无效值"""
    raw = os.getenv(var_name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class KernelConfig:
    """Kernel 执行配置

    所有配置项均支持:
    1. 直接赋值 (最高优先级)
    2. 环境变量回退 (次优先级)
    3. 默认值 (最低优先级)

    配置优先级: 构造函数参数 > 环境变量 > 类默认值

    Attributes:
        max_retries: 验证失败时最大重试次数 (默认: 3)
        retry_delay: 重试间隔时间，秒 (默认: 1.0)
        quality_threshold: 质量评分及格线，0-100 (默认: 60.0)

    Environment Variables:
        KERNELONE_MAX_RETRIES: 最大重试次数
        KERNELONE_RETRY_DELAY: 重试间隔秒数
        POLARIS_QUALITY_THRESHOLD: 质量阈值
    """

    max_retries: int = field(default_factory=lambda: _parse_int_env("KERNELONE_MAX_RETRIES", 3))
    retry_delay: float = field(default_factory=lambda: _parse_float_env("KERNELONE_RETRY_DELAY", 1.0))
    quality_threshold: float = field(default_factory=lambda: _parse_float_env("POLARIS_QUALITY_THRESHOLD", 60.0))

    def __post_init__(self) -> None:
        """验证配置值合法性"""
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.retry_delay < 0:
            raise ValueError("retry_delay must be >= 0")
        if not 0 <= self.quality_threshold <= 100:
            raise ValueError("quality_threshold must be between 0 and 100")

    @classmethod
    def from_env(cls) -> KernelConfig:
        """从环境变量创建配置实例

        Returns:
            基于环境变量值的新配置实例
        """
        return cls()

    def with_overrides(self, **kwargs: int | float) -> KernelConfig:
        """创建带部分覆盖的新配置

        Args:
            **kwargs: 要覆盖的配置项

        Returns:
            合并后的新配置实例 (原实例不变)

        Example:
            config = KernelConfig()
            config_with_overrides = config.with_overrides(max_retries=5)
        """
        import dataclasses

        # 获取当前配置的所有字段值
        current_values = dataclasses.asdict(self)
        # 合并覆盖值
        for key, value in kwargs.items():
            if key in current_values:
                current_values[key] = value

        return KernelConfig(**current_values)


# 默认配置实例 (延迟初始化，线程安全)
_default_config: KernelConfig | None = None
_config_lock = threading.Lock()


def get_default_config() -> KernelConfig:
    """获取全局默认配置实例

    Returns:
        线程安全的默认配置单例

    Note:
        使用双重检查锁定模式确保线程安全
    """
    global _default_config
    if _default_config is None:
        with _config_lock:
            # 双重检查
            if _default_config is None:
                _default_config = KernelConfig()
    return _default_config
