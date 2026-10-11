"""Factory transports its existing catalog projection through ordinary PM metadata."""

from types import SimpleNamespace

import pytest
from polaris.cells.factory.pipeline.internal import factory_stage_executor as executor_package
from polaris.cells.factory.pipeline.internal.factory_pm_contract_normalization import build_pm_product_planning_input
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.orchestration.pm_planning.public.contracts import parse_pm_product_planning_input
from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
    _adapter_context_for_task,
    _adapter_input_for_role,
)
from polaris.cells.roles.adapters.tests.test_pm_planning_context import analyzer, product


@pytest.mark.parametrize("catalog", [{}, {"project_id": "ordinary"}, {**product("d"), "test_focus": []}])
def test_invalid_catalog_omits_advisory(catalog):
    assert build_pm_product_planning_input(catalog, planning_directive="d") is None


@pytest.mark.parametrize("deterministic", [False, True])
async def test_real_factory_planning_metadata_transport(tmp_path, monkeypatch, deterministic):
    executor = OrchestrationStageExecutor(tmp_path)
    captured = {}
    catalog = product("unused")
    monkeypatch.setattr(executor, "_read_catalog_contract", lambda: catalog)
    monkeypatch.setattr(executor, "_read_text_artifact", lambda *a, **kw: "")
    monkeypatch.setattr(executor, "_factory_role_evidence_cutoff_port", lambda context: object())

    async def local_authority(port, role, invoke):
        return await invoke()

    monkeypatch.setattr(executor, "_call_with_factory_role_evidence_authority", local_authority)

    class LocalRuntime:
        def __init__(self, workspace) -> None:
            pass

        def reset_records(self, **kwargs):
            return {"ok": True}

    monkeypatch.setattr(executor_package, "TaskRuntimeService", LocalRuntime)

    class Service:
        async def execute_pm_run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status="failed", message="transport-only local control")

    monkeypatch.setattr(executor, "_build_orchestration_service", lambda c: Service())

    async def completion(service, result, **kwargs):
        return result

    monkeypatch.setattr(executor, "_wait_run_completion", completion)
    run = SimpleNamespace(id="factory-local", metadata={})
    original = "实现完整花园行为\n" + "长需求" * 10000
    result = await executor._execute_pm_planning(
        run, {"directive": original, "deterministic_pm_contracts": deterministic}
    )
    assert result.status == "failed"
    directive = captured["options"]["directive"]
    assert original in directive
    metadata = captured["options"]["metadata"]
    assert bool(metadata.get("deterministic_pm_contracts")) is deterministic
    item = parse_pm_product_planning_input(metadata["pm_product_planning_input"], directive=directive)
    assert item is not None and item.test_focus == catalog["test_focus"]
    input_data = _adapter_input_for_role(
        role_id="pm", adapter_task_id="task", role_input=directive, role_metadata=metadata
    )
    context = _adapter_context_for_task(
        run_id="pm-child", workspace=str(tmp_path), task=SimpleNamespace(timeout_seconds=43), role_metadata=metadata
    )
    assert input_data["input"] == directive
    assert context["run_id"] == "pm-child"
    assert analyzer()._analyze_directive_complexity(directive, context)["analysis_source"] == "factory.catalog_contract"
    assert parse_pm_product_planning_input(metadata["pm_product_planning_input"], directive=directive + "stale") is None
