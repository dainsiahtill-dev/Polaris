# ContextOS 全量架构审计报告 & 追赶蓝图

**审计日期**: 2026-04-11
**审计团队**: 10人专家并行审计
**基准对比**: Claude Code / GitHub Copilot / 现代主流 Agent

---

## 一、Executive Summary

### 审计规模

| 维度 | 数量 |
|------|------|
| 审计文件 | 47 个核心模块文件 |
| 测试文件 | 57+ 个测试文件 (~655 个测试函数) |
| 发现问题 | **58 个** (P0:14, P1:14, P2:20, P3:10) |
| 代码行审计 | ~16,000 行核心代码 |

### 问题分布

```
P0 (Critical)  ████████████████████████  14个  — 立即修复
P1 (High)      ████████████████████      14个  — 本周修复
P2 (Medium)    █████████████████████████████  20个  — sprint内修复
P3 (Low)       ████████████              10个  — 后续迭代
```

### 核心结论

**ContextOS 的设计理念与 Claude Code 持平**，但存在 5 个根本性架构缺陷导致设计优势无法转化为生产价值：

1. **双系统并行** — `RoleContextGateway` 和 `StateFirstContextOS` 各行其是
2. **投影数据丢失** — `ContextOSProjection` 丰富结构被压缩为文本摘要
3. **8套 Token 估算** — 公式不一致，误差可达 6 倍
4. **核心管线脱离关键路径** — `project()` 输出从未影响 LLM 上下文
5. **Semantic Search 缺失** — 规则匹配 vs Claude Code 向量嵌入

---

## 二、问题清单 (按优先级)

### P0 — Critical (14个)

#### P0-1: Token 估算 8 处独立实现，公式不一致

| 位置 | 公式 | CJK感知 |
|------|------|---------|
| `context_os/helpers.py:30` | `ascii/4 + cjk*1.5` | ✅ |
| `context_os/domain_adapters/generic.py:60` | `ascii/4 + cjk*1.5` | ✅ (重复) |
| `context_gateway.py:777` | `ascii/4 + cjk*1.5 + overhead` | ✅ |
| `context_assembler.py:867` | `ascii/4 + cjk*1.5 + overhead` | ✅ |
| `history_materialization.py:589` | `ascii/4 + cjk*1.5 + 4` | ✅ |
| `intelligent_compressor.py:460` | `len/4.0` | ❌ |
| `chunks/assembler.py:516` | `len/4` | ❌ |
| `working_memory.py:402` | `len/4` | ❌ |

**影响**: 1000个中文字符，不同实现给出 250~1500 tokens 的估算，误差 6 倍。

**修复**: 创建 `polaris/kernelone/context/_token_estimator.py` 单一来源，删除所有重复实现。

#### P0-2: `DEFAULT_FALLBACK_WINDOW = 0` 可能导致系统崩溃

**位置**: `budget_gate.py:319`

```python
DEFAULT_FALLBACK_WINDOW = 0  # 无效值，但在 __all__ 中导出
```

**影响**: 若被意外使用，模型窗口设为 0，除零错误。

**修复**: 从 `__all__` 中移除，搜索并修复任何直接引用。

#### P0-3: `StateFirstContextOS.project()` 输出未用于 LLM 上下文

**位置**: `context_gateway.py:198-354`

**证据**: `RoleContextGateway.build_context()` 完全不调用 `project()`，只消费已序列化的 snapshot dict。

**影响**: 四车道路由、目标钉选、对话行为分类的结果全部被丢弃。

**修复**: 在 `RoleContextGateway.build_context()` 中调用 `StateFirstContextOS.project()`，用其输出替代简单的 `_expand_transcript_to_messages()`。

#### P0-4: `context_slice_plan` 从未用于消息选择

**位置**: `session_continuity.py:654-700`

**证据**: `context_slice_plan` 被存储在 `prompt_context["state_first_context_os"]` 中，但后续消息构建完全不使用它。

**修复**: 在 `RoleContextGateway` 中使用 `context_slice_plan.included` 和 `excluded` 字段指导消息优先级。

#### P0-5: `_collect_active_window` 钉选结果未传递给 LLM

**位置**: `runtime.py:1085-1097`

**证据**: `pinned_sequences` 机制在 `active_window` 中正确工作，但 LLM 只收到文本摘要。

**修复**: 直接使用 `projection.active_window` 而非 `_expand_transcript_to_messages()`。

#### P0-6: 四车道路由结果未用于压缩决策

**位置**: `runtime.py:836-850`

**证据**: 每条消息的 `route` (clear/patch/archive/summarize) 被计算并存储，但消息构建时完全不读取。

**修复**: 在 `RoleContextGateway` 中基于 `route` 做差异化处理。

#### P0-7: Dialog Act 分类结果未用于注意力引导

**位置**: `runtime.py:828-835`

**证据**: `dialog_act` 存储在 metadata 中，但 `_format_context_os_snapshot()` 不使用它。

**修复**: 在消息选择时考虑对话行为的高优先级标记。

#### P0-8: BudgetPlan 验证错误只记录不处理

**位置**: `runtime.py:1042-1050`

**证据**:
```python
validation_error = "BudgetPlan invariant violated..."
return BudgetPlan(validation_error=validation_error)  # 存储但未检查
```

**修复**: 当 `validation_error` 非空时触发紧急压缩或抛出异常。

#### P0-9: Artifact 内容未真正卸载

**位置**: `runtime.py:1283-1289`

**证据**: `_select_artifacts_for_prompt()` 直接将完整 artifact 内容嵌入 prompt，而非仅含 restore_path。

**影响**: 大产物仍占用 prompt 空间。

**修复**: 实现真正的外部存储协议，prompt 只含 `artifact_id` + `restore_tool` 路径。

#### P0-10: `StateFirstContextOS.read_artifact/read_episode/get_state()` 完全未测试

**位置**: `runtime.py:478-554`

**影响**: 这些方法在生产中被调用但无测试保障。

**修复**: 添加完整测试覆盖。

#### P0-11: `classify_assistant_followup` 未在 Protocol 中定义

**位置**: `domain_adapters/contracts.py`

**证据**: `CodeContextDomainAdapter` 实现了该方法，但 Protocol 只有 4 个方法，运行时靠 `hasattr` 规避。

**修复**: 在 Protocol 中添加该方法定义，在 `GenericContextDomainAdapter` 中提供默认实现。

#### P0-12: `_continuity_chunk_assembler` 创建但从未调用

**位置**: `context_gateway.py:195`

**证据**:
```python
self._continuity_chunk_assembler = PromptChunkAssembler(...)  # 创建了
# 但整个 build_context() 中没有调用它
```

**修复**: 集成到 `build_context()` 流程，或删除死代码。

#### P0-13: `history_materialization.py:160` 不可达代码

**位置**: `history_materialization.py:160`

```python
return modified_receipt  # line 159
return True              # line 160 — 永远无法执行
```

**修复**: 删除第160行。

#### P0-14: `_continuity_summary_tokens` 估算不准确

**位置**: `chunks/assembler.py:90`

```python
summary_tokens=max(1, len(self.continuity_summary) // 4)  # 简单除4
```

**修复**: 使用统一的 `TokenEstimator`。

---

### P1 — High (14个)

#### P1-1: `safety_margin` 概念混淆 (0.85 vs 0.05)

- `ContextBudgetGate.safety_margin = 0.85` (85%)
- `StateFirstContextOSPolicy.safety_margin_ratio = 0.05` (5%)

#### P1-2: `PromptChunkAssembler._estimate_tokens` 无 CJK 感知

**位置**: `chunks/assembler.py:516` — 简单 `len/4`

#### P1-3: `RoleContextGateway` 和 `ContextAssembler` 功能重叠

两组件都实现：历史处理、Token 估算、压缩策略。

#### P1-4: 消息去重仅用前200字符

**位置**: `context_gateway.py:736`

```python
content_hash = f"{role}:{content[:200]}"  # 碰撞风险高
```

#### P1-5: `build_pack` 未传递 domain 参数

**位置**: `session_continuity.py:386`

导致强制使用 generic 适配器。

#### P1-6: Micro-compact 修改输入列表

**位置**: `compaction.py:429-439`

违反无副作用原则。

#### P1-7: IntelligentCompressor LLM 摘要失败时静默丢弃

**位置**: `intelligent_compressor.py:397-410`

无降级方案。

#### P1-8: Active window 使用 45% 硬编码预算

**位置**: `runtime.py:1100`

Claude Code 使用语义相关性驱动，本项目硬编码比例。

#### P1-9: Signal 评分公式不一致 (48 vs 40 字符阈值)

- `compaction.py:105`: `len >= 48`
- `session_continuity.py:152`: `len >= 40`

#### P1-10: `state_first_mode_active=True` 时完全跳过压缩

**位置**: `context_gateway.py:326-328`

无紧急回退机制。

#### P1-11: `continuity` 类型 `cacheable=False` 但实际适合缓存

**位置**: `chunks/taxonomy.py:89-96`

#### P1-12: `restore_tool` 在 generic 适配器中未传递

**位置**: `domain_adapters/generic.py:187-196`

#### P1-13: 两个独立压缩触发系统无协调

- `CompactionStrategy`: `trigger_at_budget_pct` (百分比)
- `RoleContextCompressor`: `token_threshold` (绝对值)

#### P1-14: 压缩正确性使用 MagicMock 测试

**位置**: `test_intelligent_compressor.py:126`

---

### P2 — Medium (20个)

#### P2-1: 已废弃的 `compress()` 方法应删除

**位置**: `models.py:857`

#### P2-2: `RoleContextCompressor` 文档声称3层实际只有2层

**位置**: `compaction.py:302`

#### P2-3: Episode digest 命名误导 (`digest_64` 非 hash)

**位置**: `runtime.py:1259-1261`

#### P2-4: `output_reserve` 未实现 Claude Code 的 min(max_expected_output) 逻辑

**位置**: `runtime.py:1020-1023`

#### P2-5: `_estimate_tokens` 在 `helpers.py` 和 `generic.py` 中重复

**位置**: `helpers.py:30`, `generic.py:60`

#### P2-6: 缺少 token 预算精度测试

#### P2-7: 硬编码 `128_000` 散布多处

#### P2-8: `cache_control_applied` 未包含在 receipt 中

**位置**: `chunks/assembler.py:435`

#### P2-9: `CodeContextDomainAdapter.classify_assistant_followup()` 未测试

#### P2-10: 测试数据过于简化

缺少真实代码片段、多语言混合等。

#### P2-11: 缺少端到端 LLM 调用链测试

#### P2-12: 异步边界条件测试缺失

#### P2-13: `adapter_id` 使用 class 属性而非实例属性

#### P2-14: Episode recency 计算对 reopened episodes 可能错误

**位置**: `runtime.py:1312`

#### P2-15: Digest tiers 72/28 split 在短文本时尾部丢失

**位置**: `runtime.py:1259`

#### P2-16: `_stub_receipt_content` 中 result 字段可能丢失

**位置**: `history_materialization.py:148-157`

#### P2-17: Continuity summary 注入格式可能混淆 LLM

**位置**: `session_continuity.py:396-403`

#### P2-18: `run_card` 的 `latest_user_intent` 未用于提示词

#### P2-19: IntelligentCompressor 未集成到生产路径

#### P2-20: `session_continuity.py` 中 rebuild 逻辑冗余

---

### P3 — Low (10个)

#### P3-1: CompactionStrategy 无生产调用路径

#### P3-2: 测试中 Mock 可能隐藏真实 bug

#### P3-3: `classify_event` 中 confidence 值可能过高

#### P3-4: `should_seal_episode` 空事件处理

#### P3-5: 搜索结果缺少多样性控制

#### P3-6: `read_artifact` span 参数边界问题

#### P3-7: Search 命名误导 (semantic 实为字符串匹配)

#### P3-8: SessionContinuityEngine 无 domain 处理

#### P3-9: 只有 generic 和 code 两个领域适配器

#### P3-10: Artifact recency 计算 O(n²) 性能问题

---

## 三、架构对比矩阵

| 维度 | Claude Code | Polaris ContextOS | 差距 |
|------|-------------|---------------------|------|
| **上下文管线** | 每次LLM调用前执行完整管线 | ❌ 仅在session层调用一次 | **极高** |
| **Token估算** | 统一单层 | ❌ 8处独立实现 | **极高** |
| **活跃窗口** | 语义相关性驱动 | ❌ 45%硬编码 + recency | **高** |
| **四车道路由** | 结果用于压缩决策 | ❌ 计算后丢弃 | **高** |
| **产物卸载** | 外部存储 + restore路径 | ❌ 完整内容嵌入prompt | **高** |
| **语义搜索** | 向量嵌入 + BM25 | ❌ 纯规则/pattern | **高** |
| **Budget模型** | Claude Code公式 (0.18/0.10/0.05) | ⚠️ 部分实现，safety_margin混乱 | **中** |
| **Episode记忆** | 密封 + 多层摘要 + 召回 | ✅ 设计对齐，部分实现 | **低** |
| **注意力追踪** | 每次投影完整追踪 | ⚠️ feature flag控制 | **低** |
| **压缩层级** | 4-tier (micro/auto/compact/emergency) | ⚠️ 3-tier，auto_compact不存在 | **中** |

---

## 四、追赶 Claude Code 详细蓝图

### Phase 0: 止血 (Week 1) — 消除 P0 级崩溃风险

| 任务 | 负责人 | 文件 | 操作 |
|------|--------|------|------|
| T0-1 | Expert 1 | `budget_gate.py:319` | 删除 `DEFAULT_FALLBACK_WINDOW` 从 `__all__` |
| T0-2 | Expert 3 | `history_materialization.py:160` | 删除不可达 `return True` |
| T0-3 | Expert 6 | `context_gateway.py:195` | 删除死代码 `_continuity_chunk_assembler` 或集成 |
| T0-4 | Expert 1 | `budget_gate.py` | 搜索并修复所有 `DEFAULT_FALLBACK_WINDOW` 引用 |
| T0-5 | Expert 9 | `runtime.py:478-554` | 添加 `read_artifact/read_episode/get_state` 测试 |
| T0-6 | Expert 7 | `domain_adapters/contracts.py` | Protocol 添加 `classify_assistant_followup` |

### Phase 1: 统一 Token 估算 (Week 1-2) — 消除 P0-1, P2-5

```
创建: polaris/kernelone/context/_token_estimator.py
  └── _estimate_tokens(text: str) -> int
        └── CJK-aware: ascii/4 + cjk*1.5
        └── 唯一事实来源

删除: helpers.py:30 (保留导入)
删除: generic.py:60 (改从 _token_estimator 导入)
删除: history_materialization.py:589 独立实现
删除: context_assembler.py:867 独立实现
更新: context_gateway.py:777 → 导入统一估算器
更新: intelligent_compressor.py:460 → 导入统一估算器
更新: chunks/assembler.py:516 → 导入统一估算器
更新: working_memory.py:402 → 导入统一估算器
```

### Phase 2: 核心管线集成 (Week 2-3) — 消除 P0-3~8, P1-10

**这是最关键的架构重构。目标：让 `StateFirstContextOS.project()` 成为唯一的上下文构建引擎。**

#### 2.1 统一上下文构建入口

```
RoleContextGateway.build_context()
  ↓
1. StateFirstContextOS.project() ← 新增: 在 gateway 内调用
  ↓
2. 使用 projection.active_window 替代 _expand_transcript_to_messages() ← 新增
  ↓
3. 使用 projection.head_anchor / tail_anchor 构建锚点 ← 新增
  ↓
4. 使用 projection.run_card 构建状态卡 ← 新增
  ↓
5. 使用 projection.context_slice_plan 指导消息优先级 ← 新增
  ↓
6. 基于 event.route 做差异化压缩 ← 新增
  ↓
7. BudgetPlan 验证错误 → 触发紧急压缩或抛异常 ← 新增
```

#### 2.2 StateFirstContextOS 实例化

在 `RoleContextGateway.__init__` 中:
```python
self._context_os = StateFirstContextOS(
    domain_adapter=get_context_domain_adapter(profile.domain),
    provider_id=profile.provider_id,
    model=profile.model,
    workspace=self.workspace,
)
```

#### 2.3 project() 调用

```python
if has_snapshot:
    projection = self._context_os.project(
        messages=request.history,  # 当前 turn 事件
        existing_snapshot=snapshot,
        recent_window_messages=self.policy.max_history_turns,
        focus=request.focus or working_state.task_state.current_goal.value,
    )
    # 使用 projection 而非简单展开
    messages.extend(self._messages_from_projection(projection))
else:
    projection = self._context_os.project(
        messages=request.history,
        existing_snapshot=None,
    )
    messages.extend(self._messages_from_projection(projection))
```

### Phase 3: 语义增强 (Week 3-4) — 消除 P1-8, P2-4

| 任务 | 描述 |
|------|------|
| T3-1 | 移除 45% 硬编码，改为基于 `context_slice_plan.budget_tokens` 的自适应计算 |
| T3-2 | 实现 `output_reserve = min(max_expected_output, 0.18C)` |
| T3-3 | 统一 `safety_margin` 命名和公式 |
| T3-4 | 集成 `Akashic.HybridMemory` 到 ContextOS 搜索路径 |

### Phase 4: 产物卸载 (Week 4-5) — 消除 P0-9

```
现状: artifact_stubs 完整内容嵌入 prompt
目标: prompt 只含 artifact_id + restore_tool

实现:
1. ArtifactStore 实现外部持久化 (使用现有 filesystem adapter)
2. artifact_stub 只含: artifact_id, artifact_type, digest, restore_tool
3. 添加 RestoreContextTool 在需要时按需恢复
4. 控制 restore 频率避免过多往返
```

### Phase 5: 语义搜索升级 (Week 5-6) — 消除 P3-7

```
现状: 规则匹配 + pattern
目标: 向量嵌入 + BM25 混合检索

实现路径:
1. 复用 Akashic.HybridMemory (已实现 VectorStoreBackend)
2. 在 episode sealing 时同时生成 embedding
3. 在 search_memory 时使用 hybrid retrieval
4. 评估 ChromaDB / LanceDB / Qdrant 集成
```

### Phase 6: 压缩层级完善 (Week 6-7) — 消除 P1-6, P1-7, P2-2

```
现状: micro → LLM (2层, auto_compact不存在)
目标: micro → auto → compact → emergency (4层 Claude Code对齐)

实现:
1. 实现真正的 auto_compact 层 (基于 token 预算的 deterministic truncate)
2. LLM 摘要添加 fallback: 当 LLM 不可用时使用 deterministic 摘要
3. 删除废弃的 compress() 方法
4. 统一 CompactionStrategy 和 RoleContextCompressor 触发机制
```

### Phase 7: 可观测性增强 (Week 7-8) — 消除 P1-12

```
目标: 让 ContextOS 成为可观测、可调试的生产级系统

实现:
1. 将 emit_debug_event 设为默认启用 (移除 feature flag)
2. 添加 attention trace 可视化 dashboard
3. 实现 budget overrun 告警
4. 添加 compression quality 评估
5. 端到端测试覆盖
```

---

## 五、验证计划

| Phase | 验证指标 | 测试要求 |
|-------|---------|---------|
| Phase 0 | 无 P0 崩溃风险 | 10 个新增测试 |
| Phase 1 | Token 估算一致性 <5% 误差 | 100 个 token 估算测试 |
| Phase 2 | Benchmark 通过率 +15% | 73 个 agentic benchmark cases |
| Phase 3 | Budget 模型对齐 Claude Code | 8 个 budget 边界测试 |
| Phase 4 | 大产物不超出 prompt 限制 | 5 个产物卸载集成测试 |
| Phase 5 | 语义搜索 MRR +20% | 20 个检索质量测试 |
| Phase 6 | 4-tier 压缩正确执行 | 15 个压缩层级测试 |
| Phase 7 | 生产 trace 可用 | 30 个 e2e 测试 |

---

## 六、风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Phase 2 重构破坏现有功能 | 高 | 先添加新路径，验证后再删除旧路径 (Strangler Fig Pattern) |
| Token 估算改动影响预算计算 | 高 | 逐文件迁移，每次修改后运行 budget 相关测试 |
| 产物卸载增加延迟 | 中 | 控制 restore 频率，实现 LRU 缓存 |
| 语义搜索引入外部依赖 | 低 | 优先使用已有 Akashic 实现 |

---

## 七、里程碑

| 日期 | 里程碑 |
|------|---------|
| 2026-04-18 | Phase 0 + Phase 1 完成 — Token 估算统一，P0 崩溃风险消除 |
| 2026-04-25 | Phase 2 完成 — ContextOS 管线集成，benchmark +15% |
| 2026-05-02 | Phase 3 + Phase 4 完成 — Budget 对齐 Claude Code，产物卸载 |
| 2026-05-16 | Phase 5 + Phase 6 完成 — 语义搜索升级，4-tier 压缩 |
| 2026-05-23 | Phase 7 完成 — 可观测性增强，全量测试通过 |
| 2026-05-30 | **目标达成**: ContextOS 对齐 Claude Code，具备下一代 Agent 上下文能力 |

---

## 八、附录：专家团队审计摘要

| 专家 | 领域 | 发现数量 | 关键问题 |
|------|------|---------|---------|
| Expert 1 | Token Budget System | 10个 P0-P3 | 8套独立估算，DEFAULT_FALLBACK_WINDOW=0 |
| Expert 2 | Compression Pipeline | 11个 P0-P2 | 死代码，micro-compact边效应，auto_compact不存在 |
| Expert 3 | StateFirstContextOS Core | 10个 P0-P2 | **管线脱离关键路径**，投影数据丢失 |
| Expert 4 | RoleContextGateway Integration | 8个 P0-P1 | 未调用project()，token公式不一致，Phase 5未真正修复 |
| Expert 5 | Session Continuity | 6个 P0-P3 | 死代码，recency计算错误，continuity summary混淆LLM |
| Expert 6 | PromptChunkAssembler | 6个 P0-P2 | 死assembler，CONTINUITY缓存策略错误，双压缩系统 |
| Expert 7 | Domain Adapters | 6个 P0-P5 | Protocol不完整，build_pack domain未传递 |
| Expert 8 | Memory Search | 6个 P0-P5 | O(n²)性能，semantic命名误导，未集成Akashic |
| Expert 9 | Test Coverage | 10个 P0-P3 | 3个P0方法未测试，mock隐藏bug |
| Expert 10 | Claude Code对比 | 6个 P0-P5 | **双系统并行根因**，45%硬编码，非真正卸载 |

**总计**: 58 个问题，10 位专家，平均每人 5.8 个发现。
