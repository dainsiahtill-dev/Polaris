#!/usr/bin/env python3
"""Check contract_change_requires_review rule.

Ensures that public contract changes have review evidence:
- Git commit messages with ADR references (adr-NNN or ADR-NNN pattern)
- Review comments or approval markers
- Verification card references

用法:
    python docs/governance/ci/scripts/check_contract_review.py
    python docs/governance/ci/scripts/check_contract_review.py --days 30
    python docs/governance/ci/scripts/check_contract_review.py --json
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Import the base framework
sys.path.insert(0, str(Path(__file__).parent))
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent

# Patterns for detecting review evidence in commit messages
ADR_PATTERN = re.compile(r"\badr-\d+[-\w]*\b", re.IGNORECASE)
VC_PATTERN = re.compile(r"\bvc-\d{8}[-\w]*\b", re.IGNORECASE)
REVIEW_PATTERNS = [
    re.compile(r"\breview(?:\s|[:\-])", re.IGNORECASE),
    re.compile(r"\bapproved?\b", re.IGNORECASE),
    re.compile(r"\bchecked?\b", re.IGNORECASE),
    re.compile(r"\bverified?\b", re.IGNORECASE),
    re.compile(r"\b LGTM \b", re.IGNORECASE),
    re.compile(r"\blooks?\s+good\b", re.IGNORECASE),
    re.compile(r"\bgovernance\b", re.IGNORECASE),
]

# Contract file patterns
CONTRACT_PATTERNS = [
    "public/contracts.py",
    "public/contract.py",
    "contracts.py",
    "contract.py",
]


@dataclass
class ContractFileInfo:
    """Information about a contract file."""

    path: Path
    cell_id: str
    relative_path: str
    has_recent_changes: bool = False
    commit_hash: str = ""
    commit_date: str = ""
    commit_message: str = ""
    has_review_evidence: bool = False
    review_evidence_type: str = ""


class ContractReviewChecker(FitnessRuleChecker):
    """Checker for contract change review requirements."""

    def __init__(self, workspace: Path | None = None, days: int = 30) -> None:
        super().__init__(workspace)
        self.days = days
        self._contract_files: list[ContractFileInfo] = []

    def find_contract_files(self) -> list[ContractFileInfo]:
        """Find all public contract files in the repository."""
        if self._contract_files:
            return self._contract_files

        cells_dir = self.workspace / "polaris" / "cells"
        if not cells_dir.exists():
            return []

        contract_files: list[ContractFileInfo] = []

        for cell_dir in cells_dir.iterdir():
            if not cell_dir.is_dir():
                continue

            cell_id = cell_dir.name

            for pattern in CONTRACT_PATTERNS:
                for contract_path in cell_dir.rglob(pattern):
                    # Skip internal/test files
                    if "/internal/" in contract_path.parts or "/test" in str(contract_path):
                        continue

                    rel_path = contract_path.relative_to(self.workspace)
                    contract_files.append(
                        ContractFileInfo(
                            path=contract_path,
                            cell_id=cell_id,
                            relative_path=str(rel_path),
                        )
                    )

        self._contract_files = contract_files
        return contract_files

    def _run_git_log(
        self, file_path: Path, since_days: int
    ) -> tuple[str, str, int]:
        """Run git log for a specific file within the last N days."""
        since_date = f"--since={since_days}.days"
        command = [
            "git",
            "log",
            "--format=%H|%ad|%s",
            "--date=iso",
            since_date,
            "--",
            str(file_path),
        ]

        try:
            result = subprocess.run(
                command,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            return result.stdout, result.stderr, result.returncode
        except Exception as e:
            return "", str(e), 1

    def _check_commit_has_review_evidence(self, commit_message: str) -> tuple[bool, str]:
        """Check if a commit message contains review evidence."""
        msg_lower = commit_message.lower()

        # Check for ADR references
        if ADR_PATTERN.search(commit_message):
            return True, "adr_reference"

        # Check for VC references
        if VC_PATTERN.search(commit_message):
            return True, "vc_reference"

        # Check for review keywords
        for pattern in REVIEW_PATTERNS:
            if pattern.search(msg_lower):
                return True, "review_keyword"

        return False, ""

    def check_contract_change_review(self) -> FitnessCheckResult:
        """Check that recent contract changes have review evidence.

        This rule ensures that public contract files that have been modified
        recently have corresponding review evidence (ADR references, VC references,
        or explicit review keywords in commit messages).

        Returns:
            FitnessCheckResult with:
            - passed: True if all recent contract changes have review evidence
            - violations: List of contract files with changes but no review evidence
            - evidence: List of contract files with proper review evidence
        """
        start_time = time.time()
        result = FitnessCheckResult(rule_id="contract_change_requires_review", passed=True)

        # Find all contract files
        contract_files = self.find_contract_files()
        result.evidence.append(f"Found {len(contract_files)} public contract file(s)")

        if not contract_files:
            result.warnings.append("No public contract files found")
            result.duration_ms = (time.time() - start_time) * 1000
            return result

        # Check each contract file for recent changes
        files_with_changes = 0
        files_with_review = 0
        files_without_review: list[str] = []

        for cf in contract_files:
            stdout, _, _ = self._run_git_log(cf.path, self.days)

            if not stdout.strip():
                # No recent changes
                continue

            files_with_changes += 1
            lines = stdout.strip().split("\n")

            # Get the most recent commit
            if lines and lines[0]:
                parts = lines[0].split("|", 2)
                if len(parts) >= 3:
                    cf.commit_hash = parts[0]
                    cf.commit_date = parts[1]
                    cf.commit_message = parts[2]

                    has_review, evidence_type = self._check_commit_has_review_evidence(
                        cf.commit_message
                    )
                    cf.has_review_evidence = has_review
                    cf.review_evidence_type = evidence_type

                    if has_review:
                        files_with_review += 1
                        result.evidence.append(
                            f"  {cf.relative_path}: reviewed ({evidence_type}) - {cf.commit_hash[:8]}"
                        )
                    else:
                        files_without_review.append(
                            f"  {cf.relative_path}: {cf.commit_hash[:8]} - {cf.commit_message[:60]}..."
                        )
                        result.violations.append(
                            f"{cf.relative_path}: no review evidence in commit {cf.commit_hash[:8]}"
                        )

        # Build summary
        result.evidence.append(
            f"Checked {files_with_changes} file(s) with recent changes (last {self.days} days)"
        )

        if files_with_changes == 0:
            result.evidence.append(
                f"No contract changes in the last {self.days} days - rule not applicable"
            )
        elif files_with_review == files_with_changes:
            result.evidence.append(
                f"{GREEN}All {files_with_changes} contract change(s) have review evidence{RESET}"
            )
        else:
            result.passed = False
            result.violations.insert(
                0,
                f"Only {files_with_review}/{files_with_changes} contract changes have review evidence",
            )
            for violation in files_without_review:
                result.warnings.append(violation)

        result.duration_ms = (time.time() - start_time) * 1000
        return result

    def check_contract_change_review_detailed(self) -> dict[str, Any]:
        """Get detailed results for all contract files.

        Returns a dictionary with comprehensive information about
        each contract file and its review status.
        """
        contract_files = self.find_contract_files()
        details: dict[str, Any] = {
            "total_files": len(contract_files),
            "files": [],
        }

        for cf in contract_files:
            stdout, _, _ = self._run_git_log(cf.path, self.days)

            file_info: dict[str, Any] = {
                "path": cf.relative_path,
                "cell_id": cf.cell_id,
                "has_recent_changes": False,
            }

            if stdout.strip():
                lines = stdout.strip().split("\n")
                if lines and lines[0]:
                    parts = lines[0].split("|", 2)
                    if len(parts) >= 3:
                        file_info["has_recent_changes"] = True
                        file_info["commit_hash"] = parts[0]
                        file_info["commit_date"] = parts[1]
                        file_info["commit_message"] = parts[2]

                        has_review, evidence_type = self._check_commit_has_review_evidence(
                            parts[2]
                        )
                        file_info["has_review_evidence"] = has_review
                        file_info["review_evidence_type"] = evidence_type

            details["files"].append(file_info)

        return details


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Check that contract changes have review evidence."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back for contract changes (default: 30)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed information for all contract files",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Repository root path (default: auto-detect)",
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = _parse_args()

    workspace = args.workspace or REPO_ROOT

    if not (workspace / ".git").exists():
        print(
            f"{YELLOW}Warning: Not a git repository at {workspace}{RESET}",
            file=sys.stderr,
        )
        return 0

    checker = ContractReviewChecker(workspace=workspace, days=args.days)

    if args.detailed:
        details = checker.check_contract_change_review_detailed()
        if args.json:
            import json

            print(json.dumps(details, ensure_ascii=False, indent=2))
        else:
            print(f"Total contract files: {details['total_files']}\n")
            for file_info in details["files"]:
                status = ""
                if file_info["has_recent_changes"]:
                    if file_info["has_review_evidence"]:
                        status = f"{GREEN}[REVIEWED]{RESET}"
                    else:
                        status = f"{RED}[NO REVIEW]{RESET}"
                else:
                    status = f"{YELLOW}[NO CHANGES]{RESET}"

                print(f"{status} {file_info['path']}")
                if file_info["has_recent_changes"]:
                    print(f"       Cell: {file_info['cell_id']}")
                    print(f"       Commit: {file_info.get('commit_hash', 'N/A')[:8]}")
                    print(f"       Message: {file_info.get('commit_message', 'N/A')[:70]}...")
                    if file_info.get("review_evidence_type"):
                        print(f"       Evidence: {file_info['review_evidence_type']}")
        return 0

    result = checker.check_contract_change_review()

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "rule_id": result.rule_id,
                    "passed": result.passed,
                    "evidence": result.evidence,
                    "violations": result.violations,
                    "warnings": result.warnings,
                    "timestamp": result.timestamp,
                    "duration_ms": result.duration_ms,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
