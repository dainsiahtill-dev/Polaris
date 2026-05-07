"""Chief Engineer v2 delivery routes."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.runtime.projection.public.role_contracts import (
    ChiefEngineerBlueprintDetailV1,
    ChiefEngineerBlueprintListV1,
    ChiefEngineerBlueprintSummaryV1,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth

router = APIRouter(tags=["chief-engineer"])

_BLUEPRINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ChiefEngineerBlueprintSummary(ChiefEngineerBlueprintSummaryV1):
    """Chief Engineer blueprint summary bound to the shared contract."""


class ChiefEngineerBlueprintListResponse(ChiefEngineerBlueprintListV1):
    """Chief Engineer blueprint list response bound to the shared contract."""


class ChiefEngineerBlueprintDetailResponse(ChiefEngineerBlueprintDetailV1):
    """Chief Engineer blueprint detail response bound to the shared contract."""


def _validate_blueprint_id(blueprint_id: str) -> str:
    token = str(blueprint_id or "").strip()
    if not _BLUEPRINT_ID_RE.fullmatch(token):
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_ID",
            message="invalid blueprint id",
        )
    return token


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(item.get("path") or item.get("file") or item.get("name") or item.get("id") or "").strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _blueprint_id_from_payload(payload: dict[str, Any], fallback: str) -> str:
    return (
        str(payload.get("blueprint_id") or payload.get("id") or payload.get("task_id") or fallback).strip() or fallback
    )


def _blueprint_summary(payload: dict[str, Any], fallback_id: str) -> ChiefEngineerBlueprintSummary:
    blueprint_id = _blueprint_id_from_payload(payload, fallback_id)
    return ChiefEngineerBlueprintSummary(
        blueprint_id=blueprint_id,
        title=str(payload.get("title") or payload.get("task_title") or payload.get("subject") or blueprint_id).strip(),
        summary=str(payload.get("summary") or payload.get("goal") or payload.get("description") or "").strip(),
        status=str(payload.get("status")).strip() if payload.get("status") is not None else None,
        target_files=_string_list(
            payload.get("target_files")
            or payload.get("scope_paths")
            or payload.get("files")
            or payload.get("affected_files")
        ),
        updated_at=str(payload.get("updated_at") or payload.get("created_at") or "").strip() or None,
        raw=payload,
    )


def _persistence_for_request(request: Request) -> BlueprintPersistence:
    state = get_state(request)
    return BlueprintPersistence(str(state.settings.workspace))


@router.get(
    "/chief-engineer/blueprints",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintListResponse,
)
def list_chief_engineer_blueprints(request: Request) -> ChiefEngineerBlueprintListResponse:
    """List persisted Chief Engineer blueprints for the active workspace."""

    persistence = _persistence_for_request(request)
    rows: list[ChiefEngineerBlueprintSummary] = []
    for blueprint_id in persistence.list_all():
        payload = persistence.load(blueprint_id)
        if isinstance(payload, dict):
            rows.append(_blueprint_summary(payload, blueprint_id))

    rows.sort(key=lambda item: item.updated_at or item.blueprint_id, reverse=True)
    return ChiefEngineerBlueprintListResponse(blueprints=rows, total=len(rows))


@router.get(
    "/chief-engineer/blueprints/{blueprint_id}",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintDetailResponse,
)
def get_chief_engineer_blueprint(
    request: Request,
    blueprint_id: str,
) -> ChiefEngineerBlueprintDetailResponse:
    """Load one persisted Chief Engineer blueprint by id."""

    safe_blueprint_id = _validate_blueprint_id(blueprint_id)
    payload = _persistence_for_request(request).load(safe_blueprint_id)
    if not isinstance(payload, dict):
        raise StructuredHTTPException(
            status_code=404,
            code="BLUEPRINT_NOT_FOUND",
            message="blueprint not found",
        )
    return ChiefEngineerBlueprintDetailResponse(
        blueprint_id=_blueprint_id_from_payload(payload, safe_blueprint_id),
        blueprint=payload,
    )
