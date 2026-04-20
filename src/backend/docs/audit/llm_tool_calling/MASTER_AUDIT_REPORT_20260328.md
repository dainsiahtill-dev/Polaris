# Polaris LLM工具调用系统全面审计报告

**审计时间**: 2026-03-28
**审计深度**: 深入代码级审计
**审计范围**: `polaris/kernelone/llm/` + `polaris/cells/roles/kernel/` + `polaris/cells/llm/tool_runtime/` + `core/llm_toolkit/`
**审计团队**: 6位高级Python工程师

---

## 目录

1. [审计执行摘要](#1-审计执行摘要)
2. [问题全景图](#2-问题全景图)
3. [类别A：权威源头碎片化](#3-类别a权威源头碎片化)
4. [类别B：解析器架构混乱](#4-类别b解析器架构混乱)
5. [类别C：Provider系统问题](#5-类别c-provider系统问题)
6. [类别D：角色策略系统问题](#6-类别d-角色策略系统问题)
7. [类别E：执行器问题](#7-类别e-执行器问题)
8. [类别F：规范化问题](#8-类别f-规范化问题)
9. [类别G：残留废弃问题](#9-类别g-残留废弃问题)
10. [根因总结](#10-根因总结)
11. [附录：问题索引](#11-附录问题索引)

---

## 1. 审计执行摘要

### 1.1 审计团队

| 工程师 | 代号 | 专长领域 | 审计重点 |
|--------|------|---------|---------|
| **A** | Tooling/Executor | 工具执行引擎、handler架构 | executor/core.py、handlers/ |
| **B** | Provider/Adapter | 多Provider适配、ProviderRegistry | provider_adapters/、provider_registry/ |
| **C** | Parsing/Normalization | 解析器架构、参数归一化 | parsers/、tool_normalization.py |
| **D** | Role/Policy | 角色授权、工具策略 | RoleToolGateway、profile/ |
| **E** | Governance/Integration | 架构治理、边界契约 | definitions.py、contracts.py |
| **F** | Pragmatic/Stability | 风险控制、渐进式收敛 | 迁移路径、优先级 |

### 1.2 问题统计

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM 工具调用系统问题统计                       │
├───────────────┬─────────────────────────────────────────────────┤
│ 类别          │ 问题数量 │ 严重程度                              │
├───────────────┼─────────┼──────────────────────────────────────┤
│ A. 权威源头   │ 3个     │ 🔴 严重                               │
│ B. 解析器    │ 5个     │ 🔴 严重                               │
│ C. Provider   │ 4个     │ 🟠 高                                │
│ D. 角色/策略 │ 4个     │ 🟠 高                                │
│ E. 执行器    │ 3个     │ 🟠 高                                │
│ F. 规范化    │ 3个     │ 🟡 中                                │
│ G. 残留废弃  │ 2个     │ 🟡 中                                │
├───────────────┴─────────┼──────────────────────────────────────┤
│ 总计              │ 24个  │                                      │
└─────────────────────────┴──────────────────────────────────────┘
```

### 1.3 架构评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ★★☆☆☆ | 权威源头碎片化，边界不清 |
| 代码质量 | ★★☆☆☆ | 异常吞噬普遍，双重标准 |
| 安全性 | ★★★☆☆ | 有基础防护但实现不一致 |
| 性能 | ★★★☆☆ | 有优化空间但不是主要问题 |
| 可测试性 | ★☆☆☆☆ | 测试覆盖严重不足 |
| 文档 | ★★☆☆☆ | 文档与实现脱节 |

**综合评分**: ★★☆☆☆ (2.2/5) - 需要重大重构

---

## 2. 问题全景图

### 2.1 问题分类总览

```
LLM工具调用流程中的24个问题分布：

┌──────────────────────────────────────────────────────────────────────────┐
│                        LLM 输出                                          │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Layer 1: 解析层 (5个问题)                                        │    │
│  │  ├── 问题B-1: 4层Parser并存，实际只用2层                           │    │
│  │  ├── 问题B-2: Provider Auto-Detection逻辑不稳定                    │    │
│  │  ├── 问题B-3: Argument Key别名规则不统一                           │    │
│  │  ├── 问题B-4: HMAC签名验证不对称                                  │    │
│  │  └── 问题B-5: 非流式JSON fallback是hotfix非架构                    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Layer 2: 规范化层 (3个问题)                                       │    │
│  │  ├── 问题F-1: 两套argument normalization系统                       │    │
│  │  ├── 问题F-2: normalize_tool_name()与TOOL_NAME_ALIASES不一致     │    │
│  │  └── 问题F-3: Path遍历检测分散                                    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Layer 3: 授权层 (4个问题)                                        │    │
│  │  ├── 问题D-1: RoleToolGateway和PolicyLayer双执行路径              │    │
│  │  ├── 问题D-2: TOOL_NAME_ALIASES导入但从未使用                      │    │
│  │  ├── 问题D-3: Director角色配置自相矛盾                            │    │
│  │  └── 问题D-4: 危险命令Patterns两处独立定义                         │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Layer 4: 执行层 (3个问题)                                        │    │
│  │  ├── 问题E-1: Handler映射是隐式Lazy Load                          │    │
│  │  ├── 问题E-2: executor同时依赖两套Tool定义                         │    │
│  │  └── 问题E-3: StreamThinkingParser的async迭代bug(已修复)          │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Tool Definition层 (3个问题)                                     │    │
│  │  ├── 问题A-1: 三处Tool定义必须手动同步                            │    │
│  │  ├── 问题A-2: YAML和Python角色配置重复且不一致                     │    │
│  │  └── 问题A-3: Contract层与LLM-facing Schema不匹配                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Provider层 (4个问题)                                            │    │
│  │  ├── 问题C-1: 两个独立的ProviderManager                           │    │
│  │  ├── 问题C-2: Provider的invoke()对multi-turn支持不完整            │    │
│  │  ├── 问题C-3: MiniMax不使用原生tool calling                       │    │
│  │  └── 问题C-4: Tool Result格式各Provider不一致                      │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                    ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  残留废弃层 (2个问题)                                             │    │
│  │  ├── 问题G-1: PromptBasedToolParser标记deprecated但未删除         │    │
│  │  └── 问题G-2: domain/services/parsing.py独立存在                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.2 问题优先级矩阵

```
                    高影响力
                        │
         ┌──────────────┼──────────────┐
         │              │              │
    ┌────┴────┐   ┌─────┴─────┐  ┌────┴────┐
    │ A-1     │   │ B-1       │  │ C-1     │
    │ A-2     │   │ B-2       │  │ C-2     │
    │ 三处定义 │   │ Parser混乱 │  │ 双Registry│
    └─────────┘   └───────────┘  └─────────┘
         │              │              │
         ▼              ▼              ▼
    ┌─────────────────────────────────────────┐
    │  高优先级：立即处理，不能继续累积         │
    └─────────────────────────────────────────┘

                    低风险
                        │
         ┌──────────────┼──────────────┐
         │              │              │
    ┌────┴────┐   ┌─────┴─────┐  ┌────┴────┐
    │ G-1     │   │ F-3       │  │ E-3     │
    │ Deprecated│   │ Path检测  │  │ async bug│
    │ 未删除   │   │ 分散      │  │ (已修复) │
    └─────────┘   └───────────┘  └─────────┘
         │              │              │
         ▼              ▼              ▼
    ┌─────────────────────────────────────────┐
    │  低优先级：渐进清理                      │
    └─────────────────────────────────────────┘
```

---

## 3. 类别A：权威源头碎片化

### 问题A-1：三处Tool定义必须手动同步

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

系统中存在**三个独立的Tool定义位置**，必须手动保持同步：

| 位置 | 工具数 | 用途 |
|------|--------|------|
| `kernelone/llm/toolkit/definitions.py` (`STANDARD_TOOLS`) | 16 | LLM-facing schemas |
| `kernelone/tools/contracts.py` (`_TOOL_SPECS`) | 30+ | 完整契约+验证+别名 |
| `kernelone/agent/tools/registry.py` (`_STANDARD_TOOL_DEFINITIONS`) | 10 | Agent注册引导 |

#### 代码证据

```python
# definitions.py (LLM-facing):
STANDARD_TOOLS = [
    {"type": "function", "function": {"name": "read_file", ...}},
    {"type": "function", "function": {"name": "search_code", ...}},
    ...
]

# contracts.py (执行契约):
_TOOL_SPECS = {
    "repo_read_head": ToolSpec(canonical_name="repo_read_head", ...),
    "repo_rg": ToolSpec(canonical_name="repo_rg", ...),
    ...
}

# registry.py (Agent注册):
_STANDARD_TOOL_DEFINITIONS = [
    ToolDefinition(name="write_file", ...),
    ToolDefinition(name="edit_file", ...),
    ...
]
```

#### 症状

同一工具三个不同名字：
- `read_file` (definitions.py / LLM看到)
- `repo_read_head` (contracts.py / 执行层用)
- `read_head` (handlers / 内部实现)

#### 根因

三个模块独立演进，未建立单一的"Tool Spec Source of Truth"

#### 影响范围

- 所有工具调用流程
- 只要添加一个tool，需要改三处
- 很容易遗漏导致不一致

---

### 问题A-2：YAML和Python角色配置重复且不一致

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

| 角色 | YAML工具数 | Python工具数 | 差异 |
|------|-----------|-------------|------|
| PM | 7 | 32+ | Python >> YAML |
| Director | 17 | 20+ | 不一致 |
| QA | 8 | 11 | 不一致 |

#### 文件位置

- YAML: `polaris/cells/roles/profile/internal/config/core_roles.yaml`
- Python: `polaris/cells/roles/profile/internal/builtin_profiles.py`

#### 代码证据

```yaml
# core_roles.yaml (Director部分)
director:
  whitelist:
    - repo_read_head
    - repo_read_slice
    - ...
  allow_command_execution: true  # 但whitelist中没有execute_command!
```

```python
# builtin_profiles.py (Director部分)
"director": {
    "whitelist": [
        "repo_read_head", "repo_read_slice", ...,
        "execute_command",  # 这里有!
    ],
    "allow_command_execution": True,
}
```

#### 根因

两套配置系统独立维护，无同步机制

#### 影响范围

- 角色授权
- 安全策略执行
- 新角色添加

---

### 问题A-3：Contract层Tool Spec与LLM-facing Schema不匹配

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

LLM被告知的工具名 vs 执行时需要的工具名**不一致**

#### 代码证据

```python
# contracts.py - 执行层知道这些工具：
_TOOL_SPECS = {
    "repo_read_head": ...,  # 执行层用这个
    "repo_rg": ...,          # 执行层用这个
    "repo_tree": ...,        # 执行层用这个
}

# definitions.py - LLM看到的是这些：
STANDARD_TOOLS = [
    {"name": "read_file", ...},      # LLM看到不一样
    {"name": "search_code", ...},    # LLM看到不一样
    {"name": "grep", ...},            # LLM看到不一样
]
```

#### 症状

1. LLM说要用 `read_file`
2. 系统找不到这个工具名
3. 解析失败或调用错误工具

#### 根因

没有建立从执行层到LLM层的工具名映射机制

---

## 4. 类别B：解析器架构混乱

### 问题B-1：4层解析器并存，执行时实际只用2层

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

```
parse_tool_calls() 入口
    ├── native_function.py      ✅ 生产使用 (OpenAI/Anthropic/Gemini/Ollama/DeepSeek)
    ├── xml_based.py            ⚠️ 仅MiniMax使用
    ├── json_based.py           ✅ 非流式fallback
    ├── prompt_based.py         ❌ 标记deprecated但代码仍在
    └── tool_chain.py           ❌ 几乎不用

roles/kernel/internal/tool_call_protocol.py (Legacy)
    └── CanonicalToolCallParser  ❌ 仅用于transcript清理

domain/services/parsing.py
    └── parse_tool_calls()      ❌ 领域检查专用，与主流程脱节
```

#### 根因

演进过程中新增parser但未清理旧parser

#### 影响

- 维护负担
- 安全漏洞（HMAC验证不对称）
- 代码理解成本

---

### 问题B-2：Provider Auto-Detection逻辑不稳定

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

```python
# core.py parse_tool_calls() 的auto逻辑：
if provider == "auto":
    if result := try_gemini(response):
        return result
    if result := try_ollama(response):
        return result
    if result := try_deepseek(response):
        return result
    return []  # 如果都不匹配，返回空！
```

#### 问题

1. 哪个provider返回结果就用哪个，不一定是正确的
2. DeepSeek、Ollama格式非常相似，容易误判
3. 返回空意味着工具调用被静默丢弃

#### 根因

没有使用provider hint，而是盲目尝试所有格式

---

### 问题B-3：Argument Key别名规则各解析器不统一

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

| Parser | 支持的argument keys |
|--------|-------------------|
| `JSONToolParser` | `arguments`, `args`, `params`, `parameters` |
| `NativeFunctionCallingParser` | `arguments`, `input` |
| `PromptBasedToolParser` | `args`, `arguments`, `input`, `params`, `parameters`, `tool_input`, `action_input` |

#### 根因

没有统一的argument schema定义

---

### 问题B-4：HMAC签名验证不对称

**严重程度**: 🔴 严重
**状态**: 存在

#### 问题描述

```python
# prompt_based.py 有签名验证
# native_function.py 没有
# xml_based.py 没有

# 如果LLM返回签名的tool call，通过native路径会被拒绝
# 但如果通过prompt_based解析，可能通过签名验证
```

#### 安全风险

这种安全不对称性是漏洞

---

### 问题B-5：非流式JSON fallback是hotfix非架构

**严重程度**: 🟠 高
**状态**: 存在 (2026-03-28刚添加)

#### 问题描述

```python
# llm_caller.py 今天新增的fallback：
def _extract_tool_calls_from_text(self, text: str) -> list[ParsedToolCall]:
    # 这是为了修复"benchmark只有15%通过率"的问题

# 说明原有的native parsing对非流式响应处理不完善
# 这个fallback是hotfix，不是架构设计
```

#### 根因

没有正确的非流式解析架构，被迫打补丁

---

## 5. 类别C：Provider系统问题

### 问题C-1：两个独立的ProviderManager

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# 位置1：polaris.kernelone.llm.providers.registry.ProviderManager
# 位置2：polaris.infrastructure.llm.providers.provider_registry.ProviderManager

# 两个类有相似API但不同实现
# 通过 provider_bootstrap.py 的 inject_kernelone_provider_runtime() 桥接
```

#### 根因

kernelone和infrastructure平行演进，bootstrap是补丁

---

### 问题C-2：Provider的invoke()对multi-turn支持不完整

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# base_provider.py:
def invoke(self, prompt: str, model: str, config: dict) -> InvokeResult

# 问题：
# 1. prompt是单个string，不是ConversationState
# 2. config中需要携带messages（由adapter构建）
# 3. 如果adapter没有设置config["messages"]，provider退化为单轮

# anthropic_compat_provider.py:
adapter_messages = _CONTRACT.extract_messages({"config": config})
if adapter_messages:
    messages = adapter_messages
else:
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]  # 单轮退化！
```

---

### 问题C-3：MiniMax不使用原生tool calling

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# minimax_provider.py
# 使用 <tool_call>...</tool_call> XML标签
# 不是OpenAI的function_calling格式
# 需要StreamThinkingParser在流式处理中提取

# 其他Provider(OpenAI/Anthropic/Ollama)都支持原生格式
# MiniMax是唯一的例外
```

---

### 问题C-4：Tool Result格式各Provider不一致

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

| Provider | Tool Result格式 |
|----------|----------------|
| OpenAI | `{"role": "tool", "tool_call_id": "...", "content": "..."}` |
| Anthropic | `{"type": "tool_result", "tool_use_id": "...", "content": "..."}` |
| Ollama | `{"role": "tool", "tool_call_id": "...", "tool_name": "...", "content": "..."}` |

#### 根因

adapter统一了request格式，但result格式的解析分散在各个provider中

---

## 6. 类别D：角色策略系统问题

### 问题D-1：RoleToolGateway和PolicyLayer双执行路径

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```
执行路径1：RoleToolGateway.check_tool_permission()
    - whitelist/blacklist检查
    - 通配符支持
    - 路径遍历检测
    - 危险命令检测

执行路径2：PolicyLayer.evaluate()
    - 类似的权限检查
    - ExplorationToolPolicy(冷却机制)
    - BudgetPolicy(预算控制)
    - 不同的stall检测逻辑
```

#### 根因

两套系统独立开发，功能重叠但实现不同

---

### 问题D-2：TOOL_NAME_ALIASES导入但从未使用

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# tool_gateway.py line 71:
TOOL_ALIASES = dict(TOOL_NAME_ALIASES)  # 导入了

# 但是 check_tool_permission() 中:
requested_tool = self._normalize_tool_name(tool_name)  # 做了归一化
execution_tool = requested_tool  # 用归一化后的名字

# 问题：TOOL_ALIASES没有被用于whitelist匹配
# whitelist中的名字如果是别名，就匹配不到！
```

---

### 问题D-3：Director角色配置自相矛盾

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# builtin_profiles.py:
"allow_command_execution": True

# core_roles.yaml:
allow_command_execution: true
# 但whitelist中没有execute_command!
```

#### 根因

YAML和Python配置独立维护

---

### 问题D-4：危险命令Patterns两处独立定义

**严重程度**: 🟡 中
**状态**: 存在

#### 问题描述

```python
# RoleToolGateway._is_dangerous_command():
# 定义了一套DANGEROUS_PATTERNS

# BudgetPolicy._DANGEROUS_PATTERNS (layer.py):
# 定义了另一套DANGEROUS_PATTERNS

# 两者不完全一致
```

---

## 7. 类别E：执行器问题

### 问题E-1：Handler映射是隐式Lazy Load

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# executor/core.py:
def _load_handler_modules(self):
    from polaris.kernelone.llm.toolkit.executor.handlers import (
        command, filesystem, navigation, search, session_memory, repo
    )

# 问题：
# 1. 导入是字符串动态的，不是显式注册
# 2. 如果handler文件不存在，错误信息不清晰
# 3. 新增tool需要修改这个import + mapping逻辑
```

---

### 问题E-2：executor同时依赖两套Tool定义

**严重程度**: 🟠 高
**状态**: 存在

#### 问题描述

```python
# executor/core.py 导入：
from polaris.kernelone.llm.toolkit.definitions import STANDARD_TOOLS
from polaris.kernelone.tools.contracts import _TOOL_SPECS

# _validate_arguments() 使用 tool_def from STANDARD_TOOLS
# 但执行时用的是 _TOOL_SPECS 中的别名映射

# 结果：验证用一套名字，执行用另一套名字
```

---

### 问题E-3：StreamThinkingParser的async迭代bug

**严重程度**: 🟡 中
**状态**: ✅ 已修复 (2026-03-28)

#### 问题描述

```python
# turn_engine.py 修复前:
for visible_kind, visible_text in visible_thinking_parser.feed(visible_chunk):
    # 错误：feed()是async iterator，但用了同步for

# 修复后:
async for visible_kind, visible_text in visible_thinking_parser.feed(visible_chunk):
```

---

## 8. 类别F：规范化问题

### 问题F-1：两套argument normalization系统

**严重程度**: 🟡 中
**状态**: 存在

#### 问题描述

```python
# system 1: tool_normalization.py
normalize_tool_arguments()  # 50+参数别名映射

# system 2: contracts.py normalize_tool_args()
# 功能重叠，但实现不完全相同
```

---

### 问题F-2：normalize_tool_name()与TOOL_NAME_ALIASES不一致

**严重程度**: 🟡 中
**状态**: 存在

#### 问题描述

```python
# tool_normalization.py:
TOOL_NAME_ALIASES = {...}  # 定义了别名

def normalize_tool_name(name: str) -> str:
    return TOOL_NAME_ALIASES.get(name, name)

# 但是 contracts.py 有自己的 canonicalize_tool_name()
# 两个函数的别名映射表不同！
```

---

### 问题F-3：Path遍历检测分散

**严重程度**: 🟡 中
**状态**: 存在

#### 问题描述

```python
# tool_normalization.py: validate_tool_path_argument()
# tool_gateway.py: _check_path_traversal()  # 重复实现

# 规则可能不一致
```

---

## 9. 类别G：残留废弃问题

### 问题G-1：PromptBasedToolParser标记deprecated但未删除

**严重程度**: 🟡 中
**状态**: 存在

#### 问题描述

```python
# prompt_based.py:
# "Text protocol is deprecated for execution - kept for compatibility"
# 实际上json_based.py还在调用它用于非流式fallback
```

---

### 问题G-2：domain/services/parsing.py的parse_tool_calls独立存在

**严重程度**: 🟡 中
**状态**: 存在

#### 问题描述

```python
# domain/services/parsing.py:
def parse_tool_calls(output: str) -> list[dict[str, Any]]:
    """Parse tool-call-shaped data for legacy/domain inspection only."""

# 与主流程 toolkit/parsers/core.py 完全独立
# 代码重复，维护负担
```

---

## 10. 根因总结

```
┌─────────────────────────────────────────────────────────────────────┐
│                    根本原因：权威源头碎片化                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   Tool定义：3处独立         →  不一致                               │
│   Parser：5层并存           →  维护负担/安全漏洞                     │
│   Provider Registry：2个     →  职责不清                             │
│   Role Policy：2套           →  规则不一致                           │
│   Normalization：2套         →  行为不确定                           │
│                                                                     │
│   这些问题的共同根因：                                              │
│   → "演进过程中增量叠加，未整体重构收敛"                             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. 附录：问题索引

| ID | 类别 | 问题 | 严重程度 | 状态 |
|----|------|------|---------|------|
| A-1 | 权威源头 | 三处Tool定义必须手动同步 | 🔴 严重 | 存在 |
| A-2 | 权威源头 | YAML和Python角色配置重复且不一致 | 🔴 严重 | 存在 |
| A-3 | 权威源头 | Contract层与LLM-facing Schema不匹配 | 🔴 严重 | 存在 |
| B-1 | 解析器 | 4层Parser并存，实际只用2层 | 🔴 严重 | 存在 |
| B-2 | 解析器 | Provider Auto-Detection逻辑不稳定 | 🔴 严重 | 存在 |
| B-3 | 解析器 | Argument Key别名规则各解析器不统一 | 🔴 严重 | 存在 |
| B-4 | 解析器 | HMAC签名验证不对称 | 🔴 严重 | 存在 |
| B-5 | 解析器 | 非流式JSON fallback是hotfix非架构 | 🟠 高 | 存在 |
| C-1 | Provider | 两个独立的ProviderManager | 🟠 高 | 存在 |
| C-2 | Provider | invoke()对multi-turn支持不完整 | 🟠 高 | 存在 |
| C-3 | Provider | MiniMax不使用原生tool calling | 🟠 高 | 存在 |
| C-4 | Provider | Tool Result格式各Provider不一致 | 🟠 高 | 存在 |
| D-1 | 角色/策略 | RoleToolGateway和PolicyLayer双执行路径 | 🟠 高 | 存在 |
| D-2 | 角色/策略 | TOOL_NAME_ALIASES导入但从未使用 | 🟠 高 | 存在 |
| D-3 | 角色/策略 | Director角色配置自相矛盾 | 🟠 高 | 存在 |
| D-4 | 角色/策略 | 危险命令Patterns两处独立定义 | 🟡 中 | 存在 |
| E-1 | 执行器 | Handler映射是隐式Lazy Load | 🟠 高 | 存在 |
| E-2 | 执行器 | executor同时依赖两套Tool定义 | 🟠 高 | 存在 |
| E-3 | 执行器 | StreamThinkingParser的async迭代bug | 🟡 中 | ✅ 已修复 |
| F-1 | 规范化 | 两套argument normalization系统 | 🟡 中 | 存在 |
| F-2 | 规范化 | normalize_tool_name()与TOOL_NAME_ALIASES不一致 | 🟡 中 | 存在 |
| F-3 | 规范化 | Path遍历检测分散 | 🟡 中 | 存在 |
| G-1 | 残留废弃 | PromptBasedToolParser标记deprecated但未删除 | 🟡 中 | 存在 |
| G-2 | 残留废弃 | domain/services/parsing.py独立存在 | 🟡 中 | 存在 |

---

*审计团队*:
- 工程师A (Tooling/Executor)
- 工程师B (Provider/Adapter)
- 工程师C (Parsing/Normalization)
- 工程师D (Role/Policy)
- 工程师E (Governance/Integration)
- 工程师F (Pragmatic/Stability)

*技术总监*: Dains
*报告时间*: 2026-03-28
