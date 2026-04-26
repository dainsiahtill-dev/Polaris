"""Application-level provider bridge.

通过 AppLLMRuntimeAdapter 提供 provider manager 能力，
不直接依赖 kernelone.llm.providers 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.infrastructure.llm.providers import ProviderManager


def get_provider_manager() -> ProviderManager:  # type: ignore[valid-type]
    """获取 infrastructure ProviderManager 单例。

    直接委托给 polaris.infrastructure.llm.providers.provider_manager，
    绕过 kernelone 层确保单例唯一性。
    """
    from polaris.infrastructure.llm.providers import provider_manager as _infra_pm

    return _infra_pm


__all__ = [
    "get_provider_manager",
]
