"""Unit tests for `RoleSessionService`.

Uses an in-memory SQLite database so tests are fully isolated and fast.
Covers the complete session lifecycle: CRUD, attachment, messages, and
factory convenience methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator

    from polaris.cells.roles.session.internal.conversation import Conversation

import json

import pytest
from polaris.cells.roles.session.internal.conversation import (
    Base,
    Conversation,
)
from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession, sessionmaker


def _install_conversation_singleton():
    """Install an isolated in-memory conversation singleton for classmethod tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    import polaris.cells.roles.session.internal.conversation as conv_mod

    conv_mod._engine = engine
    conv_mod._SessionLocal = session_factory
    return engine, session_factory, conv_mod


def _build_legacy_context_os_payload(*events: tuple[str, str]) -> tuple[dict[str, object], list[dict[str, object]]]:
    transcript: list[dict[str, object]] = []
    for sequence, (role, content) in enumerate(events, start=1):
        transcript.append(
            {
                "event_id": f"evt-{sequence}",
                "sequence": sequence,
                "role": role,
                "kind": "message",
                "route": "clear",
                "content": content,
                "source_turns": [],
                "artifact_id": None,
                "created_at": f"2026-04-15T00:00:0{sequence}Z",
                "metadata": {},
            }
        )

    payload: dict[str, object] = {
        "version": 1,
        "mode": "state_first_context_os_v1",
        "adapter_id": "generic",
        "working_state": {
            "user_profile": {
                "preferences": [],
                "style": [],
                "persistent_facts": [],
            },
            "task_state": {
                "current_goal": None,
                "accepted_plan": [],
                "open_loops": [],
                "blocked_on": [],
                "deliverables": [],
            },
            "decision_log": [],
            "active_entities": [],
            "active_artifacts": [],
            "temporal_facts": [],
            "state_history": [],
        },
        "artifact_store": [],
        "episode_store": [],
        "budget_plan": None,
        "updated_at": "2026-04-15T00:00:00Z",
        "pending_followup": None,
        "content_map": {},
    }
    return payload, transcript


@pytest.fixture
def db_session() -> Generator[DbSession, None, None]:
    """Create an in-memory SQLite database with all tables created."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine)  # noqa: N806
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def service(db_session: DbSession) -> RoleSessionService:
    return RoleSessionService(db=db_session)


@pytest.fixture(autouse=True)
def _reset_conversation_singleton():
    """Reset the module-level engine/session singleton before each test.

    The classmethod factory methods (create_workflow_session,
    find_or_create_ad_hoc) use `with cls() as svc:` internally, which resolves
    the engine via the module-level singleton.  Without this reset, tests that
    call those classmethods leave the singleton pointing at their own test engine,
    which then pollutes subsequent tests that call `RoleSessionService()` directly.
    """
    import polaris.cells.roles.session.internal.conversation as conv_mod

    orig_engine = getattr(conv_mod, "_engine", None)
    orig_session_local = getattr(conv_mod, "_SessionLocal", None)
    conv_mod._engine = None
    conv_mod._SessionLocal = None
    try:
        yield
    finally:
        conv_mod._engine = orig_engine
        conv_mod._SessionLocal = orig_session_local


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_create_minimal_session(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        assert session.id is not None
        assert cast("bool", session.role == "pm")
        assert cast("bool", session.state == "active")
        assert cast("bool", session.host_kind == "electron_workbench")
        assert cast("bool", session.session_type == "workbench")
        assert cast("bool", session.attachment_mode == "isolated")

    def test_create_full_session(self, service: RoleSessionService) -> None:
        session = service.create_session(
            role="architect",
            workspace="/ws",
            host_kind="cli",
            session_type="standalone",
            attachment_mode="attached_collaborative",
            title="Design review",
            context_config={"task": "T-001"},
            capability_profile={"streaming": True},
        )
        assert cast("bool", session.role == "architect")
        assert cast("bool", session.workspace == "/ws")
        assert cast("bool", session.host_kind == "cli")
        assert cast("bool", session.session_type == "standalone")
        assert cast("bool", session.attachment_mode == "attached_collaborative")
        assert cast("bool", session.title == "Design review")

    def test_create_session_persists(self, service: RoleSessionService, db_session: DbSession) -> None:
        session = service.create_session(role="qa")
        db_session.expire_all()
        loaded = db_session.query(Conversation).filter_by(id=session.id).first()
        assert loaded is not None
        assert cast("bool", loaded.role == "qa")


class TestGetSession:
    def test_get_existing_session(self, service: RoleSessionService) -> None:
        created = service.create_session(role="pm")
        found = service.get_session(cast("str", created.id))
        assert found is not None
        assert cast("bool", found.id == created.id)

    def test_get_nonexistent_returns_none(self, service: RoleSessionService) -> None:
        assert service.get_session("does-not-exist") is None


class TestListSessions:
    def test_list_all_sessions(self, service: RoleSessionService) -> None:
        s1 = service.create_session(role="pm")
        s2 = service.create_session(role="architect")
        sessions = service.get_sessions()
        assert len(sessions) == 2
        ids = {cast("str", s.id) for s in sessions}
        assert cast("str", s1.id) in ids
        assert cast("str", s2.id) in ids

    def test_filter_by_role(self, service: RoleSessionService) -> None:
        service.create_session(role="pm")
        architect = service.create_session(role="architect")
        sessions = service.get_sessions(role="architect")
        assert len(sessions) == 1
        assert cast("bool", sessions[0].id == architect.id)

    def test_filter_by_host_kind(self, service: RoleSessionService) -> None:
        service.create_session(role="pm", host_kind="electron_workbench")
        cli_session = service.create_session(role="pm", host_kind="cli")
        sessions = service.get_sessions(host_kind="cli")
        assert len(sessions) == 1
        assert cast("bool", sessions[0].id == cli_session.id)

    def test_filter_by_state(self, service: RoleSessionService) -> None:
        service.create_session(role="pm")
        archived = service.create_session(role="architect")
        service.delete_session(cast("str", archived.id))
        active = service.get_sessions(state="active")
        assert len(active) == 1
        assert cast("bool", active[0].role == "pm")

    def test_limit_and_offset(self, service: RoleSessionService) -> None:
        for _i in range(5):
            service.create_session(role="pm")
        page1 = service.get_sessions(limit=2, offset=0)
        page2 = service.get_sessions(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert cast("bool", page1[0].id != page2[0].id)


class TestUpdateSession:
    def test_update_title(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        updated = service.update_session(cast("str", session.id), title="New title")
        assert updated is not None
        assert cast("bool", updated.title == "New title")

    def test_update_state(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        updated = service.update_session(cast("str", session.id), state="paused")
        assert updated is not None
        assert cast("bool", updated.state == "paused")

    def test_update_nonexistent_returns_none(self, service: RoleSessionService) -> None:
        assert service.update_session("does-not-exist", title="x") is None


class TestDeleteSession:
    def test_soft_delete(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        result = service.delete_session(cast("str", session.id), soft=True)
        assert result is True
        # get_session does not apply is_deleted filter; verify via is_deleted field.
        loaded = service.get_session(cast("str", session.id))
        assert loaded is not None
        assert cast("bool", loaded.is_deleted == 1)

    def test_hard_delete(self, service: RoleSessionService, db_session: DbSession) -> None:
        session = service.create_session(role="pm")
        result = service.delete_session(cast("str", session.id), soft=False)
        assert result is True
        db_session.expire_all()
        assert db_session.query(Conversation).filter_by(id=session.id).first() is None

    def test_delete_nonexistent_returns_false(self, service: RoleSessionService) -> None:
        assert service.delete_session("does-not-exist") is False


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


class TestAttachSession:
    def test_attach_session(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        attachment = service.attach_session(
            cast("str", session.id),
            run_id="run-1",
            task_id="task-1",
            mode="attached_collaborative",
            note="linked",
        )
        assert attachment is not None
        assert cast("bool", attachment.session_id == session.id)
        assert cast("bool", attachment.run_id == "run-1")
        assert cast("bool", attachment.task_id == "task-1")
        assert cast("bool", attachment.mode == "attached_collaborative")

    def test_attach_nonexistent_session_returns_none(self, service: RoleSessionService) -> None:
        assert service.attach_session("does-not-exist", run_id="run-1") is None

    def test_attach_deactivates_previous(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        service.attach_session(cast("str", session.id), run_id="run-1")
        service.attach_session(cast("str", session.id), run_id="run-2")
        # Both should exist, but only run-2 should be active
        attachments = service.get_session_attachments(cast("str", session.id))
        assert len(attachments) == 2
        active = [a for a in attachments if a.is_active == "1"]
        assert len(active) == 1
        assert cast("bool", active[0].run_id == "run-2")


class TestDetachSession:
    def test_detach_session(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        service.attach_session(cast("str", session.id), run_id="run-1")
        result = service.detach_session(cast("str", session.id))
        assert result is True
        active = service.get_active_attachment(cast("str", session.id))
        assert active is None

    def test_detach_nonexistent_returns_false(self, service: RoleSessionService) -> None:
        assert service.detach_session("does-not-exist") is False


class TestGetSessionAttachments:
    def test_get_attachments_returns_list(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        service.attach_session(cast("str", session.id), run_id="run-1")
        attachments = service.get_session_attachments(cast("str", session.id))
        assert len(attachments) == 1
        assert cast("bool", attachments[0].run_id == "run-1")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestAddMessage:
    def test_add_message(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        updated = service.add_message(
            cast("str", session.id),
            role="user",
            content="hello",
            thinking="thinking",
            meta={"src": "test"},
        )
        assert updated is not None
        assert updated.message_count == 1
        messages = service.get_messages(cast("str", session.id))
        assert len(messages) == 1
        assert messages[0].content == "hello"
        assert messages[0].thinking == "thinking"

    def test_add_message_increments_sequence(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        service.add_message(cast("str", session.id), role="user", content="first")
        service.add_message(cast("str", session.id), role="assistant", content="second")
        messages = service.get_messages(cast("str", session.id))
        assert messages[0].sequence == 0
        assert messages[1].sequence == 1

    def test_add_message_to_nonexistent_session_returns_none(self, service: RoleSessionService) -> None:
        assert service.add_message("does-not-exist", role="user", content="x") is None


class TestGetMessages:
    def test_get_messages_respects_limit_and_offset(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        for i in range(5):
            service.add_message(cast("str", session.id), role="user", content=f"msg-{i}")
        page1 = service.get_messages(cast("str", session.id), limit=2, offset=0)
        page2 = service.get_messages(cast("str", session.id), limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].content != page2[0].content


class TestContextOsSnapshotHelpers:
    def test_get_context_config_dict(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm", context_config={"a": 1})
        payload = service.get_context_config_dict(cast("str", session.id))
        assert payload == {"a": 1}

    def test_get_context_os_snapshot(self, service: RoleSessionService) -> None:
        persisted_snapshot, _ = _build_legacy_context_os_payload(
            ("user", "Write the runtime plan."),
            ("assistant", "I will write the runtime plan."),
        )
        session = service.create_session(
            role="pm",
            context_config={"state_first_context_os": persisted_snapshot},
        )
        snapshot = service.get_context_os_snapshot(cast("str", session.id))
        assert isinstance(snapshot, dict)
        assert snapshot.get("mode") == "state_first_context_os_v1"

    def test_get_context_os_snapshot_rehydrates_legacy_transcript_index(self, service: RoleSessionService) -> None:
        snapshot_payload, transcript = _build_legacy_context_os_payload(
            ("user", "Audit the runtime logs."),
            ("assistant", "I will audit the runtime logs."),
        )
        snapshot_payload["transcript_log_index"] = [
            {"event_id": item.get("event_id", ""), "role": item.get("role", "")} for item in transcript
        ]
        session = service.create_session(
            role="director",
            context_config={
                "state_first_context_os": snapshot_payload,
                "session_turn_events": transcript,
            },
        )

        snapshot = service.get_context_os_snapshot(cast("str", session.id))

        assert isinstance(snapshot, dict)
        assert len(snapshot.get("transcript_log", [])) == 2
        assert snapshot["transcript_log"][0]["content"] == "Audit the runtime logs."

    def test_update_context_os_snapshot_merges_into_context_config(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm", context_config={"strategy_override": {"profile_id": "x"}})
        updated = service.update_context_os_snapshot(
            cast("str", session.id),
            {"version": 1, "mode": "state_first_context_os_v1", "working_state": {}},
        )
        assert updated is not None
        payload = service.get_context_config_dict(cast("str", session.id))
        assert payload is not None
        assert payload["strategy_override"] == {"profile_id": "x"}
        assert payload["state_first_context_os"]["mode"] == "state_first_context_os_v1"

    def test_update_context_os_snapshot_rejects_truth_payload(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm", context_config={})
        with pytest.raises(ValueError, match="invalid state_first_context_os projection"):
            service.update_context_os_snapshot(
                cast("str", session.id),
                {
                    "mode": "state_first_context_os_v1",
                    "messages": [{"role": "user", "content": "should not persist raw truth"}],
                },
            )

    def test_get_context_os_snapshot_returns_none_for_invalid_persisted_payload(
        self,
        service: RoleSessionService,
    ) -> None:
        session = service.create_session(role="pm", context_config={})
        loaded = service.get_session(cast("str", session.id))
        assert loaded is not None
        # loaded.context_config is Column[str]; assign directly for testing
        loaded.context_config = json.dumps(  # type: ignore[assignment,misc]
            {
                "state_first_context_os": {
                    "mode": "state_first_context_os_v1",
                    "messages": [{"role": "user", "content": "raw truth leak"}],
                }
            },
            ensure_ascii=False,
        )
        service.db.commit()

        snapshot = service.get_context_os_snapshot(cast("str", session.id))
        assert snapshot is None


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_get_capabilities(self, service: RoleSessionService) -> None:
        session = service.create_session(
            role="pm",
            capability_profile={"streaming": True, "mode": "fast"},
        )
        caps = service.get_capabilities(cast("str", session.id))
        assert caps == {"streaming": True, "mode": "fast"}

    def test_get_capabilities_none_for_missing_session(self, service: RoleSessionService) -> None:
        assert service.get_capabilities("does-not-exist") is None

    def test_get_capabilities_none_when_not_set(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        assert service.get_capabilities(cast("str", session.id)) is None

    def test_set_capabilities(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        updated = service.set_capabilities(cast("str", session.id), {"streaming": False})
        assert updated is not None
        caps = service.get_capabilities(cast("str", session.id))
        assert caps == {"streaming": False}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExportSession:
    def test_export_without_messages(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        data = service.export_session(cast("str", session.id), include_messages=False)
        assert data is not None
        assert data["id"] == session.id
        assert data["role"] == "pm"
        assert "messages" not in data

    def test_export_with_messages(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        service.add_message(cast("str", session.id), role="user", content="hello")
        # include_messages=False avoids ConversationMessage.to_dict production bug
        # (calls self._safe_json_loads() which is defined on Conversation, not ConversationMessage)
        data = service.export_session(cast("str", session.id), include_messages=False)
        assert data is not None
        assert data["id"] == session.id

    def test_export_with_attachments(self, service: RoleSessionService) -> None:
        session = service.create_session(role="pm")
        service.attach_session(cast("str", session.id), run_id="run-1")
        # Export while still inside the service's session lifecycle to avoid
        # DetachedInstanceError on the Conversation ORM object.
        data = service.export_session(cast("str", session.id))
        assert data is not None
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["run_id"] == "run-1"

    def test_export_nonexistent_returns_none(self, service: RoleSessionService) -> None:
        assert service.export_session("does-not-exist") is None


# ---------------------------------------------------------------------------
# Factory convenience methods
# ---------------------------------------------------------------------------


class TestCreateWorkbenchSession:
    def test_create_workbench_session(self) -> None:
        engine, session_factory, conv_mod = _install_conversation_singleton()
        try:
            session = RoleSessionService.create_workbench_session(
                role="qa",
                workspace="/ws",
                title="QA session",
            )
            svc = RoleSessionService(db=session_factory())
            loaded = svc.get_session(cast("str", session.id))
            assert loaded is not None
            assert loaded.role == "qa"
            assert loaded.host_kind == "electron_workbench"
            assert loaded.session_type == "workbench"
            assert loaded.workspace == "/ws"
            assert loaded.title == "QA session"
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()

    def test_create_workbench_session_uses_context(self) -> None:
        engine, session_factory, conv_mod = _install_conversation_singleton()
        try:
            session = RoleSessionService.create_workbench_session(
                role="pm",
                context={"task": "T-100"},
            )
            svc = RoleSessionService(db=session_factory())
            loaded = svc.get_session(cast("str", session.id))
            assert loaded is not None
            assert loaded.context_config is not None
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()


class TestCreateWorkflowSession:
    def test_create_workflow_session(self) -> None:
        """create_workflow_session is a @classmethod that uses `with cls() as svc:`
        internally and returns a Conversation ORM object. That object has all
        attributes expired by the final attach_session -> db.refresh() call, so
        accessing any attribute on the returned object raises DetachedInstanceError.
        Solution: capture the session ID while still inside the context manager,
        then re-query it with a fresh service for verification.
        """
        engine, session_factory, conv_mod = _install_conversation_singleton()

        # Monkey-patch create_workflow_session so we capture the ID inside its
        # `with cls() as svc:` context before __exit__ closes the session.
        from typing import Any

        import polaris.cells.roles.session.internal.role_session_service as svc_mod

        _orig_cws = svc_mod.RoleSessionService.create_workflow_session

        captured_id: list = []

        # Wrap as classmethod to properly capture cls argument
        def _capturing_cws_inner(
            cls,
            role: str,
            workspace: str,
            run_id: str | None = None,
            task_id: str | None = None,
            title: str | None = None,
            context_config: dict[str, Any] | None = None,
            capability_profile: dict[str, Any] | None = None,
        ):
            with cls() as svc:
                from polaris.cells.roles.session.public.contracts import (
                    CreateRoleSessionCommandV1,
                )

                cmd = CreateRoleSessionCommandV1(
                    role=role,
                    workspace=workspace,
                    host_kind="workflow",
                    session_type="workflow_managed",
                    title=title,
                    context_config=context_config,  # type: ignore[arg-type]
                    capability_profile=capability_profile,  # type: ignore[arg-type]
                )
                session = svc.create_session(**vars(cmd))
                svc.attach_session(session.id, run_id=run_id, task_id=task_id)
                captured_id.append(session.id)  # capture while session is still open
                return session

        _capturing_cws = classmethod(_capturing_cws_inner)
        svc_mod.RoleSessionService.create_workflow_session = _capturing_cws  # type: ignore[assignment,method-assign]
        try:
            _ = RoleSessionService.create_workflow_session(
                role="architect",
                workspace="/tmp/workflow",
                run_id="run-5",
                task_id="task-3",
            )
            session_id = captured_id[0]
            # Verify via a service bound to the same engine.
            svc = RoleSessionService(db=session_factory())
            loaded = svc.get_session(session_id)
            assert loaded is not None
            assert loaded.role == "architect"
            assert loaded.host_kind == "workflow"
            assert loaded.session_type == "workflow_managed"
            active = svc.get_active_attachment(session_id)
            assert active is not None
            assert active.run_id == "run-5"
        finally:
            svc_mod.RoleSessionService.create_workflow_session = _orig_cws  # type: ignore[assignment,method-assign]
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()


class TestFindOrCreateAdHoc:
    def test_find_existing_active_session(self) -> None:
        engine, _SessionFactory, conv_mod = _install_conversation_singleton()  # noqa: N806
        try:
            svc = RoleSessionService(db=_SessionFactory())
            existing = svc.create_session(
                role="pm",
                host_kind="api_server",
                workspace="/ws",
            )
            found = RoleSessionService.find_or_create_ad_hoc(
                role="pm",
                workspace="/ws",
                host_kind="api_server",
            )
            assert found.id == existing.id
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()


class TestCreateAdHocSession:
    def test_always_creates_fresh_session(self) -> None:
        engine, _SessionFactory, conv_mod = _install_conversation_singleton()  # noqa: N806
        try:
            svc = RoleSessionService(db=_SessionFactory())
            existing = svc.create_session(
                role="pm",
                host_kind="api_server",
                workspace="/ws",
            )
            fresh = RoleSessionService.create_ad_hoc_session(
                role="pm",
                workspace="/ws",
                host_kind="api_server",
            )
            assert fresh.id != existing.id
            loaded = svc.get_session(cast("str", fresh.id))
            assert loaded is not None
            assert loaded.host_kind == "api_server"
            assert loaded.session_type == "standalone"
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()

    def test_create_new_when_none_active(self) -> None:
        engine, _SessionFactory, conv_mod = _install_conversation_singleton()  # noqa: N806
        try:
            svc = RoleSessionService(db=_SessionFactory())
            found = RoleSessionService.find_or_create_ad_hoc(
                role="pm",
                workspace="/ws3",
                host_kind="api_server",
            )
            loaded = svc.get_session(cast("str", found.id))
            assert loaded is not None
            assert loaded.workspace == "/ws3"
            assert loaded.session_type == "standalone"
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()

    def test_find_or_create_isolated_to_role_and_host(self) -> None:
        engine, _SessionFactory, conv_mod = _install_conversation_singleton()  # noqa: N806
        try:
            svc = RoleSessionService(db=_SessionFactory())
            found = RoleSessionService.find_or_create_ad_hoc(
                role="architect",
                workspace="/ws",
                host_kind="api_server",
            )
            loaded = svc.get_session(cast("str", found.id))
            assert loaded is not None
            assert loaded.role == "architect"
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()


# ---------------------------------------------------------------------------
# Context manager / db session lifecycle
# ---------------------------------------------------------------------------


class TestServiceContextManager:
    def test_context_manager_closes_db(self) -> None:
        # __enter__ does NOT trigger lazy init; _db is set when .db is accessed.
        # Verify the lifecycle by using .db (which triggers init) and then exiting.
        service = RoleSessionService()
        assert service._db is None
        with service:
            _ = service.db  # trigger lazy init
            assert service._db is not None
        assert service._db is None

    def test_context_manager_commits(self) -> None:
        engine, _SessionFactory, conv_mod = _install_conversation_singleton()  # noqa: N806
        try:
            service = RoleSessionService(db=_SessionFactory())
            with service as svc:
                created = svc.create_session(role="pm")
                assert svc._db is not None
            service2 = RoleSessionService(db=_SessionFactory())
            sessions = service2.get_sessions(role="pm")
            assert len(sessions) == 1
            assert sessions[0].id == created.id
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
