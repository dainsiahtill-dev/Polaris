# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    RequeueTaskCommandV1,
)

from ..models import (
    TaskWorkItemRecord,
)
from ._constants import (
    _LEGACY_RESOLVED_REOPEN_SOURCES,
)

logger = logging.getLogger(__name__.rsplit(".", 1)[0])


class HelpersMixin:
    """Shared feedback, reopen-policy, and string helpers."""

    @staticmethod
    def _normalize_feedback_counters(*sources: Any) -> dict[str, int]:
        counters: dict[str, int] = {}
        for source in sources:
            if not isinstance(source, dict):
                continue
            for raw_key, raw_value in source.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                try:
                    value = int(raw_value or 0)
                except (TypeError, ValueError):
                    continue
                counters[key] = max(counters.get(key, 0), max(0, value))
        return counters

    @classmethod
    def _resolved_reopen_allowed(
        cls,
        item: TaskWorkItemRecord,
        command: RequeueTaskCommandV1,
    ) -> tuple[bool, str, str]:
        metadata = dict(command.metadata)
        source = str(metadata.get("source") or "").strip()
        if source in _LEGACY_RESOLVED_REOPEN_SOURCES:
            return True, "requeued", source

        policy = cls._resolved_reopen_policy(command)
        if not policy:
            return False, "terminal_status", source
        if not cls._reopen_policy_source_allowed(source, policy):
            return False, "terminal_status", source

        max_reopen_count = cls._policy_max_reopen_count(policy)
        if cls._safe_reopen_count(item.metadata) >= max_reopen_count:
            return False, "reopen_limit_exceeded", source

        if bool(policy.get("requires_failure_report", True)):
            failure_report = metadata.get("verification_failure_report") or metadata.get("failure_report")
            last_failure = metadata.get("last_failure")
            if not isinstance(failure_report, dict) and not isinstance(last_failure, dict):
                return False, "missing_failure_report", source

        return True, "requeued", source

    @staticmethod
    def _resolved_reopen_policy(command: RequeueTaskCommandV1) -> dict[str, Any]:
        metadata = dict(command.metadata)
        raw_policy = command.reopen_policy or metadata.get("reopen_policy") or metadata.get("reopenPolicy")
        return dict(raw_policy) if isinstance(raw_policy, dict) else {}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, (list, tuple, set)):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        return []

    @classmethod
    def _reopen_policy_source_allowed(cls, source: str, policy: dict[str, Any]) -> bool:
        if not source:
            return False
        allowed_sources = set(cls._string_list(policy.get("allowed_sources")))
        if source in allowed_sources:
            return True
        allowed_prefixes = cls._string_list(policy.get("allowed_source_prefixes"))
        return any(source.startswith(prefix) for prefix in allowed_prefixes)

    @staticmethod
    def _policy_max_reopen_count(policy: dict[str, Any]) -> int:
        try:
            value = int(policy.get("max_reopen_count", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, min(value, 20))

    @staticmethod
    def _safe_reopen_count(metadata: dict[str, Any]) -> int:
        try:
            return max(0, int(metadata.get("reopen_count", 0) or 0))
        except (TypeError, ValueError):
            return 0
