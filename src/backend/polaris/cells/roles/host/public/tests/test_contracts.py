"""Tests for polaris.cells.roles.host.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.roles.host.public.contracts import (
    HOST_KIND_PROFILES,
    AttachmentMode,
    HostCapabilityProfile,
    HostKind,
    RoleHostKind,
    SessionState,
    SessionType,
    get_capability_profile,
)


class TestHostKindAlias:
    """Tests for HostKind alias."""

    def test_host_kind_is_role_host_kind(self) -> None:
        assert HostKind is RoleHostKind

    def test_host_kind_values(self) -> None:
        assert HostKind.WORKFLOW.value == "workflow"
        assert HostKind.CLI.value == "cli"
        assert HostKind.API_SERVER.value == "api_server"


class TestReExportedEnums:
    """Tests for re-exported enums from roles.session."""

    def test_attachment_mode_values(self) -> None:
        assert AttachmentMode.ISOLATED.value == "isolated"
        assert AttachmentMode.ATTACHED_READONLY.value == "attached_readonly"
        assert AttachmentMode.ATTACHED_COLLABORATIVE.value == "attached_collaborative"

    def test_session_type_values(self) -> None:
        assert SessionType.WORKFLOW_MANAGED.value == "workflow_managed"
        assert SessionType.STANDALONE.value == "standalone"
        assert SessionType.WORKBENCH.value == "workbench"

    def test_session_state_values(self) -> None:
        assert SessionState.ACTIVE.value == "active"
        assert SessionState.PAUSED.value == "paused"
        assert SessionState.COMPLETED.value == "completed"
        assert SessionState.ARCHIVED.value == "archived"

    def test_role_host_kind_values(self) -> None:
        assert RoleHostKind.WORKFLOW.value == "workflow"
        assert RoleHostKind.ELECTRON_WORKBENCH.value == "electron_workbench"
        assert RoleHostKind.TUI.value == "tui"
        assert RoleHostKind.CLI.value == "cli"
        assert RoleHostKind.API_SERVER.value == "api_server"
        assert RoleHostKind.HEADLESS.value == "headless"


class TestHostCapabilityProfile:
    """Tests for HostCapabilityProfile dataclass."""

    def test_create_minimal(self) -> None:
        profile = HostCapabilityProfile(host_kind="custom")
        assert profile.host_kind == "custom"
        assert profile.supports_streaming is True
        assert profile.supports_tool_async is True
        assert profile.supports_file_write is True
        assert profile.max_context_tokens is None
        assert profile.supports_audit_export is True
        assert profile.supports_artifact_persistence is True
        assert profile.enable_session_orchestrator is False
        assert profile.metadata == {}

    def test_create_with_all_fields(self) -> None:
        profile = HostCapabilityProfile(
            host_kind="custom",
            supports_streaming=False,
            supports_tool_async=False,
            supports_file_write=False,
            max_context_tokens=1000,
            supports_audit_export=False,
            supports_artifact_persistence=False,
            enable_session_orchestrator=True,
            metadata={"key": "value"},
        )
        assert profile.host_kind == "custom"
        assert profile.supports_streaming is False
        assert profile.supports_tool_async is False
        assert profile.supports_file_write is False
        assert profile.max_context_tokens == 1000
        assert profile.supports_audit_export is False
        assert profile.supports_artifact_persistence is False
        assert profile.enable_session_orchestrator is True
        assert profile.metadata == {"key": "value"}

    def test_is_frozen(self) -> None:
        profile = HostCapabilityProfile(host_kind="custom")
        with pytest.raises(AttributeError):
            profile.host_kind = "other"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        profile = HostCapabilityProfile(
            host_kind="custom",
            max_context_tokens=1000,
            metadata={"key": "value"},
        )
        data = profile.to_dict()
        assert data["host_kind"] == "custom"
        assert data["supports_streaming"] is True
        assert data["supports_tool_async"] is True
        assert data["supports_file_write"] is True
        assert data["max_context_tokens"] == 1000
        assert data["supports_audit_export"] is True
        assert data["supports_artifact_persistence"] is True
        assert data["enable_session_orchestrator"] is False
        assert data["metadata"] == {"key": "value"}

    def test_to_dict_metadata_is_copied(self) -> None:
        profile = HostCapabilityProfile(host_kind="custom", metadata={"key": "value"})
        data = profile.to_dict()
        data["metadata"]["new"] = "entry"
        assert "new" not in profile.metadata

    def test_to_dict_with_none_tokens(self) -> None:
        profile = HostCapabilityProfile(host_kind="custom")
        data = profile.to_dict()
        assert data["max_context_tokens"] is None

    def test_repr(self) -> None:
        profile = HostCapabilityProfile(host_kind="custom")
        assert "HostCapabilityProfile" in repr(profile)


class TestHostKindProfiles:
    """Tests for HOST_KIND_PROFILES constant."""

    def test_contains_all_host_kinds(self) -> None:
        for kind in RoleHostKind:
            assert kind.value in HOST_KIND_PROFILES

    def test_workflow_profile(self) -> None:
        profile = HOST_KIND_PROFILES[RoleHostKind.WORKFLOW.value]
        assert profile.host_kind == "workflow"
        assert profile.supports_streaming is True
        assert profile.supports_tool_async is True
        assert profile.max_context_tokens is None

    def test_electron_workbench_profile(self) -> None:
        profile = HOST_KIND_PROFILES[RoleHostKind.ELECTRON_WORKBENCH.value]
        assert profile.host_kind == "electron_workbench"
        assert profile.supports_streaming is True
        assert profile.supports_audit_export is True

    def test_tui_profile(self) -> None:
        profile = HOST_KIND_PROFILES[RoleHostKind.TUI.value]
        assert profile.host_kind == "tui"
        assert profile.supports_tool_async is False
        assert profile.max_context_tokens == 128000
        assert profile.supports_audit_export is False

    def test_cli_profile(self) -> None:
        profile = HOST_KIND_PROFILES[RoleHostKind.CLI.value]
        assert profile.host_kind == "cli"
        assert profile.supports_tool_async is False
        assert profile.max_context_tokens == 128000
        assert profile.supports_audit_export is False
        assert profile.supports_artifact_persistence is False
        assert profile.enable_session_orchestrator is True

    def test_api_server_profile(self) -> None:
        profile = HOST_KIND_PROFILES[RoleHostKind.API_SERVER.value]
        assert profile.host_kind == "api_server"
        assert profile.supports_streaming is True
        assert profile.supports_tool_async is True

    def test_headless_profile(self) -> None:
        profile = HOST_KIND_PROFILES[RoleHostKind.HEADLESS.value]
        assert profile.host_kind == "headless"
        assert profile.supports_streaming is False
        assert profile.supports_audit_export is False

    def test_all_profiles_are_host_capability_profile(self) -> None:
        for profile in HOST_KIND_PROFILES.values():
            assert isinstance(profile, HostCapabilityProfile)


class TestGetCapabilityProfile:
    """Tests for get_capability_profile function."""

    def test_get_workflow_profile(self) -> None:
        profile = get_capability_profile("workflow")
        assert profile.host_kind == "workflow"
        assert isinstance(profile, HostCapabilityProfile)

    def test_get_cli_profile(self) -> None:
        profile = get_capability_profile("cli")
        assert profile.host_kind == "cli"
        assert profile.supports_tool_async is False

    def test_get_unknown_host_kind_returns_default(self) -> None:
        profile = get_capability_profile("unknown_kind")
        assert profile.host_kind == "unknown_kind"
        assert profile.supports_streaming is True  # default
        assert profile.supports_tool_async is True  # default

    def test_get_unknown_returns_host_capability_profile(self) -> None:
        profile = get_capability_profile("custom")
        assert isinstance(profile, HostCapabilityProfile)

    def test_get_empty_string_returns_default(self) -> None:
        profile = get_capability_profile("")
        assert profile.host_kind == ""
        assert profile.supports_streaming is True

    def test_get_with_special_characters(self) -> None:
        profile = get_capability_profile("kind-with-dashes_123")
        assert profile.host_kind == "kind-with-dashes_123"

    def test_get_is_consistent_with_host_kind_profiles(self) -> None:
        for kind in RoleHostKind:
            profile = get_capability_profile(kind.value)
            assert profile == HOST_KIND_PROFILES[kind.value]

    def test_get_returns_copy_for_unknown(self) -> None:
        profile1 = get_capability_profile("unknown1")
        profile2 = get_capability_profile("unknown2")
        assert profile1 != profile2


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.roles.host.public import contracts as mod

        assert hasattr(mod, "__all__")
        assert "AttachmentMode" in mod.__all__
        assert "HostCapabilityProfile" in mod.__all__
        assert "HostKind" in mod.__all__
        assert "RoleHostKind" in mod.__all__
        assert "SessionState" in mod.__all__
        assert "SessionType" in mod.__all__
        assert len(mod.__all__) == 6
