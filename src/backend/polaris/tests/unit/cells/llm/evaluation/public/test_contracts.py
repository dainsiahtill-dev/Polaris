"""Unit tests for polaris.cells.llm.evaluation.public.contracts."""

from __future__ import annotations

import pytest

from polaris.cells.llm.evaluation.public.contracts import (
    ILlmEvaluationService,
    LlmEvaluationCompletedEventV1,
    LlmEvaluationError,
    LlmEvaluationResultV1,
    QueryLlmEvaluationIndexV1,
    RunLlmEvaluationCommandV1,
)


class TestRunLlmEvaluationCommandV1:
    """Tests for RunLlmEvaluationCommandV1 dataclass."""

    def test_valid_command(self) -> None:
        cmd = RunLlmEvaluationCommandV1(
            workspace="/tmp/ws",
            provider_id="ollama",
            model="llama3",
        )
        assert cmd.workspace == "/tmp/ws"
        assert cmd.provider_id == "ollama"
        assert cmd.model == "llama3"
        assert cmd.role == "default"
        assert cmd.suites == ()
        assert cmd.options == {}

    def test_empty_workspace(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            RunLlmEvaluationCommandV1(workspace="", provider_id="p", model="m")

    def test_empty_provider_id(self) -> None:
        with pytest.raises(ValueError, match="provider_id must be a non-empty string"):
            RunLlmEvaluationCommandV1(workspace="/tmp", provider_id="", model="m")

    def test_empty_model(self) -> None:
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            RunLlmEvaluationCommandV1(workspace="/tmp", provider_id="p", model="")

    def test_suite_normalization(self) -> None:
        cmd = RunLlmEvaluationCommandV1(
            workspace="/tmp",
            provider_id="p",
            model="m",
            suites=["connectivity", "", "response"],
        )
        assert cmd.suites == ("connectivity", "response")

    def test_options_copy(self) -> None:
        options = {"timeout": 30}
        cmd = RunLlmEvaluationCommandV1(
            workspace="/tmp",
            provider_id="p",
            model="m",
            options=options,
        )
        assert cmd.options == {"timeout": 30}
        assert cmd.options is not options


class TestQueryLlmEvaluationIndexV1:
    """Tests for QueryLlmEvaluationIndexV1 dataclass."""

    def test_valid_query(self) -> None:
        query = QueryLlmEvaluationIndexV1(workspace="/tmp/ws")
        assert query.workspace == "/tmp/ws"
        assert query.provider_id is None
        assert query.model is None
        assert query.role is None

    def test_with_optional_fields(self) -> None:
        query = QueryLlmEvaluationIndexV1(
            workspace="/tmp/ws",
            provider_id="ollama",
            model="llama3",
            role="pm",
        )
        assert query.provider_id == "ollama"
        assert query.model == "llama3"
        assert query.role == "pm"

    def test_empty_optional_not_allowed(self) -> None:
        with pytest.raises(ValueError, match="provider_id must be a non-empty string"):
            QueryLlmEvaluationIndexV1(workspace="/tmp", provider_id="")


class TestLlmEvaluationCompletedEventV1:
    """Tests for LlmEvaluationCompletedEventV1 dataclass."""

    def test_valid_event(self) -> None:
        event = LlmEvaluationCompletedEventV1(
            event_id="evt-1",
            workspace="/tmp/ws",
            run_id="run-1",
            provider_id="ollama",
            model="llama3",
            role="pm",
            grade="PASS",
            completed_at="2024-01-01T00:00:00",
        )
        assert event.event_id == "evt-1"
        assert event.grade == "PASS"
        assert event.role == "pm"

    def test_empty_field(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            LlmEvaluationCompletedEventV1(
                event_id="",
                workspace="/tmp",
                run_id="r1",
                provider_id="p",
                model="m",
                role="pm",
                grade="PASS",
                completed_at="2024-01-01",
            )


class TestLlmEvaluationResultV1:
    """Tests for LlmEvaluationResultV1 dataclass."""

    def test_ok_result(self) -> None:
        result = LlmEvaluationResultV1(
            ok=True,
            status="completed",
            workspace="/tmp",
            run_id="r1",
        )
        assert result.ok is True
        assert result.status == "completed"
        assert result.error_code is None
        assert result.error_message is None

    def test_failed_result_without_error(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            LlmEvaluationResultV1(
                ok=False,
                status="failed",
                workspace="/tmp",
                run_id="r1",
            )

    def test_failed_result_with_error_code(self) -> None:
        result = LlmEvaluationResultV1(
            ok=False,
            status="failed",
            workspace="/tmp",
            run_id="r1",
            error_code="TIMEOUT",
        )
        assert result.error_code == "TIMEOUT"

    def test_suite_normalization(self) -> None:
        result = LlmEvaluationResultV1(
            ok=True,
            status="completed",
            workspace="/tmp",
            run_id="r1",
            suites=({"name": "connectivity"},),
        )
        assert len(result.suites) == 1


class TestLlmEvaluationError:
    """Tests for LlmEvaluationError exception."""

    def test_basic_error(self) -> None:
        exc = LlmEvaluationError("something failed")
        assert str(exc) == "something failed"
        assert exc.code == "llm_evaluation_error"
        assert exc.details == {}

    def test_error_with_code_and_details(self) -> None:
        exc = LlmEvaluationError(
            "evaluation failed",
            code="provider_unavailable",
            details={"provider": "ollama"},
        )
        assert exc.code == "provider_unavailable"
        assert exc.details == {"provider": "ollama"}

    def test_empty_message(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            LlmEvaluationError("")


class TestILlmEvaluationService:
    """Tests for ILlmEvaluationService protocol."""

    def test_is_protocol(self) -> None:
        assert hasattr(ILlmEvaluationService, "run_evaluation")
        assert hasattr(ILlmEvaluationService, "query_index")
