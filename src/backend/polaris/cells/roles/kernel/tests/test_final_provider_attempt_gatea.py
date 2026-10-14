from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.roles.kernel.internal.llm_caller import (
    final_provider_attempt_gate as gate_module,
    final_provider_attempt_qualification as qualification_module,
    invoker as invoker_module,
)
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    FinalRequestEvidenceCoverageError,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_gate import (
    FinalProviderAttemptGate,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_inflight import (
    ProviderAttemptDrainError,
    ProviderAttemptInFlightCoordinator,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_request_metrics import (
    provider_native_request_metrics,
    validated_final_context_evidence,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker import (
    _helpers as invoker_helpers,
    _structured as invoker_structured,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import StreamEngine
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as request_fact_test
from polaris.cells.roles.kernel.tests._final_provider_attempt_gate_helpers import (  # noqa: F401
    _assert_one_coverage_rejection_and_zero_physical_effects,
    _AsyncPostContext,
    _AsyncResponse,
    _AsyncSession,
    _AsyncStreamContent,
    _bootstrap,
    _failed_coverage_audit,
    _gate,
    _prepared_with_dispatch_port,
    _qualified_factory_fixture,
    _run_fixture_coroutine,
    _wire_body,
    _wire_body_from_semantic,
    _wire_request,
)
from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import (
    FactoryPhysicalAttemptTestControlError as FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptTestControlPort as FactoryPhysicalAttemptLiveControlPort,
)
from polaris.infrastructure.llm.providers import provider_helpers
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.provider_helpers import (
    invoke_stream_with_retry,
    invoke_stream_with_retry_and_handler,
    invoke_with_retry,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1, bind_physical_provider_dispatch_port
from polaris.kernelone.llm.types import Usage


class _Response:
    def __init__(self, *, status_code: int = 200, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}


class ClientResponseError(RuntimeError):
    """Retry-shaped aiohttp response error for physical stream tests."""


async def _prepare_owned_snapshot_request(
    workspace: Path,
    *,
    stage: str = "pm_initial",
    prebuilt: bool = True,
) -> tuple[Any, PreparedLLMRequest, Any]:
    """Real gateway/preparation, with only external ContextOS services isolated."""
    from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
        FactoryRoleSemanticRequestIdentityV1,
        bind_factory_role_evidence_authority,
    )
    from polaris.kernelone.context.context_os import StateFirstContextOS
    from polaris.kernelone.context.contracts import TurnEngineContextRequest

    role = "pm" if stage.startswith("pm") else "chief_engineer"
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    control.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role=role,
            stage="pm_planning" if role == "pm" else "chief_engineer",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="snapshot-stage",
            execution_authority_hash="f" * 64,
            attempt_budget=32,
        )
    )
    cutoff = request_fact_test._B32CutoffPort()
    authority = request_fact_test._b32_authority(
        cutoff,
        role=role,
        physical_attempt_control_port=control,
        execution_authority_hash="f" * 64,
        attempt_budget=32,
    )
    profile: Any = request_fact_test._profile(role)
    profile.context_policy = SimpleNamespace(
        max_history_turns=8,
        max_context_tokens=262144,
        include_project_structure=True,
        include_task_history=False,
        compression_strategy="truncate",
    )
    profile.context_domain = "generic"
    override: dict[str, Any] = {
        "request_purpose": "task_planning" if role == "pm" else "blueprint",
        "_transaction_kernel_forced_tool_definitions": [],
        "_transaction_kernel_forced_tool_choice": "none",
    }
    if role == "chief_engineer":
        override["pm_task_contract"] = request_fact_test._pm_contract_set()
        override["ce_required_command_authority"] = [
            {
                "task_id": f"TASK-{i // 4 + 1}",
                "modality": "test",
                "argv": ["node", "--test", f"test-{i}.js"],
                "cwd": ".",
                "authority_hash": f"{i:064x}",
            }
            for i in range(28)
        ]
        override["pm_task_contract"]["ce_required_command_authority"] = override["ce_required_command_authority"]
    message = "Plan current tasks."
    if stage == "pm_revision":
        message = "Correct the current PM contract after quality feedback: return complete tasks JSON."
    elif stage == "ce_correction":
        message = "Correct the current CE blueprint after schema feedback: return complete blueprint JSON."
    context = TurnEngineContextRequest(message=message, context_override=override)
    projection = SimpleNamespace(
        head_anchor="",
        tail_anchor="",
        active_window=(),
        snapshot=None,
        run_card=SimpleNamespace(
            current_goal="", open_loops=(), latest_user_intent="", pending_followup_action="", last_turn_outcome=""
        ),
    )
    identity_index = ["pm_initial", "pm_revision", "ce_nonstream", "ce_stream", "ce_correction"].index(stage) + 1
    identity = FactoryRoleSemanticRequestIdentityV1(
        run_id="snapshot-child",
        turn_id=f"snapshot-child:turn:{identity_index}",
        call_id=f"{identity_index:032x}",
        request_freeze_id=f"{identity_index + 16:032x}",
    )
    with (
        patch.object(StateFirstContextOS, "project", return_value=projection),
        patch.object(StateFirstContextOS, "resolved_context_window", new=262144),
        patch("polaris.cells.roles.kernel.internal.context_gateway.gateway.get_event_writer", return_value=Mock()),
        bind_factory_role_evidence_authority(authority),
    ):
        if prebuilt:
            source = await RoleContextGateway(profile, workspace=workspace).build_context(
                context,
                system_prompt=f"You are {role}.",
            )
            override["_transaction_kernel_prebuilt_messages"] = [dict(item) for item in source.messages]
        prepared = await LLMRequestPreparer(workspace=str(workspace))._prepare_llm_request(
            profile=profile,
            system_prompt=f"You are {role}.",
            context=context,
            temperature=0.2,
            max_tokens=60000,
            stream=stage == "ce_stream",
            factory_semantic_identity=identity,
        )
    assert len(cutoff.acquire_requests) == len(cutoff.resolve_acks) == 1
    return profile, prepared, control


def _assert_snapshot_no_physical_effects(workspace: Path, control: Any) -> None:
    state = control.budget_state("f" * 64)
    assert state.reserved_count == state.committed_count == state.terminal_count == state.consumed_attempts == 0
    assert state.remaining_attempts == 32
    assert (
        StrictProviderAttemptLifecycleStore.for_factory_run(
            workspace=str(workspace),
            factory_run_id="factory-run-1",
        ).query_strict()
        == ()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prebuilt", [False, True])
@pytest.mark.parametrize("stage", ["pm_initial", "pm_revision", "ce_nonstream", "ce_stream", "ce_correction"])
async def test_owned_factory_snapshot_qualifies_real_persistence_and_native_dispatch(
    tmp_path: Path,
    stage: str,
    prebuilt: bool,
) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        build_final_request_context_audit_for_request,
    )
    from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload

    _bootstrap(tmp_path)
    profile, prepared, control = await _prepare_owned_snapshot_request(tmp_path, stage=stage, prebuilt=prebuilt)
    frozen = prepared.factory_semantic_request
    port = prepared.factory_dispatch_port
    assert frozen is not None and port is not None
    active = copy.deepcopy(prepared.ai_request.context["chat_messages"])
    context_ref = await invoker_helpers._store_active_request_context_snapshot(
        workspace=str(tmp_path),
        active_request=prepared.ai_request,
        prepared=prepared,
        profile=profile,
        run_id=frozen.identity.run_id,
        call_id=frozen.identity.call_id,
    )
    assert context_ref and len(context_ref) == 24 and all(c in "0123456789abcdef" for c in context_ref)
    snapshot = qualification_module._read_context_snapshot(workspace=str(tmp_path), context_snapshot_ref=context_ref)
    payload = json.loads(frozen.canonical_final_payload_json)
    assert snapshot["messages"] == payload["messages"] == prepared.messages
    assert {item.get("name") for item in snapshot["messages"]} >= {
        "context_override",
        "project_structure",
        "repo_identity",
    }
    assert active == prepared.messages
    assert prepared.ai_request.context["chat_messages"] == active
    assert payload["max_tokens"] == prepared.ai_request.options["max_tokens"] == 60000
    assert snapshot["provider_request"][
        "factory_final_request"
    ] == qualification_module.final_request_snapshot_evidence(frozen)
    audit = port.bind_final_request_context_audit(
        build_final_request_context_audit_for_request(
            ai_request=prepared.ai_request,
            prepared=prepared,
            profile=profile,
        )
    )
    if stage.startswith("ce"):
        coverage = audit["final_request_evidence_coverage"]["ce_required_command_authority"]
        assert coverage["pass"] is True and len(coverage["included_rows"]) == 28
    port.enforce_final_request_evidence_coverage(ai_request=prepared.ai_request, audit=audit)
    port.qualify(final_request_context_audit=audit, context_snapshot_ref=context_ref)
    _assert_snapshot_no_physical_effects(tmp_path, control)
    stream = stage == "ce_stream"
    port.bind_provider_route_authority(
        provider_id=profile.provider_id,
        provider_type="openai_compat",
        model=profile.model,
        mode="stream" if stream else "invoke",
        provider_config={"base_url": "https://example.test"},
    )
    route = port._physical_route_authority
    assert route is not None
    body = _build_openai_payload(
        prompt=prepared.input_text,
        model=profile.model,
        api_path="/v1/chat/completions",
        stream=stream,
        config={**prepared.request_options, "chat_messages": active},
    )
    assert body == route["expected_body"]
    assert sum(item["role"] == "system" for item in body["messages"]) == 1
    assert all("name" not in item for item in body["messages"])
    wire = {
        "endpoint": route["exact_endpoint"],
        "headers": {},
        "body": body,
        "transport": {"kind": route["exact_transport_kind"], "timeout_seconds" if stream else "timeout": 5},
    }
    calls: list[Mapping[str, Any]] = []
    if stream:
        session = _AsyncSession(_AsyncResponse(chunks=(b"ok",)), [])

        def open_stream(request: Mapping[str, Any]) -> Any:
            calls.append(request)
            return session.post(str(request["endpoint"]), json=request["body"])

        async def consume(response: Any) -> AsyncIterator[str]:
            async for chunk in response.content:
                yield chunk.decode("utf-8")

        assert [
            item
            async for item in port.dispatch_stream_async(
                wire_request=wire,
                open_stream=open_stream,
                consume=consume,
            )
        ] == ["ok"]
    else:

        def send(request: Mapping[str, Any]) -> str:
            calls.append(request)
            return "ok"

        assert port.dispatch_sync(wire_request=wire, send=send) == "ok"
    assert len(calls) == 1
    final_evidence = port.final_context_evidence()
    assert final_evidence is not None
    final_ref, final_audit = final_evidence
    final_snapshot = qualification_module._read_context_snapshot(
        workspace=str(tmp_path), context_snapshot_ref=final_ref
    )
    assert final_snapshot["messages"] == payload["messages"]
    assert final_snapshot["provider_request"]["final_physical_request"]["body"] == body
    assert final_audit["audit_scope"] == "provider_native_wire"
    assert control.budget_state("f" * 64).consumed_attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["pm_initial", "ce_nonstream"])
@pytest.mark.parametrize(
    "attack",
    [
        "content",
        "role",
        "order",
        "add",
        "remove",
        "malformed",
        "missing_active",
        "spoof_name",
        "owner_name",
        "owner_missing",
        "owner_malformed",
        "freeze_missing",
        "port_missing",
        "stale_request",
        "stale_freeze",
        "freeze_tamper",
        "attempt_identity",
    ],
)
async def test_owned_snapshot_producer_rejects_drift_before_transport(tmp_path: Path, attack: str, stage: str) -> None:
    _bootstrap(tmp_path)
    profile, prepared, control = await _prepare_owned_snapshot_request(tmp_path, stage=stage)
    frozen = prepared.factory_semantic_request
    assert frozen is not None
    active_request = prepared.ai_request
    chat = active_request.context["chat_messages"]
    run_id, call_id = frozen.identity.run_id, frozen.identity.call_id
    if attack == "content":
        chat[1]["content"] += " altered"
    elif attack == "role":
        chat[1]["role"] = "user"
    elif attack == "order":
        chat[1], chat[2] = chat[2], chat[1]
    elif attack == "add":
        chat.append({"role": "user", "content": "unexpected"})
    elif attack == "remove":
        chat.pop(1)
    elif attack == "malformed":
        chat.append(None)
    elif attack == "missing_active":
        active_request.context.pop("chat_messages")
    elif attack == "spoof_name":
        chat[1]["name"] = "repo_identity"
    elif attack == "owner_name":
        prepared.messages[1]["name"] = "spoofed"
    elif attack == "owner_missing":
        # Even plausible caller names cannot replace missing owned source.
        active_request.context["chat_messages"] = copy.deepcopy(prepared.messages)
        prepared.messages = []
    elif attack == "owner_malformed":
        prepared.messages = cast(Any, [None])
    elif attack == "freeze_missing":
        prepared.factory_semantic_request = None
    elif attack == "port_missing":
        prepared.factory_dispatch_port = None
    elif attack == "stale_request":
        active_request = copy.copy(active_request)
    elif attack == "stale_freeze":
        _, other, _ = await _prepare_owned_snapshot_request(tmp_path, stage="pm_revision")
        prepared.factory_semantic_request = other.factory_semantic_request
    elif attack == "freeze_tamper":
        object.__setattr__(frozen, "final_semantic_request_hash", "e" * 64)
    else:
        call_id = "e" * 32
    context_ref = await invoker_helpers._store_active_request_context_snapshot(
        workspace=str(tmp_path),
        active_request=active_request,
        prepared=prepared,
        profile=profile,
        run_id=run_id,
        call_id=call_id,
    )
    assert context_ref is None
    assert "context_snapshot_ref" not in active_request.context
    assert active_request.context["context_snapshot_degraded"]["reason"]
    from polaris.kernelone.storage.io_paths import resolve_storage_roots

    contexts_root = Path(resolve_storage_roots(str(tmp_path)).runtime_root) / "contexts"
    assert not contexts_root.exists() or not list(contexts_root.glob("*/*"))
    calls: list[object] = []

    class Executor:
        async def invoke(self, request: object, **kwargs: Any) -> None:
            calls.append(request)

    with pytest.raises((RuntimeError, ValueError, TypeError)):
        if attack == "attempt_identity":
            from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
                build_final_request_context_audit_for_request,
            )

            port = prepared.factory_dispatch_port
            assert port is not None
            port.qualify(
                final_request_context_audit=port.bind_final_request_context_audit(
                    build_final_request_context_audit_for_request(
                        ai_request=active_request, prepared=prepared, profile=profile
                    ),
                ),
                context_snapshot_ref="",
            )
        else:
            await invoker_helpers._invoke_executor_with_factory_dispatch(
                executor=Executor(),
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
    assert calls == []
    _assert_snapshot_no_physical_effects(tmp_path, control)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["pm_initial", "ce_nonstream"])
@pytest.mark.parametrize(
    "attack", ["name_removed", "name_changed", "replace", "delete", "after_pass_replace", "after_pass_delete"]
)
async def test_owned_snapshot_strict_qualification_rejects_stored_tamper(
    tmp_path: Path, attack: str, stage: str
) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        build_final_request_context_audit_for_request,
    )
    from polaris.kernelone.llm.engine import AIExecutor
    from polaris.kernelone.storage.io_paths import resolve_storage_roots

    _bootstrap(tmp_path)
    profile, prepared, control = await _prepare_owned_snapshot_request(tmp_path, stage=stage)
    frozen, port = prepared.factory_semantic_request, prepared.factory_dispatch_port
    assert frozen is not None and port is not None
    context_ref = await invoker_helpers._store_active_request_context_snapshot(
        workspace=str(tmp_path),
        active_request=prepared.ai_request,
        prepared=prepared,
        profile=profile,
        run_id=frozen.identity.run_id,
        call_id=frozen.identity.call_id,
    )
    assert context_ref
    snapshot = qualification_module._read_context_snapshot(workspace=str(tmp_path), context_snapshot_ref=context_ref)
    audit = port.bind_final_request_context_audit(
        build_final_request_context_audit_for_request(
            ai_request=prepared.ai_request,
            prepared=prepared,
            profile=profile,
        )
    )
    if attack.startswith("after_pass"):
        port.qualify(final_request_context_audit=audit, context_snapshot_ref=context_ref)
        port.bind_provider_route_authority(
            provider_id=profile.provider_id,
            provider_type="openai_compat",
            model=profile.model,
            mode="invoke",
            provider_config={"base_url": "https://example.test"},
        )
        _assert_snapshot_no_physical_effects(tmp_path, control)
    if attack.startswith("name"):
        if attack == "name_removed":
            snapshot["messages"][1].pop("name")
        else:
            snapshot["messages"][1]["name"] = "spoofed"
        context_ref = AIExecutor._store_context_messages_sync(
            str(tmp_path),
            snapshot["messages"],
            frozen.identity.run_id,
            frozen.identity.call_id,
            snapshot["provider_request"],
        )
        prepared.ai_request.context["context_snapshot_ref"] = context_ref
        audit = port.bind_final_request_context_audit(
            build_final_request_context_audit_for_request(
                ai_request=prepared.ai_request,
                prepared=prepared,
                profile=profile,
            )
        )
        expected_error = "context_snapshot_messages_mismatch"
    else:
        path = Path(resolve_storage_roots(str(tmp_path)).runtime_root) / "contexts" / context_ref[:2] / context_ref
        if attack.endswith("replace"):
            path.write_text("{}", encoding="utf-8")
            expected_error = "context_snapshot_hash_mismatch"
        else:
            path.unlink()
            expected_error = "context_snapshot_unreadable"
    calls: list[object] = []
    with pytest.raises(qualification_module.FinalProviderAttemptQualificationError, match=expected_error):
        if attack.startswith("after_pass"):
            route = port._physical_route_authority
            assert route is not None
            port.dispatch_sync(
                wire_request={
                    "endpoint": route["exact_endpoint"],
                    "headers": {},
                    "body": route["expected_body"],
                    "transport": {"kind": route["exact_transport_kind"], "timeout": 5},
                },
                send=lambda request: calls.append(request),
            )
        else:
            port.qualify(final_request_context_audit=audit, context_snapshot_ref=context_ref)
            calls.append("transport")
    assert calls == []
    _assert_snapshot_no_physical_effects(tmp_path, control)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("case", ["genuine", "decoy", "wire_substitution"])
@pytest.mark.asyncio
async def test_ce_prebuilt_factory_cutoff_enforces_current_table_before_transport(
    tmp_path: Path,
    stream: bool,
    case: str,
) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        build_final_request_context_audit_for_request,
    )
    from polaris.cells.roles.kernel.tests.test_context_gateway_integration import _prepare_ce_boundary
    from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload

    genuine = case != "decoy"
    expected_pass = case != "decoy"
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    cutoff = request_fact_test._B32CutoffPort()
    authority = request_fact_test._b32_authority(cutoff)
    profile, prepared, capture = await _prepare_ce_boundary(
        tmp_path, genuine=genuine, stream=stream, authority=authority
    )
    port = prepared.factory_dispatch_port
    assert port is not None
    assert len(cutoff.acquire_requests) == len(cutoff.resolve_acks) == 1
    assert prepared.factory_semantic_request is not None
    frozen = json.loads(prepared.factory_semantic_request.canonical_final_payload_json)
    capture["frozen_messages"] = frozen["messages"]
    if case == "wire_substitution":
        # A complete frozen source cannot excuse loss in the actual provider body.
        chat = prepared.ai_request.context["chat_messages"]
        prepared.ai_request.context["chat_messages"] = [
            item for item in chat if "[CE_REQUIRED_COMMAND_AUTHORITY_JSON]\n" not in item["content"]
        ]
    wire = _build_openai_payload(
        prompt=prepared.input_text,
        model=profile.model,
        api_path="/v1/chat/completions",
        stream=stream,
        config={**prepared.request_options, "chat_messages": prepared.ai_request.context["chat_messages"]},
    )
    assert all(isinstance(item.get("role"), str) and "content" in item for item in wire["messages"])
    assert all("receipt_refs" not in item for item in wire["messages"])
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request, prepared=prepared, profile=profile
    )
    bound = port.bind_final_request_context_audit(audit)
    coverage = bound["final_request_evidence_coverage"]["ce_required_command_authority"]
    capture.update(
        serialized_body=wire,
        audit=bound,
        active_chat_messages=copy.deepcopy(prepared.ai_request.context["chat_messages"]),
    )
    (tmp_path / "ce-cutoff-boundary.json").write_text(
        json.dumps(capture, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert coverage["required"] is True
    assert coverage["pass"] is expected_pass
    calls: list[object] = []
    if expected_pass:
        port.enforce_final_request_evidence_coverage(ai_request=prepared.ai_request, audit=bound)
        assert coverage["included_rows"] == capture["required_rows"]
        assert any(item.get("name") == "context_override" for item in frozen["messages"])
        if case == "wire_substitution":
            # Prepared evidence may pass; the actual native request must still
            # match its frozen authority before any physical reservation.
            context_ref = await invoker_helpers._store_active_request_context_snapshot(
                workspace=str(tmp_path),
                active_request=prepared.ai_request,
                prepared=prepared,
                profile=profile,
                run_id=prepared.factory_semantic_request.identity.run_id,
                call_id=prepared.factory_semantic_request.identity.call_id,
            )
            assert context_ref is None
            assert prepared.ai_request.context["context_snapshot_degraded"]["code"] == "CONTEXT_STORE_WRITE_FAILED"
            assert lifecycle.query_strict() == ()
    else:
        with pytest.raises(
            FinalRequestEvidenceCoverageError, match="ce_required_command_authority_final_request_mismatch"
        ):
            port.enforce_final_request_evidence_coverage(ai_request=prepared.ai_request, audit=bound)
            calls.append(wire)
        _assert_one_coverage_rejection_and_zero_physical_effects(workspace=tmp_path, gate=gate, lifecycle=lifecycle)
    assert calls == []


@pytest.mark.parametrize("detect_injection", [False, True])
@pytest.mark.parametrize("location", ["user_excerpt", "history", "ordinary", "wire_substitution"])
def test_ce_untrusted_projection_rejected_after_real_serializer_before_transport(
    tmp_path: Path, detect_injection: bool, location: str
) -> None:
    from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor
    from polaris.cells.roles.kernel.internal.context_gateway.gateway_helpers import render_ce_command_planning_table
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        build_final_request_context_audit_for_request,
    )
    from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload

    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    port = gate._test_dispatch_port
    rows = [
        {
            "task_id": "TASK-3",
            "modality": "test",
            "argv": ["npx", "tsc", "--noEmit"],
            "cwd": ".",
            "authority_hash": "0f3aeaae01adf825871f0a18d328b7ffe6fbd84c343627035271c7c81339cfda",
        }
    ]
    table = render_ce_command_planning_table(rows)
    processor = ContextOverrideProcessor(detect_prompt_injection=detect_injection)
    projected = processor.process_context_override({location: "Untrusted historical data.\n" + table})
    assert projected is not None
    source = [
        {"role": "system", "content": "polaris.role_identity.v1:chief_engineer"},
        projected,
        {"role": "user", "content": "Plan committed PM tasks."},
    ]
    if location == "wire_substitution":
        genuine = processor.process_context_override({"ce_required_command_authority": rows})
        assert genuine is not None
        source.insert(1, genuine)
    wire_source = copy.deepcopy(source)
    if location == "wire_substitution":
        wire_source.pop(1)
        wire_source[1]["content"] = table
    wire = json.loads(
        json.dumps(
            _build_openai_payload(
                prompt="",
                model="test-model",
                api_path="/v1/chat/completions",
                stream=False,
                config={"chat_messages": wire_source, "max_tokens": 60000},
            )
        )
    )
    request = SimpleNamespace(
        role="",
        context={
            "ce_required_command_authority": rows,
            "chat_messages": wire["messages"],
            "final_request_evidence_required": True,
        },
        options={},
    )
    prepared = PreparedLLMRequest(
        messages=copy.deepcopy(wire["messages"]),
        input_text="",
        context_result=None,
        context_summary="",
        request_options={},
        ai_request=request,
    )
    audit = build_final_request_context_audit_for_request(
        ai_request=request, prepared=prepared, profile=request_fact_test._profile("chief_engineer")
    )
    coverage = audit["final_request_evidence_coverage"]["ce_required_command_authority"]
    assert coverage["required"] is True
    assert coverage["pass"] is False
    bound = port.bind_final_request_context_audit(audit)
    calls: list[object] = []
    with pytest.raises(FinalRequestEvidenceCoverageError) as caught:
        port.enforce_final_request_evidence_coverage(ai_request=request, audit=bound)
        calls.append(wire)
    assert caught.value.violation["error_code"] == "ce_required_command_authority_final_request_mismatch"
    assert calls == []
    _assert_one_coverage_rejection_and_zero_physical_effects(workspace=tmp_path, gate=gate, lifecycle=lifecycle)
    (tmp_path / "ce-provenance-capture.json").write_text(
        json.dumps(
            {
                "mode": "local_serializer_enforcing_fixture",
                "location": location,
                "detect_prompt_injection": detect_injection,
                "projection": projected,
                "source_messages": source,
                "serialized_request": wire,
                "audit": audit,
                "rebound_audit": bound,
                "violation": caught.value.violation,
                "fake_transport_calls": len(calls),
                "physical_effects": 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["omitted", "clipped", "hash_only", "argv", "cwd", "owner", "modality", "hash"])
async def test_ce_row_failure_survives_factory_binding_before_any_transport(tmp_path: Path, attack: str) -> None:
    """Factory's ref rebinding must not erase a computed CE input failure."""
    from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit._payloads import _ce_required_command_row_coverage

    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    port = gate._test_dispatch_port
    rows = [
        {
            "task_id": "TASK-3",
            "modality": "test",
            "argv": ["npx", "tsc", "--noEmit"],
            "cwd": ".",
            "authority_hash": "0f3aeaae01adf825871f0a18d328b7ffe6fbd84c343627035271c7c81339cfda",
        }
    ]
    context = {"ce_required_command_authority": rows, "final_request_evidence_required": True}
    rendered = ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(context)
    assert rendered is not None
    body = {"messages": [rendered]}
    if attack == "omitted":
        body["messages"] = []
    elif attack == "clipped":
        rendered["content"] = rendered["content"][:100]
    elif attack == "hash_only":
        rendered["content"] = rows[0]["authority_hash"]
    else:
        changed = copy.deepcopy(rows)
        field, value = {
            "argv": ("argv", ["node", "--test", "other.js"]),
            "cwd": ("cwd", "tests"),
            "owner": ("task_id", "TASK-4"),
            "modality": ("modality", "build"),
            "hash": ("authority_hash", "a" * 64),
        }[attack]
        changed[0][field] = value
        rendered["content"] = (
            "[CE_REQUIRED_COMMAND_AUTHORITY_JSON]\n" + json.dumps(changed) + "\n[/CE_REQUIRED_COMMAND_AUTHORITY_JSON]"
        )
    decoded = json.loads(json.dumps(body))
    row_coverage = _ce_required_command_row_coverage(
        SimpleNamespace(role="chief_engineer", context=context), decoded["messages"]
    )
    audit = copy.deepcopy(port._qualified_audit)
    audit["final_request_evidence_coverage"].pop("included_refs_authority", None)
    audit["final_request_evidence_coverage"]["ce_required_command_authority"] = row_coverage
    audit["final_request_evidence_coverage"]["pass"] = False
    bound = port.bind_final_request_context_audit(audit)
    # Existing Factory cutoff legitimately rebinds refs and recomputes pass.
    assert bound["final_request_evidence_coverage"]["pass"] is True
    prepared = _prepared_with_dispatch_port(frozen=port.frozen_semantic_request, dispatch_port=port)
    prepared.ai_request.context["final_request_evidence_required"] = True
    calls: list[object] = []

    class Executor:
        async def invoke(self, request: object, *, physical_dispatch_port: object) -> str:
            calls.append(request)
            return "forbidden"

    with (
        patch.object(invoker_helpers, "context_snapshot_matches_frozen_attempt", return_value=True),
        patch.object(invoker_helpers, "build_final_request_context_audit_for_request", return_value=audit),
        pytest.raises(FinalRequestEvidenceCoverageError, match="ce_required_command_authority_final_request_mismatch"),
    ):
        await invoker_module._invoke_executor_with_factory_dispatch(
            executor=Executor(),
            prepared=prepared,
            request=prepared.ai_request,
            profile=request_fact_test._profile("director"),
        )
    assert calls == []
    _assert_one_coverage_rejection_and_zero_physical_effects(workspace=tmp_path, gate=gate, lifecycle=lifecycle)
    (tmp_path / "ce-rejection-capture.json").write_text(
        json.dumps(
            {
                "mode": "local_gate_fixture",
                "attack": attack,
                "decoded_serialized_body": decoded,
                "row_coverage": row_coverage,
                "factory_rebound_coverage": bound["final_request_evidence_coverage"],
                "diagnostic": "ce_required_command_authority_final_request_mismatch",
                "fake_transport_calls": len(calls),
                "physical_effects": 0,
                "one_durable_rejection_asserted": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_final_context_evidence_rejects_incomplete_claimed_physical_audit() -> None:
    context_ref = "a" * 24

    class _ForgedPort:
        def final_context_evidence(self) -> tuple[str, dict[str, Any]]:
            return (
                context_ref,
                {
                    "audit_scope": "provider_native_wire",
                    "final_request_evidence_coverage": {"context_snapshot_ref": context_ref},
                },
            )

    assert validated_final_context_evidence(_ForgedPort(), expected_port_type=_ForgedPort) is None


@pytest.mark.parametrize(
    ("case", "rejection_code"),
    [
        ("missing_tools", "final_request_evidence_coverage_failed"),
        ("wrong_role", "final_request_role_identity_mismatch"),
    ],
)
def test_port_owned_prequalification_rejection_covers_tools_and_role_without_effects(
    tmp_path: Path,
    case: str,
    rejection_code: str,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    dispatch_port = gate._test_dispatch_port
    request = SimpleNamespace(
        context={"final_request_evidence_required": True},
        options={},
    )

    with pytest.raises(FinalRequestEvidenceCoverageError):
        dispatch_port.enforce_final_request_evidence_coverage(
            ai_request=request,
            audit=_failed_coverage_audit(dispatch_port, case=case),
        )

    _assert_one_coverage_rejection_and_zero_physical_effects(
        workspace=tmp_path,
        gate=gate,
        lifecycle=lifecycle,
        rejection_code=rejection_code,
    )


@pytest.mark.asyncio
async def test_sync_prequalification_coverage_failure_records_one_rejection_and_zero_effects(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    dispatch_port = gate._test_dispatch_port
    frozen = dispatch_port.frozen_semantic_request
    prepared = _prepared_with_dispatch_port(frozen=frozen, dispatch_port=dispatch_port)
    prepared.ai_request.context["final_request_evidence_required"] = True
    executor_calls = 0

    class _Executor:
        async def invoke(self, _request: object, *, physical_dispatch_port: object) -> str:
            del _request, physical_dispatch_port
            nonlocal executor_calls
            executor_calls += 1
            return "forbidden"

    with (
        patch.object(invoker_helpers, "context_snapshot_matches_frozen_attempt", return_value=True),
        patch.object(
            invoker_helpers,
            "build_final_request_context_audit_for_request",
            return_value=_failed_coverage_audit(dispatch_port),
        ),
        pytest.raises(FinalRequestEvidenceCoverageError),
    ):
        await invoker_module._invoke_executor_with_factory_dispatch(
            executor=_Executor(),
            prepared=prepared,
            request=prepared.ai_request,
            profile=request_fact_test._profile("director"),
        )

    assert executor_calls == 0
    _assert_one_coverage_rejection_and_zero_physical_effects(
        workspace=tmp_path,
        gate=gate,
        lifecycle=lifecycle,
    )


@pytest.mark.asyncio
async def test_structured_prequalification_coverage_failure_records_one_rejection_and_zero_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    dispatch_port = gate._test_dispatch_port
    prepared = _prepared_with_dispatch_port(
        frozen=dispatch_port.frozen_semantic_request,
        dispatch_port=dispatch_port,
    )
    prepared.ai_request.context["final_request_evidence_required"] = True
    profile = request_fact_test._profile("director")
    invoker = invoker_module.LLMInvoker(workspace=str(tmp_path))
    native_dispatch = AsyncMock(side_effect=AssertionError("structured provider dispatch must not run"))
    instructor_dispatch = AsyncMock(side_effect=AssertionError("instructor dispatch must not run"))
    fallback_dispatch = AsyncMock(side_effect=AssertionError("structured fallback must not run"))
    monkeypatch.setattr(invoker_module.LLMInvoker, "_profile_for_healthy_binding", lambda *_args: profile)
    monkeypatch.setattr(invoker_module.LLMInvoker, "_try_native_response_format_structured", native_dispatch)
    monkeypatch.setattr(invoker_module.LLMInvoker, "_try_instructor_structured", instructor_dispatch)
    monkeypatch.setattr(invoker_module.LLMInvoker, "_run_structured_fallback", fallback_dispatch)

    with (
        patch.object(
            invoker_module.LLMRequestPreparer,
            "_prepare_llm_request",
            AsyncMock(return_value=prepared),
        ),
        patch.object(invoker_structured, "_store_call_start_context_snapshot", AsyncMock()),
        patch.object(
            invoker_structured,
            "build_final_request_context_audit_for_request",
            return_value=_failed_coverage_audit(dispatch_port),
        ),
    ):
        response = await invoker.call_structured(
            profile=profile,
            system_prompt="You are Director.",
            context=SimpleNamespace(message="Implement.", domain="code", context_override={}),
            response_model=dict,
            run_id=prepared.factory_semantic_request.identity.run_id,
        )

    assert "Final provider request evidence coverage failed" in str(response.error)
    native_dispatch.assert_not_awaited()
    instructor_dispatch.assert_not_awaited()
    fallback_dispatch.assert_not_awaited()
    _assert_one_coverage_rejection_and_zero_physical_effects(
        workspace=tmp_path,
        gate=gate,
        lifecycle=lifecycle,
    )


@pytest.mark.asyncio
async def test_stream_prequalification_coverage_failure_records_one_rejection_and_zero_effects(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path, stream=True)
    dispatch_port = gate._test_dispatch_port
    prepared = _prepared_with_dispatch_port(
        frozen=dispatch_port.frozen_semantic_request,
        dispatch_port=dispatch_port,
    )
    prepared.ai_request.context["final_request_evidence_required"] = True
    executor_calls = 0
    store_calls = 0

    class _Executor:
        async def invoke_stream(self, _request: object, *, physical_dispatch_port: object):
            del _request, physical_dispatch_port
            nonlocal executor_calls
            executor_calls += 1
            yield {"type": "chunk", "content": "forbidden"}

    async def _store_context_messages(*_args: object, **_kwargs: object) -> str:
        nonlocal store_calls
        store_calls += 1
        return "a" * 24

    engine = StreamEngine(
        workspace=str(tmp_path),
        get_executor=lambda: _Executor(),
        allow_native_tool_text_fallback_fn=Mock(return_value=False),
        emit_call_start_event=Mock(),
        emit_call_error_event=Mock(),
        emit_call_end_event=Mock(),
        emit_call_retry_event=Mock(),
        store_context_messages=_store_context_messages,
    )
    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.stream_engine.build_final_request_context_audit_for_request",
            return_value=_failed_coverage_audit(dispatch_port),
        ),
        pytest.raises(FinalRequestEvidenceCoverageError),
    ):
        _ = [
            event
            async for event in engine.run_stream(
                profile=request_fact_test._profile("director"),
                prepared=prepared,
                context=SimpleNamespace(context_override={}, stream_cancelled=False),
                start_time=time.perf_counter(),
                role_id="director",
                run_id=dispatch_port.frozen_semantic_request.identity.run_id,
                task_id=None,
                attempt=0,
                model="model-a",
                call_id=dispatch_port.frozen_semantic_request.identity.call_id,
                event_emitter=None,
                turn_round=0,
            )
        ]

    assert executor_calls == 0
    assert store_calls == 0
    _assert_one_coverage_rejection_and_zero_physical_effects(
        workspace=tmp_path,
        gate=gate,
        lifecycle=lifecycle,
    )


def test_factory_gate_without_sidecar_minted_qualification_proof_fails_closed(tmp_path: Path) -> None:
    semantic_request = {
        "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
        "tools": [],
        "tool_choice": "auto",
        "response_format": None,
        "semantic_options": {"temperature": 0.1},
    }
    with pytest.raises(RuntimeError, match="final_provider_attempt_qualification_proof_required"):
        FinalProviderAttemptGate(
            workspace=str(tmp_path),
            verification_scope="factory",
            factory_run_id="factory-run-1",
            run_id="run-1",
            role="director",
            turn_id="turn-1",
            call_id="call-1",
            request_freeze_id="freeze-1",
            provider="openai",
            model="model-1",
            semantic_request=semantic_request,
            lifecycle=None,
            snapshot_store=None,
            physical_attempt_control_port=None,
            execution_authority_hash="f" * 64,
            attempt_budget=32,
        )


def test_factory_gate_rejects_qualification_proof_bound_to_other_request(tmp_path: Path) -> None:
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    proof, semantic_request, _wire, frozen, _dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    drifted_request = {**semantic_request, "tool_choice": "required"}
    with pytest.raises(RuntimeError, match="final_provider_attempt_qualification_proof_invalid"):
        FinalProviderAttemptGate(
            workspace=str(tmp_path),
            verification_scope="factory",
            factory_run_id="factory-run-1",
            run_id=frozen.identity.run_id,
            role="director",
            turn_id=frozen.identity.turn_id,
            call_id=frozen.identity.call_id,
            request_freeze_id=frozen.identity.request_freeze_id,
            provider=str(payload["provider_id"]),
            model=str(payload["model"]),
            semantic_request=drifted_request,
            lifecycle=None,
            snapshot_store=None,
            physical_attempt_control_port=None,
            execution_authority_hash="f" * 64,
            attempt_budget=32,
            qualification_proof=proof,
        )


def test_factory_gate_rejects_provider_identity_mutation_before_reservation(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    gate._provider = "forged-provider-id"

    with pytest.raises(RuntimeError, match="final_provider_attempt_qualification_proof_invalid"):
        gate._freeze(gate._test_qualified_wire)  # type: ignore[attr-defined]

    assert lifecycle.query_strict() == ()


def test_qualification_context_ref_contains_exact_final_physical_request(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    proof, _semantic_request, wire, _frozen, _dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
    )
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    snapshot = json.loads(Path(repository.snapshot_path(proof.context_snapshot_ref)).read_text(encoding="utf-8"))

    assert snapshot["schema_version"] == "llm.final_physical_provider_request_context.v1"
    provider_request = snapshot["provider_request"]
    assert provider_request["final_physical_request"] == wire
    assert provider_request["physical_route_authority"]["provider_type"] == "openai_compat"
    assert (
        provider_request["final_physical_wire_hash"]
        == provider_request["final_request_context_audit"]["final_physical_wire_hash"]
    )
    audit = provider_request["final_request_context_audit"]
    expected_metrics = provider_native_request_metrics(
        body=wire["body"],
        native_protocol=provider_request["physical_route_authority"]["native_protocol"],
        context_window_tokens=audit["context_window_tokens"],
    )
    assert {key: audit[key] for key in expected_metrics} == expected_metrics
    assert audit["audit_scope"] == "provider_native_wire"


@pytest.mark.parametrize(
    ("provider_type", "provider_config", "native_protocol"),
    [
        (
            "openai_compat",
            {"base_url": "https://example.test", "api_path": "/v1/chat/completions"},
            "openai_chat_completions",
        ),
        (
            "anthropic_compat",
            {"base_url": "https://example.test", "api_path": "/v1/messages"},
            "anthropic_messages",
        ),
    ],
)
def test_stream_final_audit_is_recomputed_from_exact_native_body(
    tmp_path: Path,
    provider_type: str,
    provider_config: dict[str, Any],
    native_protocol: str,
) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    proof, _semantic, wire, _frozen, _dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
        provider_type=provider_type,
        provider_config=provider_config,
        stream=True,
    )

    audit = proof.audit()
    expected = provider_native_request_metrics(
        body=wire["body"],
        native_protocol=native_protocol,
        context_window_tokens=audit["context_window_tokens"],
    )
    assert {key: audit[key] for key in expected} == expected
    assert wire["body"]["stream"] is True


@pytest.mark.parametrize("role", ["pm", "architect", "chief_engineer", "director", "qa"])
def test_native_metric_tamper_fails_closed_for_every_factory_role(
    tmp_path: Path,
    role: str,
) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    proof, _semantic, wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
        role=role,
    )
    tampered_audit = proof.audit()
    tampered_audit["request_control_token_estimate"] += 1

    with pytest.raises(
        qualification_module.FinalProviderAttemptQualificationError,
        match="final_request_native_request_control_token_estimate_mismatch",
    ):
        qualification_module._mint_final_provider_attempt_qualification_proof(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=tampered_audit,
            context_snapshot_ref=proof.context_snapshot_ref,
            wire_request=wire,
            physical_route_authority=dispatch_port._physical_route_authority,
        )


def test_context_os_prompt_audit_failure_blocks_physical_qualification(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    proof, _semantic, _wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
        role="chief_engineer",
    )
    failed_context_audit = proof.audit()
    failed_context_audit["context_os_audit"] = {
        "ok": False,
        "expected": True,
        "control_plane": {
            "isolated": False,
            "metadata_key_hits": [],
            "content_hits": ["chief_engineer_deadline_decision:"],
        },
    }

    with pytest.raises(
        qualification_module.FinalProviderAttemptQualificationError,
        match="final_request_context_os_audit_failed",
    ):
        qualification_module.qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=failed_context_audit,
            context_snapshot_ref=proof.context_snapshot_ref,
        )


def test_context_quality_error_blocks_physical_qualification(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    proof, _semantic, _wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
        role="director",
    )
    failed_quality_audit = proof.audit()
    failed_quality_audit["context_quality"] = {
        "context_needs_review": True,
        "findings": [
            {
                "code": "execution_strategy_output_budget_under_applied",
                "severity": "error",
                "expected_max_tokens": 128000,
                "actual_max_tokens": 7000,
            }
        ],
    }

    with pytest.raises(
        qualification_module.FinalProviderAttemptQualificationError,
        match="final_request_context_quality_failed",
    ):
        qualification_module.qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=failed_quality_audit,
            context_snapshot_ref=proof.context_snapshot_ref,
        )


def test_sync_terminal_event_uses_one_physical_ref_and_native_audit(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    _proof, _semantic, wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
    )
    dispatch_port._qualified_gate(wire, expected_stream=False)
    final_evidence = dispatch_port.final_context_evidence()
    assert final_evidence is not None
    final_ref, final_audit = final_evidence
    prepared = _prepared_with_dispatch_port(frozen=frozen, dispatch_port=dispatch_port)
    emitted = Mock()
    invoker = invoker_module.LLMInvoker(workspace=str(tmp_path))
    invoker._event_emitter = SimpleNamespace(emit_call_end_event=emitted)

    invoker._finalize_call_response(
        cache=None,
        prepared=prepared,
        active_request=prepared.ai_request,
        response=SimpleNamespace(
            raw={},
            output="ok",
            model="model-a",
            provider_id="provider-a",
            usage=None,
        ),
        cache_eligible=False,
        prompt_fingerprint=None,
        temperature=0.0,
        model="model-a",
        profile=SimpleNamespace(provider_id="provider-a", model="model-a"),
        prompt_tokens=1,
        turn_round=0,
        role_id="director",
        run_id="run-1",
        task_id=None,
        attempt=0,
        call_id="call-1",
        event_emitter=None,
        start_time=time.perf_counter(),
    )

    metadata = emitted.call_args.kwargs["metadata"]
    assert metadata["context_snapshot_ref"] == final_ref
    assert metadata["final_request_context_audit"] == final_audit
    assert metadata["final_request_context_audit"]["audit_scope"] == "provider_native_wire"


@pytest.mark.asyncio
async def test_stream_terminal_events_use_one_physical_ref_and_native_audit(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    _proof, _semantic, wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
        stream=True,
    )
    semantic_ref = dispatch_port._qualified_context_snapshot_ref
    semantic_audit = copy.deepcopy(dispatch_port._qualified_audit)
    prepared = _prepared_with_dispatch_port(frozen=frozen, dispatch_port=dispatch_port)
    emitted_end = Mock()

    class _Executor:
        async def invoke_stream(self, _request: object, *, physical_dispatch_port: object):
            assert physical_dispatch_port is dispatch_port
            cast(Any, dispatch_port)._qualified_gate(wire, expected_stream=True)
            yield {"type": "chunk", "content": "ok"}

    async def _store_context_messages(*_args: object, **_kwargs: object) -> str:
        return semantic_ref

    engine = StreamEngine(
        workspace=str(tmp_path),
        get_executor=lambda: _Executor(),
        allow_native_tool_text_fallback_fn=Mock(return_value=False),
        emit_call_start_event=Mock(),
        emit_call_error_event=Mock(),
        emit_call_end_event=emitted_end,
        emit_call_retry_event=Mock(),
        store_context_messages=_store_context_messages,
    )
    with patch(
        "polaris.cells.roles.kernel.internal.llm_caller.stream_engine.build_final_request_context_audit_for_request",
        return_value=semantic_audit,
    ):
        events = [
            event
            async for event in engine.run_stream(
                profile=SimpleNamespace(
                    provider_id="provider-a",
                    role_id="director",
                    max_context_tokens=32768,
                ),
                prepared=prepared,
                context=SimpleNamespace(context_override={}, stream_cancelled=False),
                start_time=time.perf_counter(),
                role_id="director",
                run_id="run-1",
                task_id=None,
                attempt=0,
                model="model-a",
                call_id="call-1",
                event_emitter=None,
                turn_round=0,
            )
        ]

    final_evidence = dispatch_port.final_context_evidence()
    assert final_evidence is not None
    final_ref, final_audit = final_evidence
    context_metadata = next(event for event in events if event.get("type") == "context_metadata")
    assert context_metadata["context_snapshot_ref"] == final_ref
    assert context_metadata["final_request_context_audit"] == final_audit
    terminal_metadata = emitted_end.call_args.kwargs["metadata"]
    assert terminal_metadata["context_snapshot_ref"] == final_ref
    assert terminal_metadata["final_request_context_audit"] == final_audit


def test_real_anthropic_provider_crosses_native_sidecar_and_factory_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    control.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role="director",
            stage="director_dispatch",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="stage-nonce-anthropic",
            execution_authority_hash="f" * 64,
            attempt_budget=32,
        )
    )
    provider_config = {
        "base_url": "https://example.test",
        "api_path": "/v1/messages",
        "api_key": "secret",
    }
    _proof, _semantic, _wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
        provider_type="anthropic_compat",
        provider_config=provider_config,
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    actual_config = {
        **provider_config,
        "chat_messages": payload["messages"],
        "tools": payload["tools"],
        "tool_choice": payload["tool_choice"],
        "temperature": payload["temperature"],
        "max_tokens": payload["max_tokens"],
    }
    posts: list[dict[str, Any]] = []

    class _AnthropicResponse(_Response):
        def json(self) -> dict[str, Any]:
            return {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    def _post(*_args: object, **kwargs: Any) -> _AnthropicResponse:
        posts.append(dict(kwargs))
        return _AnthropicResponse()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    with bind_physical_provider_dispatch_port(dispatch_port):
        result = AnthropicProvider().invoke("unused fallback", str(payload["model"]), actual_config)

    assert result.ok is True
    assert len(posts) == 1
    assert posts[0]["json"] == dispatch_port._physical_route_authority["expected_body"]
    facts = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    ).query_strict()
    assert [fact["event_type"] for fact in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    final_evidence = dispatch_port.final_context_evidence()
    assert final_evidence is not None
    _final_ref, final_audit = final_evidence
    expected_metrics = provider_native_request_metrics(
        body=posts[0]["json"],
        native_protocol="anthropic_messages",
        context_window_tokens=final_audit["context_window_tokens"],
    )
    assert {key: final_audit[key] for key in expected_metrics} == expected_metrics
    prepared = PreparedLLMRequest(
        messages=payload["messages"],
        input_text="",
        context_result=None,
        context_summary="",
        request_options={},
        ai_request=SimpleNamespace(context={"context_snapshot_ref": "b" * 24}),
        factory_semantic_request=frozen,
        factory_dispatch_port=dispatch_port,
    )
    active_request = prepared.ai_request
    assert invoker_module.LLMInvoker._extract_final_context_snapshot_ref(prepared, active_request) == _final_ref
    projected = invoker_helpers._with_final_request_context_audit(
        {},
        prepared=prepared,
        active_request=active_request,
        profile=SimpleNamespace(),
    )
    assert projected["final_request_context_audit"] == final_audit
    assert projected["contextTokens"] == final_audit["final_request_token_estimate"]


def test_factory_gate_conserves_reserve_start_send_terminal_under_one_authority(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, _lifecycle = _gate(tmp_path)
    sends: list[Mapping[str, Any]] = []

    def send(frozen: Mapping[str, Any]) -> str:
        sends.append(frozen)
        return "ok"

    result = gate.dispatch_sync(
        wire_request=_wire_request(gate),
        send=send,
    )

    assert result == "ok"
    assert len(sends) == 1
    state = gate._physical_attempt_control_port.budget_state("f" * 64)
    assert state.reserved_count == 0
    assert state.committed_count == 1
    assert state.terminal_count == 1
    assert state.consumed_attempts == 1
    assert state.settled is True


def test_invalid_factory_authority_creates_no_lifecycle_ledger_or_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    ensure_calls: list[object] = []
    snapshot_calls: list[object] = []
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle.ensure_segmented_fact_ledger",
        ensure_calls.append,
    )

    class _SnapshotStore:
        def persist_and_pin(self, attempt: object) -> object:
            snapshot_calls.append(attempt)
            raise AssertionError("invalid authority reached snapshot persistence")

    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    control.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role="director",
            stage="director_dispatch",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="stage-nonce-1",
            execution_authority_hash="f" * 64,
            attempt_budget=32,
        )
    )
    proof, semantic_request, qualified_wire, frozen, _dispatch_port = _qualified_factory_fixture(
        workspace=tmp_path,
        physical_attempt_control_port=control,
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    gate = FinalProviderAttemptGate.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
        run_id=frozen.identity.run_id,
        role="director",
        turn_id=frozen.identity.turn_id,
        call_id=frozen.identity.call_id,
        request_freeze_id=frozen.identity.request_freeze_id,
        provider=str(payload["provider_id"]),
        model=str(payload["model"]),
        semantic_request=semantic_request,
        snapshot_store=_SnapshotStore(),
        physical_attempt_control_port=control,
        execution_authority_hash="e" * 64,
        attempt_budget=32,
        qualification_proof=proof,
    )
    gate._test_qualified_wire = qualified_wire  # type: ignore[attr-defined]

    assert ensure_calls == []
    with pytest.raises(
        FactoryPhysicalAttemptControlError,
        match="factory_physical_attempt_execution_authority_hash_mismatch",
    ):
        gate.dispatch_sync(wire_request=_wire_request(gate), send=lambda _request: "must-not-send")
    assert ensure_calls == []
    assert snapshot_calls == []


def test_factory_gate_uses_injected_physical_control_port_as_only_drain_state(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, _lifecycle = _gate(tmp_path)

    assert gate.drain_coordinator is gate._physical_attempt_control_port


def test_governed_physical_http_attempt_persists_snapshot_pin_start_and_terminal_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(
        tmp_path,
        wire_factory=lambda body: {
            "endpoint": "https://example.test/v1/chat/completions",
            "headers": {"Authorization": "Bearer secret"},
            "body": body,
            "transport": {"kind": "aiohttp.ClientSession.post", "timeout": 1},
        },
    )
    physical_calls: list[dict[str, Any]] = []

    def _post(*_args: object, **kwargs: Any) -> _Response:
        physical_calls.append(dict(kwargs))
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {"Authorization": "Bearer secret"},
        _wire_body(gate),
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )
    assert result.ok is True
    assert len(physical_calls) == 1
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[0]["payload"]["provider_request_id"] == facts[1]["payload"]["provider_request_id"]
    assert facts[1]["payload"]["status"] == "completed"
    assert len(facts[0]["payload"]["context_snapshot_ref"]) == 24
    assert facts[0]["payload"]["pin_hash"]
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    pins = repository.query_snapshot_pins(facts[0]["payload"]["context_snapshot_ref"])
    assert len(pins) == 1
    pin = pins[0]
    assert pin.workspace_abs == str(tmp_path.resolve())
    assert pin.runtime_root == repository.runtime_root
    assert pin.storage_identity_token == repository.storage_identity_token
    assert pin.snapshot_logical_path == f"runtime/contexts/{pin.context_snapshot_ref[:2]}/{pin.context_snapshot_ref}"
    assert pin.snapshot_absolute_path == repository.snapshot_path(pin.context_snapshot_ref)
    assert pin.snapshot_source == "roles.kernel.final_provider_attempt"
    assert pin.factory_run_id == "factory-run-1"
    assert pin.role == "director"
    assert pin.verification_scope == "factory"
    assert pin.request_freeze_id == gate._request_freeze_id
    assert pin.provider_request_id == facts[0]["payload"]["provider_request_id"]
    assert pin.composite_request_hash == facts[0]["payload"]["composite_request_hash"]
    assert Path(repository.pin_path(pin.context_snapshot_ref, pin.provider_request_id)).is_file()
    assert gate.drain_coordinator.snapshot().settled is True


def test_governed_json_parse_failure_records_failed_physical_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    class _InvalidJsonResponse(_Response):
        def json(self) -> dict[str, Any]:
            raise ValueError("invalid provider JSON")

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post",
        lambda *_args, **_kwargs: _InvalidJsonResponse(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert result.error == "invalid provider JSON"
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminals) == 1
    assert terminals[0]["payload"]["status"] == "failed"


def test_governed_json_parse_failure_retries_as_new_failed_then_completed_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls = 0

    class _InvalidJsonResponse(_Response):
        def json(self) -> dict[str, Any]:
            raise ValueError("invalid provider JSON")

    responses = iter((_InvalidJsonResponse(), _Response()))

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return next(responses)

    class _Clock:
        current = 1.0

        def time(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.current += seconds

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        1,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        clock=_Clock(),
        physical_dispatch_port=gate,
    )

    assert result.ok is True
    assert physical_calls == 2
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert [item["payload"]["status"] for item in terminals] == ["failed", "completed"]
    assert [item["payload"]["attempt_number"] for item in terminals] == [1, 2]


@pytest.mark.parametrize(
    "extract_error",
    [KeyError("missing provider content"), TypeError("invalid provider content")],
    ids=["key-error", "type-error"],
)
def test_governed_extract_output_error_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extract_error: Exception,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _extract_output(_body: dict[str, Any]) -> str:
        raise extract_error

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post",
        lambda *_args, **_kwargs: _Response(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        _extract_output,
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    terminal = lifecycle.query_strict()[-1]
    assert terminal["event_type"] == "provider_attempt.terminal"
    assert terminal["payload"]["status"] == "failed"


def test_governed_usage_extraction_error_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _usage_error(_prompt: str, _output: str, _body: dict[str, Any]) -> Usage:
        raise ValueError("invalid provider usage")

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post",
        lambda *_args, **_kwargs: _Response(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        _usage_error,
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert result.error == "invalid provider usage"
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "failed"


def test_governed_finalize_exception_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_finalize(
        _cls: type[Any],
        _payload: Any,
        *,
        visible_text: str | None = None,
    ) -> Any:
        del visible_text
        raise ValueError("provider finalization failed")

    monkeypatch.setattr(provider_helpers.LLMResponseParser, "finalize_response", classmethod(_fail_finalize))
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post",
        lambda *_args, **_kwargs: _Response(),
    )
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert result.error == "provider finalization failed"
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "failed"


def test_governed_finalize_semantic_failure_keeps_physical_terminal_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls = 0

    class _TruncatedReasoningResponse(_Response):
        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {"content": None, "reasoning_content": "unfinished reasoning"},
                        "finish_reason": "length",
                    }
                ]
            }

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _TruncatedReasoningResponse()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        2,
        "prompt",
        lambda _body: "",
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert "reasoning truncated" in str(result.error)
    assert result.thinking == "unfinished reasoning"
    assert physical_calls == 1
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "completed"


def test_governed_success_parses_extracts_and_projects_usage_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    calls = {"json": 0, "extract": 0, "usage": 0, "post": 0}

    class _Clock:
        current = 1.0

        def time(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.current += seconds

        def advance(self, seconds: float) -> None:
            self.current += seconds

    clock = _Clock()

    class _CountingResponse(_Response):
        def json(self) -> dict[str, Any]:
            calls["json"] += 1
            clock.advance(1.0)
            return super().json()

    def _post(*_args: object, **_kwargs: object) -> _Response:
        calls["post"] += 1
        return _CountingResponse()

    def _extract(body: dict[str, Any]) -> str:
        calls["extract"] += 1
        clock.advance(10.0)
        return str(body["choices"][0]["message"]["content"])

    def _usage(_prompt: str, _output: str, _body: dict[str, Any]) -> Usage:
        calls["usage"] += 1
        clock.advance(100.0)
        return Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        _extract,
        _usage,
        clock=clock,
        physical_dispatch_port=gate,
    )

    assert result.ok is True
    assert result.output == "ok"
    assert result.latency_ms == 1000
    assert calls == {"json": 1, "extract": 1, "usage": 1, "post": 1}
    assert lifecycle.query_strict()[-1]["payload"]["status"] == "completed"


def test_governed_provider_retry_creates_one_unique_lifecycle_pair_per_physical_http_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    responses = iter(
        (
            (
                rate_limited_response := _Response(
                    status_code=429,
                    text="rate limited",
                    headers={"Retry-After": "0"},
                )
            ),
            _Response(),
        )
    )
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return next(responses)

    class _Clock:
        current = 1.0
        sleeps: list[float]

        def __init__(self) -> None:
            self.sleeps = []

        def time(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.current += seconds

    clock = _Clock()
    original_retry_after_parser = provider_helpers._parse_retry_after_seconds

    def _parse_retry_after(response: Any) -> float | None:
        assert response is rate_limited_response
        return original_retry_after_parser(response)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    monkeypatch.setattr(provider_helpers, "_parse_retry_after_seconds", _parse_retry_after)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        clock=clock,
        physical_dispatch_port=gate,
    )
    assert result.ok is True
    assert physical_calls == 2
    assert clock.sleeps == [0.0]
    facts = lifecycle.query_strict()
    starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
    terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
    assert len(starts) == len(terminals) == 2
    request_ids = [item["payload"]["provider_request_id"] for item in starts]
    assert len(set(request_ids)) == 2
    assert request_ids == [item["payload"]["provider_request_id"] for item in terminals]
    assert [item["payload"]["attempt_number"] for item in starts] == [1, 2]
    assert [item["payload"]["status"] for item in terminals] == ["failed", "completed"]


def test_governed_context_overflow_rewrite_requires_new_freeze_before_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(
        tmp_path,
        semantic_options={"temperature": 0.1, "max_tokens": 4096},
    )
    responses = iter(
        (
            _Response(
                status_code=400,
                text=(
                    "maximum context length is 8192 tokens; this request asked for "
                    "4096 output tokens and at least 7000 input tokens"
                ),
            ),
            _Response(),
        )
    )
    sent_max_tokens: list[int] = []

    def _post(*_args: object, **kwargs: Any) -> _Response:
        sent_max_tokens.append(int(kwargs["json"]["max_tokens"]))
        return next(responses)

    payload = _wire_body(gate)
    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    with pytest.raises(
        qualification_module.FinalProviderAttemptQualificationError,
        match="physical_wire_max_tokens_drift",
    ):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            payload,
            1,
            0,
            "prompt",
            lambda body: body["choices"][0]["message"]["content"],
            lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            physical_dispatch_port=gate._test_dispatch_port,  # type: ignore[attr-defined]
        )

    assert sent_max_tokens == [4096]
    assert payload["max_tokens"] == 1176
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert [item["payload"]["status"] for item in terminals] == ["failed"]
    assert [item["payload"]["attempt_number"] for item in terminals] == [1]


@pytest.mark.parametrize("status_code", [401, 500])
def test_governed_http_failure_records_failed_terminal_and_preserves_response_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    physical_calls = 0
    response_body = f"provider failure {status_code}"

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response(status_code=status_code, text=response_body)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        1,
        0,
        "prompt",
        lambda body: str(body),
        lambda _prompt, _output, _body: Usage.estimate("", ""),
        physical_dispatch_port=gate,
    )

    assert result.ok is False
    assert response_body in str(result.error)
    assert physical_calls == 1
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[-1]["payload"]["status"] == "failed"


def test_governed_snapshot_or_pin_failure_keeps_physical_http_count_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)

    class _FailingSnapshotStore:
        def persist_and_pin(self, _attempt: object) -> object:
            raise OSError("pin fsync failed")

    gate, lifecycle = _gate(tmp_path, snapshot_store=_FailingSnapshotStore())
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    with pytest.raises(OSError, match="pin fsync failed"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            _wire_body(gate),
            1,
            0,
            "prompt",
            lambda body: str(body),
            lambda _prompt, _output, _body: Usage.estimate("", ""),
            physical_dispatch_port=gate,
        )
    assert physical_calls == 0
    assert lifecycle.query_strict() == ()


def test_frozen_wire_is_detached_from_original_and_callback_cannot_mutate_authoritative_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    payload: dict[str, Any] = {}

    class _MutatingSnapshotStore:
        frozen_attempt: FrozenFinalProviderAttemptV1 | None = None

        def persist_and_pin(self, attempt: FrozenFinalProviderAttemptV1) -> object:
            self.frozen_attempt = attempt
            payload["max_tokens"] = 999
            dispatch_view = attempt.dispatch_view
            with pytest.raises(TypeError):
                dispatch_view["body"]["max_tokens"] = 777
            return SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    snapshot_store = _MutatingSnapshotStore()
    gate, _lifecycle = _gate(
        tmp_path,
        snapshot_store=snapshot_store,
        semantic_options={"temperature": 0.1, "max_tokens": 128},
    )
    payload.update(_wire_body(gate))
    dispatched_bodies: list[dict[str, Any]] = []

    def _post(*_args: object, **kwargs: Any) -> _Response:
        dispatched_bodies.append(dict(kwargs["json"]))
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    result = invoke_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        payload,
        1,
        0,
        "prompt",
        lambda body: body["choices"][0]["message"]["content"],
        lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        physical_dispatch_port=gate,
    )
    assert result.ok is True
    assert payload["max_tokens"] == 999
    assert dispatched_bodies[0]["max_tokens"] == 128
    frozen_attempt = snapshot_store.frozen_attempt
    assert frozen_attempt is not None
    assert frozen_attempt.durable_copy()["physical_wire"]["body"]["max_tokens"] == 128


def test_sync_cancellation_records_terminal_before_it_escapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _post(*_args: object, **_kwargs: object) -> _Response:
        raise KeyboardInterrupt("cancelled by caller")

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    with pytest.raises(KeyboardInterrupt, match="cancelled by caller"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            _wire_body(gate),
            1,
            0,
            "prompt",
            lambda body: str(body),
            lambda _prompt, _output, _body: Usage.estimate("", ""),
            physical_dispatch_port=gate,
        )
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[-1]["payload"]["status"] == "cancelled"


def test_terminal_fsync_failure_blocks_successful_physical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_terminal(*_args: object, **_kwargs: object) -> None:
        raise OSError("terminal fsync failed")

    monkeypatch.setattr(lifecycle, "append_terminal", _fail_terminal)
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    with pytest.raises(OSError, match="terminal fsync failed"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            _wire_body(gate),
            1,
            0,
            "prompt",
            lambda body: body["choices"][0]["message"]["content"],
            lambda _prompt, _output, _body: Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            physical_dispatch_port=gate,
        )
    assert physical_calls == 1
    sync_drain = gate.drain_coordinator.snapshot()
    assert sync_drain.settled is False
    assert sync_drain.inflight_request_ids
    assert sync_drain.terminal_failures[0].error_type == "OSError"


def test_actual_pin_fsync_or_reread_failure_keeps_physical_http_count_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    physical_calls = 0

    def _post(*_args: object, **_kwargs: object) -> _Response:
        nonlocal physical_calls
        physical_calls += 1
        return _Response()

    original_verify = ContextSnapshotAuditPinRepository._fsync_and_verify

    def _fail_pin_verify(self: ContextSnapshotAuditPinRepository, path: str, expected: bytes) -> None:
        if "/pins/" in path.replace("\\", "/"):
            raise OSError("pin fsync reread failed")
        original_verify(self, path, expected)

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling.bounded_http_post", _post)
    gate, lifecycle = _gate(tmp_path)
    monkeypatch.setattr(ContextSnapshotAuditPinRepository, "_fsync_and_verify", _fail_pin_verify)
    with pytest.raises(OSError, match="pin fsync reread failed"):
        invoke_with_retry(
            "https://example.test/v1/chat/completions",
            {},
            _wire_body(gate),
            1,
            0,
            "prompt",
            lambda body: str(body),
            lambda _prompt, _output, _body: Usage.estimate("", ""),
            physical_dispatch_port=gate,
        )
    assert physical_calls == 0
    assert lifecycle.query_strict() == ()


@pytest.mark.asyncio
async def test_async_success_and_failure_each_append_exactly_one_terminal(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    async def _success(_wire: object) -> str:
        return "ok"

    assert await gate.dispatch_async(wire_request=_wire_request(gate), send=_success) == "ok"

    async def _failure(_wire: object) -> str:
        raise ValueError("provider failed")

    with pytest.raises(ValueError, match="provider failed"):
        await gate.dispatch_async(wire_request=_wire_request(gate), send=_failure)

    facts = lifecycle.query_strict()
    starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
    terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
    assert len(starts) == len(terminals) == 2
    assert [item["payload"]["status"] for item in terminals] == ["completed", "failed"]
    assert [item["payload"]["provider_request_id"] for item in starts] == [
        item["payload"]["provider_request_id"] for item in terminals
    ]
    drained = await gate.drain_coordinator.wait_settled(
        verification_scope="factory",
        scope_id="factory-run-1",
        timeout_seconds=0.1,
    )
    assert drained.settled is True


@pytest.mark.asyncio
async def test_async_cancel_waits_for_shielded_terminal_ack_even_after_second_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    terminal_entered = threading.Event()
    release_terminal = threading.Event()
    terminal_complete = threading.Event()
    terminal_calls = 0
    original_terminal = lifecycle.append_terminal

    def _blocking_terminal(*args: Any, **kwargs: Any) -> object:
        nonlocal terminal_calls
        terminal_calls += 1
        terminal_entered.set()
        assert release_terminal.wait(timeout=2)
        terminal_complete.set()
        return original_terminal(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "append_terminal", _blocking_terminal)
    never = asyncio.Event()

    async def _send(_wire: object) -> str:
        await never.wait()
        return "unreachable"

    task = asyncio.create_task(gate.dispatch_async(wire_request=_wire_request(gate), send=_send))
    await asyncio.sleep(0)
    task.cancel()
    assert await asyncio.to_thread(terminal_entered.wait, 1)
    assert gate.drain_coordinator.inflight_request_ids
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_terminal.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert terminal_complete.is_set()
    assert terminal_calls == 1
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_blocking_worker_outlives_cancelled_waiter_and_owns_terminal(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    worker_entered = threading.Event()
    release_worker = threading.Event()

    def _worker(_wire: object) -> str:
        worker_entered.set()
        assert release_worker.wait(timeout=2)
        return "worker-result"

    task = asyncio.create_task(gate.dispatch_blocking_async(wire_request=_wire_request(gate), send=_worker))
    assert await asyncio.to_thread(worker_entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert gate.drain_coordinator.inflight_request_ids
    assert not tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    with pytest.raises(ProviderAttemptDrainError) as pending:
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.01,
        )
    assert pending.value.code == "provider_attempt_drain_timeout"
    assert pending.value.result.inflight_request_ids

    release_worker.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    terminals = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminals) == 1
    assert terminals[0]["payload"]["status"] == "completed"
    assert gate.drain_coordinator.inflight_request_ids == ()
    assert (
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )
    ).settled


@pytest.mark.asyncio
async def test_async_terminal_persistence_failure_rejects_result_and_fails_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_terminal(*_args: object, **_kwargs: object) -> None:
        raise OSError("terminal fsync failed")

    monkeypatch.setattr(lifecycle, "append_terminal", _fail_terminal)

    async def _send(_wire: object) -> str:
        return "must-not-escape"

    with pytest.raises(OSError, match="terminal fsync failed"):
        await gate.dispatch_async(wire_request=_wire_request(gate), send=_send)
    with pytest.raises(ProviderAttemptDrainError) as drain_failure:
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )
    assert drain_failure.value.result.inflight_request_ids


@pytest.mark.asyncio
async def test_role_session_gate_uses_separate_ledger_and_cannot_drain_as_factory(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    role_lifecycle = StrictProviderAttemptLifecycleStore.for_role_session(
        workspace=str(tmp_path),
        role_session_id="role-session-1",
    )
    gate = FinalProviderAttemptGate.for_role_session(
        workspace=str(tmp_path),
        role_session_id="role-session-1",
        run_id="run-1",
        role="director",
        turn_id="turn-1",
        call_id="call-1",
        request_freeze_id="freeze-1",
        provider="openai",
        model="model-1",
        semantic_request={
            "messages": [{"role": "system", "content": "polaris.role_identity.v1:director"}],
            "tools": [],
            "tool_choice": "auto",
            "response_format": None,
            "semantic_options": {"temperature": 0.1},
        },
        lifecycle=role_lifecycle,
        snapshot_store=SimpleNamespace(
            persist_and_pin=lambda _attempt: SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)
        ),
        drain_coordinator=ProviderAttemptInFlightCoordinator.for_role_session("role-session-1"),
    )

    async def _send(_wire: object) -> str:
        return "role-result"

    assert await gate.dispatch_async(wire_request=_wire_request(gate), send=_send) == "role-result"
    role_facts = role_lifecycle.query_strict()
    assert [item["event_type"] for item in role_facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert all(item["payload"]["verification_scope"] == "role_session" for item in role_facts)
    assert all(item["payload"]["scope_id"] == "role-session-1" for item in role_facts)
    factory_lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="role-session-1",
    )
    assert factory_lifecycle.query_strict() == ()
    with pytest.raises(ProviderAttemptDrainError, match="scope mismatch"):
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="role-session-1",
            timeout_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_async_stream_terminal_waits_for_response_exit_and_full_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []
    original_start = lifecycle.append_start
    original_terminal = lifecycle.append_terminal

    def _append_start(*args: Any, **kwargs: Any) -> object:
        receipt = original_start(*args, **kwargs)
        events.append("start_ack")
        return receipt

    def _append_terminal(*args: Any, **kwargs: Any) -> object:
        receipt = original_terminal(*args, **kwargs)
        events.append("terminal_ack")
        return receipt

    monkeypatch.setattr(lifecycle, "append_start", _append_start)
    monkeypatch.setattr(lifecycle, "append_terminal", _append_terminal)

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            events.append("response_exit")

    def _open_stream(_wire: Mapping[str, Any]) -> _ResponseContext:
        return _ResponseContext()

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "first"
        yield "second"

    stream = gate.dispatch_stream_async(
        wire_request=_wire_request(gate),
        open_stream=_open_stream,
        consume=_consume,
    )
    assert await anext(stream) == "first"
    assert events == ["start_ack", "post_enter", "response_exit", "terminal_ack"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "completed"
    assert await anext(stream) == "second"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert events == ["start_ack", "post_enter", "response_exit", "terminal_ack"]


@pytest.mark.asyncio
async def test_async_stream_cleanup_timeout_fails_terminal_and_leaks_zero_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    cleanup_entered = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()

    monkeypatch.setattr(
        gate_module,
        "_STREAM_CONTEXT_CLEANUP_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            cleanup_entered.set()
            try:
                await release_cleanup.wait()
            except asyncio.CancelledError:
                cleanup_cancelled.set()
                raise

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "must-not-leak"

    async def _collect() -> list[str]:
        return [
            item
            async for item in gate.dispatch_stream_async(
                wire_request=_wire_request(gate),
                open_stream=lambda _wire: _ResponseContext(),
                consume=_consume,
            )
        ]

    task = asyncio.create_task(_collect())
    await asyncio.wait_for(cleanup_entered.wait(), timeout=1)
    done, _pending = await asyncio.wait({task}, timeout=0.5)
    if task not in done:
        release_cleanup.set()
        assert await task == ["must-not-leak"]
        pytest.fail("stream cleanup exceeded its terminalization deadline")

    with pytest.raises(RuntimeError, match="provider_stream_cleanup_timeout"):
        await task
    assert cleanup_cancelled.is_set()
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "failed"
    assert "provider_stream_cleanup_timeout" in terminal[0]["payload"]["error"]
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_async_stream_consumer_cancellation_exits_response_before_cancelled_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []
    original_terminal = lifecycle.append_terminal

    def _append_terminal(*args: Any, **kwargs: Any) -> object:
        receipt = original_terminal(*args, **kwargs)
        events.append("terminal_ack")
        return receipt

    monkeypatch.setattr(lifecycle, "append_terminal", _append_terminal)

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            events.append("response_exit")

    def _open_stream(_wire: Mapping[str, Any]) -> _ResponseContext:
        return _ResponseContext()

    first_item_buffered = asyncio.Event()

    async def _consume(_response: object) -> AsyncIterator[str]:
        first_item_buffered.set()
        yield "first"
        await asyncio.Event().wait()

    stream = gate.dispatch_stream_async(
        wire_request=_wire_request(gate),
        open_stream=_open_stream,
        consume=_consume,
    )
    assert isinstance(stream, AsyncGenerator)
    first_item = asyncio.create_task(anext(stream))
    await asyncio.wait_for(first_item_buffered.wait(), timeout=1)
    first_item.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_item
    await stream.aclose()

    assert events == ["post_enter", "response_exit", "terminal_ack"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_async_stream_terminal_persistence_failure_rejects_normal_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)

    def _fail_terminal(*_args: object, **_kwargs: object) -> None:
        raise OSError("stream terminal fsync failed")

    monkeypatch.setattr(lifecycle, "append_terminal", _fail_terminal)

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "must-not-produce-success-verdict"

    escaped: list[str] = []
    with pytest.raises(OSError, match="stream terminal fsync failed"):
        async for item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            escaped.append(item)
    assert escaped == []
    with pytest.raises(ProviderAttemptDrainError):
        await gate.drain_coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_async_stream_buffer_limit_fails_terminal_and_leaks_zero_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    monkeypatch.setattr(gate_module, "_MAX_BUFFERED_STREAM_EVENTS", 2)
    monkeypatch.setattr(gate_module, "_MAX_BUFFERED_STREAM_EVENTS_HARD", 2)
    monkeypatch.setattr(gate_module, "_MAX_BUFFERED_STREAM_BYTES", 1024)
    response_exited = False

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            nonlocal response_exited
            response_exited = True

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "one"
        yield "two"
        yield "three"

    escaped: list[str] = []
    with pytest.raises(RuntimeError, match="factory_provider_stream_buffer_limit_exceeded"):
        async for item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            escaped.append(item)

    assert escaped == []
    assert response_exited is True
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "failed"
    assert "factory_provider_stream_buffer_limit_exceeded" in terminal[0]["payload"]["error"]


@pytest.mark.asyncio
async def test_async_stream_event_budget_scales_with_qualified_physical_output_budget(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(
        tmp_path,
        semantic_options={"temperature": 0.1, "max_tokens": 16_384},
        stream=True,
    )
    wire_request = _wire_request(gate)
    assert wire_request["body"]["max_tokens"] == 16_384
    response_exited = False

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            nonlocal response_exited
            response_exited = True

    async def _consume(_response: object) -> AsyncIterator[dict[str, int]]:
        for index in range(4_097):
            yield {"delta": index}

    escaped: list[dict[str, int]] = []
    async for item in gate.dispatch_stream_async(
        wire_request=wire_request,
        open_stream=lambda _wire: _ResponseContext(),
        consume=_consume,
    ):
        escaped.append(item)

    assert len(escaped) == 4_097
    assert response_exited is True
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "completed"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, 4_096),
        ({"max_tokens": 128}, 4_096),
        ({"max_output_tokens": 2_000}, 8_000),
        ({"max_completion_tokens": 4_000}, 16_000),
        ({"max_tokens": True}, 4_096),
        ({"max_tokens": 1_000_000}, 65_536),
    ],
)
def test_qualified_stream_event_limit_is_protocol_aware_and_hard_bounded(
    body: dict[str, object],
    expected: int,
) -> None:
    assert gate_module._qualified_stream_event_limit({"body": body}) == expected


@pytest.mark.asyncio
async def test_async_stream_byte_limit_fails_terminal_and_leaks_zero_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    monkeypatch.setattr(gate_module, "_MAX_BUFFERED_STREAM_EVENTS", 100)
    monkeypatch.setattr(gate_module, "_MAX_BUFFERED_STREAM_BYTES", 10)
    response_exited = False

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            nonlocal response_exited
            response_exited = True

    async def _consume(_response: object) -> AsyncIterator[str]:
        yield "1234"  # canonical JSON size: 6 bytes
        yield "5678"  # cumulative 12 bytes -> fail before append

    escaped: list[str] = []
    with pytest.raises(RuntimeError, match="factory_provider_stream_buffer_limit_exceeded"):
        async for item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            escaped.append(item)

    assert escaped == []
    assert response_exited is True
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "failed"
    assert "factory_provider_stream_buffer_limit_exceeded" in terminal[0]["payload"]["error"]


@pytest.mark.asyncio
async def test_async_stream_opaque_item_fails_closed_without_string_coercion_or_leak(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    response_exited = False

    class _OpaqueLargeItem:
        def __init__(self) -> None:
            self.payload = "x" * (9 * 1024 * 1024)

        def __str__(self) -> str:
            return "tiny-string-must-not-bypass-canonical-bound"

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            nonlocal response_exited
            response_exited = True

    async def _consume(_response: object) -> AsyncIterator[object]:
        yield _OpaqueLargeItem()

    escaped: list[object] = []
    with pytest.raises(RuntimeError, match="factory_provider_stream_buffer_item_unserializable"):
        async for item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            escaped.append(item)

    assert escaped == []
    assert response_exited is True
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "failed"
    assert "factory_provider_stream_buffer_item_unserializable" in terminal[0]["payload"]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("non_finite_item", [float("nan"), float("inf"), float("-inf")])
async def test_async_stream_non_finite_json_number_fails_closed_without_leak(
    tmp_path: Path,
    non_finite_item: float,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    response_exited = False

    class _ResponseContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            nonlocal response_exited
            response_exited = True

    async def _consume(_response: object) -> AsyncIterator[float]:
        yield non_finite_item

    escaped: list[float] = []
    with pytest.raises(RuntimeError, match="factory_provider_stream_buffer_item_unserializable"):
        async for item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            escaped.append(item)

    assert escaped == []
    assert response_exited is True
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "failed"
    assert "factory_provider_stream_buffer_item_unserializable" in terminal[0]["payload"]["error"]


@pytest.mark.asyncio
async def test_real_async_helper_retries_each_post_with_exact_frozen_mutation_and_lifecycle_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    events: list[str] = []
    payload: dict[str, Any] = {}

    class _MutatingSnapshotStore:
        attempts: list[FrozenFinalProviderAttemptV1] = []

        def persist_and_pin(self, attempt: FrozenFinalProviderAttemptV1) -> object:
            self.attempts.append(attempt)
            if len(self.attempts) == 1:
                payload["max_tokens"] = 64
            return SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    snapshot_store = _MutatingSnapshotStore()
    gate, lifecycle = _gate(
        tmp_path,
        snapshot_store=snapshot_store,
        semantic_options={"temperature": 0.1, "max_tokens": 128},
        stream=True,
    )
    payload.update(_wire_body(gate))
    original_gate_factory = FinalProviderAttemptGate.for_factory_run.__func__

    def _gate_with_test_stores(cls: type[FinalProviderAttemptGate], **kwargs: Any) -> FinalProviderAttemptGate:
        kwargs["snapshot_store"] = snapshot_store
        kwargs["lifecycle"] = lifecycle
        return original_gate_factory(cls, **kwargs)

    monkeypatch.setattr(FinalProviderAttemptGate, "for_factory_run", classmethod(_gate_with_test_stores))
    original_start = lifecycle.append_start
    original_terminal = lifecycle.append_terminal

    def _append_start(*args: Any, **kwargs: Any) -> object:
        receipt = original_start(*args, **kwargs)
        events.append("start_ack")
        return receipt

    def _append_terminal(*args: Any, **kwargs: Any) -> object:
        receipt = original_terminal(*args, **kwargs)
        events.append("terminal_ack")
        return receipt

    monkeypatch.setattr(lifecycle, "append_start", _append_start)
    monkeypatch.setattr(lifecycle, "append_terminal", _append_terminal)
    sessions = (
        _AsyncSession(_AsyncResponse(status=500), events),
        _AsyncSession(_AsyncResponse(status=503), events),
        _AsyncSession(_AsyncResponse(json_body={"ok": True}), events),
    )
    pending_sessions = iter(sessions)

    async def _create_session(_old: object) -> _AsyncSession:
        return next(pending_sessions)

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        _create_session,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.asyncio.sleep",
        AsyncMock(),
    )

    with pytest.raises(
        qualification_module.FinalProviderAttemptQualificationError,
        match="physical_wire_max_tokens_drift",
    ):
        _ = [
            item
            async for item in invoke_stream_with_retry(
                "https://example.test/v1/chat/completions",
                {},
                payload,
                5,
                max_attempts=3,
                retry_delay_seconds=0,
                governance_mode="governed_required",
                physical_dispatch_port=gate._test_dispatch_port,  # type: ignore[attr-defined]
            )
        ]

    attempts = snapshot_store.attempts
    assert len(attempts) == 1
    assert [attempt.dispatch_copy()["body"]["max_tokens"] for attempt in attempts] == [128]
    assert [attempt.durable_copy()["physical_wire"]["body"]["max_tokens"] for attempt in attempts] == [128]
    assert [session.posts[0]["json"]["max_tokens"] for session in sessions[:1]] == [128]
    assert all(not session.posts for session in sessions[1:])
    assert events == [
        "start_ack",
        "post_enter",
        "response_exit",
        "session_close",
        "terminal_ack",
    ]
    facts = lifecycle.query_strict()
    starts = tuple(item for item in facts if item["event_type"] == "provider_attempt.started")
    terminals = tuple(item for item in facts if item["event_type"] == "provider_attempt.terminal")
    assert len(starts) == len(terminals) == 1
    assert [item["payload"]["status"] for item in terminals] == ["failed"]
    assert [item["payload"]["provider_request_id"] for item in starts] == [
        item["payload"]["provider_request_id"] for item in terminals
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["pin", "start"])
async def test_real_async_helper_pin_or_start_failure_keeps_post_count_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    _bootstrap(tmp_path)

    class _SnapshotStore:
        def persist_and_pin(self, _attempt: object) -> object:
            if failure_phase == "pin":
                raise OSError("pin fsync failed")
            return SimpleNamespace(context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    gate, lifecycle = _gate(
        tmp_path,
        snapshot_store=_SnapshotStore(),
        stream=True,
    )
    if failure_phase == "start":
        monkeypatch.setattr(
            lifecycle,
            "append_start",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("start fsync failed")),
        )
    create_session_calls = 0

    async def _create_session(_old: object) -> _AsyncSession:
        nonlocal create_session_calls
        create_session_calls += 1
        return _AsyncSession(_AsyncResponse(json_body={"ok": True}), [])

    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        _create_session,
    )
    stream = invoke_stream_with_retry(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        5,
        max_attempts=1,
        governance_mode="governed_required",
        physical_dispatch_port=gate,
    )

    with pytest.raises(OSError, match=f"{failure_phase} fsync failed"):
        await anext(stream)
    assert create_session_calls == 0
    assert lifecycle.query_strict() == ()
    if failure_phase == "start":
        state = gate._physical_attempt_control_port.budget_state("f" * 64)
        assert state.aborted_count == 1
        assert state.ambiguous_count == 0
        assert gate.drain_coordinator.snapshot().settled is True


def test_start_append_with_durable_fact_but_lost_ack_is_ambiguous_and_never_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    original_start = lifecycle.append_start
    sends = 0

    def _persist_then_lose_ack(*args: Any, **kwargs: Any) -> object:
        original_start(*args, **kwargs)
        raise OSError("start durability ack lost")

    def _send(_wire: Mapping[str, Any]) -> str:
        nonlocal sends
        sends += 1
        return "forbidden"

    monkeypatch.setattr(lifecycle, "append_start", _persist_then_lose_ack)
    with pytest.raises(OSError, match="start durability ack lost"):
        gate.dispatch_sync(wire_request=_wire_request(gate), send=_send)

    state = gate._physical_attempt_control_port.budget_state("f" * 64)
    assert sends == 0
    assert state.aborted_count == 0
    assert state.ambiguous_count == 1
    assert gate.drain_coordinator.snapshot().settled is False
    assert [fact["event_type"] for fact in lifecycle.query_strict()] == ["provider_attempt.started"]


@pytest.mark.asyncio
async def test_real_async_helper_second_cancellation_closes_session_before_terminal_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(
        tmp_path,
        stream=True,
    )
    events: list[str] = []
    session = _AsyncSession(_AsyncResponse(), events)
    entered_handler = asyncio.Event()
    never = asyncio.Event()
    terminal_entered = threading.Event()
    release_terminal = threading.Event()
    terminal_calls = 0
    original_terminal = lifecycle.append_terminal

    async def _create_session(_old: object) -> _AsyncSession:
        return session

    def _blocking_terminal(*args: Any, **kwargs: Any) -> object:
        nonlocal terminal_calls
        terminal_calls += 1
        events.append("terminal_entered")
        terminal_entered.set()
        assert release_terminal.wait(timeout=2)
        events.append("terminal_ack")
        return original_terminal(*args, **kwargs)

    async def _handler(_response: object) -> AsyncGenerator[str, None]:
        entered_handler.set()
        await never.wait()
        yield "unreachable"

    monkeypatch.setattr(lifecycle, "append_terminal", _blocking_terminal)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        _create_session,
    )
    stream = invoke_stream_with_retry_and_handler(
        "https://example.test/v1/chat/completions",
        {},
        _wire_body(gate),
        5,
        _handler,  # type: ignore[arg-type]
        max_attempts=1,
        governance_mode="governed_required",
        physical_dispatch_port=gate,
    )
    task = asyncio.create_task(anext(stream))
    await entered_handler.wait()
    task.cancel()
    assert await asyncio.to_thread(terminal_entered.wait, 1)
    assert events[:3] == ["post_enter", "response_exit", "session_close"]
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_terminal.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.closed is True
    assert events == ["post_enter", "response_exit", "session_close", "terminal_entered", "terminal_ack"]
    assert terminal_calls == 1
    assert gate.drain_coordinator.inflight_request_ids == ()


@pytest.mark.asyncio
async def test_async_stream_sync_open_failure_records_failed_terminal_before_escape(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    outbound_calls = 0

    def _open_stream(_wire: Mapping[str, Any]) -> _AsyncPostContext:
        nonlocal outbound_calls
        assert outbound_calls == 0
        raise RuntimeError("open stream construction failed")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"

    with pytest.raises(RuntimeError, match="open stream construction failed"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=_open_stream,
            consume=_consume,
        ):
            pass

    assert outbound_calls == 0
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]
    assert facts[-1]["payload"]["status"] == "failed"
    assert "open stream construction failed" in facts[-1]["payload"]["error"]


@pytest.mark.asyncio
async def test_async_stream_consume_keyboard_interrupt_exits_response_and_records_cancelled(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(self, *_args: object) -> None:
            events.append("response_exit")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"
        raise KeyboardInterrupt("consume interrupted")

    with pytest.raises(KeyboardInterrupt, match="consume interrupted"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            pass

    assert events == ["post_enter", "response_exit"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert "KeyboardInterrupt: consume interrupted" in terminal[0]["payload"]["error"]
