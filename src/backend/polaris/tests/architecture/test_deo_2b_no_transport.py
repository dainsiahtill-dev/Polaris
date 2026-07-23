"""Transport and process fences for the DEO-2B execution capability."""

from __future__ import annotations

import json
import multiprocessing
import os
import pickle
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.public import (
    DirectedEffectExecutionContextV1,
)
from polaris.cells.roles.kernel.public.directed_effect_service import (
    create_directed_effect_fence_ports,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_dispatch_fence import (
    _context,
)
from polaris.cells.runtime.task_runtime.public import DirectedEffectClaimGrantV1

BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_KEYS = frozenset(
    {
        "claim_grant",
        "directed_effect_context",
        "directed_effect_execution_context",
        "directed_effect_grant",
        "execution_context",
    }
)


def _transport_violations(value: object, *, path: str = "root") -> list[str]:
    violations: list[str] = []
    if isinstance(value, (DirectedEffectExecutionContextV1, DirectedEffectClaimGrantV1)):
        violations.append(f"{path}:{type(value).__name__}")
        return violations
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                violations.append(f"{path}.{key}:forbidden_key")
            violations.extend(_transport_violations(item, path=f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            violations.extend(_transport_violations(item, path=f"{path}[{index}]"))
    return violations


def test_context_and_grant_never_cross_json_pickle_queue_ipc_or_public_transport() -> None:
    context = _context()

    with pytest.raises(TypeError, match="DirectedEffectExecutionContextV1 is not serializable"):
        pickle.dumps(context)
    with pytest.raises(TypeError, match="DirectedEffectExecutionContextV1 is not serializable"):
        ForkingPickler.dumps(context)
    with pytest.raises(TypeError):
        json.dumps(context)

    queue: Any = multiprocessing.SimpleQueue()
    try:
        with pytest.raises(TypeError, match="DirectedEffectExecutionContextV1 is not serializable"):
            queue.put(context)
    finally:
        queue.close()

    receiver, sender = multiprocessing.Pipe(duplex=False)
    try:
        with pytest.raises(TypeError, match="DirectedEffectExecutionContextV1 is not serializable"):
            sender.send(context)
    finally:
        sender.close()
        receiver.close()

    public_payloads: tuple[dict[str, Any], ...] = (
        {
            "messages": [{"role": "user", "content": "apply the authorized change"}],
            "tools": [{"name": "write_file", "parameters": {"type": "object"}}],
            "arguments": {"path": "src/app.py"},
        },
        {"command": "execute_role_session", "workspace": "/tmp/project"},
        {"metadata": {"run_id": "run-1", "task_id": "task-1"}},
        {"event": {"kind": "tool.lifecycle", "status": "completed"}},
        {"result": {"ok": True, "payload": {"changed": True}}},
        {"receipt": {"receipt_id": "receipt-1", "effect_hash": "a" * 64}},
        {"projection": {"status": "completed", "evidence_ref": "evidence-1"}},
    )
    assert all(_transport_violations(payload) == [] for payload in public_payloads)

    probes = (
        {"payload": context},
        {"payload": context.claim_grant},
        {"directed-effect-context": "context-1"},
        {"claim_grant": {"grant_hash": context.claim_grant.grant_hash}},
    )
    assert all(_transport_violations(probe) for probe in probes)

    approved_modules = (
        "polaris/cells/roles/kernel/internal/directed_effect_dispatch.py",
        "polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py",
        "polaris/cells/roles/kernel/internal/tool_batch_runtime.py",
        "polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py",
    )
    forbidden_conversions = ("asdict(", "claim_grant.to_record(", "context.to_record(")
    offenders: list[str] = []
    for relative in approved_modules:
        source = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
        offenders.extend(f"{relative}:{token}" for token in forbidden_conversions if token in source)
    assert offenders == []


@pytest.mark.filterwarnings("ignore:This process.*is multi-threaded.*:DeprecationWarning")
def test_fork_child_cannot_consume_or_create_sentinel(tmp_path: Path) -> None:
    if not hasattr(os, "fork"):
        pytest.skip("requires os.fork")

    context = _context()
    ports = create_directed_effect_fence_ports()
    assert ports.admin.register(context).ok
    sentinel = tmp_path / "forbidden-child-effect"
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            result = ports.consume.consume(context)
            if result.ok:
                sentinel.write_text("effect", encoding="utf-8")
            os.write(write_fd, str(result.error_code or "").encode("utf-8"))
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    try:
        child_error = os.read(read_fd, 256).decode("utf-8")
    finally:
        os.close(read_fd)
    _, status = os.waitpid(child_pid, 0)

    assert os.waitstatus_to_exitcode(status) == 0
    assert child_error == "deo_fence_pid_mismatch"
    assert not sentinel.exists()
    assert ports.consume.consume(context).ok
