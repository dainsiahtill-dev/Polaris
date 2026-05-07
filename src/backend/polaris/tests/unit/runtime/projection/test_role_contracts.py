"""Tests for shared role projection contracts."""

from __future__ import annotations

import pytest
from polaris.cells.runtime.projection.public.role_contracts import (
    ROLE_TASK_STATUS_VALUES,
    ChiefEngineerBlueprintDetailV1,
    ChiefEngineerBlueprintListV1,
    ChiefEngineerBlueprintSummaryV1,
    RoleTaskContractV1,
)
from pydantic import ValidationError


def test_role_task_contract_preserves_director_task_fields() -> None:
    """Director task rows should validate against one shared role contract."""

    task = RoleTaskContractV1(
        id="PM-1",
        subject="Implement Director TaskBoard",
        description="Expose claimed and blocked task details",
        status="RUNNING",
        priority="HIGH",
        claimed_by="director-1",
        metadata={"pm_task_id": "PM-1"},
        goal="Render real task details",
        acceptance=["shows unclaimed tasks", "shows blocked tasks"],
        target_files=["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
        dependencies=["PM-0"],
        current_file="DirectorTaskPanel.tsx",
        worker="director-1",
        pm_task_id="PM-1",
        blueprint_id="bp-1",
        blueprint_path="runtime/blueprints/bp-1.json",
        runtime_blueprint_path="runtime/blueprints/bp-1.json",
    )

    payload = task.model_dump()
    assert payload["status"] in ROLE_TASK_STATUS_VALUES
    assert payload["metadata"]["pm_task_id"] == "PM-1"
    assert payload["acceptance"] == ["shows unclaimed tasks", "shows blocked tasks"]


def test_role_task_contract_rejects_unknown_fields() -> None:
    """Shared contracts should fail fast when delivery and UI drift."""

    with pytest.raises(ValidationError):
        RoleTaskContractV1(
            id="PM-1",
            subject="Task",
            status="PENDING",
            priority="MEDIUM",
            unexpected_field=True,
        )


def test_chief_engineer_blueprint_contracts_preserve_summary_and_detail() -> None:
    """Chief Engineer list/detail responses should share one explicit shape."""

    summary = ChiefEngineerBlueprintSummaryV1(
        blueprint_id="bp-1",
        title="Runtime Diagnostics",
        summary="Expose NATS and WS state",
        target_files=["src/backend/polaris/delivery/http/v2/runtime_diagnostics.py"],
        raw={"blueprint_id": "bp-1"},
    )
    listing = ChiefEngineerBlueprintListV1(blueprints=[summary], total=1)
    detail = ChiefEngineerBlueprintDetailV1(blueprint_id="bp-1", blueprint={"title": "Runtime Diagnostics"})

    assert listing.model_dump()["blueprints"][0]["blueprint_id"] == "bp-1"
    assert detail.model_dump()["source"] == "runtime/blueprints"
