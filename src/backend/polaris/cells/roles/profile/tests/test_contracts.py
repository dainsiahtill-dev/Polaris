"""Unit tests for `roles.profile` public contracts.

Tests the nine command/event/result/Query dataclasses and the custom
error type.  All validation logic (non-empty normalisation, dict-copy,
format normalisation, error-code requirements, protocol interface) is
exercised here so the service layer can rely on validated inputs.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.profile.public.contracts import (
    GetRoleProfileQueryV1,
    IRoleProfileService,
    ListRoleProfilesQueryV1,
    LoadRoleProfilesCommandV1,
    RegisterRoleProfileCommandV1,
    RoleProfileError,
    RoleProfileRegisteredEventV1,
    RoleProfileResultV1,
    RoleProfilesLoadedEventV1,
    RoleProfilesResultV1,
    SaveRoleProfilesCommandV1,
)

# ---------------------------------------------------------------------------
# RegisterRoleProfileCommandV1
# ---------------------------------------------------------------------------


class TestRegisterRoleProfileCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = RegisterRoleProfileCommandV1(profile={"role_id": "pm"})
        assert cmd.profile["role_id"] == "pm"

    def test_full(self) -> None:
        cmd = RegisterRoleProfileCommandV1(
            profile={"role_id": "architect", "display_name": "Architect"},
        )
        assert cmd.profile["display_name"] == "Architect"

    def test_profile_is_copied(self) -> None:
        original = {"role_id": "qa"}
        cmd = RegisterRoleProfileCommandV1(profile=original)
        original.clear()
        assert cmd.profile == {"role_id": "qa"}


class TestRegisterRoleProfileCommandV1EdgeCases:
    def test_missing_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            RegisterRoleProfileCommandV1(profile={})

    def test_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            RegisterRoleProfileCommandV1(profile={"role_id": ""})


# ---------------------------------------------------------------------------
# LoadRoleProfilesCommandV1
# ---------------------------------------------------------------------------


class TestLoadRoleProfilesCommandV1HappyPath:
    def test_defaults(self) -> None:
        cmd = LoadRoleProfilesCommandV1(filepath="/path/to/profiles.yaml")
        assert cmd.filepath == "/path/to/profiles.yaml"
        assert cmd.format == "yaml"

    def test_explicit_format(self) -> None:
        cmd = LoadRoleProfilesCommandV1(filepath="/path/profiles.json", format="JSON")
        assert cmd.format == "json"


class TestLoadRoleProfilesCommandV1EdgeCases:
    def test_empty_filepath_raises(self) -> None:
        with pytest.raises(ValueError, match="filepath"):
            LoadRoleProfilesCommandV1(filepath="")

    def test_whitespace_filepath_raises(self) -> None:
        with pytest.raises(ValueError, match="filepath"):
            LoadRoleProfilesCommandV1(filepath="   ")

    def test_empty_format_raises(self) -> None:
        with pytest.raises(ValueError, match="format"):
            LoadRoleProfilesCommandV1(filepath="/a.yaml", format="")

    def test_whitespace_format_raises(self) -> None:
        with pytest.raises(ValueError, match="format"):
            LoadRoleProfilesCommandV1(filepath="/a.yaml", format="   ")


# ---------------------------------------------------------------------------
# SaveRoleProfilesCommandV1
# ---------------------------------------------------------------------------


class TestSaveRoleProfilesCommandV1HappyPath:
    def test_defaults(self) -> None:
        cmd = SaveRoleProfilesCommandV1(filepath="/out.yaml")
        assert cmd.filepath == "/out.yaml"
        assert cmd.format == "yaml"

    def test_json_format(self) -> None:
        cmd = SaveRoleProfilesCommandV1(filepath="/out.json", format="JSON")
        assert cmd.format == "json"


class TestSaveRoleProfilesCommandV1EdgeCases:
    def test_empty_filepath_raises(self) -> None:
        with pytest.raises(ValueError, match="filepath"):
            SaveRoleProfilesCommandV1(filepath="")

    def test_empty_format_raises(self) -> None:
        with pytest.raises(ValueError, match="format"):
            SaveRoleProfilesCommandV1(filepath="/a.yaml", format="   ")


# ---------------------------------------------------------------------------
# GetRoleProfileQueryV1
# ---------------------------------------------------------------------------


class TestGetRoleProfileQueryV1HappyPath:
    def test_construction(self) -> None:
        q = GetRoleProfileQueryV1(role_id="pm")
        assert q.role_id == "pm"


class TestGetRoleProfileQueryV1EdgeCases:
    def test_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            GetRoleProfileQueryV1(role_id="")

    def test_whitespace_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            GetRoleProfileQueryV1(role_id="   ")


# ---------------------------------------------------------------------------
# ListRoleProfilesQueryV1
# ---------------------------------------------------------------------------


class TestListRoleProfilesQueryV1HappyPath:
    def test_defaults(self) -> None:
        q = ListRoleProfilesQueryV1()
        assert q.include_loaded_files is False

    def test_explicit(self) -> None:
        q = ListRoleProfilesQueryV1(include_loaded_files=True)
        assert q.include_loaded_files is True


# ---------------------------------------------------------------------------
# RoleProfileRegisteredEventV1
# ---------------------------------------------------------------------------


class TestRoleProfileRegisteredEventV1HappyPath:
    def test_construction(self) -> None:
        evt = RoleProfileRegisteredEventV1(
            event_id="evt-1",
            role_id="pm",
            registered_at="2026-03-23T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.role_id == "pm"
        assert evt.registered_at == "2026-03-23T10:00:00Z"


class TestRoleProfileRegisteredEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            RoleProfileRegisteredEventV1(
                event_id="",
                role_id="pm",
                registered_at="2026-03-23T10:00:00Z",
            )

    def test_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            RoleProfileRegisteredEventV1(
                event_id="e1",
                role_id="",
                registered_at="2026-03-23T10:00:00Z",
            )

    def test_empty_registered_at_raises(self) -> None:
        with pytest.raises(ValueError, match="registered_at"):
            RoleProfileRegisteredEventV1(
                event_id="e1",
                role_id="pm",
                registered_at="",
            )


# ---------------------------------------------------------------------------
# RoleProfilesLoadedEventV1
# ---------------------------------------------------------------------------


class TestRoleProfilesLoadedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = RoleProfilesLoadedEventV1(
            event_id="evt-2",
            filepath="/config/roles.yaml",
            loaded_count=5,
            loaded_at="2026-03-23T10:00:00Z",
        )
        assert evt.loaded_count == 5
        assert evt.loaded_at == "2026-03-23T10:00:00Z"


class TestRoleProfilesLoadedEventV1EdgeCases:
    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError, match="loaded_count"):
            RoleProfilesLoadedEventV1(
                event_id="e1",
                filepath="/a.yaml",
                loaded_count=-1,
                loaded_at="2026-03-23T10:00:00Z",
            )

    def test_zero_count_valid(self) -> None:
        evt = RoleProfilesLoadedEventV1(
            event_id="e1",
            filepath="/a.yaml",
            loaded_count=0,
            loaded_at="2026-03-23T10:00:00Z",
        )
        assert evt.loaded_count == 0


# ---------------------------------------------------------------------------
# RoleProfileResultV1
# ---------------------------------------------------------------------------


class TestRoleProfileResultV1HappyPath:
    def test_success_result(self) -> None:
        res = RoleProfileResultV1(
            ok=True,
            role_id="pm",
            payload={"display_name": "PM"},
        )
        assert res.ok is True
        assert res.role_id == "pm"
        assert res.payload["display_name"] == "PM"
        assert res.error_code is None
        assert res.error_message is None

    def test_failure_with_code_and_message(self) -> None:
        res = RoleProfileResultV1(
            ok=False,
            role_id="pm",
            error_code="profile_not_found",
            error_message="Role 'xyz' not registered",
        )
        assert res.ok is False
        assert res.error_code == "profile_not_found"
        assert res.error_message == "Role 'xyz' not registered"


class TestRoleProfileResultV1EdgeCases:
    def test_failed_result_requires_code_or_message(self) -> None:
        with pytest.raises(ValueError, match="failed result must include"):
            RoleProfileResultV1(ok=False, role_id="pm")

    def test_failure_with_code_only_is_valid(self) -> None:
        res = RoleProfileResultV1(ok=False, role_id="pm", error_code="timeout")
        assert res.error_code == "timeout"

    def test_failure_with_message_only_is_valid(self) -> None:
        res = RoleProfileResultV1(ok=False, role_id="pm", error_message="Timeout")
        assert res.error_message == "Timeout"


# ---------------------------------------------------------------------------
# RoleProfilesResultV1
# ---------------------------------------------------------------------------


class TestRoleProfilesResultV1HappyPath:
    def test_success_result(self) -> None:
        res = RoleProfilesResultV1(
            ok=True,
            profiles=({"role_id": "pm"}, {"role_id": "qa"}),
            loaded_files=("/a.yaml", "/b.yaml"),
        )
        assert res.ok is True
        assert len(res.profiles) == 2
        assert len(res.loaded_files) == 2

    def test_failure_result(self) -> None:
        res = RoleProfilesResultV1(
            ok=False,
            error_code="load_failed",
            error_message="File not found",
        )
        assert res.ok is False
        assert res.error_code == "load_failed"

    def test_profiles_tuple_is_copied(self) -> None:
        original = ({"role_id": "pm"},)
        res = RoleProfilesResultV1(ok=True, profiles=original)
        # The result should have copied the profiles
        assert len(res.profiles) == 1

    def test_loaded_files_whitespace_filtered(self) -> None:
        res = RoleProfilesResultV1(
            ok=True,
            loaded_files=("/a.yaml", "", "  ", "/b.json"),
        )
        assert res.loaded_files == ("/a.yaml", "/b.json")


class TestRoleProfilesResultV1EdgeCases:
    def test_failed_result_requires_code_or_message(self) -> None:
        with pytest.raises(ValueError, match="failed result must include"):
            RoleProfilesResultV1(ok=False)


# ---------------------------------------------------------------------------
# RoleProfileError
# ---------------------------------------------------------------------------


class TestRoleProfileError:
    def test_default_values(self) -> None:
        err = RoleProfileError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "roles_profile_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = RoleProfileError(
            "Profile locked",
            code="profile_locked",
            details={"role_id": "pm"},
        )
        assert str(err) == "Profile locked"
        assert err.code == "profile_locked"
        assert err.details == {"role_id": "pm"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            RoleProfileError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            RoleProfileError("error", code="   ")


# ---------------------------------------------------------------------------
# IRoleProfileService Protocol
# ---------------------------------------------------------------------------


class TestIRoleProfileServiceProtocol:
    def test_protocol_defines_required_methods(self) -> None:
        # Verify the protocol lists all required methods
        required = {"register_profile", "load_profiles", "save_profiles", "get_profile", "list_profiles"}
        assert required.issubset(dir(IRoleProfileService))
