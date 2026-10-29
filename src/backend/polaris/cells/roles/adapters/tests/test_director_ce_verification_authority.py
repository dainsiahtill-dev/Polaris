from typing import Any

from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter
from polaris.cells.roles.adapters.internal.director.contract_verify import resolve_contract_step_verify


def test_ce_exact_verifier_recipe_outranks_pm_and_prompt_guesses() -> None:
    context: dict[str, Any] = {
        "verification_authority": "chief_engineer",
        "verification_commands": ["python custom_gate.py --strict"],
        "pm_task_contract": {"verification_commands": []},
        "construction_step": {"verify": "go test ./..."},
        "acceptance": ["Run npm test"],
    }
    assert resolve_contract_step_verify(context).command == "python custom_gate.py --strict"
    commands = DirectorAdapter._ensure_director_verification_commands(message="Run npm test", context=context)
    assert commands == ["python custom_gate.py --strict"]


def test_empty_ce_verifier_recipe_does_not_fall_back_to_prose_or_language_guess() -> None:
    context: dict[str, Any] = {
        "verification_authority": "chief_engineer",
        "verification_commands": [],
        "language": "go",
        "construction_step": {"verify": "go test ./..."},
        "acceptance": ["Run npm test"],
    }
    resolution = resolve_contract_step_verify(context)
    assert resolution.command == ""
    assert resolution.reason == "chief_engineer_verification_recipe_missing"
    assert DirectorAdapter._ensure_director_verification_commands(message="Run npm test", context=context) == []


def test_runtime_repair_prompt_permits_fresh_reads_before_authorized_edits() -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _build_materialization_quality_repair_message,
    )

    message = _build_materialization_quality_repair_message(
        original_message="Implement and verify answer.py",
        artifact_quality_errors=["declared target file missing 'answer.py'"],
        changed_files=[],
        missing_target_files=["answer.py"],
    )
    assert "Do not read files first" not in message
    assert "exactly one write_file" not in message


def test_functional_generic_helper_does_not_need_task_keywords(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor

    (tmp_path / "core.py").write_text(
        "def clamp(value, minimum, maximum):\n    return max(minimum, min(value, maximum))\n", encoding="utf-8"
    )
    error = DirectorPatchExecutor(str(tmp_path)).validate_generated_output(
        {"subject": "Garden simulation", "description": "Implement garden state transition"}, ["core.py"]
    )
    assert error is None
