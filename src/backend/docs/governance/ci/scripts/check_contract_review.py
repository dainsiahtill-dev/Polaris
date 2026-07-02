"""Check contract_change_requires_review governance.

The canonical policy lives in ``contract_change_review_policy``. This module
preserves the standalone CLI and compatibility checker class without duplicating
contract discovery, git history, or review-evidence rules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent

try:
    from docs.governance.ci.scripts.contract_change_review_policy import (
        DEFAULT_LOOKBACK_DAYS,
        RULE_ID,
        ContractChangeReviewPolicy,
        ContractFileInfo,
        GitLogResult,
        check_commit_has_review_evidence,
        evaluate_contract_change_review,
    )
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
except ModuleNotFoundError:
    sys.path.insert(0, str(SCRIPT_DIR))
    from contract_change_review_policy import (
        DEFAULT_LOOKBACK_DAYS,
        RULE_ID,
        ContractChangeReviewPolicy,
        ContractFileInfo,
        GitLogResult,
        check_commit_has_review_evidence,
        evaluate_contract_change_review,
    )
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


class ContractReviewChecker(FitnessRuleChecker):
    """Compatibility checker for public contract review requirements."""

    def __init__(self, workspace: Path | None = None, days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        """Initialize the checker for a backend workspace root."""
        super().__init__(workspace)
        self.days = days
        self._policy = ContractChangeReviewPolicy(self.workspace, days=days)

    def find_contract_files(self) -> list[ContractFileInfo]:
        """Find public contract files through the canonical policy."""
        return self._policy.find_contract_files()

    def _run_git_log(self, file_path: Path, since_days: int) -> tuple[str, str, int]:
        """Run git log through the canonical policy infrastructure adapter."""
        result = self._policy.run_git_log(file_path, since_days)
        return result.stdout, result.stderr, result.returncode

    def _check_commit_has_review_evidence(self, commit_message: str) -> tuple[bool, str]:
        """Check commit review evidence through the canonical policy."""
        return check_commit_has_review_evidence(commit_message)

    def check_contract_change_review(self) -> FitnessCheckResult:
        """Check that recent public contract changes have review evidence."""
        policy_result = evaluate_contract_change_review(self.workspace, days=self.days)
        result = FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )
        result.details = policy_result.details
        return result

    def check_contract_change_review_detailed(self) -> dict[str, Any]:
        """Return detailed file-level contract review evidence."""
        policy_result = evaluate_contract_change_review(self.workspace, days=self.days)
        return policy_result.details or {"total_files": 0, "files": []}

    def check(self) -> FitnessCheckResult:
        """Return the public contract review fitness result."""
        return self.check_contract_change_review()


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Check that contract changes have review evidence.")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Number of days to look back for contract changes (default: {DEFAULT_LOOKBACK_DAYS})",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--detailed", action="store_true", help="Show detailed information for all contract files")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Backend workspace root path (default: auto-detect)",
    )
    return parser.parse_args()


def _result_to_json(result: FitnessCheckResult) -> str:
    """Serialize a fitness result for command-line JSON output."""
    return json.dumps(
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


def _print_detailed(details: dict[str, Any]) -> None:
    """Print detailed contract review status for humans."""
    print(f"Total contract files: {details['total_files']}\n")
    for file_info in details["files"]:
        if file_info["has_recent_changes"]:
            status = f"{GREEN}[REVIEWED]{RESET}" if file_info["has_review_evidence"] else f"{RED}[NO REVIEW]{RESET}"
        elif file_info.get("git_error"):
            status = f"{RED}[GIT ERROR]{RESET}"
        else:
            status = f"{YELLOW}[NO CHANGES]{RESET}"

        print(f"{status} {file_info['path']}")
        if file_info["has_recent_changes"]:
            print(f"       Cell: {file_info['cell_id']}")
            print(f"       Commit: {file_info.get('commit_hash', 'N/A')[:8]}")
            print(f"       Message: {file_info.get('commit_message', 'N/A')[:70]}...")
            if file_info.get("review_evidence_type"):
                print(f"       Evidence: {file_info['review_evidence_type']}")
        if file_info.get("git_error"):
            print(f"       Error: {file_info['git_error']}")


def main() -> int:
    """Run the contract review check and return a process exit code."""
    args = _parse_args()
    checker = ContractReviewChecker(workspace=args.workspace or REPO_ROOT, days=args.days)

    if args.detailed:
        details = checker.check_contract_change_review_detailed()
        if args.json:
            print(json.dumps(details, ensure_ascii=False, indent=2))
        else:
            _print_detailed(details)
        return 0

    result = checker.check_contract_change_review()
    if args.json:
        print(_result_to_json(result))
    else:
        print(result.format())
    return 0 if result.passed else 1


__all__ = [
    "RULE_ID",
    "ContractChangeReviewPolicy",
    "ContractFileInfo",
    "ContractReviewChecker",
    "GitLogResult",
]


if __name__ == "__main__":
    sys.exit(main())
