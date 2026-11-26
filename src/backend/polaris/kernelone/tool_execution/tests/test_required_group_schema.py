"""The wire must express the same conjunction of alternatives as tool normalization."""

from itertools import combinations
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


@pytest.mark.parametrize("name", ["read_file", "repo_read_around", "edit_blocks", "repo_apply_diff"])
def test_native_required_keys_match_runtime_groups(name: str) -> None:
    spec = ToolSpecRegistry.get_all_specs()[name]
    native = ToolSpecRegistry.get_llm_schema(name)
    assert native is not None
    # Value validation, aliases and host permission are tested at their own boundaries.
    presence = {k: v for k, v in native["function"]["parameters"].items() if k in {"required", "anyOf", "allOf"}}
    validator = Draft202012Validator(presence)
    groups = [set(g) for g in spec["required_any"]]
    always = {a["name"] for a in spec["arguments"] if a.get("required") is True}
    keys = sorted(always.union(*groups))
    for count in range(len(keys) + 1):
        for chosen in combinations(keys, count):
            args: dict[str, Any] = dict.fromkeys(chosen, True)
            assert validator.is_valid(args) == (always <= args.keys() and all(g & args.keys() for g in groups)), chosen
