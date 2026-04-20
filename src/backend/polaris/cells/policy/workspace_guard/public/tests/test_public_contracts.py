"""Unit tests for `policy/workspace_guard` public contracts."""

from __future__ import annotations

from polaris.cells.policy.workspace_guard.public.contracts import (
    WorkspaceArchiveWriteGuardQueryV1,
    WorkspaceGuardDecisionV1,
    WorkspaceGuardError,
    WorkspaceGuardViolationEventV1,
    WorkspaceWriteGuardQueryV1,
)


class TestWorkspaceWriteGuardQueryV1HappyPath:
    def test_construction(self) -> None:
        q = WorkspaceWriteGuardQueryV1(path="/repo/src", operation="write")
        assert q.path == "/repo/src"
        assert q.operation == "write"


class TestWorkspaceArchiveWriteGuardQueryV1HappyPath:
    def test_construction(self) -> None:
        q = WorkspaceArchiveWriteGuardQueryV1(path="/archive", operation="write")
        assert q.path == "/archive"
        assert q.operation == "write"


class TestWorkspaceGuardDecisionV1HappyPath:
    def test_allowed(self) -> None:
        d = WorkspaceGuardDecisionV1(allowed=True, reason="whitelisted path")
        assert d.allowed is True
        assert d.reason == "whitelisted path"

    def test_denied(self) -> None:
        d = WorkspaceGuardDecisionV1(allowed=False, reason="protected path")
        assert d.allowed is False


class TestWorkspaceGuardViolationEventV1HappyPath:
    def test_construction(self) -> None:
        evt = WorkspaceGuardViolationEventV1(
            path="/repo/.polaris",
            operation="write",
            reason="protected directory",
        )
        assert evt.path == "/repo/.polaris"
        assert evt.operation == "write"
        assert evt.reason == "protected directory"


class TestWorkspaceGuardError:
    def test_raise_and_catch(self) -> None:
        err = WorkspaceGuardError("guard check failed")
        assert str(err) == "guard check failed"
        assert isinstance(err, Exception)
