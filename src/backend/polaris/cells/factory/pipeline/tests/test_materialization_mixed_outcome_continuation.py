"""Mixed native tool failures must retain progress and close the actual failed attempt."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    validate_director_handoff_from_payload,
)
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as materialization,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture
from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import (
    bound_director_scope as bound_director_scope,
)
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)
from polaris.cells.roles.adapters.public import (
    create_director_directed_effect_mutation_port,
    directed_effect_policy_service,
)
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.cells.roles.profile.public.service import RoleProfileRegistry, profile_from_dict
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1, TaskRuntimeService
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority
from polaris.kernelone.llm.engine.executor import AIExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_native_failure, post_revalidation_failure, quality_host",
    [(False, False, False), (True, False, False), (True, True, False), (True, False, True)],
)
async def test_engine_http_mixed_cli_failure_keeps_test_progress_and_repairs_entry_on_same_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_native_failure: bool,
    post_revalidation_failure: bool,
    quality_host: bool,
) -> None:
    """Provider is a local native-tool server; tools, CAS, owner close and sealed gates are real."""
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        failing=True,
        entrypoint=True,
        compiled_entrypoint=True,
        build_script_name="compile_project.py",
        stdlib_verifier=quality_host,
        task_id="DEO-LIFECYCLE",
        run_id="run",
    )
    workspace = executor.workspace
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "system"))
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    contract = port.completion_contract
    if quality_host:
        (workspace / "build.json").write_text('{"output":"dist/entrypoint.pyc"}\n', encoding="utf-8")
    for artifact in contract.obligations.artifacts:
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )
    initial = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    observations = [initial.to_dict()]
    original_test = (workspace / "tests/test_output.py").read_bytes()
    commands = ["python compile_project.py", "python -m pytest -q tests/test_output.py", "python dist/entrypoint.pyc"]
    if quality_host:
        commands[1] = "python -m unittest discover -s tests"
    bodies: list[dict[str, Any]] = []
    wire_steps: list[tuple[str, dict[str, Any]] | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            bodies.append(body)
            step = wire_steps.pop(0)
            content = (
                [{"type": "tool_use", "id": f"native-{len(bodies)}", "name": step[0], "input": step[1]}]
                if step
                else [{"type": "text", "text": "This repair round is finished."}]
            )
            payload = json.dumps(
                {
                    "id": f"message-{len(bodies)}",
                    "type": "message",
                    "role": "assistant",
                    "model": "director-fixture",
                    "content": content,
                    "stop_reason": "tool_use" if step else "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    profile = profile_from_dict(
        {
            "role_id": "director",
            "display_name": "Director",
            "model": "director-fixture",
            "provider_id": "anthropic_compat",
            "provider_type": "anthropic_compat",
            "prompt_policy": {"core_template_id": "director"},
            "tool_policy": {
                "whitelist": ["read_file", "repo_rg", "repo_tree", "write_file", "edit_file", "execute_command"],
                "allow_code_write": True,
                "allow_command_execution": True,
                "max_tool_calls_per_turn": 20,
            },
            "data_policy": {"data_subdir": "director"},
            "context_policy": {"max_context_tokens": 1_000_000},
        }
    )
    registry = RoleProfileRegistry()
    registry.register(profile)
    policy = directed_effect_policy_service.create_director_effect_policy_snapshot_port(str(workspace))
    fence = create_directed_effect_fence_ports()
    dependencies = DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=policy,
        fence_admin_port=fence.admin,
        mutation_port=create_director_directed_effect_mutation_port(
            workspace=str(workspace),
            policy_snapshot_port=policy,
            fence_consume_port=fence.consume,
        ),
    )

    class HttpInvoker:
        async def call_decision(self, **kwargs: Any) -> dict[str, Any]:
            prepared = await LLMRequestPreparer(workspace=str(workspace))._prepare_llm_request(
                profile=kwargs["profile"],
                system_prompt=kwargs["system_prompt"],
                context=kwargs["context"],
                temperature=0.0,
                max_tokens=20_000,
                stream=False,
            )
            engine = AIExecutor(workspace=str(workspace))
            monkeypatch.setattr(
                engine,
                "_get_provider_config",
                lambda _: {
                    "type": "anthropic_compat",
                    "base_url": endpoint,
                    "api_key": "local-test",
                    "timeout": 10,
                    "retries": 0,
                    "model_specific": {
                        "director-fixture": {"max_context_tokens": 1_000_000, "max_output_tokens": 20_000}
                    },
                },
            )
            invoked = await engine._execute_invoke(
                replace(prepared.ai_request, provider_id="anthropic_compat", model="director-fixture"),
                run.id,
            )
            assert invoked.ok, invoked.error
            return {
                "content": invoked.output,
                "model": "director-fixture",
                "usage": invoked.usage.to_dict() if invoked.usage is not None else {},
                "tool_calls": [
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"], ensure_ascii=False),
                        },
                    }
                    for block in (invoked.raw or {}).get("content", [])
                    if block.get("type") == "tool_use"
                ],
            }

    service = RoleRuntimeService(directed_effect_runtime=dependencies, directed_effect_required=True)
    service._kernels[str(workspace)] = RoleExecutionKernel(
        workspace=str(workspace),
        registry=registry,
        llm_invoker=cast(Any, HttpInvoker()),
        directed_effect_runtime=dependencies,
        directed_effect_required=True,
    )
    first_attempt = _setup_attempt(str(workspace))
    paths = [
        "src/module.py",
        "tests/test_output.py",
        "package.json",
        "src/entrypoint.py",
        "build.json",
        "compile_project.py",
    ]
    ce_task = ChiefEngineerPortfolioTaskV1(
        task_id=first_attempt.external_task_id,
        objective="Build and test module",
        target_files=tuple(paths),
        scope_paths=tuple(paths),
        entrypoint_targets=("src/entrypoint.py",),
    )
    portfolio = port.blueprint_portfolio
    generated = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=ce_task.task_id,
            workspace=str(workspace),
            objective=ce_task.objective,
            run_id=run.id,
            context={
                "task": ce_task.to_dict(),
                "target_files": paths,
                "scope_paths": paths,
                "acceptance_criteria": ["The built module passes tests and the compiled CLI prints ready"],
                "execution_checklist": ["Run the approved build, test and entrypoint commands"],
                **portfolio.to_task_blueprint_context(),
            },
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, ce_task.task_id),
        )
    )
    handoff = validate_director_handoff_from_payload(
        str(workspace),
        {"task_id": ce_task.task_id, "blueprint_id": generated.blueprint_id},
        require_strict=True,
    )
    assert handoff["allowed"], json.dumps(handoff, ensure_ascii=False)
    TaskRuntimeService(str(workspace)).update_task_row(
        first_attempt.task_id,
        metadata={
            "factory_run_id": run.id,
            "external_task_id": ce_task.task_id,
            "target_files": paths,
            "blueprint_id": generated.blueprint_id,
            "task_completion_projection": handoff["task_completion_projection"],
            "control_plane_job_token": handoff["job_token"],
        },
    )
    attempts = []
    native_results = []
    round_inputs: list[list[str]] = []

    async def director(_executor: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        index = len(attempts)
        round_inputs.append(kwargs["artifact_quality_errors"])
        attempt = first_attempt
        if index:
            assert "required entrypoint artifact missing" in "\n".join(round_inputs[-1])
            assert (workspace / "src/module.py").read_text(encoding="utf-8") == "value = 8\n"
            owner = TaskRuntimeService(str(workspace))
            reopened = owner.reopen_task_row(attempt.task_id, reason="workspace_quality_gate_failed")
            assert reopened["status"] == "pending", reopened
            claimed = owner.claim_execution(
                attempt.task_id,
                worker_id="worker",
                role_id="director",
                run_id=run.id,
                external_task_id=attempt.external_task_id,
                selection_source="test",
            )
            assert claimed["success"], claimed
            attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
        attempts.append(attempt)
        path, before, after = (
            ("src/module.py", "value = 7", "value = 8")
            if not index
            else ("build.json", "dist/src/entrypoint.pyc", "dist/entrypoint.pyc")
        )
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=workspace,
            candidate_id=f"mixed-repair-{index}",
            target_files=[path],
        )
        round_commands = (
            [commands[1]] if quality_host else [commands[-1]] if index and final_native_failure else commands
        )
        wire_steps.extend(
            [
                ("read_file", {"file": path}),
                ("edit_file", {"file": path, "old_text": before, "new_text": after}),
                *[("execute_command", {"command": command}) for command in round_commands],
                None,
            ]
        )
        paths = [artifact.path for artifact in contract.obligations.artifacts]
        owner_context = executor._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=run.id,
            diagnostics=round_inputs[-1],
            repair_task={"task_id": ce_task.task_id, "blueprint_id": generated.blueprint_id, "target_files": paths},
        )
        token = {**handoff["job_token"], "execution_envelope_hash": owner_context["execution_envelope_hash"]}
        result = await service.execute_role_session(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id=attempt.session_id,
                workspace=str(workspace),
                run_id=run.id,
                task_id=attempt.external_task_id,
                user_message=f"Read {path}, repair its contents, then run the CE build, test and entrypoint commands.",
                context={
                    **owner_context,
                    "job_token": token,
                    "capability_token": token,
                    "control_plane_job_token": token,
                    "task_runtime_guard": True,
                    "task_runtime_execution_attempt_authority": create_task_runtime_execution_attempt_authority(
                        attempt
                    ),
                    "execution_deadline_epoch_seconds": time.time() + 60,
                    "provider_output_tokens": 20_000,
                    "delivery_mode": "materialize_changes",
                    "target_files": paths,
                    "required_write_targets": [path],
                    "verification_authority": "chief_engineer",
                    "verification_commands": commands,
                    "allowed_commands": commands,
                    "project_completion_contract": contract.to_dict(),
                    "task_completion_projection": handoff["task_completion_projection"],
                    "metadata": {"control_plane_job_token": token},
                    "workspace_quality_evidence": {"errors": round_inputs[-1]},
                },
                metadata={"validate_output": False, "max_retries": 0},
                timeout_seconds=60,
                host_kind="director_adapter",
            )
        )
        native_results.append(result)
        (tmp_path / f"native-round-{index}.json").write_text(
            json.dumps({"ok": result.ok, "metadata": result.metadata, "provider_requests": bodies}, default=str),
            encoding="utf-8",
        )
        await guard.seal_effect()
        return [
            {**row["raw_result"], "success": row.get("status") == "success"} for row in result.metadata["tool_results"]
        ], {
            "attempted": True,
            "success": result.ok,
            "repair_mode": "director_llm",
            "task_id": attempt.external_task_id,
            "write_tool_evidence": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "mutation_committed": True,
                "candidate_guard": guard,
                "task_completion_projection": _projection(port.blueprint_portfolio, contract),
            },
        }

    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    if post_revalidation_failure:
        collect = executor._collect_director_stage_materialization_diagnostics
        collection_count = 0

        async def deny_fresh_verification(**kwargs: Any) -> Any:
            nonlocal collection_count
            collection_count += 1
            if collection_count == 3:
                port.denied = True
            return await collect(**kwargs)

        monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", deny_fresh_verification)
    try:
        if quality_host:
            import shlex

            monkeypatch.setattr(
                executor,
                "_workspace_quality_commands",
                lambda _context, *, run=None: [shlex.split(c) for c in commands],
            )
            monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
            monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)

            async def no_deterministic_plan(**_kwargs: Any) -> Any:
                return [], {"attempted": False, "success": False, "repair_mode": "director_deterministic"}

            async def owned_native_director(**kwargs: Any) -> Any:
                return await director(executor, **kwargs)

            # Earlier Factory-stage admission and unrelated deterministic
            # proposal selection are component seams; no ledger PASS is seeded.
            # Command hosts, native tools, owner close and sealed gates are real.
            monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_deterministic_plan)
            monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", owned_native_director)
            passed, report_ref = await quality._run_workspace_quality_checks(
                executor,
                run,
                {"project_id": contract.project_id, "target_files": paths},
            )
            from polaris.kernelone.storage.layout import resolve_storage_roots

            roots = resolve_storage_roots(str(workspace))
            report = json.loads(
                (Path(roots.runtime_root) / report_ref.removeprefix("runtime/")).read_text(encoding="utf-8")
            )
            assert passed is True, report
            assert len(attempts) == 1 and native_results[0].ok is False
            assert (workspace / "src/module.py").read_text(encoding="utf-8") == "value = 8\n"
            assert (workspace / "tests/test_output.py").read_bytes() == original_test
            rows = TaskRuntimeService(str(workspace)).list_task_rows(include_terminal=True)
            assert len(rows) == 1 and str(rows[0]["id"]) == str(first_attempt.task_id)
            assert rows[0]["status"] == "completed", rows
            assert "post_verification_attempt" in json.dumps(report), report
            (tmp_path / "quality-host-result.json").write_text(json.dumps(report), encoding="utf-8")
            return
        result = await materialization._continue_materialization_verifier_repair(
            executor,
            run=run,
            context={},
            result={"ok": False, "post_commit_diagnostics": list(initial)},
            sessions=[],
            observations=observations,
        )
        assert len(attempts) == 2, result
        assert attempts[0].task_id == attempts[1].task_id and attempts[0].session_id != attempts[1].session_id
        assert native_results[0].ok is False and native_results[1].ok is (not final_native_failure)
        assert result["ok"] is (not post_revalidation_failure)
        assert observations[1]["verifier_results"][-1]["receipt_ref"] is None
        assert all(row["passed"] and row["receipt_ref"] for row in observations[2]["verifier_results"])
        assert (workspace / "tests/test_output.py").read_bytes() == original_test
        assert len(TaskRuntimeService(str(workspace)).list_task_rows(include_terminal=True)) == 1
        assert len(bodies) == (10 if final_native_failure else 12)
        if final_native_failure:
            closed = result["llm_repair"]["task_runtime_repair_attempt"]
            assert closed["requested_outcome"] == "completed" and closed["outcome"] == "failed"
            assert (
                closed["settled"] is True
                and closed["completion_rejection"]["code"] == "settlement_effect_outcome_conflict"
            )
            post = result["llm_repair"]["post_verification_attempt"]
            assert post["task_row_id"] == str(first_attempt.task_id)
            assert post["session_id"] not in {attempt.session_id for attempt in attempts}
            assert post["settled"] is True and post["outcome"] == (
                "failed" if post_revalidation_failure else "completed"
            )
            assert post["completion_contract_hash"] == contract.contract_hash
            if post_revalidation_failure:
                assert observations[-1]["blockers"]
                assert not any(row["passed"] for row in observations[-1]["verifier_results"])
        for body in bodies:
            assert "Director" in str(body["system"])
            assert {"read_file", "edit_file", "execute_command"} <= {tool["name"] for tool in body["tools"]}
        (tmp_path / "mixed-outcome-result.json").write_text(
            json.dumps({"result": result, "observations": observations, "provider_requests": bodies}, default=str),
            encoding="utf-8",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
