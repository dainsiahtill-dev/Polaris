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

import json
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
        "polaris.delivery.http.v2.context.resolve_storage_roots",
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
