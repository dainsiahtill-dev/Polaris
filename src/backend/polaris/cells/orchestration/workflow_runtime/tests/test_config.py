"""Tests for polaris.cells.orchestration.workflow_runtime.internal.config module.

This module tests the configuration helpers for workflow orchestration.
"""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.config import (
    SUPPORTED_ORCHESTRATION_RUNTIMES,
    WorkflowConfig,
    _parse_bool,
    _parse_float,
    _parse_int,
    resolve_orchestration_runtime,
)


class TestParseHelpers:
    """Tests for _parse_* helper functions."""

    def test_parse_bool_with_bool(self) -> None:
        """_parse_bool returns True/False as-is."""
        assert _parse_bool(True) is True
        assert _parse_bool(False) is False

    def test_parse_bool_truthy_strings(self) -> None:
        """_parse_bool recognizes truthy strings."""
        assert _parse_bool("true") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True

    def test_parse_bool_falsey_strings(self) -> None:
        """_parse_bool recognizes falsey strings."""
        assert _parse_bool("false") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False

    def test_parse_bool_whitespace(self) -> None:
        """_parse_bool strips whitespace."""
        assert _parse_bool("  true  ") is True
        assert _parse_bool("  false  ") is False

    def test_parse_bool_default_on_empty(self) -> None:
        """_parse_bool returns default on empty string."""
        assert _parse_bool("") is False
        assert _parse_bool("  ") is False
        assert _parse_bool(None) is False

    def test_parse_bool_default_on_unknown(self) -> None:
        """_parse_bool returns default for unknown values."""
        assert _parse_bool("unknown") is False
        assert _parse_bool("maybe", default=True) is True

    def test_parse_int_valid(self) -> None:
        """_parse_int parses valid integers."""
        assert _parse_int("42", 0) == 42
        assert _parse_int("  123  ", 0) == 123
        assert _parse_int(100, 0) == 100

    def test_parse_int_invalid(self) -> None:
        """_parse_int returns default for invalid values."""
        assert _parse_int("not a number", 0) == 0
        assert _parse_int("abc", 99) == 99
        assert _parse_int(None, 5) == 5

    def test_parse_float_valid(self) -> None:
        """_parse_float parses valid floats."""
        assert _parse_float("3.14", 0.0) == 3.14
        assert _parse_float("  2.5  ", 0.0) == 2.5
        assert _parse_float(1.5, 0.0) == 1.5

    def test_parse_float_invalid(self) -> None:
        """_parse_float returns default for invalid values."""
        assert _parse_float("not a float", 0.0) == 0.0
        assert _parse_float("abc", 9.9) == 9.9
        assert _parse_float(None, 1.1) == 1.1


class TestResolveOrchestrationRuntime:
    """Tests for resolve_orchestration_runtime function."""

    def test_returns_workflow_default(self) -> None:
        """resolve_orchestration_runtime returns 'workflow' by default."""
        result = resolve_orchestration_runtime()
        assert result == "workflow"

    def test_returns_workflow_for_explicit_workflow(self) -> None:
        """resolve_orchestration_runtime accepts 'workflow'."""
        result = resolve_orchestration_runtime("workflow")
        assert result == "workflow"

    def test_normalizes_case(self) -> None:
        """resolve_orchestration_runtime normalizes case."""
        result = resolve_orchestration_runtime("WORKFLOW")
        assert result == "workflow"

    def test_returns_workflow_for_unknown(self) -> None:
        """resolve_orchestration_runtime returns 'workflow' for unknown runtime."""
        result = resolve_orchestration_runtime("unknown_runtime")
        assert result == "workflow"

    def test_uses_environ_if_no_raw(self) -> None:
        """resolve_orchestration_runtime uses KERNELONE_ORCHESTRATION_RUNTIME env var."""
        environ = {"KERNELONE_ORCHESTRATION_RUNTIME": "workflow"}
        result = resolve_orchestration_runtime(environ=environ)
        assert result == "workflow"

    def test_raw_overrides_environ(self) -> None:
        """resolve_orchestration_runtime prefers raw over environ."""
        environ = {"KERNELONE_ORCHESTRATION_RUNTIME": "other"}
        result = resolve_orchestration_runtime("workflow", environ=environ)
        assert result == "workflow"


class TestWorkflowConfig:
    """Tests for WorkflowConfig dataclass."""

    def test_default_values(self) -> None:
        """WorkflowConfig has correct default values."""
        config = WorkflowConfig()
        assert config.enabled is True
        assert config.namespace == "polaris"
        assert config.task_queue == "polaris-queue"
        assert config.retry_max_attempts == 3
        assert config.retry_initial_interval_seconds == 1.0
        assert config.retry_backoff_coefficient == 2.0
        assert config.workflow_execution_timeout_seconds > 0
        assert config.rpc_timeout_seconds == 1.0

    def test_custom_values(self) -> None:
        """WorkflowConfig accepts custom values."""
        config = WorkflowConfig(
            enabled=True,
            namespace="custom",
            task_queue="custom-queue",
            retry_max_attempts=5,
        )
        assert config.namespace == "custom"
        assert config.task_queue == "custom-queue"
        assert config.retry_max_attempts == 5

    def test_from_env_default(self) -> None:
        """WorkflowConfig.from_env uses defaults when no env vars."""
        config = WorkflowConfig.from_env(environ={})
        assert config.namespace == "polaris"
        assert config.task_queue == "polaris-queue"

    def test_from_env_with_values(self) -> None:
        """WorkflowConfig.from_env reads from environ."""
        environ = {
            "KERNELONE_WORKFLOW_NAMESPACE": "my-ns",
            "KERNELONE_WORKFLOW_TASK_QUEUE": "my-queue",
            "KERNELONE_WORKFLOW_RETRY_MAX_ATTEMPTS": "5",
            "KERNELONE_WORKFLOW_RETRY_INITIAL_INTERVAL_SECONDS": "2.0",
            "KERNELONE_WORKFLOW_RETRY_BACKOFF_COEFFICIENT": "1.5",
            "KERNELONE_WORKFLOW_RPC_TIMEOUT_SECONDS": "5.0",
        }
        config = WorkflowConfig.from_env(environ=environ)
        assert config.namespace == "my-ns"
        assert config.task_queue == "my-queue"
        assert config.retry_max_attempts == 5
        assert config.retry_initial_interval_seconds == 2.0
        assert config.retry_backoff_coefficient == 1.5
        assert config.rpc_timeout_seconds == 5.0

    def test_from_env_minimum_values(self) -> None:
        """WorkflowConfig.from_env enforces minimum values."""
        environ = {
            "KERNELONE_WORKFLOW_RETRY_MAX_ATTEMPTS": "0",  # Below minimum of 1
            "KERNELONE_WORKFLOW_RETRY_INITIAL_INTERVAL_SECONDS": "-1.0",  # Below minimum of 0.1
            "KERNELONE_WORKFLOW_RETRY_BACKOFF_COEFFICIENT": "0.5",  # Below minimum of 1.0
            "KERNELONE_WORKFLOW_RPC_TIMEOUT_SECONDS": "0.01",  # Below minimum of 0.1
            "KERNELONE_WORKFLOW_WORKFLOW_TIMEOUT_SECONDS": "10",  # Below minimum of 60
        }
        config = WorkflowConfig.from_env(environ=environ)
        assert config.retry_max_attempts == 1
        assert config.retry_initial_interval_seconds == 0.1
        assert config.retry_backoff_coefficient == 1.0
        assert config.rpc_timeout_seconds == 0.1
        assert config.workflow_execution_timeout_seconds == 60

    def test_from_env_empty_environ(self) -> None:
        """WorkflowConfig.from_env handles empty environ."""
        config = WorkflowConfig.from_env(environ={})
        # Should use defaults
        assert config.namespace == "polaris"

    def test_is_frozen(self) -> None:
        """WorkflowConfig is a frozen dataclass."""
        from dataclasses import FrozenInstanceError

        config = WorkflowConfig()
        with pytest.raises(FrozenInstanceError):
            config.namespace = "changed"  # type: ignore[misc,attr-defined]

    def test_supported_runtimes(self) -> None:
        """SUPPORTED_ORCHESTRATION_RUNTIMES contains expected values."""
        assert "workflow" in SUPPORTED_ORCHESTRATION_RUNTIMES
