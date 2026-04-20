# Product Requirements

## Overview

Polaris 是一个**单人云端主模型 + 本地 SLM 协同**的自动化编程指挥台，要求对接的 LLM 模型具备**思考过程**和**流式输出**能力。

通过 PM 规划 → Director 执行 → QA 校验 → Dashboard 可视化的闭环流程，构建一个**可控、可追溯、可回放、可长期长跑**的个人软件工厂。

**设计根源**：面向**无人值守**的自动化写代码；**宁修不滚（Fix-Forward）**——默认不自动回滚，宁愿保留问题代码后续修，也不白写再回滚浪费时间和 token。详见 `docs/agent/invariants.md` 不变量 #10。

## Goals

1. **透明可追溯的 AI 决策过程**：模型必须输出结构化的思考过程，支持 Inner Voice 和 Glass Mind 可视化
2. **实时反馈的用户体验**：支持流式输出，提供长时间任务的进度监控
3. **固定成本优先的运营模式**：通过 FIXED/CLOUD 主模型 + LOCAL(SLM) 分流协同，降低边际成本
4. **工程化的证据链管理**：所有决策必须有据可查，支持回放和复盘

## Functional Requirements

### FR-1: 思考过程支持

**描述**：模型必须能够输出结构化的思考过程，确保 PM 和 Director 的决策透明可追溯

**验收标准**：
- PM/Director 角色输出包含 `<thinking>` 标签或 `reasoning_summary` 字段
- 思考过程逻辑清晰，可理解，能说明决策依据
- 支持工具调用前的推理说明
- 未检测到 thinking 信号的角色将被标记为不胜任

**优先级**：P0（必须）

### FR-2: 流式实时输出

**描述**：模型必须支持逐 token 的实时输出，提供即时反馈

**验收标准**：
- 支持 SSE（Server-Sent Events）或兼容协议
- Token 输出延迟 < 100ms
- 支持中断和恢复机制
- 兼容 OpenAI SDK 的流式接口

**优先级**：P0（必须）

### FR-3: 模型兼容性验证

**描述**：系统必须能自动验证模型的兼容性，包括 thinking 和 streaming 能力

**验收标准**：
- 自动检测 Thinking 支持（通过输出标签识别）
- 自动测试 Streaming 能力（逐 token 验证）
- 提供兼容性报告（兼容/部分兼容/不兼容）
- 不兼容模型阻止进入 READY 状态

**优先级**：P0（必须）

### FR-4: 角色路由与胜任性测试

**描述**：支持为不同角色（PM/Director/QA/Docs）选择不同模型，并验证其胜任性

**验收标准**：
- PM/Director 必须同时支持 Thinking + Streaming
- QA/Docs 必须支持 Streaming，Thinking 推荐但不强制
- 面试模式验证角色胜任性
- 未通过验证的角色标记为 BLOCKED

**优先级**：P0（必须）

### FR-5: 成本通道管理

**描述**：支持三种成本通道的模型接入和成本控制

**验收标准**：
- LOCAL 通道：本地小模型（SLM，Ollama/LM Studio 等）前置分流，边际成本 ≈ 电费（不作为 Director 主模型）
- FIXED 通道：订阅 CLI（Codex 等），固定成本，适合长跑
- METERED 通道：标准 HTTPS API，默认强门禁控制
- 提供成本观测和预算门禁

**优先级**：P1（重要）

### FR-6: 事实流与可回放

**描述**：所有关键事件必须记录，支持回放和复盘

**验收标准**：
- `events.jsonl` 记录所有原子事件（只追加不覆盖）
- Run ID 全局唯一，串联所有产物
- 失败能在 3 hops 内定位（Phase → Evidence → Tool Output）
- 支持跨会话的状态恢复

**优先级**：P0（必须）

## Non-Functional Requirements

### NFR-1: 性能要求

| 指标 | 要求 | 说明 |
|------|------|------|
| 流式输出延迟 | < 100ms | 每 token 输出延迟 |
| 思考过程解析 | < 1s | thinking 标签解析时间 |
| 事件写入 | 原子操作 | write tmp → fsync → rename |
| UI 响应 | < 50ms | Dashboard 交互响应 |

### NFR-2: 兼容性要求

| 类型 | 要求 |
|------|------|
| SDK 兼容 | OpenAI SDK 标准接口 |
| Provider 兼容 | CLI / Local HTTP / Standard HTTPS |
| 模型兼容 | 必须支持 Thinking + Streaming |
| 协议兼容 | SSE / newline-delimited JSON |

### NFR-3: 可观测性要求

| 指标 | 要求 |
|------|------|
| 思考过程 | 必须记录到 DIALOGUE.jsonl |
| 流式性能 | 监控延迟，记录性能指标 |
| 成本追踪 | 按通道/角色/模型追踪调用和成本 |
| 事件溯源 | 所有决策有完整的证据链 |

### NFR-4: 安全与可靠性

| 指标 | 要求 |
|------|------|
| 记忆安全 | Memory/Reflection 必须带 refs，不能当事实 |
| 编码安全 | 所有文本读写显式 UTF-8 |
| UI 隔离 | 运行态 UI 只读，不修改任务/代码/状态 |
| 异常处理 | 超时/错误处理可控，支持重试 |

## Recommended Models

### 完全兼容（推荐）

| 模型 | Provider | Thinking | Streaming | 说明 |
|------|----------|----------|-----------|------|
| GPT-4 | OpenAI | ✅ | ✅ | 原生支持，最佳体验 |
| Claude-3 | Anthropic | ✅ | ✅ | 强推理能力 |
| Kimi-K2 | Moonshot | ✅ | ✅ | OpenAI 兼容，性价比高 |
| MiniMax | MiniMax | ✅ | ✅ | 支持中文优化 |
| Codex CLI | OpenAI | ✅ | ✅ | 固定成本，适合长跑 |

### 部分兼容（需要适配）

| 模型 | Provider | Thinking | Streaming | 适配要求 |
|------|----------|----------|-----------|----------|
| Local SLM（Qwen/Llama） | Ollama / LM Studio | ⚠️ | ✅ | 需 prompt 工程，仅建议用于 `director_runtime` 前置分流 |
| Gemini CLI | Google | ⚠️ | ✅ | 需要格式转换 |

> 注：Director 默认应绑定 Cloud/FIXED 主模型；本地模型作为可选 SLM，通过 `director_runtime` 前置分流并在失败时回退主模型。

### 不兼容（不推荐）

| 模型 | Provider | Thinking | Streaming | 说明 |
|------|----------|----------|-----------|------|
| 基础模型 | HuggingFace | ❌ | ❌ | 缺少必要功能 |

## Provider 开发指南

```python
class BaseProvider:
    async def invoke_stream(
        self,
        prompt: str,
        model: str,
        config: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """必须实现流式输出"""
        pass

    def supports_thinking(self, model: str) -> bool:
        """检查模型是否支持 thinking"""
        pass

    async def health_check(self) -> bool:
        """健康检查"""
        pass
```

## Test Cases

### TC-1: Thinking 能力测试

```python
async def test_thinking_support():
    response = await provider.invoke("请分析这个需求并制定计划", model)
    assert "<thinking>" in response
    assert "</thinking>" in response
```

### TC-2: 流式输出测试

```python
async def test_streaming_support():
    tokens = []
    async for token in provider.invoke_stream("测试", model):
        tokens.append(token)
        assert len(token) > 0
    assert len(tokens) > 1
```

### TC-3: 角色胜任性测试

```python
async def test_pm_qualification():
    result = await interview_pm(model)
    assert result.has_thinking
    assert result.is_streaming
    assert result.supports_planning
```

## Glossary

| 术语 | 定义 |
|------|------|
| Thinking | 模型输出的推理过程，通常用 `<thinking>` 或 `reasoning_summary` 包裹 |
| Streaming | 逐 token 的实时输出，支持 SSE 协议 |
| Provider | LLM 的接入方式（CLI / Local HTTP / HTTPS） |
| Cost Channel | 成本类型（LOCAL/FIXED/METERED） |
| Role | Polaris 中的角色（PM/Director/QA/Docs） |
| Run ID | 全局唯一的运行标识符 |
| Glass Mind | 可视化 AI 思考过程的面板 |
