from __future__ import annotations

from polaris.kernelone.editing.operation_router import route_edit_operations


def test_route_apply_patch_priority() -> None:
    text = "*** Begin Patch\n*** Add File: src/new.py\n+x = 1\n*** End Patch\n"
    ops = route_edit_operations(text, inchat_files=[])
    assert len(ops) == 1
    assert ops[0].kind == "create"
    assert ops[0].path == "src/new.py"


def test_route_editblock_when_no_apply_patch() -> None:
    text = "src/a.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"
    ops = route_edit_operations(text, inchat_files=["src/a.py"])
    assert len(ops) == 1
    assert ops[0].kind == "search_replace"
    assert ops[0].path == "src/a.py"


def test_route_unified_diff_when_no_editblock() -> None:
    text = "```diff\n--- a/src/a.py\n+++ b/src/a.py\n@@ @@\n-x = 1\n+x = 2\n```\n"
    ops = route_edit_operations(text, inchat_files=[])
    assert len(ops) == 1
    assert ops[0].kind == "search_replace"
    assert ops[0].path == "src/a.py"


def test_route_wholefile_last_fallback() -> None:
    text = "src/a.py\n```python\nx = 2\n```\n"
    ops = route_edit_operations(text, inchat_files=["src/a.py"])
    assert len(ops) == 1
    assert ops[0].kind == "full_file"
    assert ops[0].path == "src/a.py"
