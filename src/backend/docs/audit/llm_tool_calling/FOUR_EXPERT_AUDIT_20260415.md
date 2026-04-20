# LLM 工具调用系统四专家综合审计报告

**审计日期**: 2026-04-15
**审计范围**: Polaris 全链路 LLM 工具调用系统
**基准**: MASTER_AUDIT_REPORT_20260328 + TOP6_CRITICAL_FIXES_20260401
**审计团队**: 核心引擎专家 / 上下文管理专家 / 安全可靠性专家 / 架构治理专家

---

## 一、总体评价

Polaris 的 LLM 工具调用系统经历了 2026-03-28 审计和 04-01 TOP6 修复后，**核心能力已显著增强**，但在架构治理收敛、跨 Cell 依赖合规、以及部分安全硬控制方面仍有明显缺口。

| 维度 | 评分 | 评价 |
|------|------|------|
| 核心引擎健壮性 | ★★★★☆ | TurnEngine 多层防线完备，Stream/Non-Stream 已对齐，流式路径异常处理有缺口 |
| 上下文管理 | ★★★☆☆ | 三层压缩架构成熟，但配对完整性、结果截断存在缺陷 |
| 安全可靠性 | ★★★★☆ | 沙箱+代码验证+审计链+弹性策略优秀，写工具重试策略有隐患 |
| 架构治理 | ★★☆☆☆ | Phase 1-2 已完成，Phase 3-5 停滞，P0 级跨 Cell 违规未修复，CI 门禁不可运行 |

---

## 二、P0 级发现（必须修复）

### P0-1: infrastructure 层反向依赖 Cell internal（治理）

**文件**:
- `polaris/infrastructure/llm/providers/openai_compat_provider.py:10`
- `polaris/infrastructure/llm/providers/kimi_provider.py:10`

**问题**: Provider 层（infrastructure）直接导入 `polaris.cells.roles.kernel.internal.context_gateway`。底层基础设施不应了解上层 Cell 的内部实现。

**影响**: 任何对 `roles.kernel.internal` 的重构将破坏 Provider 层。

### P0-2: 只读角色工具白名单仅在提示词层面约束（安全+上下文）

**文件**: `polaris/cells/llm/tool_runtime/internal/role_integrations.py`

**问题**: PM/Architect/QA/Scout 角色创建了完整的 `create_default_registry()`（包含写工具和执行命令工具），工具的只读约束**仅通过 system prompt 文字说明执行**，运行时 `AgentAccelToolExecutor.execute()` 不做白名单检查。

**影响**: LLM 无视提示词约束时，可执行写操作。

### P0-3: CI 收敛门禁路径错误，无法运行（治理）

**文件**: `docs/governance/ci/llm-tool-convergence-gate.yaml`

**问题**: 多处路径引用错误（第 48/58-59/168 行），引用已删除的 `STANDARD_TOOLS` 常量和错误路径。

**影响**: 收敛进度无自动验证。

---

## 三、P1 级发现（应该修复）

### P1-1: 写工具 no_match 可重试，可能导致错误模糊匹配（安全）

**文件**: `polaris/kernelone/tool_execution/failure_budget.py:229`

**问题**: `_is_retryable_error_type` 将 `no_match` 分类为可重试。对写工具（edit_file, search_replace），重复 no_match 意味着 LLM 在未验证内容的情况下反复尝试修改。

### P1-2: executor/core.py 双依赖工具定义（治理）

**文件**: `polaris/kernelone/llm/toolkit/executor/core.py:16,218,427`

**问题**: 同时导入已废弃的 `definitions.py`（ToolDefinition）和 `contracts.py`（_TOOL_SPECS），且构造 ToolDefinition 时参数不完整（缺少 enum/items/properties）。

### P1-3: cells.yaml 与 cell.yaml 声明不一致（治理）

**关键差异**:
- `context.catalog` 依赖声明矛盾（cells.yaml 声明依赖 `roles.runtime`，cell.yaml 声明 `depends_on: []`）
- `context.engine` current_modules 不匹配
- `roles.kernel` depends_on 列表差异大

### P1-4: auto_compact() 可能破坏工具调用/结果配对（上下文）

**文件**: `polaris/kernelone/context/compaction.py:701-709`

**问题**: 中间删除消息时未检查是否破坏 assistant tool_use + tool_result 配对完整性。

### P1-5: 流式路径工具执行缺少 try/except（引擎）

**文件**: `polaris/cells/roles/kernel/internal/turn_engine/engine.py:1832`

**问题**: `run_stream()` 中 `_execute_single_tool()` 调用无 try/except 包裹，而非流式路径（第 1231 行）有完整异常处理。

### P1-6: core_roles.yaml 未删除（治理）

**文件**: `polaris/cells/roles/profile/internal/config/core_roles.yaml`

**问题**: ADR-0065 Phase 4 要求删除此文件，但仍然存在，导致角色工具白名单 YAML+Python 双源。

### P1-7: RoleToolGateway + PolicyLayer 双路径并存（治理）

**文件**: `tool_gateway.py:39` + `policy/layer.py`

**问题**: 工具权限评估存在两个独立路径，职责重叠。

---

## 四、P2 级发现（建议修复）

| ID | 维度 | 描述 | 文件 |
|----|------|------|------|
| P2-1 | 上下文 | 缺少统一工具结果截断策略 | `provider_formatter.py` |
| P2-2 | 上下文 | micro compact 占位符仅保留工具名 | `compaction.py:444` |
| P2-3 | 上下文 | PM/Architect/QA/Scout 提示词模板使用旧工具别名 | `role_integrations.py` |
| P2-4 | 安全 | append_to_file 跳过写前语法验证 | `filesystem.py:817-887` |
| P2-5 | 安全 | edit_file 行范围模式跳过语法验证 | `filesystem.py:689-733` |
| P2-6 | 安全 | HMAC 签名不覆盖事件数据载荷 | `audit/runtime.py:370-371` |
| P2-7 | 安全 | JS/TS/Go/Rust 仅括号匹配无 AST 验证 | `code_validator.py:805-945` |
| P2-8 | 引擎 | max_turns 和 max_total_tool_calls 共用环境变量 | `turn_engine/config.py:104-105` |
| P2-9 | 引擎 | ToolHandlerRegistry 类变量全局状态 | `handlers/registry.py:47` |
| P2-10 | 治理 | 3 个 treesitter 工具仅有规格无实现 | `contracts.py:37-40` |
| P2-11 | 治理 | definitions.py 废弃但仍被 2 个文件引用 | `role_integrations.py:17`, `tool_helpers.py:52` |
| P2-12 | 治理 | context.pack.json 双重所有权声明 | `context.catalog` + `context.engine` |

---

## 五、已有优势（值得保持）

1. **TurnEngine 多层防线**: Quota → ConversationState → PolicyLayer → ToolLoopController → max_turns 硬限制
2. **渐进式 Circuit Breaker**: 语义等价归一化 + 信息增益追踪 + 三级升级
3. **三层幻觉防御**: 第三方工具修复 → 正则幻觉模式 → AST/括号验证
4. **HMAC-SHA256 + SHA-256 双链审计**: 签名链 + 哈希链双重完整性保护
5. **熔断器 + 多 Provider 故障转移**: 完整的弹性策略栈
6. **强制读-编辑机制**: 防止 LLM 在过时内容上执行编辑
7. **幻觉循环检测**: 连续搜索失败自动触发 read_file 验证
8. **文本协议工具调用已全面禁用**: 消除了整类安全风险

---

## 六、2026-03-28 审计问题修复追踪

| 原问题 | 状态 | 备注 |
|--------|------|------|
| A-1 三处工具定义 | **部分修复** | contracts.py 已成 SSOT，definitions.py 废弃但仍有引用 |
| A-2 YAML/Python 角色配置 | **未修复** | core_roles.yaml 仍存在 |
| B-1 4层解析器 | **已修复** | 收敛至 3 层 + CanonicalToolCallParser |
| B-3 Argument Key 别名 | **已修复** | CANONICAL_ARGUMENT_KEYS 统一 |
| B-4 HMAC 签名不对称 | **已修复** | prompt_based.py 已删除 |
| D-1 Gateway+Policy 双路径 | **未修复** | 仍并存 |
| E-2 执行器双依赖 | **未修复** | executor/core.py 仍双导入 |
| G-1/G-2 废弃文件 | **已修复** | 已删除 |

**修复率**: 24 个问题中 10 个已修复，6 个部分修复，8 个未修复。

---

## 七、下一步完善建议（按优先级）

### Phase 1: 安全硬控制加固（1-2 周）

| 任务 | 工作量 | 影响 |
|------|--------|------|
| P0-2: 为只读角色注入运行时白名单拦截 | 2d | 消除工具越权风险 |
| P1-1: 写工具 no_match 标记为不可重试 | 0.5d | 消除错误模糊匹配风险 |
| P1-5: run_stream() 工具执行添加 try/except | 0.5d | 流式路径异常对齐 |

### Phase 2: 架构治理收敛续推（2-4 周）

| 任务 | 工作量 | 影响 |
|------|--------|------|
| P0-1: 抽取 ContextGateway 公共契约，消除反向依赖 | 3d | 解除 infrastructure↔Cell 耦合 |
| P0-3: 修复 CI 门禁路径，恢复自动验证 | 1d | 收敛可度量 |
| P1-2: executor/core.py 清除 definitions.py 依赖 | 2d | 完成工具定义收敛 |
| P1-6: 删除 core_roles.yaml，统一到 Python | 2d | 消除角色配置双源 |
| P1-7: 合并 RoleToolGateway + PolicyLayer | 3d | 消除权限评估双路径 |

### Phase 3: 上下文管理优化（3-4 周）

| 任务 | 工作量 | 影响 |
|------|--------|------|
| P1-4: auto_compact() 保护工具调用配对完整性 | 2d | 防止压缩破坏对话一致性 |
| P2-1: 添加工具结果截断策略（max 10000 chars） | 1d | 防止 token 爆炸 |
| P2-2: 增强 micro compact 占位符（含参数摘要） | 1d | 提升 LLM 历史理解 |
| P2-3: 统一提示词模板中的工具名称 | 1d | 减少别名依赖 |

### Phase 4: 代码卫生与治理对齐（4-6 周）

| 任务 | 工作量 | 影响 |
|------|--------|------|
| P1-3: 同步 cells.yaml 与 cell.yaml 声明 | 3d | 治理文档可信 |
| P2-4/5: append_to_file/edit_file 添加语法验证 | 1d | 写入安全加固 |
| P2-10: 清理 treesitter 死定义或实现 handler | 2d | 减少维护负担 |
| P2-11: 清除 definitions.py 最后引用 | 1d | 完成定义收敛 |
| P2-12: 解决 context.pack.json 双重所有权 | 1d | 单一所有权 |

---

## 八、关键文件索引

| 分类 | 文件 | 职责 |
|------|------|------|
| **SSOT** | `polaris/kernelone/tool_execution/contracts.py` | 工具定义 `_TOOL_SPECS` |
| **SSOT** | `polaris/kernelone/tool_execution/tool_spec_registry.py` | 工具注册中心 |
| **引擎** | `polaris/cells/roles/kernel/internal/turn_engine/engine.py` | TurnEngine 主循环 |
| **引擎** | `polaris/cells/roles/kernel/internal/tool_loop_controller.py` | 循环控制 + Circuit Breaker |
| **执行** | `polaris/kernelone/llm/toolkit/executor/core.py` | AgentAccelToolExecutor |
| **执行** | `polaris/kernelone/llm/toolkit/executor/runtime.py` | KernelToolCallingRuntime |
| **上下文** | `polaris/kernelone/context/compaction.py` | 三层压缩 |
| **上下文** | `polaris/cells/roles/kernel/internal/context_gateway.py` | 上下文网关 |
| **安全** | `polaris/kernelone/tool_execution/code_validator.py` | 幻觉防御 |
| **安全** | `polaris/kernelone/tool_execution/failure_budget.py` | 失败预算 |
| **安全** | `polaris/kernelone/audit/runtime.py` | HMAC 审计链 |
| **集成** | `polaris/cells/llm/tool_runtime/internal/role_integrations.py` | 6 角色工具集成 |
| **治理** | `docs/graph/catalog/cells.yaml` | Cell 目录 |
| **治理** | `docs/governance/ci/llm-tool-convergence-gate.yaml` | CI 收敛门禁 |
| **废弃** | `polaris/kernelone/llm/toolkit/definitions.py` | 已废弃，仍有 2 处引用 |
| **待删** | `polaris/cells/roles/profile/internal/config/core_roles.yaml` | Phase 4 要求删除 |
