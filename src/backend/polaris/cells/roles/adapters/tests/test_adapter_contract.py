"""Minimum test suite for `roles.adapters` public contracts and service.

Tests cover:
- CreateRoleAdapterCommandV1: construction, empty-string guards
- ListSupportedRoleAdaptersQueryV1: optional workspace
- RoleAdapterResultV1: ok/error invariant
- RoleAdaptersError: structured exception
- IRoleAdaptersService / IRoleAdapter: Protocol structural checks
- create_role_adapter: role resolution, unknown-role error
- get_supported_roles: returns non-empty list
- Schema exports: ROLE_OUTPUT_SCHEMAS, get_schema_for_role, BaseToolEnabledOutput
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.adapters.internal.schemas import (
    ROLE_OUTPUT_SCHEMAS,
    BaseToolEnabledOutput,
    get_schema_for_role,
)
from polaris.cells.roles.adapters.public.contracts import (
    CreateRoleAdapterCommandV1,
    IRoleAdapter,
    IRoleAdaptersService,
    ListSupportedRoleAdaptersQueryV1,
    RoleAdapterRegisteredEventV1,
    RoleAdapterResultV1,
    RoleAdaptersError,
)
from polaris.cells.roles.adapters.public.service import (
    create_role_adapter,
    get_supported_roles,
)

# ---------------------------------------------------------------------------
# Happy path: command / query construction
# ---------------------------------------------------------------------------


class TestCreateRoleAdapterCommandV1HappyPath:
    """Command validates role_id and workspace."""

    def test_basic_construction(self) -> None:
        cmd = CreateRoleAdapterCommandV1(role_id="pm", workspace="/ws")
        assert cmd.role_id == "pm"
        assert cmd.workspace == "/ws"

    def test_context_defaulted_to_empty(self) -> None:
        cmd = CreateRoleAdapterCommandV1(role_id="architect", workspace="/ws")
        assert cmd.context == {}

    def test_context_is_copied(self) -> None:
        ctx = {"task_id": "t-1"}
        cmd = CreateRoleAdapterCommandV1(role_id="qa", workspace="/ws", context=ctx)
        ctx["extra"] = "injected"
        assert "extra" not in cmd.context


class TestListSupportedRoleAdaptersQueryV1:
    """Query accepts optional workspace."""

    def test_workspace_optional(self) -> None:
        q = ListSupportedRoleAdaptersQueryV1()
        assert q.workspace is None

    def test_workspace_provided(self) -> None:
        q = ListSupportedRoleAdaptersQueryV1(workspace="/ws")
        assert q.workspace == "/ws"


# ---------------------------------------------------------------------------
# Edge cases: empty-string guard
# ---------------------------------------------------------------------------


class TestCreateRoleAdapterCommandV1EdgeCases:
    """Required fields reject empty / whitespace values."""

    def test_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            CreateRoleAdapterCommandV1(role_id="", workspace="/ws")

    def test_whitespace_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            CreateRoleAdapterCommandV1(role_id="   ", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CreateRoleAdapterCommandV1(role_id="pm", workspace="")


class TestListSupportedRoleAdaptersQueryV1EdgeCases:
    """Workspace field trims whitespace and rejects empty string."""

    def test_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ListSupportedRoleAdaptersQueryV1(workspace="   ")


# ---------------------------------------------------------------------------
# RoleAdapterResultV1 invariant
# ---------------------------------------------------------------------------


class TestRoleAdapterResultV1:
    """ok/error invariant."""

    def test_success_result_no_error_required(self) -> None:
        result = RoleAdapterResultV1(ok=True, role_id="pm", adapter_type="pm")
        assert result.ok is True

    def test_failure_result_requires_error(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            RoleAdapterResultV1(ok=False, role_id="architect", adapter_type="architect")

    def test_failure_with_error_code_valid(self) -> None:
        result = RoleAdapterResultV1(
            ok=False, role_id="director", adapter_type="director", error_code="ADAPTER_INIT_FAILED"
        )
        assert result.error_code == "ADAPTER_INIT_FAILED"


# ---------------------------------------------------------------------------
# RoleAdapterRegisteredEventV1
# ---------------------------------------------------------------------------


class TestRoleAdapterRegisteredEventV1:
    """Event enforces non-empty required fields."""

    def test_valid_construction(self) -> None:
        ev = RoleAdapterRegisteredEventV1(
            event_id="e-1", role_id="qa", adapter_type="qa", registered_at="2026-01-01T00:00:00Z"
        )
        assert ev.role_id == "qa"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            RoleAdapterRegisteredEventV1(
                event_id="", role_id="pm", adapter_type="pm", registered_at="2026-01-01T00:00:00Z"
            )


# ---------------------------------------------------------------------------
# RoleAdaptersError
# ---------------------------------------------------------------------------


class TestRoleAdaptersError:
    """Structured exception."""

    def test_default_code(self) -> None:
        err = RoleAdaptersError("init failed")
        assert err.code == "roles_adapters_error"

    def test_custom_code_and_details(self) -> None:
        err = RoleAdaptersError("role not found", code="UNKNOWN_ROLE", details={"role_id": "unknown_role"})
        assert err.code == "UNKNOWN_ROLE"
        assert err.details == {"role_id": "unknown_role"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            RoleAdaptersError("")


# ---------------------------------------------------------------------------
# Protocol structural checks
# ---------------------------------------------------------------------------


class TestIRoleAdaptersServiceProtocol:
    """IRoleAdaptersService is a runtime_checkable Protocol."""

    def test_incomplete_impl_rejected(self) -> None:
        class Bad:
            pass

        assert not isinstance(Bad(), IRoleAdaptersService)

    def test_complete_impl_accepted(self) -> None:
        class Good:
            def create_adapter(self, command) -> None:
                return None

            def list_supported_roles(self, query):
                return ()

        assert isinstance(Good(), IRoleAdaptersService)


class TestIRoleAdapterProtocol:
    """IRoleAdapter is a runtime_checkable Protocol."""

    def test_incomplete_impl_rejected(self) -> None:
        class Bad:
            pass

        assert not isinstance(Bad(), IRoleAdapter)

    def test_complete_impl_accepted(self) -> None:
        class Good:
            @property
            def role_id(self) -> str:
                return "pm"

            def get_capabilities(self) -> list[str]:
                return []

            async def execute(self, task_id, input_data, context):
                return {}

        assert isinstance(Good(), IRoleAdapter)


# ---------------------------------------------------------------------------
# create_role_adapter: service-level factory
# ---------------------------------------------------------------------------


class TestCreateRoleAdapterFactory:
    """Factory resolves role_id to adapter class."""

    def test_pm_known_role(self) -> None:
        adapter = create_role_adapter("pm", "/ws")
        assert adapter is not None

    def test_role_id_case_normalized(self) -> None:
        # Role IDs are lowercased in factory
        adapter = create_role_adapter("Architect", "/ws")
        assert adapter is not None

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            create_role_adapter("nonexistent_role", "/ws")

    def test_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            create_role_adapter("", "/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            create_role_adapter("pm", "")

    def test_whitespace_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            create_role_adapter("   ", "/ws")


# ---------------------------------------------------------------------------
# get_supported_roles: returns registered adapters
# ---------------------------------------------------------------------------


class TestGetSupportedRoles:
    """Returns non-empty list of registered role IDs."""

    def test_returns_non_empty_list(self) -> None:
        roles = get_supported_roles()
        assert isinstance(roles, list)
        assert len(roles) > 0

    def test_pm_in_supported_roles(self) -> None:
        assert "pm" in get_supported_roles()

    def test_returns_lowercase(self) -> None:
        roles = get_supported_roles()
        assert all(r == r.lower() for r in roles)


# ---------------------------------------------------------------------------
# Schema exports (value-level tests, no import of heavy adapters)
# ---------------------------------------------------------------------------


class TestSchemaExports:
    """Schema singletons are present and well-formed."""

    def test_role_output_schemas_is_dict(self) -> None:
        assert isinstance(ROLE_OUTPUT_SCHEMAS, dict)

    def test_pm_schema_exists(self) -> None:
        assert "pm" in ROLE_OUTPUT_SCHEMAS

    def test_get_schema_for_role_returns_schema(self) -> None:
        schema = get_schema_for_role("pm")
        assert schema is not None

    def test_get_schema_for_unknown_role_returns_none(self) -> None:
        schema = get_schema_for_role("nonexistent")
        assert schema is None

    def test_base_tool_enabled_output_is_base_class(self) -> None:
        # Verify the class exists and has expected attributes
        assert hasattr(BaseToolEnabledOutput, "model_fields")

    def test_schema_for_role_is_pydantic_model(self) -> None:
        from pydantic import BaseModel

        schema = get_schema_for_role("pm")
        assert schema is not None
        assert issubclass(schema, BaseModel)  # type: ignore[arg-type]
