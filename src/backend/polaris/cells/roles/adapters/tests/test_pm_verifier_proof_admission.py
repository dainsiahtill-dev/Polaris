"""Invalid verifier commands must reach PM correction before commitment."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import (
    validate_pm_verification_commands,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _contracts


@pytest.mark.parametrize(
    "argv",
    [
        ["node", "--version"],
        ["node", "-e", "process.exit(0)"],
        ["node", "--eval", "process.exit(0);"],
        ["python3", "--help"],
        ["true"],
        ["python3", "-c", "pass"],
    ],
)
def test_pm_rejects_non_proof_verification_commands(argv: list[str]) -> None:
    value = [{"modality": "test", "argv": argv, "cwd": "."}]
    before = copy.deepcopy(value)
    rows, diagnostics = validate_pm_verification_commands(value)
    assert rows == []
    assert diagnostics[0]["path"] == "$.verification_commands[0].argv"
    assert diagnostics[0]["code"] == "verification_command_proof_missing"
    assert "proof-of-work" in diagnostics[0]["message"]
    assert value == before


@pytest.mark.parametrize(
    "argv",
    [
        ["npm", "run", "build"],
        ["pytest", "-v", "tests"],
        ["python3", "-c", "assert 2 + 2 == 4"],
        ["node", "scripts/check.mjs"],
    ],
)
def test_pm_preserves_real_verification_commands(argv: list[str]) -> None:
    value = [{"modality": "test", "argv": argv, "cwd": "."}]
    rows, diagnostics = validate_pm_verification_commands(value)
    assert diagnostics == []
    assert rows == value


@pytest.mark.parametrize("argv", [["node", "--version"], ["node", "-e", "process.exit(0)"]])
def test_pm_quality_surfaces_non_proof_argv_in_retry_feedback(tmp_path: Path, argv: list[str]) -> None:
    candidate = json.loads(_contracts(valid=True))
    candidate["tasks"][0]["verification_commands"] = [{"modality": "test", "argv": argv, "cwd": "."}]
    before = copy.deepcopy(candidate)
    adapter = PMAdapter(workspace=str(tmp_path))
    _, quality = adapter._evaluate_contract_quality(candidate["tasks"], directive="Create model")
    assert quality["ok"] is False
    diagnostics = quality["contract_diagnostics"]
    assert any(d["code"] == "verification_command_proof_missing" for d in diagnostics)
    prompt = adapter._build_pm_retry_message(
        directive="Create model",
        quality=quality,
        previous_output=json.dumps(candidate, ensure_ascii=False),
        validated_candidate=True,
        output_budget_tokens=60000,
    )
    assert "verification_command_proof_missing" in prompt
    assert "proof-of-work" in prompt
    assert candidate == before
