"""Unit tests for polaris.cells.factory.verification_guard.internal.verification_engine."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.factory.verification_guard.internal.verification_engine import VerificationEngine
from polaris.cells.factory.verification_guard.public.contracts import (
    ExecutionResult,
    VerificationClaim,
    VerificationGuardErrorV1,
    VerificationReport,
    VerificationStatus,
)


class TestVerificationEngineInit:
    """Tests for VerificationEngine initialization."""

    def test_default_init(self) -> None:
        engine = VerificationEngine()
        assert engine._executor is not None
        assert engine._default_timeout == 60

    def test_custom_init(self) -> None:
        mock_executor = MagicMock()
        engine = VerificationEngine(safe_executor=mock_executor, default_timeout_seconds=120)
        assert engine._executor is mock_executor
        assert engine._default_timeout == 120


class TestVerify:
    """Tests for VerificationEngine.verify."""

    def test_verify_pass_no_commands(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="files created",
            evidence_paths=["file.txt"],
        )
        # No commands, no evidence - should fail
        report = engine.verify(claim)
        assert isinstance(report, VerificationReport)
        assert report.claim_id == "c1"

    def test_verify_with_successful_command(self) -> None:
        engine = VerificationEngine()
        mock_executor = MagicMock()
        mock_executor.execute.return_value = ExecutionResult(
            command="pytest",
            stdout="1 passed",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        engine._executor = mock_executor

        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="tests pass",
            verification_commands=["pytest"],
        )
        report = engine.verify(claim, workspace="/tmp")
        assert report.status == VerificationStatus.PASS
        assert len(report.command_results) == 1

    def test_verify_with_failed_command(self) -> None:
        engine = VerificationEngine()
        mock_executor = MagicMock()
        mock_executor.execute.return_value = ExecutionResult(
            command="pytest",
            stdout="",
            stderr="1 failed",
            return_code=1,
            execution_time_ms=100,
        )
        engine._executor = mock_executor

        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="tests pass",
            verification_commands=["pytest"],
        )
        report = engine.verify(claim)
        assert report.status == VerificationStatus.FAIL

    def test_verify_with_timed_out_command(self) -> None:
        engine = VerificationEngine()
        mock_executor = MagicMock()
        mock_executor.execute.return_value = ExecutionResult(
            command="pytest",
            stdout="",
            stderr="timeout",
            return_code=-1,
            execution_time_ms=60000,
            timed_out=True,
        )
        engine._executor = mock_executor

        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="tests pass",
            verification_commands=["pytest"],
        )
        report = engine.verify(claim)
        assert report.status == VerificationStatus.TIMEOUT

    def test_verify_blocked_command(self) -> None:
        engine = VerificationEngine()
        mock_executor = MagicMock()
        mock_executor.execute.side_effect = VerificationGuardErrorV1(
            "Command blocked",
            code="command_blocked",
        )
        engine._executor = mock_executor

        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="tests pass",
            verification_commands=["rm -rf /"],
        )
        report = engine.verify(claim)
        assert report.status == VerificationStatus.BLOCKED
        assert "blocked" in report.execution_summary.lower()

    def test_verify_strict_mode_false(self) -> None:
        engine = VerificationEngine()
        mock_executor = MagicMock()
        mock_executor.execute.return_value = ExecutionResult(
            command="pytest",
            stdout="1 passed",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        engine._executor = mock_executor

        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="tests pass",
            verification_commands=["pytest"],
        )
        report = engine.verify(claim, strict_mode=False)
        # All commands succeeded, should pass even in non-strict
        assert report.status == VerificationStatus.PASS


class TestCollectEvidence:
    """Tests for VerificationEngine._collect_evidence."""

    def test_collect_existing_file(self, tmp_path) -> None:
        engine = VerificationEngine()
        test_file = tmp_path / "evidence.txt"
        test_file.write_text("evidence")

        collected, missing = engine._collect_evidence([str(test_file)], str(tmp_path))
        assert len(collected) == 1
        assert len(missing) == 0

    def test_collect_missing_file(self, tmp_path) -> None:
        engine = VerificationEngine()
        collected, missing = engine._collect_evidence(["missing.txt"], str(tmp_path))
        assert len(collected) == 0
        assert len(missing) == 1
        assert missing[0] == "missing.txt"

    def test_collect_relative_path(self, tmp_path) -> None:
        engine = VerificationEngine()
        test_file = tmp_path / "evidence.txt"
        test_file.write_text("evidence")

        collected, missing = engine._collect_evidence(["evidence.txt"], str(tmp_path))
        assert len(collected) == 1
        assert len(missing) == 0

    def test_collect_outside_workspace(self, tmp_path) -> None:
        engine = VerificationEngine()
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("outside")

        collected, missing = engine._collect_evidence([str(outside)], str(tmp_path))
        assert len(collected) == 0
        assert len(missing) == 1
        assert "outside workspace" in missing[0]


class TestIsPathWithinWorkspace:
    """Tests for VerificationEngine._is_path_within_workspace."""

    def test_within_workspace(self, tmp_path) -> None:
        engine = VerificationEngine()
        sub = tmp_path / "sub"
        sub.mkdir()
        assert engine._is_path_within_workspace(sub, tmp_path) is True

    def test_outside_workspace(self, tmp_path) -> None:
        engine = VerificationEngine()
        outside = tmp_path.parent / "outside"
        assert engine._is_path_within_workspace(outside, tmp_path) is False

    def test_same_path(self, tmp_path) -> None:
        engine = VerificationEngine()
        assert engine._is_path_within_workspace(tmp_path, tmp_path) is True


class TestMatchOutcome:
    """Tests for VerificationEngine._match_outcome."""

    def test_match_pass_keyword(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="pytest",
            stdout="1 passed",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        assert engine._match_outcome("tests pass", [result], []) is True

    def test_match_success_keyword(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="cmd",
            stdout="success",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        assert engine._match_outcome("build success", [result], []) is True

    def test_no_commands_no_evidence(self) -> None:
        engine = VerificationEngine()
        assert engine._match_outcome("something", [], []) is False

    def test_all_commands_succeeded(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="cmd",
            stdout="output",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        assert engine._match_outcome("generic outcome", [result], []) is True

    def test_empty_claim_with_evidence(self) -> None:
        engine = VerificationEngine()
        assert engine._match_outcome("files created", [], ["file.txt"]) is True


class TestDetermineStatus:
    """Tests for VerificationEngine._determine_status."""

    def test_timeout(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="cmd",
            stdout="",
            stderr="",
            return_code=-1,
            execution_time_ms=100,
            timed_out=True,
        )
        status = engine._determine_status([result], [], True)
        assert status == VerificationStatus.TIMEOUT

    def test_strict_mode_mismatch(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="cmd",
            stdout="",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        status = engine._determine_status([result], ["missing evidence"], True)
        assert status == VerificationStatus.FAIL

    def test_all_succeeded(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="cmd",
            stdout="",
            stderr="",
            return_code=0,
            execution_time_ms=100,
        )
        status = engine._determine_status([result], [], True)
        assert status == VerificationStatus.PASS

    def test_command_failed(self) -> None:
        engine = VerificationEngine()
        result = ExecutionResult(
            command="cmd",
            stdout="",
            stderr="error",
            return_code=1,
            execution_time_ms=100,
        )
        status = engine._determine_status([result], [], True)
        assert status == VerificationStatus.FAIL


class TestGenerateSummary:
    """Tests for VerificationEngine._generate_summary."""

    def test_summary_pass(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        summary = engine._generate_summary(
            claim,
            VerificationStatus.PASS,
            [],
            [],
            [],
        )
        assert "PASS" in summary
        assert "c1" in summary

    def test_summary_with_missing_evidence(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        summary = engine._generate_summary(
            claim,
            VerificationStatus.FAIL,
            [],
            [],
            ["file1.txt", "file2.txt", "file3.txt", "file4.txt"],
        )
        assert "FAIL" in summary
        assert "Missing evidence" in summary


class TestValidateClaimStructure:
    """Tests for VerificationEngine.validate_claim_structure."""

    def test_valid_claim(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="ok",
            verification_commands=["pytest"],
        )
        errors = engine.validate_claim_structure(claim)
        assert errors == []

    def test_empty_claim_id(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        # Manually set bad claim_id to test validation
        object.__setattr__(claim, "claim_id", "")
        errors = engine.validate_claim_structure(claim)
        assert any("claim_id" in e for e in errors)

    def test_empty_claimed_outcome(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        object.__setattr__(claim, "claimed_outcome", "")
        errors = engine.validate_claim_structure(claim)
        assert any("claimed_outcome" in e for e in errors)

    def test_invalid_timeout(self) -> None:
        engine = VerificationEngine()
        # VerificationClaim.__post_init__ rejects <=0, so we must bypass it
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        object.__setattr__(claim, "timeout_seconds", -1)
        errors = engine.validate_claim_structure(claim)
        assert any("timeout" in e.lower() for e in errors)

    def test_empty_command(self) -> None:
        engine = VerificationEngine()
        # VerificationClaim normalizes and drops empty strings, so bypass __post_init__
        claim = VerificationClaim(claim_id="c1", claimed_outcome="ok")
        object.__setattr__(claim, "verification_commands", ("",))
        errors = engine.validate_claim_structure(claim)
        assert any("Empty command" in e for e in errors)

    def test_blocked_command(self) -> None:
        engine = VerificationEngine()
        claim = VerificationClaim(
            claim_id="c1",
            claimed_outcome="ok",
            verification_commands=["rm -rf /"],
        )
        errors = engine.validate_claim_structure(claim)
        assert any("not allowed" in e.lower() for e in errors)
