"""HTTP contract tests for exact-run causal diagnosis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.cells.audit.diagnosis.public import AuditDiagnosisResultV1
from polaris.delivery.http import audit_router
from polaris.delivery.http.dependencies import require_auth

RUN_ID = "factory_ec5697b14a71"
WORKSPACE = "/tmp/exact-run-causal-workspace"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(audit_router.router, prefix="/v2")
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[audit_router.get_settings] = lambda: SimpleNamespace(
        workspace=WORKSPACE,
        runtime_base=f"{WORKSPACE}/.polaris/runtime",
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_exact_run_causal_uses_bound_workspace(client: AsyncClient) -> None:
    result = AuditDiagnosisResultV1(
        ok=True,
        status="delivery_verified",
        workspace=WORKSPACE,
        payload={
            "current_status": "DELIVERY_VERIFIED",
            "root_cause_code": "",
            "historical_error_count": 47,
        },
    )
    with patch.object(
        audit_router,
        "query_exact_run_causal_audit",
        new=AsyncMock(return_value=result),
    ) as query:
        response = await client.get(f"/v2/audit/runs/{RUN_ID}/causal?project_id=L3-21")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["report"]["current_status"] == "DELIVERY_VERIFIED"
    assert payload["report"]["historical_error_count"] == 47
    command = query.await_args.args[0]
    assert command.workspace == WORKSPACE
    assert command.factory_run_id == RUN_ID
    assert command.project_id == "L3-21"


@pytest.mark.asyncio
async def test_exact_run_causal_reports_current_failure_with_http_200(client: AsyncClient) -> None:
    result = AuditDiagnosisResultV1(
        ok=False,
        status="control_plane_fail",
        workspace=WORKSPACE,
        payload={
            "current_status": "CONTROL_PLANE_FAIL",
            "root_cause_code": "control_plane.run_ledger.gate_revision_fork_after_runtime_reentry",
            "responsible_cell": "control_plane.run_ledger",
            "retry_boundary": "same_run_quality_gate_only",
        },
        error_code="control_plane.run_ledger.gate_revision_fork_after_runtime_reentry",
        error_message="exact run is blocked",
    )
    with patch.object(
        audit_router,
        "query_exact_run_causal_audit",
        new=AsyncMock(return_value=result),
    ):
        response = await client.get(f"/v2/audit/runs/{RUN_ID}/causal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["report"]["retry_boundary"] == "same_run_quality_gate_only"


@pytest.mark.asyncio
async def test_exact_run_causal_unavailable_returns_503(client: AsyncClient) -> None:
    result = AuditDiagnosisResultV1(
        ok=False,
        status="unavailable",
        workspace=WORKSPACE,
        payload={"factory_run_id": RUN_ID},
        error_code="exact_run_causal_audit_failed",
        error_message="projection unavailable",
    )
    with patch.object(
        audit_router,
        "query_exact_run_causal_audit",
        new=AsyncMock(return_value=result),
    ):
        response = await client.get(f"/v2/audit/runs/{RUN_ID}/causal")

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "exact_run_causal_audit_failed"


@pytest.mark.asyncio
async def test_exact_run_causal_rejects_invalid_run_id(client: AsyncClient) -> None:
    response = await client.get("/v2/audit/runs/bad%24id/causal")
    assert response.status_code == 400
