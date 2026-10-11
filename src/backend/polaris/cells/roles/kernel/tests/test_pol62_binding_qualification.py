from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.cells.control_plane.run_ledger.public import AppendRunLedgerEventCommandV1, append_run_ledger_event
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_gate import (
    DurableFinalProviderAttemptSnapshotStore,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker._binding import _InvokerBindingMixin
from polaris.cells.roles.kernel.public import provider_attempt_lifecycle_replay as replay
from polaris.cells.roles.kernel.tests.test_final_provider_attempt_lifecycle import (
    _attempt,
    _lease,
    _start_permit,
)
from polaris.cells.roles.profile.public.service import RoleProfile
from polaris.kernelone.events.final_request_evidence import canonical_final_request_hash
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.runtime_config import RoleBindingSlot


@pytest.fixture(autouse=True)
def project_local_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)


def _routes(
    workspace: Path, defect: str = ""
) -> tuple[replay.QueryFactoryProviderAttemptLifecycleReplayV1, tuple[str, ...]]:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution", "execution.control_plane"),
            maintenance_reason="pol62_routes",
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id="factory-run-1",
            event={
                "event_type": "gate_evaluated",
                "gate": {"ok": True},
                "job_token": {"run_id": "factory-run-1", "project_id": "P1"},
            },
        )
    )
    runs = []
    # Independent dispatch declaration, fixed before any physical observations.
    expected_by_role = {
        "pm": [{"provider_id": "provider-a", "model": "kimi-for-coding", "binding_id": "binding-1"}],
        "chief_engineer": [{"provider_id": "provider-a", "model": "kimi-for-coding", "binding_id": "binding-2"}],
        "director": [
            {"provider_id": "provider-a", "model": "kimi-for-coding", "binding_id": f"binding-{i}"} for i in (3, 4)
        ],
        "qa": [{"provider_id": "provider-a", "model": "kimi-for-coding", "binding_id": "binding-5"}],
    }
    for index, role in enumerate(("pm", "chief_engineer", "director", "director", "qa"), 1):
        if role == "qa" and defect == "unreached_qa":
            continue
        if index == 4 and defect in {"missing_director", "skipped_director"}:
            continue
        from polaris.cells.roles.kernel.public.physical_attempt_control import (
            FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            FactoryPhysicalAttemptGrantViewV1,
        )
        from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as preparation
        from polaris.cells.roles.kernel.tests._final_provider_attempt_gate_helpers import _qualified_factory_fixture
        from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import (
            FactoryPhysicalAttemptTestControlPort,
        )

        original_profile = preparation._profile
        original_identity = preparation._semantic_identity

        def selected_profile(selected_role: str, profile_factory=original_profile, attempt_index=index):
            profile = profile_factory(selected_role)
            selected = _InvokerBindingMixin._profile_for_binding(
                profile,
                RoleBindingSlot(
                    role_id=selected_role,
                    provider_id=profile.provider_id,
                    model=profile.model,
                    binding_id=f"binding-{attempt_index}",
                ),
            )
            selected.role_route_obligations_json = json.dumps(
                {
                    "schema_version": "llm.factory_role_route_obligations.v1",
                    "source": "factory.director_dispatch",
                    "expected": expected_by_role[selected_role],
                    "skips": [
                        {
                            **expected_by_role["director"][1],
                            "reason": "no_assigned_tasks",
                            "source": "factory.director_dispatch",
                        }
                    ]
                    if selected_role == "director" and defect == "skipped_director"
                    else [],
                }
            )
            if attempt_index == 4 and defect == "authorized_fallback":
                selected.route_fallback_authorization_json = json.dumps(
                    {
                        "source": "roles.kernel.role_binding_fallback",
                        "from_request_freeze_id": f"{13:032x}",
                        "from_binding": expected_by_role["director"][0],
                        "to_binding": expected_by_role["director"][1],
                        "trigger_error_category": "transient",
                    }
                )
            return selected

        control = FactoryPhysicalAttemptTestControlPort(
            factory_run_id="factory-run-1", revalidate_active_stage_claim=lambda _grant: None
        )
        control.register_grant(
            FactoryPhysicalAttemptGrantViewV1(
                schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
                verification_scope="factory",
                factory_run_id="factory-run-1",
                role=role,
                stage="director_dispatch",
                workspace_fencing_token=1,
                stage_claim_attempt=1,
                stage_claim_nonce=f"nonce-{index}",
                execution_authority_hash="f" * 64,
                attempt_budget=32,
            )
        )
        with (
            patch.object(preparation, "_profile", selected_profile),
            patch.object(
                preparation,
                "_semantic_identity",
                lambda identity_factory=original_identity, attempt_index=index: replace(
                    identity_factory(),
                    run_id=f"run-{3 if attempt_index == 4 and defect in {'authorized_fallback', 'unauthorized_fallback'} else attempt_index}",
                    call_id=f"{3 if attempt_index == 4 and defect in {'authorized_fallback', 'unauthorized_fallback'} else attempt_index:032x}",
                    turn_id=f"run-{3 if attempt_index == 4 and defect in {'authorized_fallback', 'unauthorized_fallback'} else attempt_index}:turn:0",
                    request_freeze_id=f"{attempt_index + 10:032x}",
                ),
            ),
            replay.bind_factory_provider_route_obligations(
                workspace=str(workspace),
                factory_run_id="factory-run-1",
                role=role,
                expected=expected_by_role[role],
                skips=[
                    {
                        **expected_by_role["director"][1],
                        "reason": "no_assigned_tasks",
                        "source": "factory.director_dispatch",
                    }
                ]
                if role == "director" and defect == "skipped_director"
                else [],
            ),
        ):
            proof, semantic, wire, frozen_request, _dispatch_port = _qualified_factory_fixture(
                workspace=workspace, physical_attempt_control_port=control, role=role
            )
        repository = ContextSnapshotAuditPinRepository(workspace=str(workspace))
        final_context = json.loads(
            Path(repository.snapshot_path(proof.context_snapshot_ref)).read_text(encoding="utf-8")
        )
        selection = final_context["provider_request"]["physical_route_authority"]["selected_role_binding"]
        if defect == "missing_binding" and index == 1:
            selection["binding_id"] = ""
        if defect == "foreign_binding" and index == 1:
            selection["role"] = "qa"
        semantic_hash = canonical_final_request_hash(semantic)
        wire_hash = canonical_final_request_hash(wire)
        run_id = frozen_request.identity.run_id
        payload = json.loads(frozen_request.canonical_final_payload_json)
        attempt = replace(
            _attempt(),
            role=role,
            provider=payload["provider_id"],
            model=payload["model"],
            run_id=run_id,
            provider_request_id=f"provider-request-{index}",
            request_freeze_id=frozen_request.identity.request_freeze_id,
            call_id=frozen_request.identity.call_id,
            turn_id=frozen_request.identity.turn_id,
            semantic_candidate_hash=frozen_request.semantic_candidate_hash,
            semantic_request_hash=semantic_hash,
            physical_wire_hash=wire_hash,
        )
        if defect == "wrong_provider" and index == 1:
            selection["provider_id"] = "foreign-provider"
        if defect == "wrong_model" and index == 1:
            selection["model"] = "foreign-model"
        final_pin = repository.persist_snapshot_and_pin(
            snapshot=final_context,
            factory_run_id=attempt.factory_run_id,
            role=role,
            verification_scope="factory",
            request_freeze_id=attempt.request_freeze_id,
            provider_request_id=f"physical-context-{index}",
            composite_request_hash=wire_hash,
            snapshot_source="roles.kernel.final_physical_request",
        )
        attempt = replace(
            attempt,
            durable_view={
                "schema_version": "llm.frozen_final_provider_attempt.v1",
                "canonical_semantic_request": semantic,
                "physical_wire": wire,
                "final_request_qualification": {
                    "schema_version": "llm.final_provider_attempt_qualification.v1",
                    "context_snapshot_ref": final_pin.context_snapshot_ref,
                },
            },
        )
        pin = DurableFinalProviderAttemptSnapshotStore(str(workspace)).persist_and_pin(attempt)
        if defect == "tampered_context" and index == 1:
            Path(repository.snapshot_path(final_pin.context_snapshot_ref)).write_text("{}", encoding="utf-8")
        if defect == "missing_context" and index == 1:
            Path(repository.snapshot_path(final_pin.context_snapshot_ref)).unlink()
        store = StrictProviderAttemptLifecycleStore.for_factory_run(
            workspace=str(workspace), factory_run_id=attempt.factory_run_id
        )
        permit = replace(
            _start_permit(attempt), reservation_id=f"reservation-{index}", start_permit_id=f"permit-{index}"
        )
        start = store.append_start(
            attempt, context_snapshot_ref=pin.context_snapshot_ref, pin_hash=pin.pin_hash, start_permit=permit
        )
        if not (defect == "orphan_start" and index == 1):
            store.append_terminal(
                attempt,
                context_snapshot_ref=pin.context_snapshot_ref,
                pin_hash=pin.pin_hash,
                lease=replace(_lease(permit, start), lease_id=f"lease-{index}"),
                status="failed"
                if (defect == "failed_terminal" and index == 1)
                or (defect in {"authorized_fallback", "unauthorized_fallback"} and index == 3)
                else "completed",
                error="failure"
                if (defect == "failed_terminal" and index == 1)
                or (defect in {"authorized_fallback", "unauthorized_fallback"} and index == 3)
                else "",
            )
        runs.append(run_id)
        if role == "director" and not (defect == "foreign_run" and index == 3):
            append_fact_event(
                AppendFactEventCommandV1(
                    workspace=str(workspace),
                    stream="task_runtime.execution",
                    event_type="created",
                    source="runtime.task_runtime",
                    run_id=run_id,
                    task_id=f"TASK-{index}",
                    payload={"run_id": run_id, "factory_run_id": "factory-run-1", "factory_bench_project_id": "P1"},
                    strict_integrity=True,
                )
            )
            append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=str(workspace),
                    run_id=run_id,
                    event={
                        "event_type": "gate_evaluated",
                        "gate": {"ok": True},
                        "job_token": {"run_id": run_id, "project_id": "P1"},
                    },
                )
            )
    return replay.QueryFactoryProviderAttemptLifecycleReplayV1(
        schema_version=replay.QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    ), tuple(runs)


def test_five_canonical_physical_attempts_qualify_without_local_configuration(tmp_path: Path) -> None:
    query, _runs = _routes(tmp_path)
    result = replay.query_factory_provider_route_qualification(query, project_id="P1")
    assert result["ok"] is True
    assert result["terminal_events_observed"] == 5
    assert result["roles"]["director"]["observed_count"] == 2


@pytest.mark.parametrize(
    "defect,want",
    [
        ("missing_director", False),
        ("skipped_director", True),
        ("authorized_fallback", True),
        ("unauthorized_fallback", False),
    ],
)
def test_independent_route_obligations_and_authorized_fallback(tmp_path: Path, defect: str, want: bool) -> None:
    query, _ = _routes(tmp_path, defect)
    result = replay.query_factory_provider_route_qualification(query, project_id="P1", require_all_director_routes=True)
    assert result["ok"] is want, result
    if want and defect == "authorized_fallback":
        assert any(event["terminal_status"] == "failed" for event in result["events"])
        assert result["roles"]["director"]["degraded"] is True
    if defect == "missing_director":
        assert result["roles"]["director"]["missing_bindings"]


@pytest.mark.parametrize(
    "defect",
    [
        "missing_binding",
        "foreign_binding",
        "wrong_provider",
        "wrong_model",
        "tampered_context",
        "missing_context",
        "orphan_start",
        "failed_terminal",
        "foreign_run",
        "foreign_factory",
        "foreign_workspace",
    ],
)
def test_route_qualification_rejects_incomplete_or_foreign_evidence(tmp_path: Path, defect: str) -> None:
    query, _runs = _routes(tmp_path, defect)
    if defect == "foreign_factory":
        query = replace(query, factory_run_id="other-factory")
    if defect == "foreign_workspace":
        other = tmp_path / "other"
        other.mkdir()
        query = replace(query, workspace=str(other))
    result = replay.query_factory_provider_route_qualification(query, project_id="P1")
    assert result["ok"] is False
    assert result["errors"]


def test_compatibility_duplicate_rows_cannot_inflate_physical_attempts(tmp_path: Path) -> None:
    query, _runs = _routes(tmp_path)
    compatibility = tmp_path / ".polaris/runtime/llm_calls.jsonl"
    compatibility.write_text(
        (json.dumps({"role": "pm", "invocation": True, "terminal": True}) + "\n") * 20,
        encoding="utf-8",
    )
    result = replay.query_factory_provider_route_qualification(query, project_id="P1")
    assert result["ok"] is True
    assert result["terminal_events_observed"] == 5


def test_actual_binding_selection_freezes_identity_without_credentials() -> None:
    slot = RoleBindingSlot(role_id="director", provider_id="provider-a", model="model-a", binding_id="binding-a")
    selected = _InvokerBindingMixin._profile_for_binding(
        RoleProfile(role_id="director", display_name="Director", description=""), slot
    )
    frozen = json.loads(selected.selected_role_binding_json)
    assert frozen["binding_id"] == "binding-a"
    assert frozen["role"] == "director"
    assert frozen["provider_id"] == selected.provider_id == "provider-a"
    assert frozen["model"] == selected.model == "model-a"
    assert set(frozen) == {"schema_version", "role", "provider_id", "model", "binding_id"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stream", "structured", "invoke", "transaction_stream", "transaction_invoke"])
@pytest.mark.parametrize("selection_mode", ["default", "override", "missing", "foreign", "tamper"])
async def test_real_invoker_prepare_freeze_retains_existing_route_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    selection_mode: str,
    fallback_count: int = 2,
    settlement_defect: str = "",
) -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
    from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
    from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker, _binding
    from polaris.cells.roles.kernel.internal.llm_caller.response_types import StructuredLLMResponse
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
    from polaris.cells.roles.kernel.public.physical_attempt_control import (
        FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
        FactoryPhysicalAttemptGrantViewV1,
    )
    from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as preparation
    from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import (
        FactoryPhysicalAttemptTestControlPort,
    )
    from polaris.kernelone.llm.engine.contracts import AIResponse
    from polaris.kernelone.llm.engine.executor import AIExecutor
    from pydantic import BaseModel

    role = "director" if selection_mode == "fanout" else "chief_engineer"
    prompt = f"You are {role}."
    monkeypatch.setattr(_binding, "is_role_binding_healthy", lambda *_, **__: True)
    monkeypatch.setattr(_binding, "mark_role_binding_unhealthy", lambda *_, **__: None)
    profile = preparation._profile(role)
    slot = RoleBindingSlot(
        role_id=role, provider_id=profile.provider_id, model=profile.model, binding_id="chosen-primary"
    )
    fallback_slot = RoleBindingSlot(
        role_id=role, provider_id="provider-b", model=profile.model, binding_id="fallback-worker"
    )
    fallback_slots = (
        slot,
        fallback_slot,
        *(
            RoleBindingSlot(
                role_id=role,
                provider_id=f"provider-{index}",
                model=profile.model,
                binding_id=f"fallback-worker-{index}",
            )
            for index in range(3, fallback_count + 1)
        ),
    )
    monkeypatch.setattr(
        _binding,
        "get_role_binding_candidates",
        lambda _role: (
            () if selection_mode == "missing" else (fallback_slots if selection_mode == "fallback" else (slot,))
        ),
    )
    override = (
        {
            "provider_id": profile.provider_id,
            "model": profile.model,
            "binding_id": "chosen-worker",
            "_fanout_locked": "true",
        }
        if selection_mode == "override"
        else None
    )
    monkeypatch.setattr(_binding, "get_role_binding_override", lambda _role: override)
    if selection_mode == "foreign":
        profile = SimpleNamespace(**{**vars(profile), "provider_id": "foreign-provider"})
    if selection_mode == "tamper":
        profile.selected_role_binding_json = json.dumps(
            {
                "schema_version": "llm.selected_role_binding.v1",
                "role": "qa",
                "provider_id": profile.provider_id,
                "model": profile.model,
                "binding_id": "tampered",
            }
        )
    control = FactoryPhysicalAttemptTestControlPort(
        factory_run_id="factory-run-1", revalidate_active_stage_claim=lambda _grant: None
    )
    control.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role=role,
            stage="director_dispatch" if role == "director" else "chief_engineer_review",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="nonce",
            execution_authority_hash="f" * 64,
            attempt_budget=32,
        )
    )
    cutoff = preparation._B32CutoffPort()
    authority = preparation._b32_authority(
        cutoff, role=role, physical_attempt_control_port=control, execution_authority_hash="f" * 64, attempt_budget=32
    )
    context = ContextRequest(
        message="Review.",
        context_override={
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Review."},
            ],
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
        },
    )
    original_prepare = _binding.LLMRequestPreparer._prepare_llm_request

    async def prepare_negative_authorization(self, **kwargs):
        selected = kwargs["profile"]
        is_middle = selected.provider_id == "provider-b"
        is_last = selected.provider_id == fallback_slots[-1].provider_id
        if (is_middle and settlement_defect == "orphan_middle") or (
            is_last and settlement_defect == "unauthorized_success"
        ):
            selected = SimpleNamespace(**vars(selected))
            selected.route_fallback_authorization_json = ""
            kwargs["profile"] = selected
        if (is_middle and settlement_defect == "foreign_middle_freeze") or (
            is_last and settlement_defect == "foreign_success_freeze"
        ):
            selected = SimpleNamespace(**vars(selected))
            authorization = json.loads(selected.route_fallback_authorization_json)
            authorization["from_request_freeze_id"] = "e" * 32
            selected.route_fallback_authorization_json = json.dumps(authorization)
            kwargs["profile"] = selected
        if is_middle and settlement_defect in {"foreign_middle_call", "foreign_middle_run"}:
            field = "call_id" if settlement_defect == "foreign_middle_call" else "run_id"
            kwargs["factory_semantic_identity"] = replace(
                kwargs["factory_semantic_identity"],
                **{
                    field: "e" * 32,
                    **({"turn_id": f"{'e' * 32}:turn:0"} if field == "run_id" else {}),
                },
            )
        if is_middle and settlement_defect == "unrelated_middle_semantic":
            kwargs["context"] = ContextRequest(
                message="Unrelated request.",
                context_override={
                    **context.context_override,
                    "_transaction_kernel_prebuilt_messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "Unrelated request."},
                    ],
                },
            )
        return await original_prepare(self, **kwargs)

    if settlement_defect and settlement_defect != "all_failed":
        monkeypatch.setattr(_binding.LLMRequestPreparer, "_prepare_llm_request", prepare_negative_authorization)
    snapshots = []
    physical_sends = []

    def freeze_local_transport(prepared: object):
        port = prepared.factory_dispatch_port
        frozen = prepared.factory_semantic_request
        payload = json.loads(frozen.canonical_final_payload_json)
        reference = AIExecutor._store_context_messages_sync(
            workspace=str(tmp_path),
            messages=payload["messages"],
            trace_id=frozen.identity.run_id,
            call_id=frozen.identity.call_id,
            provider_request=preparation._b35_provider_request_snapshot(frozen=frozen),
        )
        port.qualify(
            final_request_context_audit=preparation._b35_audit(frozen=frozen, context_snapshot_ref=reference),
            context_snapshot_ref=reference,
        )
        port.bind_provider_route_authority(
            provider_id=payload["provider_id"],
            provider_type="openai_compat",
            model=payload["model"],
            mode="stream" if payload["stream"] else "invoke",
            provider_config={"base_url": f"https://{payload['provider_id']}.example.test"},
        )
        route = port._physical_route_authority
        wire = {
            "endpoint": route["exact_endpoint"],
            "headers": {},
            "body": route["expected_body"],
            "transport": {"kind": route["exact_transport_kind"], "timeout_seconds": 5}
            if payload["stream"]
            else {"kind": route["exact_transport_kind"], "timeout": 1},
        }
        ref, _ = port._persist_final_physical_request_context(wire_request=wire)
        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
        snapshots.append(json.loads(Path(repository.snapshot_path(ref)).read_text(encoding="utf-8")))
        return port, wire

    async def send_local(_wire):
        return "local-result"

    @asynccontextmanager
    async def open_local(_wire):
        yield "local-result"

    async def consume_local(_response):
        yield {"type": "done"}

    async def run_stream(**kwargs: object):
        port, wire = freeze_local_transport(kwargs["prepared"])
        async for chunk in port.dispatch_stream_async(wire_request=wire, open_stream=open_local, consume=consume_local):
            yield chunk

    async def local_structured(self: object, **kwargs: object):
        port, wire = freeze_local_transport(kwargs["prepared"])
        await port.dispatch_async(wire_request=wire, send=send_local)
        return StructuredLLMResponse(data={"ok": True}, raw_content='{"ok":true}')

    async def local_invoke(_request, *, physical_dispatch_port):
        port, wire = freeze_local_transport(
            SimpleNamespace(
                factory_dispatch_port=physical_dispatch_port,
                factory_semantic_request=physical_dispatch_port.frozen_semantic_request,
            )
        )
        payload = json.loads(port.frozen_semantic_request.canonical_final_payload_json)

        async def send(_wire):
            physical_sends.append(payload["provider_id"])
            if (
                selection_mode == "fallback"
                and (settlement_defect == "all_failed" or payload["provider_id"] != fallback_slots[-1].provider_id)
            ) or (selection_mode == "physical_retry" and payload["provider_id"] == profile.provider_id):
                raise TimeoutError("provider timeout")
            return "local-result"

        try:
            await port.dispatch_async(wire_request=wire, send=send)
        except TimeoutError as exc:
            if selection_mode == "physical_retry":
                await port.dispatch_async(wire_request=wire, send=send_local)
                return AIResponse(
                    ok=True, output="local-result", provider_id=payload["provider_id"], model=payload["model"], raw={}
                )
            return AIResponse(
                ok=False, error=str(exc), provider_id=payload["provider_id"], model=payload["model"], raw={}
            )
        return AIResponse(
            ok=True, output="local-result", provider_id=payload["provider_id"], model=payload["model"], raw={}
        )

    invoker = LLMInvoker(workspace=str(tmp_path), enable_cache=False)
    invoker._executor = SimpleNamespace(invoke=local_invoke)
    invoker._stream_engine = SimpleNamespace(run_stream=run_stream)
    monkeypatch.setattr(LLMInvoker, "_try_native_response_format_structured", local_structured)
    monkeypatch.setattr(LLMInvoker, "_emit_call_start_event", lambda *_, **__: None)

    class Output(BaseModel):
        ok: bool

    with bind_factory_role_evidence_authority(authority):
        if mode == "stream":
            events = [event async for event in invoker.call_stream(profile, prompt, context, run_id="run-1")]
            assert events
        elif mode == "structured":
            await invoker.call_structured(profile, prompt, context, Output, run_id="run-1")
        elif mode == "invoke":
            await invoker.call(profile, prompt, context, run_id="run-1")
        else:
            request = SimpleNamespace(
                message="Review.",
                task_id="",
                run_id="run-1",
                workspace=str(tmp_path),
                metadata={},
                context_override={"context_os_snapshot": {}},
            )
            kernel = RoleExecutionKernel.create_default(workspace=str(tmp_path), llm_invoker=invoker)
            transaction = create_transaction_kernel(kernel, role, profile, request)
            payload = {
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "Review."},
                ],
                "tools": [],
                "tool_choice": "none",
            }
            if mode == "transaction_stream":
                async for _ in transaction.llm_provider_stream(payload):
                    pass
            else:
                try:
                    await transaction.llm_provider(payload)
                except RuntimeError:
                    if selection_mode in {"default", "override"}:
                        raise
    if selection_mode == "fanout":
        assert len(snapshots) == 1
        obligations = snapshots[0]["provider_request"]["physical_route_authority"]["role_route_obligations"]
        assert obligations["source"] == "factory.director_dispatch"
        assert len(obligations["expected"]) == 2
        assert obligations["skips"][0]["reason"] == "no_assigned_tasks"
    elif selection_mode in {"fallback", "physical_retry"}:
        assert len(snapshots) == (fallback_count if selection_mode == "fallback" else 1)
        if selection_mode == "fallback":
            assert physical_sends == [slot.provider_id for slot in fallback_slots]
            for snapshot, candidate_slot in zip(snapshots[1:], fallback_slots[1:], strict=True):
                authorization = snapshot["provider_request"]["physical_route_authority"].get(
                    "route_fallback_authorization"
                )
                if (candidate_slot.provider_id == "provider-b" and settlement_defect == "orphan_middle") or (
                    candidate_slot is fallback_slots[-1] and settlement_defect == "unauthorized_success"
                ):
                    assert authorization is None
                    continue
                assert authorization["source"] == "roles.kernel.role_binding_fallback"
                assert authorization["from_binding"]["binding_id"] == "chosen-primary"
                assert authorization["to_binding"]["binding_id"] == candidate_slot.binding_id
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                streams=("task_runtime.execution", "execution.control_plane"),
                maintenance_reason="fallback_local_test",
            )
        )
        from polaris.cells.events.fact_stream.public import AppendFactEventCommandV1, append_fact_event

        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="claimed",
                source="runtime.task_runtime",
                run_id="child",
                task_id="T1",
                payload={"run_id": "child", "task_id": "T1", "factory_run_id": "factory-run-1", "project_id": "P1"},
                strict_integrity=True,
            )
        )
        for run_id in ("factory-run-1", "child"):
            append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=str(tmp_path),
                    run_id=run_id,
                    event={
                        "event_type": "gate_evaluated",
                        "job_token": {"run_id": run_id, "project_id": "P1"},
                        "gate": {"ok": True},
                    },
                )
            )
        result = replay.query_factory_provider_route_qualification(
            replay.QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=replay.QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id="factory-run-1",
            ),
            project_id="P1",
            required_roles=(role,),
        )
        lifecycle = replay.query_factory_provider_attempt_lifecycle_replay(
            replay.QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=replay.QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id="factory-run-1",
            )
        )
        starts = [fact for fact in lifecycle.facts if fact.phase == "start"]
        terminals = [fact for fact in lifecycle.facts if fact.phase == "terminal"]
        count = fallback_count if selection_mode == "fallback" else 2
        assert len(starts) == len(terminals) == count
        assert len({fact.provider_request_id for fact in starts}) == count
        assert {fact.provider_request_id for fact in starts} == {fact.provider_request_id for fact in terminals}
        expected_status = ["failed"] * (count if settlement_defect == "all_failed" else count - 1)
        if settlement_defect != "all_failed":
            expected_status.append("completed")
        assert [fact.terminal_status for fact in terminals] == expected_status
        assert all(fact.error for fact in terminals if fact.terminal_status == "failed")
        assert [event["terminal_status"] for event in result["events"]] == expected_status
        if settlement_defect and settlement_defect != "all_failed":
            assert result["ok"] is False, result
            assert result["errors"], result
            return
        assert result["integrity_ok"] is True, result
        if settlement_defect == "all_failed":
            assert result["ok"] is False
            assert len(result["roles"][role]["failed_attempts"]) == count
            return
        assert result["ok"] is True, result
        assert result["roles"][role]["degraded"] is True
        assert result["roles"][role]["failed_attempts"] == []
    elif selection_mode in {"default", "override"}:
        assert len(snapshots) == 1
        request = snapshots[0]["provider_request"]
        selection = request["physical_route_authority"].get("selected_role_binding")
        assert selection and selection["binding_id"] == (
            "chosen-worker" if selection_mode == "override" else "chosen-primary"
        )
        assert request["provider_id"] == profile.provider_id and request["model"] == profile.model
        lifecycle = replay.query_factory_provider_attempt_lifecycle_replay(
            replay.QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=replay.QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id="factory-run-1",
            )
        )
        assert [fact.phase for fact in lifecycle.facts] == ["start", "terminal"]
        assert lifecycle.facts[-1].terminal_status == "completed"
    elif snapshots:
        assert "selected_role_binding" not in snapshots[0]["provider_request"]["physical_route_authority"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settlement_defect",
    [
        "orphan_middle",
        "unauthorized_success",
        "foreign_middle_freeze",
        "foreign_success_freeze",
        "foreign_middle_call",
        "foreign_middle_run",
        "unrelated_middle_semantic",
    ],
)
async def test_actual_multislot_fallback_rejects_unrelated_or_unauthorized_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settlement_defect: str
) -> None:
    await test_real_invoker_prepare_freeze_retains_existing_route_identity(
        tmp_path, monkeypatch, "invoke", "fallback", 3, settlement_defect
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_count", [3, 4])
@pytest.mark.parametrize("settlement_defect", ["", "all_failed"])
async def test_actual_multislot_fallback_preserves_terminals_and_settles_only_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fallback_count: int, settlement_defect: str
) -> None:
    await test_real_invoker_prepare_freeze_retains_existing_route_identity(
        tmp_path, monkeypatch, "invoke", "fallback", fallback_count, settlement_defect
    )


@pytest.mark.asyncio
async def test_actual_authorized_binding_fallback_preserves_failed_attempt_and_qualifies_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await test_real_invoker_prepare_freeze_retains_existing_route_identity(tmp_path, monkeypatch, "invoke", "fallback")


@pytest.mark.asyncio
async def test_actual_physical_retry_requires_same_frozen_request_and_control_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await test_real_invoker_prepare_freeze_retains_existing_route_identity(
        tmp_path, monkeypatch, "invoke", "physical_retry"
    )


@pytest.mark.asyncio
async def test_semantic_retry_carries_exact_predecessor_and_same_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
    from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as preparation

    original_profile = preparation._profile

    def selected_profile(role):
        profile = original_profile(role)
        slot = RoleBindingSlot(
            role_id=role, provider_id=profile.provider_id, model=profile.model, binding_id="retry-binding"
        )
        return _InvokerBindingMixin._profile_for_binding(
            _InvokerBindingMixin._with_route_obligations(profile, role, (slot,)), slot
        )

    monkeypatch.setattr(preparation, "_profile", selected_profile)
    _cutoff, authority, _context, prepared = await preparation._prepare_b33_factory_request(
        "director", workspace=str(tmp_path)
    )
    profile = selected_profile("director")
    preparer = LLMRequestPreparer(workspace=str(tmp_path))
    request = preparer._build_reasoning_truncation_retry_request(prepared=prepared, profile=profile)
    with bind_factory_role_evidence_authority(authority):
        retry = await preparer._reprepare_factory_semantic_retry_request(
            prepared=prepared, request=request, profile=profile
        )
    auth = retry.factory_dispatch_port._route_fallback_authorization
    assert auth["source"] == "roles.kernel.semantic_retry"
    assert auth["from_request_freeze_id"] == prepared.factory_semantic_request.identity.request_freeze_id
    assert auth["from_binding"] == auth["to_binding"]
    assert retry.factory_semantic_request.identity.call_id == prepared.factory_semantic_request.identity.call_id
    assert retry.factory_semantic_request.identity.request_freeze_id != auth["from_request_freeze_id"]
    assert retry.factory_dispatch_port._role_route_obligations == prepared.factory_dispatch_port._role_route_obligations


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["obligation", "selection", "fallback"])
async def test_frozen_route_declaration_rejects_in_process_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as preparation

    original_profile = preparation._profile

    def profile(role):
        original = original_profile(role)
        slot = RoleBindingSlot(
            role_id=role, provider_id=original.provider_id, model=original.model, binding_id="frozen-binding"
        )
        return _InvokerBindingMixin._profile_for_binding(
            _InvokerBindingMixin._with_route_obligations(original, role, (slot,)), slot
        )

    monkeypatch.setattr(preparation, "_profile", profile)
    _cutoff, _authority, _context, prepared = await preparation._prepare_b33_factory_request(
        "director", workspace=str(tmp_path)
    )
    port = prepared.factory_dispatch_port
    if field == "obligation":
        port._role_route_obligations["expected"] = []
    elif field == "selection":
        port._selected_role_binding["binding_id"] = "changed-after-freeze"
    else:
        port._route_fallback_authorization = {"source": "forged"}
    with pytest.raises(RuntimeError, match="route_declaration_drift"):
        port.validate_frozen_identity(prepared.factory_semantic_request)


def test_actual_preparer_gate_pin_and_lifecycle_feed_public_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as preparation
    from polaris.cells.roles.kernel.tests._final_provider_attempt_gate_helpers import _gate, _wire_request
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("task_runtime.execution", "execution.control_plane"),
            maintenance_reason="pol62_actual_gate",
        )
    )
    original_profile = preparation._profile

    def selected_profile(role: str):
        profile = original_profile(role)
        profile = _InvokerBindingMixin._with_route_obligations(
            profile,
            role,
            (
                RoleBindingSlot(
                    role_id=role,
                    provider_id=profile.provider_id,
                    model=profile.model,
                    binding_id="actual-selected-binding",
                ),
            ),
        )
        return _InvokerBindingMixin._profile_for_binding(
            profile,
            RoleBindingSlot(
                role_id=role, provider_id=profile.provider_id, model=profile.model, binding_id="actual-selected-binding"
            ),
        )

    monkeypatch.setattr(preparation, "_profile", selected_profile)
    gate, _ = _gate(tmp_path)
    # The send port is a local fixture; no Provider/network invocation occurs.
    assert gate.dispatch_sync(wire_request=_wire_request(gate), send=lambda _request: "local-result") == "local-result"
    service = TaskRuntimeService(str(tmp_path))
    monkeypatch.setattr(service, "_publish_factory_execution_event", lambda _payload: True)
    row = service.create_task_row(
        subject="Gate fixture", metadata={"factory_run_id": "factory-run-1", "factory_bench_project_id": "P1"}
    )
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id=gate._run_id, selection_source="unit"
    )
    assert claim["success"] is True
    for run_id in ("factory-run-1", gate._run_id):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=run_id,
                event={
                    "event_type": "gate_evaluated",
                    "gate": {"ok": True},
                    "job_token": {"run_id": run_id, "project_id": "P1"},
                },
            )
        )
    query = replay.QueryFactoryProviderAttemptLifecycleReplayV1(
        schema_version=replay.QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    result = replay.query_factory_provider_route_qualification(query, project_id="P1", required_roles=("director",))
    assert result["ok"] is True, result
    assert result["events"][0]["binding_id"] == "actual-selected-binding"
