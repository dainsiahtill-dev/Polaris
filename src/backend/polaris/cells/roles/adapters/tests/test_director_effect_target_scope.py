"""Physical scope narrows an admitted effect to its immutable target."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director.execution_tools import (
    DirectorToolExecutionAuthorityError,
    _create_director_tool_executor,
)
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments_from_snapshot
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def test_admitted_directory_capability_writes_only_bound_effect_target(tmp_path: Path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("src/",), effect_target="src/a.py")

    accepted = executor.execute_tool("write_file", {"path": "src/a.py", "content": "value = 1\n"})
    denied = executor.execute_tool("write_file", {"path": "src/sibling.py", "content": "value = 2\n"})

    assert accepted["ok"] is True
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "value = 1\n"
    assert denied["ok"] is False
    assert denied["error_type"] == "director_write_policy_denied"
    assert not (tmp_path / "src/sibling.py").exists()


def test_bound_effect_target_does_not_override_workspace_agents_policy(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("禁止修改 src/private.py\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("src/",), effect_target="src/private.py")

    denied = executor.execute_tool("write_file", {"path": "src/private.py", "content": "value = 1\n"})

    assert denied["ok"] is False
    assert denied["error_type"] == "director_write_policy_denied"
    assert not (tmp_path / "src/private.py").exists()


@pytest.mark.parametrize("target", ("src/sibling.py", "../outside.py", "/tmp/outside.py"))
def test_effect_target_cannot_expand_capability_scope(tmp_path: Path, target: str) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    with pytest.raises(DirectorToolExecutionAuthorityError) as caught:
        executor._bind_authorized_scope(("src/a.py",), effect_target=target)

    assert caught.value.code == "deo_path_scope_denied"
    assert not (tmp_path / "src").exists()


@pytest.mark.parametrize("target", ("src/sibling.py", "outside.py", "AGENTS.md", ".polaris/runtime/state.json"))
@pytest.mark.parametrize("carrier", ["encoded_key", "assignment_alias"])
def test_decoded_write_envelope_still_cannot_expand_bound_effect(tmp_path: Path, target: str, carrier: str) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("src/",), effect_target="src/a.py")
    raw = (
        {"file/" + target + "</parameter": '{"value": 1}\n'}
        if carrier == "encoded_key"
        else {"file=path": target, "content": '{"value": 1}\n'}
    )
    arguments = normalize_tool_arguments_from_snapshot(ToolSpecRegistry.capture_effective_spec("write_file"), raw)

    denied = executor.execute_tool("write_file", arguments)

    assert denied["ok"] is False
    assert denied["error_type"] == "director_write_policy_denied"
    assert not (tmp_path / target).exists()


def test_decoded_write_envelope_preserves_literal_xml_in_authorized_body(tmp_path: Path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("src/",), effect_target="src/a.py")
    body = 'value = "<parameter=literal>keep</parameter>"\n'
    arguments = normalize_tool_arguments_from_snapshot(
        ToolSpecRegistry.capture_effective_spec("write_file"), {"file/src/a.py</parameter": body}
    )

    accepted = executor.execute_tool("write_file", arguments)

    assert accepted["ok"] is True
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == body


def test_assignment_alias_physically_writes_only_bound_target(tmp_path: Path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("src/",), effect_target="src/a.py")
    body = 'value = "file=path"\n'
    args = normalize_tool_arguments_from_snapshot(
        ToolSpecRegistry.capture_effective_spec("write_file"), {"file=path": "src/a.py", "content": body}
    )
    result = executor.execute_tool("write_file", args)
    assert result["ok"] is True
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == body
