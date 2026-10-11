"""Controlled physical requests; fixtures prove integration, never native delivery."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

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
    _local_runtime,
    _verifier_revision_response,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
from polaris.cells.roles.kernel.public.generation_outcome import bind_role_generation_outcome
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    query_factory_provider_attempt_lifecycle_replay,
)
from polaris.cells.roles.profile.public.service import RoleProfileRegistry
from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling
from polaris.kernelone.llm.engine.executor import AIExecutor
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
async def test_real_factory_kernel_gateway_capacity_and_strict_recovery(tmp_path, monkeypatch, case, reason, budget):
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
    outputs = [{"role": "assistant", "content": initial}]
    if case != "complete_valid_max_tokens" and not normal_entry:
        outputs.append(
            _verifier_revision_response
            if case == "complete_bad_bounded"
            else {"role": "assistant", "content": '{"tasks":[' if case == "historical_incomplete_invalid" else valid}
        )
    runtime, freezes, wires = _local_runtime(workspace, monkeypatch, outputs)
    engine_responses = []
    original_invoke = AIExecutor._execute_invoke

    async def invoke(self, *args, **kwargs):
        response = await original_invoke(self, *args, **kwargs)
        engine_responses.append(response)
        return response

    monkeypatch.setattr(AIExecutor, "_execute_invoke", invoke)
    local_http = _http_pooling.bounded_http_post
    native_reasons = []

    def post(*args, **kwargs):
        response = local_http(*args, **kwargs)
        body = json.loads(response.content.decode("utf-8"))
        native_reason = reason if len(wires) == 1 else "end_turn"
        native_reasons.append(native_reason)
        if native_reason is None:
            body["choices"][0].pop("finish_reason", None)
        else:
            body["choices"][0]["finish_reason"] = native_reason
        response._content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return response

    monkeypatch.setattr(_http_pooling, "bounded_http_post", post)
    adapter = PMAdapter(workspace=str(workspace))
    authority = port.mint_authority_binding("pm")
    metadata = {"generation_outcome": {"forged": True}, "max_tokens": 1024}
    if case.startswith("historical") or normal_entry:
        metadata["pm_product_planning_input"] = build_pm_product_planning_input(
            catalog, planning_directive=directive, title="发光昆虫花园模拟器"
        )
    if case == "normal_entry_tampered":
        metadata["pm_product_planning_input"]["directive_sha256"] = "0" * 64
    context = {
        "run_id": "pm-capacity-child",
        "timeout_seconds": 43,
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
    with bind_factory_role_evidence_authority(authority):
        if normal_entry:
            mapped_input = _adapter_input_for_role(
                role_id="pm", adapter_task_id="pm-capacity-task", role_input=directive, role_metadata=metadata
            )
            mapped_context = _adapter_context_for_task(
                run_id=context["run_id"],
                workspace=str(workspace),
                task=type("LocalTask", (), {"timeout_seconds": 43})(),
                role_metadata=metadata,
            )
            result = await adapter.execute("pm-capacity-task", mapped_input, {**mapped_context, **context})
        else:
            result = await adapter._run_pm_stage("pm-capacity-task", directive, {"llm_max_tokens": 99999}, context)
    evidence = {
        "case": case,
        "responses": "copied historical first response; all success/control responses newly authored synthetic"
        if case.startswith("historical")
        else "new synthetic controls",
        "wires": wires,
        "native_reasons": native_reasons,
        "result": result,
        "commands": [asdict(c) for c in runtime._test_commands],
        "physical_timeouts": runtime._test_timeouts,
        "outcomes": [dict(r.metadata).get("generation_outcome") for r in runtime._test_results],
        "freezes": [asdict(f) for f in freezes],
        "engine_responses": [asdict(response) for response in engine_responses],
        "admission": store._read_authoritative_events_read_only_sync(run.id),
    }
    (tmp_path / "capacity-physical-proof.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if case in {"model_cap_rejected", "context_window_rejected"}:
        assert result["success"] is False and result["tasks_created"] == 0
        assert wires == []
        assert not any(item.ok for item in runtime._test_results)
        assert adapter.task_runtime.list_observable_task_rows() == []
        assert directive in runtime._test_commands[0].user_message
        assert runtime._test_commands[0].context["llm_max_tokens"] == 60000
        failure = json.dumps([asdict(response) for response in engine_responses], default=str)
        assert (
            "planning_output_budget_model_limit_exceeded"
            if case == "model_cap_rejected"
            else "planning_output_budget_context_window_exceeded"
        ) in failure
        return
    assert result["success"] is (case != "historical_incomplete_invalid"), result
    count = 1 if case == "complete_valid_max_tokens" or normal_entry else 2
    assert len(wires) == len(freezes) == len(runtime._test_results) == count
    if case in {"default_initial_and_correction", "override_higher"}:
        assert [command.context["llm_call_timeout_seconds"] for command in runtime._test_commands] == [
            call_timeout,
            call_timeout,
        ]
        assert [timeout[1] for timeout in runtime._test_timeouts] == [call_timeout, call_timeout]
    assert {f.identity.run_id for f in freezes} == {"pm-capacity-child"}
    assert len({f.identity.call_id for f in freezes}) == len({f.identity.request_freeze_id for f in freezes}) == count
    for wire, frozen, returned in zip(wires, freezes, runtime._test_results, strict=True):
        prompt = "\n".join(m.get("content", "") for m in wire["messages"])
        assert directive in prompt
        assert f"Prospective output allowance: {budget} tokens" in prompt
        assert wire["max_tokens"] == budget
        payload = json.loads(frozen.canonical_final_payload_json)
        assert payload["role"] == "pm" and "**PM**" in payload["messages"][0]["content"]
        assert {t["function"]["name"] for t in payload["tools"]} == {"repo_tree", "file_exists"}
        item, unavailable = bind_role_generation_outcome(
            returned.metadata, response_text=returned.output, expected_run_id="pm-capacity-child"
        )
        assert unavailable is None
        assert item.call_id == frozen.identity.call_id and item.request_freeze_id == frozen.identity.request_freeze_id
        assert (
            item.final_semantic_request_hash == frozen.final_semantic_request_hash
            and item.max_tokens == wire["max_tokens"]
        )
    assert runtime._test_results[0].metadata["generation_outcome"]["native_finish_reason"] == reason
    if normal_entry:
        advisory = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.meta.planning")
        assert advisory["analysis_source"] == (
            "factory.catalog_contract" if case == "normal_entry_bound" else "directive"
        )
        assert original_requirement in runtime._test_commands[0].user_message
        current_prompt = runtime._test_commands[0].user_message
        assert ("Factory 原生 PM/Chief Engineer 承担规划与蓝图" in current_prompt) is (case == "normal_entry_bound")
    if "incomplete" in case:
        retry = "\n".join(m.get("content", "") for m in wires[1]["messages"])
        assert "Previous output excerpt" not in retry
        assert incomplete not in runtime._test_commands[1].user_message
        assert "Incomplete/unparseable candidate" in retry
        assert not any(s["code"] == "pm.contracts.revision_base" for s in result["quality_gate"]["signals"])
    elif case == "complete_bad_bounded":
        assert any(s["code"] == "pm.contracts.revision_base" for s in result["quality_gate"]["signals"])
    if case == "historical_incomplete_invalid":
        assert result["tasks_created"] == 0
        assert adapter.task_runtime.list_observable_task_rows() == []
    snapshots = []
    for path in (workspace / ".polaris/runtime/contexts").glob("*/*"):
        if path.is_file() and len(path.name) == 24:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            request = snapshot.get("provider_request", {})
            if request.get("final_physical_request"):
                audit = request["final_request_context_audit"]
                call_id = request["factory_final_request"]["request_identity"]["call_id"]
                index = next(i for i, frozen in enumerate(freezes) if frozen.identity.call_id == call_id)
                assert request["final_physical_request"]["body"] == wires[index]
                assert audit["sampling"]["max_tokens"] == wires[index]["max_tokens"]
                assert request["factory_final_request"]["canonical_final_payload"] == json.loads(
                    freezes[index].canonical_final_payload_json
                )
                assert audit["final_request_token_estimate"] <= 8000
                assert audit["tool_schema_token_estimate"] > 0
                assert audit["final_request_evidence_coverage"]["role_identity_ok"]
                snapshots.append({"context_snapshot_ref": path.name, "snapshot": snapshot})
    assert len(snapshots) == count
    (tmp_path / "capacity-final-requests.json").write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    replay = query_factory_provider_attempt_lifecycle_replay(
        QueryFactoryProviderAttemptLifecycleReplayV1(
            schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
            workspace=str(workspace),
            factory_run_id=run.id,
        )
    )
    assert len([f for f in replay.facts if f.phase == "terminal"]) == count
    assert len([f for f in replay.facts if f.phase == "start"]) == count
    assert all(f.terminal_status == "completed" for f in replay.facts if f.phase == "terminal")
    (tmp_path / "capacity-public-replay.json").write_text(
        json.dumps(asdict(replay), ensure_ascii=False, indent=2), encoding="utf-8"
    )
