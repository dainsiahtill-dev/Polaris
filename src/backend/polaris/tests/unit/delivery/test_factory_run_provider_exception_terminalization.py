"""Drive real `_execute_run_with_service` terminalization on provider-class exceptions.

Regression for R49: aiohttp.ClientResponseError (403 quota) escaped a narrow
except and left Factory runs forever RUNNING. This test invokes the shipped
router coroutine with a service double whose execute_stage raises a
provider-shaped Exception; complete_run(success=False) must be called.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryRunStatus as ServiceRunStatus,
)
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.delivery.http.routers import factory as factory_router


def _running_run(run_id: str = "factory_provider_403") -> MagicMock:
    run = MagicMock()
    run.id = run_id
    run.status = ServiceRunStatus.RUNNING
    run.config = MagicMock()
    run.config.stages = ["pm_planning", "chief_engineer_review", "director_dispatch"]
    run.config.name = "provider-block-run"
    run.config.description = "test"
    run.stages_completed = ["pm_planning"]
    run.stages_failed = []
    run.recovery_point = "pm_planning"
    run.created_at = "2026-07-25T00:00:00+00:00"
    run.started_at = "2026-07-25T00:00:01+00:00"
    run.updated_at = run.started_at
    run.completed_at = None
    run.metadata = {
        "current_stage": "chief_engineer_review",
        "last_successful_stage": "pm_planning",
        "last_failed_stage": None,
        "failure": None,
    }
    return run


@pytest.mark.asyncio
async def test_execute_run_with_service_terminalizes_provider_quota_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    run = _running_run()
    provider_exc = Exception(
        "403, message='Forbidden', url='https://api.kimi.com/coding/v1/messages' "
        "You've reached your usage limit for this billing cycle."
    )

    service = MagicMock()
    service.workspace = str(tmp_path)
    service.get_run = AsyncMock(return_value=run)

    async def _apply_mutation(
        run_id: str,
        *,
        operation: str,
        mutation: Any,
        event: dict[str, Any] | None = None,
    ) -> MagicMock:
        del run_id, event
        # Mutations must observe the live run object (same as production apply path).
        mutation(run)
        if operation == "failure_terminalization":
            run.status = ServiceRunStatus.FAILED
        return run

    service.apply_automatic_router_mutation = AsyncMock(side_effect=_apply_mutation)
    service.reconcile_stage_execution_for_reentry = AsyncMock(return_value=run)
    service.complete_run = AsyncMock(return_value=run)
    service.execute_stage = AsyncMock(side_effect=provider_exc)

    async def _noop_persist(**kwargs: Any) -> None:
        del kwargs

    monkeypatch.setattr(factory_router, "_persist_run_summary", _noop_persist)
    monkeypatch.setattr(factory_router, "_resolve_quality_rework_max_cycles", lambda: 0)
    monkeypatch.setattr(
        factory_router,
        "_guard_automatic_router_mutation",
        AsyncMock(side_effect=lambda **kwargs: kwargs["current_run"]),
    )
    monkeypatch.setattr(
        factory_router,
        "_build_stage_context",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        factory_router,
        "_execution_stages_for_run",
        lambda _run, stages: list(stages),
    )

    payload = FactoryStartRequest(
        workspace=str(tmp_path),
        start_from="pm",
        directive="L1-04 true-run provider block fixture",
        loop=False,
        persist_workspace=False,
    )
    state = SimpleNamespace(settings=SimpleNamespace(workspace=str(tmp_path), ramdisk_root=""))

    await factory_router._execute_run_with_service(
        service,
        run.id,
        payload,
        state,
    )

    service.execute_stage.assert_awaited()
    service.complete_run.assert_awaited_once_with(run.id, success=False)
    failure = run.metadata.get("failure") or {}
    assert failure.get("code") == "PROVIDER_QUOTA_OR_AUTH_BLOCKED"
    # First configured stage is attempted; provider error fails the run there.
    assert failure.get("stage") == "pm_planning"
    detail = str(failure.get("detail") or "")
    assert "usage limit" in detail.lower() or "403" in detail
    assert run.metadata.get("last_failed_stage") == "pm_planning"
    # Must not leave a success terminalization path.
    for call in service.complete_run.await_args_list:
        assert call.kwargs.get("success") is False or (call.args and call.args[-1] is False)
