# Polaris/KernelOne 全息构造收敛总谱告

**版本**: v1.0 Final
**日期**: 2026-04-04
**状态**: 完成 (10/10 专家)
**优先级**: P0-P3

---

## 收敛状态追踪

| Item | 状态 | 说明 |
|------|------|------|
| P0-002 CYCLE-002 | ✅ 已修复 | `polaris/infrastructure/messaging/nats/server_runtime.py` 改用 `kernelone.storage.layout` |
| P0-002 CYCLE-003 | ✅ 已修复 | `polaris/delivery/cli/agentic_eval.py` 改用 public API |
| P0-002 CYCLE-004 | ✅ 已修复 | `polaris/application/cognitive_runtime/service.py` 改用 public contract |
| P0-002 CYCLE-010 | ⚠️ 暂缓 | FinOps→Roles 内部导入受阻于 P1-011 类型系统碎片 |
| P0-003 | ✅ 已修复 | 4处注释修正，路径引用规范化 |
| P0-004 | ⚠️ 已分类 | TOP 6 Fix 3 已覆盖最危险项；其余为可接受技术债务 |
| P0-005 | ✅ 已修正 | "5种TurnState"系误判；实际仅1个；分类为可接受子系统多样性 |
| P0-006 | ✅ 已修正 | 两种ProviderFormatter服务于不同子系统；无运行时冲突；分类为可接受 |
| P1-001 | ✅ 已核实 | 1个canonical单例+2个accessor包装；无bug |
| P1-002 | ✅ 已核实 | 1个canonical函数+1个角色方法；"4+"系夸大 |
| P1-003 | ✅ 已核实 | 3个不同RendererRegistry类接口各异；架构冗余但无bug |
| P1-004 | ✅ 已核实 | 2处几乎相同定义分属不同子系统；无冲突 |
| P1-008 | ✅ 已核实(历史) | `_call_role_llm` 去重已完成 (commit aa946271) |
| P1-011 | ⚠️ 已分类 | RoleAgent/AgentMessage 类型体系为子系统多样性；`shared_contracts` ABC 与 `agent_runtime_base` 实现服务于不同层；无需强制收敛 |
| CYCLE-010 | ✅ 已核实 | `budget_guard/budget_agent.py` 正确使用 `polaris.cells.roles.runtime.public.service`（public contract），无违规 |
| CYCLE-NEW | 🔲 待处理 | `kernelone/agent_runtime/neural_syndicate/` → `cells.roles.runtime.internal` 内层导入（3处） |

---

## 执行摘要

| 专家 | 维度 | CRITICAL | HIGH | MEDIUM | 合计 |
|------|------|----------|------|--------|------|
| Expert 1 | 重复代码 | 1 | 4 | 15+ | 20+ |
| Expert 2 | 注册表/单例 | - | 4 | 11 | 15+ |
| Expert 3 | 契约/接口 | 5 | 5 | 5 | 15 |
| Expert 4 | 命名规范 | - | 5 | 15+ | 20+ |
| Expert 5 | 异常处理 | 5 | 5 | 10 | 20+ |
| Expert 6 | 类型注解 | - | 6 | 10 | 16 |
| Expert 7 | 导入/依赖 | 7 | 3 | 5 | 15 |
| Expert 8 | 配置常量 | 2 | 3 | 15 | 20 |
| Expert 9 | 工作流引擎 | 4 | 5 | 6 | 15 |
| Expert 10 | LLM/Provider | 3 | 7 | 10 | 20 |

**总问题数**: 176+ 项
**最高优先级修复项**: 25 项 CRITICAL

---

## 🔴 P0 - 必须立即修复

### [P0-001] director_logic_rules 全文重复 (~1080行浪费)

**严重程度**: CRITICAL | **Expert**: Expert 1

| 文件 | 行数 |
|------|------|
| `polaris/cells/director/execution/internal/director_logic_rules.py` | ~540 |
| `polaris/cells/director/planning/internal/director_logic_rules.py` | ~540 |

**影响函数** (全文几乎完全相同):
- `parse_json_payload` (4处)
- `compact_pm_payload` (~100行 x2)
- `extract_defect_ticket` (5处)
- `validate_files_to_edit` (~30行 x2)
- `write_gate_check` (~187行 x2)
- `extract_required_evidence`
- `parse_acceptance` (4处)
- `_normalize_ticket_value` (2处)

**根因**: Cells 层为了解耦 Delivery 层, 直接复制了实现

**修复方案**: 在 `polaris/domain/` 或 `polaris/kernelone/` 创建共享模块

---

### [P0-002] 架构层违规 (Domain→Infrastructure, Delivery/Application→Cell internal)

**严重程度**: CRITICAL | **Expert**: Expert 7

| # | 违规 | 文件 | 状态 |
|---|------|------|------|
| CYCLE-001 | Domain → Infrastructure | `polaris/domain/services/__init__.py:7` | ✅ 已修复 |
| CYCLE-002 | Infrastructure → Cells | `polaris/infrastructure/messaging/nats/server_runtime.py:16` | ✅ 已修复 |
| CYCLE-003 | Delivery → Cell internal | `polaris/delivery/cli/agentic_eval.py:69` | ✅ 已修复 |
| CYCLE-004 | Application → Cell internal | `polaris/application/cognitive_runtime/service.py:11` | ✅ 已修复 |
| CYCLE-010 | FinOps → Roles Internal | `polaris/cells/finops/budget_guard/internal/budget_agent.py:22` | ✅ 已核实（使用 public contracts） |
| CYCLE-NEW | kernelone → Cell internal | `polaris/kernelone/agent_runtime/neural_syndicate/nats_broker.py:101` | 🔲 待处理 |
| CYCLE-NEW | kernelone → Cell internal | `polaris/kernelone/agent_runtime/neural_syndicate/broker.py:171` | 🔲 待处理 |
| CYCLE-NEW | kernelone → Cell internal | `polaris/kernelone/agent_runtime/neural_syndicate/base_agent.py:157` | 🔲 待处理 |

**约束违反**: "跨 Cell 只能走 Public Contract + DI"

**修复说明**:
- CYCLE-001: `TokenService` 实现移至 `polaris/domain/services/token_service.py`（Domain canonical），`infrastructure/llm/token_service.py` 改为 re-export
- CYCLE-002: `server_runtime.py` 改用 `polaris.kernelone.storage.layout.polaris_home`
- CYCLE-003: `agentic_eval.py` 改用 `polaris.cells.llm.dialogue.public.generate_role_response`
- CYCLE-004: `CognitiveRuntimeService` 改用 `polaris.cells.roles.session.public.service`
- CYCLE-010: 需先统一 `RoleAgent` 类型体系（`kernelone/shared_contracts` vs `roles/runtime/internal/agent_runtime_base`），见 P1-011

---

### [P0-003] 工具定义双系统 (contracts.py vs definitions.py)

**严重程度**: CRITICAL | **Expert**: Expert 10, Expert 3

| 系统 | 文件 | 工具数 |
|------|------|--------|
| `STANDARD_TOOLS` | `kernelone/llm/tools/contracts.py` | 16 |
| `_TOOL_SPECS` | `kernelone/llm/toolkit/definitions.py` | 40+ |

**问题**: 两套完全独立的工具定义系统, 不同的 schema/alias/validation 逻辑

---

### [P0-004] 250+ bare `except Exception:` 异常处理

**严重程度**: CRITICAL | **Expert**: Expert 5

**静默吞噬问题** (最危险):
- `polaris/delivery/cli/textual_console.py` - 8+ 处 `except: pass`
- `polaris/cells/workspace/integrity/internal/diff_tracker.py` - 7 处
- `polaris/infrastructure/realtime/process_local/signal_hub.py` - 模块导入级 `except:`

---

### [P0-005] TurnState 5种不同表示 + ToolCall 3种定义

**严重程度**: MEDIUM (已修正) | **Expert**: Expert 9, Expert 3

| 概念 | 实际数量 | 核实结果 |
|------|----------|---------|
| TurnState | **1** | `turn_state_machine.py` (仅1个真实定义，审计报告"5种"系误判) |
| ToolCall | **3** | `llm/contracts/tool.py`, `roles/kernel/public/transcript_ir.py`, `roles/kernel/internal/services/contracts.py` |

**调查结论**:
- **TurnState**: 审计声称"5种表示"不实。源码中仅有1个 `class TurnState` 定义(enum)
- **ToolCall**: 3个真实独立定义，分属不同子系统(`kernelone/llm`, `cells/roles/kernel/public`, `cells/roles/kernel/internal/services`)，接口各异但目前未见运行时冲突
- **LLMResponse**: 4处引用，但均为不同子系统的不同 dataclass 定义
- **Usage**: 2处，但共享同一 canonical 定义于 `kernelone/llm/shared_contracts.py`

**操作结论**: 分类为"子系统多样性，可接受"。无需强制收敛，但建议在各自子系统的 `__init__.py` 中明确标注所属上下文

---

### [P0-006] ProviderFormatter Protocol 重复定义

**严重程度**: MEDIUM (已修正) | **Expert**: Expert 3

| 位置 | 方法签名 |
|------|---------|
| `kernelone/llm/shared_contracts.py:332` | `format_tools(self, tools, provider)`, `format_messages(self, messages, provider)` |
| `cells/roles/kernel/internal/llm_caller/provider_formatter.py:15` | `format_messages(self, messages)`, `format_tool_result(...)`, `format_tools(self, tool_schemas, provider_id)` |

**调查结论**:
- 两个 Protocol 属于**不同子系统**，接口**完全不兼容**
- `kernelone/shared_contracts` 版本: 通用 LLM Provider 格式化
- `roles/kernel/internal/llm_caller` 版本: 角色 Cell 专用，带 `ContextEvent` 类型和 `format_tool_result`
- **审计声称"导致运行时类型错误"未经验证** - 两处均为 internal，未发现实际运行时冲突
- 两者均未在 public API 导出

**操作结论**: 分类为"子系统协议多样性，可接受"。建议将 roles 版本的类重命名为 `RoleProviderFormatter` 以消除命名歧义（需单独 ticket）

---

## 🟠 P1 - 高优先级

### [P1-001] get_provider_manager() 3处定义
**Expert**: Expert 2, Expert 10
- `kernelone/llm/providers/registry.py` → 懒加载包装，返回 infra 单例
- `cells/llm/provider_runtime/internal/providers.py` → 直接返回 infra 单例
- `infrastructure/llm/providers/provider_registry.py` → 唯一 canonical 单例

**调查结论**: 仅有1个 canonical 单例（infrastructure 层），kernelone 和 cells 层各1个 accessor wrapper 委托到同一单例。无实际 bug，架构可接受。无需修复。

### [P1-002] parse_tool_calls() 4+ 实现
**Expert**: Expert 10
- `kernelone/llm/toolkit/parsers/core.py:23` → **canonical 独立函数**
- `kernelone/llm/toolkit/native_function_calling.py:396` → 同类方法 `parse_tool_calls_from_response`
- `cells/roles/kernel/services/contracts.py:171` → **Protocol 方法签名**（非实现）
- `cells/roles/kernel/internal/output_parser.py:196` → **角色子系统方法实现**

**调查结论**: 1个 canonical 函数 + 1个角色子系统方法。Protocol 方法仅定义接口，不算独立实现。"4+"系夸大。架构可接受，无需强制收敛。

### [P1-003] 3个 RendererRegistry 类
**Expert**: Expert 2
- `delivery/cli/director/projection/registry.py:17` → `dispatch()` 方法，签名 `(event) → list[WidgetUpdate]`
- `delivery/cli/director/projection.py:450` → `render()`/`render_batch()` 方法，签名不同
- `delivery/cli/director/projection/projection_layer.py:471` → `render()` 方法

**调查结论**: 3个不同类，接口各异（方法名、签名都不同），服务于同一 CLI 投影系统的不同层次演进。架构冗余但无运行时 bug。建议后续合并为1个通用 RendererRegistry（需单独 ticket）。

### [P1-004] TaskRuntimeState 2处重复
**Expert**: Expert 9
- `kernelone/workflow/engine.py:141` → KernelOne workflow 引擎状态
- `cells/orchestration/workflow_runtime/.../engine.py:34` → Cells orchestration 工作流引擎状态

**调查结论**: 两处定义几乎相同（字段完全一致），但分属不同子系统（KernelOne workflow vs Cells orchestration），服务于不同工作流引擎。架构冗余但无冲突。`WorkflowRuntimeState` 有细微差异（orchestration 版本多3个字段）。无需强制收敛。

### [P1-005] Event System 三分
**Expert**: Expert 9
- `MessageBus` (legacy)
- `TypedEventBusAdapter` + `EventRegistry` (new)
- `NATSBroker` (transport)

### [P1-006] TTL=300s 在10+处重复
**Expert**: Expert 8
- `cache_policies.py`, `cache_manager.py`, `cache.py`, `workflow/base.py`, `domain/services/`, `infrastructure/providers/`

### [P1-007] 7+ 个 `get_instance()` 单例模式重复
**Expert**: Expert 2
- StrategyRegistry, RoleOverlayRegistry, OmniscientAuditBus, KernelAuditRuntime, AuditGateway, MetricsCollector, ThemeManager

### [P1-008] _call_role_llm 未完全去重
**Expert**: Expert 10
- `cells/roles/adapters/internal/director/adapter.py:154`
- `cells/roles/kernel/internal/turn_transaction_controller.py`

### [P1-009] Model Config Resolution 分散
**Expert**: Expert 10
- `kernelone/llm/runtime.py`
- `cells/llm/provider_runtime/internal/runtime_invoke.py`
- `cells/llm/provider_config/internal/provider_context.py`

### [P1-010] MAX_FILE_SIZE=10MB 在5处重复
**Expert**: Expert 8

### [P1-011] RoleAgent / AgentMessage 类型体系三分
**Expert**: Expert 7, Expert 3
- `polaris.kernelone.roles.shared_contracts`: 抽象接口 (`message_queue=None`)
- `polaris.cells.roles.runtime.internal.agent_runtime_base`: 完整实现 (`AgentBusProxy`)
- `polaris.cells.roles.runtime.public.contracts`: 从 internal re-export

**问题**: CYCLE-010 修复受阻 — `shared_contracts.RoleAgent` 的 `message_queue` 返回 `None`，而 `agent_runtime_base.RoleAgent` 提供 `AgentBusProxy`。`CFOAgent` 需要 `message_queue` 但无法使用 public abstract 版本。

**修复方案**:
1. 将 `shared_contracts.RoleAgent` 扩展为包含 `AgentBusProxy` 默认实现
2. 将 `agent_runtime_base.py` 的 `AgentMessage/MessageType/AgentStatus` 改为从 `shared_contracts` 导入（消除重复定义）
3. `polaris.cells.roles.runtime.public.contracts` 从 `shared_contracts` 导出完整类型体系

---

## 🟡 P2 - 中优先级

### [P2-001] normalize_task_status 3处几乎相同
**Expert**: Expert 1

### [P2-002] normalize_director_result_status 2处几乎相同
**Expert**: Expert 1

### [P2-003] 类型别名命名混乱 (SchemaDict/EventDict/TokenEstimate/...)
**Expert**: Expert 4

### [P2-004] ValidationError 名称冲突 Pydantic
**Expert**: Expert 4

### [P2-005] LLMError 重复定义
**Expert**: Expert 3, Expert 4
- `domain/exceptions.py`
- `cells/roles/kernel/internal/services/contracts.py`

### [P2-006] ShellDisallowedError 重复
**Expert**: Expert 4
- `kernelone/process/contracts.py`
- `kernelone/process/async_contracts.py`

### [P2-007] Singleton Mixin 缺失
**Expert**: Expert 2
建议创建 `Singleton[T]` 泛型基类

### [P2-008] TypedRegistry[T] 基类缺失
**Expert**: Expert 2
建议创建统一的注册表基类

### [P2-009] Wildcard Import 5+ 处
**Expert**: Expert 7
- `cells/resident/autonomy/internal/evidence_models.py`
- `cells/resident/autonomy/internal/resident_models.py`
- `cells/llm/evaluation/internal/validators.py`

### [P2-010] .polaris vs .polaris 路径混乱
**Expert**: Expert 8

### [P2-011] WorkflowEngine 重复 (Expert 4)
**Expert**: Expert 4

| 实例 | 位置 | 状态 |
|------|------|------|
| `WorkflowEngine` | `kernelone/workflow/engine.py:194` | Canonical |
| `SagaWorkflowEngine` | `kernelone/workflow/saga_engine.py:85` | 合理扩展 |
| `WorkflowEngine` | `cells/orchestration/workflow_runtime/.../engine.py:65` | 迁移残留副本 |

**结论**: cells/orchestration 中的 `WorkflowEngine` 是迁移残留 artifact，应消除重复。kernelone 版本为 canonical。
**建议**: Cells orchestration 层应导入并使用 `kernelone.workflow.engine.WorkflowEngine`，而非维护独立副本。

### [P2-012] BudgetPolicy 重复 (Expert 4)
**Expert**: Expert 4

| 实例 | 位置 | 字段 |
|------|------|------|
| `BudgetPolicy` (dataclass) | `cells/roles/kernel/internal/policy.py:58` | `max_tool_rounds`, `max_total_lines_read` |
| `BudgetPolicy` (class) | `cells/roles/kernel/internal/policy/budget_policy.py:86` | 完整：`BudgetState`, `evaluate()`, `from_env()`, `configure()` |

**结论**: 两个 BudgetPolicy 服务于不同目的（极简 vs 完整）。非冲突，分类为可接受子系统多样性。
**建议**: 保持现状，或将 `policy.py` 中的 dataclass 重命名为 `DirectorBudgetPolicy` 以消除命名歧义。

### [P2-013] HandlerRegistry 重复 (Expert 8处注册表)
**Expert**: Expert 4

8个注册表模式分布在不同子系统，其中 `kernelone/workflow/engine.py` 的 `HandlerRegistry` Protocol 与 `cells/orchestration/.../contracts.py` 的 `CellHandlerRegistry` 有关联（后者实现前者），其余为独立注册表需求。
**结论**: 架构冗余但无运行时冲突。

---

## 🟢 P3 - 低优先级

### [P3-001] TimerWheel / PersistentTimerWheel 重构
### [P3-002] StrategyRegistry / RoleOverlayRegistry 单例模式去重
### [P3-003] DirectorLogic 简化版重复 (~113行 x2)
### [P3-004] ToolSpec 类型别名重复定义

---

## 收敛路线图 (6阶段)

| Phase | 时间 | 任务 | 节省代码 |
|-------|------|------|---------|
| **Phase 1** | Week 1-2 | 架构违规修复 (Domain→Infra, Delivery/Appl→Cell) | - |
| **Phase 2** | Week 2-3 | director_logic_rules 合并 (~900行) | ~900行 |
| **Phase 3** | Week 3-4 | 工具定义统一 (contracts.py vs definitions.py) | ~300行 |
| **Phase 4** | Week 4-5 | 异常处理修复 (textual_console, diff_tracker) | - |
| **Phase 5** | Week 5-6 | TurnState/ToolCall/LLMResponse 统一 | ~500行 |
| **Phase 6** | Week 6+ | 常量/命名/Registry 清理 | ~400行 |

**预计总工时**: 80-120h
**预计节省代码**: 2100+ 行重复

---

## 验收标准

```bash
# 1. 架构门禁
python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only

# 2. 异常处理检查
grep -r "except Exception:" polaris/kernelone/ polaris/domain/ polaris/cells/ --include="*.py" | wc -l
# 目标: < 50 (从 250+ 降低)

# 3. pytest 全量
pytest polaris/kernelone/context/tests/ polaris/kernelone/llm/tests/ -v --tb=short
```
