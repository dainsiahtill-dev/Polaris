"""Unit tests for orchestration.pm_planning internal pipeline_ports.

Tests all public pure functions: normalize_priority, normalize_engine_config,
normalize_pm_payload, collect_schema_warnings, _looks_like_tool_call_output,
format_json_for_prompt, _extract_json_from_llm_output, _strip_llm_xml_tags,
and protocol classes.
"""

from __future__ import annotations

from types import SimpleNamespace

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


def test_cell_pm_invoke_port_normalizes_ollama_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.process.ollama_utils import OllamaResponse

    def fake_invoke_ollama(*args: object, **kwargs: object) -> OllamaResponse:
        return OllamaResponse(output='{"tasks": []}')

    monkeypatch.setattr("polaris.kernelone.process.ollama_utils.invoke_ollama", fake_invoke_ollama)
    port = CellPmInvokePort()

    output = port.invoke(NoopPmStatePort(), "prompt", "ollama", SimpleNamespace(), None)

    assert output == '{"tasks": []}'


def test_cell_pm_invoke_port_raises_on_ollama_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.process.ollama_utils import OllamaMetadata, OllamaResponse

    def fake_invoke_ollama(*args: object, **kwargs: object) -> OllamaResponse:
        return OllamaResponse(
            output="",
            metadata=OllamaMetadata(error="request timed out", error_type="timeout"),
        )

    monkeypatch.setattr("polaris.kernelone.process.ollama_utils.invoke_ollama", fake_invoke_ollama)
    port = CellPmInvokePort()

    with pytest.raises(RuntimeError, match="Ollama PM backend failed: request timed out"):
        port.invoke(NoopPmStatePort(), "prompt", "ollama", SimpleNamespace(), None)


def test_cell_pm_invoke_port_does_not_pass_stale_state_model_as_runtime_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class StateWithStaleModel(NoopPmStatePort):
        @property
        def workspace_full(self) -> str:
            return "."

        @property
        def model(self) -> str:
            return "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest"

    def fake_invoke_role_runtime_provider(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(attempted=True, ok=True, output='{"tasks": []}', error="")

    monkeypatch.setattr(
        "polaris.kernelone.llm.runtime.invoke_role_runtime_provider",
        fake_invoke_role_runtime_provider,
    )

    port = CellPmInvokePort()
    output = port.invoke(StateWithStaleModel(), "prompt", "generic", SimpleNamespace(), None)

    assert output == '{"tasks": []}'
    assert captured["fallback_model"] == ""
