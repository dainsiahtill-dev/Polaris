"""Durable immutable references survive restart without a content cache."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.kernelone.fs import KernelFileSystem, LockedRegularFileSetV1, get_default_adapter
from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root
from polaris.kernelone.storage import resolve_workspace_runtime_identity


def _store(workspace: Path):
    spec = importlib.util.find_spec("polaris.kernelone.events.sourcing.payload_store")
    assert spec is not None, "durable payload-reference storage capability is missing"
    from polaris.kernelone.events.sourcing.payload_store import DurablePayloadStore

    identity = resolve_workspace_runtime_identity(str(workspace))
    LockedRegularFileSetV1.provision_authority(
        runtime_root=identity.runtime_root,
        storage_identity_token=identity.token,
        platform_lock_root=default_platform_lock_root(),
    )
    return DurablePayloadStore(str(workspace))


def _payload():
    return {"schema_version": "test.large_payload.v1", "text": "中文内容" * 2000}


def test_large_utf8_payload_restarts_and_replays_without_rewriting(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _payload()
    ref = store.prepare(
        payload, payload_schema=payload["schema_version"], maintenance_reason="private payload storage test"
    )
    assert ref.byte_size > 4096
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    before = fs.read_bytes(ref.logical_path)
    assert hashlib.sha256(before).hexdigest() == ref.sha256
    assert len(before) == ref.byte_size
    assert json.loads(before.decode("utf-8")) == payload
    restarted = _store(tmp_path)
    assert restarted.read(ref) == payload
    assert (
        restarted.prepare(payload, payload_schema=payload["schema_version"], maintenance_reason="replay verification")
        == ref
    )
    assert fs.read_bytes(ref.logical_path) == before


@pytest.mark.parametrize("attack", ["missing", "corrupt", "wrong_size", "wrong_schema", "foreign_workspace"])
def test_payload_reference_rejects_missing_corrupt_or_foreign_bytes(tmp_path: Path, attack: str) -> None:
    from polaris.kernelone.events.sourcing.payload_store import PayloadStoreError

    store = _store(tmp_path)
    payload = _payload()
    ref = store.prepare(payload, payload_schema=payload["schema_version"], maintenance_reason="negative proof")
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    if attack == "missing":
        fs.remove(ref.logical_path)
    elif attack == "corrupt":
        fs.write_bytes(ref.logical_path, b"corrupted")
    elif attack == "wrong_size":
        ref = replace(ref, byte_size=ref.byte_size + 1)
    elif attack == "wrong_schema":
        ref = replace(ref, payload_schema="foreign.schema")
    else:
        store = _store(tmp_path / "foreign")
    with pytest.raises(PayloadStoreError):
        store.read(ref)


def test_payload_reference_does_not_accept_paths_or_traversal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _payload()
    ref = store.prepare(payload, payload_schema=payload["schema_version"], maintenance_reason="typed reference")
    from polaris.kernelone.events.sourcing.payload_store import PayloadReferenceV1

    with pytest.raises(ValueError):
        PayloadReferenceV1.from_record({**ref.to_record(), "path": "../../outside"})
    with pytest.raises(ValueError):
        replace(ref, sha256="../outside")


def test_concurrent_same_content_preparation_has_one_immutable_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _payload()
    with ThreadPoolExecutor(max_workers=2) as executor:
        refs = list(
            executor.map(
                lambda _: store.prepare(
                    payload,
                    payload_schema=payload["schema_version"],
                    maintenance_reason="concurrent same-content preparation",
                ),
                range(2),
            )
        )
    assert refs[0] == refs[1]
    assert store.read(refs[0]) == payload
