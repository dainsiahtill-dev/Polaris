"""Tests for polaris.application.session_admin."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.application.session_admin import (
    SessionAdminError,
    SessionAdminService,
    SessionListResult,
    _close_svc,
    _conversation_to_summary,
)
from polaris.cells.roles.session.public.contracts import (
    CreateRoleSessionCommandV1,
    RoleSessionError,
    SessionState,
    UpdateRoleSessionCommandV1,
)


class FakeRoleSessionError(RoleSessionError):
    """Concrete fake for RoleSessionError."""

    def __init__(self, message: str, code: str = "fake") -> None:
        super().__init__(message)
        self.code = code


class TestSessionAdminError:
    def test_default_code(self) -> None:
        err = SessionAdminError("oops")
        assert err.code == "session_admin_error"
        assert str(err) == "oops"

    def test_custom_code_and_cause(self) -> None:
        cause = ValueError("inner")
        err = SessionAdminError("oops", code="custom", cause=cause)
        assert err.code == "custom"
        assert err.cause is cause


class TestConversationToSummary:
    def test_basic_mapping(self) -> None:
        conv = MagicMock()
        conv.id = "c1"
        conv.role = "pm"
        conv.state = SessionState.ACTIVE.value
        conv.host_kind = "electron"
        conv.session_type = "workbench"
        conv.attachment_mode = "isolated"
        conv.workspace = "/tmp"
        conv.title = "title"
        conv.context_config = None
        conv.capability_profile = None
        conv.created_at = None
        conv.updated_at = None
        summary = _conversation_to_summary(conv)
        assert summary.session_id == "c1"
        assert summary.role == "pm"
        assert summary.state == SessionState.ACTIVE.value
        assert summary.workspace == "/tmp"
        assert summary.title == "title"
        assert summary.context_config == {}
        assert summary.capability_profile == {}

    def test_json_parsing(self) -> None:
        conv = MagicMock()
        conv.id = "c2"
        conv.role = "qa"
        conv.state = "archived"
        conv.host_kind = "web"
        conv.session_type = "chat"
        conv.attachment_mode = "shared"
        conv.workspace = None
        conv.title = None
        conv.context_config = '{"key": "val"}'
        conv.capability_profile = {"k": "v"}
        conv.created_at = "2024-01-01"
        conv.updated_at = "2024-01-02"
        summary = _conversation_to_summary(conv)
        assert summary.context_config == {"key": "val"}
        assert summary.capability_profile == {"k": "v"}
        assert summary.created_at == "2024-01-01"
        assert summary.updated_at == "2024-01-02"

    def test_invalid_json_fallback(self) -> None:
        conv = MagicMock()
        conv.id = "c3"
        conv.role = "architect"
        conv.state = "active"
        conv.host_kind = ""
        conv.session_type = ""
        conv.attachment_mode = ""
        conv.workspace = None
        conv.title = None
        conv.context_config = "not-json"
        conv.capability_profile = "also-not-json"
        conv.created_at = None
        conv.updated_at = None
        summary = _conversation_to_summary(conv)
        assert summary.context_config == {}
        assert summary.capability_profile == {}


class TestSessionAdminService:
    # -- _make_service -------------------------------------------------------

    def test_make_service_lazy_import_failure(self) -> None:
        svc = SessionAdminService()
        with (
            patch.dict(
                "sys.modules",
                {"polaris.cells.roles.session.internal.role_session_service": None},
            ),
            pytest.raises(SessionAdminError) as exc_info,
        ):
            svc._make_service()
        assert exc_info.value.code == "service_resolution_error"

    # -- create_session ------------------------------------------------------

    def test_create_session_success(self) -> None:
        fake_svc = MagicMock()
        fake_conv = MagicMock()
        fake_conv.id = "new-id"
        fake_conv.role = "pm"
        fake_conv.state = SessionState.ACTIVE.value
        fake_conv.host_kind = "electron"
        fake_conv.session_type = "workbench"
        fake_conv.attachment_mode = "isolated"
        fake_conv.workspace = "/ws"
        fake_conv.title = "t"
        fake_conv.context_config = None
        fake_conv.capability_profile = None
        fake_conv.created_at = None
        fake_conv.updated_at = None
        fake_svc.create_session.return_value = fake_conv

        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService(workspace="/ws")
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            cmd = CreateRoleSessionCommandV1(
                role="pm",
                host_kind="electron",
                workspace="/ws",
                session_type="workbench",
                attachment_mode="isolated",
                title="t",
            )
            summary = svc.create_session(cmd)
        assert summary.session_id == "new-id"
        fake_svc.create_session.assert_called_once_with(
            role="pm",
            host_kind="electron",
            workspace="/ws",
            session_type="workbench",
            attachment_mode="isolated",
            title="t",
            context_config=None,
            capability_profile=None,
        )

    def test_create_session_cell_error(self) -> None:
        fake_svc = MagicMock()
        fake_svc.create_session.side_effect = FakeRoleSessionError("fail", code="create_fail")
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            cmd = CreateRoleSessionCommandV1(
                role="pm",
                host_kind="electron",
                workspace="/ws",
                session_type="workbench",
                attachment_mode="isolated",
            )
            with pytest.raises(SessionAdminError) as exc_info:
                svc.create_session(cmd)
        assert exc_info.value.code == "create_fail"

    def test_create_session_unexpected_error(self) -> None:
        fake_svc = MagicMock()
        fake_svc.create_session.side_effect = RuntimeError("boom")
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            cmd = CreateRoleSessionCommandV1(
                role="pm",
                host_kind="electron",
                workspace="/ws",
                session_type="workbench",
                attachment_mode="isolated",
            )
            with pytest.raises(SessionAdminError) as exc_info:
                svc.create_session(cmd)
        assert exc_info.value.code == "session_create_unexpected"

    # -- get_session ---------------------------------------------------------

    def test_get_session_success(self) -> None:
        fake_svc = MagicMock()
        fake_conv = MagicMock()
        fake_conv.id = "s1"
        fake_conv.role = "pm"
        fake_conv.state = SessionState.ACTIVE.value
        fake_conv.host_kind = "electron"
        fake_conv.session_type = "workbench"
        fake_conv.attachment_mode = "isolated"
        fake_conv.workspace = None
        fake_conv.title = None
        fake_conv.context_config = None
        fake_conv.capability_profile = None
        fake_conv.created_at = None
        fake_conv.updated_at = None
        fake_svc.get_session.return_value = fake_conv

        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            summary = svc.get_session("s1")
        assert summary is not None
        assert summary.session_id == "s1"

    def test_get_session_not_found(self) -> None:
        fake_svc = MagicMock()
        fake_svc.get_session.return_value = None
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            assert svc.get_session("missing") is None

    def test_get_session_invalid_id(self) -> None:
        svc = SessionAdminService()
        with pytest.raises(SessionAdminError) as exc_info:
            svc.get_session("")
        assert exc_info.value.code == "invalid_session_id"
        with pytest.raises(SessionAdminError) as exc_info:
            svc.get_session("   ")
        assert exc_info.value.code == "invalid_session_id"

    def test_get_session_cell_error(self) -> None:
        fake_svc = MagicMock()
        fake_svc.get_session.side_effect = FakeRoleSessionError("fail", code="get_fail")
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with (
            patch.dict(
                "sys.modules",
                {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
            ),
            pytest.raises(SessionAdminError) as exc_info,
        ):
            svc.get_session("s1")
        assert exc_info.value.code == "get_fail"

    # -- list_sessions -------------------------------------------------------

    def test_list_sessions_success(self) -> None:
        fake_svc = MagicMock()
        fake_conv = MagicMock()
        fake_conv.id = "s1"
        fake_conv.role = "pm"
        fake_conv.state = SessionState.ACTIVE.value
        fake_conv.host_kind = "electron"
        fake_conv.session_type = "workbench"
        fake_conv.attachment_mode = "isolated"
        fake_conv.workspace = None
        fake_conv.title = None
        fake_conv.context_config = None
        fake_conv.capability_profile = None
        fake_conv.created_at = None
        fake_conv.updated_at = None
        fake_svc.get_sessions.return_value = [fake_conv]

        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            result = svc.list_sessions(role="pm", limit=10, offset=0)
        assert isinstance(result, SessionListResult)
        assert len(result.items) == 1
        assert result.limit == 10
        assert result.offset == 0
        fake_svc.get_sessions.assert_called_once_with(
            role="pm",
            host_kind=None,
            workspace=None,
            session_type=None,
            state=None,
            limit=10,
            offset=0,
        )

    def test_list_sessions_clamps_limit(self) -> None:
        fake_svc = MagicMock()
        fake_svc.get_sessions.return_value = []
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            low = svc.list_sessions(limit=0)
            high = svc.list_sessions(limit=500)
        assert low.limit == 1
        assert high.limit == 200

    def test_list_sessions_clamps_offset(self) -> None:
        fake_svc = MagicMock()
        fake_svc.get_sessions.return_value = []
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            result = svc.list_sessions(offset=-5)
        assert result.offset == 0

    def test_list_sessions_cell_error(self) -> None:
        fake_svc = MagicMock()
        fake_svc.get_sessions.side_effect = FakeRoleSessionError("fail", code="list_fail")
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with (
            patch.dict(
                "sys.modules",
                {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
            ),
            pytest.raises(SessionAdminError) as exc_info,
        ):
            svc.list_sessions()
        assert exc_info.value.code == "list_fail"

    # -- update_session ------------------------------------------------------

    def test_update_session_success(self) -> None:
        fake_svc = MagicMock()
        fake_conv = MagicMock()
        fake_conv.id = "s1"
        fake_conv.role = "pm"
        fake_conv.state = SessionState.ACTIVE.value
        fake_conv.host_kind = "electron"
        fake_conv.session_type = "workbench"
        fake_conv.attachment_mode = "isolated"
        fake_conv.workspace = None
        fake_conv.title = "new title"
        fake_conv.context_config = None
        fake_conv.capability_profile = None
        fake_conv.created_at = None
        fake_conv.updated_at = None
        fake_svc.update_session.return_value = fake_conv

        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            cmd = UpdateRoleSessionCommandV1(
                session_id="s1",
                title="new title",
            )
            summary = svc.update_session(cmd)
        assert summary is not None
        assert summary.title == "new title"

    def test_update_session_not_found(self) -> None:
        fake_svc = MagicMock()
        fake_svc.update_session.return_value = None
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            cmd = UpdateRoleSessionCommandV1(session_id="missing")
            assert svc.update_session(cmd) is None

    def test_update_session_cell_error(self) -> None:
        fake_svc = MagicMock()
        fake_svc.update_session.side_effect = FakeRoleSessionError("fail", code="upd_fail")
        fake_mod = MagicMock()
        fake_mod.RoleSessionService.return_value = fake_svc

        svc = SessionAdminService()
        with patch.dict(
            "sys.modules",
            {"polaris.cells.roles.session.internal.role_session_service": fake_mod},
        ):
            cmd = UpdateRoleSessionCommandV1(session_id="s1")
            with pytest.raises(SessionAdminError) as exc_info:
                svc.update_session(cmd)
        assert exc_info.value.code == "upd_fail"

    # -- convenience builders ------------------------------------------------

    def test_build_create_command(self) -> None:
        svc = SessionAdminService()
        cmd = svc.build_create_command(role="architect", workspace="/ws", title="t")
        assert isinstance(cmd, CreateRoleSessionCommandV1)
        assert cmd.role == "architect"
        assert cmd.workspace == "/ws"
        assert cmd.title == "t"
        assert cmd.host_kind == "electron_workbench"
        assert cmd.session_type == "workbench"
        assert cmd.attachment_mode == "isolated"

    def test_build_update_command(self) -> None:
        svc = SessionAdminService()
        cmd = svc.build_update_command(session_id="s1", title="t", state="archived")
        assert isinstance(cmd, UpdateRoleSessionCommandV1)
        assert cmd.session_id == "s1"
        assert cmd.title == "t"
        assert cmd.state == "archived"
        assert cmd.context_config is None
        assert cmd.capability_profile is None

    def test_build_update_command_with_mappings(self) -> None:
        svc = SessionAdminService()
        cmd = svc.build_update_command(
            session_id="s1",
            context_config={"k": "v"},
            capability_profile={"c": "d"},
        )
        assert cmd.context_config == {"k": "v"}
        assert cmd.capability_profile == {"c": "d"}


class TestCloseSvc:
    def test_close_called(self) -> None:
        svc = MagicMock()
        _close_svc(svc)
        svc.close.assert_called_once()

    def test_close_absent(self) -> None:
        svc = object()
        _close_svc(svc)  # should not raise

    def test_close_raises(self) -> None:
        svc = MagicMock()
        svc.close.side_effect = RuntimeError("boom")
        _close_svc(svc)  # should not raise
