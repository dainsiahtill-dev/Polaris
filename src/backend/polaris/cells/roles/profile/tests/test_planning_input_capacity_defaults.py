"""Shipped planning capacity, with synthetic inputs and zero external dispatch.

The historical correction plaintext is unavailable. Equal token counts below
are a synthetic control, never a reconstruction of its characters or hash.
"""

from __future__ import annotations

import copy
import hashlib
import json
import json as jsonlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml
from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import TokenEstimator
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.public.service import ContextRequest, PromptBuilder, RoleContextGateway
from polaris.cells.roles.profile.internal import registry as registry_module
from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES
from polaris.cells.roles.profile.public.service import (
    RoleContextPolicy,
    RoleProfile,
    RoleProfileRegistry,
    load_core_roles,
    profile_from_dict,
    profile_to_dict,
)
from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling
from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher
from polaris.kernelone.errors import BudgetExceededError
from polaris.kernelone.llm import config_store
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.runtime_config import (
    RuntimeConfigManager,
    get_runtime_config_manager,
    set_runtime_config_manager,
)

_CELL = Path(__file__).resolve().parents[1]
_SOURCES = (_CELL / "config/roles/core_roles.yaml", _CELL / "internal/config/core_roles.yaml")
_OLD_CAPS = {"pm": 8000, "chief_engineer": 12000}
# Frozen pre-repair source hashes: prove byte preservation outside four scalars.
_BASELINE_HASHES = (
    "d711a7b75989c03e43ad6eb795590fc826034ec63ff80e4240aa032e3487bbec",
    "00caf7b2e0d31f3cb2ac562a1fa58d895a5565b370017349beb8c891aa0f25ce",
)


def _rows(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["roles"]


@pytest.fixture(autouse=True)
def isolated_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Only config storage, event delivery and HTTP effects are substituted."""
    configuration: dict[str, Any] = {
        "providers": {
            "capacity-provider": {
                "type": "openai_compat",
                "base_url": "https://capacity.example.test/v1",
                "retries": 0,
                "model_specific": {
                    "capacity-model": {
                        "max_context_tokens": 1000000,
                        "max_output_tokens": 65536,
                        "supports_tools": True,
                        "supports_json_schema": True,
                    }
                },
            }
        },
        "roles": {
            row["role_id"]: {"provider_id": "capacity-provider", "model": "capacity-model"}
            for row in _rows(_SOURCES[0])
        },
    }
    binding_path = tmp_path / "isolated-binding.json"
    binding_path.write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
    old_manager = get_runtime_config_manager()
    set_runtime_config_manager(RuntimeConfigManager(config_path_resolver=lambda: str(binding_path)))
    monkeypatch.setattr(config_store, "load_llm_config", lambda *_a, **_kw: copy.deepcopy(configuration))
    monkeypatch.setattr(registry_module, "registry", RoleProfileRegistry())
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernel-home"))
    monkeypatch.setattr(JetStreamPublisher, "publish", lambda *_a, **_kw: True)

    def no_network(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Unexpected network transport in source-only capacity regression")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    try:
        yield configuration
    finally:
        set_runtime_config_manager(old_manager)


def _profile(role: str, directory: Path | None = None) -> RoleProfile:
    loaded = load_core_roles(str(directory) if directory is not None else None)
    return loaded.get_profile_or_raise(role)


@pytest.mark.parametrize("source_index", [0, 1])
def test_only_planning_default_scalars_change(source_index: int) -> None:
    path = _SOURCES[source_index]
    source = path.read_text(encoding="utf-8")
    before = source
    for role, old_cap in _OLD_CAPS.items():
        row = next(row for row in _rows(path) if row["role_id"] == role)
        assert row["context_policy"]["max_context_tokens"] == 262144
        before = before.replace("max_context_tokens: 262144", f"max_context_tokens: {old_cap}", 1)
    assert hashlib.sha256(before.encode("utf-8")).hexdigest() == _BASELINE_HASHES[source_index]


@pytest.mark.parametrize("selection", ["preferred", "fallback", "builtin"])
def test_real_loaders_and_all_profile_fields(selection: str, tmp_path: Path) -> None:
    directory = None if selection == "preferred" else _SOURCES[1].parent if selection == "fallback" else tmp_path
    loaded = load_core_roles(str(directory) if directory is not None else None)
    source_rows = _rows(_SOURCES[1 if selection == "fallback" else 0])
    assert set(loaded.list_roles()) == {row["role_id"] for row in source_rows}
    if selection == "builtin":
        assert source_rows == BUILTIN_PROFILES
    for row in source_rows:
        expected = copy.deepcopy(row)
        expected.update(provider_id="capacity-provider", model="capacity-model")
        profile = loaded.get_profile_or_raise(row["role_id"])
        assert profile_to_dict(profile) == profile_to_dict(profile_from_dict(expected))
        assert (
            profile.context_policy.max_context_tokens
            == {
                "pm": 262144,
                "chief_engineer": 262144,
                "architect": 10000,
                "director": 32000,
                "qa": 8000,
                "scout": 16000,
                "resident_agi": 32000,
            }[profile.role_id]
        )
    assert RoleContextPolicy().max_context_tokens == 8000


@pytest.mark.parametrize("role", ["pm", "chief_engineer"])
async def test_explicit_custom_caps_still_selected_and_enforced(role: str, tmp_path: Path) -> None:
    rows = _rows(_SOURCES[0])
    for row in rows:
        if row["role_id"] in _OLD_CAPS:
            row["context_policy"]["max_context_tokens"] = _OLD_CAPS[row["role_id"]]
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "core_roles.yaml").write_text(yaml.safe_dump({"roles": rows}, allow_unicode=True), encoding="utf-8")
    profile = _profile(role, custom)
    gateway = RoleContextGateway(profile, workspace=tmp_path)
    assert profile.context_policy.max_context_tokens == gateway._enforcement_budget_tokens == _OLD_CAPS[role]
    payload = "x" * (_OLD_CAPS[role] * 4)
    with pytest.raises(BudgetExceededError, match="current user instruction"):
        await gateway.build_context(ContextRequest(message=payload), system_prompt="role identity")


def _synthetic_equal_count_inputs() -> tuple[str, str]:
    system = "Synthetic PM identity; " + "s" * (12408 - len("Synthetic PM identity; "))
    current = "Synthetic correction, not historical plaintext; " + "x" * (
        30104 - len("Synthetic correction, not historical plaintext; ")
    )
    estimator = TokenEstimator()
    assert estimator.estimate([{"role": "system", "content": system}]) == 3106
    assert estimator.estimate([{"role": "user", "content": current}]) == 7530
    return system, current


async def test_actual_count_synthetic_control_rejects_old_cap_admits_shipped(tmp_path: Path) -> None:
    system, current = _synthetic_equal_count_inputs()
    old_row = next(row for row in _rows(_SOURCES[0]) if row["role_id"] == "pm")
    old_row.update(provider_id="capacity-provider", model="capacity-model")
    old_row["context_policy"]["max_context_tokens"] = 8000
    old_gateway = RoleContextGateway(profile_from_dict(old_row), workspace=tmp_path)
    with pytest.raises(BudgetExceededError) as caught:
        await old_gateway.build_context(ContextRequest(message=current), system_prompt=system)
    assert (caught.value.limit, caught.value.requested, caught.value.current) == (4894, 7530, 4894)
    gateway = RoleContextGateway(_profile("pm"), workspace=tmp_path)
    assert gateway._enforcement_budget_tokens == 262144
    result = await gateway.build_context(ContextRequest(message=current), system_prompt=system)
    assert result.messages[0] == {"role": "system", "content": system}
    assert result.messages[-1] == {"role": "user", "content": current}
    assert 10636 <= result.token_estimate <= 262144


@pytest.mark.parametrize("role", ["pm", "chief_engineer"])
@pytest.mark.parametrize(
    "window,ceiling,stage", [(8000, 6800, "current user instruction"), (2000, 1700, "system prompt")]
)
async def test_known_small_model_windows_still_reject(
    role: str, window: int, ceiling: int, stage: str, tmp_path: Path, isolated_binding: dict[str, Any]
) -> None:
    isolated_binding["providers"]["capacity-provider"]["model_specific"]["capacity-model"]["max_context_tokens"] = (
        window
    )
    gateway = RoleContextGateway(_profile(role), workspace=tmp_path)
    assert gateway._context_os.resolved_context_window == window
    assert gateway._enforcement_budget_tokens == ceiling
    system, current = _synthetic_equal_count_inputs()
    with pytest.raises(BudgetExceededError, match=stage):
        await gateway.build_context(ContextRequest(message=current), system_prompt=system)


def _synthetic_draft_feedback() -> tuple[str, str]:
    # Real estimator, exactly the prior 60,000-token allowance scale; no business stub.
    prefix, suffix = '{"synthetic_prior_draft":"', '","tail":"COMPLETE-DRAFT"}'
    size = 239984 - len(prefix) - len(suffix)
    draft = prefix + ("synthetic draft entry; " * (size // 22 + 1))[:size] + suffix
    assert TokenEstimator().estimate([{"role": "assistant", "content": draft}]) == 60000
    requirements = "Complete synthetic requirements: public symbols, owned targets, cross-file contracts and executable acceptance."
    feedback = "Complete synthetic feedback: retain every requirement and draft byte; revise all interface ownership inconsistencies."
    current = requirements + "\n" + draft + "\n" + feedback + "\nCURRENT-END"
    return draft, current


def _fake_http(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    wires: list[dict[str, Any]] = []

    def post(url: str, *, headers: Any, json: dict[str, Any], timeout: Any) -> requests.Response:
        assert url == "https://capacity.example.test/v1/chat/completions"
        # Round-trip the actual provider serializer's body; no provider invocation.
        wires.append(jsonlib.loads(jsonlib.dumps(json, ensure_ascii=False)))
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = b'{"choices":[{"message":{"role":"assistant","content":"synthetic response"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}'
        return response

    monkeypatch.setattr(_http_pooling, "bounded_http_post", post)
    return wires


async def _prepare(profile: RoleProfile, workspace: Path, current: str) -> tuple[Any, str]:
    system = PromptBuilder(workspace=str(workspace)).build_system_prompt(
        profile, message=current, include_working_memory_contract=False
    )
    prepared = await LLMRequestPreparer(workspace=str(workspace))._prepare_llm_request(
        profile=profile,
        system_prompt=system,
        context=ContextRequest(message=current),
        temperature=0.3,
        max_tokens=60000,
        stream=False,
    )
    prepared.ai_request.provider_id = profile.provider_id
    prepared.ai_request.model = profile.model
    assert prepared.messages[0]["content"] == system + f"\n\npolaris.role_identity.v1:{profile.role_id}"
    return prepared, prepared.messages[0]["content"]


@pytest.mark.parametrize("role", ["pm", "chief_engineer"])
async def test_full_prior_draft_requirements_feedback_and_60000_final_body(
    role: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft, current = _synthetic_draft_feedback()
    profile = _profile(role)
    prepared, system = await _prepare(profile, tmp_path, current)
    assert prepared.messages[0] == {"role": "system", "content": system}
    assert prepared.messages[-1] == {"role": "user", "content": current}
    wires = _fake_http(monkeypatch)
    executor = AIExecutor(workspace=str(tmp_path))
    response = await executor._execute_invoke(prepared.ai_request, "synthetic-capacity")
    assert response.ok, response.error
    assert len(wires) == 1
    wire = wires[0]
    (tmp_path / "boundary-debug.json").write_text(
        json.dumps({"wire": wire, "prepared": prepared.ai_request.context["chat_messages"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    # Existing serializer folds supplemental project blocks into the first system.
    assert wire["messages"][0]["content"].startswith(system)
    for message in prepared.ai_request.context["chat_messages"]:
        assert any(message["content"] in final["content"] for final in wire["messages"])
    assert f"polaris.role_identity.v1:{role}" in system
    assert wire["messages"][-1]["content"] == current
    assert draft in wire["messages"][-1]["content"]
    assert wire["max_tokens"] == prepared.request_options["max_tokens"] == 60000
    assert wire["tools"] == prepared.request_options["tools"]
    assert wire["tool_choice"] == prepared.request_options["tool_choice"]
    assert wire.get("response_format") == prepared.request_options.get("response_format")
    assert "repo_tree" in {tool["function"]["name"] for tool in wire["tools"]}
    ref = prepared.ai_request.context["context_snapshot_ref"]
    assert len(ref) == 24 and all(char in "0123456789abcdef" for char in ref)
    snapshot_path = tmp_path / ".polaris/runtime/contexts" / ref[:2] / ref
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    provider_request = snapshot["provider_request"]
    assert snapshot["messages"] == prepared.ai_request.context["chat_messages"]
    audit = provider_request["final_request_context_audit"]
    assert provider_request["provider_option_summary"]["max_tokens"] == 60000
    assert audit["context_window_tokens"] == 1000000
    assert audit["tool_schema_token_estimate"] > 0
    assert audit["final_request_token_estimate"] > 60000
    assert set(audit["coverage"]) == {
        "has_pm_contract",
        "has_chief_engineer_blueprint",
        "has_target_files",
        "has_failure_feedback",
        "has_workspace_quality_evidence",
    }
    (tmp_path / "synthetic-capacity-proof.json").write_text(
        json.dumps(
            {
                "kind": "synthetic_component_capacity_not_native_replay",
                "role": role,
                "system": system,
                "current": current,
                "wire": wire,
                "snapshot": snapshot,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("role", ["pm", "chief_engineer"])
@pytest.mark.parametrize(
    "window,output_cap,large_input,diagnostic",
    [
        (1000000, 32768, False, "planning_output_budget_model_limit_exceeded"),
        (60032, 65536, False, "planning_output_budget_context_window_exceeded"),
        (130000, 65536, True, "planning_output_budget"),
    ],
)
async def test_output_tools_safety_and_mandatory_input_reject_before_transport(
    role: str,
    window: int,
    output_cap: int,
    large_input: bool,
    diagnostic: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_binding: dict[str, Any],
) -> None:
    limits = isolated_binding["providers"]["capacity-provider"]["model_specific"]["capacity-model"]
    limits.update(max_context_tokens=window, max_output_tokens=output_cap)
    current = (
        ("Mandatory synthetic full input: " + "owned target; " * 27000 + " END")
        if large_input
        else "Complete requirements."
    )
    prepared, _ = await _prepare(_profile(role), tmp_path, current)
    original_messages = copy.deepcopy(prepared.ai_request.context["chat_messages"])
    wires = _fake_http(monkeypatch)
    response = await AIExecutor(workspace=str(tmp_path))._execute_invoke(prepared.ai_request, "negative-capacity")
    assert not response.ok and diagnostic in str(response.error)
    assert wires == []
    assert prepared.ai_request.context["chat_messages"] == original_messages
    assert prepared.ai_request.context["chat_messages"][-1]["content"] == current
    assert prepared.ai_request.options["max_tokens"] == 60000


@pytest.mark.parametrize("role", ["pm", "chief_engineer"])
async def test_unknown_model_provisional_fallback_cannot_dispatch(
    role: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_binding: dict[str, Any]
) -> None:
    isolated_binding["providers"]["capacity-provider"]["model_specific"] = {}
    profile = _profile(role)
    gateway = RoleContextGateway(profile, workspace=tmp_path)
    assert gateway._context_os.resolved_context_window == 262144
    assert gateway._enforcement_budget_tokens == int(262144 * 0.85)
    prepared, _ = await _prepare(profile, tmp_path, "Complete synthetic unknown-model requirements.")
    wires = _fake_http(monkeypatch)
    with pytest.raises(ValueError, match="Context window not configured"):
        await AIExecutor(workspace=str(tmp_path))._execute_invoke(prepared.ai_request, "unknown-capacity")
    assert wires == []
