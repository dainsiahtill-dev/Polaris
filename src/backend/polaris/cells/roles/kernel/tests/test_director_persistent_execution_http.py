"""A canonical Director session repairs a real failing command through DEO tools."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import VerificationCommandAuthorityV1
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.roles.adapters.public import (
    create_director_directed_effect_mutation_port,
    directed_effect_policy_service,
)
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import build_final_request_context_audit_for_request
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_qualification import (
    final_request_snapshot_evidence,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleSemanticRequestIdentityV1,
    bind_factory_role_evidence_authority,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import FactoryPhysicalAttemptTestControlPort
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.cells.roles.kernel.tests.test_role_turn_request_fact_projection import _b32_authority, _B32CutoffPort
from polaris.cells.roles.profile.public.service import RoleProfileRegistry, profile_from_dict
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.kernelone.context.prompt_safety import parse_tool_failure_summary
from polaris.kernelone.llm.engine.contracts import bind_physical_provider_dispatch_port
from polaris.kernelone.llm.engine.executor import AIExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "repair",
        "mixed_denial_engine",
        "mixed_alias_superseded_engine",
        "ce_denial_then_repair",
        "ce_denial_then_repair_engine",
        "ce_denial_then_repair_engine_forged",
        "verification_only",
        "verification_only_ce_command",
        "verification_only_ce_unauthorized_command",
        "verification_unbound_inputs",
        "stale_verifier",
        "outside_scope",
        "read_input_changed",
        "read_input_rerun",
        "read_only_unauthorized_read",
        "read_only_unauthorized_write",
    ],
)
async def test_canonical_director_session_reads_writes_repairs_and_verifies_over_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    """No tool, effect, command, receipt, continuation or settlement is replaced."""
    forged_wire = scenario == "ce_denial_then_repair_engine_forged"
    mixed_batch = scenario in {"mixed_denial_engine", "mixed_alias_superseded_engine"}
    alias_superseded = scenario == "mixed_alias_superseded_engine"
    engine_path = mixed_batch or scenario in {"ce_denial_then_repair_engine", "ce_denial_then_repair_engine_forged"}
    if engine_path and not mixed_batch:
        scenario = "ce_denial_then_repair"
    (tmp_path / "answer.py").write_text("VALUE = 0\n", encoding="utf-8")
    (tmp_path / "verify.py").write_text(
        "from answer import VALUE\nassert VALUE == 42, f'expected 42, got {VALUE}'\nprint('VERIFIED_42')\n",
        encoding="utf-8",
    )
    attempt = _setup_attempt(str(tmp_path))
    authority = create_task_runtime_execution_attempt_authority(attempt)
    command = "python verify.py"
    steps: list[tuple[str, dict[str, Any]] | None] = [
        ("read_file", {"file": "answer.py"}),
        ("write_file", {"file": "answer.py", "content": "VALUE = 1\n"}),
        ("execute_command", {"command": command}),
        ("edit_file", {"file": "answer.py", "old_text": "VALUE = 1", "new_text": "VALUE = 42"}),
        ("execute_command", {"command": command}),
        None,
    ]
    read_only = scenario in {
        "verification_only",
        "verification_only_ce_command",
        "verification_only_ce_unauthorized_command",
        "verification_unbound_inputs",
        "read_input_changed",
        "read_input_rerun",
        "read_only_unauthorized_read",
        "read_only_unauthorized_write",
    }
    if read_only:
        (tmp_path / "answer.py").write_text("VALUE = 42\n", encoding="utf-8")
        steps = [steps[0], steps[4], None]
    if scenario == "read_input_rerun":
        steps = [steps[0], steps[1], ("read_file", {"file": "verify.py"}), steps[1], None]
    elif scenario == "read_only_unauthorized_read":
        (tmp_path / "outside.py").write_text("PRIVATE_INPUT\n", encoding="utf-8")
        steps = [("read_file", {"file": "outside.py"}), None]
    elif scenario == "read_only_unauthorized_write":
        steps = [steps[0], ("write_file", {"file": "answer.py", "content": "VALUE = 41\n"}), None]
    elif scenario == "verification_only_ce_unauthorized_command":
        (tmp_path / "unauthorized.py").write_text("print('UNAUTHORIZED_EXECUTED')\n", encoding="utf-8")
        steps = [steps[0], ("execute_command", {"command": "python unauthorized.py"}), None]
    elif scenario == "ce_denial_then_repair":
        steps.insert(0, ("execute_command", {"command": "pwd && ls -la"}))
    elif scenario == "stale_verifier":
        steps.insert(-1, ("write_file", {"file": "answer.py", "content": "VALUE = 41\n"}))
    elif scenario == "outside_scope":
        steps = [steps[0], ("write_file", {"file": "outside.py", "content": "VALUE = 42\n"}), None]
    bodies: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            bodies.append(body)
            index = len(bodies) - 1
            step = steps[index] if index < len(steps) else None
            if index == 2 and scenario in {"read_input_changed", "read_input_rerun"}:
                updated_verifier = (
                    "raise AssertionError('UPSTREAM_VERIFIER_CHANGED')\n"
                    if scenario == "read_input_changed"
                    else "from answer import VALUE\nassert VALUE == 42\nprint('VERIFIED_42_RERUN')\n"
                )
                (tmp_path / "verify.py").write_text(updated_verifier, encoding="utf-8")
            content = (
                [{"type": "tool_use", "id": f"physical-{index}", "name": step[0], "input": step[1]}]
                if step
                else [{"type": "text", "text": "Completed. The current command printed VERIFIED_42."}]
            )
            if mixed_batch and index == 1:
                content = [
                    {
                        "type": "tool_use",
                        "id": "mixed-denied",
                        "name": "run_command" if alias_superseded else "execute_command",
                        "input": {"command": "pwd && ls -la"},
                    },
                    {
                        "type": "tool_use",
                        "id": "mixed-write",
                        "name": "create_file" if alias_superseded else "write_file",
                        "input": {"path": "answer.py", "text": "VALUE = 1\n"}
                        if alias_superseded
                        else {"file": "answer.py", "content": "VALUE = 1\n"},
                    },
                ]
                if alias_superseded:
                    content.insert(
                        1,
                        {
                            "type": "tool_use",
                            "id": "mixed-superseded",
                            "name": "write_file",
                            "input": {"file": "answer.py", "content": "VALUE = 0\n"},
                        },
                    )
            payload = json.dumps(
                {
                    "id": f"message-{index}",
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
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")

    qualified_requests: list[dict[str, Any]] = []
    strict_control = FactoryPhysicalAttemptTestControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    strict_control.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role="director",
            stage="director_execution",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="native-feedback-http",
            execution_authority_hash="f" * 64,
            attempt_budget=7,
        )
    )
    cutoff = _B32CutoffPort()
    strict_authority = _b32_authority(
        cutoff,
        role="director",
        physical_attempt_control_port=strict_control,
        execution_authority_hash="f" * 64,
        attempt_budget=7,
    )

    class HttpInvoker:
        async def call_decision(self, **kwargs: Any) -> dict[str, Any]:
            strict = mixed_batch or scenario == "ce_denial_then_repair"
            identity = (
                FactoryRoleSemanticRequestIdentityV1(
                    run_id=attempt.run_id,
                    turn_id=f"{attempt.run_id}:turn:{len(bodies)}",
                    call_id=f"{len(bodies) + 1:032x}",
                    request_freeze_id=f"{len(bodies) + 100:032x}",
                )
                if strict
                else None
            )
            with bind_factory_role_evidence_authority(strict_authority) if strict else nullcontext():
                prepared = await LLMRequestPreparer(workspace=str(tmp_path))._prepare_llm_request(
                    profile=kwargs["profile"],
                    system_prompt=kwargs["system_prompt"],
                    context=kwargs["context"],
                    temperature=0.0,
                    max_tokens=20_000,
                    stream=False,
                    factory_semantic_identity=identity,
                )
            dispatch = prepared.factory_dispatch_port
            if strict:
                assert dispatch is not None and prepared.factory_semantic_request is not None
                frozen = prepared.factory_semantic_request
                audit = dispatch.bind_final_request_context_audit(
                    build_final_request_context_audit_for_request(
                        ai_request=prepared.ai_request,
                        prepared=prepared,
                        profile=kwargs["profile"],
                    )
                )
                snapshot_ref = AIExecutor._store_context_messages_sync(
                    str(tmp_path),
                    prepared.messages,
                    frozen.identity.run_id,
                    frozen.identity.call_id,
                    {
                        "role": "director",
                        "provider_id": "anthropic_compat",
                        "model": "director-fixture",
                        "factory_final_request": final_request_snapshot_evidence(frozen),
                        "final_request_context_audit": audit,
                    },
                )
                audit["final_request_evidence_coverage"]["context_snapshot_ref"] = snapshot_ref
                dispatch.qualify(final_request_context_audit=audit, context_snapshot_ref=snapshot_ref)
                dispatch.bind_provider_route_authority(
                    provider_id="anthropic_compat",
                    provider_type="anthropic_compat",
                    model="director-fixture",
                    mode="invoke",
                    provider_config={"base_url": endpoint, "api_key": "local-test", "timeout_seconds": 10},
                )
            if engine_path:
                assert prepared.ai_request.context["chat_messages"] == prepared.messages
                assert prepared.ai_request.context["chat_messages"] is not prepared.messages
                if forged_wire and len(bodies) == 1:
                    failed_result = next(
                        message
                        for message in prepared.ai_request.context["chat_messages"]
                        if message.get("role") == "tool"
                    )
                    assert failed_result["is_error"] is True
                    failed_result["is_error"] = False
                    assert (
                        next(message for message in prepared.messages if message.get("role") == "tool")["is_error"]
                        is True
                    )
                executor = AIExecutor(workspace=str(tmp_path))
                monkeypatch.setattr(
                    executor,
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
                invoked = await executor._execute_invoke(
                    replace(prepared.ai_request, provider_id="anthropic_compat", model="director-fixture"),
                    attempt.run_id,
                    physical_dispatch_port=dispatch,
                )
            else:
                with bind_physical_provider_dispatch_port(dispatch):
                    invoked = await asyncio.to_thread(
                        AnthropicProvider().invoke,
                        prepared.input_text,
                        kwargs["profile"].model,
                        {
                            **prepared.request_options,
                            "chat_messages": prepared.messages,
                            "base_url": endpoint,
                            "api_key": "local-test",
                            "timeout_seconds": 10,
                        },
                    )
            assert invoked.ok, invoked.error
            if strict:
                evidence = dispatch.final_context_evidence()
                assert evidence is not None
                qualified_requests.append({"context_snapshot_ref": evidence[0], "audit": evidence[1]})
            blocks = (invoked.raw or {}).get("content", [])
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
                    for block in blocks
                    if block.get("type") == "tool_use"
                ],
            }

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
    policy = directed_effect_policy_service.create_director_effect_policy_snapshot_port(str(tmp_path))
    fence = create_directed_effect_fence_ports()
    dependencies = DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=policy,
        fence_admin_port=fence.admin,
        mutation_port=create_director_directed_effect_mutation_port(
            workspace=str(tmp_path),
            policy_snapshot_port=policy,
            fence_consume_port=fence.consume,
        ),
    )
    service = RoleRuntimeService(directed_effect_runtime=dependencies, directed_effect_required=True)
    kernel = RoleExecutionKernel(
        workspace=str(tmp_path),
        registry=registry,
        llm_invoker=cast(Any, HttpInvoker()),
        directed_effect_runtime=dependencies,
        directed_effect_required=True,
    )
    service._kernels[str(tmp_path)] = kernel
    token: dict[str, Any] = {
        "token_id": "job-persistent-director",
        "run_id": attempt.run_id,
        "factory_run_id": attempt.run_id,
        "project_id": "generic-project",
        "stage": "director_execution",
        "contract_hash": "a" * 64,
        "blueprint_hash": "b" * 64,
        "execution_envelope_hash": "e" * 64,
        "target_files": [] if read_only else ["answer.py", "optional.md"],
        "allowed_write_paths": [] if read_only else ["answer.py", "optional.md"],
        "allowed_read_paths": ["answer.py", "verify.py"],
        "allowed_paths": ["answer.py", "verify.py"] if read_only else ["answer.py", "verify.py", "optional.md"],
        "allowed_commands": [command],
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {
            "enabled_evidence_modalities": ["tool_receipt", "command"],
            "required_evidence_modalities": ["command"],
        },
    }
    original_token = deepcopy(token)
    context = {
        "task_runtime_guard": True,
        "task_runtime_execution_attempt_authority": authority,
        "execution_deadline_epoch_seconds": time.time() + 60,
        "provider_output_tokens": 20_000,
        "delivery_mode": "materialize_changes",
        "target_files": [] if read_only else ["answer.py", "optional.md"],
        "context_files": ["answer.py", "verify.py"] if read_only else [],
        "required_write_targets": [] if read_only else ["answer.py"],
        "verification_authority": "chief_engineer",
        "verification_commands": [command],
        "metadata": {"control_plane_job_token": token},
    }
    if scenario in {
        "verification_only_ce_command",
        "verification_only_ce_unauthorized_command",
        "ce_denial_then_repair",
        "mixed_denial_engine",
        "mixed_alias_superseded_engine",
    }:
        token.pop("allowed_commands")
        context["allowed_commands"] = [command]
        command_authority = VerificationCommandAuthorityV1(
            task_id=attempt.external_task_id, modality="test", argv=("python", "verify.py"), cwd="."
        )
        token["gate_policy"]["command_authority_hashes"] = [command_authority.authority_hash]
        context["project_completion_contract"] = {"verification_command_authority": [command_authority.to_dict()]}
        if scenario == "verification_only_ce_unauthorized_command":
            token["allowed_read_paths"] = ["answer.py", "verify.py", "unauthorized.py"]
            token["allowed_paths"] = ["answer.py", "verify.py", "unauthorized.py"]
            context["context_files"] = ["answer.py", "verify.py", "unauthorized.py"]
        original_token = deepcopy(token)
    if scenario == "verification_unbound_inputs":
        token.pop("allowed_read_paths")
        context["context_files"] = []
        original_token = deepcopy(token)
    try:
        result = await service.execute_role_session(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id=attempt.session_id,
                workspace=str(tmp_path),
                run_id=attempt.run_id,
                task_id=attempt.external_task_id,
                user_message=(
                    "Read answer.py, run the CE verification command and finish without modifying files."
                    if read_only
                    else "Read answer.py, implement VALUE=42, run the CE command, repair failures and finish."
                ),
                context=context,
                metadata={"validate_output": False, "max_retries": 0},
                timeout_seconds=60,
                host_kind="director_adapter",
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        (tmp_path / "http-requests.json").write_text(json.dumps(bodies, ensure_ascii=False, indent=2), encoding="utf-8")
        (tmp_path / "qualified-requests.json").write_text(
            json.dumps(qualified_requests, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if forged_wire:
        assert result.ok is False
        assert "physical_wire_messages_drift" in str(result.error_message)
        assert len(bodies) == 1 and len(qualified_requests) == 1
        assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 0\n"
        assert token == original_token
        budget = strict_control.budget_state("f" * 64)
        assert budget.committed_count == 1 and budget.terminal_count == 1 and budget.settled is True
        return
    if scenario in {
        "stale_verifier",
        "outside_scope",
        "read_input_changed",
        "read_only_unauthorized_read",
        "read_only_unauthorized_write",
        "verification_only_ce_unauthorized_command",
        "verification_unbound_inputs",
    }:
        assert result.ok is False
        if scenario == "read_only_unauthorized_read":
            assert "PRIVATE_INPUT" not in json.dumps(bodies, ensure_ascii=False)
        else:
            assert not (tmp_path / "outside.py").exists()
        assert token == original_token
        if scenario in {"stale_verifier", "read_input_changed"}:
            expected_source = "VALUE = 41\n" if scenario == "stale_verifier" else "VALUE = 42\n"
            assert (tmp_path / "answer.py").read_text(encoding="utf-8") == expected_source
            assert result.metadata["task_boundary_verdict"]["ok"] is False
            assert "verifier" in str(result.metadata["task_boundary_verdict"]).lower()
            assert len(bodies) == len(steps)
        if scenario == "read_only_unauthorized_write":
            assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 42\n"
        if scenario == "verification_only_ce_unauthorized_command":
            assert "UNAUTHORIZED_EXECUTED" not in json.dumps(bodies, ensure_ascii=False)
        if scenario == "verification_unbound_inputs":
            assert "VERIFIED_42" in json.dumps(bodies, ensure_ascii=False)
            assert result.metadata["task_boundary_verdict"]["failed_required_verifiers"] == [command]
        return
    assert result.ok, (result.error_code, result.error_message, result.metadata)
    assert len(bodies) == len(steps)
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert not (tmp_path / "optional.md").exists()
    assert token == original_token
    for body in bodies:
        assert body["max_tokens"] >= 20_000
        assert "Director" in str(body.get("system") or "")
        names = {tool["name"] for tool in body.get("tools", [])}
        assert {"read_file", "repo_rg", "write_file", "edit_file", "execute_command"} <= names
    history = json.dumps(bodies[-1], ensure_ascii=False)
    if scenario in {"repair", "ce_denial_then_repair"}:
        repair_request = json.dumps(bodies[4 if scenario == "ce_denial_then_repair" else 3], ensure_ascii=False)
        assert "expected 42, got 1" in repair_request, repair_request
    if scenario == "ce_denial_then_repair":
        assert len(bodies) == 7
        denied_block = next(
            block
            for message in bodies[1]["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result" and block.get("tool_use_id") == "physical-0"
        )
        assert denied_block["is_error"] is True
        denied_result = parse_tool_failure_summary(denied_block["content"])
        assert denied_result is not None
        assert "deo_command_scope_denied" in denied_result["reason"]
        assert denied_result["arguments"] == {"command": "pwd && ls -la"}
        assert denied_result["approved_command_scope"] == [{"command": command, "cwd": "."}]
        assert len(qualified_requests) == 7
        assert all(row["audit"]["audit_scope"] == "provider_native_wire" for row in qualified_requests)
        assert all(row["audit"]["context_os_audit"]["ok"] for row in qualified_requests)
        budget = strict_control.budget_state("f" * 64)
        assert budget.committed_count == 7 and budget.terminal_count == 7
        assert budget.settled is True
        outcomes = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path), stream="roles.kernel.turn_outcomes", strict_integrity=True, limit=20
            )
        )
        assert outcomes.total == 7
        denied_receipt = outcomes.events[0]["payload"]["outcome"]["execution"]["receipt"]
        assert denied_receipt["failure_count"] == 1
        assert denied_receipt["effect_receipts"] == []
        assert denied_receipt["results"][0]["call_id"] == "physical-0"
        assert denied_receipt["results"][0]["directed_effect_claim_status"] == "not_claimed"
        assert denied_receipt["results"][0]["effect_receipt"] is None
    if mixed_batch:
        assert len(qualified_requests) == len(steps)
        assert all(row["audit"]["audit_scope"] == "provider_native_wire" for row in qualified_requests)
        outcomes = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path), stream="roles.kernel.turn_outcomes", strict_integrity=True, limit=20
            )
        )
        mixed = next(
            row["payload"]["outcome"]["execution"]
            for row in outcomes.events
            if row["payload"]["outcome"].get("execution")
            and any(
                item["call_id"] == "mixed-write"
                for item in row["payload"]["outcome"]["execution"]["receipt"]["results"]
            )
        )
        receipts = {row["call_id"]: row for row in mixed["receipt"]["results"]}
        assert receipts["mixed-write"]["status"] == "success"
        assert receipts["mixed-write"]["effect_receipt"] is not None
        assert receipts["mixed-denied"]["status"] == "error"
        assert receipts["mixed-denied"]["effect_receipt"] is None
        assert receipts["mixed-denied"]["directed_effect_claim_status"] == "not_claimed"
        assert set(receipts) == {row["call_id"] for row in mixed["invocations"]}
        for original_id in receipts:
            blocks = [
                block
                for message in bodies[2]["messages"]
                if isinstance(message.get("content"), list)
                for block in message["content"]
                if block.get("type") in {"tool_use", "tool_result"}
            ]
            assert sum(block.get("id") == original_id for block in blocks) == 1
            assert sum(block.get("tool_use_id") == original_id for block in blocks) == 1
        if alias_superseded:
            assert receipts["mixed-superseded"]["result"]["superseded"] is True
            assert receipts["mixed-superseded"]["effect_receipt"] is None
    assert "VERIFIED_42" in history
    if scenario == "read_input_rerun":
        assert "VERIFIED_42_RERUN" in history
    assert result.metadata["task_boundary_verdict"]["ok"] is True
