from __future__ import annotations

import os
import tempfile
import threading

import pytest
from polaris.domain.cognitive_runtime import ContextHandoffPack, RuntimeReceipt
from polaris.infrastructure.cognitive_runtime import CognitiveRuntimeSqliteStore


def _build_store() -> CognitiveRuntimeSqliteStore:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-store-")
    fd, db_path = tempfile.mkstemp(prefix="cognitive-runtime-store-", suffix=".sqlite")
    os.close(fd)
    if os.path.exists(db_path):
        os.unlink(db_path)
    return CognitiveRuntimeSqliteStore(workspace, db_path=db_path)


def test_sqlite_store_serializes_receipt_writes_across_threads() -> None:
    store = _build_store()
    errors: list[BaseException] = []

    def _worker(index: int) -> None:
        try:
            store.append_receipt(
                RuntimeReceipt(
                    receipt_id=f"receipt-{index}",
                    receipt_type="handoff",
                    workspace="C:/workspace",
                    created_at=f"2026-03-27T00:00:{index:02d}+00:00",
                    payload={"index": index},
                    session_id="session-1",
                )
            )
        except (RuntimeError, ValueError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(idx,)) for idx in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    receipts = store.list_receipts(session_id="session-1", limit=20)
    store.close()

    assert not errors
    assert len(receipts) == 12
    assert {item.receipt_id for item in receipts} == {f"receipt-{idx}" for idx in range(12)}


def test_sqlite_store_close_rejects_new_writes() -> None:
    store = _build_store()
    store.save_handoff_pack(
        ContextHandoffPack(
            handoff_id="handoff-1",
            workspace="C:/workspace",
            created_at="2026-03-27T00:00:00+00:00",
            session_id="session-1",
        )
    )
    store.close()

    with pytest.raises(RuntimeError, match="closed"):
        store.append_receipt(
            RuntimeReceipt(
                receipt_id="receipt-closed",
                receipt_type="handoff",
                workspace="C:/workspace",
                created_at="2026-03-27T00:00:01+00:00",
                payload={},
            )
        )
