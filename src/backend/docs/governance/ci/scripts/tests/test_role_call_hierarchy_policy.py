"""Tests for direct role-call hierarchy policy wiring."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_no_direct_role_call import NoDirectRoleCallChecker
from docs.governance.ci.scripts.role_call_hierarchy_policy import evaluate_role_call_hierarchy


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a Python source fixture into a temporary workspace."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_role_call_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate entrypoints must match the policy."""
    _write_source(
        tmp_path,
        "polaris/cells/director/execution/service.py",
        "\n".join(
            [
                "from polaris.cells.pm.workflow.public.service import PmService",
                "",
                "def run() -> None:",
                "    PmService()",
                "",
            ]
        ),
    )

    policy = evaluate_role_call_hierarchy(tmp_path)
    standalone = NoDirectRoleCallChecker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_role_call_hierarchy()

    assert policy.passed is False
    assert standalone.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == policy.rule_id == standalone.rule_id
    assert aggregate.violations == list(policy.violations) == standalone.violations
    assert any("Direct peer role import" in violation for violation in aggregate.violations)
    assert any("Direct peer role call" in violation for violation in aggregate.violations)


def test_role_call_policy_allows_same_role_and_runtime_owner(tmp_path: Path) -> None:
    """Same-role imports and roles.runtime API ownership are not peer calls."""
    _write_source(
        tmp_path,
        "polaris/cells/director/execution/public/service.py",
        "\n".join(
            [
                "from polaris.cells.director.execution.service import DirectorService",
                "",
                "def build() -> DirectorService:",
                "    return DirectorService()",
                "",
            ]
        ),
    )
    _write_source(
        tmp_path,
        "polaris/cells/roles/runtime/public/service.py",
        "\n".join(
            [
                "def execute_role(name: str) -> str:",
                "    return name",
                "",
            ]
        ),
    )

    result = evaluate_role_call_hierarchy(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert "No direct peer role calls found in mainline orchestration" in result.evidence


def test_role_call_policy_skips_tests(tmp_path: Path) -> None:
    """Tests are outside the mainline orchestration boundary for this rule."""
    _write_source(
        tmp_path,
        "polaris/cells/director/execution/tests/test_service.py",
        "\n".join(
            [
                "from polaris.cells.pm.workflow.public.service import PmService",
                "",
                "def test_direct_call() -> None:",
                "    PmService()",
                "",
            ]
        ),
    )

    result = evaluate_role_call_hierarchy(tmp_path)

    assert result.passed is True
    assert result.violations == ()


def test_role_call_policy_flags_runtime_calls_outside_runtime_owner(tmp_path: Path) -> None:
    """Direct role runtime calls outside roles.runtime remain governance violations."""
    _write_source(
        tmp_path,
        "polaris/cells/pm/workflow/internal/runner.py",
        "\n".join(
            [
                "from polaris.cells.roles.runtime.public.service import execute_role",
                "",
                "def run() -> object:",
                "    return execute_role('director')",
                "",
            ]
        ),
    )

    result = evaluate_role_call_hierarchy(tmp_path)

    assert result.passed is False
    assert any("Suspicious role runtime call" in violation for violation in result.violations)
