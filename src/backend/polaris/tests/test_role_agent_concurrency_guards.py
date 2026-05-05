"""Role agent concurrency guard tests.

These tests verify thread-safety properties of the role agent runtime.
The imports have been migrated from core.polaris_loop.role_agent to
polaris.cells.roles.runtime.public.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

_runtime_public_available = False
with contextlib.suppress(ValueError, ModuleNotFoundError):
    _runtime_public_available = importlib.util.find_spec("polaris.cells.roles.runtime.public") is not None

if not _runtime_public_available:
    pytest.skip("Module not available: polaris.cells.roles.runtime.public", allow_module_level=True)

from polaris.cells.roles.runtime.public import (
    AgentMessage,
    AgentStatus,
    RoleAgent,
)
from polaris.cells.roles.runtime.public.service import ProtocolFSM, ProtocolType

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)


class _ReentrantAgent(RoleAgent):
    def setup_toolbox(self) -> None:
        tb = self.toolbox
        if not tb.has_tool("ping"):
            tb.register("ping", lambda: "pong")

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        return None

    def run_cycle(self) -> bool:
        time.sleep(0.01)
        return False


def test_role_agent_start_survives_reentrant_status_callback(tmp_path: Path) -> None:
    agent = _ReentrantAgent(str(tmp_path), "reentrant-agent")

    def _status_callback(_status: AgentStatus) -> None:
        _ = agent.storage
        _ = agent.toolbox

    agent.register_callback("on_status_change", _status_callback)
    initializer = threading.Thread(target=agent.initialize, daemon=True)
    initializer.start()
    initializer.join(timeout=2.0)
    assert not initializer.is_alive()

    starter = threading.Thread(target=agent.start, daemon=True)
    starter.start()
    starter.join(timeout=2.0)

    try:
        assert not starter.is_alive()
        assert agent.toolbox.has_tool("ping")
    finally:
        agent.stop(timeout=1.0)


def test_protocol_approve_does_not_require_reentrant_lock() -> None:
    fsm = ProtocolFSM()
    # Regression guard: approve/reject path should not nest-lock cleanup.
    fsm._lock = threading.Lock()  # type: ignore[assignment]
    request_id = fsm.create_request(
        protocol_type=ProtocolType.PLAN_APPROVAL,
        from_role="PM",
        to_role="QA",
        content={"task_id": "T-001"},
    )

    result: dict[str, bool] = {}
    completed = threading.Event()

    def _approve() -> None:
        result["ok"] = fsm.approve(request_id, approver="QA")
        completed.set()

    worker = threading.Thread(target=_approve, daemon=True)
    worker.start()

    assert completed.wait(timeout=2.0)
    assert result.get("ok") is True
