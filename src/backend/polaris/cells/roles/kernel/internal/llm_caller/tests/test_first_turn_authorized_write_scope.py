"""First-turn tool hints cannot turn reference scope into write permission."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.tool_policy import _apply_forced_transaction_tool_definitions
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    ensure_director_first_call_materialization_scope,
    extract_write_tool_pin_target_files,
    resolve_missing_materialization_write_targets,
)


def _context() -> dict[str, Any]:
    return {
        "target_files": ["index.html", "src/entry/browser-main.ts"],
        "scope_paths": ["index.html", "src/engine/renderer.ts"],
        "execution_envelope": {
            "authorization": {
                "allowed_write_paths": ["index.html", "src/entry/browser-main.ts"],
                "required_write_targets": [],
            }
        },
    }


def test_missing_targets_exclude_reference_only_scope(tmp_path: Path) -> None:
    assert resolve_missing_materialization_write_targets(_context(), str(tmp_path)) == [
        "index.html",
        "src/entry/browser-main.ts",
    ]


def test_schema_pinning_rechecks_authority_for_supplied_target_candidates(tmp_path: Path) -> None:
    context = _context()
    original_authority = deepcopy(context["execution_envelope"])
    tools = ensure_director_first_call_materialization_scope(
        role="director",
        context_override=context,
        workspace=str(tmp_path),
        tool_definitions=[],
        from_scratch_target="index.html",
        from_scratch_targets=["index.html", "src/entry/browser-main.ts", "src/engine/renderer.ts"],
        materialize_requested=True,
        transaction_tools_disabled=False,
    )
    properties = tools[0]["function"]["parameters"]["properties"]
    expected = {"index.html", "./index.html", "src/entry/browser-main.ts", "./src/entry/browser-main.ts"}
    assert set(properties["file"]["enum"]) == expected
    for alias in ("file=path", "filePath", "path"):
        if alias in properties:
            assert set(properties[alias]["enum"]) == expected
    assert context["execution_envelope"] == original_authority


def test_explicit_empty_write_authority_never_forces_write_schema(tmp_path: Path) -> None:
    context = _context()
    context["execution_envelope"]["authorization"]["allowed_write_paths"] = []
    assert resolve_missing_materialization_write_targets(context, str(tmp_path)) == []
    assert (
        ensure_director_first_call_materialization_scope(
            role="director",
            context_override=context,
            workspace=str(tmp_path),
            tool_definitions=[],
            from_scratch_target="index.html",
            from_scratch_targets=["index.html"],
            materialize_requested=True,
            transaction_tools_disabled=False,
        )
        == []
    )
    assert "_transaction_kernel_forced_tool_definitions" not in context


def test_malformed_present_authority_is_not_replaced_by_inventory(tmp_path: Path) -> None:
    context = _context()
    context["execution_envelope"] = {"authorization": {}}
    with pytest.raises(ValueError, match="task_write_guidance_authorization_missing"):
        resolve_missing_materialization_write_targets(context, str(tmp_path))


def test_existing_required_target_is_not_reintroduced_as_missing(tmp_path: Path) -> None:
    context = _context()
    context["execution_envelope"]["authorization"]["required_write_targets"] = ["index.html"]
    (tmp_path / "index.html").write_text("existing verified artifact", encoding="utf-8")
    missing = resolve_missing_materialization_write_targets(context, str(tmp_path))

    assert missing == ["src/entry/browser-main.ts"]
    tools = ensure_director_first_call_materialization_scope(
        role="director",
        context_override=context,
        workspace=str(tmp_path),
        tool_definitions=[],
        from_scratch_target=missing[0],
        from_scratch_targets=missing,
        materialize_requested=True,
        transaction_tools_disabled=False,
    )
    assert set(tools[0]["function"]["parameters"]["properties"]["file"]["enum"]) == {
        "src/entry/browser-main.ts",
        "./src/entry/browser-main.ts",
    }
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "existing verified artifact"


@pytest.mark.parametrize("source", ["construction_step", "director_quality_repair"])
def test_explicit_single_target_cannot_pin_reference_scope(source: str) -> None:
    context = _context()
    target = {"target_file": "src/engine/renderer.ts"}
    context[source] = target if source == "construction_step" else {"write_only_single_target": target}
    with pytest.raises(ValueError, match="director_write_target_outside_authorized_scope"):
        extract_write_tool_pin_target_files(context)


@pytest.mark.parametrize(
    "target", ["src/engine/renderer.ts", "src\\engine\\renderer.ts", "src/engine/renderer.ts\nother.ts"]
)
def test_final_forced_tool_merge_rejects_foreign_quality_target(target: str) -> None:
    context = _context()
    context["director_quality_repair"] = {"write_only_single_target": {"target_file": target}}
    context["_transaction_kernel_forced_tool_definitions"] = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {
                    "type": "object",
                    "properties": {"file": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["file", "content"],
                },
            },
        }
    ]
    with pytest.raises(ValueError, match="director_write_target_outside_authorized_scope"):
        _apply_forced_transaction_tool_definitions([], context)


@pytest.mark.parametrize("source", ["construction_step", "director_quality_repair"])
def test_authorized_single_target_alias_remains_pinnable(source: str) -> None:
    context = _context()
    target = {"target_file": "./src/entry/browser-main.ts"}
    context[source] = target if source == "construction_step" else {"write_only_single_target": target}
    assert extract_write_tool_pin_target_files(context) == ("./src/entry/browser-main.ts",)


def test_explicit_empty_authority_cannot_degrade_single_target_to_broad_write() -> None:
    context = _context()
    context["execution_envelope"]["authorization"]["allowed_write_paths"] = []
    context["construction_step"] = {"target_file": "index.html"}
    with pytest.raises(ValueError, match="director_write_target_outside_authorized_scope"):
        extract_write_tool_pin_target_files(context)


@pytest.mark.parametrize("target", ["src\\engine\\renderer.ts", "src/engine/renderer.ts\nother.ts"])
def test_malformed_foreign_pin_does_not_bypass_authority(target: str) -> None:
    context = _context()
    context["director_quality_repair"] = {"write_only_single_target": {"target_file": target}}
    with pytest.raises(ValueError, match="director_write_target_outside_authorized_scope"):
        extract_write_tool_pin_target_files(context)


def test_admitted_windows_path_is_normalized_before_single_target_pinning() -> None:
    context = _context()
    context["construction_step"] = {"target_file": "src\\entry\\browser-main.ts"}
    assert extract_write_tool_pin_target_files(context) == ("src/entry/browser-main.ts", "./src/entry/browser-main.ts")


def test_quality_target_list_fallback_cannot_pin_foreign_path() -> None:
    context = _context()
    context["director_quality_repair"] = {"repair_target_files": ["src/engine/renderer.ts"]}
    with pytest.raises(ValueError, match="director_write_target_outside_authorized_scope"):
        extract_write_tool_pin_target_files(context)


def test_scope_pattern_cannot_become_an_unrestricted_single_file_target() -> None:
    context = _context()
    context["execution_envelope"]["authorization"]["allowed_write_paths"] = ["src/**"]
    context["construction_step"] = {"target_file": "src/**"}
    with pytest.raises(ValueError, match="director_write_target_invalid"):
        extract_write_tool_pin_target_files(context)
