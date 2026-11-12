"""Controlled physical requests; fixtures prove integration, never native delivery."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_event_chain import (
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
)
from polaris.cells.factory.pipeline.internal.factory_pm_contract_normalization import build_pm_product_planning_input
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority._source import (
    _PublicFactoryRoleEvidenceFactStream,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_source_resolver import (
    CanonicalFactoryRoleEvidenceSourceAuthority,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import _authority
from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
    _adapter_context_for_task,
    _adapter_input_for_role,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
    _contracts,
    _isolated_external_delivery as _isolated_external_delivery,
    _verifier_revision_response,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
from polaris.cells.roles.profile.public.service import RoleProfileRegistry
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog


def _planning_fake_capacity(monkeypatch, *, output_cap=65536, context_window=1000000):
    original = ModelCatalog.resolve

    def resolve(self, provider_id, model, provider_cfg=None):
        if provider_id == "local-provider" and model == "local-model":
            provider_cfg = {
                "type": "openai_compat",
                "model_specific": {model: {"max_context_tokens": context_window, "max_output_tokens": output_cap}},
            }
        return original(self, provider_id, model, provider_cfg)

    monkeypatch.setattr(ModelCatalog, "resolve", resolve)


def _fixture(name: str) -> str:
    return (
        Path(__file__).resolve().parents[7]
        / ".superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-planning-capacity/fixtures"
        / name
    ).read_text(encoding="utf-8")


def _synthetic_garden_plan() -> str:
    """New model fixture; no claim that this is a historical provider body."""
    tasks = []
    for identity, title, paths, contexts, dependencies, phase, commands in [
        (
            "CORE",
            "实现萤火虫花园领域规则",
            ["src/garden/types.ts", "src/garden/dance.ts"],
            [],
            [],
            "implementation",
            [],
        ),
        (
            "SCENE",
            "实现场景渲染入口与运行配置",
            ["src/browser.ts", "index.html", "package.json", "tsconfig.json", "README.md", "data/garden.json"],
            ["src/garden/types.ts", "src/garden/dance.ts"],
            ["CORE"],
            "implementation",
            [
                {"modality": "environment_prep", "argv": ["npm", "install"], "cwd": "."},
                {"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."},
                {"modality": "entrypoint", "argv": ["npm", "run", "start"], "cwd": "."},
            ],
        ),
        (
            "TEST",
            "验证灯光舞蹈与运行入口",
            ["tests/garden.test.ts"],
            ["src/garden/dance.ts", "src/browser.ts", "index.html", "data/garden.json"],
            ["CORE", "SCENE"],
            "verification",
            [{"modality": "test", "argv": ["npm", "test"], "cwd": "."}],
        ),
    ]:
        tasks.append(
            {
                "id": identity,
                "title": title,
                "goal": "完成 TypeScript 花园产品行为，firefly/flower/moon/humidity 联合决定实时灯光舞蹈",
                "description": "产品实现采用明确领域模型、规则引擎与浏览器交互；验证正常、边界及非法输入，不以文件存在替代行为验收。",
                "scope": ", ".join(paths),
                "scope_paths": paths,
                "target_files": paths,
                "context_files": contexts,
                "steps": [
                    "定义 firefly 发光、flower 情绪、moon 月相与 humidity 湿度输入及可观察输出",
                    "实现花朵情绪与月相决定舞蹈并允许用户调整湿度",
                    "验证输入边界及非法值，说明如何运行入口与核心规则测试",
                ],
                "acceptance": [
                    "正常 moon 月相与 flower 情绪改变 firefly 舞蹈，humidity 影响亮度",
                    "边界湿度0与100均有确定行为，非法值明确报错",
                    "声明的验证命令能发现错误的灯光规则、场景和入口行为",
                ],
                "phase": phase,
                "depends_on": dependencies,
                "assigned_to": "Director",
                "execution_backend": "code_edit",
                "verification_commands": commands,
            }
        )
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


@pytest.mark.parametrize(
    "case,reason,budget",
    [
        ("historical_incomplete_legal", "max_tokens", 4000),
        ("historical_incomplete_invalid", "max_tokens", 4000),
        ("incomplete_end_turn", "end_turn", 3072),
        ("incomplete_unknown", None, 2048),
        ("incomplete_nested", None, 1536),
        ("complete_valid_max_tokens", "max_tokens", 4000),
        ("complete_bad_bounded", "end_turn", 4000),
        ("normal_entry_bound", "max_tokens", 4000),
        ("normal_entry_tampered", "max_tokens", 4000),
        ("default_initial_and_correction", "max_tokens", 60000),
        ("override_higher", "max_tokens", 64000),
        ("model_cap_rejected", "max_tokens", 60000),
        ("context_window_rejected", "max_tokens", 60000),
    ],
)
async def test_real_factory_kernel_gateway_capacity_and_strict_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, reason: str, budget: int
) -> None:
    """The controlled PM planning capacity keeps its current contract.

    The scenario this test drove reached the retired legacy role session
    entry to observe the physical provider requests, the request freezes and
    the attempt budget they consumed. That entry is closed now: a new
    engineering run has exactly one owner, the engineering run service. The
    planning capacity contract stays live: the planning output budget is
    clamped to the model window instead of being refused, the original
    requirement stays in the request, and an incomplete draft still needs one
    complete re-answer.
    """
    from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
        _pm_adapter_model,
        _pm_revision_reply,
    )
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id=f"retired-pm-capacity-{case}",
        workspace=str(tmp_path),
    )
    _planning_fake_capacity(
        monkeypatch,
        output_cap=32768 if case == "model_cap_rejected" else 65536,
        context_window=60032 if case == "context_window_rejected" else 1000000,
    )
    register = RoleProfileRegistry.register

    def register_bounded(registry, profile):
        if profile.role_id == "pm":
            profile = replace(profile, context_policy=replace(profile.context_policy, max_context_tokens=8000))
        return register(registry, profile)

    monkeypatch.setattr(RoleProfileRegistry, "register", register_bounded)
    directive = (
        _fixture("HISTORICAL_DIRECTIVE.txt")
        if case.startswith("historical")
        else "Create a Python domain model library with unit tests. Preserve complete requirements."
    )
    catalog = json.loads(_fixture("HISTORICAL_CATALOG.json"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    normal_entry = case.startswith("normal_entry")
    original_requirement = _fixture("HISTORICAL_DIRECTIVE.txt") + " \t\n" if normal_entry else directive
    if normal_entry:
        executor = OrchestrationStageExecutor(workspace)
        monkeypatch.setattr(executor, "_read_text_artifact", lambda *a, **kw: "")
        directive = executor._build_pm_planning_directive(original_requirement)
        assert directive.endswith(" \t\n")
    store = FactoryStore(workspace / ".polaris/runtime/factory")
    run = FactoryRun(
        id="factory-capacity",
        config=FactoryConfig(name="Controlled capacity integration", description=directive, stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-09T10:49:11+00:00",
        metadata={"current_stage": "pm_planning", "factory_stage_in_flight": True},
    )
    admitted = build_factory_run_admitted_event(
        FactoryRunAdmissionV1(
            factory_run_id=run.id, created_at=run.created_at, name=run.config.name, description=directive
        )
    )
    admitted.update(run_id=run.id, event_id="capacity-admission", timestamp=run.created_at)
    await store.append_authoritative_event(run.id, admitted)
    await store.save_run(run)
    source = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=workspace, factory_store=store, factory_event_loader=store._read_authoritative_events_read_only_sync
    )
    port, _, _, _facts = _authority(
        tmp_path=tmp_path,
        factory_run_id=run.id,
        stage="pm_planning",
        run=run,
        resolver=source,
        facts=_PublicFactoryRoleEvidenceFactStream(),
        run_loader=lambda: store.get_run(run.id),
    )
    valid = _synthetic_garden_plan() if case.startswith("historical") or normal_entry else _contracts(valid=True)
    incomplete = _fixture("initial-response.txt") if case.startswith("historical") else '{"tasks":['
    initial = (
        _contracts(valid=False)
        if case == "complete_bad_bounded"
        else valid
        if case == "complete_valid_max_tokens" or normal_entry
        else incomplete
    )
    outputs: list[Any] = [{"role": "assistant", "content": initial}]
    if case != "complete_valid_max_tokens" and not normal_entry:
        outputs.append(
            _pm_revision_reply(_verifier_revision_response)
            if case == "complete_bad_bounded"
            else {"role": "assistant", "content": '{"tasks":[' if case == "historical_incomplete_invalid" else valid}
        )
    adapter = PMAdapter(workspace=str(workspace))
    calls: list[dict[str, Any]] = []
    _pm_adapter_model(adapter, outputs, calls)
    metadata = {"generation_outcome": {"forged": True}, "max_tokens": 1024}
    if case.startswith("historical") or normal_entry:
        metadata["pm_product_planning_input"] = build_pm_product_planning_input(
            catalog, planning_directive=directive, title="发光昆虫花园模拟器"
        )
    if case == "normal_entry_tampered":
        metadata["pm_product_planning_input"]["directive_sha256"] = "0" * 64
    context = {
        "run_id": "pm-capacity-child",
        "timeout_seconds": 2100,
        "metadata": metadata,
        "llm_max_tokens": budget,
        "max_tokens": 1536,
    }
    if case == "default_initial_and_correction":
        context.pop("llm_max_tokens")
        context.pop("max_tokens")
        metadata.pop("max_tokens")
    if case in {"default_initial_and_correction", "override_higher"}:
        import time

        call_timeout = 800 if case == "override_higher" else 750
        context["llm_call_timeout_seconds"] = call_timeout
        context["timeout_seconds"] = 2 * call_timeout + 30
        metadata["pm_planning_deadline_epoch_seconds"] = time.time() + context["timeout_seconds"]
    if case == "incomplete_nested":
        context["llm_max_tokens"] = "invalid"
        context.pop("max_tokens")
        metadata.pop("max_tokens")
        metadata["task_execution_contract"] = {"context_budget": {"max_tokens": budget}}
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        if normal_entry:
            mapped_input = _adapter_input_for_role(
                role_id="pm", adapter_task_id="pm-capacity-task", role_input=directive, role_metadata=metadata
            )
            mapped_context = _adapter_context_for_task(
                run_id=context["run_id"],
                workspace=str(workspace),
                task=type("LocalTask", (), {"timeout_seconds": 2100})(),
                role_metadata=metadata,
            )
            result = await adapter.execute("pm-capacity-task", mapped_input, {**mapped_context, **context})
        else:
            result = await adapter._run_pm_stage("pm-capacity-task", directive, {"llm_max_tokens": 99999}, context)
    # The original requirement and the prospective output allowance stay in
    # the request: the capacity guard never drops the pinned context.
    assert calls
    assert directive in calls[0]["message"]
    assert "Prospective output allowance:" in calls[0]["message"]
    if case in {"model_cap_rejected", "context_window_rejected"}:
        # A planning output budget that does not fit the window is clamped to
        # the model window instead of refusing the request.
        assert len(calls) >= 1
        assert "Prospective output allowance: 60000 tokens" in calls[0]["message"]
    assert result["success"] is (case != "historical_incomplete_invalid"), result
    count = 1 if case == "complete_valid_max_tokens" or normal_entry else 2
    # An unusable correction ends the bounded loop; it never admits a prefix.
    assert len(calls) >= count, calls
    for call in calls:
        assert call["context"]["run_id"] == "pm-capacity-child"
    if case in {"default_initial_and_correction", "override_higher"}:
        assert [call["context"]["llm_call_timeout_seconds"] for call in calls] == [call_timeout, call_timeout]
    if "incomplete" in case:
        retry = calls[1]["message"]
        assert "Previous output excerpt" not in retry
        assert "Incomplete/unparseable candidate" in retry
        assert not any(s["code"] == "pm.contracts.revision_base" for s in result["quality_gate"]["signals"])
    elif case == "complete_bad_bounded":
        assert any(s["code"] == "pm.contracts.revision_base" for s in result["quality_gate"]["signals"])
    if case == "historical_incomplete_invalid":
        assert result["tasks_created"] == 0
        assert adapter.task_runtime.list_observable_task_rows() == []
    if normal_entry:
        advisory = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.meta.planning")
        assert advisory["analysis_source"] == (
            "factory.catalog_contract" if case == "normal_entry_bound" else "directive"
        )
        assert original_requirement in calls[0]["message"]
