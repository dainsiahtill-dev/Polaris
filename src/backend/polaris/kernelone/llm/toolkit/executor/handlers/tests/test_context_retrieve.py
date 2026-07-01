"""Tests for the context_retrieve reversible-retrieve tool handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from polaris.infrastructure.db.repositories.accel_session_receipt_store import (
    SessionReceiptStore,
    lookup_session_receipt_metadata,
)
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve import (
    _handle_context_retrieve,
    configure_receipt_metadata_lookup,
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
    configure_receipt_metadata_lookup(lookup_session_receipt_metadata)
    yield
    configure_receipt_metadata_lookup(None)
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


def test_marker_roundtrip_put_then_retrieve() -> None:
    """The canonical CCR loop: cache.put() returns a ``<<ref:HASH>>`` marker;
    the model pastes that marker (NOT a bare hash) and the handler resolves it.
    A bare hash (without the marker wrapper) is now ``unparseable_ref`` — the
    strict contract that distinguishes 'model typed prose' from 'model typed a
    real pointer that happens to be unknown'."""
    marker = get_default_cache().put("body")
    result = _handle_context_retrieve(_fake(), ref=marker)
    assert result["ok"] is True
    assert result["content"] == "body"


def test_bare_hash_without_marker_fails_closed_as_unparseable() -> None:
    """A bare hash (the inner ref without the ``<<ref:>>`` wrapper) is
    rejected with ``unparseable_ref`` per the strict contract. This is
    intentional: a weak model that strips the marker must get a teaching
    error, not a silent lookup miss that could match the wrong entry."""
    marker = get_default_cache().put("body")
    bare = marker.removeprefix("<<ref:").removesuffix(">>")
    result = _handle_context_retrieve(_fake(), ref=bare)
    assert result["ok"] is False
    assert result["error_type"] == "unparseable_ref"


def test_missing_ref_fails_closed() -> None:
    result = _handle_context_retrieve(_fake())
    assert result["ok"] is False
    assert result["error_type"] == "missing_ref"


def test_unknown_ref_fails_closed_not_retrievable() -> None:
    result = _handle_context_retrieve(_fake(), ref="<<ref:unknownhash>>")
    assert result["ok"] is False
    assert result["error_type"] == "not_retrievable"


def test_receipt_lookup_without_adapter_fails_closed(tmp_path: Path) -> None:
    configure_receipt_metadata_lookup(None)
    workspace = tmp_path
    state_dir = workspace / "runtime" / "state"
    state_dir.mkdir(parents=True)
    store = SessionReceiptStore(state_dir / "session_receipts.db")
    store.open_session(run_id="run-no-adapter", session_id="s-no-adapter")
    store.upsert_receipt(
        job_id="job-no-adapter",
        session_id="s-no-adapter",
        run_id="run-no-adapter",
        tool="write_file",
        args_hash="abc",
        status="succeeded",
    )

    result = _handle_context_retrieve(_fake(str(workspace)), ref="[receipt_ref:job-no-adapter]")

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

    result = _handle_context_retrieve(_fake(str(workspace)), ref="[receipt_ref:job-xyz]")
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
    result = _handle_context_retrieve(_fake(str(workspace)), ref="[receipt_ref:collide]")
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


# ---------------------------------------------------------------------------
# Description weak-model nudge regression guard
# ---------------------------------------------------------------------------
# This description is the FIRST place the model learns that context_retrieve is
# the right tool to call when it sees a pointer placeholder. A model that does
# not get this hint will re-read or re-run, defeating the CCR cache. If anyone
# edits the description and drops the nudge, this test fails CI. The string is
# a model-facing ground truth, NOT an audit record (per slice charter §6.6).


_REQUIRED_PLACEHOLDER_HINTS = (
    "[receipt_ref:ID]",
    "<<ref:HASH>>",
    "[Large output stored in receipt ID]",
    "[Large content stored in receipt ID]",
)
_REQUIRED_ARG_ALIAS_HINTS = ("hash", "id", "pointer", "receipt_ref", "ref")
_REQUIRED_NUDGE_PHRASES = (
    "instead",  # "instead of re-reading/re-running"
    "re-reading",
    "re-running",
    "not_retrievable",  # tells the model what an honest miss looks like
)


def _get_offered_spec() -> dict[str, Any]:
    """Return the spec that the model-facing tool layer would see."""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    ensure_context_retrieve_spec_registered()
    spec = ToolSpecRegistry.get_all_specs().get("context_retrieve")
    assert spec is not None, "context_retrieve spec must be registered before checks run"
    return spec


def test_spec_category_is_read_for_tool_batching_and_acl() -> None:
    """category=read is required so the executor classifies this as a read tool
    (no batched-commit side effects, allowed in any phase)."""
    assert _get_offered_spec()["category"] == "read"


def test_spec_arguments_pin_ref_as_required() -> None:
    """The canonical argument MUST be `ref` and MUST be required. A drift here
    breaks the handler's `_extract_ref` resolution order."""
    args = _get_offered_spec()["arguments"]
    assert len(args) == 1
    assert args[0]["name"] == "ref"
    assert args[0]["required"] is True


def test_spec_arg_aliases_cover_all_weak_model_variants() -> None:
    """The SchemaDriven normalizer relies on these aliases. If any drift, the
    inline fallback is the only safety net."""
    aliases = _get_offered_spec()["arg_aliases"]
    for alias in ("hash", "id", "pointer", "receipt_ref"):
        assert aliases.get(alias) == "ref", f"arg_aliases missing or wrong for {alias!r}: {aliases!r}"


def test_spec_description_is_weak_model_nudge() -> None:
    """The description is what the model reads. It must contain the four
    placeholder hints and the 'call instead of re-reading/re-running' nudge,
    and must surface every accepted arg alias. Catches accidental edits that
    drop the nudge and silently regress the loop-closure contract.
    """
    description = _get_offered_spec()["description"]
    assert isinstance(description, str) and description.strip(), "description must be a non-empty string"
    for hint in _REQUIRED_PLACEHOLDER_HINTS:
        assert hint in description, f"description missing required placeholder hint: {hint!r}"
    for alias in _REQUIRED_ARG_ALIAS_HINTS:
        assert alias in description, f"description missing required arg-alias name: {alias!r}"
    for phrase in _REQUIRED_NUDGE_PHRASES:
        assert phrase.lower() in description.lower(), (
            f"description missing required weak-model nudge phrase: {phrase!r}"
        )


def test_spec_registered_at_import_time_and_via_register_handlers() -> None:
    """The spec must be present both for cold-start (import) and warm
    (register_handlers) call sites. Defends against ordering regressions."""
    # Re-import the module fresh in a subprocess-like isolation: we already
    # imported it at collection time, so verify the spec is in place AND that
    # calling register_handlers() is idempotent.
    handlers = register_handlers()
    assert handlers.get("context_retrieve") is _handle_context_retrieve

    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    assert ToolSpecRegistry.is_registered("context_retrieve")
    # Calling register again must not raise or duplicate.
    ensure_context_retrieve_spec_registered()
    ensure_context_retrieve_spec_registered()
    # Still one canonical entry.
    assert ToolSpecRegistry.is_canonical("context_retrieve")
