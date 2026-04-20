from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
for entry in (str(BACKEND_ROOT), str(LOOP_CORE_ROOT)):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

from core.polaris_loop.prompts import PatchBlock, apply_patch_blocks  # noqa: E402


def test_apply_patch_blocks_single_line_replace(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text(
        "import os\n\n"
        "def run():\n"
        "    value = 1\n"
        "    return value\n",
        encoding="utf-8",
    )

    result = apply_patch_blocks(
        [PatchBlock(file="sample.txt", search="value = 1", replace="value = 2")],
        str(tmp_path),
        strict=True,
    )

    assert result.ok is True
    assert file_path.read_text(encoding="utf-8").count("value = 2") == 1


def test_apply_patch_blocks_multiline_replace(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = apply_patch_blocks(
        [PatchBlock(file="sample.txt", search="alpha\nbeta\ngamma", replace="one\ntwo\nthree")],
        str(tmp_path),
        strict=True,
    )

    assert result.ok is True
    assert file_path.read_text(encoding="utf-8") == "one\ntwo\nthree"


def test_apply_patch_blocks_tolerates_blank_line_drift(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n\nbeta\ngamma\n", encoding="utf-8")

    result = apply_patch_blocks(
        [PatchBlock(file="sample.txt", search="alpha\nbeta\ngamma", replace="one\ntwo\nthree")],
        str(tmp_path),
        strict=True,
    )

    assert result.ok is True
    assert file_path.read_text(encoding="utf-8") == "one\ntwo\nthree"


def test_apply_patch_blocks_tolerates_trailing_whitespace_drift(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("first\nvalue = 1   \nlast\n", encoding="utf-8")

    result = apply_patch_blocks(
        [PatchBlock(file="sample.txt", search="value = 1", replace="value = 2")],
        str(tmp_path),
        strict=True,
    )

    assert result.ok is True
    assert file_path.read_text(encoding="utf-8") == "first\nvalue = 2   \nlast\n"


def test_apply_patch_blocks_returns_ambiguous_error(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("start\nneedle\nend\nstart\nneedle\nend\n", encoding="utf-8")

    result = apply_patch_blocks(
        [PatchBlock(file="sample.txt", search="start\nneedle\nend", replace="done")],
        str(tmp_path),
        strict=True,
    )

    assert result.ok is False
    assert any("Ambiguous SEARCH" in err for err in result.errors)
