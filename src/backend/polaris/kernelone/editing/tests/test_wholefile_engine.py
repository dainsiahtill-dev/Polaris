from __future__ import annotations

from polaris.kernelone.editing.wholefile_engine import extract_wholefile_blocks


def test_extract_wholefile_blocks() -> None:
    text = "src/app.py\n```python\ndef run():\n    return 1\n```\n"
    edits = extract_wholefile_blocks(text, inchat_files=["src/app.py"])
    assert len(edits) == 1
    path, body = edits[0]
    assert path == "src/app.py"
    assert "def run():" in body


def test_extract_wholefile_blocks_ignores_markdown_file_inventory_fence() -> None:
    text = (
        "# Blueprint report\n\n"
        "Target files:\n"
        "```text\n"
        "src/index.ts # main entry\n"
        "src/web.ts # browser entry\n"
        "```\n\n"
        "**Additional guidance**\n"
        "1. Keep the implementation small.\n"
    )

    edits = extract_wholefile_blocks(text, inchat_files=["src/index.ts", "src/web.ts"])

    assert edits == []


def test_extract_wholefile_blocks_still_accepts_path_before_opening_fence() -> None:
    text = "src/app.py\n```python\ndef run():\n    return 1\n```\n"

    edits = extract_wholefile_blocks(text, inchat_files=["src/app.py"])

    assert edits == [("src/app.py", "def run():\n    return 1\n")]
