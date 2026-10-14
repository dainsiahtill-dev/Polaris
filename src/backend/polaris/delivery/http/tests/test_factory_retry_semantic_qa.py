"""Official same-run retries can enable the existing independent QA capability."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_03 import _Mixin03
from polaris.cells.factory.pipeline.public.types import FactoryControlRequest
from polaris.delivery.http.routers._factory_handlers.mapping import _build_retry_start_request
from polaris.delivery.http.routers._factory_handlers.stage_ops.stage_context import _build_stage_context
from pydantic import ValidationError


def test_same_run_retry_can_enable_semantic_qa_without_changing_contracts() -> None:
    request = FactoryControlRequest(action="retry_phase", qa_semantic_review_required=True)
    run = FactoryRun(
        id="factory-private",
        config=FactoryConfig(name="private", stages=["director_dispatch", "quality_gate"]),
        status=FactoryRunStatus.RECOVERING,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata={"factory_start_request": {"metadata": {"contract_hash": "original", "qa_mode": "physical"}}},
    )
    retried = _build_retry_start_request(
        run, "/home/dains/.polaris/private-fixture", qa_semantic_review_required=request.qa_semantic_review_required
    )
    assert retried.metadata["qa_semantic_review_required"] is True
    assert retried.metadata["contract_hash"] == "original"
    assert run.metadata["factory_start_request"]["metadata"] == {"contract_hash": "original", "qa_mode": "physical"}
    context = _build_stage_context("quality_gate", retried, SimpleNamespace(settings=SimpleNamespace()), run_id=run.id)
    assert _Mixin03._quality_gate_requires_llm_judgement(context) is True


def test_retry_cannot_disable_existing_semantic_qa() -> None:
    with pytest.raises(ValidationError):
        FactoryControlRequest(action="retry_phase", qa_semantic_review_required=False)


@pytest.mark.parametrize("container", [None, "metadata", "qa_input"])
def test_existing_semantic_qa_selection_is_consistent(container: str | None) -> None:
    value = {"qa_semantic_review_required": True}
    context = {container: value} if container else value
    assert _Mixin03._quality_gate_requires_llm_judgement(context) is True
    assert _Mixin03._quality_gate_requires_llm_judgement({"metadata": {}}) is False
