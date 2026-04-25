"""Tests for workflow_runtime internal config module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.config import (
    InternalWorkflowConfig,
    WorkflowConfig,
    _parse_bool,
    _parse_float,
    _parse_int,
    resolve_orchestration_runtime,
)


class TestParseBool:
    def test_true_values(self) -> None:
        assert _parse_bool(True) is True
        assert _parse_bool("1") is True
        assert _parse_bool("true") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True
        assert _parse_bool("  TRUE  ") is True

    def test_false_values(self) -> None:
        assert _parse_bool(False) is False
        assert _parse_bool("0") is False
        assert _parse_bool("false") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False

    def test_default_for_empty_or_unknown(self) -> None:
        assert _parse_bool(None) is False
        assert _parse_bool("") is False
        assert _parse_bool("maybe") is False
        assert _parse_bool("maybe", default=True) is True


class TestParseInt:
    def test_valid(self) -> None:
        assert _parse_int("42", 0) == 42
        assert _parse_int("  7  ", 0) == 7

    def test_invalid_returns_default(self) -> None:
        assert _parse_int("abc", 99) == 99
        assert _parse_int(None, 5) == 5


class TestParseFloat:
    def test_valid(self) -> None:
        assert _parse_float("3.14", 0.0) == 3.14
        assert _parse_float("  2.5  ", 0.0) == 2.5

    def test_invalid_returns_default(self) -> None:
        assert _parse_float("abc", 1.5) == 1.5
        assert _parse_float(None, 2.0) == 2.0


class TestResolveOrchestrationRuntime:
    def test_defaults_to_workflow(self) -> None:
        assert resolve_orchestration_runtime() == "workflow"
        assert resolve_orchestration_runtime("") == "workflow"

    def test_supported_runtime(self) -> None:
        assert resolve_orchestration_runtime("workflow") == "workflow"

    def test_unsupported_fallback(self) -> None:
        assert resolve_orchestration_runtime("kubernetes") == "workflow"

    def test_env_override(self) -> None:
        assert resolve_orchestration_runtime("", environ={"KERNELONE_ORCHESTRATION_RUNTIME": "workflow"}) == "workflow"
        assert resolve_orchestration_runtime("", environ={"KERNELONE_ORCHESTRATION_RUNTIME": "unknown"}) == "workflow"


class TestInternalWorkflowConfig:
    def test_defaults(self) -> None:
        cfg = InternalWorkflowConfig()
        assert cfg.enabled is True
        assert cfg.namespace == "polaris"
        assert cfg.task_queue == "polaris-queue"
        assert cfg.retry_max_attempts == 3
        assert cfg.retry_initial_interval_seconds == 1.0
        assert cfg.retry_backoff_coefficient == 2.0
        assert cfg.rpc_timeout_seconds == 1.0

    def test_from_env_defaults(self) -> None:
        cfg = InternalWorkflowConfig.from_env(environ={})
        assert cfg.namespace == "polaris"
        assert cfg.retry_max_attempts == 3

    def test_from_env_override(self) -> None:
        env = {
            "KERNELONE_WORKFLOW_NAMESPACE": "test-ns",
            "KERNELONE_WORKFLOW_RETRY_MAX_ATTEMPTS": "5",
            "KERNELONE_WORKFLOW_RETRY_INITIAL_INTERVAL_SECONDS": "2.5",
            "KERNELONE_WORKFLOW_RPC_TIMEOUT_SECONDS": "0.5",
        }
        cfg = InternalWorkflowConfig.from_env(environ=env)
        assert cfg.namespace == "test-ns"
        assert cfg.retry_max_attempts == 5
        assert cfg.retry_initial_interval_seconds == 2.5
        assert cfg.rpc_timeout_seconds == 0.5

    def test_from_env_clamps_negative_values(self) -> None:
        env = {
            "KERNELONE_WORKFLOW_RETRY_MAX_ATTEMPTS": "-1",
            "KERNELONE_WORKFLOW_RETRY_INITIAL_INTERVAL_SECONDS": "-5",
            "KERNELONE_WORKFLOW_RETRY_BACKOFF_COEFFICIENT": "0.5",
            "KERNELONE_WORKFLOW_WORKFLOW_TIMEOUT_SECONDS": "30",
            "KERNELONE_WORKFLOW_RPC_TIMEOUT_SECONDS": "-1",
        }
        cfg = InternalWorkflowConfig.from_env(environ=env)
        assert cfg.retry_max_attempts == 1
        assert cfg.retry_initial_interval_seconds == 0.1
        assert cfg.retry_backoff_coefficient == 1.0
        assert cfg.workflow_execution_timeout_seconds == 60
        assert cfg.rpc_timeout_seconds == 0.1

    def test_backward_compatible_alias(self) -> None:
        assert WorkflowConfig is InternalWorkflowConfig
