"""Tests for polaris.infrastructure.db.repositories.accel_session_receipt_store module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.infrastructure.db.repositories.accel_session_receipt_store import (
    SessionReceiptError,
    SessionReceiptStore,
    _normalize_bool_flag,
    _normalize_receipt_status,
    _normalize_session_final_status,
    _normalize_ttl_seconds,
    _parse_utc,
    _to_meta_json,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def receipt_store(tmp_path: Path) -> SessionReceiptStore:
    db_path = tmp_path / "receipts.db"
    return SessionReceiptStore(db_path)


# =============================================================================
# _parse_utc
# =============================================================================


def test_parse_utc_iso() -> None:
    from datetime import datetime, timezone

    dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _parse_utc(dt.isoformat()) == dt


def test_parse_utc_z_suffix() -> None:
    result = _parse_utc("2024-01-01T00:00:00Z")
    assert result is not None


def test_parse_utc_empty() -> None:
    assert _parse_utc("") is None
    assert _parse_utc(None) is None


def test_parse_utc_invalid() -> None:
    assert _parse_utc("not-a-date") is None


# =============================================================================
# _normalize_bool_flag
# =============================================================================


def test_normalize_bool_flag_true_values() -> None:
    assert _normalize_bool_flag(True) is True
    assert _normalize_bool_flag("true") is True
    assert _normalize_bool_flag("yes") is True
    assert _normalize_bool_flag("on") is True
    assert _normalize_bool_flag(1) is True


def test_normalize_bool_flag_false_values() -> None:
    assert _normalize_bool_flag(False) is False
    assert _normalize_bool_flag(None) is False
    assert _normalize_bool_flag(0) is False
    assert _normalize_bool_flag("false") is False
    assert _normalize_bool_flag("") is False


# =============================================================================
# _normalize_receipt_status
# =============================================================================


def test_normalize_receipt_status_success_aliases() -> None:
    assert _normalize_receipt_status("success") == "succeeded"
    assert _normalize_receipt_status("ok") == "succeeded"
    assert _normalize_receipt_status("completed") == "succeeded"


def test_normalize_receipt_status_cancel_aliases() -> None:
    assert _normalize_receipt_status("cancelled") == "canceled"
    assert _normalize_receipt_status("cancelling") == "canceled"


def test_normalize_receipt_status_valid() -> None:
    for status in {"queued", "running", "succeeded", "failed", "canceled", "degraded", "partial"}:
        assert _normalize_receipt_status(status) == status


def test_normalize_receipt_status_invalid() -> None:
    with pytest.raises(SessionReceiptError):
        _normalize_receipt_status("unknown")


# =============================================================================
# _normalize_session_final_status
# =============================================================================


def test_normalize_session_final_status_defaults() -> None:
    assert _normalize_session_final_status("") == "closed"
    assert _normalize_session_final_status("closed") == "closed"
    assert _normalize_session_final_status("canceled") == "canceled"


def test_normalize_session_final_status_cancelled_alias() -> None:
    assert _normalize_session_final_status("cancelled") == "canceled"


def test_normalize_session_final_status_invalid() -> None:
    with pytest.raises(SessionReceiptError):
        _normalize_session_final_status("active")


# =============================================================================
# _normalize_ttl_seconds
# =============================================================================


def test_normalize_ttl_seconds_default() -> None:
    assert _normalize_ttl_seconds("invalid", default_value=1800) == 1800


def test_normalize_ttl_seconds_clamping() -> None:
    assert _normalize_ttl_seconds(10) == 30
    assert _normalize_ttl_seconds(999999) == 86400


def test_normalize_ttl_seconds_valid() -> None:
    assert _normalize_ttl_seconds(3600) == 3600


# =============================================================================
# _to_meta_json
# =============================================================================


def test_to_meta_json_dict() -> None:
    assert _to_meta_json({"a": 1}) == json.dumps({"a": 1}, ensure_ascii=False)


def test_to_meta_json_string() -> None:
    assert _to_meta_json('{"a": 1}') == json.dumps({"a": 1}, ensure_ascii=False)


def test_to_meta_json_empty_string() -> None:
    assert _to_meta_json("") == "{}"


def test_to_meta_json_invalid_string() -> None:
    assert _to_meta_json("not json") == "{}"


def test_to_meta_json_none() -> None:
    assert _to_meta_json(None) == "{}"


# =============================================================================
# open_session
# =============================================================================


def test_open_session_new(receipt_store: SessionReceiptStore) -> None:
    session = receipt_store.open_session(run_id="run1")
    assert session["run_id"] == "run1"
    assert session["status"] == "open"
    assert session["session_id"].startswith("s_")


def test_open_session_with_explicit_id(receipt_store: SessionReceiptStore) -> None:
    session = receipt_store.open_session(run_id="run1", session_id="sess1")
    assert session["session_id"] == "sess1"


def test_open_session_conflict_run_id(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    with pytest.raises(SessionReceiptError, match="already bound"):
        receipt_store.open_session(run_id="run2", session_id="sess1")


def test_open_session_same_run_id_allows_reopen(receipt_store: SessionReceiptStore) -> None:
    s1 = receipt_store.open_session(run_id="run1", session_id="sess1")
    s2 = receipt_store.open_session(run_id="run1", session_id="sess1")
    assert s1["session_id"] == s2["session_id"]
    assert s2["status"] == "open"


def test_open_session_missing_run_id(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="run_id is required"):
        receipt_store.open_session(run_id="")


# =============================================================================
# attach_session
# =============================================================================


def test_attach_session_readonly(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    session = receipt_store.attach_session(session_id="sess1", run_id="run1", readonly=True)
    assert session["status"] in {"open", "active"}


def test_attach_session_write_takes_lease(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1", owner="actor1")
    session = receipt_store.attach_session(session_id="sess1", run_id="run1", readonly=False, actor="actor1")
    assert session["status"] == "active"
    assert session["lease_owner"] == "actor1"


def test_attach_session_not_found(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="session not found"):
        receipt_store.attach_session(session_id="missing", run_id="run1")


def test_attach_session_run_id_mismatch(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    with pytest.raises(SessionReceiptError, match="run_id does not match"):
        receipt_store.attach_session(session_id="sess1", run_id="run2")


def test_attach_session_expired_lease(receipt_store: SessionReceiptStore, monkeypatch) -> None:
    import time

    from polaris.infrastructure.db.repositories import accel_session_receipt_store as mod

    monkeypatch.setattr(mod, "_normalize_ttl_seconds", lambda v, default_value=1800: max(1, int(v)))
    receipt_store.open_session(run_id="run1", session_id="sess1", ttl_seconds=1)
    time.sleep(1.2)
    with pytest.raises(SessionReceiptError, match="session lease expired"):
        receipt_store.attach_session(session_id="sess1", run_id="run1", readonly=False)


def test_attach_session_lease_conflict(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1", ttl_seconds=3600, owner="actor1")
    receipt_store.attach_session(session_id="sess1", run_id="run1", readonly=False, actor="actor1")
    with pytest.raises(SessionReceiptError, match="lease is owned by another actor"):
        receipt_store.attach_session(session_id="sess1", run_id="run1", readonly=False, actor="actor2")


# =============================================================================
# heartbeat_session
# =============================================================================


def test_heartbeat_session_extends_lease(receipt_store: SessionReceiptStore) -> None:
    s = receipt_store.open_session(run_id="run1", session_id="sess1")
    lease_id = s["lease_id"]
    before = s["lease_until"]
    import time

    time.sleep(0.1)
    after = receipt_store.heartbeat_session(session_id="sess1", lease_id=lease_id)
    assert after["lease_until"] != before
    assert after["status"] == "active"


def test_heartbeat_session_wrong_lease(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    with pytest.raises(SessionReceiptError, match="lease_id mismatch"):
        receipt_store.heartbeat_session(session_id="sess1", lease_id="wrong")


def test_heartbeat_session_not_found(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="session not found"):
        receipt_store.heartbeat_session(session_id="missing", lease_id="l1")


# =============================================================================
# close_session
# =============================================================================


def test_close_session(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    session = receipt_store.close_session(session_id="sess1", final_status="succeeded")
    assert session["status"] == "succeeded"
    assert session["lease_id"] == ""


def test_close_session_invalid_final_status(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    with pytest.raises(SessionReceiptError, match="unsupported session final_status"):
        receipt_store.close_session(session_id="sess1", final_status="active")


def test_close_session_not_found(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="session not found"):
        receipt_store.close_session(session_id="missing")


# =============================================================================
# upsert_receipt
# =============================================================================


def test_upsert_receipt_new(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    r = receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    assert r["job_id"] == "job1"
    assert r["status"] == "running"
    assert r["session_id"] == "sess1"


def test_upsert_receipt_update_status(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    r = receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="succeeded",
    )
    assert r["status"] == "succeeded"


def test_upsert_receipt_args_hash_mismatch(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    with pytest.raises(SessionReceiptError, match="args_hash mismatch"):
        receipt_store.upsert_receipt(
            job_id="job1",
            session_id="sess1",
            run_id="run1",
            tool="cat",
            args_hash="ah2",
            status="succeeded",
        )


def test_upsert_receipt_missing_required(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="job_id, tool, and args_hash are required"):
        receipt_store.upsert_receipt(job_id="", tool="", args_hash="", status="running")


def test_upsert_receipt_invalid_status(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="unsupported receipt status"):
        receipt_store.upsert_receipt(job_id="j1", tool="t1", args_hash="a1", status="unknown")


def test_upsert_receipt_session_not_found(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="session not found"):
        receipt_store.upsert_receipt(
            job_id="job1",
            session_id="missing",
            run_id="run1",
            tool="cat",
            args_hash="ah1",
            status="running",
        )


def test_upsert_receipt_run_id_mismatch(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    with pytest.raises(SessionReceiptError, match="run_id does not match"):
        receipt_store.upsert_receipt(
            job_id="job1",
            session_id="sess1",
            run_id="run2",
            tool="cat",
            args_hash="ah1",
            status="running",
        )


# =============================================================================
# update_receipt_status
# =============================================================================


def test_update_receipt_status(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    r = receipt_store.update_receipt_status(job_id="job1", status="succeeded", result_ref="ref1")
    assert r["status"] == "succeeded"
    assert r["result_ref"] == "ref1"


def test_update_receipt_status_not_found(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="receipt not found"):
        receipt_store.update_receipt_status(job_id="missing", status="succeeded")


# =============================================================================
# get_receipt
# =============================================================================


def test_get_receipt_hit(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    r = receipt_store.get_receipt(job_id="job1")
    assert r is not None
    assert r["job_id"] == "job1"


def test_get_receipt_miss(receipt_store: SessionReceiptStore) -> None:
    assert receipt_store.get_receipt(job_id="missing") is None


def test_get_receipt_empty_id(receipt_store: SessionReceiptStore) -> None:
    assert receipt_store.get_receipt(job_id="") is None


# =============================================================================
# list_receipts
# =============================================================================


def test_list_receipts_no_filter(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    for i in range(3):
        receipt_store.upsert_receipt(
            job_id=f"job{i}",
            session_id="sess1",
            run_id="run1",
            tool="cat",
            args_hash=f"ah{i}",
            status="running",
        )
    results = receipt_store.list_receipts()
    assert len(results) == 3


def test_list_receipts_session_filter(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.open_session(run_id="run1", session_id="sess2")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    receipt_store.upsert_receipt(
        job_id="job2",
        session_id="sess2",
        run_id="run1",
        tool="cat",
        args_hash="ah2",
        status="running",
    )
    results = receipt_store.list_receipts(session_id="sess1")
    assert len(results) == 1
    assert results[0]["job_id"] == "job1"


def test_list_receipts_tool_filter(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    receipt_store.upsert_receipt(
        job_id="job2",
        session_id="sess1",
        run_id="run1",
        tool="dog",
        args_hash="ah2",
        status="running",
    )
    results = receipt_store.list_receipts(tool="cat")
    assert len(results) == 1
    assert results[0]["job_id"] == "job1"


def test_list_receipts_status_filter(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    receipt_store.upsert_receipt(
        job_id="job2",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah2",
        status="succeeded",
    )
    results = receipt_store.list_receipts(status="running")
    assert len(results) == 1
    assert results[0]["job_id"] == "job1"


def test_list_receipts_limit(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    for i in range(10):
        receipt_store.upsert_receipt(
            job_id=f"job{i}",
            session_id="sess1",
            run_id="run1",
            tool="cat",
            args_hash=f"ah{i}",
            status="running",
        )
    results = receipt_store.list_receipts(limit=3)
    assert len(results) == 3


def test_list_receipts_limit_clamped(receipt_store: SessionReceiptStore) -> None:
    receipt_store.open_session(run_id="run1", session_id="sess1")
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    results = receipt_store.list_receipts(limit=0)
    assert len(results) == 1
    results = receipt_store.list_receipts(limit=9999)
    assert len(results) == 1


# =============================================================================
# recover_expired_running_receipts
# =============================================================================


def test_recover_expired_running_receipts(receipt_store: SessionReceiptStore, monkeypatch) -> None:
    import time

    from polaris.infrastructure.db.repositories import accel_session_receipt_store as mod

    monkeypatch.setattr(mod, "_normalize_ttl_seconds", lambda v, default_value=1800: max(1, int(v)))
    receipt_store.open_session(run_id="run1", session_id="sess1", ttl_seconds=1)
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="running",
    )
    time.sleep(1.2)
    count = receipt_store.recover_expired_running_receipts(terminal_status="failed")
    assert count == 1
    r = receipt_store.get_receipt(job_id="job1")
    assert r is not None
    assert r["status"] == "failed"
    assert r["error_code"] == "E_SESSION_EXPIRED"


def test_recover_expired_running_receipts_not_recoverable(receipt_store: SessionReceiptStore, monkeypatch) -> None:
    import time

    from polaris.infrastructure.db.repositories import accel_session_receipt_store as mod

    monkeypatch.setattr(mod, "_normalize_ttl_seconds", lambda v, default_value=1800: max(1, int(v)))
    receipt_store.open_session(run_id="run1", session_id="sess1", ttl_seconds=1)
    receipt_store.upsert_receipt(
        job_id="job1",
        session_id="sess1",
        run_id="run1",
        tool="cat",
        args_hash="ah1",
        status="succeeded",
    )
    time.sleep(1.2)
    count = receipt_store.recover_expired_running_receipts(terminal_status="failed")
    assert count == 0


def test_recover_expired_running_receipts_invalid_terminal(receipt_store: SessionReceiptStore) -> None:
    with pytest.raises(SessionReceiptError, match="terminal_status must be failed or canceled"):
        receipt_store.recover_expired_running_receipts(terminal_status="succeeded")


# =============================================================================
# SessionReceiptError
# =============================================================================


def test_session_receipt_error_attributes() -> None:
    err = SessionReceiptError("E_CODE", "message here")
    assert err.code == "E_CODE"
    assert err.message == "message here"
    assert str(err) == "message here"
