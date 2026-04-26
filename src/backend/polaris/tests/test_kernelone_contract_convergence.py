from __future__ import annotations

from polaris.kernelone.llm.engine.contracts import (
    AIRequest as EngineAIRequest,
    AIResponse as EngineAIResponse,
    ErrorCategory as EngineErrorCategory,
    ModelSpec as EngineModelSpec,
    TaskType as EngineTaskType,
    Usage as EngineUsage,
)
from polaris.kernelone.llm.toolkit.contracts import (
    AIRequest as ToolkitAIRequest,
    AIResponse as ToolkitAIResponse,
    ErrorCategory as ToolkitErrorCategory,
    ModelSpec as ToolkitModelSpec,
    TaskType as ToolkitTaskType,
    Usage as ToolkitUsage,
)
from polaris.kernelone.llm.types import Usage as ProviderUsage


def test_toolkit_contracts_reuse_engine_single_source_of_truth() -> None:
    assert ToolkitTaskType is EngineTaskType
    assert ToolkitModelSpec is EngineModelSpec
    assert ToolkitAIRequest is EngineAIRequest
    assert ToolkitUsage is EngineUsage
    assert ToolkitAIResponse is EngineAIResponse
    assert ProviderUsage is EngineUsage


def test_toolkit_error_category_reuses_engine_single_source_of_truth() -> None:
    assert ToolkitErrorCategory is EngineErrorCategory
