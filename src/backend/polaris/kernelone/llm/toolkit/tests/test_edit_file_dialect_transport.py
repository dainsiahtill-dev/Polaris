"""Native edit dialects must keep old bytes, new bytes and occurrence intent."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.adapters.internal.director.execution_tools import _create_director_tool_executor
from polaris.cells.roles.kernel.internal.llm_caller.final_request_tool_surface import (
    assert_tool_in_final_request_surface,
)
from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_edit_file
from polaris.kernelone.llm.toolkit.tool_normalization import (
    normalize_tool_arguments,
    normalize_tool_arguments_from_snapshot,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def _admit(arguments: dict[str, object]) -> None:
    request = SimpleNamespace(options={"tools": [{"type": "function", "function": {"name": "edit_file"}}]})
    assert_tool_in_final_request_surface(
        tool_name="edit_file", tool_arguments=arguments,
        active_request=request, prepared=SimpleNamespace(request_options={}),
    )


@pytest.mark.parametrize("dialect", ["old_new_all", "replace_old_new_code"])
def test_native_dialect_edits_real_file_without_losing_new_body(tmp_path: Path, dialect: str) -> None:
    old = "".join(f"value_{i} = {i}\n" for i in range(172))
    new = old.replace("value_3 = 3", "value_3 = 33")
    target = tmp_path / "module.py"
    target.write_text(old, encoding="utf-8")
    arguments: dict[str, object] = (
        {"file": "module.py", "oldText": old, "newText": new, "replace": "all"}
        if dialect == "old_new_all"
        else {"file": "module.py", "replace": old, "newCode": new, "regex": False}
    )
    snapshot = ToolSpecRegistry.capture_effective_spec("edit_file")
    normalized = normalize_tool_arguments_from_snapshot(snapshot, arguments)
    assert normalized["search"] == old
    assert normalized["replace"] == new
    assert arguments.get("newText", arguments.get("newCode")) == new
    _admit(arguments)
    result = _handle_edit_file(AgentAccelToolExecutor(workspace=str(tmp_path)), **normalized)
    assert result["ok"] is True, result
    assert result["effect_receipt"]["operation"] == "modify"
    assert target.read_text(encoding="utf-8") == new


def test_all_occurrences_intent_is_honored(tmp_path: Path) -> None:
    target = tmp_path / "values.txt"
    target.write_text("old\nold\n", encoding="utf-8")
    normalized = normalize_tool_arguments("edit_file", {
        "file": "values.txt", "oldText": "old", "newText": "new", "replace": "all",
    })
    result = _handle_edit_file(AgentAccelToolExecutor(workspace=str(tmp_path)), **normalized)
    assert result["ok"] is True, result
    assert target.read_text(encoding="utf-8") == "new\nnew\n"
    assert result["replacements_count"] == 2


@pytest.mark.parametrize("arguments", [
    {"file": "x.py", "search": "old", "replace": "one", "newText": "two"},
    {"file": "x.py", "oldText": "old", "newText": "one", "newCode": "two"},
    {"file": "x.py", "search": "old", "oldText": "other", "newText": "new"},
])
def test_conflicting_edit_bodies_are_rejected(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="conflicting_edit_argument"):
        normalize_tool_arguments("edit_file", arguments)
    with pytest.raises(RuntimeError, match="conflicting_edit_argument"):
        _admit(arguments)


def test_new_body_without_old_witness_is_still_rejected() -> None:
    with pytest.raises(RuntimeError, match="missing_edit_mode"):
        _admit({"file": "x.py", "newCode": "value = 2\n"})


def test_literal_all_replacement_and_default_single_occurrence_stay_literal(tmp_path: Path) -> None:
    target = tmp_path / "literal.txt"
    target.write_text("old\nold\n", encoding="utf-8")
    normalized = normalize_tool_arguments("edit_file", {"file": "literal.txt", "search": "old", "replace": "all"})
    result = _handle_edit_file(AgentAccelToolExecutor(workspace=str(tmp_path)), **normalized)
    assert result["ok"] is True, result
    assert target.read_text(encoding="utf-8") == "all\nold\n"


def test_equal_literal_all_alias_does_not_change_occurrence_intent() -> None:
    normalized = normalize_tool_arguments("edit_file", {
        "file": "x.py", "search": "old", "replace": "all", "newText": "all", "replace_all": False,
    })
    assert normalized["replace"] == "all"
    assert normalized["replace_all"] is False


def test_inverted_regex_dialect_is_not_inferred() -> None:
    with pytest.raises(ValueError, match="ambiguous_edit_argument"):
        normalize_tool_arguments("edit_file", {"file": "x.py", "replace": ".*", "newCode": "new", "regex": True})


def test_all_dialect_does_not_fuzzily_apply_stale_old_body(tmp_path: Path) -> None:
    target = tmp_path / "stale.txt"
    target.write_text("current bytes\n", encoding="utf-8")
    normalized = normalize_tool_arguments("edit_file", {
        "file": "stale.txt", "oldText": "current  bytes", "newText": "new bytes", "replace": "all",
    })
    result = _handle_edit_file(AgentAccelToolExecutor(workspace=str(tmp_path)), **normalized)
    assert result["ok"] is False, result
    assert "effect_receipt" not in result
    assert target.read_text(encoding="utf-8") == "current bytes\n"


def test_destructive_shrink_guard_still_rejects_real_short_body(tmp_path: Path) -> None:
    target = tmp_path / "guard.py"
    old = "".join(f"value_{i} = {i}\n" for i in range(172))
    target.write_text(old, encoding="utf-8")
    normalized = normalize_tool_arguments("edit_file", {
        "file": "guard.py", "oldText": old, "newText": "x = 1\n", "replace": "all",
    })
    executor = _create_director_tool_executor(str(tmp_path))
    result = executor._tool_edit_file(normalized, tmp_path)
    assert result["ok"] is False, result
    assert "Destructive shrink" in result["error"]
    assert target.read_text(encoding="utf-8") == old


def test_director_executes_all_occurrences_consistently(tmp_path: Path) -> None:
    target = tmp_path / "values.txt"
    target.write_text("old\nold\n", encoding="utf-8")
    normalized = normalize_tool_arguments("edit_file", {
        "file": "values.txt", "oldText": "old", "newText": "new", "replace": "all",
    })
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("values.txt",))
    result = executor._tool_edit_file(normalized, tmp_path)
    assert result["ok"] is True, result
    assert target.read_text(encoding="utf-8") == "new\nnew\n"
    assert result["replacements"] == 2
