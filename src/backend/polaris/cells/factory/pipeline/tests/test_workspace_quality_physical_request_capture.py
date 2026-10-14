"""Real Factory facade/authority/caller/provider capture; no paid provider.

The work-selection/TaskRuntime repair implementation is a component seam; the
facade, stage admission/cap, canonical LLM preparer/invoker, HTTP transport,
physical gate, immutable snapshots/pins and public read projector are real.
Existing authority fixture supplies its documented source-cut resolver seam.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.context.engine.public.contracts import QueryFinalProviderRequestAuditV1
from polaris.cells.context.engine.public.service import query_final_provider_request_audit
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_00 import _Mixin00
from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_03 import _Mixin03
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import (
    _authority,
    _resolved_cut,
    _Resolver,
    _run,
)
from polaris.cells.orchestration.pm_dispatch.internal import orchestration_command_service as commands
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import get_factory_role_evidence_authority_binding
from polaris.cells.roles.profile.public.service import profile_from_dict
from polaris.kernelone.context.contracts import TurnEngineContextRequest
from polaris.kernelone.llm.engine.executor import AIExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,scenario", [("director", "normal"), ("qa", "normal"), ("qa", "retry"), ("director", "cancel")]
)
async def test_quality_facade_capture_real_anthropic_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, scenario: str
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "system"))
    monkeypatch.setenv("NO_PROXY", "*")
    port, _admission, _resolver, _facts = _authority(
        tmp_path=tmp_path, stage="quality_gate", resolver=_Resolver(result=_resolved_cut(role))
    )
    workspace = tmp_path / "workspace"
    bodies: list[dict[str, Any]] = []
    received = Event()
    release_response = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            received.set()
            if scenario == "cancel":
                release_response.wait(timeout=2)
            if scenario == "retry" and len(bodies) == 1:
                # Existing invoker retry policy is specifically provider overload.
                self.send_response(529)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = json.dumps(
                {
                    "id": "local-msg",
                    "type": "message",
                    "role": "assistant",
                    "model": "capture-model",
                    "content": [{"type": "text", "text": "Read-only observation completed."}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {
        "type": "anthropic_compat",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "api_key": "local-fixture",
        "timeout": 10,
        "retries": 0,
        "model_specific": {"capture-model": {"max_context_tokens": 262144, "max_output_tokens": 4096}},
    }
    profile = profile_from_dict(
        {
            "role_id": role,
            "display_name": "Director",
            "model": "capture-model",
            "provider_id": "anthropic_compat",
            "provider_type": "anthropic_compat",
            "prompt_policy": {"core_template_id": role},
            "tool_policy": {"whitelist": [], "allow_code_write": False},
            "data_policy": {"data_subdir": role},
            "context_policy": {"max_context_tokens": 262144},
        }
    )
    from polaris.kernelone.llm.config_store import save_llm_config
    from polaris.kernelone.llm.runtime_config import reset_runtime_config_manager

    monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace))
    save_llm_config(
        str(workspace),
        "",
        {
            "schema_version": 2,
            "providers": {"anthropic_compat": config},
            "roles": {role: {"provider_id": "anthropic_compat", "model": "capture-model"}},
        },
    )
    reset_runtime_config_manager()
    engine = AIExecutor(workspace=str(workspace))
    monkeypatch.setattr(engine, "_get_provider_config", lambda _provider: config)
    invoker = LLMInvoker(workspace=str(workspace), enable_cache=False, executor=engine)
    responses = []
    child_ids = []

    async def invoke(child_id: str):
        child_ids.append(child_id)
        response = await invoker.call(
            profile=profile,
            system_prompt=f"You are Polaris {role}. Read only.",
            context=TurnEngineContextRequest(
                message="Inspect the current project and return a brief read-only observation."
            ),
            run_id=child_id,
            task_id="TASK-1",
            platform_retry_max=(1 if scenario == "retry" else 0),
            max_tokens=4096,
        )
        assert response.error is None, str(response.error)
        responses.append(response)

    async def actual_http(_executor: Any, **_kwargs: Any):
        authority = get_factory_role_evidence_authority_binding()
        assert authority is not None, "quality repair reached canonical caller without Factory authority"
        await invoke(_kwargs["run"].id)
        return [], {"attempted": True, "success": True}

    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", actual_http)

    class FactoryFacade(_Mixin03):
        def __init__(self) -> None:
            self.workspace = str(workspace)

        def _workspace_quality_repair_changed_files(self):
            return ["private-source-fixture.py"]

        _call_with_factory_role_evidence_authority = staticmethod(_Mixin00._call_with_factory_role_evidence_authority)

        def _factory_role_evidence_cutoff_port(self, _context: Any):
            return port

    executor = FactoryFacade()
    if role == "qa":
        # Real QA command producer generates each UUID and validates its request.
        # Only its orchestration worker is substituted with the real Kernel caller
        # above; report acceptance/TaskRuntime/source authority are component seams.
        async def submit_run(request: Any):
            assert request.role_entries[0].role_id == "qa"
            await invoke(request.run_id)
            return SimpleNamespace(status=SimpleNamespace(value="completed"))

        service = commands.OrchestrationCommandService(SimpleNamespace())

        async def get_worker():
            return SimpleNamespace(submit_run=submit_run)

        monkeypatch.setattr(commands, "get_orchestration_service", get_worker)
        monkeypatch.setattr(commands, "register_all_adapters", lambda _worker: None)
        executor._resolve_abort_checker = lambda _ctx: None

        async def no_abort(_checker):
            return None

        executor._quality_gate_abort_reason = no_abort

        async def checks(_run, _ctx):
            return True, "current-private-verification.json"

        executor._run_workspace_quality_checks = checks
        executor._build_qa_input_with_workspace_quality_evidence = lambda *_a, **_kw: (
            "Independently review current source."
        )
        executor._canonical_qa_commit_identity = lambda **_kw: ("", "")
        executor._artifact_path = lambda _path: workspace / "qa-report-not-created.json"
        executor._build_orchestration_service = lambda _ctx: service
        executor._build_qa_execution_metadata = lambda **_kw: ({}, 10)
        executor._resolve_cancel_event = lambda _ctx: None

        async def report_acceptance_seam(_service, result, **_kw):
            assert result.status == "completed", result.message
            return CommandResult(run_id=result.run_id, status="cancelled", message="private report acceptance seam")

        executor._wait_run_completion = report_acceptance_seam
        executor._build_quality_gate_failure_stage = lambda *_a, **kw: SimpleNamespace(
            status=kw.get("status", "failed")
        )

    try:
        if scenario == "cancel":
            task = asyncio.create_task(
                executor._apply_workspace_quality_llm_repairs(
                    run=_run(stage="quality_gate"),
                    context={},
                    artifact_quality_errors=["entry artifact missing"],
                    repair_attempt=1,
                )
            )
            assert await asyncio.to_thread(received.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(bodies) == 1
            assert len(port._grants) == 1
            key = next(iter(port._grants))
            state = port._test_physical_attempt_coordinator.budget_state(key)
            assert state.settled and state.consumed_attempts == 1 and state.revoked
            snapshots = list((workspace / ".polaris/runtime/contexts").glob("*/*"))
            captured = [json.loads(p.read_text(encoding="utf-8")) for p in snapshots if p.is_file()]
            assert any(
                row.get("provider_request", {}).get("final_physical_request", {}).get("body") == bodies[0]
                for row in captured
            )
            return
        for attempt in (1, 2):
            if role == "director":
                await executor._apply_workspace_quality_llm_repairs(
                    run=_run(stage="quality_gate"),
                    context={},
                    artifact_quality_errors=["entry artifact missing"],
                    repair_attempt=attempt,
                )
            else:
                result = await executor._execute_quality_gate_once(_run(stage="quality_gate"), {})
                assert result.status == "cancelled"

        assert len(bodies) == (3 if scenario == "retry" else 2)
        for response, body in zip(responses, bodies[-2:], strict=True):
            ref = response.metadata["context_snapshot_ref"]
            path = workspace / ".polaris/runtime/contexts" / ref[:2] / ref
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            assert snapshot["provider_request"]["final_physical_request"]["body"] == body
            assert snapshot["provider_request"]["final_physical_wire_hash"]
            assert f"polaris.role_identity.v1:{role}" in str(body.get("system"))
            projected = query_final_provider_request_audit(
                QueryFinalProviderRequestAuditV1(workspace=str(workspace), context_snapshot_ref=ref)
            )
            assert projected.ok
            assert projected.payload["provider_request"]["final_physical_request"]["body"] == body

        assert len(port._grants) == (1 if role == "director" else 2)
        assert (
            (len(set(child_ids)) == 1)
            if role == "director"
            else (len(set(child_ids)) == 2 and all(child.startswith("qa-") for child in child_ids))
        )
        assert all(g.controlled_child_run_id in child_ids for g in port._grants.values())
        assert all(port._test_physical_attempt_coordinator.budget_state(key).settled for key in port._grants)
        assert sum(
            port._test_physical_attempt_coordinator.budget_state(key).consumed_attempts for key in port._grants
        ) == len(bodies)
        port.close_authority()
        before_count = len(bodies)
        with pytest.raises(Exception, match=r"closed|revoked"):
            if role == "director":
                await executor._apply_workspace_quality_llm_repairs(
                    run=_run(stage="quality_gate"),
                    context={},
                    artifact_quality_errors=["still missing"],
                    repair_attempt=3,
                )
            else:
                await executor._execute_quality_gate_once(_run(stage="quality_gate"), {})
        assert len(bodies) == before_count, "closed authority must fail before physical send"

    finally:
        release_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
