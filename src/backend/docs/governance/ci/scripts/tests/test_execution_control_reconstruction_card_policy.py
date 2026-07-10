"""Tests for execution-control-plane reconstruction card validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_execution_control_reconstruction_card import (
    ExecutionControlReconstructionCardChecker,
    main,
)
from docs.governance.ci.scripts.execution_control_reconstruction_card_policy import (
    SCHEMA_RELATIVE_PATH,
    TEMPLATE_RELATIVE_PATH,
    evaluate_execution_control_reconstruction_card,
)


def _copy_card_assets(tmp_path: Path) -> None:
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


def _write_card(tmp_path: Path, payload: dict[str, Any], name: str = "card.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def test_reconstruction_card_template_validates_against_schema(tmp_path: Path) -> None:
    """The default check validates the reconstruction-card template."""
    _copy_card_assets(tmp_path)

    result = evaluate_execution_control_reconstruction_card(tmp_path)

    assert result.passed is True
    assert any("JSON schema is valid" in item for item in result.evidence)
    assert any("schema validation passed" in item for item in result.evidence)
    assert "No card paths provided; validated the template only" in result.warnings


def test_reliable_card_requires_filled_fact_chain_and_negative_controls(tmp_path: Path) -> None:
    """A reliable sign-off cannot omit fact-chain and negative-control evidence."""
    _copy_card_assets(tmp_path)
    card = _template_payload(tmp_path)
    card["sign_off"]["architecture_reliable"] = True
    path = _write_card(tmp_path, card)

    result = evaluate_execution_control_reconstruction_card(tmp_path, card_paths=[path])

    assert result.passed is False
    assert any("final_provider_request.evidence_ref" in item for item in result.violations)
    assert any("effect receipt evidence_refs" in item for item in result.violations)
    assert any("projection_mismatch=false" in item for item in result.violations)
    assert any("negative_controls.raw_taskboard_used_as_authority.evidence" in item for item in result.violations)


def test_fitness_runner_exposes_reconstruction_card_rule(tmp_path: Path) -> None:
    """The aggregate fitness runner exposes the reconstruction-card rule."""
    _copy_card_assets(tmp_path)

    policy = evaluate_execution_control_reconstruction_card(tmp_path)
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_execution_control_reconstruction_card()

    assert aggregate.passed == policy.passed
    assert aggregate.rule_id == policy.rule_id
    assert aggregate.violations == list(policy.violations)
    assert aggregate.warnings == list(policy.warnings)
    assert "execution_control_reconstruction_card" in fitness_rule_checker.DEFAULT_RULE_IDS


def test_standalone_checker_matches_policy(tmp_path: Path) -> None:
    """The standalone checker consumes the canonical policy result."""
    _copy_card_assets(tmp_path)

    policy = evaluate_execution_control_reconstruction_card(tmp_path)
    checker = ExecutionControlReconstructionCardChecker(tmp_path).check_execution_control_reconstruction_card()

    assert checker.passed == policy.passed
    assert checker.violations == list(policy.violations)
    assert checker.warnings == list(policy.warnings)


def test_reconstruction_card_cli_json_outputs_one_document(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI JSON mode must be machine-readable."""
    _copy_card_assets(tmp_path)

    exit_code = main(["--workspace", str(tmp_path), "--json"])
    output = capsys.readouterr().out
    payload = yaml.safe_load(output)

    assert exit_code == 0
    assert payload["rule_id"] == "execution_control_reconstruction_card"
    assert payload["passed"] is True
