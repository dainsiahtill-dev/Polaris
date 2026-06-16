"""Tests for the context_retrieve reversible-retrieve tool handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from polaris.infrastructure.db.repositories.accel_session_receipt_store import (
    SessionReceiptStore,
)
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve import (
    _handle_context_retrieve,
    ensure_context_retrieve_spec_registered,
    register_handlers,
)
from polaris.kernelone.llm.toolkit.original_payload_cache import (
    OriginalPayloadCache,
    _Entry,
    get_default_cache,
)


class _FakeExecutor:
    """Minimal stand-in: the handler only reads ``workspace``."""

    def __init__(self, workspace: str = "") -> None:
        self.workspace = workspace


def _fake(workspace: str = "") -> AgentAccelToolExecutor:
    """Build a duck-typed executor stand-in cast to the handler's param type."""
    return cast(AgentAccelToolExecutor, _FakeExecutor(workspace))


@pytest.fixture(autouse=True)
def _clean_default_cache() -> Any:
    cache = get_default_cache()
    cache.clear()
    yield
    cache.clear()


def test_register_handlers_exposes_context_retrieve() -> None:
    handlers = register_handlers()
    assert "context_retrieve" in handlers


def test_ccr_cache_hit_returns_original_content() -> None:
    original = "def f():\n    return 1\n" * 100
    marker = get_default_cache().put(original)
    result = _handle_context_retrieve(_fake(), ref=marker)
    assert result["ok"] is True
    assert result["source"] == "ccr_cache"
    assert result["content"] == original
    assert result["chars"] == len(original)


def test_bare_hash_resolves_without_marker_wrapper() -> None:
    marker = get_default_cache().put("body")
    bare = marker.removeprefix("<<ref:").removesuffix(">>")
    result = _handle_context_retrieve(_fake(), ref=bare)
    assert result["ok"] is True
    assert result["content"] == "body"


def test_missing_ref_fails_closed() -> None:
    result = _handle_context_retrieve(_fake())
    assert result["ok"] is False
    assert result["error_type"] == "missing_ref"


def test_unknown_ref_fails_closed_not_retrievable() -> None:
    result = _handle_context_retrieve(_fake(), ref="<<ref:unknownhash>>")
    assert result["ok"] is False
    assert result["error_type"] == "not_retrievable"


@pytest.mark.parametrize("alias", ["hash", "id", "pointer", "receipt_ref"])
def test_inline_alias_fallback_when_canonical_ref_absent(alias: str) -> None:
    """Defensive: even if the spec/normalizer did not map the alias, the handler
    reads hash/id/pointer/receipt_ref inline (no teaching error)."""
    marker = get_default_cache().put("aliased-body")
    result = _handle_context_retrieve(_fake(), **{alias: marker})
    assert result["ok"] is True
    assert result["content"] == "aliased-body"


def test_receipt_metadata_best_effort(tmp_path: Path) -> None:
    # Build a real receipt store at the canonical runtime/state path.
    workspace = tmp_path
    state_dir = workspace / "runtime" / "state"
    state_dir.mkdir(parents=True)
    db_path = state_dir / "session_receipts.db"
    store = SessionReceiptStore(db_path)
    store.open_session(run_id="run-1", session_id="s-1")
    store.upsert_receipt(
        job_id="job-xyz",
        session_id="s-1",
        run_id="run-1",
        tool="write_file",
        args_hash="abc",
        status="succeeded",
        result_ref="ptr://artifact/1",
    )

    result = _handle_context_retrieve(_fake(str(workspace)), ref="job-xyz")
    assert result["ok"] is True
    assert result["source"] == "receipt_store"
    assert result["receipt"]["result_ref"] == "ptr://artifact/1"
    assert "note" in result


def test_receipt_ref_marker_unwrapped_before_lookup(tmp_path: Path) -> None:
    workspace = tmp_path
    state_dir = workspace / "runtime" / "state"
    state_dir.mkdir(parents=True)
    store = SessionReceiptStore(state_dir / "session_receipts.db")
    store.open_session(run_id="run-2", session_id="s-2")
    store.upsert_receipt(
        job_id="job-77",
        session_id="s-2",
        run_id="run-2",
        tool="read_file",
        args_hash="h",
        status="succeeded",
    )
    # Model pastes the whole projection marker into ref.
    result = _handle_context_retrieve(_fake(str(workspace)), ref="[receipt_ref:job-77]")
    assert result["ok"] is True
    assert result["source"] == "receipt_store"


def test_no_workspace_and_no_cache_fails_closed() -> None:
    result = _handle_context_retrieve(_fake(""), ref="<<ref:abc>>")
    assert result["ok"] is False
    assert result["error_type"] == "not_retrievable"


def test_ccr_takes_priority_over_receipt(tmp_path: Path) -> None:
    """If a ref happens to match both a cache entry and a receipt id, the
    reversible CCR content wins (it is the real original)."""
    workspace = tmp_path
    state_dir = workspace / "runtime" / "state"
    state_dir.mkdir(parents=True)
    store = SessionReceiptStore(state_dir / "session_receipts.db")
    store.open_session(run_id="r", session_id="s")
    store.upsert_receipt(
        job_id="collide",
        session_id="s",
        run_id="r",
        tool="x",
        args_hash="h",
        status="succeeded",
    )
    # Force a cache entry whose hash equals the receipt id by stuffing directly.
    cache: OriginalPayloadCache = get_default_cache()
    cache._entries["collide"] = _Entry(
        content="real-original",
        expires_at=cache._now() + 100,
    )
    result = _handle_context_retrieve(_fake(str(workspace)), ref="collide")
    assert result["ok"] is True
    assert result["source"] == "ccr_cache"
    assert result["content"] == "real-original"


def test_spec_registered_in_tool_spec_registry() -> None:
    ensure_context_retrieve_spec_registered()
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    assert ToolSpecRegistry.is_registered("context_retrieve")
    spec = ToolSpecRegistry.get_all_specs().get("context_retrieve")
    assert spec is not None
    assert spec["arg_aliases"]["hash"] == "ref"


def test_end_to_end_through_real_executor(tmp_path: Path) -> None:
    """Full wiring: AgentAccelToolExecutor.execute resolves the tool spec, loads
    the handler, normalizes args, and returns the original content."""
    ensure_context_retrieve_spec_registered()
    original = "console.log('hello');\n" * 50
    marker = get_default_cache().put(original)

    executor = AgentAccelToolExecutor(workspace=str(tmp_path))
    try:
        result = executor.execute("context_retrieve", {"ref": marker})
    finally:
        executor.close_sync()

    assert result["ok"] is True
    # The executor wraps the handler payload under "result".
    inner = result["result"]
    assert inner["source"] == "ccr_cache"
    assert inner["content"] == original


def test_end_to_end_weak_model_hash_alias(tmp_path: Path) -> None:
    """A weak model calling context_retrieve(hash=...) is normalized to ref via
    the spec arg_aliases (no teaching error)."""
    ensure_context_retrieve_spec_registered()
    marker = get_default_cache().put("aliased-via-registry")

    executor = AgentAccelToolExecutor(workspace=str(tmp_path))
    try:
        result = executor.execute("context_retrieve", {"hash": marker})
    finally:
        executor.close_sync()

    assert result["ok"] is True
    assert result["result"]["content"] == "aliased-via-registry"
