"""Scope conflict detection and lease heartbeat helpers."""

from __future__ import annotations

import sys
import threading
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
)


def _package() -> Any:
    """Return the package module for monkeypatch-visible late binding."""
    return sys.modules[__package__]


class ScopeConflictDetector:
    """Detect scope path conflicts with other in-progress tasks."""

    def check_conflict(self, workspace: str, current_task_id: str, scope_paths: list[str]) -> bool:
        """Return True if any other IN_EXECUTION task shares scope paths with current task."""
        normalized_scope = self._normalize_paths(scope_paths)
        if not normalized_scope:
            return False
        svc = _package().get_task_market_service()
        status = svc.query_status(
            QueryTaskMarketStatusV1(
                workspace=workspace,
                stage="pending_exec",
                include_payload=True,
                limit=5000,
            )
        )
        for item in status.items:
            if str(item.get("task_id") or "").strip() == str(current_task_id or "").strip():
                continue
            if item.get("is_leaf") is False:
                continue
            if str(item.get("status") or "").strip().lower() != "in_execution":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            candidate_paths = self.extract_conflict_paths(payload)
            if normalized_scope.intersection(candidate_paths):
                return True
        return False

    def extract_conflict_paths(self, payload: dict[str, Any]) -> set[str]:
        collected: list[str] = []
        raw_scope = payload.get("scope_paths")
        if isinstance(raw_scope, list):
            for row in raw_scope:
                if isinstance(row, str):
                    collected.append(row)
        raw_targets = payload.get("target_files")
        if isinstance(raw_targets, list):
            for row in raw_targets:
                if isinstance(row, str):
                    collected.append(row)
        raw_target = payload.get("target_file")
        if isinstance(raw_target, str):
            collected.append(raw_target)
        return self._normalize_paths(collected)

    def _normalize_paths(self, paths: list[str]) -> set[str]:
        normalized: set[str] = set()
        for raw in paths:
            token = str(raw or "").strip()
            if not token:
                continue
            normalized.add(token.replace("\\", "/").lower())
        return normalized


class _LeaseHeartbeat:
    """Background lease renewer for long-running execution."""

    def __init__(
        self,
        *,
        svc: Any,
        workspace: str,
        task_id: str,
        lease_token: str,
        visibility_timeout_seconds: int,
        interval_seconds: float,
    ) -> None:
        self._svc = svc
        self._workspace = workspace
        self._task_id = task_id
        self._lease_token = lease_token
        self._visibility_timeout_seconds = max(1, int(visibility_timeout_seconds))
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self._svc.renew_task_lease(
                    RenewTaskLeaseCommandV1(
                        workspace=self._workspace,
                        task_id=self._task_id,
                        lease_token=self._lease_token,
                        visibility_timeout_seconds=self._visibility_timeout_seconds,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                _package().logger.warning(
                    "Lease heartbeat failed: task_id=%s lease_token=%s error=%s",
                    self._task_id,
                    self._lease_token,
                    exc,
                )
                return
