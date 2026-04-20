"""Unit tests for `policy/permission` public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.policy.permission.public.contracts import (
    EvaluatePermissionCommandV1,
    PermissionDecisionResultV1,
    PermissionDeniedEventV1,
    PermissionPolicyError,
    QueryPermissionMatrixV1,
)


class TestEvaluatePermissionCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = EvaluatePermissionCommandV1(
            role="director",
            action="write",
            resource="/repo/src",
            workspace="/repo",
        )
        assert cmd.role == "director"
        assert cmd.action == "write"
        assert cmd.resource == "/repo/src"
        assert cmd.workspace == "/repo"
        assert cmd.context == {}

    def test_with_context(self) -> None:
        cmd = EvaluatePermissionCommandV1(
            role="director",
            action="write",
            resource="/repo/src",
            workspace="/repo",
            context={"user": "alice"},
        )
        assert cmd.context == {"user": "alice"}

    def test_context_is_copied(self) -> None:
        original = {"user": "alice"}
        cmd = EvaluatePermissionCommandV1(
            role="director",
            action="write",
            resource="/repo/src",
            workspace="/repo",
            context=original,
        )
        original.clear()
        assert cmd.context == {"user": "alice"}


class TestEvaluatePermissionCommandV1EdgeCases:
    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            EvaluatePermissionCommandV1(role="", action="write", resource="/r", workspace="/repo")

    def test_whitespace_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            EvaluatePermissionCommandV1(role="  ", action="write", resource="/r", workspace="/repo")

    def test_empty_action_raises(self) -> None:
        with pytest.raises(ValueError, match="action"):
            EvaluatePermissionCommandV1(role="director", action="", resource="/r", workspace="/repo")

    def test_empty_resource_raises(self) -> None:
        with pytest.raises(ValueError, match="resource"):
            EvaluatePermissionCommandV1(role="director", action="write", resource="", workspace="/repo")


class TestQueryPermissionMatrixV1HappyPath:
    def test_defaults(self) -> None:
        q = QueryPermissionMatrixV1(role="director", workspace="/repo")
        assert q.role == "director"
        assert q.include_inherited is True

    def test_explicit_include_inherited(self) -> None:
        q = QueryPermissionMatrixV1(role="director", workspace="/repo", include_inherited=False)
        assert q.include_inherited is False


class TestQueryPermissionMatrixV1EdgeCases:
    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            QueryPermissionMatrixV1(role="", workspace="/repo")


class TestPermissionDeniedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = PermissionDeniedEventV1(
            event_id="evt-1",
            role="director",
            action="write",
            resource="/repo/secret",
            reason="read-only workspace",
            occurred_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.reason == "read-only workspace"


class TestPermissionDeniedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            PermissionDeniedEventV1(
                event_id="",
                role="director",
                action="write",
                resource="/r",
                reason="denied",
                occurred_at="2026-03-24T10:00:00Z",
            )

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            PermissionDeniedEventV1(
                event_id="e1",
                role="director",
                action="write",
                resource="/r",
                reason="",
                occurred_at="2026-03-24T10:00:00Z",
            )


class TestPermissionDecisionResultV1HappyPath:
    def test_allowed(self) -> None:
        res = PermissionDecisionResultV1(
            allowed=True,
            role="director",
            action="write",
            resource="/repo/src",
            reason="workspace allows director writes",
            matched_policy="workspace.allow",
        )
        assert res.allowed is True
        assert res.matched_policy == "workspace.allow"

    def test_denied(self) -> None:
        res = PermissionDecisionResultV1(
            allowed=False,
            role="director",
            action="delete",
            resource="/repo/secret",
            reason="protected path",
        )
        assert res.allowed is False
        assert res.reason == "protected path"


class TestPermissionDecisionResultV1EdgeCases:
    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            PermissionDecisionResultV1(allowed=True, role="", action="x", resource="/r")


class TestPermissionPolicyError:
    def test_default_values(self) -> None:
        err = PermissionPolicyError("policy evaluation failed")
        assert str(err) == "policy evaluation failed"
        assert err.code == "permission_policy_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = PermissionPolicyError(
            "denied",
            code="access_denied",
            details={"role": "director", "resource": "/secret"},
        )
        assert err.code == "access_denied"
        assert err.details == {"role": "director", "resource": "/secret"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            PermissionPolicyError("")
