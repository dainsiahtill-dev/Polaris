from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "scripts" / "director_result_matcher.py"
    spec = importlib.util.spec_from_file_location("director_result_matcher", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load director_result_matcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_result(**extra: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "task_id": "task-1",
        "run_id": "pm-00001",
        "timestamp": now.isoformat(),
        "timestamp_epoch": now.timestamp(),
    }
    payload.update(extra)
    return payload


def test_normalize_match_mode_defaults_latest():
    mod = _load_module()
    assert mod.normalize_match_mode("strict") == "strict"
    assert mod.normalize_match_mode("run_id") == "run_id"
    assert mod.normalize_match_mode("ANY") == "any"
    assert mod.normalize_match_mode("unknown") == "latest"
    assert mod.normalize_match_mode(None) == "latest"


def test_result_timestamp_epoch_supports_multiple_fields():
    mod = _load_module()
    now = datetime.now(timezone.utc)
    iso_z = now.isoformat().replace("+00:00", "Z")
    from_epoch = mod.result_timestamp_epoch({"timestamp_epoch": now.timestamp()})
    from_iso = mod.result_timestamp_epoch({"timestamp_iso": iso_z})
    from_timestamp = mod.result_timestamp_epoch({"timestamp": now.isoformat()})
    assert abs(from_epoch - now.timestamp()) < 0.001
    assert abs(from_iso - now.timestamp()) < 0.001
    assert abs(from_timestamp - now.timestamp()) < 0.001


def test_match_director_result_mode_obeys_mode_and_since_timestamp():
    mod = _load_module()
    now = datetime.now(timezone.utc)
    recent = _make_result(task_id="task-1", run_id="pm-1", timestamp_epoch=now.timestamp())
    stale = _make_result(
        task_id="task-1",
        run_id="pm-1",
        timestamp_epoch=(now - timedelta(seconds=5)).timestamp(),
    )

    assert mod.match_director_result_mode(recent, ["task-1"], "pm-1", now.timestamp() - 1, "strict") is not None
    assert mod.match_director_result_mode(recent, ["task-2"], "pm-1", now.timestamp() - 1, "strict") is None
    assert mod.match_director_result_mode(recent, ["task-2"], "pm-1", now.timestamp() - 1, "any") is None
    assert mod.match_director_result_mode(recent, ["task-1"], "pm-2", now.timestamp() - 1, "run_id") is None
    assert mod.match_director_result_mode(recent, ["task-2"], "pm-2", now.timestamp() - 1, "latest") is not None
    assert mod.match_director_result_mode(stale, ["task-1"], "pm-1", now.timestamp(), "strict") is None


def test_wait_for_director_result_mode_reads_until_match_or_timeout():
    mod = _load_module()
    now = datetime.now(timezone.utc)
    payload = _make_result(task_id="task-x", run_id="pm-x", timestamp_epoch=now.timestamp())
    responses = [{"task_id": "old", "timestamp_epoch": now.timestamp()}, payload]
    state = {"idx": 0}

    def fake_reader(_path: str):
        idx = state["idx"]
        if idx >= len(responses):
            return responses[-1]
        state["idx"] = idx + 1
        return responses[idx]

    matched = mod.wait_for_director_result_mode(
        "fake.json",
        ["task-x"],
        "pm-x",
        now.timestamp() - 1,
        1,
        "run_id",
        fake_reader,
        poll_interval_s=0.0,
    )
    assert matched["task_id"] == "task-x"

    timeout = mod.wait_for_director_result_mode(
        "fake.json",
        ["never"],
        "pm-never",
        now.timestamp() - 1,
        1,
        "strict",
        lambda _path: {"task_id": "other", "timestamp_epoch": now.timestamp()},
        poll_interval_s=0.0,
    )
    assert timeout.get("status") == "blocked"
    assert timeout.get("error_code") == "DIRECTOR_NO_RESULT"


def test_wait_for_director_result_mode_zero_timeout_means_no_deadline():
    mod = _load_module()
    now = datetime.now(timezone.utc)
    payload = _make_result(task_id="task-z", run_id="pm-z", timestamp_epoch=now.timestamp())
    responses = [{"task_id": "old", "timestamp_epoch": now.timestamp()}, payload]
    state = {"idx": 0}

    def fake_reader(_path: str):
        idx = state["idx"]
        if idx >= len(responses):
            return responses[-1]
        state["idx"] = idx + 1
        return responses[idx]

    matched = mod.wait_for_director_result_mode(
        "fake.json",
        ["task-z"],
        "pm-z",
        now.timestamp() - 1,
        0,
        "run_id",
        fake_reader,
        poll_interval_s=0.0,
    )
    assert matched["task_id"] == "task-z"
