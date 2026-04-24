"""Contract tests for polaris.delivery.http.routers.interview module.

Regression test for: InterviewAskPayload imported under TYPE_CHECKING block,
which caused FastAPI to fail resolving request body models at runtime (422).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

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


class TestInterviewRouter:
    """Contract tests for the interview router."""

    def test_interview_ask_happy_path(self) -> None:
        """POST /llm/interview/ask returns 200 when payload is valid."""
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
        """POST /llm/interview/ask accepts all optional fields."""
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
        """POST /llm/interview/stream returns SSE response."""
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
