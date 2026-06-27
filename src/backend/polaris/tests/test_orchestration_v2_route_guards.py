"""Guards for generic orchestration v2 routes."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status
from polaris.delivery.http.v2.orchestration import CreateRunRequest, RoleEntrySpecRequest, create_run


@pytest.mark.asyncio
async def test_create_run_blocks_director_role_without_governed_handoff_path(tmp_path) -> None:
    request = CreateRunRequest(
        workspace=str(tmp_path),
        mode="workflow",
        role_entries=[
            RoleEntrySpecRequest(
                role_id="director",
                input="Execute ready tasks",
                scope_paths=[str(tmp_path)],
            )
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_run(request)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail["code"] == "chief_engineer_handoff_required"
    assert exc_info.value.detail["required_chain"] == "PM -> Chief Engineer -> Director"
