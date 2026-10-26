"""PM quality revision through real runtime, kernel and Factory authority.

Provider configuration/transport, bounded source facts and event delivery are local.
Identity generation, transaction execution, cutoff and physical budget remain real.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from typing import Any

import pytest
import requests
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import (
    _authority,
    _resolved_cut,
    _Resolver,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import PipelineTask, RoleEntrySpec
from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
    _adapter_context_for_task,
)
from polaris.cells.roles.adapters.internal import runtime_dialogue
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.kernel.public import LLMInvoker, RoleExecutionKernel
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import install_local_pm_request_capture
from polaris.cells.roles.profile.public.service import (
    RoleProfile,
    RoleProfileRegistry,
    RolePromptPolicy,
    RoleToolPolicy,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling
from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.provider_native_request import build_openai_native_messages


def _pol77_model_revision(wire: dict[str, Any], *, root_template: bool = False) -> dict[str, Any]:
    """Fake model reply only: explicit new declarations and useful QA artifacts."""
    content = "\n".join(m.get("content", "") for m in wire["messages"])
    view, _ = jsonlib.JSONDecoder().raw_decode(content.split("Revision base/view/manifest (server-owned):\n", 1)[1])
    updates = []
    for identity, output, source in [("TASK-5", "dist/browser.js", "src/entry/browser.ts"),
                                     ("TASK-6", "dist/cli.js", "src/entry/cli.ts")]:
        fields: dict[str, Any] = {"generated_outputs": [{"path": output, "inputs": [{"task_id": identity, "path": source}],
                                                       "build": {"argv": ["npm", "run", "build"], "cwd": "."}}]}
        if identity == "TASK-6" and not root_template:
            fields["depends_on"] = ["TASK-5"]
        updates.append({"task_id": identity, "set": fields})
    if root_template:
        for update in updates[:2]:
            update["append"] = {}
        for identity, name, contexts in zip(["TASK-8", "TASK-10"], ["entrypoint", "contract-integration"], [
            ["dist/browser.js", "dist/cli.js", "index.html"],
            ["requirements.md", "src/garden/dance.ts", "tests/garden.test.ts", "dist/cli.js", "dist/browser.js"],
        ], strict=True):
            path = f"tests/{name}.test.ts"
            fields = {
                "target_files": [path], "scope_paths": [path], "context_files": contexts,
                "scope": f"Create and maintain {path}; listed context files are read-only.",
                "goal": "Implement executable firefly/flower/moon/humidity integration assertions.",
                "description": "Write TypeScript tests for observable garden behavior and executable entrypoint results; do not alter build outputs.",
                "steps": ["Add normal, boundary and invalid input assertions for firefly/flower/moon/humidity behavior", "Execute the declared test and record actual failures"],
                "acceptance": ["Tests assert business values for normal, boundary and invalid cases", "Entry output reflects moon phase and humidity effects on firefly glow and flower mood", "Declared test command exits zero"],
                "verification_commands": [{"modality": "test", "argv": ["npx", "vitest", "run", path], "cwd": "."}],
            }
            updates.append({"task_id": identity, "set": fields, "append": {}})
    for identity, path in [("TASK-8", "qa/entrypoints.mjs"), ("TASK-10", "qa/qualification.mjs")]:
        if root_template:
            break
        fields = {
            "target_files": [path], "scope_paths": [path], "scope": path,
            "context_files": ["dist/browser.js", "dist/cli.js", "index.html", "requirements.md", "tests/garden.test.ts"],
            "goal": "Implement a useful executable qualification test for the owned delivery obligations",
            "description": "Create the test artifact; read dependencies, assert output behavior and record verifier failures",
            "steps": ["Read requirements and dependency artifacts", "Implement explicit assertions for build outputs and CLI behavior",
                      "Run the owned test with Node and record exit code"],
            "acceptance": ["node " + path + " exits zero with genuine assertions", "Missing outputs or incorrect behavior fail the test",
                           "The test validates browser references and firefly/flower/moon/humidity CLI output"],
            "verification_commands": [{"modality": "test", "argv": ["node", path], "cwd": "."}],
        }
        updates.append({"task_id": identity, "set": fields})
    return {"role": "assistant", "content": jsonlib.dumps({"schema_version": "pm.task_revision.v1",
            "base_hash": view["base_hash"], "updates": updates, "add_tasks": [], "remove_task_ids": []})}


@pytest.mark.parametrize("revision", ["valid", "noop", "malformed", "hidden", "wrong_owner", "wrong_build", "routing", "late_readonly",
                                      "root_legal", "root_noop", "root_hidden", "root_wrong_input", "root_foreign_language", "root_remove"])
async def test_pol77_exact_raw10_one_legal_revision_through_real_budget_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str) -> None:
    import sys
    from dataclasses import replace

    from polaris.cells.factory.pipeline.internal.factory_event_chain import (
        FactoryRunAdmissionV1,
        build_factory_run_admitted_event,
    )
    from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority._source import (
        _PublicFactoryRoleEvidenceFactStream,
    )
    from polaris.cells.factory.pipeline.internal.factory_role_evidence_source_resolver import (
        CanonicalFactoryRoleEvidenceSourceAuthority,
    )
    from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
    from polaris.cells.factory.pipeline.internal.factory_run_service import OrchestrationStageExecutor
    from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
    from polaris.cells.roles.adapters.tests.test_pm_contract_boundary_regressions import (
        _native_fake_capacity,
        _pol77_input,
    )

    _native_fake_capacity(monkeypatch)
    register = RoleProfileRegistry.register
    def bounded_register(registry: Any, profile: Any) -> Any:
        if profile.role_id == "pm":
            profile = replace(profile, context_policy=replace(profile.context_policy, max_context_tokens=8000))
        return register(registry, profile)
    monkeypatch.setattr(RoleProfileRegistry, "register", bounded_register)
    raw, directive = _pol77_input("RAW_PM_RESPONSE.txt"), _pol77_input("DIRECTIVE.txt")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FactoryStore(workspace / ".polaris/runtime/factory")
    factory_run = FactoryRun(
        id="factory-run-1", config=FactoryConfig(name="POL77 isolated contract proof", description=directive,
                                                  stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING, created_at="2026-10-09T06:49:03+00:00",
        metadata={"current_stage": "pm_planning", "factory_stage_in_flight": True},
    )
    admitted = build_factory_run_admitted_event(FactoryRunAdmissionV1(
        factory_run_id=factory_run.id, created_at=factory_run.created_at,
        name=factory_run.config.name, description=directive,
    ))
    admitted.update(run_id=factory_run.id, event_id="pol77-admission", timestamp=factory_run.created_at)
    await store.append_authoritative_event(factory_run.id, admitted)
    await store.save_run(factory_run)
    source = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=workspace, factory_store=store, factory_event_loader=store._read_authoritative_events_read_only_sync
    )
    public_facts: Any = _PublicFactoryRoleEvidenceFactStream()
    port, _, _, _ = _authority(
        tmp_path=tmp_path, stage="pm_planning", run=factory_run, resolver=source, facts=public_facts,
        run_loader=lambda: store.get_run(factory_run.id),
    )
    def reply(wire: dict[str, Any]) -> dict[str, Any]:
        model = _pol77_model_revision(wire, root_template=revision.startswith("root_"))
        patch = jsonlib.loads(model["content"])
        if revision.startswith("root_"):
            rendered = "\n".join(m.get("content", "") for m in wire["messages"])
            view, _ = jsonlib.JSONDecoder().raw_decode(rendered.split("Revision base/view/manifest (server-owned):\n", 1)[1])
            rows = {row["id"]: row for row in view["tasks"]}
            original = jsonlib.loads(raw)["tasks"]
            assert len(rows) == len(original)
            for task in original:
                row = rows[task["id"]]
                assert row["depends_on"] == task["depends_on"]
                for field in view["edit_manifest"]["tasks"].get(task["id"], {}).get("set", {}):
                    if field in task:
                        assert row[field] == task[field]
                    else:
                        assert field in row["absent_fields"]
            for identity in ["TASK-1", "TASK-2", "TASK-3", "TASK-4", "TASK-5", "TASK-6"]:
                assert rows[identity]["target_files"] == next(t["target_files"] for t in original if t["id"] == identity)
            for identity in ["TASK-5", "TASK-6"]:
                assert "target_files" not in view["edit_manifest"]["tasks"][identity]["set"]
        if revision == "root_noop":
            patch["updates"] = []
        elif revision == "root_hidden":
            patch["updates"].append({"task_id": "TASK-3", "set": {"description": "Unauthorized hidden-field change"}, "append": {}})
        elif revision == "root_wrong_input":
            patch["updates"][0]["set"]["generated_outputs"][0]["inputs"] = [{"task_id": "TASK-10", "path": "src/entry/browser.ts"}]
        elif revision == "root_foreign_language":
            patch["updates"][2]["set"].update(target_files=["tests/Entry.java"], scope_paths=["tests/Entry.java"])
        elif revision == "root_remove":
            patch["remove_task_ids"] = ["TASK-10"]
        if revision == "noop":
            patch["updates"] = []
        elif revision == "malformed":
            patch["updates"][0]["set"]["generated_outputs"] = [None]
        elif revision == "hidden":
            patch["updates"].append({"task_id": "TASK-1", "set": {"goal": "Unauthorized"}})
        elif revision == "wrong_owner":
            patch["updates"][0]["set"]["generated_outputs"][0]["inputs"][0]["task_id"] = "TASK-8"
        elif revision == "wrong_build":
            patch["updates"][0]["set"]["generated_outputs"][0]["build"]["argv"] = ["npm", "run", "other"]
        elif revision == "routing":
            patch["updates"][2]["set"]["execution_backend"] = "code_edit"
        elif revision == "late_readonly":
            patch["updates"][2]["set"]["context_files"].append("qa/entrypoints.mjs")
        model["content"] = jsonlib.dumps(patch)
        return model
    runtime, freezes, wires = _local_runtime(workspace, monkeypatch, [{"role": "assistant", "content": raw}, reply])
    adapter = PMAdapter(workspace=str(workspace))
    authority = port.mint_authority_binding("pm")
    budget_frames = []
    old_profile = sys.getprofile()
    def observe(frame: Any, event: str, arg: Any) -> None:
        if event == "return" and frame.f_code.co_name == "_build_context_impl" and frame.f_locals.get("current_instruction_tokens") is not None:
            gateway = frame.f_locals["self"]
            budget_frames.append({**{key: frame.f_locals.get(key) for key in ["reserved_system_prompt_tokens", "enforcement_budget_tokens", "current_instruction_tokens"]},
                                  "role_policy": gateway.policy.max_context_tokens, "model_window": gateway._context_os.resolved_context_window})
        if old_profile:
            old_profile(frame, event, arg)
    try:
        sys.setprofile(observe)
        with bind_factory_role_evidence_authority(authority):
            result = await adapter._run_pm_stage("pol77-pm-task", directive, {}, {"run_id": "pol77-child", "timeout_seconds": 43})
    finally:
        sys.setprofile(old_profile)
    (tmp_path / "pol77-physical-proof.json").write_text(jsonlib.dumps({"revision": revision, "wires": wires, "budget_frames": budget_frames,
        "factory_admission_chain": store._read_authoritative_events_read_only_sync(factory_run.id),
        "source_resolver": type(source).__name__, "fact_stream": type(public_facts).__name__,
        "result": result, "freezes": [{"run_id": f.identity.run_id, "call_id": f.identity.call_id, "freeze_id": f.identity.request_freeze_id,
                                       "final_payload": jsonlib.loads(f.canonical_final_payload_json)} for f in freezes]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    assert result["success"] is (revision in {"valid", "root_legal"}), (result, runtime._test_results)
    assert len(wires) == len(freezes) == 2
    assert all(w["max_tokens"] == 4000 for w in wires)
    assert {f.identity.run_id for f in freezes} == {"pol77-child"}
    assert len({f.identity.call_id for f in freezes}) == len({f.identity.request_freeze_id for f in freezes}) == 2
    assert directive in "\n".join(m.get("content", "") for m in wires[1]["messages"])
    assert budget_frames and all(f["role_policy"] == 8000 and f["model_window"] == 1000000 for f in budget_frames)
    assert all(f["current_instruction_tokens"] <= f["enforcement_budget_tokens"] for f in budget_frames)
    assert port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash).consumed_attempts == 2
    assert len([s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.quality_revision"]) == 1
    for frozen in freezes:
        payload = jsonlib.loads(frozen.canonical_final_payload_json)
        assert payload["role"] == "pm" and "**PM**" in payload["messages"][0]["content"]
        assert {t["function"]["name"] for t in payload["tools"]} == {"repo_tree", "file_exists"}
    snapshots = []
    for path in (workspace / ".polaris/runtime/contexts").glob("*/*"):
        if path.is_file() and len(path.name) == 24 and all(c in "0123456789abcdef" for c in path.name):
            snapshot = jsonlib.loads(path.read_text(encoding="utf-8"))
            request = snapshot.get("provider_request", {})
            if not request.get("final_physical_request"):
                continue
            bound = request.get("factory_final_request", {})
            for frozen, wire in zip(freezes, wires, strict=True):
                if bound.get("request_identity", {}).get("call_id") != frozen.identity.call_id:
                    continue
                assert bound["canonical_final_payload"] == jsonlib.loads(frozen.canonical_final_payload_json)
                assert request["final_physical_request"]["body"] == wire
                audit = request["final_request_context_audit"]
                assert audit["context_window_tokens"] == 1000000
                assert audit["final_request_token_estimate"] <= 8000
                assert audit["tool_schema_token_estimate"] > 0
                assert audit["final_request_token_estimate"] == (audit["message_token_estimate"]
                    + audit["tool_schema_token_estimate"] + audit["response_format_token_estimate"]
                    + audit.get("request_control_token_estimate", 0))
                assert audit["final_request_evidence_coverage"]["role_identity_ok"]
                assert audit["final_request_evidence_coverage"]["tool_schema_registry_coverage"]["arg_aliases_present"]
                intent = next(slot for slot in audit["final_request_evidence_coverage"]["evidence_slots"]
                              if slot["ref_type"] == "pm_raw_intent")
                assert intent["present"] and intent["details"]["source_fact_schema"] == "factory.event_chain.v1"
                snapshots.append({"context_snapshot_ref": path.name, "snapshot": snapshot})
    assert {s["snapshot"]["provider_request"]["factory_final_request"]["request_identity"]["call_id"] for s in snapshots} == {f.identity.call_id for f in freezes}
    (tmp_path / "pol77-final-request-snapshots.json").write_text(
        jsonlib.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if revision not in {"valid", "root_legal"}:
        assert result["tasks_created"] == 0
        assert adapter.task_runtime.list_observable_task_rows() == []
        if revision.startswith("root_"):
            signal = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.revision_derivation")
            evidence = jsonlib.loads(Path(signal["artifact_path"]).read_text(encoding="utf-8"))
            required = {"root_noop": "artifact_action_contract_invalid", "root_hidden": "pm_revision_invalid",
                        "root_wrong_input": "generated_output_input_invalid", "root_foreign_language": "pm_plan_language_mismatch",
                        "root_remove": "pm_revision_invalid"}[revision]
            assert required in {d["code"] for d in evidence["quality"]["contract_diagnostics"]}
            if revision in {"root_hidden", "root_remove"}:
                assert evidence["raw_merged_candidate"] is None
        return
    signal = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.revision_derivation")
    derivation = jsonlib.loads(Path(signal["artifact_path"]).read_text(encoding="utf-8"))
    assert derivation["original_response"] == raw
    merged = derivation["raw_merged_candidate"]["tasks"]
    original = jsonlib.loads(raw)["tasks"]
    if revision == "root_legal":
        from copy import deepcopy
        expected = {"tasks": deepcopy(original)}
        by_id = {t["id"]: t for t in expected["tasks"]}
        patch = jsonlib.loads(_pol77_model_revision(wires[1], root_template=True)["content"])
        for update in patch["updates"]:
            by_id[update["task_id"]].update(update["set"])
        assert derivation["raw_merged_candidate"] == expected
    assert [t["id"] for t in merged] == [t["id"] for t in original]
    assert [t["assigned_to"] for t in merged] == [t["assigned_to"] for t in original]
    assert [t["execution_backend"] for t in merged] == [t["execution_backend"] for t in original]
    catalog = workspace / ".polaris/catalog_contract.json"
    catalog.parent.mkdir(exist_ok=True)
    catalog.write_text(jsonlib.dumps({"primary_language": "typescript"}), encoding="utf-8")
    executor = OrchestrationStageExecutor(workspace)
    assert executor._validate_pm_plan_artifact_contract() == ""
    assert executor._validate_pm_plan_language_consistency() == ""
    assert executor._enrich_pm_plan_contract_artifact()["generated_target_count"] == 2
    tasks = executor._load_pm_plan_tasks()
    portfolio = executor._chief_engineer_portfolio_tasks(tasks)
    assert portfolio[4].to_dict()["generated_outputs"] == merged[4]["generated_outputs"]
    assert portfolio[5].to_dict()["generated_outputs"] == merged[5]["generated_outputs"]
    assert portfolio[7].to_dict()["context_files"] == merged[7]["context_files"]
    assert {t for task in portfolio for t in task.target_files}.isdisjoint({"dist/browser.js", "dist/cli.js"})
    assert {"dist/browser.js", "dist/cli.js"}.issubset(set(executor._missing_declared_delivery_targets(tasks)))
    board_rows = adapter.task_runtime.list_observable_task_rows()
    assert {r["metadata"]["pm_task_id"] for r in board_rows} == {t["id"] for t in original}
    from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter

    claimed = next(r for r in board_rows if r["metadata"]["pm_task_id"] == "TASK-5")
    director_context: dict[str, Any] = {}
    DirectorAdapter._promote_task_contract_to_runtime_context(
        task=claimed, context=director_context, workspace=str(workspace)
    )
    runtime_metadata = DirectorAdapter._build_role_runtime_metadata(director_context, max_retries=1)
    assert runtime_metadata["generated_outputs"] == merged[4]["generated_outputs"]
    assert "dist/browser.js" not in director_context["target_files"]
    assert "dist/browser.js" not in director_context["scope_paths"]


@pytest.fixture(autouse=True)
def _isolated_external_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Lock authority is platform-owned and persistent: keep test provisioning
    # in this fresh sandbox, including when pytest is run repeatedly.
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernel-home"))
    monkeypatch.setattr(JetStreamPublisher, "publish", lambda *_args, **_kwargs: True)


def _contracts(*, valid: bool) -> str:
    task: dict[str, Any] = {
        "id": "TASK-1",
        "title": "Create model",
        "description": "Create the domain model",
        "goal": "model",
        "scope_paths": ["src/model.py", "tests/test_model.py"],
        "target_files": ["src/model.py", "tests/test_model.py"],
        "steps": ["Read the record contract", "Implement model validation", "Test valid and invalid records"],
        "acceptance": ["Valid records preserve fields", "Malformed records raise ValueError", "Unit tests pass"],
        "depends_on": [],
    }
    if valid:
        task["verification_commands"] = [
            {"modality": "environment_prep", "argv": ["python3", "-m", "pip", "install", "-e", "."], "cwd": "."},
            {"modality": "test", "argv": ["python3", "-m", "unittest", "discover"], "cwd": "."},
        ]
    return jsonlib.dumps({"tasks": [task]})


def _verifier_revision_response(wire: dict[str, Any]) -> dict[str, Any]:
    """Model fixture reads the real wire base and explicitly replaces verifier rows."""
    content = "\n".join(message.get("content", "") for message in wire["messages"])
    view, _ = jsonlib.JSONDecoder().raw_decode(content.split("Revision base/view/manifest (server-owned):\n", 1)[1])
    return {
        "role": "assistant",
        "content": jsonlib.dumps(
            {
                "schema_version": "pm.task_revision.v1",
                "base_hash": view["base_hash"],
                "updates": [
                    {
                        "task_id": "TASK-1",
                        "set": {
                            "verification_commands": jsonlib.loads(_contracts(valid=True))["tasks"][0][
                                "verification_commands"
                            ],
                        },
                        "append": {},
                    }
                ],
                "add_tasks": [],
                "remove_task_ids": [],
            }
        ),
    }


def _local_runtime(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, outputs: list[Any]
) -> tuple[RoleRuntimeService, list[Any], list[dict[str, Any]]]:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="pol67_isolated_runtime_regression",
        )
    )
    registry = RoleProfileRegistry()
    profile = RoleProfile(
        role_id="pm",
        display_name="PM",
        description="Project planning",
        provider_id="local-provider",
        model="local-model",
        prompt_policy=RolePromptPolicy(core_template_id="pm"),
        tool_policy=RoleToolPolicy(whitelist=["repo_tree", "file_exists"]),
    )
    registry.register(profile)
    freezes = install_local_pm_request_capture(monkeypatch)
    monkeypatch.setattr(
        AIExecutor,
        "_get_provider_config",
        lambda *_args: {
            "type": "openai_compat",
            "base_url": "https://local-provider.example.test/v1",
            "retries": 0,
            "max_context_tokens": 262144,
            "max_output_tokens": 4096,
        },
    )
    monkeypatch.setattr(AIExecutor, "_resolve_provider_model", lambda *_args: ("local-provider", "local-model"))
    invoker = LLMInvoker(workspace=str(workspace), enable_cache=False)
    kernel = RoleExecutionKernel(workspace=str(workspace), registry=registry, llm_invoker=invoker)
    runtime = RoleRuntimeService()
    runtime._kernels[str(workspace)] = kernel
    runtime_results: list[Any] = []
    commands: list[Any] = []
    timeouts: list[Any] = []
    original_execute = runtime.execute_role_session

    async def execute(command: Any) -> Any:
        commands.append(command)
        result = await original_execute(command)
        runtime_results.append(result)
        return result

    monkeypatch.setattr(runtime, "execute_role_session", execute)
    runtime._test_results = runtime_results
    runtime._test_commands = commands
    runtime._test_timeouts = timeouts
    monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: runtime)
    wire_requests: list[dict[str, Any]] = []

    def post(url: str, *, headers: Any, json: Any, timeout: Any) -> requests.Response:
        del headers
        timeouts.append(timeout)
        assert url == "https://local-provider.example.test/v1/chat/completions"
        wire_requests.append(json)
        assert outputs, "Unexpected physical call exceeds bounded PM revision"
        message = outputs.pop(0)
        if callable(message):
            message = message(json)
        if isinstance(message, Exception):
            raise message
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = jsonlib.dumps(
            {
                "choices": [
                    {"message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
            }
        ).encode("utf-8")
        return response

    monkeypatch.setattr(_http_pooling, "bounded_http_post", post)
    return runtime, freezes, wire_requests


async def test_quality_revision_keeps_orchestration_child_through_real_factory_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _admission, _resolver, facts = _authority(
        tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm"))
    )
    workspace = tmp_path / "workspace"
    outputs = [
        {
            "role": "assistant",
            "content": "Inspect workspace",
            "tool_calls": [
                {
                    "id": "tree-call",
                    "type": "function",
                    "function": {"name": "repo_tree", "arguments": '{"path":"."}'},
                }
            ],
        },
        {"role": "assistant", "content": _contracts(valid=False)},
        _verifier_revision_response,
    ]
    _runtime, freezes, wires = _local_runtime(workspace, monkeypatch, outputs)
    adapter = PMAdapter(workspace=str(workspace))
    authority = port.mint_authority_binding("pm")
    context = _adapter_context_for_task(
        run_id="pm-owned-child",
        workspace=str(workspace),
        task=PipelineTask(task_id="pm-task", role_entry=RoleEntrySpec(role_id="pm"), timeout_seconds=43),
        role_metadata={"run_id": "foreign-metadata-child", "turn_request_id": "foreign-turn"},
    )
    with bind_factory_role_evidence_authority(authority):
        result = await adapter._run_pm_stage(
            "pm-task", "Create model", {"run_id": "foreign-input-child", "factory_run_id": "foreign-parent"}, context
        )

    assert _runtime._test_results[0].ok is True
    assert "verification_commands" not in _runtime._test_results[0].output
    revision = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.quality_revision")
    assert revision["score"] == 40
    assert revision["critical_issues"] == ["verification_commands_missing"]
    assert revision["parsed_task_count"] == 1
    assert result["success"] is True, (result, _runtime._test_results)
    assert result["real_planning_qualified"] is True
    assert len(wires) == len(freezes) == 3
    assert {f.identity.run_id for f in freezes} == {"pm-owned-child"}
    assert len({f.identity.call_id for f in freezes}) == 3
    assert len({f.identity.request_freeze_id for f in freezes}) == 3
    commands = _runtime._test_commands
    assert len({c.turn_request_id for c in commands}) == 2
    assert {c.run_id for c in commands} == {"pm-owned-child"}
    assert all(c.context["timeout_seconds"] == 43 for c in commands)
    assert "foreign-turn" not in {c.turn_request_id for c in commands}
    assert len(_runtime._test_timeouts) == 3
    state = port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash)
    assert state.consumed_attempts == 3
    assert state.attempt_budget == authority.attempt_budget
    assert facts.events
    assert jsonlib.loads(freezes[0].canonical_final_payload_json)["messages"][0]["role"] == "system"
    for index, frozen in enumerate(freezes):
        payload = jsonlib.loads(frozen.canonical_final_payload_json)
        if index == 1:
            assert "FINAL ANSWER" in payload["messages"][0]["content"]
        else:
            assert "**PM**" in payload["messages"][0]["content"]
        assert payload["role"] == "pm"
    assert {tool["function"]["name"] for tool in wires[0]["tools"]} == {"repo_tree", "file_exists"}
    assert wires[0]["tool_choice"] == "auto"
    snapshots = {}
    for path in (workspace / ".polaris/runtime/contexts").glob("*/*"):
        if path.is_file() and len(path.name) == 24:
            request = jsonlib.loads(path.read_text(encoding="utf-8")).get("provider_request", {})
            final = request.get("factory_final_request", {})
            if final and "final_physical_request" in request:
                snapshots[final["final_semantic_request_hash"]] = request
    for frozen, wire in zip(freezes, wires, strict=True):
        snapshot = snapshots[frozen.final_semantic_request_hash]
        physical = snapshot["final_physical_request"]
        assert physical["body"] == wire
        assert physical["transport"]["timeout"] == [10.0, 43.0]
        payload = snapshot["factory_final_request"]["canonical_final_payload"]
        assert build_openai_native_messages(payload["messages"]) == wire["messages"]
        assert payload["tools"] == wire.get("tools", [])
        audit = snapshot["final_request_context_audit"]
        assert audit["request_identity"]["call_id"] == frozen.identity.call_id
        assert audit["final_request_token_estimate"] >= audit["message_token_estimate"]
        assert audit["context_window_tokens"] > 0
        assert audit["context_window_utilization"] > 0
        assert audit["tool_schema_count"] == len(payload["tools"])
        coverage = audit["final_request_evidence_coverage"]
        assert coverage["role_identity_ok"] is True
        assert coverage["pass"] is True


@pytest.mark.parametrize("revision", ["none", "valid", "invalid", "failure"])
async def test_pm_quality_gate_and_single_revision_remain_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str
) -> None:
    port, _admission, _resolver, _facts = _authority(
        tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm"))
    )
    workspace = tmp_path / "workspace"
    outputs: list[Any] = [{"role": "assistant", "content": _contracts(valid=revision == "none")}]
    if revision != "none":
        outputs.append(
            RuntimeError("local_transport_failed")
            if revision == "failure"
            else _verifier_revision_response
            if revision == "valid"
            else {"role": "assistant", "content": "invalid reply"}
        )
    runtime, freezes, wires = _local_runtime(workspace, monkeypatch, outputs)
    adapter = PMAdapter(workspace=str(workspace))
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        result = await adapter._run_pm_stage("pm-task", "Create model", {}, {"run_id": "pm-owned-child"})
    assert result["success"] is (revision in {"none", "valid"}), (result, runtime._test_results)
    assert len(wires) == (1 if revision == "none" else 2)
    assert {f.identity.run_id for f in freezes} == {"pm-owned-child"}
    assert len({f.identity.call_id for f in freezes}) == len(freezes)
    signals = result["quality_gate"]["signals"]
    revisions = [s for s in signals if s["code"] == "pm.contracts.quality_revision"]
    assert len(revisions) == (0 if revision == "none" else 1)
    if revisions:
        assert revisions[0]["score"] == 40
        assert revisions[0]["critical_issues"] == ["verification_commands_missing"]
        assert revisions[0]["parsed_task_count"] == 1
    if revision in {"invalid", "failure"}:
        assert result["tasks_created"] == 0
        assert result["real_planning_qualified"] is False


async def test_foreign_pm_child_under_same_grant_still_rejected_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _admission, _resolver, facts = _authority(
        tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm"))
    )
    workspace = tmp_path / "workspace"
    runtime, _freezes, wires = _local_runtime(
        workspace, monkeypatch, [{"role": "assistant", "content": _contracts(valid=True)}]
    )
    adapter = PMAdapter(workspace=str(workspace))
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        first = await adapter._call_role_llm("Plan model", context={"run_id": "pm-owned-child"})
        event_count = len(facts.events)
        foreign = await adapter._call_role_llm("Plan model", context={"run_id": "pm-foreign-child"})
    assert first["success"] is True, runtime._test_results
    assert foreign["success"] is False
    assert "controlled_child_run_mismatch" in foreign["error"]
    assert len(wires) == 1
    assert len(facts.events) == event_count


@pytest.mark.parametrize("governed_context", [False, True])
async def test_standalone_support_and_independent_pm_task_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, governed_context: bool
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    correction = (
        _verifier_revision_response if governed_context else {"role": "assistant", "content": _contracts(valid=True)}
    )
    outputs = [
        {"role": "assistant", "content": _contracts(valid=False)},
        correction,
        {"role": "assistant", "content": _contracts(valid=False)},
        correction,
    ]
    runtime, _freezes, wires = _local_runtime(workspace, monkeypatch, outputs)
    for child in ("pm-independent-a", "pm-independent-b"):
        adapter = PMAdapter(workspace=str(workspace))
        result = await adapter._run_pm_stage(child, "Create model", {}, {"run_id": child} if governed_context else {})
        assert result["success"] is True
    assert len(wires) == 4
    commands = runtime._test_commands
    assert len({c.turn_request_id for c in commands}) == 4
    assert [c.run_id for c in commands] == (
        ["pm-independent-a", "pm-independent-a", "pm-independent-b", "pm-independent-b"]
        if governed_context
        else [None] * 4
    )
