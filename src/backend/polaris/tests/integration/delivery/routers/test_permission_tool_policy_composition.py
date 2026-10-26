"""Real HTTP permission composition keeps workspace and tool-policy bindings."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.cells.policy.permission.public.service import get_permission_service
from polaris.delivery.http.routers import permissions
from polaris.delivery.http.routers._shared import require_auth
from starlette.requests import Request


@pytest.mark.asyncio
async def test_http_dependency_reuses_real_policy_binding_and_keeps_other_scopes_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # These labels only enable RoleProfile metadata for permission checks.
    # No provider/model is loaded or called and no live configuration is touched.
    labels = tmp_path / "permission-only-bindings.json"
    labels.write_text(
        json.dumps(
            {
                "roles": {
                    role: {"provider_id": "permission-test-only", "model": "not-invoked"}
                    for role in ("pm", "architect", "chief_engineer", "director", "qa", "scout")
                },
                "providers": {"permission-test-only": {"type": "generic", "max_concurrency": 1}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(labels))
    state = SimpleNamespace(settings=SimpleNamespace(workspace=str(tmp_path)))
    monkeypatch.setattr(permissions, "get_state", lambda request: state)
    app = FastAPI()
    app.include_router(permissions.router)
    app.dependency_overrides[require_auth] = lambda: None
    request = Request({"type": "http", "app": app})
    first = await permissions._get_permission_service(request)
    assert first is await permissions._get_permission_service(request)
    assert first is not await get_permission_service(str(tmp_path))
    assert (await first.check_tool_permission("director", "write_file", {"file": "src/a.py", "content": "x"})).allowed
    assert not (await first.check_tool_permission("pm", "write_file", {"file": "src/a.py", "content": "x"})).allowed
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://permission.test") as client:
        response = await client.get("/v2/permissions/roles")
        assert response.status_code == 200
        rows = response.json()["roles"]
        assert next(row for row in rows if row["id"] == "pm")["permission_count"] > 0
        response = await client.get(
            "/v2/permissions/effective", params={"subject_type": "role", "subject_id": "director"}
        )
        assert response.status_code == 200
        assert "file:write:**/*" in response.json()["permissions"]
    state.settings.workspace = str(tmp_path / "other")
    second = await permissions._get_permission_service(request)
    assert second is not first
    assert second.workspace == str(tmp_path / "other")
