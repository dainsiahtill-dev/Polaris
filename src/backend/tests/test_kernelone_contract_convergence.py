from __future__ import annotations

from polaris.kernelone.llm.engine.contracts import AIRequest as EngineAIRequest
from polaris.kernelone.llm.engine.contracts import AIResponse as EngineAIResponse
from polaris.kernelone.llm.engine.contracts import ErrorCategory as EngineErrorCategory
from polaris.kernelone.llm.engine.contracts import ModelSpec as EngineModelSpec
from polaris.kernelone.llm.engine.contracts import TaskType as EngineTaskType
from polaris.kernelone.llm.engine.contracts import Usage as EngineUsage
from polaris.kernelone.llm.toolkit.contracts import AIRequest as ToolkitAIRequest
from polaris.kernelone.llm.toolkit.contracts import AIResponse as ToolkitAIResponse
from polaris.kernelone.llm.toolkit.contracts import ErrorCategory as ToolkitErrorCategory
from polaris.kernelone.llm.toolkit.contracts import ModelSpec as ToolkitModelSpec
from polaris.kernelone.llm.toolkit.contracts import TaskType as ToolkitTaskType
from polaris.kernelone.llm.toolkit.contracts import Usage as ToolkitUsage
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
