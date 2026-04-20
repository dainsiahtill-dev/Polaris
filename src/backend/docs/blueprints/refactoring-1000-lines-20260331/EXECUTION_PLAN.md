# 重构执行计划

**版本**: 2026-03-31
**状态**: Draft
**执行周期**: 3天

---

## 1. 执行概述

本文档定义10人专家团队并行执行重构的详细步骤、依赖关系和交付物。

---

## 2. 并行执行策略

### 2.1 执行波次

```
Wave 1 (并行): 基础模块提取
├── E1: turn_engine/config.py
├── E2: context_os/patterns.py + helpers.py
├── E3: llm_caller/retry_policy.py
├── E4: runtime/service/persistence.py
├── E5: kernel/retry_handler.py
└── E6: context_event.py

Wave 2 (并行): 数据类提取
├── E1: turn_engine/artifacts.py
├── E2: context_os/classifier.py
├── E3: llm_caller/response_parser.py
├── E4: runtime/service/context_adapter.py
├── E5: kernel/prompt_adapter.py
└── E6: tool_result_formatter.py

Wave 3 (依赖 Wave 1-2): 核心类精简
├── E1: turn_engine/engine.py
├── E2: context_os/runtime.py
├── E3: llm_caller/caller.py
├── E4: runtime/service/service.py
├── E5: kernel/kernel.py
└── E6: tool_loop_controller.py

Wave 4 (集成): 测试与验证
├── E7: 接口契约验证
├── E8: 回归测试
├── E9: 文档更新
└── E10: 质量门禁
```

### 2.2 依赖矩阵

| 任务 | 依赖任务 | 阻塞者 |
|------|----------|--------|
| E1-Wave3 | E1-Wave1, E1-Wave2 | 无 |
| E2-Wave3 | E2-Wave1, E2-Wave2 | 无 |
| E3-Wave3 | E3-Wave1, E3-Wave2 | E1-Wave3 (TurnEngine依赖) |
| E4-Wave3 | E4-Wave1, E4-Wave2 | E2-Wave3 (ContextOS依赖) |
| E5-Wave3 | E5-Wave1, E5-Wave2 | E3-Wave3 (LLMCaller依赖) |
| E6-Wave3 | E6-Wave1, E6-Wave2 | E1-Wave3 (TurnEngine依赖) |
| E8-Wave4 | E1-E6 Wave3 | 全部核心任务 |
| E10-Wave4 | E8-Wave4 | 测试通过 |

---

## 3. 各专家详细任务

### 3.1 E1: TurnEngine Lead

**Wave 1: config.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/turn_engine/config.py
# 提取内容:
# - TurnEngineConfig (dataclass)
# - SafetyState (dataclass)
# - 环境变量读取函数

# 预估行数: ~120行
# 依赖: 无
# 测试: test_turn_engine_config.py
```

**Wave 2: artifacts.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/turn_engine/artifacts.py
# 提取内容:
# - AssistantTurnArtifacts (dataclass)
# - _BracketToolWrapperFilter (class)
# - 相关正则表达式

# 预估行数: ~200行
# 依赖: 无
# 测试: test_turn_engine_artifacts.py
```

**Wave 3: engine.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/turn_engine/engine.py
# 提取内容:
# - TurnEngine (class)
# - 核心循环逻辑

# 预估行数: ~600行
# 依赖: config.py, artifacts.py
# 测试: test_turn_engine.py (已有)
```

---

### 3.2 E2: Context OS Lead

**Wave 1: patterns.py + helpers.py**
```python
# 目标文件: polaris/kernelone/context/context_os/patterns.py
# 提取内容:
# - 所有正则表达式常量
# - 配置常量

# 预估行数: ~200行

# 目标文件: polaris/kernelone/context/context_os/helpers.py
# 提取内容:
# - _normalize_text, _trim_text, _estimate_tokens 等
# - _StateAccumulator

# 预估行数: ~300行
```

**Wave 2: classifier.py**
```python
# 目标文件: polaris/kernelone/context/context_os/classifier.py
# 提取内容:
# - DialogActClassifier (class)
# - Dialog Act 模式

# 预估行数: ~250行
# 依赖: patterns.py
```

**Wave 3: runtime.py**
```python
# 目标文件: polaris/kernelone/context/context_os/runtime.py
# 保留内容:
# - StateFirstContextOS (class)
# - project() 核心逻辑

# 预估行数: ~800行
# 依赖: patterns.py, helpers.py, classifier.py
```

---

### 3.3 E3: LLM Caller Lead

**Wave 1: retry_policy.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/llm_caller/retry_policy.py
# 提取内容:
# - RetryPolicy (dataclass)
# - RetryDecision (enum)
# - 重试策略函数

# 预估行数: ~200行
# 依赖: 无
```

**Wave 2: response_parser.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/llm_caller/response_parser.py
# 提取内容:
# - ResponseParser (class)
# - 解析逻辑

# 预估行数: ~250行
# 依赖: 无
```

**Wave 3: caller.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/llm_caller/caller.py
# 保留内容:
# - LLMCaller (class)
# - call(), call_stream() 核心

# 预估行数: ~600行
# 依赖: retry_policy.py, response_parser.py
```

---

### 3.4 E4: Service Layer Lead

**Wave 1: persistence.py**
```python
# 目标文件: polaris/cells/roles/runtime/public/service/persistence.py
# 提取内容:
# - SessionPersistence (class)
# - 持久化逻辑

# 预估行数: ~300行
# 依赖: 无
```

**Wave 2: context_adapter.py**
```python
# 目标文件: polaris/cells/roles/runtime/public/service/context_adapter.py
# 提取内容:
# - ContextOSAdapter (class)
# - Context OS 集成逻辑

# 预估行数: ~350行
# 依赖: context_os 模块
```

**Wave 3: service.py**
```python
# 目标文件: polaris/cells/roles/runtime/public/service/service.py
# 保留内容:
# - RoleRuntimeService (class)
# - 协调逻辑

# 预估行数: ~500行
# 依赖: persistence.py, context_adapter.py
```

---

### 3.5 E5: Kernel Lead

**Wave 1: retry_handler.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/kernel/retry_handler.py
# 提取内容:
# - RetryHandler (class)
# - 重试循环逻辑

# 预估行数: ~300行
# 依赖: 无
```

**Wave 2: prompt_adapter.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/kernel/prompt_adapter.py
# 提取内容:
# - PromptAdapter (class)
# - 提示词构建逻辑

# 预估行数: ~350行
# 依赖: 无
```

**Wave 3: kernel.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/kernel/kernel.py
# 保留内容:
# - RoleExecutionKernel (class)
# - facade 方法

# 预估行数: ~600行
# 依赖: retry_handler.py, prompt_adapter.py
```

---

### 3.6 E6: Controller Lead

**Wave 1: context_event.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/context_event.py
# 提取内容:
# - ContextEvent (dataclass)
# - ToolLoopSafetyPolicy (dataclass)
# - 配置常量

# 预估行数: ~200行
# 依赖: 无
```

**Wave 2: tool_result_formatter.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/tool_result_formatter.py
# 提取内容:
# - ToolResultFormatter (class)
# - 格式化逻辑

# 预估行数: ~150行
# 依赖: 无
```

**Wave 3: tool_loop_controller.py**
```python
# 目标文件: polaris/cells/roles/kernel/internal/tool_loop_controller.py
# 保留内容:
# - ToolLoopController (class)
# - 核心控制逻辑

# 预估行数: ~500行
# 依赖: context_event.py, tool_result_formatter.py
```

---

### 3.7 E7: Integration Architect

**职责**:
- 定义模块间接口契约
- 解决循环依赖
- 验证依赖图正确性

**交付物**:
- `INTERFACE_CONTRACT.md`
- `DEPENDENCY_GRAPH.md`

---

### 3.8 E8: Test Engineer

**职责**:
- 维护现有测试通过率
- 为新模块创建测试
- 执行回归测试

**交付物**:
- 测试报告
- 覆盖率报告

---

### 3.9 E9: Documentation Lead

**职责**:
- 更新模块文档
- 更新 CLAUDE.md 引用
- 生成 CHANGELOG

**交付物**:
- 更新的文档文件
- CHANGELOG 条目

---

### 3.10 E10: Quality Gate

**职责**:
- 执行 Ruff 检查
- 执行 Mypy 类型检查
- 验证代码质量

**交付物**:
- 质量报告
- 门禁通过确认

---

## 4. 执行检查点

### Checkpoint 1 (Day 1 End)

- [ ] Wave 1 所有任务完成
- [ ] 无循环导入
- [ ] 现有测试通过

### Checkpoint 2 (Day 2 End)

- [ ] Wave 2 所有任务完成
- [ ] Wave 3 所有任务完成
- [ ] 集成测试通过

### Checkpoint 3 (Day 3 End)

- [ ] 文档更新完成
- [ ] 质量门禁通过
- [ ] 准备合并

---

## 5. 回滚计划

如果重构导致严重问题：

1. **立即回滚**: `git checkout .`
2. **保留提取的模块**: 仅回滚核心文件修改
3. **分阶段合并**: 按文件逐个合并，降低风险

---

## 6. 通信协议

- **每日同步**: 09:00, 17:00
- **问题升级**: 发现阻塞立即通知 E7
- **进度报告**: 每个检查点后更新本文档