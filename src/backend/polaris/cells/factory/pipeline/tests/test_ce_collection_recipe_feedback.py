"""Collection-aware draft feedback preserves authenticated requirements."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import ChiefEngineerBlueprintErrorV1, VerificationCommandAuthorityV1
from polaris.cells.chief_engineer.blueprint.public.service._delegated_recipes import prepare_delegated_recipes
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor


def _command(workspace: Path) -> Any:
    return SimpleNamespace(
        workspace=str(workspace),
        tasks=(
            SimpleNamespace(
                task_id="TASK-1", planning_contract_version="pm.task_intent.v1", verification_authority="chief_engineer"
            ),
        ),
    )


@pytest.mark.parametrize(
    ("command_text", "allowed"), [("npm test", "test"), ("npm run build", "build"), ("go test ./...", "test")]
)
def test_entrypoint_mismatch_identifies_collection_and_preserves_rejection(
    tmp_path: Path, command_text: str, allowed: str
) -> None:
    requirements: dict[str, Any] = {
        "obligations": {
            "verification": [],
            "entrypoints": [
                {
                    "obligation_id": "DRAFT-ONLY",
                    "owner_task_id": "TASK-1",
                    "applicability": "required",
                    "command": command_text,
                }
            ],
        }
    }
    original = deepcopy(requirements)
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        prepare_delegated_recipes(_command(tmp_path), requirements, SimpleNamespace(verification_command_authority=()))
    error = caught.value
    assert error.code == "invalid_chief_engineer_verification_recipe"
    diagnostic = error.details["verification_recipe_diagnostics"][0]
    assert diagnostic["profile_feedback"]["error_code"] == "verifier_modality_mismatch"
    assert diagnostic["collection"] == "entrypoints"
    assert diagnostic["source_path"] == ["obligations", "entrypoints", 0]
    assert diagnostic["expected_modality"] == "entrypoint"
    assert diagnostic["allowed_recipe_modalities"] == [allowed]
    assert diagnostic["allowed_recipe_collections"] == ["verification"]
    assert error.details["repair_operation"] == "entrypoint_upsert"
    assert requirements == original


def test_shared_guidance_separates_unaccepted_rows_from_required_authority(tmp_path: Path) -> None:
    binding = VerificationCommandAuthorityV1(task_id="TASK-1", modality="test", argv=("npm", "test"), cwd=".")
    text = _executor(tmp_path)._chief_engineer_trusted_recipe_guidance(
        tasks=_command(tmp_path).tasks, authority_carrier=SimpleNamespace(verification_command_authority=(binding,))
    )
    assert "original PM/user requirements" in text
    assert "accepted CE obligations" in text
    assert "required in an unaccepted draft" in text
    assert "remove or relocate" in text
    assert "obligations.entrypoints" in text
    assert "obligations.verification" in text
    assert "owner_task_id" in text and "obligation_id" in text
    assert "current result schema" in text
    assert "required verification" in text


def test_correcting_draft_collection_cannot_drop_authenticated_test_recipe(tmp_path: Path) -> None:
    binding = VerificationCommandAuthorityV1(task_id="TASK-1", modality="test", argv=("npm", "test"), cwd=".")
    carrier = SimpleNamespace(verification_command_authority=(binding,))
    rows: dict[str, Any] = {
        "obligations": {
            "verification": [],
            "entrypoints": [
                {"obligation_id": "APP", "owner_task_id": "TASK-1", "applicability": "required", "command": "npm start"}
            ],
        }
    }
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="explicit user command constraint is missing"):
        prepare_delegated_recipes(_command(tmp_path), rows, carrier)
    rows["obligations"]["verification"].append(
        {
            "obligation_id": "TEST",
            "owner_task_id": "TASK-1",
            "applicability": "required",
            "modality": "test",
            "argv": ["npm", "test"],
            "cwd": ".",
            "command_authority_hash": binding.authority_hash,
        }
    )
    prepared, authorities = prepare_delegated_recipes(_command(tmp_path), rows, carrier)
    assert prepared["obligations"]["verification"][0]["command_authority_hash"] == binding.authority_hash
    assert binding in authorities


def test_entrypoint_profile_can_remain_a_verification_recipe(tmp_path: Path) -> None:
    requirements: dict[str, Any] = {
        "obligations": {
            "verification": [
                {
                    "obligation_id": "RUN",
                    "owner_task_id": "TASK-1",
                    "applicability": "required",
                    "modality": "test",
                    "argv": ["npm", "start"],
                    "cwd": ".",
                    "command_authority_hash": None,
                }
            ],
            "entrypoints": [],
        }
    }
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        prepare_delegated_recipes(_command(tmp_path), requirements, SimpleNamespace(verification_command_authority=()))
    diagnostic = caught.value.details["verification_recipe_diagnostics"][0]
    assert diagnostic["collection"] == "verification"
    assert diagnostic["allowed_recipe_modalities"] == ["entrypoint"]
    assert diagnostic["allowed_recipe_collections"] == ["verification", "entrypoints"]
    requirements["obligations"]["verification"][0]["modality"] = "entrypoint"
    prepared, _ = prepare_delegated_recipes(
        _command(tmp_path), requirements, SimpleNamespace(verification_command_authority=())
    )
    assert prepared["obligations"]["verification"][0]["modality"] == "entrypoint"
