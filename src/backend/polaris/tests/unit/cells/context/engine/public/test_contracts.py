"""Tests for polaris.cells.context.engine.public.contracts."""

from __future__ import annotations

from polaris.cells.context.engine.public.contracts import (
    BuildRoleContextCommandV1,
    ContextEngineError,
    ContextResolvedEventV1,
    ResolveRoleContextQueryV1,
    RoleContextResultV1,
)


class TestBuildRoleContextCommandV1:
    def test_fields(self) -> None:
        cmd = BuildRoleContextCommandV1(role_id="r1", objective="test")
        assert cmd.role_id == "r1"
        assert cmd.objective == "test"


class TestResolveRoleContextQueryV1:
    def test_default_limit(self) -> None:
        q = ResolveRoleContextQueryV1(role_id="r1")
        assert q.limit == 8

    def test_custom_limit(self) -> None:
        q = ResolveRoleContextQueryV1(role_id="r1", limit=5)
        assert q.limit == 5


class TestRoleContextResultV1:
    def test_fields(self) -> None:
        r = RoleContextResultV1(context_items=("a", "b"), source_cells=("c1",))
        assert r.context_items == ("a", "b")
        assert r.source_cells == ("c1",)


class TestContextResolvedEventV1:
    def test_fields(self) -> None:
        ev = ContextResolvedEventV1(role_id="r1", source_cells=("c1",))
        assert ev.role_id == "r1"
        assert ev.source_cells == ("c1",)


class TestContextEngineError:
    def test_is_exception(self) -> None:
        assert issubclass(ContextEngineError, Exception)
