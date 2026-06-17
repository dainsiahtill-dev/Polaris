"""End-to-end proof-of-effect for the CCR retrieve loop (Headroom T1-A).

This is the first test across the campaign that exercises the WHOLE loop on the
DOMINANT live path rather than a single component in isolation:

  producer offload (ReceiptStore + make_offload_capture)
    -> model-visible inline placeholder "[Large output stored in receipt <id>]"
      -> context_retrieve(ref=that placeholder)
        -> verbatim original bytes recovered.

It is also the regression guard for two real defects the deep audits found:
  - the inline placeholder shape must be resolvable by strip_ref_markers (CCR-3a);
  - the workspace-scoped producer key must match the consumer lookup key.

Each test isolates the process-global ``OriginalPayloadCache`` singleton via
``get_default_cache().clear()`` so the order/parallelism of this test file can
never leak state into a neighbouring file. That isolation guard is the same
pattern every other CCR test in the campaign uses; it is the most common
``wired-but-inert``-looking slip in a singleton-cache test suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve import (
    _handle_context_retrieve,
)
from polaris.kernelone.llm.toolkit.original_payload_cache import (
    OriginalPayloadCache,
    get_default_cache,
    make_offload_capture,
)


@pytest.fixture(autouse=True)
def _isolate_process_cache() -> Any:
    """Isolate the process-global CCR singleton for every test in this file.

    Required because every other CCR test in the campaign (``test_ccr_*``,
    ``test_original_payload_cache``, ``test_context_retrieve``) shares the
    same ``_default_cache`` instance. Without this, a passing test that
    stashes bytes under key ``evt-1`` can bleed into a later test that
    expects ``not_retrievable`` for the same key.
    """
    get_default_cache().clear()
    yield
    get_default_cache().clear()


def test_ccr_end_to_end_offload_then_retrieve_recovers_verbatim(tmp_path) -> None:
    workspace = str(tmp_path)
    event_id = "evt_42"
    original = "RESULT-LINE-with-detail\n" * 500  # comfortably over the 500-byte threshold

    # --- Producer: projection offloads the large tool output to a tiny placeholder.
    store = ReceiptStore(workspace=workspace, on_offload=make_offload_capture(workspace))
    display, refs = store.offload_content(
        f"tool_{event_id}",
        original,
        threshold=500,
        placeholder=f"[Large output stored in receipt tool_{event_id}]",
    )

    # Proof of the token/byte effect the feature exists for: the model sees a tiny
    # placeholder on the turn, not the full output.
    assert display == f"[Large output stored in receipt tool_{event_id}]"
    assert refs == ("tool_evt_42",)
    assert len(display) < len(original) // 20

    # --- Consumer: the model pastes the placeholder it actually sees into the tool.
    executor = SimpleNamespace(workspace=workspace)
    result = _handle_context_retrieve(executor, ref=display)

    # First real proof-of-effect: the loop recovers the verbatim original bytes.
    assert result["ok"] is True
    assert result["source"] == "ccr_cache"
    assert result["content"] == original
    assert result["chars"] == len(original)


def test_ccr_end_to_end_wrong_workspace_does_not_leak(tmp_path) -> None:
    """§8 / isolation guard: workspace B can never retrieve workspace A's bytes."""
    ws_a = str(tmp_path / "a")
    ws_b = str(tmp_path / "b")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    original = "SECRET-A\n" * 500

    store_a = ReceiptStore(workspace=ws_a, on_offload=make_offload_capture(ws_a))
    display, _ = store_a.offload_content(
        "tool_evt_1",
        original,
        threshold=500,
        placeholder="[Large output stored in receipt tool_evt_1]",
    )

    # Same placeholder, but a DIFFERENT workspace executor must NOT resolve it.
    leaked = _handle_context_retrieve(SimpleNamespace(workspace=ws_b), ref=display)
    assert leaked["ok"] is False
    assert leaked["error_type"] == "not_retrievable"

    # The owning workspace still resolves it.
    owned = _handle_context_retrieve(SimpleNamespace(workspace=ws_a), ref=display)
    assert owned["ok"] is True
    assert owned["content"] == original


# ---------------------------------------------------------------------------
# Path-faithfulness: whatever ProjectionEngine.build_turns shows the model
# inline, context_retrieve must resolve via the same pointer. The dominant
# live path is tool-role events offloaded through the wired ReceiptStore
# (the engine constructs the store with on_offload=make_offload_capture(ws);
# mirror that here so the offload mirrors the bytes into the CCR cache).
# ---------------------------------------------------------------------------


class _ToolEvent:
    """Minimal stand-in for an ActiveWindowEvent consumed by ``build_turns``.

    The engine reads: sequence, route, role, content, event_id, metadata,
    artifact_id. The default route="patch" bypasses the "clear-route / not
    recent" filter that drops non-recent user events.
    """

    def __init__(self, *, sequence: int, event_id: str, content: str, role: str = "tool") -> None:
        self.sequence = sequence
        self.route = "patch"
        self.role = role
        self.content = content
        self.event_id = event_id
        self.metadata: tuple[Any, ...] = ()
        self.artifact_id = ""


@pytest.mark.xfail(
    reason=(
        "KNOWN DEFECT (cross-slice finding): strip_control_plane_markers drops a "
        "trailing newline (splitlines+join), so the bytes the CCR cache snapshots "
        "are 1 char shorter than the original. The fix belongs in a future slice "
        "(snapshot the pre-clean content OR preserve the trailing separator in "
        "the cleaner). This test pins the bug so a future green run proves the fix."
    ),
    strict=True,
)
def test_ccr_end_to_end_build_turns_placeholder_resolves_via_context_retrieve(
    tmp_path,
) -> None:
    """Whatever ``build_turns`` puts inline in the model's prompt, the tool can resolve.

    This was the gap the audit called out (CCR-3 path-faithfulness): the
    earlier tests proved the producer and consumer were wired individually,
    but never that the EXACT text the model sees on the projection path is
    resolvable. Driving the real ``ProjectionEngine.build_turns`` proves it.

    Marked ``xfail`` because this slice's adversarial verification surfaced
    a real defect: the cache content is 1 char shorter than the verbatim
    original. The CCR cache owns the verbatim contract; the projection
    cleaner is breaking it. The slice report flags the fix path.
    """
    workspace = str(tmp_path)
    engine = ProjectionEngine(learning_key="default")
    # The production engine constructs the store with on_offload=make_offload_capture(ws);
    # mirror that here so the offload mirrors the bytes into the CCR cache.
    receipt_store = ReceiptStore(workspace=workspace, on_offload=make_offload_capture(workspace))

    original = "OBSERVATION-DETAIL\n" * 400  # > 500-byte tool threshold
    event = _ToolEvent(sequence=1, event_id="evt_42", content=original, role="tool")

    # The DOMINANT live path: this is exactly what the model will see in the prompt.
    turns = engine.build_turns([event], receipt_store)
    assert len(turns) == 1
    inline_content = turns[0]["content"]
    assert inline_content == "[Large output stored in receipt tool_evt_42]"
    assert turns[0]["receipt_refs"] == ["tool_evt_42"]

    # The model pastes the EXACT inline placeholder it sees into the tool.
    result = _handle_context_retrieve(SimpleNamespace(workspace=workspace), ref=inline_content)
    assert result["ok"] is True
    assert result["source"] == "ccr_cache"
    assert result["content"] == original
    assert result["chars"] == len(original)
    # The recovered ref is the canonical id (post strip_ref_markers), not the
    # full placeholder text — the loop returns the bare key to the model.
    assert result["ref"] == "tool_evt_42"


# ---------------------------------------------------------------------------
# Weak-model ergonomics: the canonical arg is ``ref``, but the
# SchemaDriven normalizer may not always run (spec not loaded, or a raw
# model that ignores the spec). The handler's defensive ``_extract_ref``
# accepts hash / id / pointer / receipt_ref as inline aliases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ["hash", "id", "pointer", "receipt_ref"])
def test_ccr_end_to_end_weak_model_alias_resolves_via_inline_fallback(tmp_path, alias: str) -> None:
    workspace = str(tmp_path)
    original = "aliased-body-content\n" * 50
    # The model pastes the FULL projection marker (one of the forms ContextOS
    # actually injects). ``try_strip_ref_marker`` unwraps it back to the
    # receipt_id the cache is keyed under. The alias name (hash / id / pointer
    # / receipt_ref) is just a label for the same arg — the handler reads
    # whichever alias the model chose and treats the value identically.
    placeholder = "[Large output stored in receipt tool_evt_99]"
    receipt_id = "tool_evt_99"
    get_default_cache().put_under(receipt_id, original)

    result = _handle_context_retrieve(SimpleNamespace(workspace=workspace), **{alias: placeholder})
    assert result["ok"] is True
    assert result["source"] == "ccr_cache"
    assert result["content"] == original


# ---------------------------------------------------------------------------
# Defensive coverage: defensive / failure-mode contracts the loop must hold
# even when the model passes something unparseable.
# ---------------------------------------------------------------------------


def test_ccr_end_to_end_missing_ref_fails_closed_with_missing_ref(tmp_path) -> None:
    """No ref argument at all -> ok=False, error_type=missing_ref.

    Real defect this guards against: a previous revision returned ``ok=True``
    with empty ``content`` when the model forgot the argument (silent success
    on garbage), which made the Director tool look 'succeed' to upstream
    feedback loops. The loop must fail-closed so a missing arg is never
    mistaken for a successful retrieve.
    """
    result = _handle_context_retrieve(SimpleNamespace(workspace=str(tmp_path)))
    assert result["ok"] is False
    assert result["error_type"] == "missing_ref"
    # No content, no source — the model gets no signal that the tool 'worked'.
    assert "content" not in result
    assert "source" not in result


def test_ccr_end_to_end_empty_string_ref_fails_closed(tmp_path) -> None:
    """Empty string in the ref arg must surface as missing_ref (not unparseable)."""
    result = _handle_context_retrieve(SimpleNamespace(workspace=str(tmp_path)), ref="")
    assert result["ok"] is False
    assert result["error_type"] == "missing_ref"


def test_ccr_end_to_end_prose_ref_fails_closed_as_unparseable(tmp_path) -> None:
    """Prose / non-marker text -> fail-closed with unparseable_ref, never silent ok.

    The handler MUST distinguish prose from a well-formed but unknown ref
    so upstream can react differently (teaching error vs. cache miss). The
    ``try_strip_ref_marker`` helper reports the match decision explicitly
    so the ``unparseable_ref`` branch is no longer dead code.
    """
    result = _handle_context_retrieve(SimpleNamespace(workspace=str(tmp_path)), ref="the previous tool output please")
    assert result["ok"] is False
    assert result["error_type"] == "unparseable_ref"


def test_ccr_end_to_end_empty_marker_inner_fails_closed_as_unparseable(tmp_path) -> None:
    """ADVERSARIAL: a marker with an empty inner ref (e.g. ``<<ref:>>``)
    must fail-closed as ``unparseable_ref``, not silently return
    ``not_retrievable`` and let the model confuse "I sent a real pointer"
    with "I sent a malformed one".

    This case would have slipped through the old ``strip_ref_markers`` path
    because the helper returned the empty string for any ``<<ref:>>`` match,
    but the contract for a non-empty raw_ref that DOES match a marker shape
    and yields an empty inner must be its own error_type so upstream error
    classification can be precise (teaching error vs. cache eviction).
    """
    workspace = str(tmp_path)
    for empty_marker in ("<<ref:>>", "[receipt_ref:]", "<receipt_ref:>"):
        result = _handle_context_retrieve(SimpleNamespace(workspace=workspace), ref=empty_marker)
        assert result["ok"] is False, f"{empty_marker!r} must fail-closed"
        assert result["error_type"] == "unparseable_ref", (
            f"{empty_marker!r} empty inner should be unparseable_ref, got {result['error_type']!r}"
        )


def test_ccr_end_to_end_unknown_ref_fails_closed_with_not_retrievable(tmp_path) -> None:
    """A well-formed ref (marker or bare hash) that is simply not in the
    cache -> not_retrievable. Distinct from unparseable_ref: this means the
    loop got the syntax right but the cache has nothing for it (expired or
    wrong workspace)."""
    result = _handle_context_retrieve(SimpleNamespace(workspace=str(tmp_path)), ref="[receipt_ref:never_seen_this_id]")
    assert result["ok"] is False
    assert result["error_type"] == "not_retrievable"


def test_ccr_end_to_end_expired_ref_fails_closed_with_not_retrievable(tmp_path, monkeypatch) -> None:
    """A ref that was live but has now passed its TTL must return
    not_retrievable (fail-closed), not 'ok=True with empty content'. The
    loop's strongest contract under load is that an expired entry NEVER
    silently passes as a successful retrieve."""
    workspace = str(tmp_path)
    original = "short-lived-payload\n" * 50
    cache: OriginalPayloadCache = get_default_cache()

    # Freeze time so the put/write path uses a known deadline, then jump past
    # the default TTL to force expiry on the next read.
    clock = {"t": 1000.0}
    monkeypatch.setattr(
        "polaris.kernelone.llm.toolkit.original_payload_cache.time.monotonic",
        lambda: clock["t"],
    )
    cache.put_under("tool_evt_short", original)
    assert cache.get("tool_evt_short") == original  # still live

    clock["t"] = 1000.0 + 10_000.0  # well past the default 300s TTL
    result = _handle_context_retrieve(SimpleNamespace(workspace=workspace), ref="[receipt_ref:tool_evt_short]")
    assert result["ok"] is False
    assert result["error_type"] == "not_retrievable"
    # And the loop must NOT silently return a different (e.g. stale) entry
    # under the same key — assert the cache is empty post-expiry (lazy purge).
    assert len(cache) == 0


# ---------------------------------------------------------------------------
# ADVERSARIAL: workspace-less executor. A model that calls context_retrieve
# before workspace is bound (early Director boot) must not silently fall
# through to a bare-key lookup that could resolve another workspace's bytes
# — the real defect a 'fail-open' path could cause.
# ---------------------------------------------------------------------------


def test_ccr_end_to_end_no_workspace_does_not_resolve_other_workspace_bytes(
    tmp_path,
) -> None:
    """Workspace-less executor (early boot / mis-config) must NOT silently
    fall through to a bare-key lookup. Real bug this guards against: the
    CCR cache stores the bare key only for content-addressed ``<<ref:HASH>>``
    entries (which are intentionally safe to share); if a workspace-less
    consumer tried to retrieve a *receipt-id* ref (which is workspace-scoped)
    via the bare-key fallback, it could resolve another workspace's bytes
    under a positional collision. The handler must require the ref to be
    either (a) under an explicit workspace match, or (b) the
    content-addressed <<ref:HASH>> form. A bare receipt-id ref under
    no-workspace must fail-closed.
    """
    ws_a = str(tmp_path / "a")
    (tmp_path / "a").mkdir()
    original = "WS-A-secret-bytes\n" * 50
    store_a = ReceiptStore(workspace=ws_a, on_offload=make_offload_capture(ws_a))
    store_a.offload_content(
        "idx_0",
        original,
        threshold=500,
        placeholder="[receipt_ref:idx_0]",
    )

    # No workspace on the executor + a receipt-id-style ref (not content-addressed).
    leaked = _handle_context_retrieve(SimpleNamespace(workspace=""), ref="[receipt_ref:idx_0]")
    assert leaked["ok"] is False
    assert leaked["error_type"] == "not_retrievable"

    # The bytes are still safe behind the namespaced key in the cache.
    assert get_default_cache().get("idx_0") is None


# ---------------------------------------------------------------------------
# ADVERSARIAL: ensure the loop holds under a sized cache (eviction pressure).
# If the bounded cache evicts the entry between offload and retrieve, the
# loop must fail-closed — not return a *different* entry whose hash happened
# to collide. This was the worry when the cache was sized at 4096; with
# many sequential pointerize calls the offload-then-retrieve pair must
# still hold for the entry that was just inserted.
# ---------------------------------------------------------------------------


def test_ccr_end_to_end_insert_then_evict_then_retrieve_fails_closed(tmp_path) -> None:
    """The loop must NOT promote a stale entry to a fresh ref after eviction.

    Real bug this guards against: with a tiny max_entries, the just-inserted
    receipt could be evicted before retrieve, and a *different* entry whose
    hash collided in the bar could be returned as 'the original'. We use
    unique content per insertion so the hash is unique, and assert that
    after forced eviction the retrieve returns not_retrievable (never
    silently different bytes).
    """
    workspace = str(tmp_path)
    cache = get_default_cache()
    # Force a 1-entry cap on the in-memory tier so the second put evicts
    # the first. Restore the original cap in finally to keep the singleton
    # safe for any later test in the suite.
    original_max = cache._max_entries
    cache._max_entries = 1
    try:
        cache.put_under("first", "first-original")
        cache.put_under("second", "second-original")  # evicts 'first'
        assert len(cache) == 1

        result = _handle_context_retrieve(SimpleNamespace(workspace=workspace), ref="[receipt_ref:first]")
        assert result["ok"] is False
        assert result["error_type"] == "not_retrievable"

        # The second entry still resolves (proves eviction only dropped the first).
        result2 = _handle_context_retrieve(SimpleNamespace(workspace=workspace), ref="[receipt_ref:second]")
        assert result2["ok"] is True
        assert result2["content"] == "second-original"
    finally:
        cache._max_entries = original_max
