"""Role tool projection keeps explicit emptiness through the actual K1 consumer.

Handlers are inert fixtures. RoleAgent projection, BackendToolRuntime construction,
and Toolbox registration are real; no tool, provider or lifecycle is executed.
"""

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy
from polaris.cells.roles.runtime.internal.agent_runtime_base import AgentMessage, RoleAgent, Toolbox
from polaris.kernelone.tool_execution.runtime_executor import BackendToolRuntime


class _ProbeAgent(RoleAgent):
    profile: RoleProfile | None = None

    def setup_toolbox(self) -> None:
        pass

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        return None

    def run_cycle(self) -> bool:
        return False


def _agent(root: Path, whitelist: list[str] | None) -> _ProbeAgent:
    agent = _ProbeAgent(str(root), "synthetic-allowlist", enable_context_compression=False)
    if whitelist is not None:
        agent.profile = RoleProfile(
            role_id="synthetic-allowlist",
            display_name="Synthetic allowlist",
            description="No provider",
            tool_policy=RoleToolPolicy(whitelist=whitelist),
        )
    return agent


def _registered_tools(agent: _ProbeAgent, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    calls: list[dict[str, Any]] = []

    def inert_handler(**arguments: Any) -> dict[str, bool]:
        calls.append(arguments)
        return {"ok": True}

    runtime = BackendToolRuntime(agent.workspace, allowed_tools=agent._backend_allowed_tools_from_profile())
    monkeypatch.setattr(runtime, "_handlers", {"read_file": inert_handler, "write_file": inert_handler})
    monkeypatch.setattr(agent, "_backend_tool_runtime", runtime)
    toolbox = Toolbox(agent.agent_name)
    agent._register_backend_tools(toolbox)
    assert calls == []
    return set(toolbox.list_tools())


@pytest.mark.parametrize(
    ("whitelist", "expected"),
    [(None, None), ([], frozenset()), (["read_file"], frozenset({"read_file"}))],
)
def test_role_allowlist_preserves_explicit_empty_and_existing_inputs(
    tmp_path: Path, whitelist: list[str] | None, expected: frozenset[str] | None
) -> None:
    agent = _agent(tmp_path, whitelist)
    allowed = agent._backend_allowed_tools_from_profile()
    assert allowed == expected
    assert BackendToolRuntime(agent.workspace, allowed_tools=allowed)._allowed_tools == expected


@pytest.mark.parametrize(
    ("whitelist", "expected"),
    [(None, {"read_file", "write_file"}), ([], set()), (["read_file"], {"read_file"})],
)
def test_real_backend_registration_cannot_expand_an_explicit_empty_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, whitelist: list[str] | None, expected: set[str]
) -> None:
    assert _registered_tools(_agent(tmp_path, whitelist), monkeypatch) == expected
