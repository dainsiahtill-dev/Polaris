"""Provider adapters for LLM transcript normalization.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §7 ProviderAdapter

Usage:
    from polaris.kernelone.llm.provider_adapters import get_adapter

    adapter = get_adapter("anthropic")
    request = adapter.build_request(state)
    # request['config']['messages'] contains properly formatted messages
"""

from __future__ import annotations

from polaris.kernelone.llm.provider_adapters.base import (
    DecodedProviderOutput,
    ProviderAdapter,
)
from polaris.kernelone.llm.provider_adapters.factory import (
    get_adapter,
    get_adapter_class,
    list_registered_adapters,
    provider_adapter,
)

__all__ = [
    # Base classes
    "DecodedProviderOutput",
    "ProviderAdapter",
    # Factory functions
    "get_adapter",
    "get_adapter_class",
    "list_registered_adapters",
    # Decorator
    "provider_adapter",
]
