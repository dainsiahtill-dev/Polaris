"""Tests for `polaris.cells.context.engine.public.contracts`."""

from __future__ import annotations

import dataclasses

import pytest
from polaris.cells.context.engine.public.contracts import (
    BuildRoleContextCommandV1,
    ContextEngineError,
    ContextResolvedEventV1,
    ResolveRoleContextQueryV1,
    RoleContextResultV1,
)


class TestBuildRoleContextCommandV1:
    def test_required_fields(self) -> None:
        cmd = BuildRoleContextCommandV1(role_id="pm", objective="Plan the sprint")
        assert cmd.role_id == "pm"
        assert cmd.objective == "Plan the sprint"

    def test_is_frozen(self) -> None:
        cmd = BuildRoleContextCommandV1(role_id="pm", objective="x")
        with pytest.raises(dataclasses.FrozenInstanceError):  # type: ignore[name-defined]
            cmd.role_id = "changed"


class TestResolveRoleContextQueryV1:
    def test_defaults(self) -> None:
        q = ResolveRoleContextQueryV1(role_id="director")
        assert q.role_id == "director"
        assert q.limit == 8

    def test_explicit_limit(self) -> None:
        q = ResolveRoleContextQueryV1(role_id="qa", limit=20)
        assert q.limit == 20


class TestRoleContextResultV1:
    def test_fields(self) -> None:
        r = RoleContextResultV1(
            context_items=("item1", "item2"),
            source_cells=("cell.a", "cell.b"),
        )
        assert r.context_items == ("item1", "item2")
        assert r.source_cells == ("cell.a", "cell.b")


class TestContextResolvedEventV1:
    def test_fields(self) -> None:
        e = ContextResolvedEventV1(role_id="pm", source_cells=("cell.x",))
        assert e.role_id == "pm"
        assert e.source_cells == ("cell.x",)


class TestContextEngineError:
    def test_is_exception(self) -> None:
        err = ContextEngineError("boom")
        with pytest.raises(ContextEngineError):
            raise err

    def test_message_preserved(self) -> None:
        err = ContextEngineError("detail")
        assert str(err) == "detail"


class TestPublicContractsExports:
    """Verify __all__ is exhaustive."""

    def test_all_exports(self) -> None:
        from polaris.cells.context.engine.public import contracts

        for name in contracts.__all__:
            assert hasattr(contracts, name), f"{name} listed in __all__ but not present"
