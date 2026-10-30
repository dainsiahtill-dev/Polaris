"""Platform control-plane HTTP read models."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    read_run_ledger_projection,
    read_run_provenance_bundle,
)
from polaris.cells.control_plane.verifier_policy.public import (
    ControlPlaneVerifierPolicyV1Error,
    ReadVerifierPolicyQueryV1,
    UpdateVerifierPolicyCommandV1,
    read_verifier_policy,
    update_verifier_policy,
)
from polaris.delivery.http.workspace import requested_or_active_workspace
from pydantic import BaseModel, Field

from ._shared import get_state, require_auth

router = APIRouter(
    prefix="/v2/control-plane",
    tags=["control-plane"],
    dependencies=[Depends(require_auth)],
)


class VerifierPolicyScriptPayload(BaseModel):
    id: str = ""
    path: str = ""
    modality: str = "custom_script"
    enabled: bool = True
    required: bool = False


class UpdateVerifierPolicyPayload(BaseModel):
    browser_enabled: bool | None = None
    visual_enabled: bool | None = None
    llm_judge_enabled: bool | None = None
    custom_script_enabled: bool | None = None
    required_modalities: list[str] = Field(default_factory=list)
    custom_scripts: list[VerifierPolicyScriptPayload] = Field(default_factory=list)


@router.get("/ledger/projection")
def get_control_plane_ledger_projection(
    request: Request,
    workspace: str = Query(default=""),
    run_id: str = Query(default=""),
    max_runs: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Return the platform Run Ledger projection for the active workspace.

    This is a snapshot query for initial load or explicit refresh. Realtime
    projection updates must still flow through runtime.v2 WebSocket events.
    """

    state = get_state(request)
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    query = ReadRunLedgerProjectionQueryV1(
        workspace=resolved_workspace,
        run_id=run_id,
        max_runs=max_runs,
    )
    return read_run_ledger_projection(query).projection


@router.get("/ledger/provenance")
def get_control_plane_run_provenance_bundle(
    request: Request,
    workspace: str = Query(default=""),
    run_id: str = Query(default=""),
    include_migration_ledgers: bool = Query(default=False),
) -> dict[str, Any]:
    """Return the platform provenance bundle for one run."""

    state = get_state(request)
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    try:
        query = ReadRunProvenanceBundleQueryV1(
            workspace=resolved_workspace,
            run_id=run_id,
            include_migration_ledgers=include_migration_ledgers,
        )
        return read_run_provenance_bundle(query).bundle
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/verifier-policy")
def get_control_plane_verifier_policy(
    request: Request,
    workspace: str = Query(default=""),
) -> dict[str, Any]:
    """Return platform verifier policy for optional QA evidence modalities."""

    state = get_state(request)
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    query = ReadVerifierPolicyQueryV1(workspace=resolved_workspace)
    try:
        return read_verifier_policy(query).policy
    except ControlPlaneVerifierPolicyV1Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/verifier-policy")
def update_control_plane_verifier_policy(
    request: Request,
    payload: UpdateVerifierPolicyPayload,
    workspace: str = Query(default=""),
) -> dict[str, Any]:
    """Persist platform verifier policy for optional QA evidence modalities."""

    state = get_state(request)
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    command = UpdateVerifierPolicyCommandV1(
        workspace=resolved_workspace,
        browser_enabled=payload.browser_enabled,
        visual_enabled=payload.visual_enabled,
        llm_judge_enabled=payload.llm_judge_enabled,
        custom_script_enabled=payload.custom_script_enabled,
        required_modalities=tuple(payload.required_modalities),
        custom_scripts=tuple(item.model_dump() for item in payload.custom_scripts),
    )
    try:
        return update_verifier_policy(command).policy
    except ControlPlaneVerifierPolicyV1Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
