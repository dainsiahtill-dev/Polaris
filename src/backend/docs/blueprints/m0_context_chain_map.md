# M0.1 Context主链现状测绘

> 生成时间: 2026/04/13
> 分析基于: runtime.py, context_gateway.py, context_assembler.py

## 1. 当前 Context 主链流程图

```
用户输入 + 历史记录
         │
         ▼
RoleContextGateway.build_context() [context_gateway.py:217]
         │
         ├──→ StateFirstContextOS.project() [runtime.py:346]
         │        └──→ _project_impl() [runtime.py:364] (11步骤)
         │
         ├──→ _messages_from_projection() [context_gateway.py:1310]
         │
         └──→ 压缩 + TokenBudget强制

ContextAssembler.build_context() [context_assembler.py:294]
         ├──→ _build_standard_context() [context_assembler.py:450]
         └──→ _build_turn_engine_context() [context_assembler.py:364]
```

## 2. 所有入口点列表

| 入口点 | 文件:行号 | 类型 | 说明 |
|--------|-----------|------|------|
| `StateFirstContextOS.project()` | `runtime.py:346` | Async主入口 | Context OS核心投影方法 |
| `StateFirstContextOS._project_impl()` | `runtime.py:364` | 内部实现 | 实际投影逻辑，含11个步骤 |
| `StateFirstContextOS._project_via_pipeline()` | `runtime.py:514` | Pipeline模式 | 当enable_pipeline=True时调用 |
| `RoleContextGateway.build_context()` | `context_gateway.py:217` | Async主入口 | Gateway层上下文组装 |
| `ContextAssembler.build_context()` | `context_assembler.py:294` | 主入口 | 服务层上下文组装 |
| `ContextAssembler._build_standard_context()` | `context_assembler.py:450` | 内部 | 标准ContextRequest处理路径 |
| `ContextAssembler._build_turn_engine_context()` | `context_assembler.py:364` | 内部 | TurnEngineContextRequest处理路径 |

## 3. 旁路组装嫌疑点列表

| 嫌疑点 | 文件:行号 | 证据 | 风险级别 |
|-------|-----------|------|----------|
| **Provider直接构建** | `openai_compat_provider.py:445-446` | `messages.append({"role": "system"...})` | 高 |
| **Provider直接构建** | `kimi_provider.py:363-364`, `439-440` | 同上 | 高 |
| **CLI Director工具** | `director_llm_tools.py:98-102`, `131-135` | 直接构建完整messages数组 | 高 |
| **CLI PM工具** | `chief_engineer_llm_tools.py:75-79`, `109-113` | 直接构建完整messages数组 | 高 |
| **SubagentRuntime** | `subagent_runtime.py:231`, `274` | 直接append消息 | 中 |
| **RoleIntegrations** | `role_integrations.py:336-340`, `987-990` | 直接append消息 | 中 |
| **OpenAI Responses Adapter** | `openai_responses_adapter.py:133`, `181-185` | 直接构建messages | 中 |
| **fake_context测试** | `fake_context.py:323-324` | 测试代码直接构建 | 低 |
| **ContextAssembler旁路** | `context_assembler.py:450-555` | `_build_standard_context()`直接用PromptChunkAssembler | 中 |

## 4. TokenBudget 强制执行现状

### 4.1 多层TokenBudget架构

```
Layer 1: ContextAssembler (服务层)
├─ max_context_tokens: 120,000 (default)
├─ safety_margin: 0.85
└─ 压缩策略: SLIDING_WINDOW / SUMMARIZE / TRUNCATE

Layer 2: RoleContextGateway (Gateway层)
├─ policy.max_context_tokens: 从RolePolicy获取
└─ _emergency_truncate() 作为最终安全网

Layer 3: StateFirstContextOS (OS层)
├─ resolved_context_window: 模型实际窗口大小
├─ BudgetPlan计算
└─ active_window_ratio: 0.45

Layer 4: Provider适配器层
└─ Provider自己处理最终消息格式化（无TokenBudget控制）
```

### 4.2 当前问题

1. **多重Budget标准不统一**: 三层的max_context_tokens来源不同
2. **旁路绕过风险**: Provider/CLI层完全绕过StateFirstContextOS的BudgetPlan控制
3. **压缩触发条件不一致**: 三处压缩触发条件各不相同

## 5. 优先修复项

1. **统一TokenBudget标准**: 三层的max_context_tokens应源自同一配置
2. **封堵Provider旁路**: Provider适配器应通过Gateway获取context
3. **封堵CLI旁路**: CLI LLM工具应使用统一的Context组装服务
4. **增强Budget验证**: 在RoleContextGateway.build_context()末尾增加BudgetPlan.validate_invariants()
