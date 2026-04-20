# ContextOS Memory Architecture v2.1: 消除冗余的可靠设计方案

> 基于 2026-04-15 生产事件分析（fileserver-32fc198ee3e4, 109KB snapshot, src/server.ts 出现 20+ 次）
> v2.0 评审反馈深化，补全持久化盲点、引用生命周期、灰度策略、可观测性。

---

## 0. 核心教训

### 上次修了什么（Phase 1 已落地）

| 修复 | 文件 | 效果 |
|------|------|------|
| `if`→`elif` 互斥分类 | `generic.py:301-313` | "蓝图"不再同时进 plan+deliverable |
| 跨列表 value 去重 | `helpers.py:235-332` | 同一 value 不能在 `task_state.*` 内重复 |
| `state_history` 只保留 superseded | `runtime.py:1475` | 消除全量副本 |
| read_file 截断提示 | `tool_loop_controller.py:812-817` | LLM 知道内容不完整 |

### 为什么这些修复不彻底

1. **治标不治本**：修的是分类逻辑 bug，但架构允许内容被无限复制
2. **没有全局屏障**：每个模块各自做去重，没有单一真相源（SSOT）
3. **`to_dict()` 是核弹**：`ContextOSSnapshot.to_dict()` 一次序列化全部内容，只为提取 5 行摘要
4. **19 个拷贝点**，同一条内容 **5 次独立内存分配**

### 根本问题

**不是"某些分类有重叠"这种 bug，而是整个数据流架构缺乏"存一次、用引用"的基本原则。**

---

## 1. 设计哲学：三个铁律 + 引用生命周期

### 铁律 1：内容只存一次（Content-Addressable Storage）

```
任何文本内容（文件内容、工具结果、状态值）在内存中只存一份。
其他所有位置通过 ContentRef 引用。
```

### 铁律 2：数据流不可变（Immutable Append-Only）

```
内容一旦 intern 就不可修改。
"修改" = 追加新版本 + 旧版本标记 superseded。
```

### 铁律 3：查询与存储分离（Read Model ≠ Write Model）

```
写入时只记录事实。
读取时按需组装视图（projection）。
working_state 是 projection，不是存储层。
```

### 铁律 4（v2.1 新增）：引用必须管理生命周期

```
每个 ContentRef 有明确的 acquire/release 时机。
不存在隐式全局 store —— store 通过参数传递，可追踪可调试。
evict 只淘汰 ref_count == 0 的内容，历史内容被 evict 时触发告警。
```

---

## 2. 核心数据结构：ContentStore（全面实现版）

### 2.1 ContentRef

```python
@dataclass(frozen=True, slots=True)
class ContentRef:
    """内容引用 —— 替代所有裸字符串"""
    hash: str          # sha256[:24], 内容唯一 ID
    size: int          # 原始字节数
    mime: str          # "text/plain", "application/json", etc.
    encoding: str = "utf-8"

    def __repr__(self) -> str:
        return f"ContentRef({self.hash[:8]}..., {self.size}B)"
```

### 2.2 ContentStore

```python
class ContentStore:
    """全局单一真相源（SSOT）—— Content-Addressable + Immutable

    生命周期: 跟随 ContextOS 实例，跨 turn 持久化。
    一个 session 一个实例。通过参数传递，不使用全局变量。
    """

    def __init__(self, max_entries: int = 500, max_bytes: int = 50_000_000):
        self._store: dict[str, str] = {}      # hash → content
        self._refs: dict[str, int] = {}        # hash → ref_count
        self._access: dict[str, float] = {}    # hash → last_access_time
        self._total_bytes: int = 0
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        # 统计
        self._hit: int = 0
        self._miss: int = 0
        self._evict_count: int = 0
        self._dedup_saved_bytes: int = 0

    def intern(self, content: str) -> ContentRef:
        """唯一写入入口。相同的字符串永远返回相同的 ContentRef。"""
        if not content:
            content = ""
        raw = content.encode("utf-8")
        h = hashlib.sha256(raw).hexdigest()[:24]

        if h in self._store:
            # hash 碰撞二次验证
            if self._store[h] != content:
                raise RuntimeError(
                    f"ContentStore hash collision: {h[:8]}... "
                    f"(size={len(raw)} vs {len(self._store[h].encode('utf-8'))})"
                )
            self._refs[h] += 1
            self._access[h] = time.monotonic()
            self._dedup_saved_bytes += len(raw)
            self._hit += 1
        else:
            self._evict_if_needed(len(raw))
            self._store[h] = content
            self._refs[h] = 1
            self._access[h] = time.monotonic()
            self._total_bytes += len(raw)

        return ContentRef(hash=h, size=len(raw), mime=_guess_mime(content))

    def get(self, ref: ContentRef) -> str:
        """按需取回。evicted 返回占位符。"""
        content = self._store.get(ref.hash)
        if content is None:
            self._miss += 1
            logger.warning(
                "ContentStore evicted historical content: hash=%s size=%d",
                ref.hash[:8], ref.size,
            )
            return f"<evicted:{ref.hash}>"
        self._access[ref.hash] = time.monotonic()
        return content

    def get_if_present(self, ref: ContentRef) -> str | None:
        """取回内容，不存在返回 None（不计数为 miss）。"""
        return self._store.get(ref.hash)

    def release(self, ref: ContentRef) -> None:
        """释放引用计数。ref_count 归零后可被 evict。"""
        if ref.hash in self._refs:
            self._refs[ref.hash] -= 1
            if self._refs[ref.hash] <= 0:
                self._refs.pop(ref.hash, None)
                self._access.pop(ref.hash, None)

    def release_all(self, refs: Iterable[ContentRef]) -> None:
        """批量释放（snapshot 销毁时调用）。"""
        for ref in refs:
            self.release(ref)

    def _evict_if_needed(self, incoming_bytes: int) -> None:
        """混合淘汰策略：ref_count 最低优先 → LRU 辅助。"""
        while (
            (self._total_bytes + incoming_bytes > self._max_bytes
             or len(self._store) >= self._max_entries)
            and self._store
        ):
            # 只淘汰 ref_count == 0 的条目
            zero_ref = [
                (self._access.get(h, 0), h)
                for h in self._store
                if self._refs.get(h, 0) == 0
            ]
            if zero_ref:
                zero_ref.sort()  # 最老访问优先
                _, victim = zero_ref[0]
            else:
                # 所有条目都有引用 —— 选 ref_count 最低 + 最老
                candidates = [
                    (self._refs.get(h, 0), self._access.get(h, 0), h)
                    for h in list(self._store.keys())
                ]
                candidates.sort()
                _, _, victim = candidates[0]

            content = self._store.pop(victim)
            self._refs.pop(victim, None)
            self._access.pop(victim, None)
            self._total_bytes -= len(content.encode("utf-8"))
            self._evict_count += 1
            logger.info(
                "ContentStore evicted: hash=%s size=%d total=%d",
                victim[:8], len(content.encode("utf-8")), self._total_bytes,
            )

    def _guess_mime(self, content: str) -> str:
        """简单高效 MIME 猜测。"""
        stripped = content.lstrip()
        if stripped.startswith(("{", "[")):
            return "application/json"
        if stripped.startswith("<?xml") or stripped.startswith("<"):
            return "application/xml"
        if any(kw in content[:200] for kw in ("def ", "class ", "import ", "function ")):
            return "text/x-code"
        return "text/plain"

    @property
    def stats(self) -> dict[str, Any]:
        total_lookups = self._hit + self._miss
        return {
            "entries": len(self._store),
            "bytes": self._total_bytes,
            "max_bytes": self._max_bytes,
            "utilization": self._total_bytes / self._max_bytes if self._max_bytes else 0,
            "dedup_saved_bytes": self._dedup_saved_bytes,
            "hit_rate": self._hit / total_lookups if total_lookups else 1.0,
            "evict_count": self._evict_count,
        }

    # ── 持久化重建 ──────────────────────────────────────────────

    def export_content_map(self, refs: set[str]) -> dict[str, str]:
        """导出指定 hash 集合对应的 content_map（用于序列化）。"""
        return {h: self._store[h] for h in refs if h in self._store}

    @classmethod
    def from_content_map(cls, content_map: dict[str, str]) -> ContentStore:
        """从持久化的 content_map 重建 store。"""
        store = cls()
        for h, content in content_map.items():
            store._store[h] = content
            store._refs[h] = 1
            store._access[h] = time.monotonic()
            store._total_bytes += len(content.encode("utf-8"))
        return store
```

### 2.3 引用生命周期管理

```python
class RefTracker:
    """追踪活跃引用，确保 acquire/release 配对。"""

    def __init__(self, store: ContentStore):
        self._store = store
        self._active: set[str] = set()  # hash 集合

    def acquire(self, ref: ContentRef) -> ContentRef:
        """intern + 注册为活跃引用。"""
        self._active.add(ref.hash)
        return ref

    def release(self, ref: ContentRef) -> None:
        """释放引用 + 从活跃集合移除。"""
        self._active.discard(ref.hash)
        self._store.release(ref)

    def release_all(self) -> None:
        """批量释放所有活跃引用（snapshot 销毁时调用）。"""
        self._store.release_all(
            ContentRef(hash=h, size=0, mime="") for h in self._active
        )
        self._active.clear()

    def collect_refs_for_persist(self) -> set[str]:
        """收集所有活跃引用的 hash（用于 export_content_map）。"""
        return set(self._active)
```

**生命周期规则（严格执行）**：

| 时机 | 操作 |
|------|------|
| `append_tool_result` | `store.intern()` + `tracker.acquire()` |
| `supersedes` 发生 | `tracker.release(旧 ref)` |
| `ARCHIVE/CLEAR` route | `tracker.release(event.content_ref)` |
| snapshot 销毁 | `tracker.release_all()` |

### 2.4 持久化机制（v2.1 关键补全）

**v2.0 的盲点**："ContentStore 不持久化" 与 "历史 tool result 必须精确保留" 存在矛盾。
如果一个 50KB 的文件内容被 evict，后续 turn 的历史 transcript 中会出现 `<evicted:abc123>`，
导致 LLM 看到不完整的历史上下文。

**v2.1 解决方案**：`content_map` 嵌入 snapshot 序列化产物。

```python
# models.py
@dataclass(frozen=True, slots=True)
class ContextOSSnapshotV2:
    version: int = 2
    transcript_log: tuple[TranscriptEventV2, ...] = ()
    working_state: WorkingStateV2 = field(default_factory=WorkingStateV2)
    artifact_store: tuple[ArtifactRecord, ...] = ()
    episode_store: tuple[EpisodeCard, ...] = ()
    content_map: dict[str, str] = field(default_factory=dict)  # hash → content
```

**序列化流程**：
1. `tracker.collect_refs_for_persist()` → 收集所有活跃 hash
2. `store.export_content_map(hashes)` → 导出 `{hash: content}`
3. 写入 `snapshot_v2.content_map`（去重后的唯一内容）
4. 所有 Event/StateEntry 只写 `ContentRef`（hash + size）

**反序列化流程**：
1. `ContentStore.from_content_map(snapshot.content_map)` → 重建 store
2. Event/StateEntry 的 `ContentRef` 直接可用
3. `get(ref)` 恢复完整内容

**磁盘收益**：109KB → < 25KB（每条内容只存一次 + ref 替代全文）

**不违反"不持久化 ContentStore"原则**：
- `content_map` 嵌入 snapshot 文件，不单独 IO
- 不跨 session 全局持久化
- 每次投影可从事件重建

---

## 3. 改造后的数据流

### 3.1 写入路径（tool result → transcript）

```
Raw Tool Result (dict, 包含 file content)
  │
  ├─ compact_result = _compact_tool_result_payload(...)
  ├─ ref = store.intern(compact_result)       # 唯一存储点
  ├─ tracker.acquire(ref)
  │
  └─ 创建 TranscriptEventV2:
       content_ref: ContentRef(hash="abc123")
       content: ""                             # 不存原始内容
```

### 3.2 读取路径（transcript → LLM prompt）

```
TranscriptEventV2(content_ref=ContentRef(hash="abc123"))
  │
  ├─ ACTIVE: content = store.get(ref) → 全文注入 prompt
  ├─ ARCHIVE: 只注入 stub (artifact_id + peek 180 chars)
  └─ CLEAR: 不注入
```

### 3.3 WorkingState 路径（transcript → state extraction）

```
TranscriptEventV2(content_ref=ContentRef(hash="abc123"))
  │
  ├─ full_text = store.get(ref)
  ├─ hints = extract_state_hints(full_text)
  └─ 每个 hint value:
       hint_ref = store.intern(hint_text)
       tracker.acquire(hint_ref)
       StateEntryV2(value_ref=hint_ref)
```

### 3.4 去重发生在哪里

| 层级 | 机制 | 效果 |
|------|------|------|
| ContentStore | hash 去重 | 相同字符串永远只存一份 |
| extract_state_hints | elif 互斥分类 | 同一内容不进多个分类列表 |
| StateAccumulator | _seen_value_hashes | 分类遗漏时兜底去重 |
| state_history | 只保留 superseded | 不复制活跃条目 |
| content_map | 持久化去重 | 磁盘上也只存一份 |

---

## 4. 最大的收益点：消除 to_dict() 核弹

### 4.1 当前问题

`context_gateway.py:417`:
```python
snapshot_summary = self._format_context_os_snapshot(proj_snapshot.to_dict())
```

这行代码：
1. `to_dict()` 序列化整个 snapshot（~109KB dict 树）
2. `_format_context_os_snapshot` 从中提取 5 行摘要
3. 整个 109KB dict 被垃圾回收

### 4.2 修复：投影专用视图

```python
class SnapshotSummaryView:
    """轻量级 snapshot 摘要 —— 不序列化任何内容。"""

    @staticmethod
    def from_snapshot(snapshot: ContextOSSnapshot) -> dict[str, Any]:
        ws = snapshot.working_state
        return {
            "goal": ws.task_state.current_goal.value if ws.task_state.current_goal else None,
            "open_loops_count": len(ws.task_state.open_loops),
            "decisions_count": len(ws.decision_log),
            "artifacts_count": len(snapshot.artifact_store),
            "episodes_count": len(snapshot.episode_store),
            "transcript_events_count": len(snapshot.transcript_log),
        }
```

**预估收益**：每次 context 组装减少 ~100KB 内存分配。

---

## 5. 分层改造路线图（灰度版）

### Phase 0：ContentStore 核心（Week 1）

**目标**：建立 SSOT 基础设施，不改现有接口

| 文件 | 改动 |
|------|------|
| `context_os/content_store.py` | **新建** ContentStore + ContentRef + RefTracker |
| `context_os/content_store_test.py` | **新建** 单元测试 |
| `context_os/models.py` | StateEntry 添加 `value_ref: ContentRef \| None = None` |
| `context_os/helpers.py` | `_StateAccumulator` 添加 `store: ContentStore \| None = None` 参数 |

**灰度开关**：
```python
# polaris/kernelone/context/context_os/feature_flags.py
USE_CONTENT_STORE: bool = False  # 默认关闭，Phase 1 双写验证后开启
```

**验证**：
- `test_content_store_intern_idempotent` — 相同内容返回相同 ref
- `test_content_store_evict_ref_count_zero` — 只淘汰无引用条目
- `test_content_store_hash_collision_detection` — 不同内容相同 hash 抛异常
- `test_content_store_from_content_map` — 持久化重建正确
- `test_content_store_memory_limit` — 超限淘汰
- **现有测试全部通过**（value_ref 可选，向后兼容）

### Phase 1：TranscriptEvent 改造 + 双写（Week 2）

**目标**：TranscriptEvent 使用 content_ref，双写验证

| 文件 | 改动 |
|------|------|
| `models.py` | TranscriptEvent 添加 `content_ref: ContentRef \| None = None` |
| `runtime.py` | `_merge_transcript` 时 `store.intern(content)` + 双写 content_ref |
| `context_gateway.py` | `_messages_from_projection` 优先用 `store.get(content_ref)` |
| `tool_loop_controller.py` | `append_tool_result` 时 intern |

**双写策略**：
```python
# 在 _merge_transcript 中
if feature_flags.USE_CONTENT_STORE and store is not None:
    content_ref = store.intern(content)
    tracker.acquire(content_ref)
else:
    content_ref = None
# 两个字段都写入，旧字段在 Phase 3 删除
event = TranscriptEvent(content=content, content_ref=content_ref, ...)
```

**验证**：
- 内存 profile：同一 50KB 文件在 snapshot 中只占 1 份（当前 5 份）
- 双写对比：`store.get(content_ref) == content` 对所有事件成立
- **现有测试全部通过**

### Phase 2：消除 to_dict() 核弹 + 持久化 content_map（Week 2-3）

**目标**：消除全量序列化，引入 SnapshotV2

| 文件 | 改动 |
|------|------|
| `context_gateway.py` | 用 `SnapshotSummaryView` 替代 `to_dict()` |
| `session_continuity.py` | `to_dict_v2()` 带 content_map；`from_dict_v2()` 重建 store |
| `models.py` | 新增 `ContextOSSnapshotV2` |

**灰度开关**：
```python
USE_SNAPSHOT_V2: bool = False  # 默认关闭，对比验证后开启
```

**验证**：
- `test_snapshot_v2_size_regression` — 断言 < 30KB
- `test_snapshot_v2_roundtrip` — 序列化/反序列化内容一致
- `test_snapshot_summary_view` — 不触发 to_dict()

### Phase 3：StateEntry 全面迁移（Week 3）

**目标**：所有 StateEntry.value → StateEntry.value_ref

| 文件 | 改动 |
|------|------|
| `models.py` | `StateEntry.value` 变为 `@property`（从 store 取） |
| `runtime.py` | `_patch_working_state` 全部通过 store |
| `generic.py` | `extract_state_hints` 内部使用 store.intern |
| `models_v2.py` | Pydantic 版本同步 |
| `feature_flags.py` | `USE_CONTENT_STORE = True` |

**验证**：
- 109KB snapshot → 目标 < 25KB
- src/server.ts 出现次数：20+ → < 3
- **现有测试全部通过**

### Phase 4：清理、监控、告警（Week 4）

| 文件 | 改动 |
|------|------|
| `content_store.py` | Prometheus metrics + 结构化日志 |
| `context_gateway.py` | snapshot 大小 > 30KB → Sentry warning |
| `feature_flags.py` | 删除所有开关（默认 V2） |
| 删除旧字段 | 移除 `StateEntry.value` 的 backward compat |
| 删除旧代码 | 移除 `_seen_value_hashes`（已被 ContentStore 取代） |

**可观测性**：

| 指标 | 类型 | 告警条件 |
|------|------|----------|
| `content_store_entries` | gauge | > 400 |
| `content_store_bytes` | gauge | > 40MB |
| `content_store_hit_rate` | gauge | < 70% |
| `content_store_evict_total` | counter | 任何 evict |
| `content_store_evicted_historical` | counter | > 0（**立即告警**） |
| `snapshot_size_bytes` | gauge | > 30KB（warning），> 50KB（critical） |

---

## 6. 为什么这个方案靠谱

### 6.1 渐进式改造，每步可验证

```
Phase 0: 新增 ContentStore（不改任何现有代码）→ 全部测试通过
Phase 1: 双写 content_ref + content（灰度关闭）→ 全部测试通过
Phase 2: SnapshotSummaryView + content_map（灰度关闭）→ 全部测试通过
Phase 3: 全面迁移，删除旧字段（开启灰度）→ 全部测试通过
Phase 4: 清理 + 监控
```

每个 Phase 独立、可回滚、可验证。不存在"大爆炸"式重写。

### 6.2 与现有代码兼容

```python
# 旧代码（继续工作）
entry.value  # @property, 从 ContentStore 取回

# 新代码（显式引用）
entry.value_ref  # ContentRef, 不取回内容
store.get(entry.value_ref)  # 按需取回
```

### 6.3 解决根本问题

| 之前 | 之后 |
|------|------|
| 每个模块各自持有内容副本 | ContentStore 是唯一持有者 |
| 去重依赖正确分类 | 去重基于 hash（永远正确） |
| `to_dict()` 全量序列化 | 只序列化 ref + content_map |
| 109KB snapshot | 目标 < 25KB |
| 同一文件 5x 内存 | 同一文件 1x 内存 |
| evict 导致历史丢失 | content_map 嵌入 snapshot 保证完整 |
| 隐式全局 store | 显式参数传递 + RefTracker 追踪 |

### 6.4 已有的类似模式可以复用

| 已有模式 | 位置 | 复用方式 |
|----------|------|----------|
| ArtifactRecord stub | `models.py:281-291` | StateEntry 也用 ref/stub 模式 |
| IdempotentVectorStore | `idempotent_vector_store.py` | hash → ID 的映射模式 |
| CompressionStateTracker | `compression_tracker.py` | hash → 结果的 memoization |
| _seen_value_hashes | `helpers.py:253` | 演进为 ContentStore（Phase 4 删除） |

### 6.5 失败模式分析

| 风险 | 缓解 |
|------|------|
| ContentStore 被清空（重启） | `content_map` 嵌入 snapshot，反序列化时重建 |
| 历史内容 evicted | `content_store_evicted_historical` counter > 0 触发告警；返回 `<evicted:hash>` 占位符 |
| hash 碰撞 | 24 chars (96 bits)，500 条目碰撞概率 < 10^-20；intern 时二次验证 |
| 内存超限 | 80% 水位线日志 warning；ref_count 最低优先 evict |
| 向后兼容 | value 是 property，旧代码无感 |
| 性能 | intern O(1)、get O(1)、evict O(n log n) 但 n ≤ 500 |
| 调试困难 | ContentStore.stats + RefTracker + 结构化日志 |
| 大内容（>1MB） | intern 时日志 warning，不计入 dedup_savings |

---

## 7. 不做什么

| 不做 | 原因 |
|------|------|
| 不做全局 ContentStore（跨 session） | 内容可从文件系统/LLM 重新获取 |
| 不做 ContentStore 单独持久化 | `content_map` 嵌入 snapshot 足够 |
| 不改 ContextEvent（ToolLoopController 的） | turn 级临时数据，不需要全局去重 |
| 不改 TranscriptEvent 的 route/decision 逻辑 | 那些是正确的，问题只在内容存储方式 |
| 不引入外部依赖（Redis 等） | ContentStore 是纯内存结构 |
| 不做内容压缩（gzip） | 50MB 容量已覆盖 99.9% session，压缩收益 <10% |
| 不引入 magic 库猜测 MIME | 内置 `_guess_mime` 足够，避免新依赖 |

---

## 8. 验证指标

| 指标 | 当前值 | Phase 3 目标 | 测量方式 |
|------|--------|-------------|----------|
| Snapshot JSON 大小 | 109KB | ≤ 25KB | `len(json.dumps(snapshot.to_dict_v2()))` |
| 同一文件在 snapshot 中出现次数 | 20+ | ≤ 2 | `snapshot_json.count("server.ts")` |
| 每次 context 组装内存峰值 | ~500KB | ≤ 80KB | `tracemalloc` |
| `to_dict()` 调用次数（每次 projection） | 1 | 0 | 日志计数 |
| ContentStore hit rate | N/A | ≥ 85% | `store.stats` |
| content_map 去重率 | N/A | ≥ 70% | `(raw - unique) / raw` |
| 现有测试通过率 | 100% | 100% | `pytest` |

**新增测试**：
- `test_content_store_intern_idempotent` — 相同内容返回相同 ref
- `test_content_store_evict_ref_count_zero` — 只淘汰无引用条目
- `test_content_store_hash_collision_detection` — 不同内容相同 hash 抛异常
- `test_content_store_persist_roundtrip` — export + from_content_map 一致
- `test_snapshot_v2_size_regression` — 断言 < 30KB
- `test_production_snapshot_replay` — 109KB 生产快照回放对比 v2
- `test_long_session_no_leak` — 100+ turn 内存不泄漏
- `test_grayscale_dual_write_parity` — 灰度双写新旧字段一致
