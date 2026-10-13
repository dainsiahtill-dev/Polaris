"""Session continuation must retain real effects across kernel turn boundaries."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService


class _PhysicalTurnKernel:
    """Deterministic provider/tool boundary; effects and verifier really execute."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests = []
        self.gate_exits = []
        self.results = []

    async def run(self, role, request):
        return self._execute(role, request)

    async def run_stream(self, role, request):
        yield {"type": "complete", "result": self._execute(role, request)}

    def _execute(self, role, request):
        assert role == "director"
        assert request.context_override["execution_envelope"]["allowed_paths"] == ["answer.py"]
        self.requests.append(request)
        index = len(self.requests) - 1
        file = self.workspace / "answer.py"
        status = "success"
        if index < 3:
            tool = "read_file"
            path = f"input{index}.txt"
            payload = {"file": path, "content": (self.workspace / path).read_text(encoding="utf-8")}
        elif index in (3, 5):
            tool = "write_file" if index == 3 else "edit_file"
            file.write_text("print(41)\n" if index == 3 else "print(42)\n", encoding="utf-8")
            payload = {"file": "answer.py", "ok": True}
        else:
            tool = "execute_command"
            gate = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys; out=subprocess.check_output([sys.executable,'answer.py'],text=True); sys.exit(0 if out.strip()=='42' else 1)",
                ],
                cwd=self.workspace,
                capture_output=True,
                encoding="utf-8",
                check=False,
            )
            self.gate_exits.append(gate.returncode)
            payload = {"command": "python actual_answer_gate", "exit_code": gate.returncode, "stderr": gate.stderr}
            status = "success" if gate.returncode == 0 else "error"
        row = {"call_id": f"call-{index}", "tool_name": tool, "status": status, "result": payload}
        if tool in {"write_file", "edit_file"}:
            row["effect_receipt"] = {"committed": True, "side_effect_class": "write", "after_hash": f"hash-{index}"}
        final = index == 6
        result = RoleTurnResult(
            content="verified" if final else "continue",
            is_complete=final,
            tool_calls=[{"tool": tool, "call_id": row["call_id"]}],
            tool_results=[{"tool_name": tool, "success": status == "success", "result": payload}],
            batch_receipt={
                "batch_id": f"batch-{index}",
                "results": [row],
                "success_count": int(status == "success"),
                "failure_count": int(status != "success"),
            },
            execution_stats={"tool_calls": 1, "llm_calls": 1, "transaction_kernel": True},
            metadata={
                "transaction_kind": "final_answer" if final else "continue_multi_turn",
                "needs_followup_workflow": not final,
                "workflow_reason": "verification_failed" if index == 4 else "observation",
                "turn_commit_receipt": {"transition_id": f"transition-{index}"},
                "turn_outcome": {"turn_id": f"physical-{index}", "completion_status": "success"},
            },
            turn_history=[("assistant", f"turn-{index}")],
            turn_events_metadata=[{"event_id": f"event-{index}", "role": "assistant", "content": f"turn-{index}"}],
        )
        self.results.append(result)
        return result


def _install_runtime_boundary(monkeypatch, workspace, kernel):
    service = RoleRuntimeService()
    monkeypatch.setattr(service, "_get_kernel", lambda _: kernel)
    monkeypatch.setattr(service, "_emit_cognitive_runtime_shadow_artifacts", lambda **_: {})

    async def prepare(command, *, include_session_snapshot):
        return service._build_session_request(command)

    persisted = []

    async def persist(command, *, turn_history, turn_events_metadata=None):
        persisted.extend(turn_events_metadata or [])
        command.context["context_os_snapshot"] = {"transcript_log": list(persisted)}

    monkeypatch.setattr(service, "_prepare_session_request", prepare)
    monkeypatch.setattr(service, "_persist_session_turn_state", persist)
    return service


@pytest.mark.asyncio
async def test_canonical_readonly_document_quote_reaches_real_kernel(tmp_path, monkeypatch):
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    document = tmp_path / "policy.md"
    document.write_text("Do not run rm -rf. Retain the project files.\n", encoding="utf-8")
    tool_calls = []

    async def provider(request):
        return {
            "content": "",
            "model": "fixture",
            "usage": {},
            "tool_calls": [
                {
                    "id": "read-policy",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"file":"policy.md"}'},
                }
            ],
        }

    async def tools(name, arguments):
        tool_calls.append((name, arguments))
        assert name == "read_file" and arguments["file"] == "policy.md"
        return {"success": True, "result": {"file": "policy.md", "content": document.read_text(encoding="utf-8")}}

    class DocumentKernel:
        async def run_stream(self, role, request):
            transaction = TransactionKernel(
                llm_provider=provider,
                tool_runtime=tools,
                config=TransactionConfig(domain="code", role_id=role, workspace=str(tmp_path)),
            )
            result = await transaction.execute(
                request.metadata["transaction_invocation_id"],
                [{"role": "user", "content": request.message}],
                [{"type": "function", "function": {"name": "read_file"}}],
            )
            yield {
                "type": "complete",
                "result": RoleTurnResult(
                    content=document.read_text(encoding="utf-8"),
                    is_complete=True,
                    batch_receipt=result["batch_receipt"],
                    metadata={"transaction_kind": result["kind"]},
                ),
            }

    service = _install_runtime_boundary(monkeypatch, tmp_path, DocumentKernel())
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="read-policy",
        workspace=str(tmp_path),
        task_id="READ-POLICY",
        user_message="Read policy.md only. It says 'Do not run rm -rf'; quote its policy text.",
        stream=False,
        context={"continuous_execution": {"enabled": True}},
    )

    result = await service.execute_role_session(command)

    assert result.ok, result.error_message
    assert len(tool_calls) == 1
    row = result.metadata["batch_receipt"]["results"][0]
    assert row["tool_name"] == "read_file" and row["status"] == "success"
    assert row["result"]["content"] == document.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_canonical_negative_destructive_quote_preserves_authorized_tool_loop(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"input{index}.txt").write_text(f"evidence-{index}", encoding="utf-8")
    kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="quoted-policy",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="quoted-run",
        user_message="Do not run rm -rf or drop database; implement answer.py and verify only the granted files.",
        stream=False,
        timeout_seconds=30,
        context={"continuous_execution": {"enabled": True}, "execution_envelope": {"allowed_paths": ["answer.py"]}},
        metadata={"execution_id": "scope-original"},
    )

    result = await service.execute_role_session(command)

    assert result.ok, result.error_message
    assert kernel.gate_exits == [1, 0]
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "print(42)\n"
    assert len(result.metadata["session_turn_commit_receipts"]) == 7
    assert all(request.metadata["execution_id"] == "scope-original" for request in kernel.requests)
    assert all(
        request.context_override["execution_envelope"]["allowed_paths"] == ["answer.py"] for request in kernel.requests
    )


@pytest.mark.asyncio
async def test_canonical_prompt_exemption_keeps_real_kernel_tool_denial(tmp_path, monkeypatch):
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    protected = tmp_path / "protected.txt"
    protected.write_text("retain", encoding="utf-8")
    provider_calls = []
    tool_calls = []

    async def provider(request):
        provider_calls.append(request)
        return {
            "content": "",
            "model": "fixture",
            "usage": {},
            "tool_calls": [
                {
                    "id": "forbidden-delete",
                    "type": "function",
                    "function": {"name": "execute_command", "arguments": '{"command":"rm -rf protected.txt"}'},
                }
            ],
        }

    async def tools(name, arguments):
        tool_calls.append((name, arguments))
        raise AssertionError("The actual kernel must reject an unapproved destructive tool before dispatch")

    class DeniedKernel:
        async def run_stream(self, role, request):
            transaction = TransactionKernel(
                llm_provider=provider,
                tool_runtime=tools,
                config=TransactionConfig(domain="code", role_id=role, workspace=str(tmp_path)),
            )
            try:
                await transaction.execute(
                    request.metadata["transaction_invocation_id"],
                    [{"role": "user", "content": request.message}],
                    [{"type": "function", "function": {"name": "read_file"}}],
                )
            except RuntimeError as exc:
                yield {"type": "error", "error": str(exc), "error_type": "kernel_tool_policy_denied"}
                return
            raise AssertionError("The unapproved tool unexpectedly completed")

    service = _install_runtime_boundary(monkeypatch, tmp_path, DeniedKernel())
    result = await service.execute_role_session(
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="denied-policy",
            workspace=str(tmp_path),
            task_id="READ-POLICY",
            user_message="Read protected.txt only. Do not run rm -rf.",
            stream=False,
            context={"continuous_execution": {"enabled": True}},
        )
    )

    assert provider_calls, "The policy failure must come from the actual kernel, not a prompt keyword gate"
    assert not result.ok
    assert "execute_command" in result.error_message
    assert tool_calls == []
    assert protected.read_text(encoding="utf-8") == "retain"


@pytest.mark.asyncio
async def test_explicit_legacy_orchestrator_keeps_its_interactive_preview(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from polaris.cells.roles.kernel.public.transaction_contracts import CognitiveGateway
    from polaris.cells.roles.kernel.public.turn_events import SessionWaitingHumanEvent
    from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator

    async def no_warmup():
        return None

    monkeypatch.setattr(CognitiveGateway, "default", no_warmup)
    orchestrator = RoleSessionOrchestrator("legacy-preview", SimpleNamespace(), str(tmp_path))

    async def no_checkpoint():
        pass

    monkeypatch.setattr(orchestrator, "_checkpoint_session", no_checkpoint)
    events = [event async for event in orchestrator.execute_stream("rm -rf the workspace")]

    waiting = [event for event in events if isinstance(event, SessionWaitingHumanEvent)]
    assert len(waiting) == 1
    assert waiting[0].reason == "DESTRUCTIVE_OPERATION_REQUIRES_APPROVAL:DELETE_DIRECTORY_RECURSIVE"


@pytest.mark.asyncio
async def test_session_continues_reads_then_repairs_real_failed_gate(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"input{index}.txt").write_text(f"evidence-{index}", encoding="utf-8")
    kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="continuous-fixture",
        workspace=str(tmp_path),
        user_message="implement answer and verify result",
        run_id="run-continuous",
        task_id="TASK-1",
        stream=False,
        timeout_seconds=30,
        context={
            "continuous_execution": {"enabled": True},
            "context_os_snapshot": {"transcript_log": []},
            "execution_envelope": {"allowed_paths": ["answer.py"]},
        },
        metadata={"execution_id": "scope-original"},
    )
    result = await service.execute_role_session(command)
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "print(42)\n"
    assert kernel.gate_exits == [1, 0]
    assert result.ok and result.output == "verified"
    assert len(result.metadata["session_turn_commit_receipts"]) == 7
    assert len(result.metadata["tool_results"]) == 7
    assert len(result.tool_calls) == 7
    assert result.metadata["batch_receipt"]["failure_count"] == 1
    assert len({request.metadata["transaction_invocation_id"] for request in kernel.requests}) == 7
    assert all(request.metadata["execution_id"] == "scope-original" for request in kernel.requests)
    assert len(kernel.requests[-1].context_override["context_os_snapshot"]["transcript_log"]) == 6


@pytest.mark.asyncio
async def test_real_director_adapter_defaults_to_continuation(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"input{index}.txt").write_text("evidence", encoding="utf-8")
    kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="default-fixture",
        workspace=str(tmp_path),
        user_message="implement answer and verify",
        task_id="TASK-1",
        run_id="run-default",
        domain="code",
        host_kind="director_adapter",
        stream=False,
        context={"context_os_snapshot": {"transcript_log": []}, "execution_envelope": {"allowed_paths": ["answer.py"]}},
        metadata={"execution_id": "scope-original"},
    )
    result = await service.execute_role_session(command)
    assert result.ok and kernel.gate_exits == [1, 0]
    assert all(request.context_override["continuous_execution"]["enabled"] is True for request in kernel.requests)


@pytest.mark.asyncio
async def test_explicit_disable_keeps_one_user_turn(tmp_path, monkeypatch):
    (tmp_path / "input0.txt").write_text("evidence", encoding="utf-8")
    kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="disabled",
        workspace=str(tmp_path),
        user_message="implement answer",
        task_id="TASK-1",
        host_kind="director_adapter",
        stream=False,
        context={"continuous_execution": {"enabled": False}, "execution_envelope": {"allowed_paths": ["answer.py"]}},
    )
    await service.execute_role_session(command)
    assert len(kernel.requests) == 1
    assert not (tmp_path / "answer.py").exists()


@pytest.mark.asyncio
async def test_continuation_cannot_bypass_task_runtime_authority(tmp_path, monkeypatch):
    from polaris.cells.roles.runtime.public.contracts import RoleRuntimeError

    kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="denied",
        workspace=str(tmp_path),
        user_message="implement answer",
        stream=False,
        context={"continuous_execution": {"enabled": True}, "task_runtime_guard": True},
    )
    with pytest.raises(RoleRuntimeError, match="valid TaskRuntime execution attempt"):
        await service.execute_role_session(command)
    assert kernel.requests == []


@pytest.mark.asyncio
async def test_shared_deadline_is_not_renewed_between_turns(tmp_path, monkeypatch):
    class SlowKernel(_PhysicalTurnKernel):
        async def run_stream(self, role, request):
            await asyncio.sleep(0.65)
            yield {"type": "complete", "result": self._execute(role, request)}

    for index in range(3):
        (tmp_path / f"input{index}.txt").write_text("evidence", encoding="utf-8")
    kernel = SlowKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="deadline",
        workspace=str(tmp_path),
        user_message="implement answer",
        stream=False,
        timeout_seconds=1,
        context={"continuous_execution": {"enabled": True}, "execution_envelope": {"allowed_paths": ["answer.py"]}},
    )
    started = time.monotonic()
    result = await service.execute_role_session(command)
    assert not result.ok
    assert "continuous_execution_deadline_exceeded" in str(result.error_message)
    assert time.monotonic() - started < 1.5
    assert len(kernel.requests) == 1
    assert not (tmp_path / "answer.py").exists()


@pytest.mark.asyncio
async def test_provider_timeout_does_not_replace_larger_enclosing_session_budget(tmp_path, monkeypatch):
    class SlowKernel(_PhysicalTurnKernel):
        async def run_stream(self, role, request):
            await asyncio.sleep(0.2)
            yield {"type": "complete", "result": self._execute(role, request)}

    for index in range(3):
        (tmp_path / f"input{index}.txt").write_text("evidence", encoding="utf-8")
    kernel = SlowKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="provider-budget",
        workspace=str(tmp_path),
        user_message="implement and verify answer",
        stream=False,
        timeout_seconds=1,
        context={
            "continuous_execution": {"enabled": True},
            "request_timeout_seconds": 5,
            "timeout_seconds": 1,
            "execution_envelope": {"allowed_paths": ["answer.py"]},
        },
    )
    result = await service.execute_role_session(command)
    assert result.ok and len(kernel.requests) == 7
    assert all(request.metadata["timeout_seconds"] == 1 for request in kernel.requests)
    assert kernel.requests[-1].context_override["request_timeout_seconds"] < 5


@pytest.mark.asyncio
async def test_missing_complete_event_is_terminal(tmp_path, monkeypatch):
    class IncompleteKernel:
        async def run_stream(self, role, request):
            yield {"type": "content_chunk", "content": "looks complete"}

    service = _install_runtime_boundary(monkeypatch, tmp_path, IncompleteKernel())
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="incomplete",
        workspace=str(tmp_path),
        user_message="implement answer",
        stream=False,
        context={"continuous_execution": {"enabled": True}},
    )
    result = await service.execute_role_session(command)
    assert not result.ok and "role_stream_incomplete" in str(result.error_message)


@pytest.mark.asyncio
async def test_provider_error_cannot_be_hidden_by_later_complete(tmp_path, monkeypatch):
    class ErrorKernel:
        async def run_stream(self, role, request):
            yield {"type": "error", "error": "provider_transport_failure"}
            yield {"type": "complete", "result": RoleTurnResult(content="done", is_complete=True)}

    service = _install_runtime_boundary(monkeypatch, tmp_path, ErrorKernel())
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="transport",
        workspace=str(tmp_path),
        user_message="implement answer",
        stream=False,
        context={"continuous_execution": {"enabled": True}},
    )
    result = await service.execute_role_session(command)
    assert not result.ok and "provider_transport_failure" in str(result.error_message)


@pytest.mark.parametrize("different_targets, expected_stagnation", [(True, False), (False, True)])
def test_continuation_policy_measures_receipt_progress_not_read_count(different_targets, expected_stagnation):
    from polaris.cells.roles.kernel.public.turn_contracts import TurnContinuationMode, TurnOutcomeEnvelope, TurnResult
    from polaris.cells.roles.runtime.internal.continuation_policy import ContinuationPolicy, OrchestratorSessionState

    state = OrchestratorSessionState(session_id="progress", turn_count=4)
    state.structured_findings = {
        "_canonical_role_kernel": True,
        "_findings_trajectory": [{"task_progress": "content_gathered"}] * 4,
    }
    state.turn_history = [
        {
            "batch_receipt": {
                "results": [
                    {
                        "tool_name": "read_file",
                        "arguments": {"file": f"file-{index if different_targets else 0}.py"},
                        "status": "success",
                        "result": {"content": "new facts"},
                    }
                ]
            }
        }
        for index in range(4)
    ]
    envelope = TurnOutcomeEnvelope(
        turn_result=TurnResult(turn_id="progress-turn", kind="continue_multi_turn", visible_content="", decision={}),
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
    )
    assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is expected_stagnation


@pytest.mark.asyncio
async def test_new_submission_on_same_role_session_receives_its_own_user_message(tmp_path, monkeypatch):
    class CompletedKernel:
        def __init__(self) -> None:
            self.messages = []

        async def run_stream(self, role, request):
            self.messages.append(request.message)
            yield {"type": "complete", "result": RoleTurnResult(content="analysis complete", is_complete=True)}

    kernel = CompletedKernel()
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    for message in ("analyze first request", "analyze second request"):
        await service.execute_role_session(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id="shared-session",
                workspace=str(tmp_path),
                user_message=message,
                stream=False,
                context={"continuous_execution": {"enabled": True}},
            )
        )
    assert kernel.messages == ["analyze first request", "analyze second request"]


def test_local_repair_keeps_session_in_verifying_phase():
    from polaris.cells.roles.kernel.public.turn_contracts import TurnContinuationMode, TurnOutcomeEnvelope, TurnResult
    from polaris.cells.roles.runtime.internal.continuation_policy import OrchestratorSessionState
    from polaris.cells.roles.runtime.internal.session_orchestrator import SessionStateReducer

    state = OrchestratorSessionState(session_id="repair", task_progress="verifying")
    state.structured_findings["_canonical_role_kernel"] = True
    state.session_invariants.freeze("materialize_changes", "implement and verify", "verifying")
    receipt = {
        "results": [
            {
                "tool_name": "edit_file",
                "status": "success",
                "result": {"ok": True},
                "effect_receipt": {"committed": True, "side_effect_class": "write"},
            }
        ]
    }
    envelope = TurnOutcomeEnvelope(
        turn_result=TurnResult(
            turn_id="repair-turn",
            kind="continue_multi_turn",
            visible_content="repaired",
            decision={},
            batch_receipt=receipt,
        ),
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
    )
    SessionStateReducer(state).apply_turn_outcome(envelope, turn_index=1)
    assert state.task_progress == "verifying"
    state.session_invariants.validate("materialize_changes", "implement and verify", state.task_progress)


@pytest.mark.asyncio
async def test_actual_stream_projector_keeps_failed_boundary_write_for_local_repair(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from polaris.cells.control_plane.run_ledger.public.task_boundary import evaluate_task_boundary_verdict
    from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection
    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent

    verdicts = []

    def append_boundary(**kwargs):
        verdict = evaluate_task_boundary_verdict(
            workspace=tmp_path,
            task_id="TASK-1",
            run_id="projector-run",
            target_files=["partial.py", "answer.py"],
        ).to_dict()
        verdicts.append(verdict)
        return verdict

    monkeypatch.setattr(projection, "append_role_turn_task_boundary_verdict", append_boundary)

    class Publisher:
        async def publish_stream_event(self, **kwargs):
            pass

    class ProjectorKernel:
        def __init__(self) -> None:
            self.turn_count = 0
            self.messages = []

        async def run_stream(self, role, request):
            index = self.turn_count
            self.turn_count += 1
            self.messages.append(request.message)
            path = "partial.py" if index == 0 else "answer.py"
            content = "print(42)\n"
            (tmp_path / path).write_text(content, encoding="utf-8")
            invocations = [
                {
                    "call_id": f"write-{index}",
                    "tool_name": "write_file",
                    "arguments": {"file": path, "content": content},
                }
            ]
            rows = [
                {
                    "call_id": f"write-{index}",
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"file": path, "ok": True},
                    "effect_receipt": {"committed": True, "side_effect_class": "write", "after_hash": f"hash-{index}"},
                }
            ]
            if index == 1:
                gate = subprocess.run(
                    [sys.executable, "answer.py"], cwd=tmp_path, capture_output=True, encoding="utf-8", check=False
                )
                rows.append(
                    {
                        "call_id": "gate",
                        "tool_name": "execute_command",
                        "status": "success",
                        "result": {"exit_code": gate.returncode, "stdout": gate.stdout},
                    }
                )
                invocations.append(
                    {
                        "call_id": "gate",
                        "tool_name": "execute_command",
                        "arguments": {"command": f"{sys.executable} answer.py"},
                    }
                )
            projector = projection.StreamEventProjector(
                kernel=SimpleNamespace(workspace=str(tmp_path)),
                role=role,
                profile=SimpleNamespace(role_id=role, model="fixture", provider_id="fixture"),
                request=request,
                fingerprint=SimpleNamespace(full_hash="fixture"),
                context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: None),
                context_result=SimpleNamespace(token_estimate=12),
                stream_run_id="projector-run",
                uep_publisher=Publisher(),
                runtime_tool_policy_audit={},
                tool_filter_audit=None,
            )
            receipt = {
                "batch_id": f"projector-batch-{index}",
                "results": rows,
                "success_count": len(rows),
                "failure_count": 0,
            }
            projected = await projector.project(
                CompletionEvent(
                    turn_id=f"projector-{index}",
                    status="success",
                    turn_kind="tool_batch_with_receipt",
                    batch_receipt=receipt,
                    turn_outcome={"execution": {"invocations": invocations, "receipt": receipt}},
                    commit_receipt={"transition_id": f"committed-{index}"},
                )
            )
            assert projected is not None
            yield projected.event

    kernel = ProjectorKernel()
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="actual-projector",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="projector-run",
        user_message="implement both files and verify",
        stream=False,
        context={"continuous_execution": {"enabled": True}},
        metadata={"validate_output": False},
    )
    result = await service.execute_role_session(command)
    assert result.ok, result.error_message
    assert verdicts[0]["status"] == "incomplete_materialization"
    assert verdicts[-1]["status"] == "completed_verified"
    assert kernel.turn_count == 2
    assert "incomplete_materialization" in kernel.messages[1]
    assert "answer.py" in kernel.messages[1]
    assert len(result.metadata["session_turn_commit_receipts"]) == 2
    assert [row["result"].get("file") for row in result.metadata["batch_receipt"]["results"][:2]] == [
        "partial.py",
        "answer.py",
    ]


@pytest.mark.asyncio
async def test_public_stream_and_nonstream_share_continuous_kernel_turns(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"input{index}.txt").write_text("evidence", encoding="utf-8")
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="parity",
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="parity-run",
        user_message="implement answer and verify",
        stream=False,
        context={"continuous_execution": {"enabled": True}, "execution_envelope": {"allowed_paths": ["answer.py"]}},
    )
    nonstream_kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, nonstream_kernel)
    nonstream = await service.execute_role_session(command)
    stream_kernel = _PhysicalTurnKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, stream_kernel)
    monkeypatch.setattr(service, "emit_strategy_receipt", lambda *_: tmp_path / "strategy-fixture.json")
    events = [event async for event in service.stream_chat_turn(command)]
    final = next(event["result"] for event in reversed(events) if event.get("type") == "complete")
    assert final.is_complete and final.error is None
    assert len(stream_kernel.requests) == len(nonstream_kernel.requests) == 7
    assert final.metadata["session_turn_commit_receipts"] == nonstream.metadata["session_turn_commit_receipts"]
    assert final.batch_receipt == nonstream.metadata["batch_receipt"]


@pytest.mark.parametrize("with_receipt,error_type", [(True, "control_plane_failure"), (False, "task_boundary_failed")])
@pytest.mark.asyncio
async def test_terminal_error_retains_committed_effects_without_inventing_recovery(
    tmp_path, monkeypatch, with_receipt, error_type
):
    class FailedKernel:
        def __init__(self) -> None:
            self.calls = 0

        async def run_stream(self, role, request):
            self.calls += 1
            rows = (
                [
                    {
                        "tool_name": "write_file",
                        "call_id": "committed-write",
                        "status": "success",
                        "result": {"file": "partial.py"},
                        "effect_receipt": {"committed": True},
                    }
                ]
                if with_receipt
                else []
            )
            if with_receipt:
                (tmp_path / "partial.py").write_text("VALUE = 42\n", encoding="utf-8")
            metadata = {"transaction_kind": "continue_multi_turn", "needs_followup_workflow": True}
            if with_receipt:
                metadata["turn_commit_receipt"] = {"transition_id": "committed-turn"}
            yield {
                "type": "error",
                "error_type": error_type,
                "error": "owner_failure",
                "result": RoleTurnResult(
                    content="",
                    error="owner_failure",
                    is_complete=False,
                    batch_receipt={"results": rows, "success_count": len(rows), "failure_count": 0},
                    metadata=metadata,
                ),
            }

    kernel = FailedKernel()
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="failure-facts",
        workspace=str(tmp_path),
        user_message="implement task",
        stream=False,
        context={"continuous_execution": {"enabled": True}},
    )
    result = await service.execute_role_session(command)
    assert not result.ok and kernel.calls == 1
    assert "owner_failure" in str(result.error_message)
    assert len(result.metadata["batch_receipt"]["results"]) == int(with_receipt)
    assert len(result.metadata["session_turn_commit_receipts"]) == int(with_receipt)


@pytest.mark.asyncio
async def test_failed_batch_with_empty_turn_error_feeds_safe_diagnostic_to_next_turn(tmp_path, monkeypatch):
    class FailedEditKernel:
        def __init__(self) -> None:
            self.requests = []

        async def run_stream(self, role, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                receipt = {
                    "results": [
                        {
                            "tool_name": "edit_file",
                            "call_id": "failed-edit",
                            "status": "error",
                            "arguments": {"file": "tests/garden.test.ts", "replace": "RAW_REPLACEMENT_CODE"},
                            "error": "deo_physical_execution_failed: Destructive shrink rejected in tests/garden.test.ts",
                            "result": {
                                "physical_error_type": "destructive_shrink",
                                "physical_error": "Destructive shrink rejected:172 lines of tests/garden.test.ts would become1 line",
                                "effect_recovery": {
                                    "claim_grant_hash": "SECRET_GRANT",
                                    "token": "SECRET_TOKEN",
                                    "receipt_ref": "RAW_RECEIPT",
                                },
                            },
                            "effect_receipt": None,
                        }
                    ],
                    "failure_count": 1,
                    "success_count": 0,
                }
                yield {
                    "type": "complete",
                    "result": RoleTurnResult(
                        content="",
                        error=None,
                        is_complete=False,
                        batch_receipt=receipt,
                        metadata={
                            "transaction_kind": "continue_multi_turn",
                            "needs_followup_workflow": True,
                            "workflow_reason": "observation",
                        },
                    ),
                }
            else:
                receipt = {
                    "results": [
                        {
                            "tool_name": "edit_file",
                            "status": "success",
                            "result": {"ok": True},
                            "effect_receipt": {
                                "committed": True,
                                "side_effect_class": "write",
                                "after_hash": "changed",
                            },
                        }
                    ],
                    "success_count": 1,
                    "failure_count": 0,
                }
                yield {
                    "type": "complete",
                    "result": RoleTurnResult(content="repaired", error=None, is_complete=True, batch_receipt=receipt),
                }

    kernel = FailedEditKernel()
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    result = await service.execute_role_session(
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="failed-batch-feedback",
            workspace=str(tmp_path),
            user_message="implement repair for tests/garden.test.ts",
            stream=False,
            context={"continuous_execution": {"enabled": True}},
        )
    )
    assert result.ok and len(kernel.requests) == 2
    prompt = kernel.requests[1].message
    assert "edit_file" in prompt and "tests/garden.test.ts" in prompt
    assert "destructive_shrink" in prompt
    assert "172" in prompt
    for private_value in ["SECRET_GRANT", "SECRET_TOKEN", "RAW_RECEIPT", "RAW_REPLACEMENT_CODE"]:
        assert private_value not in prompt
    assert result.metadata["batch_receipt"]["failure_count"] == 1


@pytest.mark.parametrize(
    "status,expected_feedback",
    [
        ("error", True),
        ("failed", True),
        ("timeout", True),
        ("aborted", True),
        ("cancelled", True),
        ("success", False),
        ("pending", False),
    ],
)
def test_failure_feedback_projects_terminal_receipt_statuses_without_mutation(status, expected_feedback):
    from copy import deepcopy

    from polaris.cells.roles.runtime.internal.kernel_session_turn import _failed_batch_feedback_summary

    receipt = {
        "results": [
            {
                "tool_name": "execute_command",
                "status": status,
                "arguments": {"file": "tests/garden.test.ts"},
                "error": "command stopped before verifier completion",
                "result": {"error_type": "verifier_interrupted", "effect_recovery": {"token": "PRIVATE_TOKEN"}},
                "effect_receipt": None,
            }
        ],
    }
    before = deepcopy(receipt)
    summary = _failed_batch_feedback_summary(receipt)
    if expected_feedback:
        assert "execute_command" in summary
        assert "tests/garden.test.ts" in summary
        assert "verifier_interrupted" in summary
        assert "command stopped" in summary
        assert "PRIVATE_TOKEN" not in summary
    else:
        assert summary == ""
    assert receipt == before
