# HEADROOM CCR — Producer Loop Closure (T1-A keystone)

**Date:** 2026-06-16
**Status:** Blueprint (implementation-ready). Supersedes the producer-wiring scope of `HEADROOM_CCR_RETRIEVE_20260616.md`.
**Owner coordination:** the CCR consumer half (`context_retrieve.py`, `original_payload_cache.py`) was landed by a concurrent agent (commit `03f4a4be`). This blueprint closes the **producer** half.

## 0. Problem (codegraph-grounded, verified)

The CCR retrieve loop is **OPEN**: nothing makes `context_retrieve` resolvable.

- Consumer exists: `context_retrieve.py:176 → get_default_cache().get(ref)` (`OriginalPayloadCache`, content-hash keyed, emits `<<ref:HASH>>`, optional sqlite).
- **No producer** calls `.put()` anywhere non-test (whole-repo grep: only the consumer references `get_default_cache()`).
- The live pointerizers — `ReceiptStore.offload_content` (`receipt_store.py:38`, 9 callers across `projection_engine.py` build_turns + `role_signals.py:418`) — store the original into **`ReceiptStore`** (a *different* store) and emit the model-visible placeholder `[receipt_ref:ID]` / `receipt://ID`. They never touch `OriginalPayloadCache`.

Net: a Director (weak **or** strong) that pointerized a tool output it later needs cannot get it back → it re-reads, loops, burns budget, dead-letters. **This is a platform fault feeding the write-convergence read-loop wall** (`write-convergence-multimodal` mode A; `repair-mode-crossfile-coherence`), not a model ceiling — so it counts against the goal.

## 1. Design decision: two options, recommend the floor-safe one

**Option H (hash-marker, codex's current cache shape).** Producer emits `<<ref:HASH>>` into the model-visible placeholder; `context_retrieve` resolves the hash.
- ✗ Changes the model-visible prompt placeholder → mutates the cacheable **prefix** on the **hot projection path** → **requires an L2-floor bench** (F21/F22/F25-class risk). Slower to land.
- ✓ Content-addressed dedup.

**Option R (resolve the ref the model already sees) — RECOMMENDED.** Keep the placeholder **byte-identical** (`[receipt_ref:ID]` / `receipt://ID`); make `context_retrieve` resolve **that existing id** against a durable id→content backing.
- ✓ **Floor-safe by construction**: the prompt/prefix is unchanged, so the stochastic L2 success path cannot move → **no bench gate required**. Lands now.
- ✓ Resolves the refs that are *actually in the transcript today* (the only thing a model can call).
- ✗ Id-addressed, not content-dedup (acceptable — `ContentStore` already dedups the bytes by hash underneath).

**Decision: implement Option R.** It closes the loop, advances the goal, and is the only variant that does not gate on a bench.

## 2. Key enabling fact (codegraph)

The original bytes are **already durably persisted**: `ReceiptStore.put → ContentStore.intern` (`receipt_store.py:27`) is content-addressed and **on-disk persistent**. The *only* thing lost cross-turn is the **`receipt_id → ContentRef(hash)` index** (`ReceiptStore._index`, in-memory, rebuilt fresh per context build at `gateway.py:1086`; `import_receipts` has **zero** production callers). So R1 (durability) reduces to: **persist the `receipt_id → hash` mapping**; the content itself is already durable.

## 3. Architecture (text)

```
offload (live, hot path)                     retrieve (model-invoked tool, cold path)
─────────────────────────                    ────────────────────────────────────────
ReceiptStore.offload_content(id, content)    context_retrieve(ref)
  └─ ContentStore.intern(content) ──┐          └─ ReceiptRefResolver.resolve(ref)
  └─ _index[id] = ref  (in-mem)     │               ├─ canonicalize ref:  [receipt_ref:ID] |
  └─ ReceiptIdIndex.put(id, hash) ──┤                 receipt://ID | <receipt_ref:ID> | <<ref:HASH>> | bare
     (NEW: durable sqlite, additive)│               ├─ if id-form → ReceiptIdIndex.get(id) → hash
  └─ placeholder UNCHANGED          │               └─ ContentStore.get_by_hash(hash) → original bytes
                                    └── content already durable on disk (ContentStore)
```

**No change to the placeholder text → prefix byte-identical → floor-safe.**

## 4. Module responsibilities (implementation units)

1. **`ReceiptIdIndex`** (NEW, `kernelone/context/receipt_id_index.py`): a tiny durable `receipt_id → content_hash` map, sqlite-backed under the workspace (reuse the `OriginalPayloadCache` sqlite pattern / `ContentStore` dir). Pure side-store; `put(id, hash)`, `get(id) -> hash|None`. TTL/bounded like `OriginalPayloadCache`.
2. **`ReceiptStore.offload_content`** (`receipt_store.py:38`): after `self.put(receipt_id, content)`, additionally `self._id_index.put(receipt_id, ref.hash)` when an id-index is wired. **Additive; placeholder + return value unchanged.** Guard behind a constructor-injected optional `id_index` (default `None` → exact current behavior = floor-inert for every existing caller/test).
3. **`context_retrieve` handler** (`executor/handlers/context_retrieve.py`): extend ref canonicalization to accept the id-forms (`[receipt_ref:ID]`, `receipt://ID`, `<receipt_ref:ID>`) in addition to `<<ref:HASH>>`; resolve id → hash via `ReceiptIdIndex` → `ContentStore.get_by_hash`. Keep the existing `OriginalPayloadCache` path as a fallback.
4. **Wiring** (`context_os/runtime/engine.py` / `gateway.py:1086` where `ReceiptStore(workspace=…)` is built): pass a workspace-scoped `ReceiptIdIndex` into the `ReceiptStore`. One construction-site change.

## 5. Data flow / floor-safety argument

- Offload path: identical placeholder, identical return tuple, one extra durable `put(id, hash)` (a hash already computed by `intern`). **The model sees exactly the same prompt.** Therefore the L2 stochastic success path is provably unperturbed → **no L2-floor bench required** (this is the distinguishing property vs Option H).
- Retrieve path: only reachable when the model *calls* `context_retrieve` — a cold, opt-in path that does not exist on the success path at all.

## 6. §8 / §6.6 red lines

- `ReceiptIdIndex` / `OriginalPayloadCache` stay **generic content-addressed containers** (opaque bytes keyed by id/hash). `context_retrieve` returns **verbatim** original bytes only — never stores or returns learned project answers (§8).
- No raw audited tool name is rewritten; `context_retrieve` is observed as itself (§6.6).
- UTF-8 explicit on the sqlite text column and all reads.

## 7. Testing (fail-closed)

- Unit: `offload_content` with an injected `ReceiptIdIndex` puts `id→hash`; without one, behavior is byte-identical (floor-inert lock).
- Unit: `context_retrieve` resolves each ref form (`[receipt_ref:ID]`, `receipt://ID`, `<receipt_ref:ID>`, `<<ref:HASH>>`, bare) to the original bytes; returns `not_retrievable` for unknown/expired.
- Cross-turn: offload in store-A, build a fresh store-B (simulating per-build re-instantiation), `context_retrieve` still resolves via the durable index — the case that was previously inert.
- Regression: existing `ReceiptStore` / projection-purity / context-os-hardening suites stay green (the default `id_index=None` path).
- ruff/format/mypy clean.

## 8. Why this is landable now (vs the rest of the headroom plan)

Floor-safe (no prompt change) ⇒ no bench gate ⇒ unblocked. This is the single headroom item that **directly** advances `L2-L8 platform-fault = 0` (closes the read-loop modality of the write-convergence wall). Sequence the lossy crushers (T2-B) **after** this, so any crush is reversible via the now-closed retrieve loop.

## 9. Implementation order

1. `ReceiptIdIndex` (new, self-contained, tested in isolation).
2. `context_retrieve` ref-form extension + resolver (tested in isolation).
3. `ReceiptStore.offload_content` additive `id_index` hook (default None — floor-inert).
4. One wiring site (`gateway.py:1086` / runtime engine) to inject the index.
5. Cross-turn integration test proving the loop is closed.

Coordinate with the concurrent CCR agent on `context_retrieve.py` / `original_payload_cache.py` to avoid hot-file collision; everything else (`receipt_id_index.py`, the wiring site) is non-overlapping.
