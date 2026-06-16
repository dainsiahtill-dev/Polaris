"""T1-A CCR producer loop closure: offloaded originals must be retrievable.

ContextOS pointerizes large tool outputs to ``[receipt_ref:ID]`` placeholders.
The consumer (``context_retrieve`` -> ``OriginalPayloadCache.get``) existed, but
no producer stored the original, so retrieve always returned ``not_retrievable``
(the loop was open -> a correct model that pointerized a tool output it later
needs could never get it back = platform-induced read-loop failure). The
``ReceiptStore.on_offload`` hook + ``capture_under`` close the loop WITHOUT
changing the placeholder text the model sees (floor-safe). These tests pin the
closed loop and the floor-inert default.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.llm.toolkit.original_payload_cache import (
    OriginalPayloadCache,
    capture_under,
    get_default_cache,
    make_offload_capture,
    workspace_scoped_ref,
)


def test_put_under_roundtrip_via_receipt_ref_marker() -> None:
    cache = OriginalPayloadCache()
    cache.put_under("evt-42", "the original tool output bytes")
    # the model sees the marker form; get must unwrap it back to the same key
    assert cache.get("[receipt_ref:evt-42]") == "the original tool output bytes"
    assert cache.get("evt-42") == "the original tool output bytes"


def test_put_under_empty_key_is_noop() -> None:
    cache = OriginalPayloadCache()
    cache.put_under("", "x")
    cache.put_under("   ", "y")
    assert len(cache) == 0


def test_capture_under_is_defensive() -> None:
    # best-effort: never raises, even on empty key / content
    capture_under("", "")
    capture_under("k", "")


def test_offload_mirrors_into_ccr_cache(tmp_path: Path) -> None:
    get_default_cache().clear()
    store = ReceiptStore(workspace=str(tmp_path), on_offload=capture_under)
    big = "X" * 4000
    display, refs = store.offload_content("evt-7", big, threshold=500, placeholder="[receipt_ref:evt-7]")
    assert refs == ("evt-7",)
    assert display == "[receipt_ref:evt-7]"
    # the loop is closed: context_retrieve resolves the SAME pointer the model sees
    assert get_default_cache().get("[receipt_ref:evt-7]") == big


def test_offload_default_no_hook_is_floor_inert(tmp_path: Path) -> None:
    get_default_cache().clear()
    store = ReceiptStore(workspace=str(tmp_path))  # no on_offload -> old behavior
    big = "Y" * 4000
    display, refs = store.offload_content("evt-9", big, threshold=500, placeholder="[receipt_ref:evt-9]")
    assert refs == ("evt-9",)
    assert display == "[receipt_ref:evt-9]"
    # cache untouched: the default path is byte-for-byte the prior behavior
    assert get_default_cache().get("[receipt_ref:evt-9]") is None


def test_below_threshold_does_not_offload_or_cache(tmp_path: Path) -> None:
    get_default_cache().clear()
    store = ReceiptStore(workspace=str(tmp_path), on_offload=capture_under)
    display, refs = store.offload_content("evt-1", "tiny", threshold=500, placeholder="[receipt_ref:evt-1]")
    assert refs == ()
    assert display == "tiny"
    assert get_default_cache().get("[receipt_ref:evt-1]") is None


def test_default_no_index_is_byte_identical_to_hooked_model_output(tmp_path: Path) -> None:
    """CRITICAL floor-safety lock (Option R): wiring the producer hook must not
    perturb a single byte the model sees. ``offload_content`` returns the same
    ``(placeholder, refs)`` whether or not the ``on_offload`` index is injected,
    so the cacheable prompt prefix on the hot projection path is provably
    unchanged. Only the side cache differs -- which the model never reads on the
    success path. This pins that the default (``on_offload=None``) path is
    byte-for-byte identical to today.
    """
    get_default_cache().clear()
    big = "Q" * 4000
    placeholder = "[receipt_ref:evt-lock]"

    hooked = ReceiptStore(workspace=str(tmp_path), on_offload=capture_under)
    plain = ReceiptStore(workspace=str(tmp_path))  # exact pre-change signature

    hooked_display, hooked_refs = hooked.offload_content("evt-lock", big, threshold=500, placeholder=placeholder)
    plain_display, plain_refs = plain.offload_content("evt-lock", big, threshold=500, placeholder=placeholder)

    # The model-visible return is byte-identical across both paths ...
    assert hooked_display == plain_display == placeholder
    assert hooked_refs == plain_refs == ("evt-lock",)
    # ... and the placeholder string is passed straight through, never rewritten
    # (the producer hook may not mutate the model-visible pointer text).
    assert hooked_display is placeholder
    assert hooked_display == "[receipt_ref:evt-lock]"
    # The ONLY observable difference is the side cache: hook populated, default not.
    assert get_default_cache().get(placeholder) == big


def test_cross_turn_retrieval_via_process_singleton(tmp_path: Path) -> None:
    """A fresh ReceiptStore per build (the real gateway behavior) still resolves
    a prior turn's offload, because the CCR cache is a process singleton
    independent of the per-build store -- the cross-turn case that was inert."""
    get_default_cache().clear()
    store_turn_a = ReceiptStore(workspace=str(tmp_path), on_offload=capture_under)
    store_turn_a.offload_content("evt-x", "Z" * 3000, threshold=500, placeholder="[receipt_ref:evt-x]")
    del store_turn_a
    # later turn: a brand-new store, but the model's pointer still resolves
    _store_turn_b = ReceiptStore(workspace=str(tmp_path), on_offload=capture_under)
    assert get_default_cache().get("[receipt_ref:evt-x]") == "Z" * 3000


def test_state_first_context_os_offload_path_is_wired(tmp_path: Path) -> None:
    """The second live offload path (StateFirstContextOS / cognitive runtime,
    application/cognitive_runtime/service.py -> project_messages) must also close
    the loop. The engine builds its own long-lived ReceiptStore; wiring the
    on_offload hook there means a payload pointerized on THIS path resolves via
    context_retrieve too. Previously this store had no hook -> refs were inert."""
    from polaris.kernelone.context.context_os import StateFirstContextOS

    get_default_cache().clear()
    context_os = StateFirstContextOS(workspace=str(tmp_path))
    try:
        receipt_store = context_os._receipt_store
        big = "W" * 3500
        display, refs = receipt_store.offload_content(
            "evt-engine", big, threshold=500, placeholder="[receipt_ref:evt-engine]"
        )
        assert refs == ("evt-engine",)
        assert display == "[receipt_ref:evt-engine]"
        # loop closed on the engine path: the same pointer the model sees
        # resolves, under this workspace's scope (context_retrieve re-applies the
        # same scoping from self.workspace).
        assert get_default_cache().get(workspace_scoped_ref(str(tmp_path), "evt-engine")) == big
    finally:
        context_os.__exit__(None, None, None)


def test_workspace_scoping_prevents_cross_workspace_leak(tmp_path: Path) -> None:
    """The production hook (make_offload_capture) must isolate concurrent
    workspaces that share the process-global cache. Two workspaces offloading
    DIFFERENT content under the SAME un-namespaced receipt_id ('idx_0', a real
    positional collision vector) must NOT cross-resolve — otherwise project A's
    context_retrieve could return project B's bytes (benchmark contamination)."""
    get_default_cache().clear()
    ws_a = str(tmp_path / "a")
    ws_b = str(tmp_path / "b")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    store_a = ReceiptStore(workspace=ws_a, on_offload=make_offload_capture(ws_a))
    store_b = ReceiptStore(workspace=ws_b, on_offload=make_offload_capture(ws_b))
    store_a.offload_content("idx_0", "A" * 3000, threshold=500, placeholder="[receipt_ref:idx_0]")
    store_b.offload_content("idx_0", "B" * 3000, threshold=500, placeholder="[receipt_ref:idx_0]")
    # each workspace resolves ONLY its own content
    assert get_default_cache().get(workspace_scoped_ref(ws_a, "idx_0")) == "A" * 3000
    assert get_default_cache().get(workspace_scoped_ref(ws_b, "idx_0")) == "B" * 3000
    # and the bare key was never stored, so a bare lookup cannot bleed across
    assert get_default_cache().get("idx_0") is None
    # distinct workspaces -> distinct scoped keys (no collision)
    assert workspace_scoped_ref(ws_a, "idx_0") != workspace_scoped_ref(ws_b, "idx_0")
