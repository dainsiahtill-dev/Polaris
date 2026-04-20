"""Unit tests for finops.budget_guard cell — CFOAgent and BudgetKFSStore.

Covers:
- CFOAgent._tool_check_budget() normal and budget-exceeded scenarios
- CFOAgent._tool_record_usage() normal scenarios
- CFOAgent._tool_allocate_budget() normal scenarios
- CFOAgent._tool_get_budget_status() scenarios
- CFOAgent._tool_get_usage_stats() scenarios
- CFOAgent._tool_set_budget_limit() scenarios
- CFOAgent._tool_set_global_budget() scenarios
- BudgetKFSStore read/write/recovery scenarios
- BudgetThresholdExceededEventV1 contract validation
- FinOpsBudgetError contract validation
- ReserveBudgetCommandV1 / RecordUsageCommandV1 validation
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from polaris.cells.finops.budget_guard.internal.budget_store import (
    BudgetKFSStore,
    BudgetRecord,
    UsageRecord,
)
from polaris.cells.finops.budget_guard.public.contracts import (
    BudgetDecisionResultV1,
    BudgetThresholdExceededEventV1,
    FinOpsBudgetError,
    GetBudgetStatusQueryV1,
    RecordUsageCommandV1,
    ReserveBudgetCommandV1,
)
from polaris.cells.finops.budget_guard.public.service import CFOAgent
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.contracts import FileWriteReceipt, KernelFileSystemAdapter

if TYPE_CHECKING:
    from pathlib import Path

# ── Fake KFS adapter ───────────────────────────────────────────────────────────


class _FakeKFSAdapter(KernelFileSystemAdapter):
    """In-memory KFS adapter for isolated testing — never touches the real filesystem."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def exists(self, path: Path) -> bool:
        return str(path) in self._store

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        key = str(path)
        if key not in self._store:
            raise FileNotFoundError(f"FakeKFS: {key} not found")
        return self._store[key]

    def read_bytes(self, path: Path) -> bytes:
        key = str(path)
        if key not in self._store:
            raise FileNotFoundError(f"FakeKFS: {key} not found")
        return self._store[key].encode("utf-8")

    def write_text(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = False,
    ) -> int:
        key = str(path)
        self._store[key] = content
        encoded = content.encode(encoding)
        return len(encoded)

    def write_bytes(self, path: Path, content: bytes) -> int:
        key = str(path)
        self._store[key] = content.decode("utf-8")
        return len(content)

    def append_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        key = str(path)
        if key not in self._store:
            self._store[key] = content
        else:
            self._store[key] += content
        return len(content.encode(encoding))

    def write_json_atomic(self, path: Path, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        import json

        key = str(path)
        content = json.dumps(data, indent=indent, ensure_ascii=False)
        self._store[key] = content
        encoded = content.encode("utf-8")
        return FileWriteReceipt(
            logical_path=key,
            absolute_path=key,
            bytes_written=len(encoded),
            atomic=True,
        )

    def is_file(self, path: Path) -> bool:
        return str(path) in self._store

    def is_dir(self, path: Path) -> bool:
        # In-memory store doesn't have directories
        return False

    def remove(self, path: Path, *, missing_ok: bool = True) -> bool:
        key = str(path)
        if key in self._store:
            del self._store[key]
            return True
        if missing_ok:
            return False
        raise FileNotFoundError(f"FakeKFS: {key} not found")


def _fake_fs() -> KernelFileSystem:
    return KernelFileSystem(".", _FakeKFSAdapter())


# ── CFOAgent fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_fs() -> KernelFileSystem:
    return _fake_fs()


@pytest.fixture
def cfo_agent(fake_fs: KernelFileSystem) -> CFOAgent:
    """Isolated CFOAgent with in-memory KFS adapter."""
    return CFOAgent(workspace=".", fs=fake_fs)


@pytest.fixture
def cfo_with_budget(fake_fs: KernelFileSystem) -> CFOAgent:
    """CFOAgent with a pre-allocated budget of 1000 tokens for task 'test-task'."""
    agent = CFOAgent(workspace=".", fs=fake_fs)
    agent._tool_allocate_budget(
        task_id="test-task",
        budget_type="general",
        limit=1000,
        unit="tokens",
    )
    return agent


# ── BudgetRecord / UsageRecord dataclass round-trip ─────────────────────────────


class TestBudgetRecord:
    def test_to_dict_roundtrip(self) -> None:
        record = BudgetRecord(
            budget_id="b1",
            task_id="t1",
            budget_type="general",
            limit=500,
            used=200,
            unit="tokens",
            status="active",
        )
        restored = BudgetRecord.from_dict(record.to_dict())
        assert restored.budget_id == "b1"
        assert restored.task_id == "t1"
        assert restored.limit == 500
        assert restored.used == 200
        assert restored.unit == "tokens"
        assert restored.status == "active"

    def test_from_dict_missing_fields_use_defaults(self) -> None:
        data = {"budget_id": "b2", "task_id": "t2", "limit": 300}
        record = BudgetRecord.from_dict(data)
        assert record.budget_type == "general"
        assert record.used == 0
        assert record.unit == "tokens"
        assert record.status == "active"

    def test_to_dict_typesafe(self) -> None:
        record = BudgetRecord(budget_id="b3", task_id="t3", budget_type="compute", limit=0)
        d = record.to_dict()
        assert isinstance(d["limit"], int)
        assert isinstance(d["used"], int)


class TestUsageRecord:
    def test_to_dict_roundtrip(self) -> None:
        record = UsageRecord(
            record_id="u1",
            task_id="t1",
            agent_id="director",
            resource_type="tokens",
            amount=150,
        )
        restored = UsageRecord.from_dict(record.to_dict())
        assert restored.record_id == "u1"
        assert restored.task_id == "t1"
        assert restored.agent_id == "director"
        assert restored.amount == 150

    def test_from_dict_missing_fields_use_defaults(self) -> None:
        data = {"record_id": "u2", "task_id": "t2", "amount": 50}
        record = UsageRecord.from_dict(data)
        assert record.agent_id == ""
        assert record.resource_type == "general"


# ── BudgetKFSStore ─────────────────────────────────────────────────────────────


class TestBudgetKFSStore:
    def test_save_and_retrieve_budget(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope1", fs=fake_fs)
        budget = BudgetRecord(
            budget_id="b1",
            task_id="task-a",
            budget_type="general",
            limit=2000,
        )
        store.save_budget(budget)
        retrieved = store.get_budget("b1")
        assert retrieved is not None
        assert retrieved.budget_id == "b1"
        assert retrieved.limit == 2000

    def test_save_budget_is_persisted(self, fake_fs: KernelFileSystem) -> None:
        """Verify write-through: state survives after store instance is replaced."""
        store1 = BudgetKFSStore(workspace=".", scope_id="scope2", fs=fake_fs)
        budget = BudgetRecord(budget_id="b2", task_id="task-b", budget_type="compute", limit=3000)
        store1.save_budget(budget)

        # New store instance — should recover from KFS
        store2 = BudgetKFSStore(workspace=".", scope_id="scope2", fs=fake_fs)
        retrieved = store2.get_budget("b2")
        assert retrieved is not None
        assert retrieved.limit == 3000

    def test_append_usage_record(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope3", fs=fake_fs)
        usage = UsageRecord(
            record_id="u1",
            task_id="task-c",
            agent_id="director",
            resource_type="tokens",
            amount=100,
        )
        store.append_usage(usage)
        rows = store.usage_by_task("task-c")
        assert len(rows) == 1
        assert rows[0].amount == 100

    def test_usage_persists_after_reload(self, fake_fs: KernelFileSystem) -> None:
        store1 = BudgetKFSStore(workspace=".", scope_id="scope4", fs=fake_fs)
        store1.append_usage(
            UsageRecord(record_id="u2", task_id="task-d", agent_id="pm", resource_type="tokens", amount=250)
        )
        store2 = BudgetKFSStore(workspace=".", scope_id="scope4", fs=fake_fs)
        rows = store2.usage_by_task("task-d")
        assert len(rows) == 1
        assert rows[0].amount == 250

    def test_usage_totals(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope5", fs=fake_fs)
        store.append_usage(
            UsageRecord(record_id="u3", task_id="task-e", agent_id="a1", resource_type="tokens", amount=100)
        )
        store.append_usage(
            UsageRecord(record_id="u4", task_id="task-e", agent_id="a2", resource_type="tokens", amount=200)
        )
        store.append_usage(
            UsageRecord(record_id="u5", task_id="task-e", agent_id="a3", resource_type="compute", amount=50)
        )
        totals = store.usage_totals("task-e")
        assert totals["tokens"] == 300
        assert totals["compute"] == 50

    def test_budgets_by_task(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope6", fs=fake_fs)
        store.save_budget(BudgetRecord(budget_id="b3", task_id="task-f", budget_type="general", limit=500))
        store.save_budget(BudgetRecord(budget_id="b4", task_id="task-f", budget_type="compute", limit=300))
        rows = store.budgets_by_task("task-f")
        assert len(rows) == 2

    def test_usage_by_agent(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope7", fs=fake_fs)
        store.append_usage(
            UsageRecord(record_id="u6", task_id="t1", agent_id="director", resource_type="tokens", amount=80)
        )
        store.append_usage(UsageRecord(record_id="u7", task_id="t2", agent_id="pm", resource_type="tokens", amount=90))
        rows = store.usage_by_agent("director")
        assert len(rows) == 1
        assert rows[0].agent_id == "director"

    def test_nonexistent_budget_returns_none(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope8", fs=fake_fs)
        assert store.get_budget("nonexistent") is None

    def test_empty_task_query_returns_empty(self, fake_fs: KernelFileSystem) -> None:
        store = BudgetKFSStore(workspace=".", scope_id="scope9", fs=fake_fs)
        assert store.budgets_by_task("ghost-task") == []
        assert store.usage_by_task("ghost-task") == []


# ── CFOAgent: allocate_budget ──────────────────────────────────────────────────


class TestCFOAgent_Allocate:
    def test_allocate_budget_success(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_allocate_budget(
            task_id="task-alloc",
            budget_type="general",
            limit=5000,
            unit="tokens",
        )
        assert result["ok"] is True
        assert "budget" in result
        assert result["budget"]["limit"] == 5000
        assert result["budget"]["task_id"] == "task-alloc"

    def test_allocate_budget_empty_task_id(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_allocate_budget(task_id="", budget_type="general", limit=1000)
        assert result["ok"] is False
        assert result["error"] == "task_id_required"

    def test_allocate_budget_negative_limit_clamped(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_allocate_budget(
            task_id="task-neg",
            budget_type="general",
            limit=-999,
            unit="tokens",
        )
        assert result["ok"] is True
        assert result["budget"]["limit"] == 0


# ── CFOAgent: check_budget ────────────────────────────────────────────────────


class TestCFOAgent_CheckBudget:
    def test_check_budget_no_budget_allocated(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_check_budget(task_id="unallocated-task", estimated_cost=100)
        assert result["ok"] is True
        assert result["within_budget"] is True
        assert result["reason"] == "no_budget_allocated"

    def test_check_budget_within_limit(self, cfo_with_budget: CFOAgent) -> None:
        # Pre-allocated budget: limit=1000, used=0
        result = cfo_with_budget._tool_check_budget(task_id="test-task", estimated_cost=500)
        assert result["ok"] is True
        assert result["within_budget"] is True
        assert result["remaining"] == 1000

    def test_check_budget_exceeds_limit(self, cfo_with_budget: CFOAgent) -> None:
        # Pre-allocated budget: limit=1000, used=0; request 1500 > 1000
        result = cfo_with_budget._tool_check_budget(task_id="test-task", estimated_cost=1500)
        assert result["ok"] is True
        assert result["within_budget"] is False
        assert result["reason"] == "exceeds_limit"
        assert result["remaining"] == 1000
        assert result["requested"] == 1500

    def test_check_budget_after_partial_usage(self, cfo_with_budget: CFOAgent) -> None:
        # Record 600 tokens usage first
        cfo_with_budget._tool_record_usage(
            task_id="test-task",
            agent_id="director",
            resource_type="general",
            amount=600,
        )
        # Remaining: 1000 - 600 = 400; requesting 500 exceeds it
        result = cfo_with_budget._tool_check_budget(task_id="test-task", estimated_cost=500)
        assert result["within_budget"] is False
        assert result["remaining"] == 400

    def test_check_budget_zero_cost_always_allowed(self, cfo_with_budget: CFOAgent) -> None:
        result = cfo_with_budget._tool_check_budget(task_id="test-task", estimated_cost=0)
        assert result["within_budget"] is True


# ── CFOAgent: record_usage ─────────────────────────────────────────────────────


class TestCFOAgent_RecordUsage:
    def test_record_usage_success(self, cfo_agent: CFOAgent) -> None:
        # Allocate first
        cfo_agent._tool_allocate_budget(
            task_id="task-rec",
            budget_type="general",
            limit=2000,
            unit="tokens",
        )
        result = cfo_agent._tool_record_usage(
            task_id="task-rec",
            agent_id="director",
            resource_type="general",
            amount=300,
        )
        assert result["ok"] is True
        assert "usage" in result
        assert result["usage"]["amount"] == 300
        assert result["usage"]["task_id"] == "task-rec"
        assert result["usage"]["agent_id"] == "director"

    def test_record_usage_updates_budget_used_counter(self, cfo_agent: CFOAgent) -> None:
        cfo_agent._tool_allocate_budget(
            task_id="task-upd",
            budget_type="general",
            limit=1000,
            unit="tokens",
        )
        cfo_agent._tool_record_usage(
            task_id="task-upd",
            agent_id="pm",
            resource_type="general",
            amount=400,
        )
        status = cfo_agent._tool_get_budget_status("task-upd")
        budgets = status["budgets"]
        assert len(budgets) == 1
        assert budgets[0]["used"] == 400

    def test_record_usage_multiple_accumulates(self, cfo_agent: CFOAgent) -> None:
        cfo_agent._tool_allocate_budget(
            task_id="task-multi",
            budget_type="general",
            limit=3000,
            unit="tokens",
        )
        cfo_agent._tool_record_usage(task_id="task-multi", agent_id="a1", resource_type="general", amount=100)
        cfo_agent._tool_record_usage(task_id="task-multi", agent_id="a2", resource_type="general", amount=200)
        status = cfo_agent._tool_get_budget_status("task-multi")
        assert status["budgets"][0]["used"] == 300


# ── CFOAgent: get_budget_status ────────────────────────────────────────────────


class TestCFOAgent_GetBudgetStatus:
    def test_get_status_no_budget(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_get_budget_status("ghost")
        assert result["ok"] is True
        assert result["has_budget"] is False
        assert result["budgets"] == []

    def test_get_status_with_budget(self, cfo_with_budget: CFOAgent) -> None:
        result = cfo_with_budget._tool_get_budget_status("test-task")
        assert result["ok"] is True
        assert result["has_budget"] is True
        assert len(result["budgets"]) == 1
        assert result["budgets"][0]["limit"] == 1000


# ── CFOAgent: get_usage_stats ──────────────────────────────────────────────────


class TestCFOAgent_GetUsageStats:
    def test_stats_no_records(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_get_usage_stats(task_id="empty-task")
        assert result["ok"] is True
        assert result["record_count"] == 0
        assert result["totals"] == {}

    def test_stats_by_task(self, cfo_agent: CFOAgent) -> None:
        cfo_agent._tool_allocate_budget(
            task_id="task-stat",
            budget_type="general",
            limit=5000,
            unit="tokens",
        )
        cfo_agent._tool_record_usage(
            task_id="task-stat",
            agent_id="director",
            resource_type="tokens",
            amount=500,
        )
        result = cfo_agent._tool_get_usage_stats(task_id="task-stat")
        assert result["record_count"] == 1
        assert result["totals"]["tokens"] == 500

    def test_stats_by_agent(self, cfo_agent: CFOAgent) -> None:
        cfo_agent._tool_allocate_budget(
            task_id="task-agent",
            budget_type="general",
            limit=5000,
            unit="tokens",
        )
        cfo_agent._tool_record_usage(
            task_id="task-agent",
            agent_id="director",
            resource_type="tokens",
            amount=700,
        )
        result = cfo_agent._tool_get_usage_stats(agent_id="director")
        assert result["record_count"] == 1
        assert result["totals"]["tokens"] == 700


# ── CFOAgent: set_budget_limit ────────────────────────────────────────────────


class TestCFOAgent_SetBudgetLimit:
    def test_set_limit_success(self, cfo_agent: CFOAgent) -> None:
        cfo_agent._tool_allocate_budget(
            task_id="task-limit",
            budget_type="general",
            limit=1000,
            unit="tokens",
        )
        # Get the budget_id from status
        status = cfo_agent._tool_get_budget_status("task-limit")
        budget_id = status["budgets"][0]["budget_id"]

        result = cfo_agent._tool_set_budget_limit(budget_id=budget_id, new_limit=9999)
        assert result["ok"] is True
        assert result["budget"]["limit"] == 9999

    def test_set_limit_not_found(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_set_budget_limit(budget_id="nonexistent-id", new_limit=5000)
        assert result["ok"] is False
        assert result["error"] == "budget_not_found"

    def test_set_limit_negative_clamped_to_zero(self, cfo_agent: CFOAgent) -> None:
        cfo_agent._tool_allocate_budget(
            task_id="task-neg-limit",
            budget_type="general",
            limit=1000,
            unit="tokens",
        )
        status = cfo_agent._tool_get_budget_status("task-neg-limit")
        budget_id = status["budgets"][0]["budget_id"]
        result = cfo_agent._tool_set_budget_limit(budget_id=budget_id, new_limit=-100)
        assert result["ok"] is True
        assert result["budget"]["limit"] == 0


# ── CFOAgent: set_global_budget ───────────────────────────────────────────────


class TestCFOAgent_SetGlobalBudget:
    def test_set_global_budget(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_set_global_budget(limit=50000, unit="tokens")
        assert result["ok"] is True
        assert result["budget"]["budget_type"] == "global"
        assert result["budget"]["task_id"] == "global"
        assert result["budget"]["limit"] == 50000

    def test_set_global_budget_negative_clamped(self, cfo_agent: CFOAgent) -> None:
        result = cfo_agent._tool_set_global_budget(limit=-100, unit="tokens")
        assert result["ok"] is True
        assert result["budget"]["limit"] == 0


# ── CFOAgent: BudgetThresholdExceededEventV1 emission ──────────────────────────


class TestBudgetThresholdExceededEvent:
    def test_event_construction_valid(self) -> None:
        event = BudgetThresholdExceededEventV1(
            event_id="evt-001",
            scope_id="scope-test",
            role="director",
            threshold=1000.0,
            observed=1500.0,
            occurred_at="2026-03-23T10:00:00Z",
        )
        assert event.threshold == 1000.0
        assert event.observed == 1500.0
        assert event.role == "director"

    def test_event_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            BudgetThresholdExceededEventV1(
                event_id="",
                scope_id="scope-test",
                role="director",
                threshold=1000.0,
                observed=1500.0,
                occurred_at="2026-03-23T10:00:00Z",
            )

    def test_event_empty_scope_id_raises(self) -> None:
        with pytest.raises(ValueError, match="scope_id must be a non-empty string"):
            BudgetThresholdExceededEventV1(
                event_id="evt-002",
                scope_id="",
                role="director",
                threshold=1000.0,
                observed=1500.0,
                occurred_at="2026-03-23T10:00:00Z",
            )


# ── CFOAgent: FinOpsBudgetError ───────────────────────────────────────────────


class TestFinOpsBudgetError:
    def test_error_with_code_and_details(self) -> None:
        err = FinOpsBudgetError(
            "Budget limit exceeded",
            code="limit_exceeded",
            details={"scope_id": "s1", "budget_id": "b1"},
        )
        assert str(err) == "Budget limit exceeded"
        assert err.code == "limit_exceeded"
        assert err.details["scope_id"] == "s1"

    def test_error_default_code(self) -> None:
        err = FinOpsBudgetError("Something went wrong")
        assert err.code == "finops_budget_error"

    def test_error_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            FinOpsBudgetError("")

    def test_error_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            FinOpsBudgetError("msg", code="")


# ── CFOAgent: ReserveBudgetCommandV1 & RecordUsageCommandV1 validation ────────


class TestReserveBudgetCommandV1:
    def test_valid_construction(self) -> None:
        cmd = ReserveBudgetCommandV1(
            scope_id="scope-cmd-1",
            workspace="/tmp",
            role="director",
            token_budget=5000,
        )
        assert cmd.token_budget == 5000
        assert cmd.role == "director"

    def test_negative_budget_raises(self) -> None:
        with pytest.raises(ValueError, match="token_budget must be >= 0"):
            ReserveBudgetCommandV1(
                scope_id="s1",
                workspace="/tmp",
                role="director",
                token_budget=-1,
            )

    def test_empty_scope_id_raises(self) -> None:
        with pytest.raises(ValueError, match="scope_id must be a non-empty string"):
            ReserveBudgetCommandV1(scope_id="", workspace="/tmp", role="director", token_budget=100)

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ReserveBudgetCommandV1(scope_id="s1", workspace="  ", role="director", token_budget=100)


class TestRecordUsageCommandV1:
    def test_valid_construction(self) -> None:
        cmd = RecordUsageCommandV1(
            scope_id="scope-uc-1",
            workspace="/tmp",
            role="director",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.002,
        )
        assert cmd.prompt_tokens == 100
        assert cmd.completion_tokens == 50
        assert cmd.cost_usd == 0.002

    def test_negative_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="tokens must be >= 0"):
            RecordUsageCommandV1(
                scope_id="s1",
                workspace="/tmp",
                role="director",
                prompt_tokens=-10,
                completion_tokens=50,
            )

    def test_negative_cost_raises(self) -> None:
        with pytest.raises(ValueError, match="cost_usd must be >= 0"):
            RecordUsageCommandV1(
                scope_id="s1",
                workspace="/tmp",
                role="director",
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=-0.01,
            )

    def test_metadata_is_copied(self) -> None:
        meta = {"key": "value"}
        cmd = RecordUsageCommandV1(
            scope_id="s1",
            workspace="/tmp",
            role="director",
            prompt_tokens=10,
            completion_tokens=5,
            metadata=meta,
        )
        meta["key"] = "modified"
        assert cmd.metadata["key"] == "value"


# ── CFOAgent: BudgetDecisionResultV1 ──────────────────────────────────────────


class TestBudgetDecisionResultV1:
    def test_valid_construction(self) -> None:
        result = BudgetDecisionResultV1(
            allowed=True,
            scope_id="scope-dec-1",
            role="director",
            remaining_tokens=800,
            estimated_cost_usd=0.05,
            reason="within_limit",
        )
        assert result.allowed is True
        assert result.remaining_tokens == 800

    def test_negative_remaining_raises(self) -> None:
        with pytest.raises(ValueError, match="remaining_tokens must be >= 0"):
            BudgetDecisionResultV1(
                allowed=True,
                scope_id="s1",
                role="director",
                remaining_tokens=-1,
                estimated_cost_usd=0.0,
            )

    def test_negative_cost_raises(self) -> None:
        with pytest.raises(ValueError, match="estimated_cost_usd must be >= 0"):
            BudgetDecisionResultV1(
                allowed=True,
                scope_id="s1",
                role="director",
                remaining_tokens=100,
                estimated_cost_usd=-0.01,
            )


# ── CFOAgent: GetBudgetStatusQueryV1 ──────────────────────────────────────────


class TestGetBudgetStatusQueryV1:
    def test_valid_construction(self) -> None:
        query = GetBudgetStatusQueryV1(scope_id="scope-q1", workspace="/tmp")
        assert query.scope_id == "scope-q1"
        assert query.workspace == "/tmp"

    def test_empty_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="scope_id must be a non-empty string"):
            GetBudgetStatusQueryV1(scope_id="", workspace="/tmp")


# ── Integration: end-to-end budget lifecycle ────────────────────────────────────


class TestBudgetLifecycle:
    def test_full_lifecycle(self, cfo_agent: CFOAgent) -> None:
        """Simulate a complete budget lifecycle: allocate → check (ok) → record → check (exceeded)."""
        # 1. Allocate 1000-token budget
        alloc = cfo_agent._tool_allocate_budget(
            task_id="task-lifecycle",
            budget_type="general",
            limit=1000,
            unit="tokens",
        )
        assert alloc["ok"] is True

        # 2. Check within limit
        check1 = cfo_agent._tool_check_budget(task_id="task-lifecycle", estimated_cost=500)
        assert check1["within_budget"] is True

        # 3. Record 600 tokens usage
        record = cfo_agent._tool_record_usage(
            task_id="task-lifecycle",
            agent_id="director",
            resource_type="general",
            amount=600,
        )
        assert record["ok"] is True

        # 4. Check — now 400 remaining, 500 request exceeds
        check2 = cfo_agent._tool_check_budget(task_id="task-lifecycle", estimated_cost=500)
        assert check2["within_budget"] is False
        assert check2["reason"] == "exceeds_limit"
        assert check2["remaining"] == 400

        # 5. Get stats
        stats = cfo_agent._tool_get_usage_stats(task_id="task-lifecycle")
        assert stats["totals"]["general"] == 600

        # 6. Get status
        status = cfo_agent._tool_get_budget_status("task-lifecycle")
        assert status["budgets"][0]["used"] == 600
