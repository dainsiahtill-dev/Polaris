"""Pure policy for public contract change review governance."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

RULE_ID = "contract_change_requires_review"
DEFAULT_LOOKBACK_DAYS = 30
CONTRACT_PATTERNS: tuple[str, ...] = (
    "public/contracts.py",
    "public/contract.py",
    "contracts.py",
    "contract.py",
)
ADR_PATTERN = re.compile(r"\badr-\d+[-\w]*\b", re.IGNORECASE)
VC_PATTERN = re.compile(r"\bvc-\d{8}[-\w]*\b", re.IGNORECASE)
REVIEW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\breview(?:\s|[:\-])", re.IGNORECASE),
    re.compile(r"\bapproved?\b", re.IGNORECASE),
    re.compile(r"\bchecked?\b", re.IGNORECASE),
    re.compile(r"\bverified?\b", re.IGNORECASE),
    re.compile(r"\blgtm\b", re.IGNORECASE),
    re.compile(r"\blooks?\s+good\b", re.IGNORECASE),
    re.compile(r"\bgovernance\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class GitLogResult:
    """Result of reading git history for one contract file."""

    stdout: str
    stderr: str = ""
    returncode: int = 0


@dataclass(frozen=True)
class ContractFileInfo:
    """Public contract file and its most recent review evidence status."""

    path: Path
    cell_id: str
    relative_path: str
    has_recent_changes: bool = False
    commit_hash: str = ""
    commit_date: str = ""
    commit_message: str = ""
    has_review_evidence: bool = False
    review_evidence_type: str = ""
    git_error: str = ""


@dataclass(frozen=True)
class ContractChangeReviewPolicyResult:
    """Evaluation result for public contract change review governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] | None = None


GitLogReader = Callable[[Path, int], GitLogResult]


class ContractChangeReviewPolicy:
    """Evaluate whether recent public contract changes have review evidence.

    The policy scans public Cell contract files, reads the latest commit within
    the configured lookback window, and requires ADR, verification-card, or
    explicit review evidence in the commit message. The git reader is injected
    for tests and alternate infrastructure, keeping the policy logic independent
    from subprocess execution.

    Complexity:
        O(f * p + f * h) time for contract-file discovery, contract patterns,
        and one git history read per discovered file. O(f) space for contract
        metadata and emitted diagnostics.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        days: int = DEFAULT_LOOKBACK_DAYS,
        git_log_reader: GitLogReader | None = None,
    ) -> None:
        """Create a contract review policy evaluator.

        Args:
            workspace: Backend workspace root containing ``polaris/cells``.
            days: Number of days to inspect in git history. Must be positive.
            git_log_reader: Optional infrastructure adapter for git history.
        """
        if days <= 0:
            raise ValueError("days must be a positive integer")
        self.workspace = workspace
        self.days = days
        self.git_log_reader = git_log_reader or self.run_git_log

    def evaluate(self) -> ContractChangeReviewPolicyResult:
        """Evaluate the contract review policy and return structured evidence."""
        contract_files = self.find_contract_files()
        evidence: list[str] = [f"Found {len(contract_files)} public contract file(s)"]
        violations: list[str] = []
        warnings: list[str] = []

        if not contract_files:
            return ContractChangeReviewPolicyResult(
                rule_id=RULE_ID,
                passed=True,
                evidence=tuple(evidence),
                warnings=("No public contract files found",),
                details=self.build_details(contract_files),
            )

        inspected_files = self.inspect_contract_files(contract_files)
        files_with_changes = [file_info for file_info in inspected_files if file_info.has_recent_changes]
        files_with_review = [file_info for file_info in files_with_changes if file_info.has_review_evidence]
        files_without_review = [file_info for file_info in files_with_changes if not file_info.has_review_evidence]
        git_errors = [file_info for file_info in inspected_files if file_info.git_error]

        for file_info in files_with_review:
            evidence.append(
                f"{file_info.relative_path}: reviewed ({file_info.review_evidence_type}) - {file_info.commit_hash[:8]}"
            )
        evidence.append(f"Checked {len(files_with_changes)} file(s) with recent changes (last {self.days} days)")

        for file_info in git_errors:
            warnings.append(f"{file_info.relative_path}: git log failed: {file_info.git_error}")

        if not files_with_changes:
            evidence.append(f"No contract changes in the last {self.days} days - rule not applicable")
        elif len(files_with_review) == len(files_with_changes):
            evidence.append(f"All {len(files_with_changes)} contract change(s) have review evidence")
        else:
            violations.append(
                f"Only {len(files_with_review)}/{len(files_with_changes)} contract changes have review evidence"
            )
            for file_info in files_without_review:
                short_hash = file_info.commit_hash[:8]
                violations.append(f"{file_info.relative_path}: no review evidence in commit {short_hash}")
                warnings.append(f"{file_info.relative_path}: {short_hash} - {file_info.commit_message[:60]}...")

        return ContractChangeReviewPolicyResult(
            rule_id=RULE_ID,
            passed=not violations,
            evidence=tuple(evidence),
            violations=tuple(violations),
            warnings=tuple(warnings),
            details=self.build_details(inspected_files),
        )

    def find_contract_files(self) -> list[ContractFileInfo]:
        """Find deduplicated public Cell contract files in the workspace."""
        cells_dir = self.workspace / "polaris" / "cells"
        if not cells_dir.exists():
            return []

        contract_files: dict[Path, ContractFileInfo] = {}
        for cell_dir in sorted(cells_dir.iterdir()):
            if not cell_dir.is_dir():
                continue

            for pattern in CONTRACT_PATTERNS:
                for contract_path in sorted(cell_dir.rglob(pattern)):
                    if is_excluded_contract_path(contract_path):
                        continue
                    resolved = contract_path.resolve()
                    rel_path = relative_path(self.workspace, contract_path)
                    contract_files.setdefault(
                        resolved,
                        ContractFileInfo(
                            path=contract_path,
                            cell_id=cell_dir.name,
                            relative_path=rel_path,
                        ),
                    )

        return sorted(contract_files.values(), key=lambda file_info: file_info.relative_path)

    def inspect_contract_files(self, contract_files: list[ContractFileInfo]) -> list[ContractFileInfo]:
        """Attach git-history review evidence to contract file metadata."""
        inspected: list[ContractFileInfo] = []
        for contract_file in contract_files:
            git_result = self.git_log_reader(contract_file.path, self.days)
            if git_result.returncode != 0:
                inspected.append(
                    replace(
                        contract_file,
                        git_error=git_result.stderr.strip() or f"git exited {git_result.returncode}",
                    )
                )
                continue

            first_commit = first_git_log_entry(git_result.stdout)
            if first_commit is None:
                inspected.append(contract_file)
                continue

            commit_hash, commit_date, commit_message = first_commit
            has_review, evidence_type = check_commit_has_review_evidence(commit_message)
            inspected.append(
                replace(
                    contract_file,
                    has_recent_changes=True,
                    commit_hash=commit_hash,
                    commit_date=commit_date,
                    commit_message=commit_message,
                    has_review_evidence=has_review,
                    review_evidence_type=evidence_type,
                )
            )

        return inspected

    def run_git_log(self, file_path: Path, since_days: int) -> GitLogResult:
        """Run git log for one file within the configured lookback window."""
        command = [
            "git",
            "log",
            "--format=%H|%ad|%s",
            "--date=iso",
            f"--since={since_days}.days",
            "--",
            str(file_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return GitLogResult(stdout="", stderr=str(exc), returncode=1)
        return GitLogResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def build_details(self, contract_files: list[ContractFileInfo]) -> dict[str, Any]:
        """Return detailed file-level review evidence for CLI and audits."""
        return {
            "total_files": len(contract_files),
            "files": [
                {
                    "path": file_info.relative_path,
                    "cell_id": file_info.cell_id,
                    "has_recent_changes": file_info.has_recent_changes,
                    **(
                        {
                            "commit_hash": file_info.commit_hash,
                            "commit_date": file_info.commit_date,
                            "commit_message": file_info.commit_message,
                            "has_review_evidence": file_info.has_review_evidence,
                            "review_evidence_type": file_info.review_evidence_type,
                        }
                        if file_info.has_recent_changes
                        else {}
                    ),
                    **({"git_error": file_info.git_error} if file_info.git_error else {}),
                }
                for file_info in contract_files
            ],
        }


def is_excluded_contract_path(path: Path) -> bool:
    """Return true for internal or test contract paths excluded from governance."""
    path_parts = set(path.parts)
    return bool(path_parts.intersection({"fixtures", "internal", "test", "tests"}))


def relative_path(workspace: Path, path: Path) -> str:
    """Return a stable workspace-relative path."""
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def first_git_log_entry(stdout: str) -> tuple[str, str, str] | None:
    """Parse the first git log entry from ``git log --format=%H|%ad|%s``."""
    first_line = stdout.strip().split("\n", 1)[0].strip()
    if not first_line:
        return None
    parts = first_line.split("|", 2)
    if len(parts) < 3 or not parts[0]:
        return None
    return parts[0], parts[1], parts[2]


def check_commit_has_review_evidence(commit_message: str) -> tuple[bool, str]:
    """Return whether a commit message contains accepted review evidence."""
    if ADR_PATTERN.search(commit_message):
        return True, "adr_reference"
    if VC_PATTERN.search(commit_message):
        return True, "vc_reference"
    for pattern in REVIEW_PATTERNS:
        if pattern.search(commit_message):
            return True, "review_keyword"
    return False, ""


def evaluate_contract_change_review(
    workspace: Path,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    git_log_reader: GitLogReader | None = None,
) -> ContractChangeReviewPolicyResult:
    """Evaluate public contract review evidence for a workspace."""
    return ContractChangeReviewPolicy(
        workspace,
        days=days,
        git_log_reader=git_log_reader,
    ).evaluate()
