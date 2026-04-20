# KernelOne Domain-Aware Runtime 实施主计划

状态: In Progress  
日期: 2026-03-25  
范围: `polaris/cells/roles/runtime/`、`polaris/cells/roles/profile/`、`polaris/kernelone/context/`、`polaris/cells/roles/kernel/`、`polaris/kernelone/context/chunks/`

> 本文是实施计划，不是 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 为准。

---

## 1. 目标结果（DoD）

1. 用户可显式指定所属领域（写代码/写作/研究/其他）。
2. Runtime 能稳定把 domain 传递到策略解析、overlay 选择、turn 请求与调试回执。
3. Prompt/Context 能按 domain 使用不同能力组合，而不是单一路径硬编码。
4. 无 domain 输入时保持向后兼容，不影响现有链路。

---

## 2. 当前基线（2026-03-25）

### 2.1 已落地

1. `roles.runtime` 公共命令已增加 `domain` 字段。
2. `RoleTurnRequest` 已携带 `domain`（默认 `code`）。
3. `RoleRuntimeService` 已实现 domain 规范化、解析优先级、策略域映射、run context 注入。
4. `RoleOverlayRegistry` 已支持 domain-aware overlay 选择。
5. 新增并通过 domain 关键测试（runtime strategy 8 项）。

### 2.2 待收口

1. PromptChunkAssembler 尚未按 domain 接入 `roles.kernel` prompt builder 主路径。
2. RepoIntelligenceFacade 尚未按 domain 挂入 WorkingSetAssembler 主路径。
3. ReasoningStripper 尚未作为 domain-agnostic 强制前置钩子完成全链路收口。
4. FinalRequestReceipt 尚未与 domain 字段形成统一调试面板输出。

---

## 3. 分阶段落地路线

### Phase 1A（已开始）：Domain 基础链路固化

目标：

1. 契约、运行时、策略、overlay 形成闭环。
2. 确保默认行为不回归。

交付：

1. contracts/profile/runtime/overlay 完整 domain 路由代码。
2. runtime strategy domain 测试通过。

退出门禁：

1. `pytest polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py -q`
2. `pytest polaris/cells/roles/profile/tests/test_schema.py -q`

### Phase 1B：Prompt Chunk Domain 接入

目标：

1. `PromptChunkAssembler` 成为 prompt 组装主入口之一。
2. `document/research` 与 `code` 采用不同 chunk 组合权重。

实施点：

1. `polaris/cells/roles/kernel/internal/prompt_builder.py`
2. `polaris/kernelone/context/chunks/assembler.py`
3. `polaris/kernelone/context/chunks/taxonomy.py`

验收：

1. domain=code 时保留 repo-intel/readonly chunk 优先。
2. domain=document 时提升 outline/draft continuity chunk 权重。
3. `FinalRequestReceipt` 输出 domain + chunk eviction 明细。

### Phase 1C：Working Set Domain 接入

目标：

1. `RepoIntelligenceFacade` 只在 code/research 路径重度启用。
2. document/general 路径避免无效 repo map 开销。

实施点：

1. `polaris/kernelone/context/working_set.py`
2. `polaris/kernelone/context/repo_intelligence/facade.py`

验收：

1. domain=document 时 working set 不强依赖 symbol rank。
2. domain=code 时继续保留 ranker + lines-of-interest 路径。

### Phase 1D：Reasoning 安全链路收口

目标：

1. `ReasoningStripper` 统一前置到 history materialization。
2. 与 domain 无关，默认全域启用。

实施点：

1. `polaris/kernelone/context/history_materialization.py`
2. `polaris/cells/roles/kernel/internal/context_gateway.py`

验收：

1. 任意 domain 下，history 入模前都完成剥离。
2. 不出现 reasoning 注入回流到模型上下文。

---

## 4. 实施任务清单（可执行）

1. Runtime/Kernel 对接任务
   - 将 domain 写入 debug/receipt 标准字段。
   - 在 prompt builder 中按 domain 分派 chunk 组装策略。
2. Context 对接任务
   - 在 working set 入口加 domain-aware capability switch。
   - 为 document/research 增补最小可用 selector。
3. 安全与治理任务
   - 在 history materialization 统一调用 ReasoningStripper。
   - 补齐 domain 路径的回归测试与残余风险说明。

---

## 5. 质量门禁与验证

### 5.1 目标测试

1. `pytest polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py -q`
2. `pytest polaris/cells/roles/profile/tests/test_schema.py -q`
3. `pytest polaris/kernelone/context/tests/ -q`
4. `pytest polaris/kernelone/llm/reasoning/tests/ -q`
5. `pytest polaris/kernelone/context/tests/test_chunks.py -q`

### 5.2 验收信号

1. Domain 字段在 runtime debug payload 可见。
2. domain=document 与 domain=code 至少有一项策略行为差异可观测。
3. domain 缺省路径与历史行为一致。

---

## 6. 风险与防御

1. 风险：domain 配置漂移导致策略不一致。  
   防御：统一入口 `_resolve_execution_domain`，禁止多处各自解析。

2. 风险：PromptChunk 改造引发 token 预算波动。  
   防御：所有 domain 路径统一走 chunk budget 统计并产出 receipt。

3. 风险：引入文档域后 code 路径性能退化。  
   防御：code domain 保持原高优先能力，document 路径采用减负策略。

4. 风险：安全链路在某 domain 漏钩子。  
   防御：ReasoningStripper 设计为 domain-agnostic 强制前置。

---

## 7. 回滚与降级策略

1. 保留 `domain` 字段但可关闭 domain-first 解析，回退 role-first。
2. Prompt chunk domain 分派异常时回退到 `canonical_balanced` 组装。
3. Repo intelligence domain 分派异常时回退到现有 working set 路径。

---

## 8. 下一步执行顺序（建议）

1. 先做 Phase 1B（PromptChunk domain 接入），因为它直接影响可观测行为。
2. 再做 Phase 1C（WorkingSet domain 接入），控制性能与准确性差异。
3. 最后做 Phase 1D（Reasoning 全链收口），完成安全闭环。

