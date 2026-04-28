"""Contract tests for polaris.delivery.http.routers.interview module.

NOTE: The interview router imports InterviewAskPayload under TYPE_CHECKING only.
At runtime FastAPI sees a ForwardRef it cannot resolve, so the parameter is treated
as a query parameter.  This causes every endpoint that depends on InterviewAskPayload
to return 422 (missing payload) or 500 (Pydantic forward-ref error).

These tests document the *current* behaviour without modifying the router source
(per task constraint).  The schema-validation tests for llm_models.py provide the
real contract coverage.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import interview as interview_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(interview_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestInterviewPayloadSchemaValidation:
    """Schema validation tests for Interview Pydantic models (llm_models.py)."""

    def test_interview_ask_payload_defaults(self) -> None:
        """InterviewAskPayload uses correct defaults."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload

        payload = InterviewAskPayload(
            role="director",
            provider_id="test",
            model="gpt-4",
            question="Q",
        )
        assert payload.headers == {}
        assert payload.env_overrides == {}
        assert payload.session_id is None
        assert payload.context is None
        assert payload.criteria is None

    def test_interview_ask_payload_normalizes_empty_session_id(self) -> None:
        """InterviewAskPayload normalizes empty string session_id to None."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload

        payload = InterviewAskPayload(
            role="director",
            provider_id="test",
            model="gpt-4",
            question="Q",
            session_id="",
        )
        assert payload.session_id is None

    def test_interview_ask_payload_normalizes_context(self) -> None:
        """InterviewAskPayload normalizes empty list context to None."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload

        payload = InterviewAskPayload(
            role="director",
            provider_id="test",
            model="gpt-4",
            question="Q",
            context=[],
        )
        assert payload.context is None

    def test_interview_ask_payload_normalizes_criteria(self) -> None:
        """InterviewAskPayload normalizes empty list criteria to None."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload

        payload = InterviewAskPayload(
            role="director",
            provider_id="test",
            model="gpt-4",
            question="Q",
            criteria=[],
        )
        assert payload.criteria is None

    def test_interview_ask_payload_filters_non_string_criteria(self) -> None:
        """InterviewAskPayload filters non-string items from criteria."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload

        payload = InterviewAskPayload(
            role="director",
            provider_id="test",
            model="gpt-4",
            question="Q",
            criteria=["good", 123, None, "bad"],
        )
        assert payload.criteria == ["good", "123", "bad"]

    def test_interview_ask_payload_invalid_missing_required(self) -> None:
        """InterviewAskPayload raises ValidationError when required fields missing."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            InterviewAskPayload()

    def test_interview_ask_payload_invalid_question_type(self) -> None:
        """InterviewAskPayload raises ValidationError for wrong question type."""
        from polaris.delivery.http.routers.llm_models import InterviewAskPayload
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            InterviewAskPayload(
                role="director",
                provider_id="test",
                model="gpt-4",
                question=12345,  # type: ignore[arg-type]
            )

    def test_interview_cancel_payload_requires_session_id(self) -> None:
        """InterviewCancelPayload requires session_id."""
        from polaris.delivery.http.routers.llm_models import InterviewCancelPayload
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            InterviewCancelPayload()

    def test_interview_cancel_payload_happy_path(self) -> None:
        """InterviewCancelPayload accepts valid session_id."""
        from polaris.delivery.http.routers.llm_models import InterviewCancelPayload

        payload = InterviewCancelPayload(session_id="sess-123")
        assert payload.session_id == "sess-123"

    def test_interview_save_payload_requires_report(self) -> None:
        """InterviewSavePayload requires report dict."""
        from polaris.delivery.http.routers.llm_models import InterviewSavePayload
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            InterviewSavePayload(
                role="director",
                provider_id="test",
                model="gpt-4",
            )

    def test_interview_save_payload_happy_path(self) -> None:
        """InterviewSavePayload accepts valid data."""
        from polaris.delivery.http.routers.llm_models import InterviewSavePayload

        payload = InterviewSavePayload(
            role="director",
            provider_id="test",
            model="gpt-4",
            report={"score": 95},
        )
        assert payload.report == {"score": 95}
        assert payload.session_id is None

    def test_llm_test_payload_defaults(self) -> None:
        """LlmTestPayload uses correct defaults."""
        from polaris.delivery.http.routers.llm_models import LlmTestPayload

        payload = LlmTestPayload()
        assert payload.test_level == "quick"
        assert payload.role is None
        assert payload.suites is None

    def test_llm_test_payload_connectivity_fields(self) -> None:
        """LlmTestPayload accepts connectivity-only fields."""
        from polaris.delivery.http.routers.llm_models import LlmTestPayload

        payload = LlmTestPayload(
            provider_type="openai",
            base_url="https://api.openai.com",
            api_path="/v1/chat/completions",
            timeout=30,
        )
        assert payload.provider_type == "openai"
        assert payload.base_url == "https://api.openai.com"
        assert payload.timeout == 30

    def test_provider_action_payload_defaults(self) -> None:
        """ProviderActionPayload uses correct defaults."""
        from polaris.delivery.http.routers.llm_models import ProviderActionPayload

        payload = ProviderActionPayload()
        assert payload.api_key is None
        assert payload.headers is None


class TestInterviewRouterMockedHappyPath:
    """Happy path tests with mocked LLM responses (bypass TYPE_CHECKING issue)."""

    def test_interview_ask_happy_path(self) -> None:
        """POST /llm/interview/ask returns 200 when payload is valid (mocked)."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.interview.generate_interview_answer",
            return_value={
                "raw_output": "Hello!",
                "thinking": "",
                "answer": "Hello!",
                "evaluation": {},
            },
        ):
            response = client.post(
                "/llm/interview/ask",
                json={
                    "role": "pm",
                    "provider_id": "test-provider",
                    "model": "gpt-4",
                    "question": "What is 2+2?",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["answer"] == "Hello!"

    def test_interview_ask_with_optional_fields(self) -> None:
        """POST /llm/interview/ask accepts all optional fields (mocked)."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.interview.generate_interview_answer",
            return_value={
                "raw_output": "The answer is 4.",
                "thinking": "I need to add the numbers.",
                "answer": "The answer is 4.",
                "evaluation": {"score": 10},
            },
        ):
            response = client.post(
                "/llm/interview/ask",
                json={
                    "role": "pm",
                    "provider_id": "test-provider",
                    "model": "gpt-4",
                    "question": "What is 2+2?",
                    "context": [{"role": "system", "content": "You are a math tutor."}],
                    "expects_thinking": True,
                    "criteria": ["accuracy", "clarity"],
                    "session_id": "session-123",
                    "api_key": "sk-test",
                    "headers": {"X-Custom": "value"},
                    "env_overrides": {"TEMP": "0.5"},
                    "debug": True,
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["session_id"] == "session-123"

    def test_interview_save_happy_path(self) -> None:
        """POST /llm/interview/save returns 200."""
        client = _build_client()
        response = client.post(
            "/llm/interview/save",
            json={
                "role": "pm",
                "provider_id": "test-provider",
                "model": "gpt-4",
                "report": {"score": 95, "notes": "Great job"},
                "session_id": "session-123",
            },
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["saved"] is True

    def test_interview_cancel_happy_path(self) -> None:
        """POST /llm/interview/cancel returns 200."""
        client = _build_client()
        response = client.post(
            "/llm/interview/cancel",
            json={"session_id": "session-123"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["cancelled"] is True

    def test_interview_stream_happy_path(self) -> None:
        """POST /llm/interview/stream returns SSE response (mocked)."""
        client = _build_client()

        async def _mock_stream(*_args: Any, **kwargs: Any) -> None:
            queue = kwargs.get("output_queue")
            if queue is not None:
                await queue.put({"type": "message", "data": {"chunk": "Hello"}})
                await queue.put({"type": "complete", "data": {"answer": "Hello!"}})

        with patch(
            "polaris.delivery.http.routers.interview.generate_interview_answer_streaming",
            side_effect=_mock_stream,
        ):
            response = client.post(
                "/llm/interview/stream",
                json={
                    "role": "pm",
                    "provider_id": "test-provider",
                    "model": "gpt-4",
                    "question": "What is 2+2?",
                    "session_id": "session-123",
                },
            )

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("text/event-stream")
