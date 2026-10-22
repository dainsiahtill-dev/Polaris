"""Phase 2 hardening tests for GET /v2/context/{hash}.

Three layers of coverage:

1. Hash validation matrix — every adversarial input that the FastAPI path
   decoder could plausibly surface must round-trip to 400 INVALID_HASH via
   the single shared :func:`validate_context_hash` validator.
2. Workspace ACL — without the header the A-bound client is allowed; with
   X-ContextOS-Workspace pointing at workspace B the response is 403; with a
   valid hash that exists only in workspace B the A-bound client sees 404
   (no header), proving the ACL never leaks existence.
3. Defence in depth — monkeypatch ``StorageLayout.resolve_artifact_path``
   to raise and assert the endpoint still rejects with 400, not 500.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.v2._shared import require_auth
from polaris.delivery.http.v2.context import router as context_router


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


def _build_client(workspace: str) -> TestClient:
    app = FastAPI()
    app.include_router(context_router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=workspace, ramdisk_root=""),
    )
    # require_auth binds state.auth_context; dependency_overrides lets the
    # ACL helper see a real auth context without re-checking the token.
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


# ----------------------------------------------------------------------------
# 1. Hash validation matrix
# ----------------------------------------------------------------------------


HASH_MATRIX: list[tuple[str, str]] = [
    ("uppercase hex", "AABBCC112233445566778899"),
    ("oversized 64 chars", "a" * 64),
    ("undersized 23 chars", "a" * 23),
    ("oversized 25 chars", "a" * 25),
    ("uppercase mixed", "AaBbCc112233445566778899"),
    ("non-hex 24 chars", "g" * 24),
    ("unicode emoji", "✨" * 24),
    ("long input", "a" * 128),
    ("oversized 4K", "0" * 4096),
]


@pytest.mark.parametrize(
    "label,raw",
    HASH_MATRIX,
    ids=[case[0] for case in HASH_MATRIX],
)
def test_hash_validation_matrix(tmp_path, label: str, raw: str) -> None:
    """Every adversarial input must produce 400 INVALID_HASH."""
    client = _build_client(str(tmp_path))
    response = client.get(f"/v2/context/{raw}")
    assert response.status_code == 400, f"{label}: expected 400, got {response.status_code}"
    detail = response.json().get("detail", {})
    assert detail.get("code") == "INVALID_HASH", f"{label}: expected INVALID_HASH, got {detail}"


# ----------------------------------------------------------------------------
# 2. Workspace ACL
# ----------------------------------------------------------------------------


def _seed_context(workspace: Path, label: str, content: str) -> str:
    """Write a context snapshot via the producer and return its hash."""
    from polaris.kernelone.llm.engine.executor import AIExecutor

    return AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=[{"role": "user", "content": content}],
        trace_id=f"trace-{label}",
        call_id=f"call-{label}",
    )


def _seed_context_with_provider_request(workspace: Path, label: str) -> str:
    from polaris.kernelone.llm.engine.executor import AIExecutor

    return AIExecutor._store_context_messages_sync(
        workspace=str(workspace),
        messages=[{"role": "system", "content": "You are Resident AGI."}],
        trace_id=f"trace-{label}",
        call_id=f"call-{label}",
        provider_request={
            "schema_version": "llm.provider_request_snapshot.v1",
            "role": "resident_agi",
            "provider_id": "mock-provider",
            "provider_type": "mock",
            "model": "mock-model",
            "tool_schema_count": 1,
            "tools": [{"type": "function", "name": "repo_tree", "argument_keys": [], "required": []}],
            "tool_choice": "auto",
            "response_format": {"type": "json_schema", "name": "ResidentAgiDecisionOutputV1"},
            "final_request_context_audit": {
                "schema_version": "llm.final_request_context_audit.v1",
                "final_request_token_estimate": 128,
                "tool_schema_count": 1,
            },
        },
    )


def _write_context_snapshot(runtime_root: Path, payload: dict) -> str:
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hash_key = hashlib.sha256(content).hexdigest()[:24]
    target = runtime_root / "contexts" / hash_key[:2] / hash_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return hash_key


def test_workspace_acl_allows_with_no_header(tmp_path) -> None:
    """No X-ContextOS-Workspace header → reads from active workspace freely."""
    workspace_a = tmp_path / "workspaceA"
    workspace_a.mkdir()
    hash_key = _seed_context(workspace_a, "A", "hello-from-A")
    client = _build_client(str(workspace_a))
    response = client.get(f"/v2/context/{hash_key}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hash"] == hash_key
    assert body["messages"] == [{"role": "user", "content": "hello-from-A"}]


def test_final_request_endpoint_returns_provider_request_audit(tmp_path) -> None:
    workspace_a = tmp_path / "workspaceA"
    workspace_a.mkdir()
    hash_key = _seed_context_with_provider_request(workspace_a, "provider-request")
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}/final-request")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "context.final_provider_request_audit.v1"
    assert body["context_hash"] == hash_key
    assert body["message_count"] == 1
    assert body["provider_request"]["schema_version"] == "llm.provider_request_snapshot.v1"
    assert body["tools"][0]["name"] == "repo_tree"
    assert body["tool_choice"] == "auto"
    assert body["response_format"]["name"] == "ResidentAgiDecisionOutputV1"
    assert body["final_request_context_audit"]["final_request_token_estimate"] == 128


def test_final_request_endpoint_fails_closed_without_provider_request(tmp_path) -> None:
    workspace_a = tmp_path / "workspaceA"
    workspace_a.mkdir()
    hash_key = _seed_context(workspace_a, "missing-provider-request", "hello")
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}/final-request")

    assert response.status_code == 409, response.text
    detail = response.json().get("detail", {})
    assert detail.get("code") == "provider_request_missing", detail


def test_workspace_acl_blocks_when_header_targets_other_workspace(tmp_path) -> None:
    """X-ContextOS-Workspace pointing at B → 403 WORKSPACE_FORBIDDEN."""
    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    workspace_a.mkdir()
    workspace_b.mkdir()
    hash_key = _seed_context(workspace_a, "A", "hello-from-A")
    client = _build_client(str(workspace_a))
    response = client.get(
        f"/v2/context/{hash_key}",
        headers={"X-ContextOS-Workspace": str(workspace_b)},
    )
    assert response.status_code == 403, response.text
    detail = response.json().get("detail", {})
    assert detail.get("code") == "WORKSPACE_FORBIDDEN", detail


def test_workspace_acl_missing_hash_returns_404_without_leaking(tmp_path) -> None:
    """A valid 24-hex hash that exists only in workspace B must 404 for A.

    The check fires before the ACL sees the hash on disk, so the ACL never
    reveals existence of cross-workspace data.  The hash is valid (so we
    pass the validator) but absent from A (so we return 404, not 403).
    """
    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    workspace_a.mkdir()
    workspace_b.mkdir()
    # Seed B with real context — A has nothing under this hash.
    hash_key = _seed_context(workspace_b, "B", "secret-B")
    client = _build_client(str(workspace_a))
    response = client.get(f"/v2/context/{hash_key}")
    assert response.status_code == 404, response.text
    detail = response.json().get("detail", {})
    assert detail.get("code") == "CONTEXT_NOT_FOUND", detail


def test_workspace_query_selects_snapshot_workspace(tmp_path) -> None:
    """Explicit ?workspace= selects the snapshot store for launcher/web views.

    ContextOS can render events for a workspace that is not the backend's
    default active workspace.  The hash must be resolved against the selected
    workspace instead of silently reading the default workspace and returning
    a false CONTEXT_NOT_FOUND.
    """
    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    workspace_a.mkdir()
    workspace_b.mkdir()
    hash_key = _seed_context(workspace_b, "B", "visible-from-selected-workspace")
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}", params={"workspace": str(workspace_b)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hash"] == hash_key
    assert body["messages"] == [{"role": "user", "content": "visible-from-selected-workspace"}]


def test_workspace_query_falls_back_to_kernelone_system_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader must survive runtime-root override vs default cache split."""
    from polaris.kernelone.storage.layout import clear_storage_roots_cache

    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    workspace_a.mkdir()
    workspace_b.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    from polaris.cells.context.engine.public.snapshot_paths import default_kernelone_runtime_root

    # Synthetic historical bytes test discovery only; current writers reject external roots.
    hash_key = _write_context_snapshot(
        default_kernelone_runtime_root(str(workspace_b)),
        {"schema_version": 1, "messages": [{"role": "user", "content": "default-cache-context"}]},
    )

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "server-runtime"))
    clear_storage_roots_cache()
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}", params={"workspace": str(workspace_b)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hash"] == hash_key
    assert body["storage_source"] == "kernelone_system_cache"
    assert body["messages"] == [{"role": "user", "content": "default-cache-context"}]


def test_workspace_query_falls_back_to_registered_instance_runtime_root(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ContextOS must find snapshots under a registered instance runtime root."""
    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    runtime_root = tmp_path / "instances" / "bench-l1-10" / "runtime"
    workspace_a.mkdir()
    workspace_b.mkdir()
    hash_key = _write_context_snapshot(
        runtime_root,
        {
            "schema_version": 1,
            "trace_id": "trace-instance",
            "call_id": "call-instance",
            "messages": [{"role": "user", "content": "from instance runtime"}],
            "stored_at": "2026-06-25T07:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.snapshot_paths.query_instance_records",
        lambda: [
            {
                "instance_id": "bench-l1-10",
                "workspace": str(workspace_b),
                "runtime_root": str(runtime_root),
                "status": "running",
                "updated_at": "2026-06-25T07:01:00Z",
            }
        ],
    )
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}", params={"workspace": str(workspace_b)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hash"] == hash_key
    assert body["storage_source"] == "instance_runtime_root:bench-l1-10"
    assert body["messages"] == [{"role": "user", "content": "from instance runtime"}]


def test_final_request_workspace_query_selects_snapshot_workspace(tmp_path) -> None:
    """The final-request audit endpoint must share the same workspace selector."""
    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    workspace_a.mkdir()
    workspace_b.mkdir()
    hash_key = _seed_context_with_provider_request(workspace_b, "provider-request-B")
    client = _build_client(str(workspace_a))

    response = client.get(
        f"/v2/context/{hash_key}/final-request",
        params={"workspace": str(workspace_b)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context_hash"] == hash_key
    assert body["provider_request"]["provider_id"] == "mock-provider"
    assert body["final_request_context_audit"]["final_request_token_estimate"] == 128


def test_final_request_uses_registered_instance_runtime_root(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final-request endpoint must share instance runtime-root fallback."""
    workspace_a = tmp_path / "workspaceA"
    workspace_b = tmp_path / "workspaceB"
    runtime_root = tmp_path / "instances" / "bench-l1-11" / "runtime"
    workspace_a.mkdir()
    workspace_b.mkdir()
    hash_key = _write_context_snapshot(
        runtime_root,
        {
            "schema_version": 1,
            "trace_id": "trace-instance-final",
            "call_id": "call-instance-final",
            "messages": [{"role": "system", "content": "Director"}],
            "stored_at": "2026-06-25T07:10:00+00:00",
            "provider_request": {
                "schema_version": "llm.provider_request_snapshot.v1",
                "role": "director",
                "provider_id": "qwen",
                "provider_type": "openai-compatible",
                "model": "qwen3.6-27b",
                "tools": [{"type": "function", "name": "write_file"}],
                "tool_choice": "auto",
                "response_format": {"type": "json_object"},
                "final_request_context_audit": {
                    "schema_version": "llm.final_request_context_audit.v1",
                    "final_request_token_estimate": 4096,
                },
            },
        },
    )
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.snapshot_paths.query_instance_records",
        lambda: [
            {
                "instance_id": "bench-l1-11",
                "workspace": str(workspace_b),
                "runtime_root": str(runtime_root),
                "status": "running",
                "updated_at": "2026-06-25T07:11:00Z",
            }
        ],
    )
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}/final-request", params={"workspace": str(workspace_b)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context_hash"] == hash_key
    assert body["storage_source"] == "instance_runtime_root:bench-l1-11"
    assert body["provider_request"]["provider_id"] == "qwen"
    assert body["final_request_context_audit"]["final_request_token_estimate"] == 4096


def test_legacy_runtime_context_is_not_read(tmp_path) -> None:
    """Old Polaris runtime context locations are no longer readable.

    ContextOS now treats the current KernelOne ``runtime/contexts`` tree as the
    only snapshot source. A hash that exists only in an old directory must 404
    instead of silently reviving stale state.
    """
    workspace_a = tmp_path / "workspaceA"
    workspace_a.mkdir()
    hash_key = "c24c57d5069883b282f4e32b"
    legacy_file = tmp_path / "legacy" / "contexts" / hash_key[:2] / hash_key
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trace_id": "trace-legacy",
                "call_id": "call-legacy",
                "messages": [{"role": "user", "content": "legacy context"}],
                "stored_at": "2026-06-21T13:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    client = _build_client(str(workspace_a))

    response = client.get(f"/v2/context/{hash_key}")

    assert response.status_code == 404, response.text
    detail = response.json().get("detail", {})
    assert detail.get("code") == "CONTEXT_NOT_FOUND", detail
    assert legacy_file.is_file()


# ----------------------------------------------------------------------------
# 3. Defence in depth — StorageLayout.resolve_artifact_path failure
# ----------------------------------------------------------------------------


def test_validator_runs_before_layout(tmp_path) -> None:
    """The hash validator runs BEFORE layout resolution.

    If ``StorageLayout.resolve_artifact_path`` is hostile (raises on
    every call), a syntactically invalid hash must STILL short-circuit
    at the validator and produce 400 INVALID_HASH — not 500.  The
    validator is the only contract the producer and consumer share, so
    it must run first, regardless of what the layout is doing.
    """
    client = _build_client(str(tmp_path))

    def _explode(_workspace: str) -> None:
        raise ValueError("simulated layout explosion")

    with patch(
        "polaris.delivery.http.v2.context.read_context_snapshot",
        _explode,
    ):
        response = client.get("/v2/context/ZZZZZZZZZZZZZZZZZZZZZZZZ")
    assert response.status_code == 400, response.text
    assert response.json().get("detail", {}).get("code") == "INVALID_HASH"


def test_validator_unit_layer_rejects_path_traversal() -> None:
    """The validator (unit layer) must reject dot segments / encoded slashes.

    These particular inputs are caught by Starlette's URL parser before
    the request reaches the handler — they return 404 from the router —
    but we still test the validator directly so any future refactor that
    loosens URL parsing cannot bypass the regex check.
    """
    from polaris.kernelone.llm.engine.internal.context_hash import (
        validate_context_hash,
    )

    bad = [
        "../aabbcc112233445566778899",
        "%2Faabbcc112233445566778899",
        "%5Caabbcc112233445566778899",
        "aabbcc112233445566778899%2F",
        "aabbcc112233445566778899%00",
    ]
    for raw in bad:
        with pytest.raises(ValueError):
            validate_context_hash(raw)


@pytest.mark.parametrize("suffix", ["", "/final-request"])
def test_cold_context_read_has_no_provisioning_effects(tmp_path, monkeypatch, suffix) -> None:
    """Catches writable resolver/registry constructors on absent snapshot queries."""
    workspace = tmp_path / "absent-project"
    home = tmp_path / "absent-registry"
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    client = _build_client(str(workspace))
    effects = []
    original = Path.mkdir

    def observe(path, *args, **kwargs):
        effects.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", observe)
    response = client.get(f"/v2/context/{'a' * 24}{suffix}")
    assert response.status_code == 404, response.text
    details = response.json()["detail"]["details"]
    assert details["workspace"] == str(workspace)
    assert details["context_hash"] == "a" * 24
    assert details["searched_paths"]
    assert effects == []
    assert not workspace.exists()
    assert not home.exists()


@pytest.mark.parametrize("suffix", ["", "/final-request"])
def test_context_tamper_first_present_stops_before_valid_historical_copy(tmp_path, monkeypatch, suffix) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    ref = _seed_context_with_provider_request(workspace, "synthetic-tamper")
    target = workspace / ".polaris" / "runtime" / "contexts" / ref[:2] / ref
    historical = tmp_path / "historical"
    copy = historical / "contexts" / ref[:2] / ref
    copy.parent.mkdir(parents=True)
    copy.write_bytes(target.read_bytes())
    target.write_bytes(target.read_bytes() + b" ")
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.snapshot_paths.query_instance_records",
        lambda: [
            {
                "instance_id": "synthetic-history",
                "workspace": str(workspace),
                "runtime_root": str(historical),
                "status": "running",
            }
        ],
    )
    response = _build_client(str(workspace)).get(f"/v2/context/{ref}{suffix}")
    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert "hash_mismatch" in detail["code"].lower()
    assert detail["details"]["context_path"] == str(target)
    assert copy.exists()


@pytest.mark.parametrize("kind", ["symlink", "dangling", "directory", "fifo", "hardlink"])
def test_context_unsafe_first_present_file_is_not_absence(tmp_path, monkeypatch, kind) -> None:
    workspace = tmp_path / "project"
    runtime = workspace / ".polaris" / "runtime"
    ref = "f" * 24
    target = runtime / "contexts" / ref[:2] / ref
    target.parent.mkdir(parents=True)
    other = tmp_path / "other"
    other.write_text("{}", encoding="utf-8")
    if kind == "symlink":
        target.symlink_to(other)
    elif kind == "dangling":
        target.symlink_to(tmp_path / "absent")
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        os.link(other, target)
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(tmp_path / "missing-registry"))
    response = _build_client(str(workspace)).get(f"/v2/context/{ref}")
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["details"]["context_path"] == str(target)


@pytest.mark.parametrize("suffix", ["", "/final-request"])
def test_context_registry_degradation_does_not_hide_valid_canonical_or_missing_history(
    tmp_path, monkeypatch, suffix
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    ref = _seed_context_with_provider_request(workspace, "synthetic-registry-error")
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.snapshot_paths.query_instance_records",
        lambda: {"degraded": True, "items": [], "error": {"code": "synthetic_registry_unavailable"}},
    )
    client = _build_client(str(workspace))
    assert client.get(f"/v2/context/{ref}{suffix}").status_code == 200
    response = client.get(f"/v2/context/{'e' * 24}{suffix}")
    assert response.status_code == 503, response.text
    details = response.json()["detail"]["details"]
    assert details["registry_errors"] == [{"code": "synthetic_registry_unavailable"}]
    assert details["workspace"] == str(workspace)
    assert details["searched_paths"]


@pytest.mark.parametrize(
    "content,code", [(b"[]", "CONTEXT_FORMAT_ERROR"), (b"\xff", "CONTEXT_READ_ERROR"), (b"", "CONTEXT_READ_ERROR")]
)
def test_context_preserves_decode_vs_object_format_error(tmp_path, content, code) -> None:
    ref = hashlib.sha256(content).hexdigest()[:24]
    target = tmp_path / ".polaris" / "runtime" / "contexts" / ref[:2] / ref
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    response = _build_client(str(tmp_path)).get(f"/v2/context/{ref}")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == code


def test_stats_missing_authority_returns_503_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "cold-unavailable"
    response = _build_client(str(workspace)).get("/v2/context/stats")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "CONTEXT_INVENTORY_UNAVAILABLE"
    assert detail["details"]["inventory_complete"] is False
    assert not workspace.exists()


def test_stats_and_admin_stats_use_readonly_same_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import LockedRegularFileSetV1
    from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository

    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    repository.persist_snapshot_bytes("中文统计".encode())

    def denied(**kwargs) -> None:
        raise AssertionError("stats attempted authority provisioning")

    monkeypatch.setattr(LockedRegularFileSetV1, "provision_authority", denied)
    monkeypatch.setattr(LockedRegularFileSetV1, "enroll_stream_lock_keys", denied)
    monkeypatch.setenv("KERNELONE_CONTEXT_ADMIN_ENABLED", "1")
    client = _build_client(str(tmp_path))
    for endpoint in ("/v2/context/stats", "/v2/context/admin/stats"):
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        assert response.json()["file_count"] == 1
        assert response.json()["total_bytes"] == len("中文统计".encode())


def test_admin_partial_sweep_returns_503_with_true_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.delivery.http.v2 import context as module
    from polaris.kernelone.llm.engine.context_store_retention import (
        ContextSnapshotAuditPinRepository,
        ContextStoreInventoryError,
        ContextStoreRetention,
        ContextStoreRetentionConfig,
    )

    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    repository.persist_snapshot_bytes(b"real synthetic deletion")
    retention = ContextStoreRetention(workspace=str(tmp_path), config=ContextStoreRetentionConfig(ttl_seconds=0))
    inventory = retention._inventory
    calls = 0

    def final_failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ContextStoreInventoryError("synthetic final observation failure")
        return inventory(*args, **kwargs)

    monkeypatch.setattr(retention, "_inventory", final_failure)
    monkeypatch.setattr(module, "_build_retention", lambda *args, **kwargs: retention)
    monkeypatch.setenv("KERNELONE_CONTEXT_ADMIN_ENABLED", "1")
    response = _build_client(str(tmp_path)).post("/v2/context/admin/sweep")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "CONTEXT_INVENTORY_UNAVAILABLE"
    assert detail["details"]["removed_files"] == 1
    assert detail["details"]["removed_bytes"] == len(b"real synthetic deletion")
    assert "total_bytes_after" not in detail["details"]
    assert not Path(retention.sweep_state_path).exists()
