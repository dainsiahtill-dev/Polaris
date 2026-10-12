"""PM request failure must not become an empty task-contract response."""

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal import pm_adapter


@pytest.mark.asyncio
async def test_pm_role_call_preserves_provider_overload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    error = "LLM call failed: 529 Server Error: overloaded_error"

    async def failure(**_kwargs: object) -> dict:
        return {"content": "", "error": error, "metadata": {"error_category": "network"}}

    monkeypatch.setattr(pm_adapter, "invoke_role_runtime_first", failure)
    adapter = pm_adapter.PMAdapter(workspace=str(tmp_path))
    with pytest.raises(RuntimeError, match="529 Server Error: overloaded_error"):
        await adapter._call_role_llm("Correct the PM task contract")
