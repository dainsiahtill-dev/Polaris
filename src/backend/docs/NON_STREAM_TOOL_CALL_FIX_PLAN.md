# Non-Stream 模式工具调用解析问题修复方案

## 1. 问题描述

### 1.1 现象
- Benchmark 测试通过率仅 15% (3/20)
- 非 stream 模式下，模型返回的工具调用未被正确识别
- 触发错误：`assistant_visible_output_empty: model returned no visible output or tool calls`

### 1.2 影响范围
- 所有使用非 stream 模式的 LLM 调用场景
- Benchmark 评测系统
- 依赖工具调用的 Agent 工作流

---

## 2. 根因分析

### 2.1 问题链路

```
Provider Response (非 stream)
    │
    ├── output: "...<tool_call>{\"name\":\"repo_rg\"...}</tool_call>..."
    ├── structured: null
    └── raw: {...}  ← 不包含 tool_calls
            │
            ▼
AIExecutor.invoke()
    ├── 只提取 output 和 structured
    ├── 不解析工具调用
    └── 返回 AIResponse
            │
            ▼
LLMCaller._extract_native_tool_calls()
    ├── raw.tool_calls (OpenAI) ❌ 不存在
    ├── raw.content[].tool_use (Anthropic) ❌ 不存在
    └── output 文本中的工具调用 ❌ 未处理 ← 根因
            │
            ▼
TurnEngine.run()
    ├── native_tool_calls = []
    ├── parsed_tool_calls = []
    └── _resolve_empty_visible_output_error() → 错误
```

### 2.2 根本原因
1. **文本协议已废弃**：`parse_tool_calls()` 中的文本解析路径直接返回空列表
2. **非 Stream 执行器不处理工具调用**：`AIExecutor.invoke()` 只返回 output 和 structured
3. **依赖外部传入**：`KernelToolCallingRuntime` 需要通过 `native_tool_calls` 参数接收

### 2.3 为什么 Stream 模式正常
- Stream 模式通过 SSE 流直接接收 `tool_call` 事件
- 不依赖于文本解析

---

## 3. 修复方案

### 3.1 方案 A：文本工具调用回退解析（推荐）

**原理**：当 `native_tool_calls` 为空但 `content` 中存在文本格式的工具调用时，尝试解析。

**优点**：
- 改动最小，局部修复
- 不影响现有结构化解析路径
- 符合"最后防线"设计原则

### 3.2 方案 B：强制 Stream 模式

**原理**：在 benchmark 配置中强制使用 `stream=True`

**优点**：
- 彻底解决问题
- 符合 OpenCode 的设计理念

---

## 4. 实施计划

### Phase 1: 核心修复
| 任务 | 修改文件 | 负责人 |
|------|----------|--------|
| 添加文本工具调用回退解析 | `turn_engine.py` | Agent-1 |
| 增强 LLMCaller._extract_native_tool_calls() | `llm_caller.py` | Agent-2 |

### Phase 2: 边界处理
| 任务 | 修改文件 | 负责人 |
|------|----------|--------|
| 添加白名单验证 | `turn_engine.py` | Agent-3 |
| 处理解析失败场景 | `turn_engine.py` | Agent-4 |

### Phase 3: 测试覆盖
| 任务 | 修改文件 | 负责人 |
|------|----------|--------|
| 单元测试 | `test_turn_engine.py` | Agent-5 |
| 集成测试 | `test_llm_caller.py` | Agent-6 |

### Phase 4: 验证
| 任务 | 修改文件 | 负责人 |
|------|----------|--------|
| Benchmark 验证 | - | Agent-7 |
| 回归测试 | - | Agent-8 |

### Phase 5: 文档与优化
| 任务 | 修改文件 | 负责人 |
|------|----------|--------|
| 代码审查 | - | Agent-9 |
| 性能优化 | `turn_engine.py` | Agent-10 |

---

## 5. OpenCode 对比分析

### 5.1 OpenCode 设计
- **只使用 Stream 模式**：避免文本解析问题
- **IsError 布尔标志**：统一错误区分
- **多层 Fallback**：工具级降级策略
- **Panic 恢复**：生产环境健壮性

### 5.2 Polaris 当前实现
- **Stream + Non-Stream 双模式**：复杂性增加
- **解析器废弃文本协议**：历史遗留
- **缺少 IsError 标志**：需要添加

### 5.3 改进建议
| 优先级 | 建议 | 理由 |
|--------|------|------|
| P0 | 添加文本工具调用回退解析 | 立即解决 |
| P1 | 统一使用 Stream 模式 | 彻底解决 |
| P2 | 添加 IsError 错误标志 | 提升一致性 |

---

## 6. 验证方法

### 6.1 单元测试
```bash
pytest polaris/cells/roles/kernel/tests/test_turn_engine.py -v -k "text_tool"
```

### 6.2 Benchmark
```bash
pytest polaris/kernelone/context/benchmarks/ -v
```

### 6.3 日志检查
```bash
grep "fallback: parsed" logs/kernel.log
```

---

## 7. 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|----------|
| 回退解析可能误解析正常文本 | 中 | 仅在 native_tool_calls 和 parsed_tool_calls 都为空时触发 |
| 性能开销 | 低 | 仅在必要时触发 |
| 引入回归 | 中 | 已有测试覆盖 |

---

## 8. 成功标准

- [ ] Benchmark 通过率从 15% 提升至 80%+
- [ ] 单元测试覆盖率不降低
- [ ] 无回归问题
- [ ] 代码审查通过

---

**创建时间**: 2026-03-28
**更新历史**:
- 2026-03-28: 初始版本
