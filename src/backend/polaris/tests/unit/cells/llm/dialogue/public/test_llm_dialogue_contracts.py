"""Tests for polaris.cells.llm.dialogue.public.contracts.

Covers dataclass construction, validation, serialization, and error contracts.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from polaris.cells.llm.dialogue.public.contracts import (
    DialogueTurnCompletedEventV1,
    DialogueTurnResultV1,
    ILlmDialogueService,
    InvokeDocsDialogueCommandV1,
    InvokeRoleDialogueCommandV1,
    LlmDialogueError,
    ValidateRoleOutputQueryV1,
)


class TestInvokeRoleDialogueCommandV1:
    def test_minimal_construction(self) -> None:
        cmd = InvokeRoleDialogueCommandV1(
            workspace="/tmp/ws",
            role="pm",
            message="hello",
        )
        assert cmd.workspace == "/tmp/ws"
        assert cmd.role == "pm"
        assert cmd.message == "hello"
        assert cmd.stream is False
        assert cmd.context == {}
        assert cmd.metadata == {}

    def test_with_context_and_metadata(self) -> None:
        cmd = InvokeRoleDialogueCommandV1(
            workspace="ws",
            role="architect",
            message="design this",
            stream=True,
            context={"key": "val"},
            metadata={"trace": "id"},
        )
        assert cmd.stream is True
        assert cmd.context == {"key": "val"}
        assert cmd.metadata == {"trace": "id"}

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            InvokeRoleDialogueCommandV1(workspace="", role="pm", message="hello")

    def test_whitespace_only_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            InvokeRoleDialogueCommandV1(workspace="   ", role="pm", message="hello")

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            InvokeRoleDialogueCommandV1(workspace="ws", role="", message="hello")

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            InvokeRoleDialogueCommandV1(workspace="ws", role="pm", message="")

    def test_context_is_copied(self) -> None:
        original = {"a": 1}
        cmd = InvokeRoleDialogueCommandV1(workspace="ws", role="pm", message="hi", context=original)
        original["a"] = 2
        assert cmd.context == {"a": 1}

    def test_frozen_dataclass(self) -> None:
        cmd = InvokeRoleDialogueCommandV1(workspace="ws", role="pm", message="hi")
        with pytest.raises(FrozenInstanceError):
            cmd.role = "qa"  # type: ignore[misc]


class TestInvokeDocsDialogueCommandV1:
    def test_minimal_construction(self) -> None:
        cmd = InvokeDocsDialogueCommandV1(workspace="/tmp/ws", message="explain")
        assert cmd.workspace == "/tmp/ws"
        assert cmd.message == "explain"
        assert cmd.fields == {}
        assert cmd.state == {}
        assert cmd.stream is False

    def test_with_fields_and_state(self) -> None:
        cmd = InvokeDocsDialogueCommandV1(
            workspace="ws",
            message="docs",
            fields={"topic": "api"},
            state={"history": ["a", "b"]},
            stream=True,
        )
        assert cmd.fields == {"topic": "api"}
        assert cmd.state == {"history": ["a", "b"]}
        assert cmd.stream is True

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            InvokeDocsDialogueCommandV1(workspace="", message="docs")

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            InvokeDocsDialogueCommandV1(workspace="ws", message="")

    def test_fields_are_copied(self) -> None:
        original = {"x": 1}
        cmd = InvokeDocsDialogueCommandV1(workspace="ws", message="hi", fields=original)
        original["x"] = 2
        assert cmd.fields == {"x": 1}


class TestValidateRoleOutputQueryV1:
    def test_construction(self) -> None:
        q = ValidateRoleOutputQueryV1(role="pm", output="some output")
        assert q.role == "pm"
        assert q.output == "some output"

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            ValidateRoleOutputQueryV1(role="", output="out")

    def test_empty_output_raises(self) -> None:
        with pytest.raises(ValueError, match="output must be a non-empty string"):
            ValidateRoleOutputQueryV1(role="pm", output="")


class TestDialogueTurnCompletedEventV1:
    def test_minimal_construction(self) -> None:
        ev = DialogueTurnCompletedEventV1(
            event_id="e1",
            workspace="ws",
            role="pm",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.workspace == "ws"
        assert ev.role == "pm"
        assert ev.status == "ok"
        assert ev.completed_at == "2024-01-01T00:00:00Z"
        assert ev.run_id is None
        assert ev.task_id is None

    def test_with_optional_fields(self) -> None:
        ev = DialogueTurnCompletedEventV1(
            event_id="e1",
            workspace="ws",
            role="pm",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
            run_id="r1",
            task_id="t1",
        )
        assert ev.run_id == "r1"
        assert ev.task_id == "t1"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            DialogueTurnCompletedEventV1(
                event_id="",
                workspace="ws",
                role="pm",
                status="ok",
                completed_at="2024-01-01T00:00:00Z",
            )

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id must be a non-empty string"):
            DialogueTurnCompletedEventV1(
                event_id="e1",
                workspace="ws",
                role="pm",
                status="ok",
                completed_at="2024-01-01T00:00:00Z",
                run_id="",
            )

    def test_none_run_id_allowed(self) -> None:
        ev = DialogueTurnCompletedEventV1(
            event_id="e1",
            workspace="ws",
            role="pm",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
            run_id=None,
        )
        assert ev.run_id is None


class TestDialogueTurnResultV1:
    def test_success_result(self) -> None:
        result = DialogueTurnResultV1(
            ok=True,
            status="ok",
            workspace="ws",
            role="pm",
            content="hello",
        )
        assert result.ok is True
        assert result.status == "ok"
        assert result.content == "hello"
        assert result.metadata == {}
        assert result.error_code is None
        assert result.error_message is None

    def test_failed_result_with_error_code(self) -> None:
        result = DialogueTurnResultV1(
            ok=False,
            status="failed",
            workspace="ws",
            role="pm",
            content="",
            error_code="E001",
            error_message="something went wrong",
        )
        assert result.ok is False
        assert result.error_code == "E001"
        assert result.error_message == "something went wrong"

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            DialogueTurnResultV1(
                ok=False,
                status="failed",
                workspace="ws",
                role="pm",
                content="",
            )

    def test_content_coerced_to_string(self) -> None:
        result = DialogueTurnResultV1(
            ok=True,
            status="ok",
            workspace="ws",
            role="pm",
            content=123,  # type: ignore[arg-type]
        )
        assert result.content == "123"

    def test_metadata_is_copied(self) -> None:
        original = {"key": "val"}
        result = DialogueTurnResultV1(
            ok=True,
            status="ok",
            workspace="ws",
            role="pm",
            content="hi",
            metadata=original,
        )
        original["key"] = "changed"
        assert result.metadata == {"key": "val"}

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            DialogueTurnResultV1(
                ok=True,
                status="",
                workspace="ws",
                role="pm",
                content="hi",
            )


class TestLlmDialogueError:
    def test_default_code(self) -> None:
        err = LlmDialogueError("something bad")
        assert str(err) == "something bad"
        assert err.code == "llm_dialogue_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = LlmDialogueError(
            "bad request",
            code="validation_error",
            details={"field": "message"},
        )
        assert err.code == "validation_error"
        assert err.details == {"field": "message"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            LlmDialogueError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            LlmDialogueError("msg", code="")

    def test_details_are_copied(self) -> None:
        original = {"a": 1}
        err = LlmDialogueError("msg", details=original)
        original["a"] = 2
        assert err.details == {"a": 1}

    def test_is_runtime_error(self) -> None:
        assert issubclass(LlmDialogueError, RuntimeError)


class TestILlmDialogueService:
    def test_is_protocol(self) -> None:
        assert hasattr(ILlmDialogueService, "invoke_role_dialogue")
        assert hasattr(ILlmDialogueService, "invoke_docs_dialogue")
        assert hasattr(ILlmDialogueService, "validate_role_output")

    def test_runtime_checkable(self) -> None:
        class FakeService:
            async def invoke_role_dialogue(self, command): ...
            async def invoke_docs_dialogue(self, command): ...
            def validate_role_output(self, query): ...

        assert isinstance(FakeService(), ILlmDialogueService)

    def test_missing_method_fails_check(self) -> None:
        class BadService:
            async def invoke_role_dialogue(self, command): ...

        assert not isinstance(BadService(), ILlmDialogueService)
