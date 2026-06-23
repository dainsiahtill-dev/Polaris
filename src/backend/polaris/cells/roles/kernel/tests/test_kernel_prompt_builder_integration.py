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


def test_build_system_prompt_for_director_codegen_suppresses_conflicting_layers(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="gpt-5.3-codex",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["read_file", "write_file"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="[mode:propose] Do not call tools.",
        history=[],
        context_override={
            "director_runtime_codegen": True,
            "director_runtime_codegen_mode": "proposal_then_apply",
            "delivery_mode": "propose_patch",
        },
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = kernel._get_prompt_builder()
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = kernel._build_system_prompt_for_request(profile, request, "bridge appendix")  # type: ignore[arg-type]

    assert result == "system-prompt"
    assert captured["appendix"] == "bridge appendix"
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is False


def test_build_system_prompt_for_quality_repair_suppresses_working_memory_only(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="qwen3.6-27b-int4",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["write_file"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=(
            "MATERIALIZATION QUALITY REPAIR MODE:\n"
            "Artifact quality scan failed: npm package manifest script references a missing file.\n"
            "Do not read files first. Do not list directories. Emit exactly one write_file tool call."
        ),
        history=[],
        context_override={"delivery_mode": "materialize_changes"},
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = kernel._get_prompt_builder()
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = kernel._build_system_prompt_for_request(profile, request, "quality repair appendix")  # type: ignore[arg-type]

    assert result == "system-prompt"
    assert captured["appendix"] == "quality repair appendix"
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is True


def test_build_system_prompt_for_factory_contract_suppresses_working_memory_only(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="qwen3.6-27b-q6-code-gpu0",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["write_file", "execute_command"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=(
            "PM Task Contract / 任务合同:\n"
            "任务: 实现 发光昆虫花园模拟器 TypeScript 项目骨架与核心模块\n"
            "目标文件: package.json, tsconfig.json, src/index.ts\n"
            "Chief Engineer Blueprint / CE 蓝图交接:\n"
            "- blueprint_id: ce_TASK-1\n"
            "请通过运行时正式写入工具完成修改；若只能返回文本，输出可解析的文件块。"
        ),
        history=[],
        context_override={},
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = kernel._get_prompt_builder()
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = kernel._build_system_prompt_for_request(profile, request, "factory appendix")  # type: ignore[arg-type]

    assert result == "system-prompt"
    assert captured["appendix"] == "factory appendix"
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is True


def test_build_system_prompt_for_forced_write_suppresses_working_memory_only(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="qwen3.6-27b-int4",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["execute_command", "write_file"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="Retry the failed artifact write using exactly one write_file call.",
        history=[],
        context_override={
            "_transaction_kernel_forced_tool_choice": {
                "type": "function",
                "function": {"name": "write_file"},
            },
        },
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = kernel._get_prompt_builder()
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = kernel._build_system_prompt_for_request(profile, request, "forced write appendix")  # type: ignore[arg-type]

    assert result == "system-prompt"
    assert captured["appendix"] == "forced write appendix"
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is True
