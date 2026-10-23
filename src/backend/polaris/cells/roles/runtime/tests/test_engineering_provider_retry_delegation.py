"""Trusted provider 5xx delegation; real native client and finite loopback HTTP.

These are synthetic component fixtures, never historical/native delivery evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter, deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import uuid4

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import RunLimits
from polaris.cells.roles.runtime.public.engineering_service import _native_client
from polaris.kernelone.errors import ErrorCategory
from polaris.kernelone.fs import get_default_adapter, guarded_create_directory
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor

_ROOT = Path(__file__).resolve().parents[7]
_AUDIT = _ROOT / ".polaris/audits/provider-529-recovery-20261024/implementation"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@pytest.fixture
def isolated_workspace() -> Path:
    relative = "fixtures/" + uuid4().hex
    guarded_create_directory(str(_AUDIT), relative, parents=True, exist_ok=True)
    return _AUDIT / relative


@dataclass
class _LocalProvider:
    base_url: str = ""
    responses: deque[Any] = field(default_factory=deque)
    received: list[dict[str, Any]] = field(default_factory=list)
    observed: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, _request: AIRequest, observation: Mapping[str, Any]) -> None:
        self.observed.append(dict(observation))


@pytest.fixture
def local_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[_LocalProvider]:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.delenv("KERNELONE_PLATFORM_RETRY_MAX", raising=False)
    local = _LocalProvider()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            scripted = local.responses.popleft() if local.responses else 599
            status = scripted[0] if isinstance(scripted, tuple) else scripted
            local.received.append({"body": json.loads(body.decode("utf-8")), "status": status})
            payload = (
                {
                    "id": "synthetic",
                    "type": "message",
                    "role": "assistant",
                    "model": "synthetic-model",
                    "content": [{"type": "text", "text": "LOCAL_OK"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
                if status == 200
                else {"type": "error", "error": {"type": "overloaded_error", "message": f"synthetic HTTP {status}"}}
            )
            if isinstance(scripted, tuple):
                payload = scripted[1]
            reply = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            if status == 429:
                self.send_header("Retry-After", "0.05")
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    local.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {
        "type": "anthropic_compat",
        "base_url": local.base_url,
        "api_path": "/v1/messages",
        "api_key": "synthetic-loopback-only",
        "timeout": 360,
        "retries": 2,
        "max_context_tokens": 1048576,
        "max_output_tokens": 524288,
        "supports_tools": True,
    }
    monkeypatch.setattr(AIExecutor, "_get_provider_config", lambda _self, _provider: dict(config))
    try:
        yield local
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def _invoke(
    local: _LocalProvider,
    workspace: Path,
    sequence: list[int],
    *,
    max_retries: int | None = None,
    deadline_seconds: float = 60,
) -> Any:
    local.responses.extend(sequence)
    # A new, explicit synthetic deadline; never rebase the frozen historical one.
    limits = RunLimits(
        max_output_bytes=33554432, max_tool_output_bytes=8388608, deadline=time.time() + deadline_seconds
    )
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="synthetic",
        model="synthetic-model",
        input="Synthetic provider delegation fixture",
        options={"max_tokens": 524288},
        context={
            "workspace": str(workspace),
            "session_id": uuid4().hex,
            "call_id": uuid4().hex,
            "turn_seq": 1,
            "engine_version": "engineering_v2",
        },
    )
    if max_retries is not None:
        request.options["max_retries"] = max_retries
    response = await _native_client(workspace, limits, native_request_observer=local.observe).invoke(request)
    # Deadline cancellation may finish the async caller before the physical
    # worker publishes its terminal snapshot. Observe bounded settlement only.
    async with asyncio.timeout(2):
        while sum(event["phase"] == "terminal" for event in local.observed) < len(local.received):
            await asyncio.sleep(0.005)
    fs = get_default_adapter()
    for observation in local.observed:
        ref = observation["context_snapshot_ref"]
        snapshot = json.loads(
            fs.read_text(str(workspace / ".polaris/runtime/contexts" / ref[:2] / ref), encoding="utf-8")
        )
        local.snapshots.append(snapshot["provider_request"])
    report = {
        "synthetic_component_only": True,
        "sequence": sequence,
        "deadline": limits.deadline,
        "request_options": request.options,
        "actual_loopback_POST_count": len(local.received),
        "response_ok": response.ok,
        "error": response.error,
        "received": local.received,
        "observed": local.observed,
        "provider_requests": local.snapshots,
    }
    fs.write_text(
        str(workspace / "result.json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    received = Counter(_digest(row["body"]) for row in local.received)
    for phase in ("prepared", "terminal"):
        matches = [
            (event, snapshot)
            for event, snapshot in zip(local.observed, local.snapshots, strict=True)
            if event["phase"] == phase
        ]
        assert Counter(_digest(snapshot["final_physical_request"]["body"]) for _, snapshot in matches) == received
    keys = [
        (event["capture_id"], event["provider_request_id"], event["dispatch_attempt_number"])
        for event in local.observed
        if event["phase"] == "prepared"
    ]
    assert len(keys) == len(set(keys)) == len(local.received)
    terminal_keys = [
        (event["capture_id"], event["provider_request_id"], event["dispatch_attempt_number"])
        for event in local.observed
        if event["phase"] == "terminal"
    ]
    assert Counter(keys) == Counter(terminal_keys)
    assert all(
        snapshot["final_request_context_audit"]["context_window_tokens"] == 1048576 for snapshot in local.snapshots
    )
    assert len({_digest(row["body"]) for row in local.received}) == min(1, len(local.received))
    assert all(row["body"]["max_tokens"] == 524288 for row in local.received)
    return response


async def test_native_529_then_200_recovers_with_original_caller_budget(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    response = await _invoke(local_provider, isolated_workspace, [529, 200])
    assert response.ok, response.error
    assert len(local_provider.received) == 2


async def test_persistent_529_stops_at_two_caller_attempts(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    response = await _invoke(local_provider, isolated_workspace, [529, 529, 200])
    assert not response.ok
    assert len(local_provider.received) == 2
    assert list(local_provider.responses) == [200]


@pytest.mark.parametrize("status", [200, 401, 402])
async def test_non_transient_controls_send_once(
    status: int, local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    response = await _invoke(local_provider, isolated_workspace, [status, 200])
    assert response.ok is (status == 200)
    assert len(local_provider.received) == 1
    assert list(local_provider.responses) == [200]


async def test_explicit_zero_retry_stays_single_dispatch(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    response = await _invoke(local_provider, isolated_workspace, [529, 200], max_retries=0)
    assert not response.ok
    assert len(local_provider.received) == 1


async def test_effective_request_policy_is_persisted_without_changing_body(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    response = await _invoke(local_provider, isolated_workspace, [200])
    assert response.ok
    for snapshot in local_provider.snapshots:
        policy = snapshot["effective_caller_policy"]
        assert policy["owner"] == "AIExecutor.request"
        assert policy["max_attempts"] == 2
        assert policy["transport_only"] is True
        assert ErrorCategory.SERVICE_UNAVAILABLE.value in policy["retryable_categories"]
        assert "effective_caller_policy" not in snapshot["final_physical_request"]["body"]


async def test_429_keeps_retry_after_and_recovers_locally(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    started = time.monotonic()
    response = await _invoke(local_provider, isolated_workspace, [429, 200])
    assert response.ok
    assert len(local_provider.received) == 2
    assert time.monotonic() - started >= 0.05


async def test_semantic_http200_failure_is_not_delegated(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    local_provider.responses.append(
        (200, {"content": [{"type": "thinking", "thinking": "not finished"}], "stop_reason": "max_tokens"})
    )
    response = await _invoke(local_provider, isolated_workspace, [200])
    assert not response.ok
    assert len(local_provider.received) == 1
    assert list(local_provider.responses) == [200]


async def test_open_shared_circuit_blocks_second_dispatch(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    from polaris.infrastructure.llm.providers.provider_helpers import get_circuit_breaker

    breaker = get_circuit_breaker("invoke:" + local_provider.base_url + "/v1/messages", failure_threshold=1)
    response = await _invoke(local_provider, isolated_workspace, [529, 200])
    assert not response.ok
    assert len(local_provider.received) == 1
    assert breaker.snapshot()["state"] == "open"
    assert list(local_provider.responses) == [200]


async def test_original_synthetic_deadline_prevents_second_dispatch(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    response = await _invoke(local_provider, isolated_workspace, [529, 200], deadline_seconds=0.5)
    assert not response.ok
    # A busy host may expire before the first physical send. Both zero and one
    # are safe; a second dispatch would violate this absolute deadline.
    assert len(local_provider.received) <= 1
    assert local_provider.responses[-1] == 200


async def test_cancellation_during_backoff_never_sends_second_request(
    local_provider: _LocalProvider, isolated_workspace: Path
) -> None:
    task = asyncio.create_task(_invoke(local_provider, isolated_workspace, [529, 200]))
    async with asyncio.timeout(5):
        while not any(item["phase"] == "terminal" for item in local_provider.observed):
            await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert len(local_provider.received) == 1
    assert list(local_provider.responses) == [200]


async def test_engineering_recovered_model_request_does_not_replay_successful_write(
    local_provider: _LocalProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
    from polaris.kernelone.llm.engine import _executor_base

    monkeypatch.setattr(_executor_base, "get_role_model", lambda _role: ("synthetic", "synthetic-model"))
    local_provider.responses.extend(
        [
            (
                200,
                {
                    "id": "write-turn",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "write-once",
                            "name": "write_file",
                            "input": {"file": "effect.txt", "content": "written once"},
                        }
                    ],
                    "stop_reason": "tool_use",
                },
            ),
            529,
            200,
        ]
    )
    service = EngineeringRunService(isolated_workspace)
    command = RoleRunCommand(
        "provider-recovery-write-once", "director", "Write effect.txt once, then report the result.", isolated_workspace
    )
    scope = PermissionScope((isolated_workspace,), (isolated_workspace,), frozenset({"write_file"}), False)
    limits = RunLimits(time.time() + 15, 1000000, 64000)
    result = await service.run_role(command, scope=scope, limits=limits, cancel=asyncio.Event())
    assert result.status == "completed", result.text
    fs = get_default_adapter()
    assert fs.read_text(str(isolated_workspace / "effect.txt"), encoding="utf-8") == "written once"
    events = service.store.load_events(command.session_id)
    starts = [event["payload"] for event in events if event["kind"] == "tool_started"]
    observations = [event["payload"] for event in events if event["kind"] == "tool_observed"]
    assert len(starts) == len(observations) == 1
    assert observations[0]["call_id"] == "write-once"
    assert observations[0]["ok"] is True
    assert len(local_provider.received) == 3
    assert _digest(local_provider.received[1]["body"]) == _digest(local_provider.received[2]["body"])
    assert "write-once" in json.dumps(local_provider.received[2]["body"], ensure_ascii=False)
    fs.write_text(
        str(isolated_workspace / "engineering-effect-result.json"),
        json.dumps(
            {
                "synthetic_component_only": True,
                "status": result.status,
                "verification": result.verification,
                "POST_count": len(local_provider.received),
                "tool_starts": starts,
                "tool_observations": observations,
                "events": events,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
