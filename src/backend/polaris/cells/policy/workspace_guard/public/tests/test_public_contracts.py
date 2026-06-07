"""Unit tests for `policy/workspace_guard` public contracts."""

from __future__ import annotations

from polaris.cells.policy.workspace_guard.public.contracts import (
    WorkspaceArchiveWriteGuardQueryV1,
    WorkspaceGuardBatchDecisionV1,
    WorkspaceGuardDecisionV1,
    WorkspaceGuardError,
    WorkspaceGuardPathDecisionV1,
    WorkspaceGuardViolationEventV1,
    WorkspaceWriteGuardBatchQueryV1,
    WorkspaceWriteGuardQueryV1,
)
from polaris.cells.policy.workspace_guard.public.service import check_workspace_write_guard_batch


class TestWorkspaceWriteGuardQueryV1HappyPath:
    def test_construction(self) -> None:
        q = WorkspaceWriteGuardQueryV1(path="/repo/src", operation="write")
        assert q.path == "/repo/src"
        assert q.operation == "write"


class TestWorkspaceWriteGuardBatchQueryV1HappyPath:
    def test_construction(self) -> None:
        q = WorkspaceWriteGuardBatchQueryV1(paths=("/repo/src/a.py", "/repo/src/b.py"), operation="write")
        assert q.paths == ("/repo/src/a.py", "/repo/src/b.py")
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


class TestWorkspaceGuardBatchDecisionV1HappyPath:
    def test_allowed(self) -> None:
        path_decision = WorkspaceGuardPathDecisionV1(
            path="/repo/src/a.py",
            operation="write",
            allowed=True,
            reason="workspace target allowed",
        )
        d = WorkspaceGuardBatchDecisionV1(
            allowed=True,
            reason="workspace target allowed",
            checked_paths=("/repo/src/a.py",),
            denied_path="",
            path_decisions=(path_decision,),
        )
        assert d.allowed is True
        assert d.checked_paths == ("/repo/src/a.py",)
        assert d.denied_path == ""
        assert d.path_decisions == (path_decision,)


class TestWorkspaceWriteGuardBatchService:
    def test_allowed_batch_deduplicates_paths(self, tmp_path) -> None:
        first = str(tmp_path / "a.py")
        second = str(tmp_path / "b.py")
        result = check_workspace_write_guard_batch(
            WorkspaceWriteGuardBatchQueryV1(paths=(first, first, second), operation="write")
        )

        assert result.allowed is True
        assert result.checked_paths == (first, second)
        assert result.denied_path == ""
        assert tuple(decision.path for decision in result.path_decisions) == (first, second)


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
