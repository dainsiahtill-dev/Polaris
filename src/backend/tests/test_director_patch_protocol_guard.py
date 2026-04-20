from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter


@pytest.mark.asyncio
async def test_patch_protocol_failure_does_not_fall_back_to_markdown_write(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    response = """tests/test_bill_crud.py
```python
<<<<<<< SEARCH

=======
print("unexpected raw patch")
```"""

    results = await adapter._execute_patch_file_format(response, task_id="task-1")

    assert results
    assert results[0]["success"] is False
    assert not (tmp_path / "tests" / "test_bill_crud.py").exists()


@pytest.mark.asyncio
async def test_markdown_file_blocks_still_write_full_files(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    response = """src/app.py
```python
print("ok")
```"""

    results = await adapter._execute_patch_file_format(response, task_id="task-2")

    assert any(item.get("success") for item in results)
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == 'print("ok")'
