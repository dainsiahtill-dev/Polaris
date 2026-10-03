"""Derived model guidance is not an authorization fallback."""

from __future__ import annotations

from copy import deepcopy

import pytest
from polaris.kernelone.audit.task_write_guidance import project_task_write_guidance


@pytest.mark.parametrize(
    "authorization,targets,writes,references",
    [
        ({"allowed_write_paths": []}, ["main.go"], [], ["main.go"]),
        ({"allowed_write_paths": ["src/"]}, ["src/main.go", "main.go"], ["src/main.go"], ["main.go"]),
        (
            {"allowed_write_paths": ["src/**/*.go"]},
            ["src/main.go", "src/deep/lib.go", "README.md"],
            ["src/main.go", "src/deep/lib.go"],
            ["README.md"],
        ),
        ({"allowed_write_paths": ["main.go"]}, ["./main.go", "main_test.go"], ["main.go"], ["main_test.go"]),
    ],
)
def test_guidance_projects_scope_without_changing_authority(authorization, targets, writes, references) -> None:
    envelope = {"authorization": authorization}
    original = deepcopy(envelope)

    guidance = project_task_write_guidance(envelope, targets)

    assert guidance["write_targets"] == writes
    assert guidance["reference_only_targets"] == references
    assert guidance["inventory_is_not_write_authority"] is True
    assert envelope == original


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"authorization": {}},
        {"authorization": None},
        {"authorization": {"allowed_write_paths": "main.go"}},
        {"authorization": {"allowed_write_paths": ["../outside.go"]}},
        {"authorization": {"allowed_write_paths": ["/outside.go"]}},
        {"authorization": {"allowed_write_paths": ["C:/outside.go"]}},
        {"authorization": {"allowed_write_paths": [True]}},
        {"authorization": {"allowed_write_paths": ["main.go"], "required_write_targets": ["other.go"]}},
    ],
)
def test_guidance_rejects_malformed_authority_and_outside_required_effects(envelope) -> None:
    with pytest.raises(ValueError, match="task_write_guidance"):
        project_task_write_guidance(envelope, ["main.go"])
