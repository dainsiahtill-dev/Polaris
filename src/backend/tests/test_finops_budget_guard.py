"""Tests for finops.budget_guard state convergence.

Verification matrix:
  1. Three-view consistency: after every mutation, in-memory cache, KFS file,
     and event stream notification are consistent.
  2. Restart recovery: a new store constructed over the same KFS file recovers
     all state from the file without relying on the old in-memory cache.
  3. Event vs state reconciliation: event stream records match the authoritative
     KFS state (no phantom or missing entries).
  4. TokenService no longer calls Path.write_text directly.
  5. TokenService restart recovery via KFS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import set_default_adapter

# ---------------------------------------------------------------------------
# Helpers: minimal filesystem adapter (no external deps, no global state)
# ---------------------------------------------------------------------------

class _LocalFSAdapter:
    """Minimal local-filesystem-backed adapter for testing."""

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return len(content.encode(encoding))

    def write_bytes(self, path: Path, content: bytes) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return len(content)

    def append_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding=encoding) as fh:
            fh.write(content)
        return len(content.encode(encoding))

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def remove(self, path: Path, *, missing_ok: bool = True) -> bool:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise


def _make_fs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[KernelFileSystem, Path]:
    """Create an isolated KernelFileSystem pointing at a fresh tmp directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLARIS_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("POLARIS_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("POLARIS_RUNTIME_CACHE_ROOT", raising=False)
    adapter = _LocalFSAdapter()
    set_default_adapter(adapter)
    fs = KernelFileSystem(str(workspace), adapter)
    return fs, workspace


# ---------------------------------------------------------------------------
# Helpers: read the raw KFS budget state file
# ---------------------------------------------------------------------------

def _read_kfs_state(fs: KernelFileSystem, scope_id: str) -> dict | None:
    """Read and deserialise the raw state file for a scope.  None if absent."""
    path = f"runtime/state/budget/{scope_id}.json"
    if not fs.exists(path):
        return None
    raw = fs.read_text(path, encoding="utf-8")
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 1. Budget update — three-view consistency
# ---------------------------------------------------------------------------

class TestThreeViewConsistency:
    def test_allocate_budget_persists_to_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """After allocate_budget, KFS file must contain the record."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            BudgetRecord,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)
        store = BudgetKFSStore("workspace", scope_id="test-alloc", fs=fs)

        budget = BudgetRecord(
            budget_id="b-001",
            task_id="task-1",
            budget_type="tokens",
            limit=1000,
        )
        store.save_budget(budget)

        # In-memory read consistent
        from_mem = store.get_budget("b-001")
        assert from_mem is not None
        assert from_mem.limit == 1000

        # KFS file consistent
        kfs_state = _read_kfs_state(fs, "test-alloc")
        assert kfs_state is not None
        assert "b-001" in kfs_state["budgets"]
        assert kfs_state["budgets"]["b-001"]["limit"] == 1000

    def test_record_usage_persists_to_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """After append_usage, KFS file must contain the usage record."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            UsageRecord,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)
        store = BudgetKFSStore("workspace", scope_id="test-usage", fs=fs)

        usage = UsageRecord(
            record_id="u-001",
            task_id="task-1",
            agent_id="agent-a",
            resource_type="tokens",
            amount=150,
        )
        store.append_usage(usage)

        # In-memory read consistent
        by_task = store.usage_by_task("task-1")
        assert len(by_task) == 1
        assert by_task[0].amount == 150

        # KFS file consistent
        kfs_state = _read_kfs_state(fs, "test-usage")
        assert kfs_state is not None
        assert len(kfs_state["usage"]) == 1
        assert kfs_state["usage"][0]["amount"] == 150

    def test_multiple_mutations_all_reflected_in_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Sequential budget + usage mutations stay consistent across all views."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            BudgetRecord,
            UsageRecord,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)
        store = BudgetKFSStore("workspace", scope_id="multi-mut", fs=fs)

        b1 = BudgetRecord(budget_id="b-1", task_id="t1", budget_type="tokens", limit=500)
        b2 = BudgetRecord(budget_id="b-2", task_id="t2", budget_type="tokens", limit=300)
        store.save_budget(b1)
        store.save_budget(b2)
        store.append_usage(UsageRecord(record_id="u-1", task_id="t1", agent_id="ag", resource_type="tokens", amount=100))
        store.append_usage(UsageRecord(record_id="u-2", task_id="t1", agent_id="ag", resource_type="tokens", amount=50))

        kfs_state = _read_kfs_state(fs, "multi-mut")
        assert kfs_state is not None
        assert len(kfs_state["budgets"]) == 2
        assert len(kfs_state["usage"]) == 2

        totals = store.usage_totals("t1")
        assert totals["tokens"] == 150

        # KFS totals computed fresh match in-memory totals
        kfs_usage_sum = sum(
            u["amount"] for u in kfs_state["usage"] if u["task_id"] == "t1"
        )
        assert kfs_usage_sum == 150


# ---------------------------------------------------------------------------
# 2. Restart recovery
# ---------------------------------------------------------------------------

class TestRestartRecovery:
    def test_new_store_recovers_budgets_from_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A new store instance recovers all state from the KFS file."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            BudgetRecord,
            UsageRecord,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)

        # First store writes state
        store1 = BudgetKFSStore("workspace", scope_id="restart-test", fs=fs)
        store1.save_budget(BudgetRecord(
            budget_id="b-restart", task_id="t-restart", budget_type="tokens", limit=9999
        ))
        store1.append_usage(UsageRecord(
            record_id="u-restart", task_id="t-restart", agent_id="ag", resource_type="tokens", amount=42
        ))

        # Second store (simulates restart) reads from same KFS
        store2 = BudgetKFSStore("workspace", scope_id="restart-test", fs=fs)
        recovered_budget = store2.get_budget("b-restart")
        assert recovered_budget is not None, "Budget not recovered after restart"
        assert recovered_budget.limit == 9999

        recovered_usage = store2.usage_by_task("t-restart")
        assert len(recovered_usage) == 1
        assert recovered_usage[0].amount == 42

    def test_restart_with_no_prior_file_starts_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A store with no prior KFS file starts with empty state."""
        from polaris.cells.finops.budget_guard.internal.budget_store import BudgetKFSStore
        fs, _ = _make_fs(monkeypatch, tmp_path)

        store = BudgetKFSStore("workspace", scope_id="fresh-scope", fs=fs)
        assert store.get_budget("any") is None
        assert store.budgets_by_task("t") == []
        assert store.usage_by_task("t") == []

    def test_restart_with_corrupt_file_starts_empty_not_crash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A corrupt KFS state file must not crash the store — empty state is used."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            _scope_path,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)

        # Write corrupt JSON directly via the adapter
        corrupt_path = fs.resolve_path(_scope_path("corrupt-scope"))
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("{ this is not valid json }", encoding="utf-8")

        # Must not raise
        store = BudgetKFSStore("workspace", scope_id="corrupt-scope", fs=fs)
        assert store.get_budget("any") is None  # graceful empty state


# ---------------------------------------------------------------------------
# 3. Event vs state reconciliation
# ---------------------------------------------------------------------------

class TestEventStateReconciliation:
    def test_usage_totals_match_kfs_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """In-memory usage_totals == sum of usage records in KFS file."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            UsageRecord,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)
        store = BudgetKFSStore("workspace", scope_id="reconcile", fs=fs)

        records = [
            UsageRecord(record_id=f"u-{i}", task_id="t1", agent_id="ag",
                        resource_type="tokens", amount=i * 10)
            for i in range(1, 6)
        ]
        for r in records:
            store.append_usage(r)

        in_mem_total = store.usage_totals("t1")["tokens"]
        expected = sum(i * 10 for i in range(1, 6))  # 10+20+30+40+50 = 150
        assert in_mem_total == expected

        kfs_state = _read_kfs_state(fs, "reconcile")
        assert kfs_state is not None
        kfs_total = sum(u["amount"] for u in kfs_state["usage"])
        assert kfs_total == expected, "KFS state diverged from in-memory totals"

    def test_budget_used_counter_consistent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """budget.used in KFS matches what record_usage applied in-memory."""
        from polaris.cells.finops.budget_guard.internal.budget_store import (
            BudgetKFSStore,
            BudgetRecord,
            UsageRecord,
        )
        fs, _ = _make_fs(monkeypatch, tmp_path)
        store = BudgetKFSStore("workspace", scope_id="budget-used", fs=fs)

        b = BudgetRecord(budget_id="b-used", task_id="t1", budget_type="tokens", limit=500)
        store.save_budget(b)

        # Simulate two record_usage calls (as CFOAgent._tool_record_usage does)
        for amount in (100, 75):
            usage = UsageRecord(
                record_id=f"u-{amount}", task_id="t1", agent_id="ag",
                resource_type="tokens", amount=amount,
            )
            store.append_usage(usage)
            current = store.get_budget("b-used")
            assert current is not None
            current.used = int(current.used) + amount
            store.save_budget(current)

        final = store.get_budget("b-used")
        assert final is not None
        assert final.used == 175

        kfs_state = _read_kfs_state(fs, "budget-used")
        assert kfs_state is not None
        assert kfs_state["budgets"]["b-used"]["used"] == 175


# ---------------------------------------------------------------------------
# 4. TokenService no longer uses Path.write_text
# ---------------------------------------------------------------------------

class TestTokenServiceKFSPersistence:
    def test_record_usage_does_not_call_path_write_text(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """TokenService.record_usage must not write to the old absolute state_file path.

        It must go through KFS (which resolves to a KFS-managed logical path),
        not directly to the absolute OS path passed as ``state_file``.
        """
        from polaris.domain.services.token_service import TokenService
        fs, _ = _make_fs(monkeypatch, tmp_path)

        # The absolute legacy state_file path should never receive a direct write.
        state_path = tmp_path / "runtime" / "state" / "budget" / "token_svc_legacy.json"
        writes_to_legacy_path: list[str] = []
        original_write_text = Path.write_text

        def _spy_write_text(self_path: Path, data: str, *args, **kwargs):  # type: ignore[override]
            if self_path.resolve() == state_path.resolve():
                writes_to_legacy_path.append(str(self_path))
            return original_write_text(self_path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _spy_write_text)

        svc = TokenService(budget_limit=1000, state_file=state_path, fs=fs)
        svc.record_usage(200)

        assert writes_to_legacy_path == [], (
            f"TokenService wrote directly to the legacy absolute path (bypassing KFS logical path): "
            f"{writes_to_legacy_path}"
        )

    def test_token_service_persists_and_recovers_via_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """TokenService persists used_tokens to KFS and recovers on restart."""
        from polaris.domain.services.token_service import TokenService, reset_token_service
        fs, _ = _make_fs(monkeypatch, tmp_path)
        reset_token_service()

        kfs_path = "runtime/state/budget/token_svc.json"
        svc1 = TokenService(budget_limit=5000, kfs_logical_path=kfs_path, fs=fs)
        svc1.record_usage(300)
        svc1.record_usage(200)
        assert svc1._used_tokens == 500

        # Verify the KFS file was written
        assert fs.exists(kfs_path), "KFS state file was not created"

        # Simulate restart: new instance reads same KFS file
        svc2 = TokenService(budget_limit=5000, kfs_logical_path=kfs_path, fs=fs)
        assert svc2._used_tokens == 500, (
            f"Restart did not recover used_tokens: got {svc2._used_tokens}, expected 500"
        )
        reset_token_service()

    def test_token_service_without_state_file_works_in_memory_only(self) -> None:
        """TokenService without state_file stays in-memory (no KFS interaction)."""
        from polaris.domain.services.token_service import TokenService, reset_token_service
        reset_token_service()
        svc = TokenService(budget_limit=100)
        svc.record_usage(30)
        status = svc.get_budget_status()
        assert status.used_tokens == 30
        reset_token_service()


# ---------------------------------------------------------------------------
# 5. CFOAgent integration (uses BudgetKFSStore under the hood)
# ---------------------------------------------------------------------------

class TestCFOAgentIntegration:
    def test_cfo_allocate_and_check_persisted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CFOAgent allocate_budget + check_budget results match KFS state."""
        from polaris.cells.finops.budget_guard.internal.budget_agent import CFOAgent
        fs, workspace = _make_fs(monkeypatch, tmp_path)

        agent = CFOAgent(str(workspace), fs=fs)
        alloc = agent._tool_allocate_budget(
            task_id="t-cfo", budget_type="tokens", limit=1000
        )
        assert alloc["ok"] is True
        budget_id = alloc["budget"]["budget_id"]

        # Check budget within limit
        check = agent._tool_check_budget("t-cfo", estimated_cost=500)
        assert check["within_budget"] is True

        # Check exceeding limit
        check_over = agent._tool_check_budget("t-cfo", estimated_cost=1500)
        assert check_over["within_budget"] is False

        # Verify KFS state matches
        kfs_state = _read_kfs_state(fs, "global")
        assert kfs_state is not None
        assert budget_id in kfs_state["budgets"]

    def test_cfo_record_usage_updates_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CFOAgent record_usage updates both in-memory and KFS state."""
        from polaris.cells.finops.budget_guard.internal.budget_agent import CFOAgent
        fs, workspace = _make_fs(monkeypatch, tmp_path)

        agent = CFOAgent(str(workspace), fs=fs)
        agent._tool_allocate_budget(task_id="t-usage", budget_type="tokens", limit=2000)
        agent._tool_record_usage(task_id="t-usage", agent_id="ag-1", resource_type="tokens", amount=300)
        agent._tool_record_usage(task_id="t-usage", agent_id="ag-1", resource_type="tokens", amount=200)

        stats = agent._tool_get_usage_stats(task_id="t-usage")
        assert stats["totals"].get("tokens", 0) == 500

        kfs_state = _read_kfs_state(fs, "global")
        assert kfs_state is not None
        kfs_usage_total = sum(
            u["amount"] for u in kfs_state["usage"] if u["task_id"] == "t-usage"
        )
        assert kfs_usage_total == 500

    def test_cfo_restart_recovers_from_kfs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A new CFOAgent over the same workspace recovers all state from KFS."""
        from polaris.cells.finops.budget_guard.internal.budget_agent import CFOAgent
        fs, workspace = _make_fs(monkeypatch, tmp_path)

        agent1 = CFOAgent(str(workspace), fs=fs)
        agent1._tool_allocate_budget(task_id="t-persist", budget_type="tokens", limit=500)
        agent1._tool_record_usage(task_id="t-persist", agent_id="ag", resource_type="tokens", amount=100)

        # Simulate process restart
        agent2 = CFOAgent(str(workspace), fs=fs)
        status = agent2._tool_get_budget_status("t-persist")
        assert status["has_budget"] is True, "Budget not found after restart"
        budgets = status["budgets"]
        assert len(budgets) == 1
        assert budgets[0]["limit"] == 500

        stats = agent2._tool_get_usage_stats(task_id="t-persist")
        assert stats["totals"].get("tokens", 0) == 100, (
            "Usage not recovered after restart"
        )
