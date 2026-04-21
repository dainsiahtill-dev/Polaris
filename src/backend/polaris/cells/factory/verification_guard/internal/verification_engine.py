"""Verification Engine - Core verification logic.

This module implements the "Verification Before Completion" pattern,
providing a robust engine to verify completion claims through:
- Safe command execution
- Evidence collection
- Outcome matching
- Detailed reporting
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from polaris.cells.factory.verification_guard.internal.safe_executor import (
    SafeExecutor,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    ExecutionResult,
    VerificationClaim,
    VerificationGuardErrorV1,
    VerificationReport,
    VerificationStatus,
)


class VerificationEngine:
    """Engine for verifying completion claims.

    Implements the core "Verification Before Completion" workflow:
    1. Validate claim structure
    2. Execute verification commands safely
    3. Collect evidence from specified paths
    4. Match claimed outcomes against actual results
    5. Generate detailed verification report

    """

    def __init__(
        self,
        *,
        safe_executor: SafeExecutor | None = None,
        default_timeout_seconds: int = 60,
    ) -> None:
        """Initialize the verification engine.

        Args:
            safe_executor: Custom safe executor instance
            default_timeout_seconds: Default timeout for verification commands

        """
        self._executor = safe_executor or SafeExecutor()
        self._default_timeout = default_timeout_seconds

    def verify(
        self,
        claim: VerificationClaim,
        *,
        workspace: str | None = None,
        strict_mode: bool = True,
    ) -> VerificationReport:
        """Verify a completion claim and return a detailed report.

        This is the main entry point for verification. It:
        1. Executes all verification commands
        2. Collects evidence from specified paths
        3. Compares claimed outcomes with actual results
        4. Generates a comprehensive report

        Args:
            claim: The verification claim to check
            workspace: Optional workspace directory for execution
            strict_mode: If True, any mismatch fails verification

        Returns:
            VerificationReport with full details of the verification

        Raises:
            VerificationGuardErrorV1: If verification cannot be performed

        """
        command_results: list[ExecutionResult] = []
        mismatch_details: list[str] = []
        recommendations: list[str] = []

        # Execute verification commands
        for command in claim.verification_commands:
            try:
                result = self._executor.execute(
                    command,
                    timeout_seconds=claim.timeout_seconds,
                    working_dir=workspace,
                )
                command_results.append(result)

                # Check if command succeeded
                if result.timed_out:
                    mismatch_details.append(f"Command timed out: {command} ({claim.timeout_seconds}s)")
                    recommendations.append(f"Increase timeout or optimize: {command}")
                elif result.return_code != 0:
                    mismatch_details.append(f"Command failed (exit {result.return_code}): {command}")
                    if result.stderr:
                        error_preview = result.stderr[:200].replace("\n", " ")
                        mismatch_details.append(f"  Error: {error_preview}...")

            except VerificationGuardErrorV1 as e:
                # Command was blocked by safety checks
                msg = str(e)
                return VerificationReport(
                    claim_id=claim.claim_id,
                    status=VerificationStatus.BLOCKED,
                    command_results=tuple(command_results),
                    mismatch_details=[f"Command blocked: {command} - {msg}"],
                    recommendations=["Check command against whitelist"],
                    execution_summary=f"Verification blocked: {msg}",
                )

        # Collect evidence
        evidence_collected, evidence_missing = self._collect_evidence(
            claim.evidence_paths,
            workspace,
        )

        # Check for missing evidence
        if evidence_missing:
            mismatch_details.extend(f"Missing evidence: {path}" for path in evidence_missing)
            recommendations.append("Ensure all claimed evidence files exist")

        # Match claimed outcome against actual results
        outcome_match = self._match_outcome(
            claim.claimed_outcome,
            command_results,
            evidence_collected,
        )

        if not outcome_match:
            mismatch_details.append(f"Claimed outcome not verified: '{claim.claimed_outcome}'")
            recommendations.append("Review command outputs and evidence")

        # Determine final status
        status = self._determine_status(
            command_results,
            mismatch_details,
            strict_mode,
        )

        # Generate summary
        summary = self._generate_summary(
            claim,
            status,
            command_results,
            evidence_collected,
            evidence_missing,
        )

        return VerificationReport(
            claim_id=claim.claim_id,
            status=status,
            command_results=tuple(command_results),
            evidence_collected=tuple(evidence_collected),
            evidence_missing=tuple(evidence_missing),
            mismatch_details=tuple(mismatch_details),
            recommendations=tuple(recommendations) if recommendations else (),
            execution_summary=summary,
        )

    def _collect_evidence(
        self,
        evidence_paths: Sequence[str],
        workspace: str | None,
    ) -> tuple[list[str], list[str]]:
        """Collect evidence from specified paths.

        Args:
            evidence_paths: List of file/directory paths to check
            workspace: Optional base directory for relative paths

        Returns:
            Tuple of (collected_paths, missing_paths)

        """
        collected: list[str] = []
        missing: list[str] = []

        base_path = Path(workspace) if workspace else Path.cwd()

        for path_str in evidence_paths:
            path = Path(path_str)
            if not path.is_absolute():
                path = base_path / path

            resolved_path = path.resolve()

            # Security check: ensure path is within workspace
            if workspace and not self._is_path_within_workspace(resolved_path, base_path):
                missing.append(f"{path_str} (outside workspace)")
                continue

            if resolved_path.exists():
                collected.append(str(resolved_path))
            else:
                missing.append(path_str)

        return collected, missing

    def _is_path_within_workspace(self, path: Path, workspace: Path) -> bool:
        """Check if a path is within the allowed workspace."""
        try:
            path.relative_to(workspace.resolve())
            return True
        except ValueError:
            return False

    def _match_outcome(
        self,
        claimed_outcome: str,
        command_results: Sequence[ExecutionResult],
        evidence_collected: Sequence[str],
    ) -> bool:
        """Match claimed outcome against actual results.

        Uses fuzzy matching to determine if the claimed outcome
        is supported by the actual command results and evidence.

        Args:
            claimed_outcome: The claimed result description
            command_results: Results from executed commands
            evidence_collected: Paths to collected evidence

        Returns:
            True if the claim is supported by evidence

        """
        claimed_lower = claimed_outcome.lower()

        # Check for common outcome patterns
        success_patterns = {
            "pass": [r"passed", r"pass", r"success", r"ok", r"0 failed"],
            "success": [r"success", r"completed", r"done", r"ok"],
            "complete": [r"complete", r"finished", r"done"],
            "format": [r"formatted", r"clean", r"unchanged"],
            "lint": [r"clean", r"no errors", r"passed"],
            "test": [r"passed", r"pass", r"success", r"ok"],
            "build": [r"success", r"built", r"complete"],
            "check": [r"clean", r"passed", r"ok"],
        }

        # Check command outputs for success indicators
        all_commands_succeeded = all(r.return_code == 0 and not r.timed_out for r in command_results)

        # Look for success keywords in claimed outcome
        for keyword, patterns in success_patterns.items():
            if keyword in claimed_lower:
                # Check if any command output indicates success
                for result in command_results:
                    output = (result.stdout + result.stderr).lower()
                    for pattern in patterns:
                        if re.search(pattern, output):
                            return True

                # If no commands run but evidence exists, consider it a match
                # for claims like "files created" or "evidence generated"
                if not command_results and evidence_collected:
                    return True

                # If all commands succeeded, consider it a match
                return bool(all_commands_succeeded and command_results)

        # Default: if all commands succeeded and we have evidence, it's a match
        # Guard against all([]) = True vacuous truth when command_results is empty
        if all_commands_succeeded and command_results:
            return True

        # If no commands and no specific keywords, be lenient
        if not command_results and not evidence_collected:
            # Empty claim with no verification - this is suspicious
            return False

        return all_commands_succeeded

    def _determine_status(
        self,
        command_results: Sequence[ExecutionResult],
        mismatch_details: Sequence[str],
        strict_mode: bool,
    ) -> VerificationStatus:
        """Determine the final verification status.

        Args:
            command_results: Results from all executed commands
            mismatch_details: List of identified mismatches
            strict_mode: Whether to fail on any mismatch

        Returns:
            VerificationStatus enum value

        """
        # Check for timeouts
        if any(r.timed_out for r in command_results):
            return VerificationStatus.TIMEOUT

        # Check for blocked commands (handled separately, but defensive)
        if any("blocked" in str(r.stderr).lower() for r in command_results):
            return VerificationStatus.BLOCKED

        # In strict mode, any mismatch is a failure
        if strict_mode and mismatch_details:
            return VerificationStatus.FAIL

        # Check if all commands succeeded
        all_succeeded = all(r.return_code == 0 for r in command_results)

        if not all_succeeded:
            return VerificationStatus.FAIL

        # If we have mismatches but commands succeeded (non-strict)
        if mismatch_details:
            return VerificationStatus.FAIL

        return VerificationStatus.PASS

    def _generate_summary(
        self,
        claim: VerificationClaim,
        status: VerificationStatus,
        command_results: Sequence[ExecutionResult],
        evidence_collected: Sequence[str],
        evidence_missing: Sequence[str],
    ) -> str:
        """Generate a human-readable summary of verification results."""
        lines = [
            f"Verification of claim '{claim.claim_id}': {status.name}",
            f"Claimed outcome: {claim.claimed_outcome}",
            f"Commands executed: {len(command_results)}",
            f"Evidence found: {len(evidence_collected)}/{len(evidence_collected) + len(evidence_missing)}",
        ]

        if command_results:
            success_count = sum(1 for r in command_results if r.return_code == 0)
            lines.append(f"Command success rate: {success_count}/{len(command_results)}")

        if evidence_missing:
            lines.append(f"Missing evidence: {', '.join(evidence_missing[:3])}")
            if len(evidence_missing) > 3:
                lines.append(f"  ... and {len(evidence_missing) - 3} more")

        return "\n".join(lines)

    def validate_claim_structure(self, claim: VerificationClaim) -> list[str]:
        """Validate the structure of a verification claim.

        Returns a list of validation errors (empty if valid).

        """
        errors: list[str] = []

        if not claim.claim_id or not claim.claim_id.strip():
            errors.append("claim_id is required")

        if not claim.claimed_outcome or not claim.claimed_outcome.strip():
            errors.append("claimed_outcome is required")

        if claim.timeout_seconds <= 0:
            errors.append("timeout_seconds must be positive")

        # Validate command syntax
        for cmd in claim.verification_commands:
            if not cmd or not cmd.strip():
                errors.append("Empty command")
                continue

            # Check if command is allowed
            if not self._executor.is_command_allowed(cmd):
                errors.append(f"Command not allowed: {cmd}")

        # Also check for empty strings in the list
        for i, cmd in enumerate(claim.verification_commands):
            if cmd == "":
                errors.append(f"Empty command at index {i}")

        return errors
