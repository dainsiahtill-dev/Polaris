"""KFS-backed budget store: single source of truth for finops.budget_guard.

Architecture contract:
  - KFS JSON file at ``runtime/state/budget/<scope_id>.json`` is the
    authoritative persistent state (source-of-truth).
  - The in-memory dict is a write-through cache: every mutation writes to KFS
    first, then updates the cache.  Reads always come from the cache.
  - On construction the cache is populated from KFS if the file exists
    (restart-recovery).
  - The event stream (``runtime/events/runtime.events.jsonl``) is notification-
    only; it is never read back to reconstruct state.

KernelOne FS effect contract declared in cell.yaml:
  - fs.read:runtime/**
  - fs.write:runtime/state/budget/*

If the KernelOne default adapter is not bootstrapped (test / standalone
contexts), callers must inject a ``KernelFileSystem`` directly via
``BudgetKFSStore(fs=...)``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.utils.time_utils import utc_now_str

logger = logging.getLogger(__name__)

_BUDGET_STATE_PATH_PREFIX = "runtime/state/budget"


@dataclass
class BudgetRecord:
    budget_id: str
    task_id: str
    budget_type: str
    limit: int
    used: int = 0
    unit: str = "tokens"
    status: str = "active"
    created_at: str = field(default_factory=utc_now_str)
    updated_at: str = field(default_factory=utc_now_str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "task_id": self.task_id,
            "budget_type": self.budget_type,
            "limit": int(self.limit),
            "used": int(self.used),
            "unit": self.unit,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> BudgetRecord:
        return BudgetRecord(
            budget_id=str(data["budget_id"]),
            task_id=str(data["task_id"]),
            budget_type=str(data.get("budget_type", "general")),
            limit=int(data.get("limit", 0)),
            used=int(data.get("used", 0)),
            unit=str(data.get("unit", "tokens")),
            status=str(data.get("status", "active")),
            created_at=str(data.get("created_at", utc_now_str())),
            updated_at=str(data.get("updated_at", utc_now_str())),
        )


@dataclass
class UsageRecord:
    record_id: str
    task_id: str
    agent_id: str
    resource_type: str
    amount: int
    timestamp: str = field(default_factory=utc_now_str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "resource_type": self.resource_type,
            "amount": int(self.amount),
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> UsageRecord:
        return UsageRecord(
            record_id=str(data["record_id"]),
            task_id=str(data["task_id"]),
            agent_id=str(data.get("agent_id", "")),
            resource_type=str(data.get("resource_type", "general")),
            amount=int(data.get("amount", 0)),
            timestamp=str(data.get("timestamp", utc_now_str())),
        )


def _scope_path(scope_id: str) -> str:
    """Return the KFS logical path for a scope's budget state file."""
    safe = str(scope_id or "").strip().replace("/", "_").replace("\\", "_") or "default"
    return f"{_BUDGET_STATE_PATH_PREFIX}/{safe}.json"


def _serialize_state(
    budgets: dict[str, BudgetRecord],
    usage: list[UsageRecord],
) -> str:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "budgets": {bid: b.to_dict() for bid, b in budgets.items()},
        "usage": [u.to_dict() for u in usage],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _deserialize_state(raw: str) -> tuple[dict[str, BudgetRecord], list[UsageRecord]]:
    data = json.loads(raw)
    budgets: dict[str, BudgetRecord] = {}
    for bid, bdata in (data.get("budgets") or {}).items():
        try:
            budgets[bid] = BudgetRecord.from_dict(bdata)
        except (RuntimeError, ValueError) as exc:
            logger.warning("budget_guard: skipping corrupt budget record %s: %s", bid, exc)
    usage: list[UsageRecord] = []
    for udata in data.get("usage") or []:
        try:
            usage.append(UsageRecord.from_dict(udata))
        except (RuntimeError, ValueError) as exc:
            logger.warning("budget_guard: skipping corrupt usage record: %s", exc)
    return budgets, usage


class BudgetKFSStore:
    """Thread-safe, KFS-backed write-through budget store.

    The KFS JSON file is the authoritative state.  The in-memory dicts are a
    read cache that is always consistent with the file (all mutations flush to
    KFS before updating the cache).

    Parameters
    ----------
    workspace:
        Workspace root for the ``KernelFileSystem``.  Ignored when ``fs`` is
        provided directly.
    scope_id:
        Logical scope identifier (e.g. run_id or task_id).  Controls the
        KFS path: ``runtime/state/budget/<scope_id>.json``.
    fs:
        Optional injected ``KernelFileSystem``.  When ``None`` the default
        adapter from the registry is used (requires bootstrap).
    """

    def __init__(
        self,
        workspace: str,
        scope_id: str,
        *,
        fs: KernelFileSystem | None = None,
    ) -> None:
        self._workspace = str(workspace or "").strip()
        self._scope_id = str(scope_id or "").strip() or "default"
        self._kfs_path = _scope_path(self._scope_id)
        self._lock = RLock()

        # KFS handle — injected or resolved from registry
        if fs is not None:
            self._fs = fs
        else:
            self._fs = KernelFileSystem(self._workspace, get_default_adapter())

        # In-memory write-through cache
        self._budgets: dict[str, BudgetRecord] = {}
        self._usage: list[UsageRecord] = []

        # Recover from KFS on startup
        self._load_from_kfs()

    # ------------------------------------------------------------------
    # Public read API (reads from in-memory cache — always consistent)
    # ------------------------------------------------------------------

    def get_budget(self, budget_id: str) -> BudgetRecord | None:
        token = str(budget_id or "").strip()
        with self._lock:
            return self._budgets.get(token)

    def budgets_by_task(self, task_id: str) -> list[BudgetRecord]:
        token = str(task_id or "").strip()
        with self._lock:
            rows = [b for b in self._budgets.values() if b.task_id == token]
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)

    def usage_by_task(self, task_id: str) -> list[UsageRecord]:
        token = str(task_id or "").strip()
        with self._lock:
            return [u for u in self._usage if u.task_id == token]

    def usage_by_agent(self, agent_id: str) -> list[UsageRecord]:
        token = str(agent_id or "").strip()
        with self._lock:
            return [u for u in self._usage if u.agent_id == token]

    def usage_totals(self, task_id: str | None = None) -> dict[str, int]:
        token = str(task_id or "").strip() if task_id is not None else ""
        with self._lock:
            rows = [u for u in self._usage if (not token or u.task_id == token)]
        totals: dict[str, int] = {}
        for row in rows:
            totals[row.resource_type] = totals.get(row.resource_type, 0) + int(row.amount)
        return totals

    # ------------------------------------------------------------------
    # Public write API (write-through: KFS first, then cache)
    # ------------------------------------------------------------------

    def save_budget(self, budget: BudgetRecord) -> None:
        """Persist a budget record.  KFS is updated before the cache."""
        with self._lock:
            budget.updated_at = utc_now_str()
            # Stage into a copy first — flush may raise; we must not corrupt
            # the cache before the durable write succeeds.
            staged = dict(self._budgets)
            staged[budget.budget_id] = budget
            staged_usage = list(self._usage)
            self._flush_to_kfs(staged, staged_usage)
            # Durable write succeeded — update cache.
            self._budgets = staged

    def append_usage(self, usage: UsageRecord) -> None:
        """Append a usage record.  KFS is updated before the cache."""
        with self._lock:
            staged_budgets = dict(self._budgets)
            staged_usage = [*list(self._usage), usage]
            self._flush_to_kfs(staged_budgets, staged_usage)
            self._usage = staged_usage

    # ------------------------------------------------------------------
    # Persistence internals
    # ------------------------------------------------------------------

    def _flush_to_kfs(
        self,
        budgets: dict[str, BudgetRecord],
        usage: list[UsageRecord],
    ) -> None:
        """Atomically overwrite the KFS state file.

        Called while the caller already holds ``_lock``.
        """
        content = _serialize_state(budgets, usage)
        # KernelFileSystem.write_text enforces UTF-8 at the contract layer.
        self._fs.write_text(self._kfs_path, content, encoding="utf-8")

    def _load_from_kfs(self) -> None:
        """Recover in-memory cache from the KFS state file.

        Called once at construction.  Failures are logged and the cache
        remains empty — the store is still operable (blank-slate recovery).
        """
        with self._lock:
            try:
                if not self._fs.exists(self._kfs_path):
                    return
                raw = self._fs.read_text(self._kfs_path, encoding="utf-8")
                budgets, usage = _deserialize_state(raw)
                self._budgets = budgets
                self._usage = usage
                logger.debug(
                    "budget_guard: loaded %d budgets, %d usage records from %s",
                    len(budgets),
                    len(usage),
                    self._kfs_path,
                )
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "budget_guard: failed to load state from %s: %s — starting with empty state",
                    self._kfs_path,
                    exc,
                )
                self._budgets = {}
                self._usage = []
