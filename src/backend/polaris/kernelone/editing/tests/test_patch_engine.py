from __future__ import annotations

from polaris.kernelone.editing.patch_engine import extract_apply_patch_operations


def test_extract_apply_patch_add_and_delete() -> None:
    text = (
        "*** Begin Patch\n"
        "*** Add File: src/new.py\n"
        "+def x():\n"
        "+    return 1\n"
        "*** Delete File: src/old.py\n"
        "*** End Patch\n"
    )
    ops = extract_apply_patch_operations(text)
    assert len(ops) == 2
    assert ops[0].kind == "create"
    assert ops[0].path == "src/new.py"
    assert "def x():" in ops[0].content
    assert ops[1].kind == "delete"
    assert ops[1].path == "src/old.py"


def test_extract_apply_patch_update_to_search_replace() -> None:
    text = (
        "*** Begin Patch\n"
        "*** Update File: src/app.py\n"
        "@@\n"
        "-def old():\n"
        "-    return 1\n"
        "+def new():\n"
        "+    return 2\n"
        "*** End Patch\n"
    )
    ops = extract_apply_patch_operations(text)
    assert len(ops) == 1
    assert ops[0].kind == "search_replace"
    assert ops[0].path == "src/app.py"
    assert "def old():" in ops[0].search
    assert "def new():" in ops[0].replace


def test_extract_apply_patch_update_with_move_to() -> None:
    text = (
        "*** Begin Patch\n"
        "*** Update File: src/app.py\n"
        "*** Move to: src/app_renamed.py\n"
        "@@\n"
        "-def old():\n"
        "+def new():\n"
        "*** End Patch\n"
    )
    ops = extract_apply_patch_operations(text)
    assert len(ops) == 1
    assert ops[0].kind == "search_replace"
    assert ops[0].path == "src/app.py"
    assert ops[0].move_to == "src/app_renamed.py"
