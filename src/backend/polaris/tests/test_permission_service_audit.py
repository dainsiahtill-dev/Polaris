"""Tests for PermissionService audit integration (P0-09).

Covers:
  - ALLOW decision emits one audit record with correct fields
  - DENY decision emits one audit record with correct fields
  - Default-deny (no matching policy) emits one DENY audit record
  - Audit sink exception does NOT affect permission decision result
  - Audit sink absence (None) does not break permission check
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# Ensure project root is on sys.path when run standalone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from polaris.cells.policy.permission.internal.permission_service import (
    DecisionContext,
    PermissionService,
)
from polaris.cells.roles.profile.public.service import (
    Action,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)

# ---------------------------------------------------------------------------
# Stub audit sink
# ---------------------------------------------------------------------------


class _CapturingAuditSink:
    """In-memory stub that records every record_decision() call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record_decision(
        self,
        *,
        subject: str,
        action: str,
        resource: str,
        result: str,
        reason: str,
        timestamp: str,
        request_id: str,
    ) -> None:
        self.calls.append(
            {
                "subject": subject,
                "action": action,
                "resource": resource,
                "result": result,
                "reason": reason,
                "timestamp": timestamp,
                "request_id": request_id,
            }
        )


class _FailingAuditSink:
    """Stub that always raises on record_decision() to test fire-and-forget."""

    def record_decision(self, **_kwargs: Any) -> None:
        raise RuntimeError("audit backend unavailable")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def service_with_audit() -> tuple[PermissionService, _CapturingAuditSink]:
    """Initialized service with a capturing audit sink."""
    sink = _CapturingAuditSink()
    svc = PermissionService(workspace="", audit_sink=sink)
    await svc.initialize()
    return svc, sink


@pytest_asyncio.fixture
async def service_without_audit() -> PermissionService:
    """Initialized service with no audit sink (default None)."""
    svc = PermissionService(workspace="")
    await svc.initialize()
    return svc


@pytest_asyncio.fixture
async def service_with_failing_audit() -> tuple[PermissionService, _FailingAuditSink]:
    """Initialized service whose audit sink always raises."""
    sink = _FailingAuditSink()
    svc = PermissionService(workspace="", audit_sink=sink)
    await svc.initialize()
    return svc, sink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pm_read_file_subject_resource_action():
    return (
        Subject(type=SubjectType.ROLE, id="pm"),
        Resource(type=ResourceType.FILE, pattern="**/*"),
        Action.READ,
    )


def _pm_write_file_subject_resource_action():
    """PM does NOT have a write policy → default DENY."""
    return (
        Subject(type=SubjectType.ROLE, id="pm"),
        Resource(type=ResourceType.FILE, pattern="some/file.py"),
        Action.WRITE,
    )


def _director_env_subject_resource_action():
    """Director tries to read a .env file → explicit DENY via director-deny-sensitive."""
    return (
        Subject(type=SubjectType.ROLE, id="director"),
        Resource(type=ResourceType.FILE, pattern="**/.env", path=".env"),
        Action.READ,
    )


# ---------------------------------------------------------------------------
# Tests: ALLOW decision produces audit record
# ---------------------------------------------------------------------------


class TestAuditOnAllow:
    @pytest.mark.asyncio
    async def test_allow_emits_exactly_one_record(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is True, "Pre-condition: PM read should be ALLOWED"
        assert len(sink.calls) == 1, "Exactly one audit record expected"

    @pytest.mark.asyncio
    async def test_allow_record_has_correct_result_field(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        rec = sink.calls[0]
        assert rec["result"] == "ALLOW"

    @pytest.mark.asyncio
    async def test_allow_record_has_correct_subject(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        rec = sink.calls[0]
        assert rec["subject"] == "role:pm"

    @pytest.mark.asyncio
    async def test_allow_record_has_correct_action(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        rec = sink.calls[0]
        assert rec["action"] == "read"

    @pytest.mark.asyncio
    async def test_allow_record_has_nonempty_timestamp(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        rec = sink.calls[0]
        # Must be non-empty ISO-8601 string
        assert rec["timestamp"] and "T" in rec["timestamp"]

    @pytest.mark.asyncio
    async def test_allow_record_has_nonempty_request_id(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        rec = sink.calls[0]
        assert rec["request_id"]

    @pytest.mark.asyncio
    async def test_allow_record_uses_provided_request_id(self, service_with_audit):
        """When DecisionContext carries a request_id, it must be forwarded verbatim."""
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()
        ctx = DecisionContext(request_id="test-req-abc123")

        await svc.check_permission(subject, resource, action, context=ctx)

        rec = sink.calls[0]
        assert rec["request_id"] == "test-req-abc123"

    @pytest.mark.asyncio
    async def test_allow_record_has_nonempty_reason(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        rec = sink.calls[0]
        assert rec["reason"]


# ---------------------------------------------------------------------------
# Tests: DENY decision produces audit record
# ---------------------------------------------------------------------------


class TestAuditOnDeny:
    @pytest.mark.asyncio
    async def test_explicit_deny_emits_exactly_one_record(self, service_with_audit):
        """Explicit DENY policy (director-deny-sensitive) must produce an audit record."""
        svc, sink = service_with_audit
        subject, resource, action = _director_env_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is False, "Pre-condition: director reading .env should be DENIED"
        assert len(sink.calls) == 1

    @pytest.mark.asyncio
    async def test_explicit_deny_record_has_correct_result(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _director_env_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        assert sink.calls[0]["result"] == "DENY"

    @pytest.mark.asyncio
    async def test_default_deny_emits_exactly_one_record(self, service_with_audit):
        """No matching policy → default deny must also produce an audit record."""
        svc, sink = service_with_audit
        subject, resource, action = _pm_write_file_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is False
        assert len(sink.calls) == 1

    @pytest.mark.asyncio
    async def test_default_deny_record_has_correct_result(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _pm_write_file_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        assert sink.calls[0]["result"] == "DENY"

    @pytest.mark.asyncio
    async def test_deny_record_has_nonempty_reason(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _director_env_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        assert sink.calls[0]["reason"]

    @pytest.mark.asyncio
    async def test_deny_record_has_correct_subject(self, service_with_audit):
        svc, sink = service_with_audit
        subject, resource, action = _director_env_subject_resource_action()

        await svc.check_permission(subject, resource, action)

        assert sink.calls[0]["subject"] == "role:director"


# ---------------------------------------------------------------------------
# Tests: audit failure is observable but non-fatal
# ---------------------------------------------------------------------------


class TestAuditFailureIsolation:
    @pytest.mark.asyncio
    async def test_failing_audit_does_not_raise(self, service_with_failing_audit):
        """A crashing audit sink must not propagate the exception."""
        svc, _ = service_with_failing_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        # Must NOT raise despite audit sink raising RuntimeError
        result = await svc.check_permission(subject, resource, action)
        assert result is not None

    @pytest.mark.asyncio
    async def test_failing_audit_preserves_allow_result(self, service_with_failing_audit):
        svc, _ = service_with_failing_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_failing_audit_preserves_deny_result(self, service_with_failing_audit):
        svc, _ = service_with_failing_audit
        subject, resource, action = _director_env_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_failing_audit_logs_error(self, service_with_failing_audit, caplog):
        """An audit failure must be logged at ERROR level (observable)."""
        import logging

        svc, _ = service_with_failing_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        with caplog.at_level(logging.ERROR, logger="polaris.cells.policy.permission.internal.permission_service"):
            await svc.check_permission(subject, resource, action)

        error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("Audit write failed" in msg for msg in error_messages), (
            f"Expected 'Audit write failed' in error logs, got: {error_messages}"
        )


# ---------------------------------------------------------------------------
# Tests: no audit sink (None) - backward compatibility
# ---------------------------------------------------------------------------


class TestNoAuditSink:
    @pytest.mark.asyncio
    async def test_no_sink_allow_still_works(self, service_without_audit):
        svc = service_without_audit
        subject, resource, action = _pm_read_file_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_no_sink_deny_still_works(self, service_without_audit):
        svc = service_without_audit
        subject, resource, action = _director_env_subject_resource_action()

        result = await svc.check_permission(subject, resource, action)

        assert result.allowed is False
