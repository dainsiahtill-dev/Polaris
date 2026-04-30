"""Tests for polaris.cells.factory.verification_guard.public.contracts.

Covers enum semantics, dataclass validation, frozen behavior, and error types.
All tests are pure — no I/O required.
"""

from __future__ import annotations

from typing import Any

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

    def test_members(self) -> None:
        assert VerificationStatus.PASS.name == "PASS"
        assert VerificationStatus.FAIL.name == "FAIL"
        assert VerificationStatus.BLOCKED.name == "BLOCKED"
        assert VerificationStatus.TIMEOUT.name == "TIMEOUT"
        assert VerificationStatus.ERROR.name == "ERROR"

    def test_auto_values(self) -> None:
        # auto() assigns unique integers
        values = {s.value for s in VerificationStatus}
        assert len(values) == len(VerificationStatus)

    def test_membership(self) -> None:
        assert VerificationStatus.PASS in VerificationStatus
        assert VerificationStatus.FAIL in VerificationStatus


class TestVerificationClaim:
    """Tests for VerificationClaim dataclass."""

    def test_valid_construction(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="tests pass")
        assert claim.claim_id == "c1"
        assert claim.claimed_outcome == "tests pass"
        assert claim.timeout_seconds == 60
        assert claim.verification_commands == ()
        assert claim.evidence_paths == ()

    def test_empty_claim_id_raises(self) -> None:
        with pytest.raises(ValueError, match="claim_id"):
            VerificationClaim(claim_id="", claimed_outcome="tests pass")

    def test_empty_claimed_outcome_raises(self) -> None:
        with pytest.raises(ValueError, match="claimed_outcome"):
            VerificationClaim(claim_id="c1", claimed_outcome="")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            VerificationClaim(claim_id="c1", claimed_outcome="pass", timeout_seconds=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            VerificationClaim(claim_id="c1", claimed_outcome="pass", timeout_seconds=-1)

    def test_commands_coerced_to_tuple(self) -> None:
        claim = VerificationClaim(
            claim_id="c1", claimed_outcome="pass", verification_commands=["pytest", "ruff"]
        )
        assert isinstance(claim.verification_commands, tuple)
        assert claim.verification_commands == ("pytest", "ruff")

    def test_empty_commands_filtered(self) -> None:
        claim = VerificationClaim(
            claim_id="c1", claimed_outcome="pass", verification_commands=["pytest", "", "ruff"]
        )
        assert claim.verification_commands == ("pytest", "ruff")

    def test_frozen_cannot_mutate(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="pass")
        with pytest.raises(AttributeError):
            claim.claim_id = "c2"


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_defaults(self) -> None:
        res = ExecutionResult(command="pytest", stdout="ok", stderr="", return_code=0, execution_time_ms=100)
        assert res.timed_out is False

    def test_all_fields(self) -> None:
        res = ExecutionResult(
            command="pytest", stdout="ok", stderr="err", return_code=1, execution_time_ms=200, timed_out=True
        )
        assert res.return_code == 1
        assert res.timed_out is True


class TestVerificationReport:
    """Tests for VerificationReport dataclass."""

    def test_valid_construction(self) -> None:
        report = VerificationReport(claim_id="c1", status=VerificationStatus.PASS)
        assert report.claim_id == "c1"
        assert report.status == VerificationStatus.PASS
        assert report.execution_summary == ""

    def test_empty_claim_id_raises(self) -> None:
        with pytest.raises(ValueError, match="claim_id"):
            VerificationReport(claim_id="", status=VerificationStatus.PASS)

    def test_sequence_fields_coerced(self) -> None:
        report = VerificationReport(
            claim_id="c1",
            status=VerificationStatus.FAIL,
            evidence_collected=["file1"],
            evidence_missing=["file2"],
            mismatch_details=["m1"],
            recommendations=["fix it"],
        )
        assert report.evidence_collected == ("file1",)
        assert report.evidence_missing == ("file2",)

    def test_command_results_tuple(self) -> None:
        res = ExecutionResult(command="pytest", stdout="ok", stderr="", return_code=0, execution_time_ms=10)
        report = VerificationReport(claim_id="c1", status=VerificationStatus.PASS, command_results=[res])
        assert isinstance(report.command_results, tuple)
        assert len(report.command_results) == 1


class TestVerifyCompletionCommandV1:
    """Tests for VerifyCompletionCommandV1."""

    def test_valid_construction(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="pass")
        cmd = VerifyCompletionCommandV1(workspace="/ws", claim=claim)
        assert cmd.strict_mode is True
        assert cmd.allowed_commands is None

    def test_empty_workspace_raises(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="pass")
        with pytest.raises(ValueError, match="workspace"):
            VerifyCompletionCommandV1(workspace="", claim=claim)

    def test_non_claim_raises(self) -> None:
        with pytest.raises(TypeError, match="claim"):
            VerifyCompletionCommandV1(workspace="/ws", claim="not a claim")  # type: ignore[arg-type]

    def test_allowed_commands_coerced(self) -> None:
        claim = VerificationClaim(claim_id="c1", claimed_outcome="pass")
        cmd = VerifyCompletionCommandV1(workspace="/ws", claim=claim, allowed_commands=["pytest"])
        assert cmd.allowed_commands == ("pytest",)


class TestVerifyCompletionResultV1:
    """Tests for VerifyCompletionResultV1."""

    def test_defaults(self) -> None:
        res = VerifyCompletionResultV1(ok=True)
        assert res.report is None
        assert res.error_code is None
        assert res.error_message is None

    def test_with_report(self) -> None:
        report = VerificationReport(claim_id="c1", status=VerificationStatus.PASS)
        res = VerifyCompletionResultV1(ok=True, report=report)
        assert res.report is not None
        assert res.report.status == VerificationStatus.PASS


class TestVerificationCompletedEventV1:
    """Tests for VerificationCompletedEventV1."""

    def test_construction(self) -> None:
        evt = VerificationCompletedEventV1(
            claim_id="c1", status=VerificationStatus.PASS, workspace="/ws", verified_at="ts"
        )
        assert evt.claim_id == "c1"
        assert evt.status == VerificationStatus.PASS


class TestVerificationGuardErrorV1:
    """Tests for VerificationGuardErrorV1."""

    def test_message(self) -> None:
        err = VerificationGuardErrorV1("something wrong")
        assert str(err) == "something wrong"

    def test_code_default(self) -> None:
        err = VerificationGuardErrorV1("fail")
        assert err.code == "verification_guard_error"

    def test_custom_code(self) -> None:
        err = VerificationGuardErrorV1("fail", code="custom")
        assert err.code == "custom"

    def test_details(self) -> None:
        err = VerificationGuardErrorV1("fail", details={"a": 1})
        assert err.details == {"a": 1}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError):
            VerificationGuardErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError):
            VerificationGuardErrorV1("fail", code="")


class TestStateTransitions:
    """Tests that model status transitions are representable."""

    @pytest.mark.parametrize(
        ("status", "is_terminal"),
        [
            (VerificationStatus.PASS, True),
            (VerificationStatus.FAIL, True),
            (VerificationStatus.BLOCKED, True),
            (VerificationStatus.TIMEOUT, True),
            (VerificationStatus.ERROR, True),
        ],
    )
    def test_all_statuses_representable(self, status: VerificationStatus, is_terminal: bool) -> None:
        report = VerificationReport(claim_id="c1", status=status)
        assert report.status == status

    def test_result_ok_true_with_pass(self) -> None:
        report = VerificationReport(claim_id="c1", status=VerificationStatus.PASS)
        res = VerifyCompletionResultV1(ok=True, report=report)
        assert res.ok is True
        assert res.report.status == VerificationStatus.PASS

    def test_result_ok_false_with_fail(self) -> None:
        report = VerificationReport(claim_id="c1", status=VerificationStatus.FAIL)
        res = VerifyCompletionResultV1(ok=False, report=report)
        assert res.ok is False
        assert res.report.status == VerificationStatus.FAIL
