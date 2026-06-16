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
