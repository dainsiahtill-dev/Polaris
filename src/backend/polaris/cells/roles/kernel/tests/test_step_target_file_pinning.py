"""Fix-11: pin a fission step's declared target_file into write-tool schemas.

Live I3-r9: on its FIRST attempt the Director wrote index.html for a readme.md
step and the run false-resolved — prompt-level steering is exactly what weak
models ignore, but a guided-decoding ``enum`` on the ``file`` parameter cannot
be ignored (same mechanism that made W1.10's line-range narrowing stick).
Pinning happens where ``build_native_tool_schemas`` output enters the turn, so
the escalation ladder's strict/forced subsets inherit it automatically.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    extract_declared_step_target_files,
    pin_write_tool_file_param_to_targets,
)
from polaris.cells.roles.kernel.internal.transaction.retry_escalation_policy import (
    narrow_edit_blocks_schema_to_line_range,
)

_DEFS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "write",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "path"},
                    "content": {"type": "string"},
                },
                "required": ["file", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_rg",
            "description": "search",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_blocks",
            "description": "edit",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "blocks": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "replace": {"type": "string"},
                },
            },
        },
    },
]


class TestExtractDeclaredStepTargetFiles:
    def test_non_step_context_yields_nothing(self) -> None:
        assert extract_declared_step_target_files(None) == ()
        assert extract_declared_step_target_files({}) == ()
        assert extract_declared_step_target_files({"construction_step": "S1"}) == ()
        assert extract_declared_step_target_files({"construction_step": {"step_id": "S1"}}) == ()

    def test_bare_target_yields_both_enum_variants(self) -> None:
        override = {"construction_step": {"target_file": "style.css"}}
        assert extract_declared_step_target_files(override) == ("style.css", "./style.css")

    def test_prefixed_target_yields_single_variant(self) -> None:
        override = {"construction_step": {"target_file": "./main.js"}}
        assert extract_declared_step_target_files(override) == ("./main.js",)

    def test_malformed_targets_refuse_to_pin(self) -> None:
        """Adversarial review D-fix: a CE-authored glob/list/absolute target
        must never become a hard decode-grammar constraint — refusing to pin
        is the safe degradation (EXEC_TARGET_MISSING still backstops)."""
        for target in ("src/*.js", "a.js, b.js", "a.js b.js", "/etc/passwd", "~/x.js", "../escape.js", "a\\b.js"):
            override = {"construction_step": {"target_file": target}}
            assert extract_declared_step_target_files(override) == (), target

    def test_subdir_target_is_pinnable(self) -> None:
        override = {"construction_step": {"target_file": "src/game/main.js"}}
        assert extract_declared_step_target_files(override) == ("src/game/main.js", "./src/game/main.js")


class TestPinWriteToolFileParam:
    def test_pins_write_tools_and_leaves_read_tools_alone(self) -> None:
        pinned = pin_write_tool_file_param_to_targets(_DEFS, ("style.css", "./style.css"))

        write_file_schema = pinned[0]["function"]["parameters"]["properties"]["file"]
        edit_blocks_schema = pinned[2]["function"]["parameters"]["properties"]["file"]
        assert write_file_schema["enum"] == ["style.css", "./style.css"]
        assert edit_blocks_schema["enum"] == ["style.css", "./style.css"]
        assert pinned[1] == _DEFS[1]

    def test_source_definitions_are_not_mutated(self) -> None:
        pin_write_tool_file_param_to_targets(_DEFS, ("a.md",))

        assert "enum" not in _DEFS[0]["function"]["parameters"]["properties"]["file"]
        assert "enum" not in _DEFS[2]["function"]["parameters"]["properties"]["file"]

    def test_empty_targets_is_a_noop(self) -> None:
        assert pin_write_tool_file_param_to_targets(_DEFS, ()) is _DEFS

    def test_definition_without_file_property_passes_through(self) -> None:
        defs = [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "parameters": {"type": "object", "properties": {"content": {"type": "string"}}},
                },
            }
        ]
        assert pin_write_tool_file_param_to_targets(defs, ("a.md",)) == defs

    def test_non_dict_entries_pass_through(self) -> None:
        defs = ["garbage", {"function": None}]
        assert pin_write_tool_file_param_to_targets(defs, ("a.md",)) == defs

    def test_alias_path_properties_are_pinned_too(self) -> None:
        """Adversarial review B-fix: arg_aliases expand into REAL optional
        properties and the normalizer maps an alias into `file` when the
        canonical is absent — an unpinned `path` would be a wrong-file escape."""
        defs = [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string"},
                            "path": {"type": "string"},
                            "filepath": {"type": "string"},
                            "filePath": {"type": "string"},
                            "file_path": {"type": "string"},
                            "filename": {"type": "string"},
                            "target": {"type": "string"},
                            "target_file": {"type": "string"},
                            "targetFile": {"type": "string"},
                            "target_path": {"type": "string"},
                            "targetPath": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["file", "content"],
                    },
                },
            }
        ]
        pinned = pin_write_tool_file_param_to_targets(defs, ("a.md", "./a.md"))
        properties = pinned[0]["function"]["parameters"]["properties"]
        for name in (
            "file",
            "path",
            "filepath",
            "filePath",
            "file_path",
            "filename",
            "target",
            "target_file",
            "targetFile",
            "target_path",
            "targetPath",
        ):
            assert properties[name]["enum"] == ["a.md", "./a.md"], name
        assert "enum" not in properties["content"]


class TestPinSurvivesLineRangeNarrowing:
    def test_pinned_enum_survives_edit_blocks_narrowing(self) -> None:
        """The W1.10 wholesale parameters rewrite must carry the pinned enum —
        losing it at the most-forced attempt would reopen the wrong-file escape."""
        pinned = pin_write_tool_file_param_to_targets(_DEFS, ("main.js", "./main.js"))
        narrowed = narrow_edit_blocks_schema_to_line_range(pinned)

        parameters = narrowed[2]["function"]["parameters"]
        assert parameters["required"] == ["file", "start", "end", "replace"]
        assert "blocks" not in parameters["properties"]
        assert parameters["properties"]["file"]["enum"] == ["main.js", "./main.js"]

    def test_unpinned_narrowing_has_no_enum(self) -> None:
        narrowed = narrow_edit_blocks_schema_to_line_range(_DEFS)

        assert "enum" not in narrowed[2]["function"]["parameters"]["properties"]["file"]
