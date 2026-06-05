"""Unit tests for orchestration.pm_planning internal pipeline_ports.

Tests all public pure functions: normalize_priority, normalize_engine_config,
normalize_pm_payload, collect_schema_warnings, _looks_like_tool_call_output,
format_json_for_prompt, _extract_json_from_llm_output, _strip_llm_xml_tags,
and protocol classes.
"""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
    CellPmInvokePort,
    NoopPmInvokePort,
    NoopPmStatePort,
    PmBackendInvokeResult,
    PmStatePort,
    _looks_like_tool_call_output,
    _strip_llm_xml_tags,
    collect_schema_warnings,
    format_json_for_prompt,
    normalize_engine_config,
    normalize_pm_payload,
    normalize_priority,
)
from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    _CARD3D_PM_REQUIRED_DOMAINS,
    build_card3d_pm_required_domain_contract,
)
from polaris.cells.orchestration.pm_planning.pipeline import (
    _autofix_domain_coverage_critical_issues,
    _build_pm_quality_retry_prompt,
    _merge_engine_config,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1

# ---------------------------------------------------------------------------
# normalize_priority
# ---------------------------------------------------------------------------


class TestNormalizePriority:
    def test_integer_in_range(self) -> None:
        assert normalize_priority(3) == 3

    def test_string_aliases(self) -> None:
        assert normalize_priority("high") == 1
        assert normalize_priority("urgent") == 0
        assert normalize_priority("low") == 9
        assert normalize_priority("medium") == 5

    def test_clamped_to_0_9(self) -> None:
        assert normalize_priority(99) == 9
        assert normalize_priority(-5) == 0

    def test_float_parsing(self) -> None:
        assert normalize_priority("7.8") == 7

    def test_invalid_fallback(self) -> None:
        assert normalize_priority("not a number", fallback=3) == 3


class TestAutofixDomainCoverageCriticalIssues:
    def test_card3d_bulk_domain_autofix_is_hard_quality_evidence(self) -> None:
        issues = _autofix_domain_coverage_critical_issues(
            {"card3d_domain_tasks_added": len(_CARD3D_PM_REQUIRED_DOMAINS), "game_domain_tasks_added": 0}
        )

        assert len(issues) == 1
        assert "card3d" in issues[0]
        assert "Regenerate the PM contract" in issues[0]

    def test_card3d_minor_domain_autofix_does_not_force_retry(self) -> None:
        issues = _autofix_domain_coverage_critical_issues(
            {"card3d_domain_tasks_added": 2, "game_domain_tasks_added": 0}
        )

        assert issues == []

    def test_card3d_retry_prompt_includes_exact_domain_contract(self) -> None:
        prompt = _build_pm_quality_retry_prompt(
            base_prompt="Return PM JSON.",
            previous_payload={"overall_goal": "Build Card3D", "tasks": []},
            quality_report={
                "summary": "failed",
                "critical_issues": [
                    "card3d domain autofix added "
                    f"{len(_CARD3D_PM_REQUIRED_DOMAINS)} tasks; PM decomposition was insufficient."
                ],
                "warnings": [],
            },
        )

        assert "CARD3D HARD CONTRACT" in prompt
        assert "default 1-3 task batch limit" in prompt
        assert "Compact output rule" in prompt
        assert "Do not group multiple domains into one task" in prompt
        assert "FINAL NON-NEGOTIABLE DOMAIN CONTRACT" in prompt
        assert "PM-CARD3D-CLIENT3D-01" in prompt
        assert "metadata.domain=client3d" in prompt
        assert "scope_paths=[src/client/three-scene.ts]" in prompt
        assert "tests/unit/card-rules.test.ts" in prompt
        assert "tests/e2e/card-table-3d.test.ts" in prompt
        assert "replacing/removing existing trivial arithmetic placeholder tests" in prompt

    def test_card3d_domain_contract_is_canonical_prompt_source(self) -> None:
        contract = build_card3d_pm_required_domain_contract()

        assert "CARD3D HARD CONTRACT" in contract
        assert f"Required task count: at least {len(_CARD3D_PM_REQUIRED_DOMAINS)}." in contract
        assert "default 1-3 task batch limit" in contract
        assert "exactly 3 execution_checklist items" in contract
        assert "PM-CARD3D-CLIENT3D-01" in contract
        assert "PM-CARD3D-TESTS-22" in contract


# ---------------------------------------------------------------------------
# normalize_engine_config
# ---------------------------------------------------------------------------


class TestNormalizeEngineConfig:
    def test_empty_input(self) -> None:
        assert normalize_engine_config(None) == {}
        assert normalize_engine_config("not a dict") == {}

    def test_passes_valid_fields(self) -> None:
        cfg = {
            "director_execution_mode": "multi",
            "scheduling_policy": "priority",
            "max_directors": 3,
        }
        result = normalize_engine_config(cfg)
        assert result["director_execution_mode"] == "multi"
        assert result["scheduling_policy"] == "priority"
        assert result["max_directors"] == 3

    def test_ignores_unknown_fields(self) -> None:
        cfg = {"unknown_field": "value", "director_execution_mode": "single"}
        result = normalize_engine_config(cfg)
        assert "unknown_field" not in result
        assert result["director_execution_mode"] == "single"

    def test_invalid_max_directors(self) -> None:
        cfg = {"max_directors": "three"}
        result = normalize_engine_config(cfg)
        assert "max_directors" not in result

    def test_negative_max_directors_ignored(self) -> None:
        cfg = {"max_directors": -1}
        result = normalize_engine_config(cfg)
        assert "max_directors" not in result

    def test_accepts_workflow_payload_aliases(self) -> None:
        cfg = {
            "director_workflow_execution_mode": "parallel",
            "max_parallel_tasks": 3,
            "scheduling_policy": "dag",
        }
        result = normalize_engine_config(cfg)
        assert result["director_execution_mode"] == "multi"
        assert result["max_directors"] == 3
        assert result["scheduling_policy"] == "dag"


def test_merge_engine_config_uses_desktop_workflow_args() -> None:
    args = Namespace(
        director_workflow_execution_mode="parallel",
        director_max_parallel_tasks=3,
        director_scheduling_policy="dag",
    )

    result = _merge_engine_config(None, args)

    assert result == {
        "director_execution_mode": "multi",
        "max_directors": 3,
        "scheduling_policy": "dag",
    }


# ---------------------------------------------------------------------------
# normalize_pm_payload
# ---------------------------------------------------------------------------


class TestNormalizePmPayloadHappyPath:
    def test_dict_payload(self) -> None:
        raw = {
            "tasks": [
                {
                    "title": "Build login",
                    "goal": "Create login form",
                    "priority": "high",
                    "context_files": "a.py,b.py",
                    "assigned_to": "director",
                    "phase": "bootstrap",
                    "dependencies": [],
                    "acceptance_criteria": ["renders", "validates"],
                }
            ],
            "overall_goal": "Implement auth",
            "focus": "security",
        }
        result = normalize_pm_payload(raw, iteration=1, start_timestamp="2026-03-23T00:00:00Z")
        assert result["schema_version"] == 2
        assert result["run_id"] == "pm-00001"
        assert result["pm_iteration"] == 1
        assert len(result["tasks"]) == 1
        task = result["tasks"][0]
        assert task["priority"] == 1  # "high" → 1
        # context_files is passed through as-is in a list
        assert task["context_files"] == ["a.py,b.py"]
        assert task["assigned_to"] == "director"
        assert task["phase"] == "bootstrap"
        assert "renders" in task["acceptance_criteria"]
        # pipeline.py injects these after normalization; they are not present here
        assert "doc_id" not in task
        assert "blueprint_id" not in task

    def test_payload_can_bind_workspace(self) -> None:
        raw = {
            "tasks": [
                {
                    "title": "Build login",
                    "goal": "Create login form",
                    "acceptance_criteria": ["npm test passes"],
                }
            ],
        }
        result = normalize_pm_payload(raw, iteration=1, start_timestamp="t", workspace_full="C:/Temp/Product")
        assert result["workspace"] == "C:/Temp/Product"

    def test_migrate_acceptance_alias(self) -> None:
        raw = {
            "tasks": [
                {
                    "title": "T",
                    "goal": "G",
                    "acceptance": ["check1"],
                    "phase": "impl",
                    "dependencies": [],
                }
            ]
        }
        result = normalize_pm_payload(raw, iteration=0, start_timestamp="t")
        assert "check1" in result["tasks"][0]["acceptance_criteria"]

    def test_migrate_depends_on_alias(self) -> None:
        raw = {
            "tasks": [
                {
                    "title": "T",
                    "goal": "G",
                    "depends_on": ["T01"],
                    "phase": "impl",
                    "acceptance_criteria": ["done"],
                }
            ]
        }
        result = normalize_pm_payload(raw, iteration=1, start_timestamp="t")
        assert "T01" in result["tasks"][0]["dependencies"]

    def test_directory_targets_are_promoted_to_scope_paths(self) -> None:
        raw = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Build FashionGen modules",
                    "goal": "create renderer shell and generation contract",
                    "target_files": [
                        "src/renderer",
                        "src/shared/generationSpec.ts",
                        "package.json",
                    ],
                    "phase": "impl",
                    "acceptance_criteria": ["npm test passes"],
                }
            ]
        }
        result = normalize_pm_payload(raw, iteration=1, start_timestamp="t")
        task = result["tasks"][0]
        assert task["target_files"] == ["src/shared/generationSpec.ts", "package.json"]
        assert task["scope_paths"] == ["src/renderer"]

    def test_preserves_explicit_pm_ids_and_dependency_refs(self) -> None:
        raw = {
            "tasks": [
                {
                    "id": "PM-0001-1",
                    "title": "Implement client scene",
                    "goal": "Implement the Three.js card table client scene.",
                    "target_files": ["src/client/three-scene.ts"],
                    "phase": "implementation",
                    "acceptance_criteria": ["Run `npm run build` exits 0"],
                    "execution_checklist": ["Read existing files", "Implement client scene", "Run npm run build"],
                },
                {
                    "id": "PM-0002-1",
                    "title": "Implement realtime backend",
                    "goal": "Implement the Node.js realtime backend.",
                    "target_files": ["src/server/app.ts"],
                    "phase": "implementation",
                    "depends_on": ["PM-0001-1"],
                    "acceptance_criteria": ["Run `npm run build` exits 0"],
                    "execution_checklist": ["Read client protocol", "Implement backend", "Run npm run build"],
                },
            ]
        }

        result = normalize_pm_payload(raw, iteration=3, start_timestamp="t")

        assert [task["id"] for task in result["tasks"]] == ["PM-0001-1", "PM-0002-1"]
        assert result["tasks"][1]["dependencies"] == ["PM-0001-1"]


class TestNormalizePmPayloadEdgeCases:
    def test_non_dict_returns_error_note(self) -> None:
        result = normalize_pm_payload("not a dict", iteration=0, start_timestamp="t")
        assert result["tasks"] == []
        assert "Invalid PM payload" in result["notes"]

    def test_non_list_tasks(self) -> None:
        result = normalize_pm_payload({"tasks": "not a list"}, iteration=0, start_timestamp="t")
        assert result["tasks"] == []

    def test_generates_task_id_from_title(self) -> None:
        raw = {
            "tasks": [
                {
                    "title": "Design login form",
                    "goal": "Create the login page",
                    "phase": "impl",
                    "acceptance_criteria": [],
                    "dependencies": [],
                }
            ]
        }
        result = normalize_pm_payload(raw, iteration=2, start_timestamp="t")
        task_id = result["tasks"][0]["id"]
        assert task_id.startswith("T02-")
        assert "design_login_form" in task_id

    def test_skips_non_dict_items_in_tasks(self) -> None:
        raw = {
            "tasks": [
                "not a dict",
                123,
                {"title": "Good task", "goal": "g", "phase": "impl", "acceptance_criteria": [], "dependencies": []},
            ]
        }
        result = normalize_pm_payload(raw, iteration=0, start_timestamp="t")
        assert len(result["tasks"]) == 1


# ---------------------------------------------------------------------------
# collect_schema_warnings
# ---------------------------------------------------------------------------


class TestCollectSchemaWarnings:
    def test_valid_payload(self, tmp_path) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "priority": 1,
                    "spec": "spec text",
                    "acceptance_criteria": ["done"],
                    "assigned_to": "director",
                    "dependencies": [],
                }
            ]
        }
        warnings = collect_schema_warnings(payload, str(tmp_path))
        assert len(warnings) == 0

    def test_missing_required_field(self, tmp_path) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    # priority missing
                    "spec": "",
                    "acceptance_criteria": [],
                    "assigned_to": "",
                    "dependencies": [],
                }
            ]
        }
        warnings = collect_schema_warnings(payload, str(tmp_path))
        assert any("missing required field 'priority'" in w for w in warnings)

    def test_empty_tasks_list(self, tmp_path) -> None:
        # Empty task list returns no warnings (no invalid content to report)
        warnings = collect_schema_warnings({"tasks": []}, str(tmp_path))
        assert warnings == []

    def test_non_list_tasks(self, tmp_path) -> None:
        warnings = collect_schema_warnings({"tasks": "bad"}, str(tmp_path))
        assert any("not a list" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# _looks_like_tool_call_output
# ---------------------------------------------------------------------------


class TestLooksLikeToolCallOutput:
    def test_xml_markers(self) -> None:
        assert _looks_like_tool_call_output("[TOOL_CALL]call") is True
        assert _looks_like_tool_call_output("</tool_call>") is True
        assert _looks_like_tool_call_output("<tool_call>call</tool_call>") is True

    def test_function_call_markers(self) -> None:
        assert _looks_like_tool_call_output("[function_call]fn") is True
        assert _looks_like_tool_call_output("</function_call>") is True

    def test_object_markers(self) -> None:
        assert _looks_like_tool_call_output("tool_calls: []") is True
        assert _looks_like_tool_call_output("function_calls: []") is True

    def test_empty(self) -> None:
        assert _looks_like_tool_call_output("") is False
        assert _looks_like_tool_call_output("   ") is False

    def test_normal_text(self) -> None:
        assert _looks_like_tool_call_output("Here is my plan") is False


# ---------------------------------------------------------------------------
# _strip_llm_xml_tags
# ---------------------------------------------------------------------------


class TestStripLlmXmlTags:
    def test_strips_tool_call_tags(self) -> None:
        # Content between tags is also removed entirely
        result = _strip_llm_xml_tags("<tool_call>call</tool_call>result")
        assert "<tool_call>" not in result
        assert "call" not in result  # content between tags removed
        assert "result" in result

    def test_strips_invoke_tags(self) -> None:
        result = _strip_llm_xml_tags("<invoke>content</invoke>")
        assert "<invoke>" not in result
        assert "content" not in result  # content between tags removed

    def test_preserves_content(self) -> None:
        result = _strip_llm_xml_tags("Before <tool_call>x</tool_call> After")
        assert "Before" in result
        assert "After" in result


# ---------------------------------------------------------------------------
# format_json_for_prompt
# ---------------------------------------------------------------------------


class TestFormatJsonForPrompt:
    def test_dict(self) -> None:
        result = format_json_for_prompt({"key": "val"})
        assert '"key"' in result
        assert "val" in result

    def test_none(self) -> None:
        assert format_json_for_prompt(None) == "none"

    def test_truncation(self) -> None:
        big = {"key": "v" * 5000}
        result = format_json_for_prompt(big, max_chars=200)
        assert len(result) < 5000
        assert result.endswith("...")

    def test_max_chars_zero_disables_truncation(self) -> None:
        result = format_json_for_prompt({"k": "v" * 10000}, max_chars=0)
        assert len(result) > 200


# ---------------------------------------------------------------------------
# _extract_json_from_llm_output
# ---------------------------------------------------------------------------


def test_extract_json_direct() -> None:
    from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
        _extract_json_from_llm_output,
    )

    raw = '{"tasks": [{"id": "T01", "title": "Do it"}], "overall_goal": "goal"}'
    result = _extract_json_from_llm_output(raw)
    assert result is not None
    assert result["overall_goal"] == "goal"
    assert len(result["tasks"]) == 1


def test_extract_json_in_fence() -> None:
    from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
        _extract_json_from_llm_output,
    )

    raw = 'Here is the plan:\n```json\n{"tasks": []}\n```\nDone.'
    result = _extract_json_from_llm_output(raw)
    assert result is not None
    assert "tasks" in result


def test_extract_json_in_text() -> None:
    from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
        _extract_json_from_llm_output,
    )

    raw = 'Answer: {"tasks": [{"id":"T01"}], "overall_goal": "impl auth"}\nThanks!'
    result = _extract_json_from_llm_output(raw)
    assert result is not None
    assert result["overall_goal"] == "impl auth"


def test_extract_json_strips_tool_tags() -> None:
    from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
        _extract_json_from_llm_output,
    )

    raw = '<tool_call>x</tool_call>{"tasks": []}<invoke>y</invoke>'
    result = _extract_json_from_llm_output(raw)
    assert result is not None
    assert "tasks" in result


def test_extract_json_empty_returns_none() -> None:
    from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
        _extract_json_from_llm_output,
    )

    assert _extract_json_from_llm_output("") is None
    assert _extract_json_from_llm_output("no json here") is None


# ---------------------------------------------------------------------------
# PmBackendInvokeResult
# ---------------------------------------------------------------------------


class TestPmBackendInvokeResult:
    def test_construction(self) -> None:
        r = PmBackendInvokeResult(output="json output", ok=True)
        assert r.output == "json output"
        assert r.ok is True
        assert r.error is None

    def test_with_error(self) -> None:
        r = PmBackendInvokeResult(output="", ok=False, error="timeout")
        assert r.ok is False
        assert r.error == "timeout"


# ---------------------------------------------------------------------------
# NoopPmStatePort
# ---------------------------------------------------------------------------


class TestNoopPmStatePort:
    def test_all_properties_return_empty(self) -> None:
        port = NoopPmStatePort()
        assert port.workspace_full == ""
        assert port.model == ""
        assert port.show_output is False
        assert port.timeout == 0
        assert port.prompt_profile == ""
        assert port.ollama_full == ""
        assert port.events_full == ""
        assert port.log_full == ""
        assert port.llm_events_full == ""

    def test_is_protocol_compatible(self) -> None:
        port = NoopPmStatePort()
        assert isinstance(port, PmStatePort)


# ---------------------------------------------------------------------------
# NoopPmInvokePort
# ---------------------------------------------------------------------------


class TestNoopPmInvokePort:
    def test_build_prompt_returns_requirements(self) -> None:
        port = NoopPmInvokePort()
        result = port.build_prompt("req", "plan", "gap", "qa", [], {}, {}, 0)
        assert result == "req"

    def test_extract_json_parses(self) -> None:
        port = NoopPmInvokePort()
        result = port.extract_json('{"key": "val"}')
        assert result == {"key": "val"}

    def test_extract_json_invalid_returns_none(self) -> None:
        port = NoopPmInvokePort()
        assert port.extract_json("not json") is None

    def test_invoke_raises(self) -> None:
        port = NoopPmInvokePort()
        with pytest.raises(RuntimeError, match="NoopPmInvokePort"):
            port.invoke(None, "p", "kind", None, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CellPmInvokePort (smoke test — just checks it instantiates)
# ---------------------------------------------------------------------------


def test_cell_pm_invoke_port_instantiates() -> None:
    port = CellPmInvokePort()
    assert port is not None


def test_cell_pm_invoke_port_build_prompt_includes_card3d_domain_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setattr(
        "polaris.kernelone.memory.integration.get_anthropomorphic_context",
        lambda *args, **kwargs: {
            "persona_instruction": "",
            "anthropomorphic_context": "",
            "prompt_context_obj": SimpleNamespace(model_dump=lambda: {}),
        },
    )
    monkeypatch.setattr(
        "polaris.kernelone.prompts.meta_prompting.build_meta_prompting_appendix",
        lambda *args, **kwargs: "",
    )

    prompt = CellPmInvokePort().build_prompt(
        requirements="构建多人在线创意卡牌游戏，前端 TypeScript + Three.js，后端 Node.js。",
        plan_text="Card3D client, Node.js server, WebSocket realtime sync, rooms, lobby, rules, and tests.",
        gap_report="",
        last_qa="",
        last_tasks=[],
        director_result={},
        pm_state={},
        workspace_root=str(tmp_path),
    )

    assert "Card3D multiplayer decomposition override" in prompt
    assert "CARD3D HARD CONTRACT" in prompt
    assert "禁止 1-3 小批量" in prompt
    assert "do not output audit reports" in prompt.lower()
    assert "This overrides the default 1-3 task batch limit" in prompt
    assert f"Required task count: at least {len(_CARD3D_PM_REQUIRED_DOMAINS)}." in prompt
    assert "PM-CARD3D-CLIENT3D-01" in prompt
    assert "PM-CARD3D-TESTS-22" in prompt


def test_cell_pm_invoke_port_normalizes_ollama_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                output='{"tasks": []}',
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )
    port = CellPmInvokePort()

    output = port.invoke(NoopPmStatePort(), "prompt", "ollama", SimpleNamespace(), None)

    assert output == '{"tasks": []}'
    command = captured["command"]
    assert command.metadata["requested_backend"] == "ollama"
    assert command.metadata["allowed_provider_types"] == ("ollama",)
    assert command.context["llm_provider_policy"]["allowed_provider_types"] == ("ollama",)


def test_cell_pm_invoke_port_propagates_timeout_to_role_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class TimedState(NoopPmStatePort):
        @property
        def timeout(self) -> int:
            return 300

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                output='{"tasks": []}',
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )

    output = CellPmInvokePort().invoke(TimedState(), "prompt", "generic", SimpleNamespace(), None)

    assert output == '{"tasks": []}'
    command = captured["command"]
    assert command.timeout_seconds == 300
    assert command.context["_transaction_kernel_forced_tool_definitions"] == []
    assert command.context["_transaction_kernel_forced_tool_choice"] == "none"
    assert command.context["disable_internal_tool_rounds"] is True
    assert command.metadata["max_tokens"] == 16_000
    assert command.metadata["timeout_seconds"] == 300
    assert command.context["llm_max_tokens"] == 16_000
    assert command.context["max_output_tokens"] == 16_000
    assert command.context["max_tokens"] == 16_000
    assert command.context["llm_call_timeout_seconds"] == 300
    assert command.context["request_timeout_seconds"] == 300
    assert command.context["suppress_tool_policy_prompt"] is True
    assert command.context["suppress_working_memory_contract"] is True
    assert command.context["timeout_seconds"] == 300


def test_cell_pm_invoke_port_raises_on_ollama_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                error_message="provider_type_not_allowed:openai_compat",
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )
    port = CellPmInvokePort()

    with pytest.raises(RuntimeError, match="PM role runtime invocation failed: provider_type_not_allowed"):
        port.invoke(NoopPmStatePort(), "prompt", "ollama", SimpleNamespace(), None)


def test_cell_pm_invoke_port_codex_backend_uses_role_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                output='{"tasks": []}',
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )

    port = CellPmInvokePort()
    output = port.invoke(NoopPmStatePort(), "prompt", "codex", SimpleNamespace(), None)

    assert output == '{"tasks": []}'
    command = captured["command"]
    assert command.metadata["requested_backend"] == "codex"
    assert command.metadata["allowed_provider_types"] == ("codex", "codex_cli", "codex_sdk")
    assert command.context["llm_provider_policy"]["allowed_provider_types"] == (
        "codex",
        "codex_cli",
        "codex_sdk",
    )


def test_cell_pm_invoke_port_does_not_pass_stale_state_model_as_runtime_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class StateWithStaleModel(NoopPmStatePort):
        @property
        def workspace_full(self) -> str:
            return "."

        @property
        def model(self) -> str:
            return "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest"

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                output='{"tasks": []}',
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )

    port = CellPmInvokePort()
    output = port.invoke(StateWithStaleModel(), "prompt", "generic", SimpleNamespace(), None)

    assert output == '{"tasks": []}'
    command = captured["command"]
    assert command.role == "pm"
    assert command.domain == "document"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True


def test_cell_pm_invoke_port_generic_uses_role_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                output='{"tasks": []}',
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )

    port = CellPmInvokePort()
    output = port.invoke(NoopPmStatePort(), "prompt", "generic", SimpleNamespace(), None)

    assert output == '{"tasks": []}'
    command = captured["command"]
    assert command.role == "pm"
    assert command.host_kind == "pm_planning_pipeline"
    assert command.stream is False


def test_cell_pm_invoke_port_reports_missing_runtime_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                error_message="provider/model missing",
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )

    port = CellPmInvokePort()

    with pytest.raises(RuntimeError, match="PM role runtime invocation failed: provider/model missing"):
        port.invoke(NoopPmStatePort(), "prompt", "generic", SimpleNamespace(), None)


def test_cell_pm_invoke_port_reports_runtime_provider_invocation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="pm",
                workspace=".",
                session_id=command.session_id,
                error_message="429 insufficient_quota",
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )

    port = CellPmInvokePort()

    with pytest.raises(RuntimeError, match="PM role runtime invocation failed: 429 insufficient_quota"):
        port.invoke(NoopPmStatePort(), "prompt", "generic", SimpleNamespace(), None)
