"""R127 Forced Tool Surface SSOT gates."""

from __future__ import annotations

import pytest
from polaris.kernelone.tool_execution.forced_tool_surface import (
    ForcedToolSurfaceError,
    assert_registry_faithful_tool_surface,
    build_forced_tool_surface,
    pin_write_file_paths,
    resolve_registry_tool_schema,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def test_resolve_registry_tool_schema_matches_tool_spec_registry() -> None:
    for name in ("write_file", "edit_file", "execute_command", "read_file"):
        actual = resolve_registry_tool_schema(name)
        expected = ToolSpecRegistry.get_llm_schema(
            name,
            include_arg_aliases=True,
            deterministic=True,
        )
        assert expected is not None
        assert actual == expected


def test_build_forced_tool_surface_quality_repair_set_is_registry_faithful() -> None:
    tools = build_forced_tool_surface(("edit_file", "write_file", "execute_command"))
    names = [item["function"]["name"] for item in tools]
    assert names == ["edit_file", "write_file", "execute_command"]
    assert_registry_faithful_tool_surface(tools)


def test_pin_write_file_paths_only_authorized_on_write_file() -> None:
    write = resolve_registry_tool_schema("write_file")
    pinned = pin_write_file_paths(write, ["src/a.ts", "src/b.ts"])
    props = pinned["function"]["parameters"]["properties"]
    assert props["file"]["enum"] == ["src/a.ts", "src/b.ts"]
    assert props["path"]["enum"] == ["src/a.ts", "src/b.ts"]
    # Faithful except path enums.
    assert_registry_faithful_tool_surface([pinned], allow_write_file_path_enum=True)

    edit = resolve_registry_tool_schema("edit_file")
    with pytest.raises(ForcedToolSurfaceError) as exc:
        pin_write_file_paths(edit, ["src/a.ts"])
    assert exc.value.code == "forced_tool_path_pin_unauthorized"


def test_build_forced_tool_surface_pins_only_write_file() -> None:
    tools = build_forced_tool_surface(
        ("write_file", "edit_file"),
        pin_write_paths=["src/main.ts"],
    )
    by_name = {item["function"]["name"]: item for item in tools}
    assert by_name["write_file"]["function"]["parameters"]["properties"]["file"]["enum"] == ["src/main.ts"]
    assert "enum" not in by_name["edit_file"]["function"]["parameters"]["properties"]["file"]


def test_unknown_tool_fails_closed() -> None:
    with pytest.raises(ForcedToolSurfaceError) as exc:
        resolve_registry_tool_schema("definitely_not_a_registered_tool_xyz")
    assert exc.value.code == "tool_registry_contract_missing"
