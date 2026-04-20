"""Application-level provider actions.

通过 kernelone provider manager + adapter bridge 执行 provider 操作。
不使用 infrastructure provider_manager 直接依赖。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from polaris.cells.llm.provider_runtime.public.contracts import UnsupportedProviderTypeError

logger = logging.getLogger(__name__)

ProviderAction = Literal["health", "models"]


def run_provider_action(
    *,
    action: ProviderAction,
    provider_type: str,
    provider_cfg: dict[str, Any],
    api_key: str | None,
) -> dict[str, Any]:
    """通过 kernelone provider manager 执行 provider 操作.

    使用 AppLLMRuntimeAdapter 获取 provider 实例，
    而不是直接依赖 infrastructure.provider_manager。
    """
    # 通过 AppLLMRuntimeAdapter 获取 provider 实例
    try:
        from polaris.infrastructure.llm import AppLLMRuntimeAdapter

        adapter = AppLLMRuntimeAdapter()
        provider_instance = adapter.get_provider_instance(provider_type)

        if provider_instance:
            if action == "health":
                return provider_instance.health(provider_cfg).to_dict()
            return provider_instance.list_models(provider_cfg).to_dict()
    except (RuntimeError, ValueError) as e:
        logger.debug("Provider manager action failed, falling back to direct implementation: %s", e)

    # 回退到直接实现（用于不支持的 provider 类型）
    if provider_type == "ollama":
        from polaris.infrastructure.llm.providers.ollama import ollama_health, ollama_list_models

        if action == "health":
            return ollama_health(provider_cfg).to_dict()
        return ollama_list_models(provider_cfg).to_dict()

    if provider_type == "openai_compat":
        from polaris.infrastructure.llm.providers.openai_compat import openai_health, openai_list_models

        if action == "health":
            return openai_health(provider_cfg, api_key).to_dict()
        return openai_list_models(provider_cfg, api_key).to_dict()

    if provider_type == "anthropic_compat":
        from polaris.infrastructure.llm.providers.anthropic_compat import anthropic_health, anthropic_list_models

        if action == "health":
            return anthropic_health(provider_cfg, api_key).to_dict()
        return anthropic_list_models(provider_cfg, api_key).to_dict()

    raise UnsupportedProviderTypeError(provider_type)
