from __future__ import annotations

from polaris.kernelone.editing.wholefile_engine import extract_wholefile_blocks


def test_extract_wholefile_blocks() -> None:
    text = "src/app.py\n```python\ndef run():\n    return 1\n```\n"
    edits = extract_wholefile_blocks(text, inchat_files=["src/app.py"])
    assert len(edits) == 1
    path, body = edits[0]
    assert path == "src/app.py"
    assert "def run():" in body
