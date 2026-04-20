"""Director result matching helpers.

Canonical location for PM/Director loop result correlation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

JsonReader = Callable[[str], Any]


def normalize_match_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in ("latest", "any", "strict", "run_id"):
        return mode
    return "latest"


def result_timestamp_epoch(result: dict[str, Any]) -> float:
    raw_epoch = result.get("timestamp_epoch")
    if isinstance(raw_epoch, (int, float)):
        try:
            return float(raw_epoch)
        except (RuntimeError, ValueError):
            return 0.0
    ts = str(result.get("timestamp_iso") or "").strip()
    if ts:
        try:
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            return datetime.fromisoformat(ts).timestamp()
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to parse timestamp_iso: %s", exc)
    ts = str(result.get("timestamp") or "").strip()
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (RuntimeError, ValueError):
        return 0.0


def _is_recent_enough(result: dict[str, Any], since_ts: float, tolerance_s: float) -> bool:
    if not since_ts:
        return True
    return result_timestamp_epoch(result) >= (since_ts - tolerance_s)


def match_director_result(
    result: Any,
    expected_task_id: str,
    since_ts: float,
    *,
    tolerance_s: float = 1.0,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    task_id = str(result.get("task_id") or "")
    if not task_id or not expected_task_id or task_id != expected_task_id:
        return None
    if not _is_recent_enough(result, since_ts, tolerance_s):
        return None
    return result


def match_director_result_any(
    result: Any,
    expected_task_ids: list[str],
    since_ts: float,
    *,
    tolerance_s: float = 1.0,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    task_id = str(result.get("task_id") or "").strip()
    if expected_task_ids and (not task_id or task_id not in expected_task_ids):
        return None
    if not _is_recent_enough(result, since_ts, tolerance_s):
        return None
    return result


def match_director_result_mode(
    result: Any,
    expected_task_ids: list[str],
    expected_run_id: str,
    since_ts: float,
    mode: str,
    *,
    tolerance_s: float = 1.0,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    if not _is_recent_enough(result, since_ts, tolerance_s):
        return None

    normalized_mode = normalize_match_mode(mode)
    task_id = str(result.get("task_id") or "").strip()

    if normalized_mode == "latest":
        return result
    if normalized_mode == "run_id":
        run_id = str(result.get("run_id") or "").strip()
        if expected_run_id and run_id != expected_run_id:
            return None
        return result
    if normalized_mode == "any":
        if expected_task_ids and (not task_id or task_id not in expected_task_ids):
            return None
        return result

    expected_task_id = expected_task_ids[0] if expected_task_ids else ""
    if not expected_task_id or task_id != expected_task_id:
        return None
    return result


def wait_for_director_result(
    path: str,
    expected_task_id: str,
    since_ts: float,
    timeout_s: int,
    read_json_file: JsonReader,
    *,
    poll_interval_s: float = 1.0,
    tolerance_s: float = 0.0,
) -> dict[str, Any]:
    deadline: float | None = None
    if timeout_s is not None and int(timeout_s) > 0:
        deadline = time.time() + int(timeout_s)
    while True:
        if deadline is not None and time.time() >= deadline:
            break
        data = read_json_file(path)
        if (
            match_director_result(
                data,
                expected_task_id,
                since_ts,
                tolerance_s=tolerance_s,
            )
            is not None
        ):
            return data if isinstance(data, dict) else {"status": "unknown"}
        if poll_interval_s > 0:
            time.sleep(poll_interval_s)
    return {"status": "blocked", "error_code": "DIRECTOR_NO_RESULT"}


def wait_for_director_result_mode(
    path: str,
    expected_task_ids: list[str],
    expected_run_id: str,
    since_ts: float,
    timeout_s: int,
    mode: str,
    read_json_file: JsonReader,
    *,
    poll_interval_s: float = 1.0,
    tolerance_s: float = 1.0,
) -> dict[str, Any]:
    deadline: float | None = None
    if timeout_s is not None and int(timeout_s) > 0:
        deadline = time.time() + int(timeout_s)
    while True:
        if deadline is not None and time.time() >= deadline:
            break
        data = read_json_file(path)
        if (
            match_director_result_mode(
                data,
                expected_task_ids,
                expected_run_id,
                since_ts,
                mode,
                tolerance_s=tolerance_s,
            )
            is not None
        ):
            return data if isinstance(data, dict) else {"status": "unknown"}
        if poll_interval_s > 0:
            time.sleep(poll_interval_s)
    return {"status": "blocked", "error_code": "DIRECTOR_NO_RESULT"}
