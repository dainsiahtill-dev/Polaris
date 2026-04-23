# B桶回源验证报告

> **验证日期**: 2026-04-23
> **验证人**: Principal Architect
> **方法**: 源码级 sink 点分析

---

## B1: workspace 路径穿越 — 已防护

**验证结果**: ✅ 风险已控制，降级为 P3

**证据链**:
1. `resolve_workspace_persistent_path()` (storage/layout.py:711) 调用 `normalize_logical_rel_path()`
2. `normalize_logical_rel_path()` (storage/layout.py:664) 明确拒绝:
   - 绝对路径 (`os.path.isabs(raw)`) — line 668
   - `../` 或 `..` 开头的路径 — line 682-683
   - 使用 `os.path.normpath()` 规范化 — line 679
3. `_join_under()` (storage/layout.py:690) 使用 `os.path.commonpath()` 确保最终路径在根目录下:
   ```python
   if os.path.commonpath([abs_root, full]) != abs_root:
       raise ValueError(...)
   ```

**结论**: 路径穿越攻击面已被多层防御覆盖，无需额外修复。

---

## B2: event_id 截断碰撞 — 风险极低

**验证结果**: ✅ 风险可控，降级为 P3

**证据链**:
1. `_event_id()` (helpers.py:56) 使用 SHA-256 hex digest 前 12 字符:
   ```python
   digest = hashlib.sha256(f"{sequence}:{role}:{content}".encode()).hexdigest()[:12]
   ```
2. 12 hex chars = 48 bits 熵
3. 生日碰撞概率:
   - n=1K (普通 session): P ≈ 10^-10 (可忽略)
   - n=1M (极端 session): P ≈ 0.18%
   - n=10M (几乎不可能): P ≈ 18%

**结论**: 普通使用场景下碰撞概率可忽略。建议添加 collision detection 作为防御性编程，但不列为 P0。

---

## B3: cleanup 资源泄漏 — 基本完整

**验证结果**: ⚠️ 已改善，建议增强，维持 P1

**证据链**:
1. `cleanup()` (runtime.py:332-365) 已清理:
   - `_dialog_act_classifier = None`
   - `_pipeline_runner = None`
   - `_snapshot_store = None`
   - `_content_store_cache.clear()`
   - `_executor.shutdown(wait=True)`
   - `_receipt_store.close()`
   - `_working_state_manager.close()`
   - `_projection_engine.close()`

**建议增强**:
- 添加弱引用追踪（`weakref.ref`）监控实例生命周期
- 添加 `__del__` 或 `atexit` 钩子确保 cleanup 被调用
- 为长时间运行的服务添加定期 health check

---

## B4: snapshot 异步保存阻塞 — 已优化

**验证结果**: ✅ 已优化，降级为 P3

**证据链**:
1. `save_async()` (snapshot.py:189-224) 使用 `run_in_executor()`:
   ```python
   loop = asyncio.get_event_loop()
   return await loop.run_in_executor(None, self._save_sync, snapshot, filepath)
   ```
2. 文件 I/O 已 offload 到线程池
3. 锁内只保留前置检查（idempotency + filepath resolve）

**结论**: 事件循环阻塞已消除。大 snapshot 序列化 CPU 开销仍存在，但属于正常计算负载。

---

## B桶综合定级调整

| ID | 原评级 | 验证后评级 | 理由 |
|----|--------|-----------|------|
| B1 | P1 (待证实) | P3 | 路径穿越已有多层防护 |
| B2 | P1-P2 (待评估) | P3 | 48-bit 熵，碰撞概率极低 |
| B3 | P1 (待确认) | P1 | 已较完整，建议增强弱引用追踪 |
| B4 | P1 (待评估) | P3 | 已使用 run_in_executor 优化 |

**建议**: B1/B2/B4 从 blocker 列表移除，作为常规技术债管理。B3 保持关注。
