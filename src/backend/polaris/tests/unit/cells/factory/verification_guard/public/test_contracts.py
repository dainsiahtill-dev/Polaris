"""Unit tests for polaris.cells.factory.verification_guard.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.factory.verification_guard.public.contracts import (
    ExecutionResult,
    VerificationClaim,
    VerificationCompletedEventV1,
    VerificationGuardErrorV1,
    VerificationReport,
    VerificationStatus,
    VerifyCompletionCommandV1,
    VerifyCompletionResultV1,
)


class TestVerificationStatus:
    """Tests for VerificationStatus enum."""

    def test_status_values(self) -> None:
        assert VerificationStatus.PASS.name == "PASS"
        assert VerificationStatus.FAIL.name == "FAIL"
        assert VerificationStatus.BLOCKED.name == "BLOCKED"
        assert VerificationStatus.TIMEOUT.name == "TIMEOUT"
        assert VerificationStatus.ERROR.name == "ERROR"


class TestVerificationClaim:
    """Tests for VerificationClaim dataclass."""

    def test_valid_claim(self) -> None:
        claim = VerificationClaim(
            claim_id="claim-1",
            claimed_outcome="tests pass",
            verification_commands=["pytest", "ruff check ."],
            evidence_paths=["report.xml"],
            timeout_seconds=120,
        )
        assert claim.claim_id == "claim-1"
        assert claim.claimed_outcome == "tests pass"
        assert claim.verification_commands == ("pytest", "ruff check .")
        assert claim.evidence_paths == ("report.xml",)
        assert claim.timeout_seconds == 120
        assert claim.metadata == {}

    def test_empty_claim_id(self) -> None:
        with pytest.raises(ValueError, match="claim_id must be a non-empty string"):
            VerificationClaim(claim_id="", claimed_outcome="tests pass")

    def test_empty_claimed_outcome(self) -> None:
        with pytest.raises(ValueError, match="claimed_outcome must be a non-empty string"):
            VerificationClaim(claim_id="claim-1", claimed_outcome="")

    def test_invalid_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            VerificationClaim(claim_id="claim-1", claimed_outcome="tests pass", timeout_seconds=0)
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            VerificationClaim(claim_id="claim-1", claimed_outcome="tests pass", timeout_seconds=-1)

    def test_default_values(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        assert claim.verification_commands == ()
        assert claim.evidence_paths == ()
        assert claim.timeout_seconds == 60
        assert claim.metadata == {}

    def test_tuple_normalization(self) -> None:
        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="ok",
            verification_commands=["pytest", "", "ruff"],
            evidence_paths=["", "report.xml"],
        )
        assert claim.verification_commands == ("pytest", "ruff")
        assert claim.evidence_paths == ("report.xml",)


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_valid_result(self) -> None:
        result = ExecutionResult(
            command="pytest",
            stdout="passed",
            stderr="",
            return_code=0,
            execution_time_ms=1500,
        )
        assert result.command == "pytest"
        assert result.return_code == 0
        assert result.timed_out is False

    def test_timed_out(self) -> None:
        result = ExecutionResult(
            command="pytest",
            stdout="",
            stderr="timeout",
            return_code=-1,
            execution_time_ms=60000,
            timed_out=True,
        )
        assert result.timed_out is True


class TestVerificationReport:
    """Tests for VerificationReport dataclass."""

    def test_valid_report(self) -> None:
        result = ExecutionResult(
            command="pytest",
            stdout="passed",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        report = VerificationReport(
            claim_id="claim-1",
            status=VerificationStatus.PASS,
            command_results=(result,),
            evidence_collected=("report.xml",),
            execution_summary="All good",
        )
        assert report.claim_id == "claim-1"
        assert report.status == VerificationStatus.PASS
        assert len(report.command_results) == 1
        assert report.execution_summary == "All good"

    def test_empty_claim_id(self) -> None:
        with pytest.raises(ValueError, match="claim_id must be a non-empty string"):
            VerificationReport(claim_id="", status=VerificationStatus.PASS)

    def test_default_values(self) -> None:
        report = VerificationReport(claim_id="c1", status=VerificationStatus.PASS)
        assert report.command_results == ()
        assert report.evidence_collected == ()
        assert report.evidence_missing == ()
        assert report.mismatch_details == ()
        assert report.recommendations == ()
        assert report.execution_summary == ""
        assert report.metadata == {}


class TestVerifyCompletionCommandV1:
    """Tests for VerifyCompletionCommandV1 dataclass."""

    def test_valid_command(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        cmd = VerifyCompletionCommandV1(workspace="/tmp/ws", claim=claim)
        assert cmd.workspace == "/tmp/ws"
        assert cmd.claim is claim
        assert cmd.strict_mode is True
        assert cmd.allowed_commands is None

    def test_empty_workspace(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            VerifyCompletionCommandV1(workspace="", claim=claim)

    def test_invalid_claim_type(self) -> None:
        with pytest.raises(TypeError, match="claim must be a VerificationClaim instance"):
            VerifyCompletionCommandV1(workspace="/tmp/ws", claim="not_a_claim")  # type: ignore[arg-type]

    def test_allowed_commands(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        cmd = VerifyCompletionCommandV1(
            workspace="/tmp/ws",
            claim=claim,
            allowed_commands=["pytest", "ruff"],
        )
        assert cmd.allowed_commands == ("pytest", "ruff")


class TestVerifyCompletionResultV1:
    """Tests for VerifyCompletionResultV1 dataclass."""

    def test_ok_result(self) -> None:
        result = VerifyCompletionResultV1(ok=True)
        assert result.ok is True
        assert result.report is None
        assert result.error_code is None
        assert result.error_message is None

    def test_with_report(self) -> None:
        report = VerificationReport(claim_id="c1", status=VerificationStatus.PASS)
        result = VerifyCompletionResultV1(ok=True, report=report)
        assert result.report is report


class TestVerificationCompletedEventV1:
    """Tests for VerificationCompletedEventV1 dataclass."""

    def test_valid_event(self) -> None:
        event = VerificationCompletedEventV1(
            claim_id="c1",
            status=VerificationStatus.PASS,
            workspace="/tmp/ws",
            verified_at="2024-01-01T00:00:00",
        )
        assert event.claim_id == "c1"
        assert event.status == VerificationStatus.PASS
        assert event.workspace == "/tmp/ws"
        assert event.verified_at == "2024-01-01T00:00:00"


class TestVerificationGuardErrorV1:
    """Tests for VerificationGuardErrorV1 exception."""

    def test_basic_error(self) -> None:
        exc = VerificationGuardErrorV1("something failed")
        assert str(exc) == "something failed"
        assert exc.code == "verification_guard_error"
        assert exc.details == {}

    def test_error_with_code_and_details(self) -> None:
        exc = VerificationGuardErrorV1(
            "command blocked",
            code="command_blocked",
            details={"command": "rm -rf /"},
        )
        assert exc.code == "command_blocked"
        assert exc.details == {"command": "rm -rf /"}

    def test_empty_message(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            VerificationGuardErrorV1("")

    def test_empty_code(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            VerificationGuardErrorV1("msg", code="")
