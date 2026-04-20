from __future__ import annotations

from polaris.kernelone.editing.unified_diff_engine import extract_unified_diff_edits


def test_extract_unified_diff_edits() -> None:
    text = (
        "```diff\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ @@\n"
        "-def old():\n"
        "-    return 1\n"
        "+def new():\n"
        "+    return 2\n"
        "```\n"
    )
    edits = extract_unified_diff_edits(text)
    assert len(edits) == 1
    path, before, after = edits[0]
    assert path == "src/app.py"
    assert "def old():" in before
    assert "def new():" in after
