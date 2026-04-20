from __future__ import annotations

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


class _FakeSessionMemoryProvider:
    def search_memory_for_session(
        self,
        session_id: str,
        query: str,
        *,
        kind: str | None = None,
        entity: str | None = None,
        limit: int = 6,
    ) -> list[dict]:
        return [
            {
                "kind": kind or "artifact",
                "id": "art_001",
                "score": 0.88,
                "text": f"{session_id}:{query}:{entity or ''}",
                "metadata": {"limit": limit},
            }
        ]

    def read_artifact_for_session(
        self,
        session_id: str,
        artifact_id: str,
        *,
        span: tuple[int, int] | None = None,
    ) -> dict | None:
        return {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "content": f"span={span}",
        }

    def read_episode_for_session(
        self,
        session_id: str,
        episode_id: str,
    ) -> dict | None:
        return {
            "episode_id": episode_id,
            "session_id": session_id,
            "intent": "test memory tools",
        }

    def get_state_for_session(
        self,
        session_id: str,
        path: str,
    ) -> dict | None:
        return {"session_id": session_id, "path": path, "value": "ok"}


def test_search_memory_tool_uses_bound_session_context() -> None:
    executor = AgentAccelToolExecutor(
        ".",
        session_id="sess-1",
        session_memory_provider=_FakeSessionMemoryProvider(),
    )

    result = executor.execute("search_memory", {"query": "continuity", "entity": "session.py"})

    assert result["ok"] is True
    payload = dict(result["result"])
    assert payload["session_id"] == "sess-1"
    assert payload["total"] == 1
    assert "continuity" in payload["items"][0]["text"]


def test_read_artifact_tool_reads_through_provider() -> None:
    executor = AgentAccelToolExecutor(
        ".",
        session_id="sess-2",
        session_memory_provider=_FakeSessionMemoryProvider(),
    )

    result = executor.execute(
        "read_artifact",
        {"artifact_id": "art_001", "start_line": 2, "end_line": 4},
    )

    assert result["ok"] is True
    payload = dict(result["result"])
    assert payload["artifact_id"] == "art_001"
    assert payload["session_id"] == "sess-2"
    assert "span=(2, 4)" in payload["content"]


def test_get_state_tool_requires_session_memory_context() -> None:
    executor = AgentAccelToolExecutor(".")

    result = executor.execute("get_state", {"path": "task_state.current_goal"})

    assert result["ok"] is False
    assert "Session memory is unavailable" in str(result["error"])
