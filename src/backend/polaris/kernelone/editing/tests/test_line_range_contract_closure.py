"""Exact offset and explicit deletion semantics of the shared editing primitive."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from polaris.kernelone.editing.contracts import EditPrewriteRejectionV1, hash_edit_prewrite_arguments
from polaris.kernelone.editing.editblock_engine import compose_line_range_edit


@pytest.mark.parametrize(
    ("content", "start", "end", "replacement", "expected"),
    [
        ("one\ntwo\nthree\n", 2, 2, "", "one\nthree\n"),
        ("one\ntwo", 2, 2, "", "one\n"),
        ("one", 1, 1, "", ""),
        ("same\nsame\nend\n", 2, 2, "changed", "same\nchanged\nend\n"),
        ("one\ntwo\n", 2, 2, "   ", "one\n   \n"),
        ("one\r\ntwo\r\n", 2, 2, "", "one\r\n"),
    ],
)
def test_exact_line_range(content, start, end, replacement, expected):
    assert compose_line_range_edit(content, start, end, replacement) == expected


@pytest.mark.parametrize(
    "start,end,replacement", [(0, 1, "x"), (2, 1, "x"), (1, 3, "x"), (True, 1, "x"), ("1", 1, "x"), (1, 1, None)]
)
def test_invalid_range_never_infers_deletion(start, end, replacement):
    with pytest.raises(ValueError):
        compose_line_range_edit("one\ntwo\n", start, end, replacement)


def test_prewrite_proof_is_frozen_and_cannot_claim_changed_bytes():
    digest = hash_edit_prewrite_arguments({"file": "owned.txt", "start": 1, "end": 1, "replace": ""})
    proof = EditPrewriteRejectionV1("edit_blocks_invalid_line_range", "owned.txt", "a" * 64, digest, "b" * 64, "b" * 64)
    with pytest.raises(FrozenInstanceError):
        proof.target_path = "foreign.txt"
    with pytest.raises(ValueError):
        EditPrewriteRejectionV1("edit_blocks_invalid_line_range", "owned.txt", "a" * 64, digest, "b" * 64, "c" * 64)
    assert digest != hash_edit_prewrite_arguments({"file": "owned.txt", "start": 1, "end": 1})
