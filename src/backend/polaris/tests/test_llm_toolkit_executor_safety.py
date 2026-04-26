import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_CORE_ROOT = _BACKEND_ROOT / "core"
for _path in (str(_BACKEND_ROOT), str(_CORE_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


def test_executor_close_sync_is_idempotent() -> None:
    executor = AgentAccelToolExecutor(".")

    executor.close_sync()
    executor.close_sync()

    assert executor._closed is True


def test_removed_semantic_tool_is_rejected() -> None:
    executor = AgentAccelToolExecutor(".")

    result = executor.execute("get_semantic_context", {"query": "UserService"})

    assert result["ok"] is False
    assert "Unknown tool" in str(result.get("error") or "")
