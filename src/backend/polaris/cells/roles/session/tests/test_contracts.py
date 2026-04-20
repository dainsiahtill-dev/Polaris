"""Unit tests for `roles.session` public contracts.

Tests the four command/event/result dataclasses and the custom error type.
All validation logic (non-empty string normalisation, JSON copy, error-
message requirements) must be exercised here so the service layer can rely on
validated inputs.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.session.public.contracts import (
    AttachRoleSessionCommandV1,
    CreateRoleSessionCommandV1,
    GetRoleSessionStateQueryV1,
    ReadRoleSessionArtifactQueryV1,
    ReadRoleSessionEpisodeQueryV1,
    RoleSessionContextQueryResultV1,
    RoleSessionError,
    RoleSessionLifecycleEventV1,
    RoleSessionResultV1,
    SearchRoleSessionMemoryQueryV1,
    UpdateRoleSessionCommandV1,
)

# ---------------------------------------------------------------------------
# CreateRoleSessionCommandV1
# ---------------------------------------------------------------------------


class TestCreateRoleSessionCommandV1HappyPath:
    def test_minimal_construction(self) -> None:
        cmd = CreateRoleSessionCommandV1(role="pm")
        assert cmd.role == "pm"
        assert cmd.workspace is None
        assert cmd.host_kind == "electron_workbench"
        assert cmd.session_type == "workbench"
        assert cmd.attachment_mode == "isolated"
        assert cmd.title is None
        assert cmd.context_config == {}
        assert cmd.capability_profile == {}

    def test_full_construction(self) -> None:
        cmd = CreateRoleSessionCommandV1(
            role="architect",
            workspace="/ws",
            host_kind="cli",
            session_type="standalone",
            attachment_mode="attached_collaborative",
            title="Design review",
            context_config={"task": "T-001"},
            capability_profile={"streaming": True},
        )
        assert cmd.role == "architect"
        assert cmd.workspace == "/ws"
        assert cmd.host_kind == "cli"
        assert cmd.session_type == "standalone"
        assert cmd.attachment_mode == "attached_collaborative"
        assert cmd.title == "Design review"
        assert cmd.context_config == {"task": "T-001"}
        assert cmd.capability_profile == {"streaming": True}


class TestCreateRoleSessionCommandV1EdgeCases:
    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            CreateRoleSessionCommandV1(role="")

    def test_whitespace_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            CreateRoleSessionCommandV1(role="   ")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CreateRoleSessionCommandV1(role="pm", workspace="")

    def test_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CreateRoleSessionCommandV1(role="pm", workspace="   ")

    def test_empty_host_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="host_kind"):
            CreateRoleSessionCommandV1(role="pm", host_kind="")

    def test_empty_session_type_raises(self) -> None:
        with pytest.raises(ValueError, match="session_type"):
            CreateRoleSessionCommandV1(role="pm", session_type="")

    def test_empty_attachment_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="attachment_mode"):
            CreateRoleSessionCommandV1(role="pm", attachment_mode="")

    def test_empty_title_raises(self) -> None:
        with pytest.raises(ValueError, match="title"):
            CreateRoleSessionCommandV1(role="pm", title="")

    def test_whitespace_title_raises(self) -> None:
        with pytest.raises(ValueError, match="title"):
            CreateRoleSessionCommandV1(role="pm", title="  ")

    def test_context_config_is_copied(self) -> None:
        original = {"key": "value"}
        cmd = CreateRoleSessionCommandV1(role="pm", context_config=original)
        original.clear()
        assert cmd.context_config == {"key": "value"}

    def test_capability_profile_is_copied(self) -> None:
        original = {"streaming": True}
        cmd = CreateRoleSessionCommandV1(role="pm", capability_profile=original)
        original.clear()
        assert cmd.capability_profile == {"streaming": True}


# ---------------------------------------------------------------------------
# UpdateRoleSessionCommandV1
# ---------------------------------------------------------------------------


class TestUpdateRoleSessionCommandV1HappyPath:
    def test_minimal_construction(self) -> None:
        cmd = UpdateRoleSessionCommandV1(session_id="sess-1")
        assert cmd.session_id == "sess-1"
        assert cmd.title is None
        assert cmd.context_config is None
        assert cmd.capability_profile is None
        assert cmd.state is None

    def test_full_construction(self) -> None:
        cmd = UpdateRoleSessionCommandV1(
            session_id="sess-1",
            title="Updated title",
            context_config={"task": "T-002"},
            capability_profile={"streaming": False},
            state="paused",
        )
        assert cmd.session_id == "sess-1"
        assert cmd.title == "Updated title"
        assert cmd.context_config == {"task": "T-002"}
        assert cmd.capability_profile == {"streaming": False}
        assert cmd.state == "paused"


class TestUpdateRoleSessionCommandV1EdgeCases:
    def test_empty_session_id_raises(self) -> None:
        with pytest.raises(ValueError, match="session_id"):
            UpdateRoleSessionCommandV1(session_id="")

    def test_empty_title_raises(self) -> None:
        with pytest.raises(ValueError, match="title"):
            UpdateRoleSessionCommandV1(session_id="sess-1", title="")

    def test_empty_state_raises(self) -> None:
        with pytest.raises(ValueError, match="state"):
            UpdateRoleSessionCommandV1(session_id="sess-1", state="")


# ---------------------------------------------------------------------------
# AttachRoleSessionCommandV1
# ---------------------------------------------------------------------------


class TestAttachRoleSessionCommandV1HappyPath:
    def test_minimal_construction(self) -> None:
        cmd = AttachRoleSessionCommandV1(session_id="sess-1")
        assert cmd.session_id == "sess-1"
        assert cmd.run_id is None
        assert cmd.task_id is None
        assert cmd.mode == "attached_readonly"
        assert cmd.note is None

    def test_full_construction(self) -> None:
        cmd = AttachRoleSessionCommandV1(
            session_id="sess-1",
            run_id="run-42",
            task_id="task-7",
            mode="attached_collaborative",
            note="linked to planning",
        )
        assert cmd.session_id == "sess-1"
        assert cmd.run_id == "run-42"
        assert cmd.task_id == "task-7"
        assert cmd.mode == "attached_collaborative"
        assert cmd.note == "linked to planning"


class TestAttachRoleSessionCommandV1EdgeCases:
    def test_empty_session_id_raises(self) -> None:
        with pytest.raises(ValueError, match="session_id"):
            AttachRoleSessionCommandV1(session_id="")

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            AttachRoleSessionCommandV1(session_id="sess-1", run_id="")

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            AttachRoleSessionCommandV1(session_id="sess-1", task_id="")

    def test_empty_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            AttachRoleSessionCommandV1(session_id="sess-1", mode="")


class TestRoleSessionContextQueries:
    def test_search_memory_query_happy_path(self) -> None:
        query = SearchRoleSessionMemoryQueryV1(
            session_id="sess-1",
            query="continuity",
            kind="artifact",
            entity="session_continuity.py",
            limit=3,
        )
        assert query.session_id == "sess-1"
        assert query.kind == "artifact"
        assert query.limit == 3

    def test_search_memory_query_rejects_empty_query(self) -> None:
        with pytest.raises(ValueError, match="query"):
            SearchRoleSessionMemoryQueryV1(session_id="sess-1", query="")

    def test_read_artifact_query_rejects_invalid_range(self) -> None:
        with pytest.raises(ValueError, match="end_line"):
            ReadRoleSessionArtifactQueryV1(
                session_id="sess-1",
                artifact_id="art-1",
                start_line=5,
                end_line=3,
            )

    def test_read_episode_query_requires_episode_id(self) -> None:
        with pytest.raises(ValueError, match="episode_id"):
            ReadRoleSessionEpisodeQueryV1(session_id="sess-1", episode_id="")

    def test_get_state_query_requires_path(self) -> None:
        with pytest.raises(ValueError, match="path"):
            GetRoleSessionStateQueryV1(session_id="sess-1", path="")

    def test_context_query_result_requires_error_payload_on_failure(self) -> None:
        with pytest.raises(ValueError, match="failed result"):
            RoleSessionContextQueryResultV1(ok=False, session_id="sess-1")


# ---------------------------------------------------------------------------
# RoleSessionLifecycleEventV1
# ---------------------------------------------------------------------------


class TestRoleSessionLifecycleEventV1HappyPath:
    def test_minimal_construction(self) -> None:
        evt = RoleSessionLifecycleEventV1(
            event_id="evt-1",
            session_id="sess-1",
            role="pm",
            status="active",
            occurred_at="2026-03-23T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.session_id == "sess-1"
        assert evt.role == "pm"
        assert evt.status == "active"
        assert evt.occurred_at == "2026-03-23T10:00:00Z"
        assert evt.run_id is None
        assert evt.task_id is None

    def test_full_construction(self) -> None:
        evt = RoleSessionLifecycleEventV1(
            event_id="evt-2",
            session_id="sess-1",
            role="pm",
            status="completed",
            occurred_at="2026-03-23T11:00:00Z",
            run_id="run-99",
            task_id="task-5",
        )
        assert evt.run_id == "run-99"
        assert evt.task_id == "task-5"


class TestRoleSessionLifecycleEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            RoleSessionLifecycleEventV1(
                event_id="",
                session_id="sess-1",
                role="pm",
                status="active",
                occurred_at="2026-03-23T10:00:00Z",
            )


# ---------------------------------------------------------------------------
# RoleSessionResultV1
# ---------------------------------------------------------------------------


class TestRoleSessionResultV1HappyPath:
    def test_success_result(self) -> None:
        res = RoleSessionResultV1(
            ok=True,
            session_id="sess-1",
            role="pm",
            state="active",
            payload={"message_count": 5},
        )
        assert res.ok is True
        assert res.session_id == "sess-1"
        assert res.role == "pm"
        assert res.state == "active"
        assert res.error_code is None
        assert res.error_message is None

    def test_failure_result(self) -> None:
        res = RoleSessionResultV1(
            ok=False,
            session_id="sess-1",
            role="pm",
            state="active",
            error_code="session_not_found",
            error_message="Session does not exist",
        )
        assert res.ok is False
        assert res.error_code == "session_not_found"
        assert res.error_message == "Session does not exist"


class TestRoleSessionResultV1EdgeCases:
    def test_failed_result_requires_error_code_or_message(self) -> None:
        with pytest.raises(ValueError, match="failed result must include"):
            RoleSessionResultV1(
                ok=False,
                session_id="sess-1",
                role="pm",
                state="active",
            )

    def test_failure_with_code_only_is_valid(self) -> None:
        res = RoleSessionResultV1(
            ok=False,
            session_id="sess-1",
            role="pm",
            state="active",
            error_code="timeout",
        )
        assert res.error_code == "timeout"

    def test_failure_with_message_only_is_valid(self) -> None:
        res = RoleSessionResultV1(
            ok=False,
            session_id="sess-1",
            role="pm",
            state="active",
            error_message="Timeout exceeded",
        )
        assert res.error_message == "Timeout exceeded"


# ---------------------------------------------------------------------------
# RoleSessionError
# ---------------------------------------------------------------------------


class TestRoleSessionError:
    def test_default_code(self) -> None:
        err = RoleSessionError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "role_session_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = RoleSessionError(
            "Session locked",
            code="session_locked",
            details={"session_id": "sess-1"},
        )
        assert str(err) == "Session locked"
        assert err.code == "session_locked"
        assert err.details == {"session_id": "sess-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            RoleSessionError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            RoleSessionError("error", code="")
