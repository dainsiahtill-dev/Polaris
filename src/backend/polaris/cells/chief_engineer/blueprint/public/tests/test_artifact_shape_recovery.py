"""Producer recovery must never turn inert syntax into active authority."""

from copy import deepcopy

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    normalize_chief_engineer_portfolio_tool_arguments,
)


def _payload(path="assets/", applicability="not_applicable", owner=None):
    return {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {"provider_declarations": [], "consumer_declarations": []},
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-assets-extra",
                        "path": path,
                        "semantic_role": "assets",
                        "applicability": applicability,
                        "owner_task_id": owner,
                    }
                ],
                "entrypoints": [],
                "verification": [],
            }
        },
    }


@pytest.mark.parametrize(
    "path,want", [("assets/", "assets"), ("data/", "data"), ("optional/resources/", "optional/resources")]
)
def test_ownerless_inert_terminal_slash_recovers_once_without_changing_identity(path, want):
    # Missing producer recovery leaves the strict DTO constructor failure unrecoverable.
    source = _payload(path)
    before = deepcopy(source)
    original = source["project_completion_contract"]["obligations"]["artifacts"][0]
    with pytest.raises(ValueError):
        ArtifactObligationV1(**original)
    recovery = normalize_chief_engineer_portfolio_tool_arguments(source)
    assert recovery.recovered
    assert "normalize_inert_artifact_terminal_separator" in recovery.repair_codes
    repaired = recovery.payload["project_completion_contract"]["obligations"]["artifacts"][0]
    assert repaired == {**original, "path": want}
    assert ArtifactObligationV1(**repaired).path == want
    assert source == before
    assert recovery.source_hash != recovery.recovered_hash
    assert recovery.to_dict()["artifact_path_normalizations"] == [
        {"obligation_id": "ART-assets-extra", "source_path": path, "recovered_path": want}
    ]
    assert not normalize_chief_engineer_portfolio_tool_arguments(recovery.payload).recovered


@pytest.mark.parametrize(
    "path,applicability,owner",
    [
        ("assets/", "required", "TASK-1"),
        ("assets/", "optional", None),
        ("assets/", "not_applicable", "TASK-1"),
        ("/assets/", "not_applicable", None),
        ("../assets/", "not_applicable", None),
        ("./assets/", "not_applicable", None),
        ("assets/../data/", "not_applicable", None),
        ("assets//", "not_applicable", None),
        ("/", "not_applicable", None),
        ("", "not_applicable", None),
        ("assets\\", "not_applicable", None),
        (" assets/", "not_applicable", None),
    ],
)
def test_unsafe_active_or_owned_artifact_is_not_normalized(path, applicability, owner):
    source = _payload(path, applicability, owner)
    with pytest.raises(ValueError):
        ArtifactObligationV1(**source["project_completion_contract"]["obligations"]["artifacts"][0])
    recovery = normalize_chief_engineer_portfolio_tool_arguments(source)
    assert not recovery.recovered
    assert recovery.payload == source


@pytest.mark.parametrize("collision", ["path", "id", "entrypoint"])
def test_inert_recovery_does_not_hide_identity_or_path_collision(collision):
    source = _payload()
    obligations = source["project_completion_contract"]["obligations"]
    if collision == "entrypoint":
        obligations["entrypoints"].append({"obligation_id": "entry", "source_path": "assets"})
    else:
        obligations["artifacts"].append(
            {
                "obligation_id": "ART-assets-extra" if collision == "id" else "other",
                "path": "different" if collision == "id" else "assets",
                "semantic_role": "assets",
                "applicability": "not_applicable",
                "owner_task_id": None,
            }
        )
    recovery = normalize_chief_engineer_portfolio_tool_arguments(source)
    assert not recovery.recovered
    assert recovery.payload == source
