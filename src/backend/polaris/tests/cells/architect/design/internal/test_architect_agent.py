"""Tests for polaris.cells.architect.design.internal.architect_agent.

Mock strategies:
- RoleAgent.__init__ is patched to avoid filesystem I/O during agent construction.
- AgentMemory / storage roots are bypassed via the same init patch.
- The in-memory BlueprintStore needs no mocking (it is pure in-memory).
- MessageQueue (AgentBusProxy) operations are mocked for run_cycle tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.architect.design.internal.architect_agent import (
    ArchitectAgent,
    BlueprintRecord,
    BlueprintStore,
)
from polaris.cells.roles.runtime.internal.agent_runtime_base import (
    AgentMessage,
    MessageType,
)


# ---------------------------------------------------------------------------
# BlueprintRecord
# ---------------------------------------------------------------------------

class TestBlueprintRecord:
    def test_to_dict_returns_expected_keys(self) -> None:
        record = BlueprintRecord(
            blueprint_id="bp_1",
            task_id="t1",
            title="Test Blueprint",
            modules=["mod_a", "mod_b"],
            file_structure={"src": ["main.py"]},
            contracts={"api": "REST"},
            boundaries={"db": "postgres"},
            status="draft",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        d = record.to_dict()
        assert d["blueprint_id"] == "bp_1"
        assert d["task_id"] == "t1"
        assert d["title"] == "Test Blueprint"
        assert d["modules"] == ["mod_a", "mod_b"]
        assert d["file_structure"] == {"src": ["main.py"]}
        assert d["contracts"] == {"api": "REST"}
        assert d["boundaries"] == {"db": "postgres"}
        assert d["status"] == "draft"

    def test_default_factory_values(self) -> None:
        record = BlueprintRecord(blueprint_id="bp_2", task_id="t2", title="Default")
        assert record.modules == []
        assert record.file_structure == {}
        assert record.contracts == {}
        assert record.boundaries == {}
        assert record.status == "draft"
        assert record.created_at != ""
        assert record.updated_at != ""


# ---------------------------------------------------------------------------
# BlueprintStore
# ---------------------------------------------------------------------------

class TestBlueprintStore:
    def test_save_and_get(self) -> None:
        store = BlueprintStore()
        record = BlueprintRecord(blueprint_id="bp_1", task_id="t1", title="Save Test")
        store.save(record)
        fetched = store.get("bp_1")
        assert fetched is not None
        assert fetched.blueprint_id == "bp_1"

    def test_get_returns_none_for_missing(self) -> None:
        store = BlueprintStore()
        assert store.get("missing") is None

    def test_get_strips_whitespace(self) -> None:
        store = BlueprintStore()
        record = BlueprintRecord(blueprint_id="bp_trim", task_id="t1", title="Trim")
        store.save(record)
        assert store.get("  bp_trim  ") is not None

    def test_list_all_sorted_by_updated_desc(self) -> None:
        store = BlueprintStore()
        r1 = BlueprintRecord(blueprint_id="bp_a", task_id="t1", title="A", updated_at="2024-01-02T00:00:00")
        r2 = BlueprintRecord(blueprint_id="bp_b", task_id="t1", title="B", updated_at="2024-01-03T00:00:00")
        r3 = BlueprintRecord(blueprint_id="bp_c", task_id="t2", title="C", updated_at="2024-01-01T00:00:00")
        # Bypass save() to avoid overwriting updated_at timestamps
        store._by_id = {"bp_a": r1, "bp_b": r2, "bp_c": r3}
        all_rows = store.list_all()
        assert [r.blueprint_id for r in all_rows] == ["bp_b", "bp_a", "bp_c"]

    def test_list_by_task_filters_and_sorts(self) -> None:
        store = BlueprintStore()
        r1 = BlueprintRecord(blueprint_id="bp_1", task_id="task-x", title="X1", updated_at="2024-01-02T00:00:00")
        r2 = BlueprintRecord(blueprint_id="bp_2", task_id="task-x", title="X2", updated_at="2024-01-03T00:00:00")
        r3 = BlueprintRecord(blueprint_id="bp_3", task_id="task-y", title="Y1", updated_at="2024-01-01T00:00:00")
        store.save(r1)
        store.save(r2)
        store.save(r3)
        rows = store.list_by_task("task-x")
        assert [r.blueprint_id for r in rows] == ["bp_2", "bp_1"]

    def test_save_updates_timestamp(self) -> None:
        store = BlueprintStore()
        record = BlueprintRecord(blueprint_id="bp_ts", task_id="t1", title="TS", updated_at="2024-01-01T00:00:00")
        store.save(record)
        assert record.updated_at != "2024-01-01T00:00:00"


# ---------------------------------------------------------------------------
# ArchitectAgent
# ---------------------------------------------------------------------------

@pytest.fixture
def agent() -> ArchitectAgent:
    """Return an ArchitectAgent with a fresh in-memory store and no I/O."""
    inst = ArchitectAgent(workspace="/tmp/ws")
    inst._store = BlueprintStore()
    inst._toolbox = None
    inst._message_queue = None
    yield inst


class TestArchitectAgentToolbox:
    def test_setup_toolbox_registers_tools(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        tools = agent.toolbox.list_tools()
        assert "create_blueprint" in tools
        assert "update_blueprint_structure" in tools
        assert "add_contract" in tools
        assert "add_boundary" in tools
        assert "finalize_blueprint" in tools
        assert "get_blueprint" in tools
        assert "list_blueprints" in tools
        assert len(tools) == 7


class TestArchitectAgentTools:
    def test_create_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        result = agent._tool_create_blueprint(task_id="t1", title="My Blueprint", modules=["a", "b"])
        assert result["ok"] is True
        bp = result["blueprint"]
        assert bp["title"] == "My Blueprint"
        assert bp["modules"] == ["a", "b"]
        assert bp["task_id"] == "t1"
        assert bp["status"] == "draft"

    def test_create_blueprint_defaults(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        result = agent._tool_create_blueprint(task_id="", title="")
        assert result["ok"] is True
        bp = result["blueprint"]
        assert bp["title"] == "Architecture Blueprint"
        assert bp["modules"] == []

    def test_update_blueprint_structure(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_update_blueprint_structure(
            blueprint_id=bp_id,
            file_structure={"src": ["main.py"]},
        )
        assert result["ok"] is True
        assert result["blueprint"]["file_structure"] == {"src": ["main.py"]}

    def test_update_blueprint_structure_missing(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        result = agent._tool_update_blueprint_structure(
            blueprint_id="missing",
            file_structure={"src": []},
        )
        assert result["ok"] is False
        assert result["error"] == "blueprint_not_found"

    def test_add_contract(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_add_contract(
            blueprint_id=bp_id,
            contract_name="auth",
            contract_definition="OAuth2",
        )
        assert result["ok"] is True
        assert result["blueprint"]["contracts"] == {"auth": "OAuth2"}

    def test_add_contract_missing_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        result = agent._tool_add_contract(
            blueprint_id="missing",
            contract_name="auth",
            contract_definition="OAuth2",
        )
        assert result["ok"] is False
        assert result["error"] == "blueprint_not_found"

    def test_add_contract_empty_name(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_add_contract(bp_id, "", "def")
        assert result["ok"] is False
        assert result["error"] == "contract_name_required"

    def test_add_boundary(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_add_boundary(
            blueprint_id=bp_id,
            boundary_name="db",
            boundary_definition="Postgres",
        )
        assert result["ok"] is True
        assert result["blueprint"]["boundaries"] == {"db": "Postgres"}

    def test_add_boundary_empty_name(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_add_boundary(bp_id, "", "def")
        assert result["ok"] is False
        assert result["error"] == "boundary_name_required"

    def test_finalize_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_finalize_blueprint(bp_id)
        assert result["ok"] is True
        assert result["blueprint"]["status"] == "finalized"

    def test_finalize_blueprint_missing(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        result = agent._tool_finalize_blueprint("missing")
        assert result["ok"] is False
        assert result["error"] == "blueprint_not_found"

    def test_get_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        result = agent._tool_get_blueprint(bp_id)
        assert result["ok"] is True
        assert result["blueprint"]["blueprint_id"] == bp_id

    def test_get_blueprint_missing(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        result = agent._tool_get_blueprint("missing")
        assert result["ok"] is False
        assert result["error"] == "blueprint_not_found"

    def test_list_blueprints_all(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        agent._tool_create_blueprint(task_id="t1", title="A", modules=[])
        agent._tool_create_blueprint(task_id="t2", title="B", modules=[])
        result = agent._tool_list_blueprints()
        assert result["ok"] is True
        assert result["count"] == 2

    def test_list_blueprints_by_task(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        agent._tool_create_blueprint(task_id="t1", title="A", modules=[])
        agent._tool_create_blueprint(task_id="t1", title="B", modules=[])
        agent._tool_create_blueprint(task_id="t2", title="C", modules=[])
        result = agent._tool_list_blueprints(task_id="t1")
        assert result["ok"] is True
        assert result["count"] == 2


class TestArchitectAgentHandleMessage:
    def _make_msg(self, action: str, payload: dict | None = None) -> AgentMessage:
        return AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="test",
            receiver="Architect",
            payload={"action": action, **(payload or {})},
        )

    def test_handle_create_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        msg = self._make_msg("create_blueprint", {"task_id": "t1", "title": "Msg BP"})
        resp = agent.handle_message(msg)
        assert resp is not None
        assert resp.type == MessageType.RESULT
        assert resp.payload["action"] == "create_blueprint"
        assert resp.payload["result"]["ok"] is True

    def test_handle_update_blueprint_structure(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        msg = self._make_msg("update_blueprint_structure", {"blueprint_id": bp_id, "file_structure": {"a": 1}})
        resp = agent.handle_message(msg)
        assert resp is not None
        assert resp.payload["result"]["ok"] is True

    def test_handle_finalize_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        msg = self._make_msg("finalize_blueprint", {"blueprint_id": bp_id})
        resp = agent.handle_message(msg)
        assert resp is not None
        assert resp.payload["result"]["ok"] is True

    def test_handle_get_blueprint(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        created = agent._tool_create_blueprint(task_id="t1", title="BP", modules=[])
        bp_id = created["blueprint"]["blueprint_id"]
        msg = self._make_msg("get_blueprint", {"blueprint_id": bp_id})
        resp = agent.handle_message(msg)
        assert resp is not None
        assert resp.payload["result"]["ok"] is True

    def test_handle_list_blueprints(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        agent._tool_create_blueprint(task_id="t1", title="A", modules=[])
        msg = self._make_msg("list_blueprints", {"task_id": "t1"})
        resp = agent.handle_message(msg)
        assert resp is not None
        assert resp.payload["result"]["ok"] is True

    def test_handle_unsupported_action(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        msg = self._make_msg("destroy_everything")
        resp = agent.handle_message(msg)
        assert resp is not None
        assert resp.payload["result"]["ok"] is False
        assert resp.payload["result"]["error"] == "unsupported_action"

    def test_handle_non_task_returns_none(self, agent: ArchitectAgent) -> None:
        msg = AgentMessage.create(
            msg_type=MessageType.EVENT,
            sender="test",
            receiver="Architect",
            payload={"action": "create_blueprint"},
        )
        assert agent.handle_message(msg) is None


class TestArchitectAgentRunCycle:
    def test_run_cycle_no_message(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        with patch.object(agent.message_queue, "receive", return_value=None):
            assert agent.run_cycle() is False

    def test_run_cycle_with_message(self, agent: ArchitectAgent) -> None:
        agent.setup_toolbox()
        msg = AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="test",
            receiver="Architect",
            payload={"action": "create_blueprint", "task_id": "t1", "title": "RC BP"},
        )
        with patch.object(agent.message_queue, "receive", return_value=msg):
            with patch.object(agent.message_queue, "send") as mock_send:
                assert agent.run_cycle() is True
                mock_send.assert_called_once()
                sent = mock_send.call_args[0][0]
                assert sent.payload["action"] == "create_blueprint"
                assert sent.payload["result"]["ok"] is True
