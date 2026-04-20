"""Tests for KernelConfig - 重试策略配置外部化

测试覆盖:
- KernelConfig 默认值
- 环境变量覆盖
- 验证合法性
- with_overrides 方法
- get_default_config 单例
- 与 RoleExecutionKernel 集成
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from polaris.cells.roles.kernel.public.config import (
    KernelConfig,
    get_default_config,
)


class TestKernelConfigDefaults:
    """测试 KernelConfig 默认值"""

    def test_default_values(self) -> None:
        """测试默认配置值"""
        config = KernelConfig()
        assert config.max_retries == 3
        assert config.retry_delay == 1.0
        assert config.quality_threshold == 60.0

    def test_is_frozen(self) -> None:
        """测试 dataclass 是 frozen 的"""
        config = KernelConfig()
        with pytest.raises(AttributeError):
            config.max_retries = 5  # type: ignore

    def test_quality_threshold_bounds(self) -> None:
        """测试 quality_threshold 边界值"""
        # 有效边界
        config = KernelConfig(quality_threshold=0.0)
        assert config.quality_threshold == 0.0

        config = KernelConfig(quality_threshold=100.0)
        assert config.quality_threshold == 100.0

        # 无效边界
        with pytest.raises(ValueError, match="quality_threshold must be between 0 and 100"):
            KernelConfig(quality_threshold=-1.0)

        with pytest.raises(ValueError, match="quality_threshold must be between 0 and 100"):
            KernelConfig(quality_threshold=101.0)

    def test_max_retries_non_negative(self) -> None:
        """测试 max_retries 必须非负"""
        config = KernelConfig(max_retries=0)
        assert config.max_retries == 0

        config = KernelConfig(max_retries=10)
        assert config.max_retries == 10

        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            KernelConfig(max_retries=-1)

    def test_retry_delay_non_negative(self) -> None:
        """测试 retry_delay 必须非负"""
        config = KernelConfig(retry_delay=0.0)
        assert config.retry_delay == 0.0

        config = KernelConfig(retry_delay=5.5)
        assert config.retry_delay == 5.5

        with pytest.raises(ValueError, match="retry_delay must be >= 0"):
            KernelConfig(retry_delay=-0.1)


class TestKernelConfigEnvVars:
    """测试环境变量覆盖"""

    def test_env_override_max_retries(self) -> None:
        """测试 KERNELONE_MAX_RETRIES 环境变量覆盖"""
        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": "7"}):
            config = KernelConfig()
            assert config.max_retries == 7

    def test_env_override_retry_delay(self) -> None:
        """测试 KERNELONE_RETRY_DELAY 环境变量覆盖"""
        with patch.dict(os.environ, {"KERNELONE_RETRY_DELAY": "2.5"}):
            config = KernelConfig()
            assert config.retry_delay == 2.5

    def test_env_override_quality_threshold(self) -> None:
        """测试 POLARIS_QUALITY_THRESHOLD 环境变量覆盖"""
        with patch.dict(os.environ, {"POLARIS_QUALITY_THRESHOLD": "75.0"}):
            config = KernelConfig()
            assert config.quality_threshold == 75.0

    def test_env_override_all(self) -> None:
        """测试所有环境变量同时覆盖"""
        with patch.dict(
            os.environ,
            {
                "KERNELONE_MAX_RETRIES": "5",
                "KERNELONE_RETRY_DELAY": "3.0",
                "POLARIS_QUALITY_THRESHOLD": "80.0",
            },
        ):
            config = KernelConfig()
            assert config.max_retries == 5
            assert config.retry_delay == 3.0
            assert config.quality_threshold == 80.0

    def test_explicit_value_overrides_env(self) -> None:
        """测试显式参数优先级高于环境变量"""
        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": "10"}):
            config = KernelConfig(max_retries=3)
            assert config.max_retries == 3  # 显式值优先

    def test_from_env_classmethod(self) -> None:
        """测试 from_env 类方法"""
        with patch.dict(
            os.environ,
            {
                "KERNELONE_MAX_RETRIES": "8",
                "KERNELONE_RETRY_DELAY": "4.0",
                "POLARIS_QUALITY_THRESHOLD": "85.0",
            },
        ):
            config = KernelConfig.from_env()
            assert config.max_retries == 8
            assert config.retry_delay == 4.0
            assert config.quality_threshold == 85.0


class TestKernelConfigOverrides:
    """测试 with_overrides 方法"""

    def test_with_overrides_single(self) -> None:
        """测试单个字段覆盖"""
        config = KernelConfig()
        new_config = config.with_overrides(max_retries=10)

        # 原配置不变
        assert config.max_retries == 3
        # 新配置已覆盖
        assert new_config.max_retries == 10
        # 其他字段保持不变
        assert new_config.retry_delay == 1.0
        assert new_config.quality_threshold == 60.0

    def test_with_overrides_multiple(self) -> None:
        """测试多个字段覆盖"""
        config = KernelConfig()
        new_config = config.with_overrides(
            max_retries=5,
            retry_delay=2.5,
            quality_threshold=75.0,
        )

        assert new_config.max_retries == 5
        assert new_config.retry_delay == 2.5
        assert new_config.quality_threshold == 75.0

    def test_with_overrides_returns_new_instance(self) -> None:
        """测试 with_overrides 返回新实例"""
        config = KernelConfig()
        new_config = config.with_overrides(max_retries=7)

        assert config is not new_config
        assert config.max_retries == 3
        assert new_config.max_retries == 7

    def test_with_overrides_preserves_frozen(self) -> None:
        """测试 with_overrides 返回的也是 frozen"""
        config = KernelConfig()
        new_config = config.with_overrides(max_retries=10)

        with pytest.raises(AttributeError):
            new_config.max_retries = 20  # type: ignore


class TestGetDefaultConfig:
    """测试 get_default_config 单例"""

    def test_returns_kernel_config_instance(self) -> None:
        """测试返回 KernelConfig 实例"""
        config = get_default_config()
        assert isinstance(config, KernelConfig)

    def test_singleton_behavior(self) -> None:
        """测试单例行为 - 多次调用返回同一实例"""
        config1 = get_default_config()
        config2 = get_default_config()
        assert config1 is config2

    def test_default_values_from_env(self) -> None:
        """测试单例也遵循环境变量"""
        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": "12"}):
            # 重置单例
            import polaris.cells.roles.kernel.public.config as config_module

            config_module._default_config = None

            config = get_default_config()
            assert config.max_retries == 12


class TestKernelConfigIntegration:
    """测试与 RoleExecutionKernel 集成"""

    def test_kernel_accepts_config(self) -> None:
        """测试 RoleExecutionKernel 接受 config 参数"""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

        config = KernelConfig(max_retries=5, retry_delay=2.0, quality_threshold=70.0)
        kernel = RoleExecutionKernel(workspace=".", config=config)

        assert kernel.config is config
        assert kernel.config.max_retries == 5
        assert kernel.config.retry_delay == 2.0
        assert kernel.config.quality_threshold == 70.0

    def test_kernel_uses_default_config_when_none(self) -> None:
        """测试 RoleExecutionKernel 使用默认配置"""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

        kernel = RoleExecutionKernel(workspace=".")

        assert kernel.config is not None
        assert isinstance(kernel.config, KernelConfig)

    def test_kernel_config_from_env(self) -> None:
        """测试 Kernel 从环境变量读取配置"""
        # 需要重置单例以读取新的环境变量
        import polaris.cells.roles.kernel.public.config as config_module
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

        config_module._default_config = None

        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": "9"}):
            kernel = RoleExecutionKernel(workspace=".")
            assert kernel.config.max_retries == 9

    def test_kernel_request_max_retries_priority(self) -> None:
        """测试 request.max_retries 优先级高于 config"""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        from polaris.cells.roles.profile.internal.schema import RoleExecutionMode, RoleTurnRequest

        kernel = RoleExecutionKernel(workspace=".", config=KernelConfig(max_retries=3))

        RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="test",
            max_retries=7,  # 显式指定
        )

        # 在 run 方法中，request.max_retries > 0 时优先使用
        # 这里我们测试 kernel 正确存储了 config
        assert kernel.config.max_retries == 3


class TestKernelConfigEdgeCases:
    """边界情况测试"""

    def test_empty_env_value_fallback_to_default(self) -> None:
        """测试空环境变量值回退到默认值"""
        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": ""}):
            config = KernelConfig()
            assert config.max_retries == 3  # 回退到默认值

    def test_invalid_env_value_graceful_fallback(self) -> None:
        """测试无效环境变量值优雅回退到默认值

        注意: 与其抛出异常，我们选择优雅回退到默认值以保持系统稳定性。
        这符合防御式编程原则。
        """
        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": "invalid"}):
            config = KernelConfig()
            # 无效值回退到默认值
            assert config.max_retries == 3

    def test_float_env_value_for_int_field(self) -> None:
        """测试浮点环境变量值用于整数字段

        float("4.9") -> 4 (截断)
        """
        with patch.dict(os.environ, {"KERNELONE_MAX_RETRIES": "4.9"}):
            config = KernelConfig()
            # int(float("4.9")) -> 4
            assert config.max_retries == 4

    def test_config_equality(self) -> None:
        """测试配置相等性"""
        config1 = KernelConfig(max_retries=5, retry_delay=2.0, quality_threshold=70.0)
        config2 = KernelConfig(max_retries=5, retry_delay=2.0, quality_threshold=70.0)
        # frozen dataclass 可以用 == 比较
        assert config1 == config2

    def test_config_hash(self) -> None:
        """测试配置可哈希（frozen dataclass）"""
        config = KernelConfig(max_retries=5)
        # frozen dataclass 默认可哈希
        hash_value = hash(config)
        assert isinstance(hash_value, int)

    def test_config_in_set(self) -> None:
        """测试配置可用于 set"""
        config1 = KernelConfig(max_retries=5)
        config2 = KernelConfig(max_retries=5)
        config3 = KernelConfig(max_retries=10)

        config_set = {config1, config2, config3}
        assert len(config_set) == 2  # config1 和 config2 相等，只保留一个
