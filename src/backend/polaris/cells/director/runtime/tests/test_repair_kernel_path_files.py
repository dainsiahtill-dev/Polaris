"""Characterization + contract tests for repair_kernel path_files SSoT.

These tests lock strict vs permissive semantics so future package splits do not
re-copy diverging ``_normalize_repair_path`` helpers.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.runtime.internal.repair_kernel.path_files import (
    normalize_base_files_permissive,
    normalize_base_files_strict,
    normalize_repair_path_permissive,
    normalize_repair_path_strict,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("foo.ts", "foo.ts"),
        ("./foo.ts", "foo.ts"),
        ("././bar/baz.ts", "bar/baz.ts"),
        ("foo\\bar.ts", "foo/bar.ts"),
        ("  src/x.ts  ", "src/x.ts"),
        ("", ""),
        ("   ", ""),
        ("/abs/x.ts", ""),
        ("../x.ts", ""),
        ("a/../b.ts", ""),  # substring /../ rejection (strict majority)
        ("a/../../b.ts", ""),
    ],
)
def test_normalize_repair_path_strict_rejects_traversal_and_absolute(raw: str, expected: str) -> None:
    assert normalize_repair_path_strict(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("foo.ts", "foo.ts"),
        ("./foo.ts", "foo.ts"),
        ("/abs/x.ts", "/abs/x.ts"),
        ("../x.ts", "../x.ts"),
        ("a/../b.ts", "a/../b.ts"),
        ("foo\\bar.ts", "foo/bar.ts"),
        ("", ""),
    ],
)
def test_normalize_repair_path_permissive_keeps_absolute_and_parent(raw: str, expected: str) -> None:
    assert normalize_repair_path_permissive(raw) == expected


def test_normalize_base_files_strict_drops_bad_keys_and_coerces_content() -> None:
    result = normalize_base_files_strict(
        {
            "./src/a.ts": "export const a = 1;",
            "../escape.ts": "bad",
            "/abs.ts": "bad",
            "src/b.ts": None,  # type: ignore[dict-item]
            "": "empty-key",
        }
    )
    assert result == {
        "src/a.ts": "export const a = 1;",
        "src/b.ts": "",
    }


def test_normalize_base_files_permissive_keeps_parent_keys() -> None:
    result = normalize_base_files_permissive(
        {
            "./src/a.ts": "a",
            "../escape.ts": "escaped",
            "": "drop-me",
        }
    )
    assert result == {
        "src/a.ts": "a",
        "../escape.ts": "escaped",
    }


def test_strict_and_permissive_diverge_on_parent_segment() -> None:
    """Regression: do not silently merge semantics across call sites."""

    raw = "pkg/../secret.ts"
    assert normalize_repair_path_strict(raw) == ""
    assert normalize_repair_path_permissive(raw) == "pkg/../secret.ts"
