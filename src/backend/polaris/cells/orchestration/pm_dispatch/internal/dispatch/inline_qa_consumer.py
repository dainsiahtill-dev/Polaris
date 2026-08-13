"""Inline integration-QA consumer construction + result artifact writer.

Extracted losslessly from ``dispatch_pipeline.py``. These helpers build the
inline QA consumer and persist the mainline-full integration-QA result
artifact. They hold no module-level cross-Cell imports and are not test
monkeypatch targets, so moving them into this sibling module is lossless.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def _build_inline_qa_consumer(
    qa_consumer_type: Any,
    *,
    workspace_full: str,
    worker_id: str,
    visibility_timeout_seconds: int,
    poll_interval: float,
) -> Any:
    try:
        return qa_consumer_type(
            workspace=workspace_full,
            worker_id=worker_id,
            visibility_timeout_seconds=visibility_timeout_seconds,
            poll_interval=poll_interval,
            enable_llm_audit=True,
        )
    except TypeError:
        return qa_consumer_type(
            workspace=workspace_full,
            worker_id=worker_id,
            visibility_timeout_seconds=visibility_timeout_seconds,
            poll_interval=poll_interval,
        )


def _write_mainline_full_integration_qa_result(
    *,
    workspace_full: str,
    cache_root_full: str,
    run_id: str,
    iteration: int,
    integration_qa_result: dict[str, Any],
) -> str:
    payload = {
        "schema_version": 1,
        "enabled": True,
        "ran": True,
        "passed": bool(integration_qa_result.get("passed", False)),
        "reason": str(integration_qa_result.get("reason") or ""),
        "summary": str(integration_qa_result.get("summary") or ""),
        "errors": list(integration_qa_result.get("errors") or []),
        "run_id": run_id,
        "pm_iteration": int(iteration or 0),
        "workspace": workspace_full,
        "execution_mode": "task_market_mainline_full",
        "qa_path": "task_market_inline",
        "qa_results": list(integration_qa_result.get("qa_results") or []),
        "unresolved_task_ids": list(integration_qa_result.get("unresolved_task_ids") or []),
        "rejected_task_ids": list(integration_qa_result.get("rejected_task_ids") or []),
        "publish_failed_task_ids": list(integration_qa_result.get("publish_failed_task_ids") or []),
        "timestamp": datetime.now().replace(microsecond=0).isoformat(),
    }
    try:
        from polaris.kernelone.fs.text_ops import write_json_atomic
        from polaris.kernelone.storage.io_paths import resolve_artifact_path

        target = resolve_artifact_path(workspace_full, cache_root_full, "runtime/results/integration_qa.result.json")
        if not target:
            return ""
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        write_json_atomic(target, payload)
        return target
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("mainline-full integration QA artifact write failed: %s", exc)
        return ""
