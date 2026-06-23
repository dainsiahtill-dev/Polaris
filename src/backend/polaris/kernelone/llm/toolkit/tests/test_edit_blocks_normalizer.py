"""Tests for the edit_blocks weak-model arg-healing normalizer.

Weak local models (gemma, etc.) emit the ``blocks`` argument as a JSON list
instead of the canonical SEARCH/REPLACE string. The normalizer coerces those
shapes so the call survives schema validation and reaches the handler.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.editing.editblock_engine import parse_edit_blocks
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments
from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._edit_blocks import (
    normalize_edit_blocks_args,
)


class TestEditBlocksNormalizer:
    def test_dict_list_coerced_to_parseable_blocks(self) -> None:
        args = normalize_edit_blocks_args(
            {"file": "a.py", "blocks": [{"search": "def old():\n    pass", "replace": "def new():\n    return 1"}]}
        )
        assert isinstance(args["blocks"], str)
        assert len(parse_edit_blocks(args["blocks"], default_filepath="a.py")) == 1

    def test_alt_key_names_parse(self) -> None:
        for search_key, replace_key in (
            ("old", "new"),
            ("old_string", "new_string"),
            ("before", "after"),
            ("search_text", "replace_text"),
        ):
            args = normalize_edit_blocks_args({"blocks": [{search_key: "X", replace_key: "Y", "file": "z.py"}]})
            assert isinstance(args["blocks"], str)
            assert len(parse_edit_blocks(args["blocks"], default_filepath="z.py")) == 1

    def test_contract_target_file_key_inside_block_parse(self) -> None:
        args = normalize_edit_blocks_args({"blocks": [{"target_file": "z.py", "search": "X", "replace": "Y"}]})

        assert isinstance(args["blocks"], str)
        assert "SEARCH:z.py" in args["blocks"]
        assert len(parse_edit_blocks(args["blocks"], default_filepath="fallback.py")) == 1

    def test_string_list_joined(self) -> None:
        args = normalize_edit_blocks_args({"blocks": ["<<<< SEARCH:f.py", "x", "====", "y", ">>>> REPLACE"]})
        assert isinstance(args["blocks"], str)
        assert "SEARCH" in args["blocks"]

    def test_plain_string_unchanged(self) -> None:
        text = "<<<< SEARCH:f.py\nx\n====\ny\n>>>> REPLACE"
        assert normalize_edit_blocks_args({"blocks": text})["blocks"] == text

    def test_file_marker_text_shape_populates_file_and_blocks(self) -> None:
        body = "export const firefly = 1;\nexport const flower = 2;\n"
        args = normalize_edit_blocks_args({"blocks": f"FILE: src/engine/simulation.ts\n{body}"})

        assert args["file"] == "src/engine/simulation.ts"
        assert args["blocks"] == body
        assert args["normalized_from_file_marker"] is True

    def test_file_marker_two_line_header_populates_file_and_blocks(self) -> None:
        body = "export const moon = 'full';\nexport const humidity = 0.7;\n"
        args = normalize_tool_arguments("edit_blocks", {"blocks": f"FILE:\nsrc/engine/simulation.ts\n{body}"})

        assert args["file"] == "src/engine/simulation.ts"
        assert args["blocks"] == body
        assert args["normalized_from_file_marker"] is True

    def test_file_marker_inside_fence_populates_file_and_blocks(self) -> None:
        body = "export const firefly = true;\n"
        args = normalize_tool_arguments("edit_blocks", {"blocks": f"```typescript\nfile: src/main.ts\n{body}```"})

        assert args["file"] == "src/main.ts"
        assert args["blocks"].strip() == body.strip()
        assert args["normalized_from_file_marker"] is True

    def test_python_tuple_line_range_string_maps_to_canonical_args(self) -> None:
        args = normalize_edit_blocks_args(
            {
                "blocks": (
                    "('services/__init__.py', '1', '145', "
                    "'from flask import Flask\\n\\ndef create_app():\\n    return Flask(__name__)\\n')"
                )
            }
        )

        assert args["file"] == "services/__init__.py"
        assert args["start"] == 1
        assert args["end"] == 145
        assert args["replace"] == "from flask import Flask\n\ndef create_app():\n    return Flask(__name__)\n"
        assert "blocks" not in args

    def test_public_normalizer_accepts_python_tuple_line_range_string(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {"blocks": '("app.py", "2", "3", "def main():\\n    return 0\\n")'},
        )

        assert args == {
            "file": "app.py",
            "start": 2,
            "end": 3,
            "replace": "def main():\n    return 0\n",
        }

    def test_public_normalizer_accepts_json_object_line_range_string(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {
                "blocks": (
                    '{"file": "services/__init__.py", "start": 37, "end": 57, '
                    '"replace": "def create_app(config=None):\\n    return app\\n"}'
                )
            },
        )

        assert args == {
            "file": "services/__init__.py",
            "start": 37,
            "end": 57,
            "replace": "def create_app(config=None):\n    return app\n",
        }

    def test_public_normalizer_accepts_jsonish_line_range_string_with_unescaped_docstring(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {
                "blocks": (
                    '{ "file": "services/__init__.py", "start": 27, "end": 57, '
                    '"replace": "def create_app(config=None):\n'
                    '    """Application factory."""\n'
                    "    return app\n"
                    '" }'
                )
            },
        )

        assert args == {
            "file": "services/__init__.py",
            "start": 27,
            "end": 57,
            "replace": 'def create_app(config=None):\n    """Application factory."""\n    return app\n',
        }

    def test_public_normalizer_accepts_nested_edits_line_range_string(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {
                "blocks": (
                    '{"edits": [{"path": "services/order_service/routes.py", '
                    '"start": 52, "end": 54, '
                    '"replace": "    for field in required_fields:\\n        validate(field)\\n"}]}'
                )
            },
        )

        assert args == {
            "file": "services/order_service/routes.py",
            "start": 52,
            "end": 54,
            "replace": "    for field in required_fields:\n        validate(field)\n",
        }

    def test_public_normalizer_accepts_nested_edits_line_range_mapping(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {
                "blocks": {
                    "edits": [
                        {
                            "path": "services/order_service/routes.py",
                            "start": "52",
                            "end": "54",
                            "replace": "    for field in required_fields:\n        validate(field)\n",
                        }
                    ]
                }
            },
        )

        assert args == {
            "file": "services/order_service/routes.py",
            "start": 52,
            "end": 54,
            "replace": "    for field in required_fields:\n        validate(field)\n",
        }

    def test_nested_edits_multiple_line_ranges_stays_fail_closed(self) -> None:
        payload = {
            "blocks": {
                "edits": [
                    {"path": "a.py", "start": 1, "end": 1, "replace": "a = 1\n"},
                    {"path": "b.py", "start": 1, "end": 1, "replace": "b = 1\n"},
                ]
            }
        }

        assert normalize_tool_arguments("edit_blocks", payload) == payload

    def test_public_normalizer_accepts_label_style_line_range_string(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {
                "blocks": (
                    "file_path: services/__init__.py\n"
                    "start_line: 27\n"
                    "end_line: 57\n"
                    "replace: def create_app(config=None):\n"
                    "    return app\n"
                )
            },
        )

        assert args == {
            "file": "services/__init__.py",
            "start": 27,
            "end": 57,
            "replace": "def create_app(config=None):\n    return app\n",
        }

    def test_public_normalizer_accepts_line_range_object_nested_in_blocks(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {
                "blocks": [
                    {
                        "path": "pkg/service.py",
                        "startLine": "10",
                        "endLine": "12",
                        "newText": "def build():\n    return True\n",
                    }
                ]
            },
        )

        assert args == {
            "file": "pkg/service.py",
            "start": 10,
            "end": 12,
            "replace": "def build():\n    return True\n",
        }

    def test_json_wrapped_line_range_camel_case_args_are_unwrapped(self) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {"arguments": ('{"path": "pkg/service.py", "startLine": "2", "endLine": "3", "newText": "value = 1\\n"}')},
        )

        assert args == {
            "file": "pkg/service.py",
            "start": 2,
            "end": 3,
            "replace": "value = 1\n",
        }

    # ----- broadened shapes (qwen3-coder & other capable models) -----

    def test_single_dict_not_wrapped_in_list(self) -> None:
        args = normalize_edit_blocks_args({"file": "z.py", "blocks": {"search": "a", "replace": "b"}})
        assert isinstance(args["blocks"], str)
        assert len(parse_edit_blocks(args["blocks"], default_filepath="z.py")) == 1

    def test_old_new_key_aliases(self) -> None:
        args = normalize_edit_blocks_args({"file": "z.py", "blocks": [{"old": "a", "new": "b"}]})
        assert isinstance(args["blocks"], str)
        assert len(parse_edit_blocks(args["blocks"], default_filepath="z.py")) == 1

    def test_search_replace_pair_list(self) -> None:
        args = normalize_edit_blocks_args({"file": "z.py", "blocks": [["a", "b"]]})
        assert isinstance(args["blocks"], str)
        assert len(parse_edit_blocks(args["blocks"], default_filepath="z.py")) == 1

    def test_direct_block_text_key(self) -> None:
        block = "<<<< SEARCH:z.py\na\n====\nb\n>>>> REPLACE"
        args = normalize_edit_blocks_args({"file": "z.py", "blocks": [{"block": block}]})
        assert isinstance(args["blocks"], str)
        assert len(parse_edit_blocks(args["blocks"], default_filepath="z.py")) == 1

    def test_unknown_dict_shape_left_for_validator(self) -> None:
        # Unrecognized shape must be preserved so the existing validator surfaces it.
        assert isinstance(normalize_edit_blocks_args({"blocks": [{"foo": "bar"}]})["blocks"], list)

    def test_content_alias_mirrored_to_blocks(self) -> None:
        args = normalize_edit_blocks_args({"content": [{"search": "a", "replace": "b", "file": "z.py"}]})
        assert isinstance(args.get("blocks"), str)

    def test_registered_in_tool_normalizers(self) -> None:
        out = normalize_tool_arguments("edit_blocks", {"blocks": [{"search": "a", "replace": "b", "file": "z.py"}]})
        assert isinstance(out["blocks"], str)
        assert len(parse_edit_blocks(out["blocks"], default_filepath="z.py")) == 1

    @pytest.mark.parametrize(
        ("search_key", "replace_key"),
        [
            ("search", "replace"),
            ("old", "new"),
            ("old_string", "new_string"),
            ("before", "after"),
            ("search_text", "replacement_text"),
        ],
    )
    def test_top_level_search_replace_synonyms_coerce_to_blocks(
        self,
        search_key: str,
        replace_key: str,
    ) -> None:
        args = normalize_tool_arguments(
            "edit_blocks",
            {"file": "z.py", search_key: "old()", replace_key: "new()"},
        )
        assert isinstance(args["blocks"], str)
        assert len(parse_edit_blocks(args["blocks"], default_filepath="z.py")) == 1

    def test_top_level_search_replace_without_file_stays_fail_closed(self) -> None:
        args = normalize_tool_arguments("edit_blocks", {"old_string": "old()", "new_string": "new()"})
        assert args == {"old_string": "old()", "new_string": "new()"}
