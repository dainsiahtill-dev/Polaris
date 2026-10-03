"""Exact pre-split physical-attempt gate fixtures; test-only shared ownership."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.llm_caller import (
    final_provider_attempt_qualification as qualification_module,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_gate import (
    DurableFinalProviderAttemptSnapshotStore,
    FinalProviderAttemptGate,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as request_fact_test
from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import (
    FactoryPhysicalAttemptTestControlPort as FactoryPhysicalAttemptLiveControlPort,
)
from polaris.kernelone.llm.engine.executor import AIExecutor


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


class _AsyncStreamContent:
    def __init__(self, chunks: tuple[bytes | str, ...]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes | str]:
        for chunk in self._chunks:
            yield chunk


class _AsyncResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        chunks: tuple[bytes | str, ...] = (),
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.ok = status < 400
        self.headers = {"Content-Type": "application/json" if json_body is not None else "text/event-stream"}
        self.content = _AsyncStreamContent(chunks)
        self._json_body = json_body

    async def text(self) -> str:
        return f"HTTP {self.status}"

    async def json(self) -> dict[str, Any]:
        assert self._json_body is not None
        return dict(self._json_body)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise ClientResponseError(str(self.status))


class _AsyncPostContext:
    def __init__(self, response: _AsyncResponse, events: list[str]) -> None:
        self._response = response
        self._events = events

    async def __aenter__(self) -> _AsyncResponse:
        self._events.append("post_enter")
        return self._response

    async def __aexit__(self, *_args: object) -> None:
        self._events.append("response_exit")


class _AsyncSession:
    def __init__(self, response: _AsyncResponse, events: list[str]) -> None:
        self._response = response
        self._events = events
        self.closed = False
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _AsyncPostContext:
        self.posts.append({"url": url, **kwargs})
        return _AsyncPostContext(self._response, self._events)

    async def close(self) -> None:
        self.closed = True
        self._events.append("session_close")


def _bootstrap(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="final_provider_attempt_test",
        )
    )


def _run_fixture_coroutine(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run async request preparation without nesting the caller's event loop."""

    result: list[Any] = []
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except Exception as exc:  # noqa: BLE001 - test helper must preserve the original failure
            errors.append(exc)

    worker = threading.Thread(target=_target, daemon=False)
    worker.start()
    worker.join()
    if errors:
        raise errors[0]
    assert len(result) == 1
    return result[0]


def _wire_body_from_semantic(
    *,
    semantic_request: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    options = semantic_request["semantic_options"]
    assert isinstance(options, Mapping)
    body = {
        "model": model,
        "messages": semantic_request["messages"],
        "tools": semantic_request["tools"],
        "tool_choice": semantic_request["tool_choice"],
        "response_format": semantic_request["response_format"],
        "temperature": options["temperature"],
    }
    if "max_tokens" in options:
        body["max_tokens"] = options["max_tokens"]
    return body


def _qualified_factory_fixture(
    *,
    workspace: Path,
    physical_attempt_control_port: FactoryPhysicalAttemptLiveControlPort,
    semantic_options: Mapping[str, Any] | None = None,
    stream: bool = False,
    provider_type: str = "openai_compat",
    provider_config: Mapping[str, Any] | None = None,
    wire_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    role: str = "director",
) -> tuple[
    qualification_module._FinalProviderAttemptQualificationProofV1,
    Mapping[str, Any],
    dict[str, Any],
    Any,
    Any,
]:
    """Build a real frozen request, snapshot, audit, route, wire, and proof."""

    options = dict(semantic_options or {"temperature": 0.1, "max_tokens": 128})
    options.setdefault("max_tokens", 128)
    _, _, _, prepared = _run_fixture_coroutine(
        request_fact_test._prepare_b33_factory_request(
            role,
            workspace=str(workspace),
            physical_attempt_control_port=physical_attempt_control_port,
            execution_authority_hash="f" * 64,
            attempt_budget=32,
            tool_choice="auto",
            temperature=float(options["temperature"]),
            max_tokens=int(options["max_tokens"]),
            stream=stream,
        )
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=payload["messages"],
        trace_id=frozen.identity.run_id,
        call_id=frozen.identity.call_id,
        provider_request=request_fact_test._b35_provider_request_snapshot(frozen=frozen),
    )
    audit = request_fact_test._b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref)
    dispatch_port.qualify(
        final_request_context_audit=audit,
        context_snapshot_ref=context_snapshot_ref,
    )
    dispatch_port.bind_provider_route_authority(
        provider_id=str(payload["provider_id"]),
        provider_type=provider_type,
        model=str(payload["model"]),
        mode="stream" if payload["stream"] else "invoke",
        provider_config=dict(provider_config or {"base_url": "https://example.test"}),
    )
    semantic_request = qualification_module.final_gate_semantic_request(frozen)
    route = dispatch_port._physical_route_authority
    assert isinstance(route, dict)
    body = dict(route["expected_body"])
    wire = (
        wire_factory(body)
        if wire_factory is not None
        else {
            "endpoint": route["exact_endpoint"],
            "headers": {},
            "body": body,
            "transport": (
                {"kind": route["exact_transport_kind"], "timeout_seconds": 5}
                if stream
                else {"kind": route["exact_transport_kind"], "timeout": 1}
            ),
        }
    )
    transport = wire.get("transport")
    assert isinstance(transport, Mapping)
    endpoint = str(wire.get("endpoint") or "")
    assert endpoint.startswith("https://example.test/")
    final_context_ref, final_audit = dispatch_port._persist_final_physical_request_context(
        wire_request=wire,
    )
    proof = qualification_module._mint_final_provider_attempt_qualification_proof(
        workspace=str(workspace),
        frozen=frozen,
        binding=dispatch_port._binding,
        final_request_context_audit=final_audit,
        context_snapshot_ref=final_context_ref,
        wire_request=wire,
        physical_route_authority=route,
    )
    return proof, semantic_request, wire, frozen, dispatch_port


def _prepared_with_dispatch_port(*, frozen: Any, dispatch_port: Any) -> PreparedLLMRequest:
    payload = json.loads(frozen.canonical_final_payload_json)
    return PreparedLLMRequest(
        messages=payload["messages"],
        input_text="",
        context_result=SimpleNamespace(
            token_estimate=1,
            compression_applied=False,
            compression_strategy=None,
        ),
        context_summary="",
        request_options={},
        ai_request=SimpleNamespace(
            context={"context_snapshot_ref": "b" * 24},
            provider_id=payload["provider_id"],
            model=payload["model"],
            options={},
        ),
        context_os_audit={},
        factory_semantic_request=frozen,
        factory_dispatch_port=dispatch_port,
    )


def _failed_coverage_audit(dispatch_port: Any, *, case: str = "missing_refs") -> dict[str, Any]:
    audit = copy.deepcopy(dispatch_port._qualified_audit)
    coverage = audit["final_request_evidence_coverage"]
    coverage["pass"] = False
    if case == "missing_refs":
        coverage["missing_required_refs"] = ["pm_contract_ref"]
    elif case == "missing_tools":
        coverage["missing_required_tools"] = ["write_file"]
    elif case == "wrong_role":
        coverage["role_identity_ok"] = False
        coverage["role_id"] = "pm"
    else:
        raise AssertionError(f"unsupported coverage failure case: {case}")
    return audit


def _assert_one_coverage_rejection_and_zero_physical_effects(
    *,
    workspace: Path,
    gate: FinalProviderAttemptGate,
    lifecycle: StrictProviderAttemptLifecycleStore,
    rejection_code: str = "final_request_evidence_coverage_failed",
) -> None:
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream=qualification_module.qualification_rejection_stream("factory-run-1"),
            limit=10,
        )
    )
    assert events.total == 1
    assert events.events[0]["payload"]["rejection_code"] == rejection_code
    state = gate._physical_attempt_control_port.budget_state("f" * 64)
    assert state.reserved_count == 0
    assert state.committed_count == 0
    assert state.terminal_count == 0
    assert state.consumed_attempts == 0
    assert state.remaining_attempts == 32
    assert state.settled is True
    assert lifecycle.query_strict() == ()
    assert gate._test_dispatch_port.final_context_evidence() is None


def _gate(
    workspace: Path,
    *,
    snapshot_store: object | None = None,
    semantic_options: dict[str, Any] | None = None,
    stream: bool = False,
    wire_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[FinalProviderAttemptGate, StrictProviderAttemptLifecycleStore]:
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    )
    physical_attempt_control_port = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    physical_attempt_control_port.register_grant(
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
    qualification_proof, semantic_request, qualified_wire, frozen, dispatch_port = _qualified_factory_fixture(
        workspace=workspace,
        physical_attempt_control_port=physical_attempt_control_port,
        semantic_options=semantic_options,
        stream=stream,
        wire_factory=wire_factory,
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    gate = FinalProviderAttemptGate(
        workspace=str(workspace),
        verification_scope="factory",
        factory_run_id="factory-run-1",
        run_id=frozen.identity.run_id,
        role="director",
        turn_id=frozen.identity.turn_id,
        call_id=frozen.identity.call_id,
        request_freeze_id=frozen.identity.request_freeze_id,
        provider=str(payload["provider_id"]),
        model=str(payload["model"]),
        semantic_request=semantic_request,
        physical_attempt_control_port=physical_attempt_control_port,
        execution_authority_hash="f" * 64,
        attempt_budget=32,
        lifecycle=lifecycle,
        snapshot_store=snapshot_store or DurableFinalProviderAttemptSnapshotStore(str(workspace)),
        qualification_proof=qualification_proof,
    )
    gate._test_qualified_wire = qualified_wire  # type: ignore[attr-defined]
    gate._test_dispatch_port = dispatch_port  # type: ignore[attr-defined]
    return gate, lifecycle


def _wire_request(gate: FinalProviderAttemptGate) -> dict[str, Any]:
    qualified = getattr(gate, "_test_qualified_wire", None)
    if qualified is not None:
        return json.loads(json.dumps(qualified))
    return {
        "endpoint": "https://example.test/v1/chat/completions",
        "headers": {},
        "body": _wire_body(gate),
        "transport": {"kind": "aiohttp.ClientSession.post", "timeout": 1},
    }


def _wire_body(gate: FinalProviderAttemptGate) -> dict[str, Any]:
    qualified_wire = getattr(gate, "_test_qualified_wire", None)
    if isinstance(qualified_wire, dict) and isinstance(qualified_wire.get("body"), dict):
        return copy.deepcopy(qualified_wire["body"])
    return _wire_body_from_semantic(
        semantic_request=gate._semantic_request,
        model=gate._model,
    )
