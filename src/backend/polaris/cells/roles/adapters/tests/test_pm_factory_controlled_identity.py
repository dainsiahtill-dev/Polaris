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
    for identity, output, source in [
        ("TASK-5", "dist/browser.js", "src/entry/browser.ts"),
        ("TASK-6", "dist/cli.js", "src/entry/cli.ts"),
    ]:
        fields: dict[str, Any] = {
            "generated_outputs": [
                {
                    "path": output,
                    "inputs": [{"task_id": identity, "path": source}],
                    "build": {"argv": ["npm", "run", "build"], "cwd": "."},
                }
            ]
        }
        if identity == "TASK-6" and not root_template:
            fields["depends_on"] = ["TASK-5"]
        updates.append({"task_id": identity, "set": fields})
    if root_template:
        for update in updates[:2]:
            update["append"] = {}
        for identity, name, contexts in zip(
            ["TASK-8", "TASK-10"],
            ["entrypoint", "contract-integration"],
            [
                ["dist/browser.js", "dist/cli.js", "index.html"],
                ["requirements.md", "src/garden/dance.ts", "tests/garden.test.ts", "dist/cli.js", "dist/browser.js"],
            ],
            strict=True,
        ):
            path = f"tests/{name}.test.ts"
            fields = {
                "target_files": [path],
                "scope_paths": [path],
                "context_files": contexts,
                "scope": f"Create and maintain {path}; listed context files are read-only.",
                "goal": "Implement executable firefly/flower/moon/humidity integration assertions.",
                "description": "Write TypeScript tests for observable garden behavior and executable entrypoint results; do not alter build outputs.",
                "steps": [
                    "Add normal, boundary and invalid input assertions for firefly/flower/moon/humidity behavior",
                    "Execute the declared test and record actual failures",
                ],
                "acceptance": [
                    "Tests assert business values for normal, boundary and invalid cases",
                    "Entry output reflects moon phase and humidity effects on firefly glow and flower mood",
                    "Declared test command exits zero",
                ],
                "verification_commands": [{"modality": "test", "argv": ["npx", "vitest", "run", path], "cwd": "."}],
            }
            updates.append({"task_id": identity, "set": fields, "append": {}})
    for identity, path in [("TASK-8", "qa/entrypoints.mjs"), ("TASK-10", "qa/qualification.mjs")]:
        if root_template:
            break
        fields = {
            "target_files": [path],
            "scope_paths": [path],
            "scope": path,
            "context_files": [
                "dist/browser.js",
                "dist/cli.js",
                "index.html",
                "requirements.md",
                "tests/garden.test.ts",
            ],
            "goal": "Implement a useful executable qualification test for the owned delivery obligations",
            "description": "Create the test artifact; read dependencies, assert output behavior and record verifier failures",
            "steps": [
                "Read requirements and dependency artifacts",
                "Implement explicit assertions for build outputs and CLI behavior",
                "Run the owned test with Node and record exit code",
            ],
            "acceptance": [
                "node " + path + " exits zero with genuine assertions",
                "Missing outputs or incorrect behavior fail the test",
                "The test validates browser references and firefly/flower/moon/humidity CLI output",
            ],
            "verification_commands": [{"modality": "test", "argv": ["node", path], "cwd": "."}],
        }
        updates.append({"task_id": identity, "set": fields})
    return {
        "role": "assistant",
        "content": jsonlib.dumps(
            {
                "schema_version": "pm.task_revision.v1",
                "base_hash": view["base_hash"],
                "updates": updates,
                "add_tasks": [],
                "remove_task_ids": [],
            }
        ),
    }


@pytest.mark.parametrize(
    "revision",
    [
        "valid",
        "noop",
        "malformed",
        "hidden",
        "wrong_owner",
        "wrong_build",
        "routing",
        "late_readonly",
        "root_legal",
        "root_noop",
        "root_hidden",
        "root_wrong_input",
        "root_foreign_language",
        "root_remove",
    ],
)
async def test_pol77_exact_raw10_one_legal_revision_through_real_budget_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str
) -> None:
    """The captured raw10 plan keeps one legal revision.

    The scenario this test drove reached the retired legacy role session
    entry to observe the physical provider turns, the request freezes and
    the attempt budget they consumed. That entry is closed now: a new
    engineering run has exactly one owner, the engineering run service. The
    plan admission and the typed revision boundary stay live server-side
    contracts, so they are asserted here against the adapter's own model
    seam.
    """
    from copy import deepcopy

    from polaris.cells.roles.adapters.internal.pm.parsing import _PMContractError
    from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id=f"retired-pm-pol77-{revision}",
        workspace=str(tmp_path),
    )
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
        id="factory-run-1",
        config=FactoryConfig(name="POL77 isolated contract proof", description=directive, stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-09T06:49:03+00:00",
        metadata={"current_stage": "pm_planning", "factory_stage_in_flight": True},
    )
    admitted = build_factory_run_admitted_event(
        FactoryRunAdmissionV1(
            factory_run_id=factory_run.id,
            created_at=factory_run.created_at,
            name=factory_run.config.name,
            description=directive,
        )
    )
    admitted.update(run_id=factory_run.id, event_id="pol77-admission", timestamp=factory_run.created_at)
    await store.append_authoritative_event(factory_run.id, admitted)
    await store.save_run(factory_run)
    source = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=workspace, factory_store=store, factory_event_loader=store._read_authoritative_events_read_only_sync
    )
    public_facts: Any = _PublicFactoryRoleEvidenceFactStream()
    port, _, _, _ = _authority(
        tmp_path=tmp_path,
        stage="pm_planning",
        run=factory_run,
        resolver=source,
        facts=public_facts,
        run_loader=lambda: store.get_run(factory_run.id),
    )
    adapter = PMAdapter(workspace=str(workspace))
    authority = port.mint_authority_binding("pm")

    # The captured plan is admitted by the current quality gate as it stands.
    calls: list[dict[str, Any]] = []
    _pm_adapter_model(adapter, [{"role": "assistant", "content": raw}], calls)
    with bind_factory_role_evidence_authority(authority):
        admitted_result = await adapter._run_pm_stage(
            "pol77-pm-task", directive, {}, {"run_id": "pol77-child", "timeout_seconds": 43}
        )
    assert admitted_result["success"] is True, admitted_result
    assert len(calls) == 1
    assert admitted_result["tasks_created"] == 10

    def reply(wire: dict[str, Any]) -> dict[str, Any]:
        model = _pol77_model_revision(wire, root_template=revision.startswith("root_"))
        patch = jsonlib.loads(model["content"])
        if revision.startswith("root_"):
            rendered = "\n".join(m.get("content", "") for m in wire["messages"])
            view, _ = jsonlib.JSONDecoder().raw_decode(
                rendered.split("Revision base/view/manifest (server-owned):\n", 1)[1]
            )
            rows = {row["id"]: row for row in view["tasks"]}
            original = jsonlib.loads(raw)["tasks"]
            assert len(rows) == len(original)
            for task in original:
                row = rows[task["id"]]
                assert row["depends_on"] == task["depends_on"]
            for identity in ["TASK-5", "TASK-6"]:
                assert "target_files" not in view["edit_manifest"]["tasks"][identity]["set"]
        if revision == "root_noop":
            patch["updates"] = []
        elif revision == "root_hidden":
            patch["updates"].append(
                {"task_id": "TASK-3", "set": {"description": "Unauthorized hidden-field change"}, "append": {}}
            )
        elif revision == "root_wrong_input":
            patch["updates"][0]["set"]["generated_outputs"][0]["inputs"] = [
                {"task_id": "TASK-10", "path": "src/entry/browser.ts"}
            ]
        elif revision == "root_foreign_language":
            patch["updates"][2]["set"].update(target_files=["tests/Entry.java"], scope_paths=["tests/Entry.java"])
        elif revision == "root_remove":
            patch["remove_task_ids"] = ["TASK-10"]
        if revision == "valid":
            # The repair authority covers the diagnosed generated outputs only.
            patch["updates"] = [
                update
                for update in patch["updates"]
                if update["task_id"] in {"TASK-5", "TASK-6"}
                and set(update["set"]) == {"generated_outputs"}
            ]
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

    # A diagnosed generated-output defect gives the model exactly the
    # generated_outputs repair authority, and nothing else.
    defect = deepcopy(jsonlib.loads(raw))
    for identity, path in (("TASK-5", "dist/browser.js"), ("TASK-6", "dist/cli.js")):
        task = next(item for item in defect["tasks"] if item["id"] == identity)
        task["generated_outputs"] = [
            {
                "path": path,
                "inputs": [{"task_id": identity, "path": "src/entry/absent.ts"}],
                "build": {"argv": ["npx", "tsc", "-p", "."], "cwd": "."},
            }
        ]
    defect_text = jsonlib.dumps(defect, ensure_ascii=False)
    contracts = adapter._extract_task_contracts(defect_text, directive=directive)
    _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    assert quality["ok"] is False
    base = make_revision_base(
        defect_text,
        workspace=str(workspace),
        run_id="pol77-child",
        task_id="pol77-pm-task",
        directive=directive,
        quality=quality,
    )
    assert base is not None
    wire = {"messages": [{"content": "Revision base/view/manifest (server-owned):\n" + base.view_json}]}
    merged: dict[str, Any] | None = None
    try:
        merged = apply_revision(base, reply(wire)["content"])
    except (ValueError, _PMContractError):
        merged = None
    if merged is None:
        # The manifest refuses the revision before the quality gate runs.
        assert revision != "valid"
        return
    _merged_contracts, merged_quality = adapter._evaluate_contract_quality(merged["tasks"], directive=directive)
    # The captured revision still has to pass the current quality gate: its
    # generated-output rows bind a build that the task does not declare, so
    # the merge is not an admission.
    assert merged_quality["ok"] is False, (revision, merged_quality)
    assert "generated_output_build_invalid" in {
        str(item["code"]) for item in merged_quality["contract_diagnostics"]
    }


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


def _pm_model_reply(message: str) -> dict[str, Any]:
    """Read the server-owned revision view out of one PM request message."""
    view, _ = jsonlib.JSONDecoder().raw_decode(
        message.split("Revision base/view/manifest (server-owned):\n", 1)[1]
    )
    return view


def _pm_adapter_model(
    adapter: PMAdapter,
    outputs: list[Any],
    calls: list[dict[str, Any]],
) -> Any:
    """Bind the PM adapter's own LLM seam to a scripted model reply.

    The retired legacy role session entry is closed, so the adapter's model
    turn is injected at its own boundary. Every other adapter behaviour —
    the revision loop, the quality gate, the derivation artifacts and the
    board task rows — stays real.
    """

    async def call(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append({"message": message, "context": context or {}})
        assert outputs, "The scripted PM revision has to start with one model turn"
        output = outputs.pop(0)
        if not outputs:
            # The bounded stage budget ends the revision loop. Repeating the
            # last reply reproduces the no-progress termination the adapter
            # detects from the repeated candidate state.
            outputs.append(output)
        if callable(output):
            output = output(message)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, str):
            content = output
        else:
            content = str(output.get("content", ""))
        return {
            "success": True,
            "response": content,
            "content": content,
            "error": None,
            "metadata": {"role_runtime_entrypoint": "roles.adapters.tests.model_seam"},
        }

    adapter._call_role_llm = call
    return call


def _pm_revision_reply(reply: Any) -> Any:
    """Adapt a wire-based model fixture to the adapter's message seam."""

    def call(message: str) -> Any:
        return reply({"messages": [{"content": message}]})

    return call


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
    """The quality revision keeps the orchestration child identity.

    The scenario this test drove reached the retired legacy role session
    entry to observe the physical provider turn and the request freeze it
    produced. That entry is closed now: a new engineering run has exactly
    one owner, the engineering run service. The revision admission and the
    child identity stay live server-side contracts, so they are asserted here
    against the adapter's own model seam.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id="retired-pm-quality-revision-child",
        workspace=str(tmp_path),
    )
    port, _admission, _resolver, facts = _authority(
        tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm"))
    )
    workspace = tmp_path / "workspace"
    adapter = PMAdapter(workspace=str(workspace))
    calls: list[dict[str, Any]] = []
    _pm_adapter_model(
        adapter,
        [
            {"role": "assistant", "content": _contracts(valid=False)},
            _pm_revision_reply(_verifier_revision_response),
        ],
        calls,
    )
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
    assert len(calls) == 2
    revision = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.quality_revision")
    assert revision["score"] == 40
    assert revision["critical_issues"] == ["verification_commands_missing"]
    assert revision["parsed_task_count"] == 1
    assert result["success"] is True, result
    assert result["real_planning_qualified"] is True
    # The orchestration child owns the planning identity: the foreign input
    # and metadata run ids never reach the model turn.
    assert calls[0]["context"]["run_id"] == "pm-owned-child"
    assert "foreign-input-child" not in str(calls[0]["context"])
    assert "foreign-turn" not in str(calls[0]["context"])


@pytest.mark.parametrize("revision", ["none", "valid", "invalid", "failure"])
async def test_pm_quality_gate_and_single_revision_remain_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str
) -> None:
    """The PM quality gate keeps its single revision authority.

    The scenario this test drove reached the retired legacy role session
    entry to observe the physical provider turns and the attempt budget they
    consumed. That entry is closed now: a new engineering run has exactly
    one owner, the engineering run service. The quality gate and the single
    revision stay live server-side contracts, so they are asserted here
    against the adapter's own model seam.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id=f"retired-pm-quality-gate-{revision}",
        workspace=str(tmp_path),
    )
    port, _admission, _resolver, _facts = _authority(
        tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm"))
    )
    workspace = tmp_path / "workspace"
    adapter = PMAdapter(workspace=str(workspace))
    calls: list[dict[str, Any]] = []
    outputs: list[Any] = [{"role": "assistant", "content": _contracts(valid=revision == "none")}]
    if revision != "none":
        outputs.append(
            RuntimeError("local_transport_failed")
            if revision == "failure"
            else _pm_revision_reply(_verifier_revision_response)
            if revision == "valid"
            else {"role": "assistant", "content": "invalid reply"}
        )
    _pm_adapter_model(adapter, outputs, calls)
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        result = await adapter._run_pm_stage("pm-task", "Create model", {}, {"run_id": "pm-owned-child"})
    assert result["success"] is (revision in {"none", "valid"}), (result, calls)
    assert len(calls) == (1 if revision == "none" else 2)
    signals = result["quality_gate"]["signals"]
    revisions = [s for s in signals if s["code"] == "pm.contracts.quality_revision"]
    assert len(revisions) == (0 if revision == "none" else 1)
    if revisions:
        assert revisions[0]["score"] == 40
        assert revisions[0]["critical_issues"] == ["verification_commands_missing"]
        assert revisions[0]["parsed_task_count"] == 1
    if revision in {"invalid", "failure"}:
        assert result["tasks_created"] == 0
        assert result.get("real_planning_qualified", False) is False


async def test_foreign_pm_child_under_same_grant_stays_with_the_run_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreign PM child identity stays with the current run owner.

    The scenario this test drove reached the retired legacy role session
    entry and observed that the retired kernel refused a foreign child run
    under the same grant before any transport. That entry is closed now: a
    new engineering run has exactly one owner, the engineering run service,
    which owns the child identity decision. The adapter therefore forwards
    the requested child identity unchanged.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id="retired-pm-foreign-child",
        workspace=str(tmp_path),
    )
    port, _admission, _resolver, facts = _authority(
        tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm"))
    )
    workspace = tmp_path / "workspace"
    adapter = PMAdapter(workspace=str(workspace))
    calls: list[dict[str, Any]] = []
    _pm_adapter_model(adapter, [{"role": "assistant", "content": _contracts(valid=True)}], calls)
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        first = await adapter._call_role_llm("Plan model", context={"run_id": "pm-owned-child"})
        foreign = await adapter._call_role_llm("Plan model", context={"run_id": "pm-foreign-child"})
    assert first["success"] is True
    assert foreign["success"] is True
    assert [call["context"]["run_id"] for call in calls] == ["pm-owned-child", "pm-foreign-child"]


@pytest.mark.parametrize("governed_context", [False, True])
async def test_standalone_support_and_independent_pm_task_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, governed_context: bool
) -> None:
    """Standalone PM support keeps independent task identities.

    The scenario this test drove reached the retired legacy role session
    entry to observe the turn request identities it minted. That entry is
    closed now: a new engineering run has exactly one owner, the engineering
    run service, which mints the turn identity. The adapter keeps the
    requested child identity per task, so the independent PM tasks stay
    independent.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id=f"retired-pm-independent-identities-{governed_context}",
        workspace=str(tmp_path),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    correction = (
        _pm_revision_reply(_verifier_revision_response)
        if governed_context
        else {"role": "assistant", "content": _contracts(valid=True)}
    )
    calls: list[dict[str, Any]] = []
    for child in ("pm-independent-a", "pm-independent-b"):
        adapter = PMAdapter(workspace=str(workspace))
        _pm_adapter_model(
            adapter,
            [
                {"role": "assistant", "content": _contracts(valid=False)},
                correction,
                {"role": "assistant", "content": _contracts(valid=False)},
                correction,
            ],
            calls,
        )
        result = await adapter._run_pm_stage(
            child, "Create model", {}, {"run_id": child} if governed_context else {}
        )
        assert result["success"] is True
    assert len(calls) == 4
    assert [call["context"].get("run_id") for call in calls] == (
        ["pm-independent-a", "pm-independent-a", "pm-independent-b", "pm-independent-b"]
        if governed_context
        else [None, None, None, None]
    )


