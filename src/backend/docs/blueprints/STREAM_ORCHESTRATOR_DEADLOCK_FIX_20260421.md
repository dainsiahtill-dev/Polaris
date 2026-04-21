# StreamOrchestrator 死锁修复蓝图

## 文档信息
- **版本**: v1.0
- **日期**: 2026-04-21
- **作者**: Principal Architect
- **状态**: 已实施

## 1. 问题背景

Polaris CLI 在自我完善时遭遇**自指死锁（self-referential deadlock）**：
- stream_orchestrator.py 陷入无限探索循环（9+ Turn 只读不写）
- delivery_mode 跨 Turn 丢失（MATERIALIZE_CHANGES → ANALYZE_ONLY）
- PhaseManager 卡在 EXPLORING 阶段

## 2. 根因分析

```
Turn 0: ledger._original_delivery_mode = MATERIALIZE_CHANGES
        → Turn 结束，ledger 被丢弃
        
Turn 1: 新 ledger._original_delivery_mode = None
        → continuation prompt 解析为 ANALYZE_ONLY
        → 触发 intent-mismatch-allow-exploration
        → 死循环
```

## 3. 架构设计

### 3.1 多层 Fallback 链

```
Priority 1: SESSION_PATCH JSON 块中的 delivery_mode
      ↓
Priority 2: <DeliveryMode> XML 标签（session_orchestrator.py 注入）
      ↓
Priority 3: 中文关键词检测（完善/修改/优化 + recent_reads 非空）
      ↓
Priority 4: original_delivery_mode 历史状态
      ↓
Priority 5: parsed_progress 推断（implementing → MATERIALIZE_CHANGES）
      ↓
Fallback: ANALYZE_ONLY
```

### 3.2 模块职责

| 模块 | 职责 | 文件 |
|------|------|------|
| Prompt Parser | 多格式解析（JSON/XML/Keywords） | stream_orchestrator.py |
| Delivery Resolver | 智能决策 delivery_mode | stream_orchestrator.py |
| Mutation Triggers | 中文关键词检测 | mutation_triggers.py |
| Read Strategy | 自动切换读取策略 | read_strategy.py |
| Escape Hatch | 自修复超时机制 | stream_orchestrator.py |

## 4. 核心数据流

```
User Prompt
    │
    ├─▶ [Parser] SESSION_PATCH JSON
    ├─▶ [Parser] <DeliveryMode> XML
    └─▶ [Parser] 中文关键词（完善/修改/优化）
    │
    ▼
[Delivery Resolver]
    │
    ├─▶ Priority 1-3: 显式元数据
    └─▶ Priority 4-5: Fallback 推断
    │
    ▼
[Mutation Guard]
    │
    ├─▶ recent_reads 检查
    ├─▶ 关键词检测
    └─▶ 文件内容确认
    │
    ▼
[Phase Manager]
    │
    ├─▶ EXPLORING → CONTENT_GATHERED → MUTATION_READY
    └─▶ Escape Hatch: N turns 无 write → 强制降级
```

## 5. 实施清单

### Phase 1: DeliveryMode 跨 Turn 持久化 ✅
- [x] session_orchestrator.py: Goal 块注入 `<DeliveryMode>` 标签
- [x] stream_orchestrator.py: 解析 XML 标签恢复 delivery_mode

### Phase 2: 关键词触发机制 ✅
- [x] mutation_triggers.py: 18 个中文关键词检测
- [x] stream_orchestrator.py: 集成关键词检测到 Delivery Resolver

### Phase 3: 读取策略优化 ✅
- [x] read_strategy.py: 自动切换 read_file / repo_read_slice
- [x] 100KB 阈值 + 截断检测

### Phase 4: 自修复逃生舱 ✅
- [x] Turn 计数器监控（`_consecutive_exploring_count`）
- [x] N turns（阈值=3）无 write 强制降级为 MATERIALIZE_CHANGES
- [x] 限制工具列表为只保留 write 工具（write_file/edit_file/repo_apply_diff/precision_edit/write_files_batch）
- [x] 逃生舱触发后锁定（`_escape_hatch_triggered` 防止重复触发）

### Phase 5: Prompt 清理（WorkingMemory 优化）✅
- [x] read_files 限制只显示最近 5 个文件（避免历史文件列表过长）
- [x] 上回合分析结果截断从 3000 chars 降到 1500 chars（约 375 tokens）
- [x] 工具执行结果总预算限制 3000 chars（避免多工具结果累加爆炸）
- [x] 保持关键上下文：已确认事实、待验证假设、最近失败

## 6. 技术选型理由

1. **多格式 Parser**: 兼容历史 SESSION_PATCH 和新 XML tag，平滑迁移
2. **预编译正则**: 关键词检测性能优化，避免重复编译
3. **优先级 Fallback**: 确保任意元数据缺失时仍能正确决策
4. **防御性设计**: 多层保险防止极端情况死锁

## 7. 验证结果

| 门禁 | 状态 |
|------|------|
| Ruff | ✅ PASS |
| MyPy --strict | ✅ PASS |
| pytest (transaction) | ✅ 261/261 PASS |
| pytest (mutation_triggers) | ✅ 69/69 PASS |
| pytest (read_strategy) | ✅ 47/47 PASS |

## 8. 风险与边界

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| **关键词误触发** | 低 | recent_reads 约束 + 语义匹配 |
| **XML 标签冲突** | 极低 | 用户输入包含 `<DeliveryMode>` 概率趋近于 0 |
| **文件大小阈值** | 中 | 100KB 经验值，后续可动态调整 |
| **中文语境复杂** | 低 | 18 个关键词覆盖主要变体 |
| **逃生舱阈值** | 低 | 3 回合经验值，已添加锁定机制防止反复触发 |
| **Prompt 截断** | 低 | 保留 1500 chars 足够分析上下文 |

## 9. 生产验证清单

- [x] 所有 288 个 transaction + session 测试通过
- [x] Ruff lint 零错误（含新增代码）
- [x] MyPy --strict 零类型错误
- [x] 向后兼容：现有 SESSION_PATCH 解析不受影响
- [x] 防御性设计：逃生舱只在 continuation turn 触发
- [x] Token 优化：WorkingMemory 减少约 50% token 消耗

## 10. 后续优化

1. **机器学习意图识别**: 替代关键词匹配，提升准确率
2. **动态阈值调整**: 基于历史数据自动优化文件大小阈值和逃生舱阈值
3. **可视化监控**: 添加 Phase 流转监控面板
4. **A/B 测试**: 对比逃生舱开启/关闭的任务完成率

---
*本蓝图为 Polaris Backend 架构文档，遵循 AGENTS.md 规范*
*实施日期: 2026-04-21*
*实施团队: Principal Architect + 3 Senior Engineers*
