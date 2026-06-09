"""Tests for the edit_blocks weak-model arg-healing normalizer.

Weak local models (gemma, etc.) emit the ``blocks`` argument as a JSON list
instead of the canonical SEARCH/REPLACE string. The normalizer coerces those
shapes so the call survives schema validation and reaches the handler.
"""

from __future__ import annotations

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

    def test_string_list_joined(self) -> None:
        args = normalize_edit_blocks_args({"blocks": ["<<<< SEARCH:f.py", "x", "====", "y", ">>>> REPLACE"]})
        assert isinstance(args["blocks"], str)
        assert "SEARCH" in args["blocks"]

    def test_plain_string_unchanged(self) -> None:
        text = "<<<< SEARCH:f.py\nx\n====\ny\n>>>> REPLACE"
        assert normalize_edit_blocks_args({"blocks": text})["blocks"] == text

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
