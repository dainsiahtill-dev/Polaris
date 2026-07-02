"""Tests for public contract change review policy wiring."""

from __future__ import annotations

import subprocess
from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_contract_review import ContractReviewChecker
from docs.governance.ci.scripts.contract_change_review_policy import (
    RULE_ID,
    ContractChangeReviewPolicy,
    GitLogResult,
    check_commit_has_review_evidence,
    evaluate_contract_change_review,
)


def _write_source(workspace: Path, relative_path: str, content: str = "# contract\n") -> Path:
    """Write a UTF-8 source fixture."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _run_git(workspace: Path, *args: str) -> None:
    """Run a git command in a temporary fixture repository."""
    subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_git_workspace(workspace: Path, commit_message: str) -> None:
    """Create a minimal git repository with one public contract commit."""
    _write_source(workspace, "polaris/cells/example/public/contracts.py")
    _run_git(workspace, "init")
    _run_git(workspace, "config", "user.email", "tests@example.invalid")
    _run_git(workspace, "config", "user.name", "Polaris Tests")
    _run_git(workspace, "add", ".")
    _run_git(workspace, "commit", "-m", commit_message)


def _reviewed_git_log(_path: Path, _days: int) -> GitLogResult:
    """Return a reviewed synthetic git log result."""
    return GitLogResult(
        stdout="abc12345|2026-07-02 00:00:00 +0000|adr-0071 review contract\n",
    )


def _unreviewed_git_log(_path: Path, _days: int) -> GitLogResult:
    """Return an unreviewed synthetic git log result."""
    return GitLogResult(
        stdout="def67890|2026-07-02 00:00:00 +0000|change contract shape\n",
    )


def test_contract_review_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate entrypoints must match the policy."""
    _init_git_workspace(tmp_path, "change contract shape")

    policy = evaluate_contract_change_review(tmp_path)
    standalone = ContractReviewChecker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_contract_change_review()

    assert policy.passed is False
    assert standalone.passed is False
    assert aggregate.passed is False
    assert policy.rule_id == standalone.rule_id == aggregate.rule_id == RULE_ID
    assert aggregate.violations == list(policy.violations) == standalone.violations
    assert any("no review evidence" in violation for violation in aggregate.violations)


def test_contract_review_detailed_entrypoint_uses_policy_details(tmp_path: Path) -> None:
    """The standalone detailed entrypoint exposes canonical policy details."""
    _init_git_workspace(tmp_path, "adr-0071 review contract")

    details = ContractReviewChecker(tmp_path).check_contract_change_review_detailed()

    assert details["total_files"] == 1
    assert details["files"][0]["path"] == "polaris/cells/example/public/contracts.py"
    assert details["files"][0]["has_review_evidence"] is True
    assert details["files"][0]["review_evidence_type"] == "adr_reference"


def test_contract_review_policy_deduplicates_and_skips_internal_contracts(tmp_path: Path) -> None:
    """Discovery deduplicates broad patterns and excludes non-authoritative contracts."""
    _write_source(tmp_path, "polaris/cells/example/public/contracts.py")
    _write_source(tmp_path, "polaris/cells/example/internal/contracts.py")
    _write_source(tmp_path, "polaris/cells/example/tests/contracts.py")
    _write_source(tmp_path, "polaris/cells/example/fixtures/project/contracts.py")

    policy = ContractChangeReviewPolicy(tmp_path, git_log_reader=_reviewed_git_log)
    contract_files = policy.find_contract_files()

    assert [file_info.relative_path for file_info in contract_files] == ["polaris/cells/example/public/contracts.py"]


def test_contract_review_policy_accepts_reviewed_commit(tmp_path: Path) -> None:
    """A recent public contract commit with review evidence passes."""
    _write_source(tmp_path, "polaris/cells/example/public/contracts.py")

    result = evaluate_contract_change_review(tmp_path, git_log_reader=_reviewed_git_log)

    assert result.passed is True
    assert result.violations == ()
    assert result.evidence[-1] == "All 1 contract change(s) have review evidence"


def test_contract_review_policy_rejects_unreviewed_commit(tmp_path: Path) -> None:
    """A recent public contract commit without review evidence fails."""
    _write_source(tmp_path, "polaris/cells/example/public/contracts.py")

    result = evaluate_contract_change_review(tmp_path, git_log_reader=_unreviewed_git_log)

    assert result.passed is False
    assert result.violations[0] == "Only 0/1 contract changes have review evidence"
    assert any("polaris/cells/example/public/contracts.py" in item for item in result.violations)


def test_contract_review_evidence_detection_accepts_known_markers() -> None:
    """Review evidence detection accepts ADR, verification-card, and review markers."""
    assert check_commit_has_review_evidence("adr-0071 refine contract") == (True, "adr_reference")
    assert check_commit_has_review_evidence("vc-20260416 verify contract") == (True, "vc_reference")
    assert check_commit_has_review_evidence("LGTM contract change") == (True, "review_keyword")
    assert check_commit_has_review_evidence("change contract") == (False, "")
