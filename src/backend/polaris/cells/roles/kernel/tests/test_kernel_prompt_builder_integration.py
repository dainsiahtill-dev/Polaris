from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest


class _StubRegistry:
    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


def test_build_system_prompt_for_request_passes_message_to_prompt_builder(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="pm",
        model="gpt-5",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="pm-policy-v1", whitelist=["read_file"]),
        prompt_policy=SimpleNamespace(core_template_id="pm-v1", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="inspect README and summarize findings",
        history=[],
        context_override={},
    )
    captured: dict[str, str] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, *, domain="code", message="") -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured["domain"] = str(domain or "")
        captured["message"] = str(message or "")
        return "system-prompt"

    # FIX: 新的架构使用 _get_prompt_builder() 获取 prompt_builder
    prompt_builder = kernel._get_prompt_builder()
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = kernel._build_system_prompt_for_request(profile, request, "benchmark appendix")  # type: ignore[arg-type]

    assert result == "system-prompt"
    assert captured["appendix"] == "benchmark appendix"
    assert captured["domain"] == "code"
    assert captured["message"] == "inspect README and summarize findings"
