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

---

## 10. 二次深度审计修订（2026-06-16，codegraph + superpowers 对抗，已重跑确认）

> 本节修正本蓝图与实际提交（`27c05ae6` + `7670e903`）的分叉，并登记两个活路径缺口。证据见 `docs/research/HEADROOM_CROSS_POLLINATION_20260616.md` §4。

### 10.1 实际提交 ≠ 本蓝图的 Option R（CCR-1）
- 本蓝图 §4/§9 规定的持久模块 **`kernelone/context/receipt_id_index.py` 从未创建**（全仓 0 引用）。
- 实际落地是**第三种 hybrid**：保留了 R 的 *placeholder 字节不变* 性质（✅ floor-safe，见 §5），但承载用 `OriginalPayloadCache` 进程内存单例 `get_default_cache()`（`original_payload_cache.py:327`，**`sqlite_path=None`**）+ `make_offload_capture(workspace)` 作 `ReceiptStore.on_offload` 钩子（`receipt_store.py:27/52-69`）。**不是** R 承诺的持久 sqlite `receipt_id→hash` 索引。
- ⟹ 本蓝图 §2「R1 reduces to: persist the `receipt_id→hash` mapping」**未兑现**。

### 10.2 耐久性缺口 OPEN（CCR-2）
默认 CCR cache 纯内存、TTL=300s（monotonic 时钟）、4096-entry LRU。turn A 落的指针在**新进程**（后端重启 / 新 `director` CLI run）或 **>300s 后** 取回 `None`。**闭环仅在「单长寿进程 + 5 分钟窗口」成立**。
- **决策待定（P1-doc）**：① 若 director worker 池确为单长寿进程且真实 turn 间隔 <300s → 在此节**显式文档化该 closure scope**（可接受）；② 否则给 `get_default_cache()` 接 workspace `sqlite_path`（`OriginalPayloadCache` 已支持 `sqlite_path` + `_sqlite_get` 提升，:192/:236），或回到 §4 的持久 `receipt_id_index`。两条都是 side-store/冷路径，**免 L2 bench**。
- 顺带：考虑把 receipt-id 键的 TTL 与「读循环」时间尺度对齐——模型早期指针化、长规划绕路后再取回会静默 `not_retrievable`。

### 10.3 活路径指针双形不一致（CCR-3，**最高 ROI 修复，本人重跑确认**）
主投影路径 `projection_engine.py:539/546` 对每个被 offload 的 turn 发**模型可见 inline 占位符**：
- `[Large output stored in receipt tool_<id>]`（tool turn，threshold=500）
- `[Large content stored in receipt evt_<id>]`（其它 turn，threshold=2000）

但 `strip_ref_markers` 的 `_MARKER_PATTERNS`（`original_payload_cache.py:65`）**只认** `[receipt_ref:ID]` / `<receipt_ref:ID>`。可解析的 `[receipt_ref:<id>]` 只出现在 `projection_engine.py:255` 另起的 refs_text 行。⟹ **同一 id（`tool_{event_id}`）两种形并存，只有不显眼的那个能取回**；弱模型复制显眼 inline 形 → `not_retrievable` → 正落读循环墙。`test_ccr_producer_loop_closure.py`（10 绿）只测 `[receipt_ref:ID]` 形，从不测 inline 形 → 绿掩盖此 bug（CCR-4）。

**修复 P0（floor-safe，冷路径，免 bench）**：
1. 给 `_MARKER_PATTERNS` **加两条 retrieve-side pattern** 把 `[Large output stored in receipt <id>]` / `[Large content stored in receipt <id>]` 解到裸 `<id>`。**只动 retrieve 解析侧，不碰任何 model-visible placeholder/前缀 → 无 prefix 变更 → 不需 L2 bench**（保持 §5 / CCR-5 的 floor-safe 不变式）。
2. **禁止**用「改 `build_turns` 占位符为 `[receipt_ref:]` 形」来修——那会 mutate 热投影前缀（floor-unsafe，须 bench）。
3. 加 **path-faithful 测**（CCR-4）：驱真 `ProjectionEngine.build_payload→project`，断言模型所见的**任一**指针形都能 `context_retrieve` 取回 verbatim bytes。把绿测从「形态特定」升级为「路径忠实」。

### 10.4 不变项（CCR-5，第一轮做对的）
`offload_content` 无论 `on_offload` 是否接、返回**同一 placeholder 对象**（`receipt_store.py:69`）→ 模型可见前缀字节不变 → producer 接线本身**确 floor-safe、免 bench**。workspace 隔离（`7670e903`）+ §8 verbatim-only 均 clean。**任何 CCR-3 修复必须保住此不变式**（优先冷路径/retrieve-side）。

---

## 11. 三次深度审计修订（2026-06-17，实证定论）

> 第三轮把 §10 的 hypothetical 用真实运行时定论，并发现一个**比 CCR-3 更上游、决定整个 T1-A 死活**的阻塞。证据见 `docs/research/HEADROOM_CROSS_POLLINATION_20260616.md` §5。

### 11.1 真正的 keystone 阻塞：consumer 在供给层 inert（supersede CCR-3）
**`context_retrieve` 不在任何角色 `tool_policy.whitelist`**（pm/architect/CE 14、director 18、qa 9、scout 18 全无）。活 Director 工具集由 `build_native_tool_schemas(profile)`（`tool_helpers.py:291`）**只发白名单内工具**构造 → 模型拿 17 个 native schema、**无 context_retrieve** → 永不被供给该工具。系统提示 7044 字符也无一字提 retrieve/receipt_ref（本人实证）。⟹ **producer（本蓝图主体）缓存的字节无任何模型能读回 = 纯内存开销；§10 的 CCR-3 指针修复 moot 直到工具被供给。** 三方实证（lead 本人 + auditor + adversarial skeptic）一致，severity=blocker。

三层叠加，每层独立致命，须一起修：
1. **供给层**：加 context_retrieve 进 director(+qa) 白名单（两份 core_roles.yaml）。**改 tool-schema 前缀 → 须 L2 bench**（env flag default off 对照）。
2. **注册层**：spec 不在持久 `_BUILTIN_REGISTRY`（与 `context_retrieve.py:52-57` 注释矛盾），只靠 handler import 时 ContextVar self-register。skeptic 实证 warm-context（真实 role loop 几乎总有先跑的工具）下 dispatch 正常 → "Unknown tool 不可派发"过强=**refuted**；但 cold-start 仍可能漏 → 移 spec 进 `_BUILTIN_REGISTRY`（cheap，floor-free）。
3. **可用层**：系统提示加一行 nudge 解释何时调（并入 #1 bench）。
之后才轮到 §10 的 CCR-3a（且 regex 须**锚定 + 精确字符类** `^\[\s*Large (output|content) stored in receipt\s+(?P<ref>[A-Za-z0-9_.\-]+)\s*\]$`，捕获 id 已含 tool_/evt_ 前缀=正是 cache key），最后加 E2E proof-of-effect 测。

### 11.2 §10.2 耐久性 DECIDED（降级，非升级）
director run 是**单长寿 asyncio 进程**（`cli_thin.py:213`）+ 线程 worker（`dispatch_pipeline.py:1142`，**非** ProcessPool/subprocess）。`get_default_cache()` 单例 id() 跨线程不变，offload（gateway 侧）↔ retrieve（executor 侧）实证命中**同一** cache 实例。⟹ **§10.2 的"必加 sqlite 持久 / 建 receipt_id_index"降为"文档化 in-process scope"**：在 `get_default_cache()` 加 docstring + `dispatch_pipeline.py:1142` 加 tripwire 注释"CCR 正确性依赖 worker 是单进程内线程；改 ProcessPool 会静默打断内存 CCR"。可选 TTL 300→600s（一标量 floor-free）吸尾延（cadence 数据部分不可复现，故仅可选）。

### 11.3 线程安全 + §8/§6.6 实证 CLEAN
CCR 单例在真并发（threading.Thread/RLock，24线程×4000op stress 零损坏）下线程安全；唯一 latent hazard=lock 跨 sqlite IO 持有，但 live 单例 sqlite_path=None 故该分支 DEAD（若将来上持久 sqlite 须把 IO 移出锁）。§8：cache 严格进程内 verbatim、无跨 run 答案记忆，execute_method 合成器若其工具结果被 pointerize 也是 verbatim 且随进程蒸发。workspace 48-bit 命名空间安全到 ~20M。§6.6 clean。**这些是"实际没问题"的实证结论，非待办。**
