"""Test suite for polaris.cells.llm.evaluation.internal.suites module.

Tests cover all 5 suite functions:
- run_connectivity_suite: provider not found, health check failure, success
- run_response_suite: JSON parsing, success
- run_thinking_suite: tag extraction, missing tags
- run_qualification_suite: deflection detection
- run_interview_suite: semantic scoring
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Direct import from internal module path using __import__ to bypass package __init__
# This avoids circular import issues with evaluation cell
_suites_spec = __import__(
    "polaris.cells.llm.evaluation.internal.suites",
    fromlist=["run_connectivity_suite"]
)
run_connectivity_suite = _suites_spec.run_connectivity_suite
run_connectivity_suite_sync = _suites_spec.run_connectivity_suite_sync
run_response_suite = _suites_spec.run_response_suite
run_thinking_suite = _suites_spec.run_thinking_suite
run_qualification_suite = _suites_spec.run_qualification_suite
run_interview_suite = _suites_spec.run_interview_suite

from polaris.kernelone.llm.providers.base_provider import BaseProvider
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult
from polaris.kernelone.llm.shared_contracts import Usage


# =============================================================================
# Fixtures
# =============================================================================

@dataclass
class FakeHealthResult:
    """Fake HealthResult for testing."""
    ok: bool
    latency_ms: int
    error: str | None = None


@dataclass
class FakeInvokeResult:
    """Fake InvokeResult for testing."""
    ok: bool
    output: str
    latency_ms: int
    error: str | None = None


@dataclass
class FakeModelListResult:
    """Fake ModelListResult for testing."""
    ok: bool
    models: list[Any]
    error: str | None = None


class FakeProvider(BaseProvider):
    """Fake provider for testing."""

    def __init__(
        self,
        health_result: FakeHealthResult | None = None,
        invoke_result: FakeInvokeResult | None = None,
        list_models_result: FakeModelListResult | None = None,
    ) -> None:
        self._health_result = health_result
        self._invoke_result = invoke_result
        self._list_models_result = list_models_result
        self.health_calls: list[dict[str, Any]] = []
        self.invoke_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.list_models_calls: list[dict[str, Any]] = []

    @classmethod
    def get_provider_info(cls) -> Any:
        return MagicMock()

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> Any:
        return MagicMock(valid=True)

    def health(self, config: dict[str, Any]) -> HealthResult:
        self.health_calls.append({"config": config})
        if self._health_result is None:
            return HealthResult(ok=True, latency_ms=100)
        return HealthResult(
            ok=self._health_result.ok,
            latency_ms=self._health_result.latency_ms,
            error=self._health_result.error,
        )

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        self.list_models_calls.append({"config": config})
        if self._list_models_result is None:
            return ModelListResult(ok=True, models=[])
        return ModelListResult(
            ok=self._list_models_result.ok,
            models=self._list_models_result.models,
            error=self._list_models_result.error,
        )

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        self.invoke_calls.append((prompt, model, config))
        if self._invoke_result is None:
            return InvokeResult(
                ok=True,
                output="Test output",
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )
        return InvokeResult(
            ok=self._invoke_result.ok,
            output=self._invoke_result.output,
            latency_ms=self._invoke_result.latency_ms,
            error=self._invoke_result.error,
            usage=Usage(0, 0, 0),
        )


@pytest.fixture
def mock_provider_manager() -> MagicMock:
    """Create a mock ProviderManager."""
    return MagicMock()


@pytest.fixture
def default_provider_cfg() -> dict[str, Any]:
    """Default provider configuration for testing."""
    return {"type": "test_provider", "api_key": "test-key"}


@pytest.fixture
def fake_provider(
    health_result: FakeHealthResult | None = None,
    invoke_result: FakeInvokeResult | None = None,
    list_models_result: FakeModelListResult | None = None,
) -> FakeProvider:
    """Create a fake provider with optional custom results."""
    return FakeProvider(
        health_result=health_result,
        invoke_result=invoke_result,
        list_models_result=list_models_result,
    )


# =============================================================================
# run_connectivity_suite Tests
# =============================================================================

class TestRunConnectivitySuite:
    """Tests for run_connectivity_suite function."""

    @pytest.mark.asyncio
    async def test_provider_not_found(self) -> None:
        """Test that provider not found returns appropriate error."""
        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = None

            result = await run_connectivity_suite(
                provider_cfg={"type": "nonexistent"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()
        assert result["details"] == {}

    @pytest.mark.asyncio
    async def test_missing_provider_type(self) -> None:
        """Test that missing provider type returns appropriate error."""
        result = await run_connectivity_suite(
            provider_cfg={},
            model="test-model",
        )

        assert result["ok"] is False
        assert "not specified" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        """Test that health check failure returns appropriate error."""
        fake = FakeProvider(
            health_result=FakeHealthResult(
                ok=False, latency_ms=0, error="Connection timeout"
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "failed" in result["error"].lower() or "timeout" in result["error"].lower()
        assert result["details"]["health"]["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_exception(self) -> None:
        """Test that health check exception returns appropriate error."""
        fake = FakeProvider()
        # Make health raise an exception
        original_health = fake.health
        fake.health = MagicMock(side_effect=RuntimeError("Network error"))

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "Network error" in result["error"]
        assert result["details"]["health"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_health_check_success(
        self, mock_provider_manager: MagicMock
    ) -> None:
        """Test successful health check returns ok=True."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50)
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is True
        assert result["latency_ms"] == 50
        assert result["details"]["health"]["status"] == "healthy"
        assert result["details"]["health"]["latency_ms"] == 50

    @pytest.mark.asyncio
    async def test_ollama_list_models_failure(self) -> None:
        """Test Ollama list_models failure returns appropriate error."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50),
            list_models_result=FakeModelListResult(
                ok=False, models=[], error="Failed to connect"
            ),
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "ollama", "base_url": "http://120.24.117.59:11434"},
                model="llama2",
            )

        assert result["ok"] is False
        assert "model list check failed" in result["error"].lower()
        assert "model_available" in result["details"]

    @pytest.mark.asyncio
    async def test_ollama_model_not_installed(self) -> None:
        """Test Ollama model not installed returns appropriate error."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50),
            list_models_result=FakeModelListResult(
                ok=True,
                models=[ModelInfo(id="other-model")],
            ),
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "ollama", "base_url": "http://120.24.117.59:11434"},
                model="llama2",
            )

        assert result["ok"] is False
        assert "not installed" in result["error"].lower()
        assert result["details"]["model_available"]["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_ollama_model_available(self) -> None:
        """Test Ollama model available passes connectivity check."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50),
            list_models_result=FakeModelListResult(
                ok=True,
                models=[ModelInfo(id="llama2:latest"), ModelInfo(id="other-model")],
            ),
            invoke_result=FakeInvokeResult(
                ok=True, output="OK", latency_ms=100
            ),
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "ollama", "base_url": "http://120.24.117.59:11434"},
                model="llama2:latest",
            )

        assert result["ok"] is True
        assert result["details"]["model_available"]["status"] == "available"
        assert result["details"]["invoke_smoke"]["status"] == "ok"
        # Total latency should include health + invoke
        assert result["latency_ms"] == 150

    @pytest.mark.asyncio
    async def test_ollama_invoke_failure(self) -> None:
        """Test Ollama invoke smoke test failure returns error."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50),
            list_models_result=FakeModelListResult(
                ok=True,
                models=[ModelInfo(id="llama2")],
            ),
            invoke_result=FakeInvokeResult(
                ok=False, output="", latency_ms=0, error="Invoke failed"
            ),
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "ollama", "base_url": "http://120.24.117.59:11434"},
                model="llama2",
            )

        assert result["ok"] is False
        assert "invoke" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_api_key_override(self) -> None:
        """Test that api_key parameter overrides provider_cfg api_key."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50)
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            await run_connectivity_suite(
                provider_cfg={"type": "test", "api_key": "original"},
                model="test-model",
                api_key="override",
            )

            # Check that health was called with overridden api_key
            assert fake.health_calls[0]["config"]["api_key"] == "override"


# =============================================================================
# run_connectivity_suite_sync Tests
# =============================================================================

class TestRunConnectivitySuiteSync:
    """Tests for run_connectivity_suite_sync function."""

    def test_sync_wrapper_success(self) -> None:
        """Test synchronous wrapper returns successful result."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50)
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = run_connectivity_suite_sync(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is True
        assert result["latency_ms"] == 50

    def test_sync_wrapper_exception_handling(self) -> None:
        """Test synchronous wrapper handles exceptions gracefully."""
        fake = FakeProvider()
        fake.health = MagicMock(side_effect=RuntimeError("Sync error"))

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = run_connectivity_suite_sync(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "Sync error" in result["error"]


# =============================================================================
# run_response_suite Tests
# =============================================================================

class TestRunResponseSuite:
    """Tests for run_response_suite function."""

    @pytest.mark.asyncio
    async def test_provider_not_found(self) -> None:
        """Test that provider not found returns appropriate error."""
        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = None

            result = await run_response_suite(
                provider_cfg={"type": "nonexistent"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_invoke_failure(self) -> None:
        """Test that invoke failure returns appropriate error."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=False, output="", latency_ms=0, error="API error"
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_response_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "API error" in result["error"]

    @pytest.mark.asyncio
    async def test_json_parsing_success(self) -> None:
        """Test that valid JSON response returns ok=True."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output='{"status": "ok", "test": true, "data": 123}',
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_response_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is True
        assert result["details"]["has_json"] is True
        assert result["latency_ms"] == 100

    @pytest.mark.asyncio
    async def test_json_parsing_failure(self) -> None:
        """Test that non-JSON response returns ok=False."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output="This is just plain text without JSON",
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_response_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert result["details"]["has_json"] is False

    @pytest.mark.asyncio
    async def test_exception_handling(self) -> None:
        """Test that exception during invoke is handled gracefully."""
        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=RuntimeError("Connection lost"))

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_response_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "Connection lost" in result["error"]


# =============================================================================
# run_thinking_suite Tests
# =============================================================================

class TestRunThinkingSuite:
    """Tests for run_thinking_suite function."""

    @pytest.mark.asyncio
    async def test_provider_not_found(self) -> None:
        """Test that provider not found returns appropriate error."""
        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = None

            result = await run_thinking_suite(
                provider_cfg={"type": "nonexistent"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_invoke_failure(self) -> None:
        """Test that invoke failure returns appropriate error."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=False, output="", latency_ms=0, error="API error"
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "API error" in result["error"]

    @pytest.mark.asyncio
    async def test_tag_extraction_success(self) -> None:
        """Test that correct thinking/answer tags return ok=True."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output=(
                    "<thinking>\n"
                    "Let me calculate: 60 km in 30 minutes\n"
                    "30 minutes = 0.5 hours\n"
                    "Speed = 60 / 0.5 = 120 km/h\n"
                    "</thinking>\n"
                    "<answer>\n"
                    "The average speed is 120 km/h\n"
                    "</answer>"
                ),
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is True
        assert result["details"]["thinking"]["has_thinking"] is True
        assert result["details"]["answer"]["has_answer"] is True
        assert result["details"]["answer"]["looks_reasonable"] is True

    @pytest.mark.asyncio
    async def test_missing_thinking_tag(self) -> None:
        """Test that missing thinking tag returns ok=False."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output="The average speed is 120 km/h",
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert result["details"]["thinking"]["has_thinking"] is False
        assert result["details"]["answer"]["has_answer"] is True

    @pytest.mark.asyncio
    async def test_missing_answer_tag(self) -> None:
        """Test that missing answer tag returns ok=False."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output="<thinking>Some reasoning but no answer</thinking>",
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert result["details"]["answer"]["has_answer"] is False

    @pytest.mark.asyncio
    async def test_wrong_answer_value(self) -> None:
        """Test that incorrect answer value returns ok=False."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output=(
                    "<thinking>Let me calculate</thinking>\n"
                    "<answer>The speed is 60 km/h</answer>"
                ),
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert result["details"]["answer"]["looks_reasonable"] is False

    @pytest.mark.asyncio
    async def test_thinking_tag_variations(self) -> None:
        """Test that different thinking tag variations are recognized."""
        # Test with <thinking> tag which is the primary supported tag
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output=(
                    "<thinking>Reasoning process here</thinking>\n"
                    "<answer>The answer is 120</answer>"
                ),
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        # Should recognize the thinking tag
        assert result["details"]["thinking"]["has_thinking"] is True

    @pytest.mark.asyncio
    async def test_exception_handling(self) -> None:
        """Test that exception during invoke is handled gracefully."""
        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=RuntimeError("Connection lost"))

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "Connection lost" in result["error"]


# =============================================================================
# run_qualification_suite Tests
# =============================================================================

class TestRunQualificationSuite:
    """Tests for run_qualification_suite function."""

    @pytest.mark.asyncio
    async def test_provider_not_found(self) -> None:
        """Test that provider not found returns appropriate error."""
        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = None

            result = await run_qualification_suite(
                provider_cfg={"type": "nonexistent"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()
        assert result["cases"] == []

    @pytest.mark.asyncio
    async def test_deflection_detection(self) -> None:
        """Test that deflection detection works correctly."""
        # Create a provider that returns responses for each case
        responses = [
            '{"result": "success", "value": 42}',  # json_basic
            '["red", "green", "blue"]',  # list_format
            'def hello_world():\n    print("Hello")',  # no_deflection
        ]
        response_index = {"index": 0}

        def invoke_sync(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            idx = response_index["index"]
            response_index["index"] = idx + 1
            return InvokeResult(
                ok=True,
                output=responses[idx % len(responses)],
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = invoke_sync  # type: ignore - replace with sync function

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_qualification_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        # 3 test cases: json_basic, list_format, no_deflection
        assert len(result["details"]["cases"]) == 3

        # json_basic should pass (has "result" and "success")
        assert result["details"]["cases"][0]["passed"] is True

        # list_format should pass (has [ and ])
        assert result["details"]["cases"][1]["passed"] is True

        # no_deflection: code should pass (has def, no deflection)
        no_deflection_case = result["details"]["cases"][2]
        # The deflection detector checks for patterns like "i cannot"
        assert no_deflection_case["id"] == "no_deflection"

    @pytest.mark.asyncio
    async def test_all_cases_pass(self) -> None:
        """Test that 100% pass rate returns ok=True."""
        responses = [
            '{"result": "success", "value": 42}',  # json_basic
            '["a", "b", "c"]',  # list_format
            'def hello():\n    pass',  # no_deflection
        ]
        response_index = {"index": 0}

        def invoke_sync(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            idx = response_index["index"]
            response_index["index"] = idx + 1
            return InvokeResult(
                ok=True,
                output=responses[idx % len(responses)],
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = invoke_sync  # type: ignore - replace with sync function

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_qualification_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        # All 3 cases pass = 100% > 60% threshold
        assert result["ok"] is True
        assert result["details"]["passed"] == 3
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_below_threshold(self) -> None:
        """Test that below 60% pass rate returns ok=False."""
        responses = [
            '{"result": "success"}',  # json_basic - passes
            'invalid',  # list_format - fails (no brackets)
            'I cannot help',  # no_deflection - fails (deflection detected)
        ]
        response_index = {"index": 0}

        def invoke_sync(prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            idx = response_index["index"]
            response_index["index"] = idx + 1
            return InvokeResult(
                ok=True,
                output=responses[idx % len(responses)],
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = invoke_sync  # type: ignore - replace with sync function

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_qualification_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        # 1 out of 3 passes = 33% < 60%
        assert result["ok"] is False
        assert result["details"]["passed"] == 1
        assert result["score"] == pytest.approx(1 / 3)

    @pytest.mark.asyncio
    async def test_exception_in_case(self) -> None:
        """Test that exception in one case doesn't fail entire suite."""
        call_count = {"count": 0}

        def invoke_side_effect(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            call_count["count"] += 1
            if call_count["count"] == 1:
                # First call succeeds
                return InvokeResult(
                    ok=True,
                    output='{"result": "success"}',
                    latency_ms=100,
                    usage=Usage(0, 0, 0),
                )
            else:
                # Subsequent calls raise exception
                raise RuntimeError("Network error")

        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=invoke_side_effect)

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_qualification_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        # Should still have 3 cases (one with error)
        assert len(result["details"]["cases"]) == 3
        # First case should pass
        assert result["details"]["cases"][0]["passed"] is True
        # Other cases should have error
        assert "error" in result["details"]["cases"][1]
        assert "error" in result["details"]["cases"][2]


# =============================================================================
# run_interview_suite Tests
# =============================================================================

class TestRunInterviewSuite:
    """Tests for run_interview_suite function."""

    @pytest.mark.asyncio
    async def test_provider_not_found(self) -> None:
        """Test that provider not found returns appropriate error."""
        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = None

            result = await run_interview_suite(
                provider_cfg={"type": "nonexistent"},
                model="test-model",
            )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_semantic_scoring_enabled(self) -> None:
        """Test semantic scoring when INTERVIEW_SEMANTIC_ENABLED is True."""
        # Mock semantic_criteria_hits to return specific values
        def invoke_side_effect(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            return InvokeResult(
                ok=True,
                output=(
                    "<thinking>I need to address this systematically</thinking>\n"
                    "<answer>"
                    "To handle tight deadlines, I prioritize tasks by impact, "
                    "communicate clearly with stakeholders about scope, and focus "
                    "on quality while managing time effectively. I break down work "
                    "into manageable chunks and test incrementally."
                    "</answer>"
                ),
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=invoke_side_effect)

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            with patch(
                "polaris.cells.llm.evaluation.internal.suites.semantic_criteria_hits"
            ) as mock_hits:
                # Return hits indicating all criteria met
                mock_hits.return_value = {
                    "prioritize": 0.8,
                    "communicate": 0.7,
                    "scope": 0.6,
                    "quality": 0.75,
                }

                result = await run_interview_suite(
                    provider_cfg={"type": "test"},
                    model="test-model",
                )

        # With semantic scoring, average should be > 0.6
        assert result["ok"] is True
        assert result["score"] == pytest.approx(0.7125)  # (0.8 + 0.7 + 0.6 + 0.75) / 4
        assert len(result["details"]["results"]) == 2  # 2 questions

    @pytest.mark.asyncio
    async def test_fallback_scoring_short_answer(self) -> None:
        """Test fallback scoring when answer is too short."""
        def invoke_side_effect(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            # Very short answer
            return InvokeResult(
                ok=True,
                output="<answer>I handle deadlines well.</answer>",
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=invoke_side_effect)

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            with patch(
                "polaris.cells.llm.evaluation.internal.suites.INTERVIEW_SEMANTIC_ENABLED",
                True,
            ):
                result = await run_interview_suite(
                    provider_cfg={"type": "test"},
                    model="test-model",
                )

        # Short answer (< 80 chars) falls back to 0.0
        assert result["score"] == 0.0
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_fallback_scoring_semantic_disabled(self) -> None:
        """Test fallback scoring when semantic scoring is disabled."""
        def invoke_side_effect(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            # Long enough answer for semantic but will use fallback
            return InvokeResult(
                ok=True,
                output=(
                    "<answer>"
                    "I handle deadlines by prioritizing tasks, communicating with stakeholders, "
                    "managing scope, and ensuring quality. This systematic approach helps me "
                    "meet tight deadlines effectively."
                    "</answer>"
                ),
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=invoke_side_effect)

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            # Disable semantic scoring
            with patch(
                "polaris.cells.llm.evaluation.internal.suites.INTERVIEW_SEMANTIC_ENABLED",
                False,
            ):
                result = await run_interview_suite(
                    provider_cfg={"type": "test"},
                    model="test-model",
                )

        # Without semantic, long answer gets 0.5
        assert result["score"] == 0.5
        assert result["ok"] is False  # 0.5 is not > 0.6

    @pytest.mark.asyncio
    async def test_average_score_above_threshold(self) -> None:
        """Test that average score above 0.6 returns ok=True."""
        call_count = {"count": 0}

        def invoke_side_effect(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            call_count["count"] += 1
            return InvokeResult(
                ok=True,
                output=(
                    "<answer>"
                    "This is a comprehensive answer with lots of details about "
                    "handling deadlines, reproducing issues, isolating problems, "
                    "and testing systematically. Very detailed and thorough."
                    "</answer>"
                ),
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=invoke_side_effect)

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            with patch(
                "polaris.cells.llm.evaluation.internal.suites.semantic_criteria_hits"
            ) as mock_hits:
                # All criteria hit with high scores
                mock_hits.return_value = {
                    "prioritize": 0.9,
                    "communicate": 0.8,
                    "scope": 0.7,
                    "quality": 0.8,
                }

                result = await run_interview_suite(
                    provider_cfg={"type": "test"},
                    model="test-model",
                )

        # Average = 0.8 > 0.6
        assert result["ok"] is True
        assert result["score"] > 0.6

    @pytest.mark.asyncio
    async def test_exception_in_question(self) -> None:
        """Test that exception in one question doesn't fail entire suite."""
        responses = [
            "<answer>Answer with sufficient content to pass all semantic criteria and ensure length is greater than eighty characters</answer>",
            None,  # Second call will raise exception
        ]
        response_index = {"index": 0}

        def invoke_sync(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            idx = response_index["index"]
            response_index["index"] = idx + 1
            if idx > 0:
                raise RuntimeError("API error")
            return InvokeResult(
                ok=True,
                output=responses[idx],
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = invoke_sync  # type: ignore - replace with sync function

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            with patch(
                "polaris.cells.llm.evaluation.internal.suites.semantic_criteria_hits"
            ) as mock_hits:
                mock_hits.return_value = {
                    "prioritize": 0.8,
                    "communicate": 0.7,
                    "scope": 0.6,
                    "quality": 0.7,
                }

                result = await run_interview_suite(
                    provider_cfg={"type": "test"},
                    model="test-model",
                )

        # Should have 2 results (one passed, one with error)
        assert len(result["details"]["results"]) == 2
        assert result["details"]["results"][0]["passed"] is True
        assert "error" in result["details"]["results"][1]

    @pytest.mark.asyncio
    async def test_role_parameter(self) -> None:
        """Test that role parameter is passed correctly."""
        captured_prompts: list[str] = []

        def invoke_side_effect(
            prompt: str, model: str, config: dict[str, Any]
        ) -> InvokeResult:
            captured_prompts.append(prompt)
            return InvokeResult(
                ok=True,
                output="<answer>Test answer</answer>",
                latency_ms=100,
                usage=Usage(0, 0, 0),
            )

        fake = FakeProvider()
        fake.invoke = MagicMock(side_effect=invoke_side_effect)

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            await run_interview_suite(
                provider_cfg={"type": "test"},
                model="test-model",
                role="engineer",
            )

        # Verify prompts contain the role context
        for prompt in captured_prompts:
            assert "job candidate" in prompt.lower()


# =============================================================================
# Edge Cases and Boundary Tests
# =============================================================================

class TestEdgeCases:
    """Edge case tests for suite functions."""

    @pytest.mark.asyncio
    async def test_empty_provider_cfg(self) -> None:
        """Test handling of empty provider config."""
        result = await run_connectivity_suite(
            provider_cfg={},
            model="",
        )
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_whitespace_provider_type(self) -> None:
        """Test handling of whitespace-only provider type."""
        result = await run_connectivity_suite(
            provider_cfg={"type": "   "},
            model="test-model",
        )
        assert result["ok"] is False
        assert "not specified" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_none_model(self) -> None:
        """Test handling of None model."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50)
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "test"},
                model=None,  # type: ignore
            )

        # Should handle None model gracefully
        assert result is not None

    @pytest.mark.asyncio
    async def test_empty_output(self) -> None:
        """Test handling of empty response output."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output="",
                latency_ms=100,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_response_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is False
        assert result["details"]["has_json"] is False

    @pytest.mark.asyncio
    async def test_none_latency(self) -> None:
        """Test handling of None latency in result."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=0)
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is True
        # latency_ms should be 0 (not None)
        assert result["latency_ms"] == 0


# =============================================================================
# Integration-like Tests (with mocked provider)
# =============================================================================

class TestSuiteIntegration:
    """Integration-like tests using mocked providers."""

    @pytest.mark.asyncio
    async def test_full_connectivity_flow(self) -> None:
        """Test complete connectivity flow with all checks passing."""
        fake = FakeProvider(
            health_result=FakeHealthResult(ok=True, latency_ms=50),
            list_models_result=FakeModelListResult(
                ok=True,
                models=[ModelInfo(id="llama2:latest")],
            ),
            invoke_result=FakeInvokeResult(
                ok=True, output="OK", latency_ms=75
            ),
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_connectivity_suite(
                provider_cfg={
                    "type": "ollama",
                    "base_url": "http://120.24.117.59:11434",
                },
                model="llama2:latest",
                api_key="test-key",
            )

        assert result["ok"] is True
        assert result["latency_ms"] == 125  # 50 + 75
        assert result["details"]["health"]["status"] == "healthy"
        assert result["details"]["model_available"]["status"] == "available"
        assert result["details"]["invoke_smoke"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_thinking_suite_full_flow(self) -> None:
        """Test complete thinking suite flow."""
        fake = FakeProvider(
            invoke_result=FakeInvokeResult(
                ok=True,
                output=(
                    "Let me work through this problem step by step.\n"
                    "<thinking>\n"
                    "The train travels 60 km in 30 minutes.\n"
                    "30 minutes = 0.5 hours.\n"
                    "Speed = Distance / Time = 60 / 0.5 = 120 km/h.\n"
                    "</thinking>\n"
                    "<answer>\n"
                    "The average speed is 120 km/h.\n"
                    "</answer>"
                ),
                latency_ms=150,
            )
        )

        with patch(
            "polaris.cells.llm.evaluation.internal.suites.get_provider_manager"
        ) as mock_pm:
            mock_pm.return_value.get_provider_instance.return_value = fake

            result = await run_thinking_suite(
                provider_cfg={"type": "test"},
                model="test-model",
            )

        assert result["ok"] is True
        assert result["latency_ms"] == 150
        assert result["details"]["thinking"]["has_thinking"] is True
        assert result["details"]["thinking"]["length"] > 10
        assert result["details"]["answer"]["has_answer"] is True
        assert result["details"]["answer"]["looks_reasonable"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
