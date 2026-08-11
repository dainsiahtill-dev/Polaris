from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.prompt_assembly import (
    build_system_prompt_for_request,
    resolve_prompt_layer_options,
)
from polaris.cells.roles.kernel.internal.kernel.prompt_builder_provider import get_prompt_builder
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest


class _StubRegistry:
    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


def test_system_prompt_builder_receives_request_message(monkeypatch) -> None:
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

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="benchmark appendix",
        workspace=kernel.workspace,
    )

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

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="bridge appendix",
        workspace=kernel.workspace,
    )

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
        context_override={
            "delivery_mode": "materialize_changes",
            # Historical payload metadata must not downgrade a physical repair
            # turn to a read-only profession protocol.
            "task_type": "readonly",
        },
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="quality repair appendix",
        workspace=kernel.workspace,
    )

    assert result == "system-prompt"
    assert str(captured["appendix"]).startswith("quality repair appendix")
    assert "[POLARIS PROMPT PROFILE]" in str(captured["appendix"])
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is True
    assert captured["task_type"] == "bug_fix"


def test_prompt_task_type_ignores_payload_phase_and_readonly_metadata() -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="gpt-5",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["write_file"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    prompt_builder = get_prompt_builder(kernel)

    result = prompt_builder._resolve_prompt_task_type(
        task_type="default",
        message="MATERIALIZATION QUALITY REPAIR MODE: fix the failing TypeScript verifier.",
        prompt_appendix=(
            "historical_payload: {'task_type': 'readonly', 'phase': 'requirements'}\n"
            "selection=language:typescript; task:review; stage:requirements"
        ),
    )

    assert result == "bug_fix"


def test_prompt_task_type_maps_authoritative_write_code_to_new_code(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="gpt-5",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["write_file"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="Implement the declared target files.",
        history=[],
        context_override={
            "task_type": "readonly",
            "director_execution_profile": {"task_type": "write_code"},
        },
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="",
        workspace=kernel.workspace,
    )

    assert result == "system-prompt"
    assert captured["task_type"] == "new_code"


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

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="factory appendix",
        workspace=kernel.workspace,
    )

    assert result == "system-prompt"
    assert str(captured["appendix"]).startswith("factory appendix")
    assert "[POLARIS PROMPT PROFILE]" in str(captured["appendix"])
    assert "builtin.language.typescript" in str(captured["appendix"])
    assert request.context_override["selected_prompt_profile_ids"]
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is True


def test_build_system_prompt_recomputes_stale_empty_prompt_profile_audit(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="qwen3.6-27b-q6-code-gpu0",
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
            "src/domain/humidity.ts(48,3): error TS2739. tests/verify.test.ts must pass."
        ),
        history=[],
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/domain/humidity.ts", "tests/verify.test.ts"],
            "prompt_profile_audit": {
                "selected_prompt_profile_ids": [],
                "inferred_language": "typescript",
                "inferred_task_type": "bugfix",
                "inferred_stage": "quality_repair",
                "inferred_artifact": "test_suite",
                "skipped_reason": "strict_quality_repair",
            },
            "selected_prompt_profile_ids": [],
            "prompt_profile_appendix": "",
        },
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="",
        workspace=kernel.workspace,
    )

    assert result == "system-prompt"
    assert "[POLARIS PROMPT PROFILE]" in str(captured["appendix"])
    selected_ids = request.context_override["selected_prompt_profile_ids"]
    assert "builtin.language.typescript" in selected_ids
    assert "builtin.task.bugfix" in selected_ids
    assert "builtin.role_stage.director.quality_repair" in selected_ids
    assert "builtin.artifact.test_suite" in selected_ids
    assert request.context_override["prompt_profile_audit"]["skipped_reason"] == ""


def test_build_system_prompt_recomputes_stale_cached_prompt_profile_appendix(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="director",
        model="qwen3.6-27b-q6-code-gpu0",
        version="1.0.0",
        tool_policy=SimpleNamespace(policy_id="director-policy-v1", whitelist=["write_file"]),
        prompt_policy=SimpleNamespace(core_template_id="director", tpl_version="1.0"),
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=(
            "PM Task Contract / 任务合同:\n"
            "任务: 实现 发光昆虫花园模拟器 TypeScript 项目骨架与核心模块\n"
            "目标文件: package.json, tsconfig.json, src/index.ts, src/main.ts, "
            "src/domain/firefly.ts, src/domain/flower.ts, src/domain/moon.ts, src/domain/humidity.ts\n"
            "目标文件覆盖硬门禁: 本任务列出的目标文件必须全部由本轮工具写入或编辑。\n"
            "请通过运行时正式写入工具完成修改；若只能返回文本，输出可解析的文件块。"
        ),
        history=[],
        context_override={
            "target_files": [
                "package.json",
                "tsconfig.json",
                "src/index.ts",
                "src/main.ts",
                "src/domain/firefly.ts",
                "src/domain/flower.ts",
                "src/domain/moon.ts",
                "src/domain/humidity.ts",
            ],
            "prompt_profile_audit": {
                "selected_prompt_profile_ids": [
                    "builtin.language.typescript",
                    "builtin.task.bugfix",
                    "builtin.role_stage.director.default",
                    "builtin.artifact.config",
                ],
                "inferred_stage": "default",
                "inferred_artifact": "config",
            },
            "selected_prompt_profile_ids": [
                "builtin.language.typescript",
                "builtin.task.bugfix",
                "builtin.role_stage.director.default",
                "builtin.artifact.config",
            ],
            "prompt_profile_appendix": (
                "[POLARIS PROMPT PROFILE]\n"
                "selection=language:typescript; task:bugfix; stage:default; artifact:config\n"
                "- builtin.artifact.config: stale"
            ),
        },
    )
    captured: dict[str, object] = {}

    def _fake_build_system_prompt(_profile, prompt_appendix, **kwargs: object) -> str:
        captured["appendix"] = str(prompt_appendix or "")
        captured.update(kwargs)
        return "system-prompt"

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="",
        workspace=kernel.workspace,
    )

    assert result == "system-prompt"
    appendix = str(captured["appendix"])
    assert "stage:materialize; artifact:library" in appendix
    assert "builtin.role_stage.director.materialize" in appendix
    assert "builtin.artifact.library" in appendix
    assert "builtin.role_stage.director.default" not in appendix
    assert "builtin.artifact.config" not in appendix
    selected_ids = request.context_override["selected_prompt_profile_ids"]
    assert "builtin.task.implement" in selected_ids
    assert "builtin.role_stage.director.materialize" in selected_ids
    assert "builtin.artifact.library" in selected_ids


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

    prompt_builder = get_prompt_builder(kernel)
    monkeypatch.setattr(prompt_builder, "build_system_prompt", _fake_build_system_prompt)

    result = build_system_prompt_for_request(
        prompt_builder=prompt_builder,
        profile=profile,  # type: ignore[arg-type]
        request=request,
        prompt_appendix="forced write appendix",
        workspace=kernel.workspace,
    )

    assert result == "system-prompt"
    assert captured["appendix"] == "forced write appendix"
    assert captured["include_working_memory_contract"] is False
    assert captured["include_tool_policy"] is True


def test_deprecated_exact_edit_forced_choice_does_not_trigger_forced_write_prompt_layer() -> None:
    retired_tool_name = "precision" + "_edit"

    assert (
        resolve_prompt_layer_options(
            {"_transaction_kernel_forced_tool_choice": {"function": {"name": retired_tool_name}}},
            message="Retry with the historical exact-edit tool.",
        )
        == {}
    )
