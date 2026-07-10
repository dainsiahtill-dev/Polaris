"""Tests for skeptical architecture review report validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_skeptical_architecture_review import (
    SkepticalArchitectureReviewChecker,
    main,
)
from docs.governance.ci.scripts.skeptical_architecture_review_policy import (
    SCHEMA_RELATIVE_PATH,
    TEMPLATE_RELATIVE_PATH,
    evaluate_skeptical_architecture_review,
)


def _copy_skeptical_assets(tmp_path: Path) -> None:
    repo_root = next(
        parent for parent in Path(__file__).resolve().parents if (parent / "src/backend/docs/governance").is_dir()
    )
    for relative in (SCHEMA_RELATIVE_PATH, TEMPLATE_RELATIVE_PATH):
        source = repo_root / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _template_payload(tmp_path: Path) -> dict[str, Any]:
    payload = yaml.safe_load((tmp_path / TEMPLATE_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_report(tmp_path: Path, payload: dict[str, Any], name: str = "report.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def test_skeptical_review_template_validates_against_schema(tmp_path: Path) -> None:
    """The default check validates the template against the schema."""
    _copy_skeptical_assets(tmp_path)

    result = evaluate_skeptical_architecture_review(tmp_path)

    assert result.passed is True
    assert any("JSON schema is valid" in item for item in result.evidence)
    assert any("schema validation passed" in item for item in result.evidence)
    assert "No report paths provided; validated the template only" in result.warnings


def test_reliable_claim_requires_system_oracle_and_complete_fact_chain(tmp_path: Path) -> None:
    """A report cannot call the architecture reliable without full proof."""
    _copy_skeptical_assets(tmp_path)
    report = _template_payload(tmp_path)
    report["architecture_claim"]["claim_status"] = "proven"
    report["verdict"]["architecture_reliable"] = True
    report["verdict"]["verdict_status"] = "proven"
    path = _write_report(tmp_path, report)

    result = evaluate_skeptical_architecture_review(tmp_path, report_paths=[path])

    assert result.passed is False
    assert any("without fresh completed_verified evidence" in item for item in result.violations)
    assert any("requires proof_level=system_oracle" in item for item in result.violations)
    assert any("requires every fact-chain status present" in item for item in result.violations)


def test_reliable_claim_rejects_true_red_flags(tmp_path: Path) -> None:
    """Even a complete fact chain cannot be marked reliable while a red flag is true."""
    _copy_skeptical_assets(tmp_path)
    report = _template_payload(tmp_path)
    report["architecture_claim"]["claim_status"] = "proven"
    report["architecture_claim"]["proof_level"] = "system_oracle"
    report["fresh_isolated_run"]["completed_verified"] = True
    report["fresh_isolated_run"]["evidence_ref"] = "audits/fresh-run.yaml"
    report["verdict"]["architecture_reliable"] = True
    report["verdict"]["verdict_status"] = "proven"
    report["red_flags"]["task_runtime_run_ledger_qa_disagree"] = "true"
    for node in report["fact_chain"].values():
        if isinstance(node, dict):
            node["status"] = "present"
    path = _write_report(tmp_path, report)

    result = evaluate_skeptical_architecture_review(tmp_path, report_paths=[path])

    assert result.passed is False
    assert any("red flags are true" in item for item in result.violations)


def test_skeptical_review_cli_json_outputs_one_document(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI JSON mode must be machine-readable."""
    _copy_skeptical_assets(tmp_path)

    exit_code = main(["--workspace", str(tmp_path), "--json"])
    output = capsys.readouterr().out
    payload = yaml.safe_load(output)

    assert exit_code == 0
    assert payload["rule_id"] == "skeptical_architecture_review_report"
    assert payload["passed"] is True


def test_checker_matches_policy(tmp_path: Path) -> None:
    """The standalone checker consumes the canonical policy result."""
    _copy_skeptical_assets(tmp_path)

    policy = evaluate_skeptical_architecture_review(tmp_path)
    checker = SkepticalArchitectureReviewChecker(tmp_path).check_skeptical_architecture_review()

    assert checker.passed == policy.passed
    assert checker.violations == list(policy.violations)
    assert checker.warnings == list(policy.warnings)


def test_fitness_runner_uses_skeptical_review_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must expose the skeptical review rule."""
    _copy_skeptical_assets(tmp_path)

    policy = evaluate_skeptical_architecture_review(tmp_path)
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_skeptical_architecture_review()

    assert aggregate.passed == policy.passed
    assert aggregate.rule_id == policy.rule_id
    assert aggregate.violations == list(policy.violations)
    assert aggregate.warnings == list(policy.warnings)
    assert "skeptical_architecture_review" in fitness_rule_checker.DEFAULT_RULE_IDS
