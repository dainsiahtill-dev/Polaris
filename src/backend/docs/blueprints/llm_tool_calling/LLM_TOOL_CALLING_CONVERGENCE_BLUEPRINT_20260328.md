# LLM工具调用系统收敛蓝图

**文档版本**: v1.0
**创建时间**: 2026-03-28
**基于**: `docs/audit/llm_tool_calling/MASTER_AUDIT_REPORT_20260328.md`
**目标**: 解决24个LLM工具调用系统问题，建立单一权威源头

---

## 目录

1. [愿景与目标](#1-愿景与目标)
2. [当前架构](#2-当前架构)
3. [目标架构](#3-目标架构)
4. [Phase 1: 单一Tool Spec权威源头](#4-phase-1-单一tool-spec权威源头)
5. [Phase 2: Parser收敛](#5-phase-2-parser收敛)
6. [Phase 3: Provider收敛](#6-phase-3-provider收敛)
7. [Phase 4: 角色策略收敛](#7-phase-4-角色策略收敛)
8. [Phase 5: 执行器收敛](#8-phase-5-执行器收敛)
9. [迁移验证](#9-迁移验证)

---

## 1. 愿景与目标

### 1.1 核心愿景

```
"一个工具定义、一种解析路径、一个Provider注册表、一套授权策略"
```

### 1.2 量化目标

| 指标 | 当前 | 目标 |
|------|------|------|
| Tool定义位置数 | 3 | 1 |
| Parser层数 | 5 | 2 |
| ProviderRegistry数 | 2 | 1 |
| Policy执行路径数 | 2 | 1 |
| Normalization系统数 | 2 | 1 |
| 角色配置系统数 | 2 | 1 |

### 1.3 约束条件

1. **向后兼容**: 现有API不能破坏
2. **渐进迁移**: 不能一次性大爆炸重构
3. **可回滚**: 每个Phase可独立回滚
4. **零停机**: 迁移期间功能正常

---

## 2. 当前架构

### 2.1 当前Tool定义分布

```
┌─────────────────────────────────────────────────────────────────┐
│                     Tool定义碎片化现状                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  LLM-facing (definitions.py)     ←──16 tools──→   LLM看到这些   │
│           ↓                                                   │
│           ≠ (名字不匹配)                                        │
│           ↓                                                   │
│  执行契约 (contracts.py)          ←──30+ tools──→  执行层用这些 │
│           ↓                                                   │
│           ≠ (另一套名字)                                        │
│           ↓                                                   │
│  Agent注册 (registry.py)          ←──10 tools──→   Agent引导  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 当前Parser分布

```
┌─────────────────────────────────────────────────────────────────┐
│                     Parser碎片化现状                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  StreamingPatchBuffer                                          │
│       ↓                                                        │
│  StreamThinkingParser                                          │
│       ↓                                                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  parse_tool_calls() 入口                                 │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │  native_function.py   ←── OpenAI/Anthropic/Gemini...   │   │
│  │  xml_based.py         ←── MiniMax                       │   │
│  │  json_based.py        ←── 非流式fallback (今天新增!)     │   │
│  │  prompt_based.py      ←── DEPRECATED但未删除            │   │
│  │  tool_chain.py        ←── 几乎不用                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 当前Provider架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     Provider双注册表                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  kernelone/llm/providers/           infrastructure/llm/providers/│
│  ┌─────────────────────────┐         ┌─────────────────────────┐│
│  │ ProviderManager         │  ←──→   │ ProviderManager         ││
│  │ (registry.py)           │ bridge  │ (provider_registry.py)  ││
│  └─────────────────────────┘         └─────────────────────────┘│
│           ↑                                        ↑           │
│           │                                        │           │
│    ProviderAdapter                            ProviderAdapter    │
│    (kernelone内部)                            (业务实现)          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 目标架构

### 3.1 单一Tool Spec权威源头

```
┌─────────────────────────────────────────────────────────────────┐
│              单一Tool Spec权威源头（Phase 1完成后）               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              ToolSpecRegistry (NEW)                    │    │
│  │              位置: kernelone/tools/spec.py              │    │
│  │              单一事实来源                                │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  canonical_name: str                                    │    │
│  │  aliases: tuple[str, ...]                              │    │
│  │  description: str                                      │    │
│  │  parameters: JSONSchema                                │    │
│  │  categories: tuple[str, ...]                           │    │
│  │  dangerous_patterns: tuple[str, ...]                   │    │
│  │  handler: str  # handler模块路径                        │    │
│  └─────────────────────────────────────────────────────────┘    │
│            │                                                   │
│            │  generate_llm_schema()                           │
│            ↓                                                   │
│  ┌─────────────────┐                                          │
│  │  definitions.py │  ←── 自动生成，不再手动维护              │
│  │  (16 tools)     │                                          │
│  └─────────────────┘                                          │
│                                                                 │
│  ┌─────────────────┐                                          │
│  │  contracts.py   │  ←── 委托ToolSpecRegistry，不再独立定义   │
│  │  (30+ tools)    │                                          │
│  └─────────────────┘                                          │
│                                                                 │
│  ┌─────────────────┐                                          │
│  │  registry.py    │  ←── 从ToolSpecRegistry生成              │
│  │  (10 tools)     │                                          │
│  └─────────────────┘                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Parser收敛架构

```
┌─────────────────────────────────────────────────────────────────┐
│              Parser收敛架构（Phase 2完成后）                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           CanonicalToolCallParser (统一入口)            │    │
│  │           位置: kernelone/llm/toolkit/parsers/         │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  输入: raw (Any)                                       │    │
│  │  format_hint: str | None  ← provider hint优先           │    │
│  │  allowed_tools: list[str] | None                       │    │
│  │  输出: list[CanonicalToolCall]                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│            │                                                   │
│            │  委托给                                           │
│            ↓                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           FormatAdapters (格式适配器)                   │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  OpenAIAdapter      ←── native_function.py (保留)       │    │
│  │  AnthropicAdapter   ←── native_function.py (保留)       │    │
│  │  GeminiAdapter      ←── native_function.py (保留)       │    │
│  │  OllamaAdapter     ←── native_function.py (保留)       │    │
│  │  JSONTextAdapter   ←── json_based.py (保留)            │    │
│  │  XMLTextAdapter    ←── xml_based.py (合并到JSONText)   │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  ⚠️ DELETE: prompt_based.py (deprecated)              │    │
│  │  ⚠️ DELETE: tool_chain.py (unused)                    │    │
│  │  ⚠️ DELETE: domain/services/parsing.py (redundant)    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 Provider收敛架构

```
┌─────────────────────────────────────────────────────────────────┐
│              Provider收敛架构（Phase 3完成后）                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           Single ProviderRegistry                       │    │
│  │           位置: infrastructure/llm/providers/           │    │
│  │           消除: kernelone/llm/providers/registry.py    │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  kernelone通过注入使用infrastructure的Registry         │    │
│  │  消除双注册表                                            │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           CanonicalToolResult                           │    │
│  │           位置: kernelone/llm/toolkit/results.py       │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  tool_name: str                                        │    │
│  │  success: bool                                        │    │
│  │  output: str  ← 统一为string                           │    │
│  │  error: str | None                                    │    │
│  │  execution_time_ms: int                               │    │
│  │  raw_result: Any                                      │    │
│  └─────────────────────────────────────────────────────────┘    │
│            │                                                   │
│            │  ProviderAdapter.build_tool_result_payload()      │
│            ↓                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           Provider-native格式                          │    │
│  │  OpenAI: {"role": "tool", "tool_call_id": ...}        │    │
│  │  Anthropic: {"type": "tool_result", "tool_use_id": ...} │   │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 角色策略收敛架构

```
┌─────────────────────────────────────────────────────────────────┐
│              角色策略收敛架构（Phase 4完成后）                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           Single RolePolicyEngine                       │    │
│  │           位置: kernelone/policy/role_engine.py (NEW)  │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  唯一策略执行点                                         │    │
│  │  消除: RoleToolGateway 和 PolicyLayer 双路径           │    │
│  └─────────────────────────────────────────────────────────┘    │
│            │                                                   │
│            │  委托                                             │
│            ↓                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           ToolSpecRegistry (共享)                       │    │
│  │  用于: canonical_name查表、category权限检查              │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           Single RoleProfile Source                     │    │
│  │           位置: builtin_profiles.py (保留)              │    │
│  │           删除: core_roles.yaml                         │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  name: str                                             │    │
│  │  tool_whitelist: tuple[str, ...]  ← canonical names   │    │
│  │  tool_blacklist: tuple[str, ...]                       │    │
│  │  category_permissions: dict[str, bool]                  │    │
│  │  max_tool_calls_per_turn: int                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Phase 1: 单一Tool Spec权威源头

### 4.1 目标

```
当前: 3处独立Tool定义
目标: 1处单一权威源头
```

### 4.2 新建文件

```
polaris/kernelone/tools/spec.py
polaris/kernelone/tools/tool_spec_registry.py
```

### 4.3 ToolSpecRegistry设计

```python
# polaris/kernelone/tools/tool_spec_registry.py

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class ToolSpec:
    """单一权威Tool定义"""
    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    parameters: dict[str, Any]  # JSON Schema
    categories: tuple[str, ...]  # code_write, command_execution, file_delete, read_only
    dangerous_patterns: tuple[str, ...]
    handler_module: str
    handler_function: str


class ToolSpecRegistry:
    """
    单一Source of Truth for所有Tool定义

    用法:
        # 注册工具
        ToolSpecRegistry.register(ToolSpec(...))

        # 查询工具
        spec = ToolSpecRegistry.get("repo_read_head")
        for alias in spec.aliases:
            spec = ToolSpecRegistry.get(alias)

        # 生成LLM schemas
        schemas = ToolSpecRegistry.generate_llm_schemas()

        # 生成executor注册表
        handlers = ToolSpecRegistry.generate_handler_registry()
    """
    _specs: dict[str, ToolSpec] = field(default_factory=dict)

    @classmethod
    def register(cls, spec: ToolSpec) -> None:
        if spec.canonical_name in cls._specs:
            raise ValueError(f"Duplicate tool: {spec.canonical_name}")
        cls._specs[spec.canonical_name] = spec
        # 同时注册别名
        for alias in spec.aliases:
            if alias in cls._specs:
                raise ValueError(f"Duplicate alias: {alias}")
            cls._specs[alias] = spec

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        return cls._specs.get(name)

    @classmethod
    def get_canonical(cls, name: str) -> str:
        spec = cls._specs.get(name)
        return spec.canonical_name if spec else name

    @classmethod
    def generate_llm_schemas(cls) -> list[dict[str, Any]]:
        """生成LLM-facing的tool schemas"""
        schemas = []
        for spec in cls._specs.values():
            if spec.canonical_name == spec.aliases[0] if spec.aliases else True:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": spec.canonical_name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    }
                })
        return schemas

    @classmethod
    def generate_handler_registry(cls) -> dict[str, tuple[str, str]]:
        """生成 handler_module, handler_function 的映射"""
        return {
            spec.canonical_name: (spec.handler_module, spec.handler_function)
            for spec in cls._specs.values()
            if spec.canonical_name == (spec.aliases[0] if spec.aliases else spec.canonical_name)
        }

    @classmethod
    def get_by_category(cls, category: str) -> list[ToolSpec]:
        return [s for s in cls._specs.values() if category in s.categories]
```

### 4.4 迁移策略

#### 步骤1: 创建ToolSpecRegistry

```python
# polaris/kernelone/tools/tool_spec_registry.py
# 实现上述代码
```

#### 步骤2: 从现有contracts.py迁移定义

```python
# scripts/migrate_tool_specs.py
# 读取 _TOOL_SPECS
# 转换为 ToolSpec 格式
# 注册到 ToolSpecRegistry
```

#### 步骤3: 修改definitions.py

```python
# polaris/kernelone/llm/toolkit/definitions.py

# 修改前:
STANDARD_TOOLS = [...]

# 修改后:
def get_standard_tools():
    return ToolSpecRegistry.generate_llm_schemas()

STANDARD_TOOLS = get_standard_tools()  # 延迟生成
```

#### 步骤4: 修改contracts.py

```python
# polaris/kernelone/tools/contracts.py

# 修改前:
_TOOL_SPECS = {...}
def normalize_tool_args(...): ...

# 修改后:
def get_tool_spec(name: str) -> ToolSpec | None:
    return ToolSpecRegistry.get(name)

def normalize_tool_args(...):
    spec = ToolSpecRegistry.get(name)
    if spec:
        # 使用spec.parameters进行校验
        ...
```

#### 步骤5: 修改registry.py

```python
# polaris/kernelone/agent/tools/registry.py

# 修改前:
_STANDARD_TOOL_DEFINITIONS = [...]

# 修改后:
def get_standard_tool_definitions():
    return ToolSpecRegistry.generate_handler_registry()
```

### 4.5 验收条件

- [ ] `ToolSpecRegistry` 类实现完成
- [ ] 所有Tool定义从3处迁移到1处
- [ ] `definitions.py` 从`ToolSpecRegistry`生成
- [ ] `contracts.py` 委托`ToolSpecRegistry`
- [ ] `registry.py` 委托`ToolSpecRegistry`
- [ ] 现有测试全部通过
- [ ] 新增CI检查：Tool定义一致性

---

## 5. Phase 2: Parser收敛

### 5.1 目标

```
当前: 5个Parser层
目标: 2个 (native + JSONText)
```

### 5.2 迁移任务

#### 任务2.1: 创建CanonicalToolCallParser

```python
# polaris/kernelone/llm/toolkit/parsers/canonical.py

@dataclass(frozen=True)
class CanonicalToolCall:
    tool_name: str  # canonical name (from ToolSpecRegistry)
    arguments: dict
    raw_format: str  # 调试用
    raw_data: Any   # 审计用


class CanonicalToolCallParser:
    """
    统一Parser入口
    """
    def __init__(self, tool_spec_registry: ToolSpecRegistry):
        self._registry = tool_spec_registry

    def parse(
        self,
        raw: Any,
        format_hint: str | None = None,
        allowed_tools: list[str] | None = None
    ) -> list[CanonicalToolCall]:
        """
        统一解析入口
        """
        if format_hint:
            return self._parse_with_hint(raw, format_hint, allowed_tools)
        return self._auto_parse(raw, allowed_tools)

    def _parse_with_hint(self, raw, hint, allowed_tools):
        adapters = {
            "openai": OpenAIAdapter(),
            "anthropic": AnthropicAdapter(),
            "gemini": GeminiAdapter(),
            "ollama": OllamaAdapter(),
            "json_text": JSONTextAdapter(),
        }
        adapter = adapters.get(hint.lower())
        if not adapter:
            raise ValueError(f"Unknown format hint: {hint}")
        return adapter.parse(raw, self._registry, allowed_tools)
```

#### 任务2.2: 统一Argument Key处理

```python
# 所有Adapter统一使用:
CANONICAL_ARGUMENT_KEYS = ["arguments", "args", "params", "parameters", "input"]

def extract_arguments(data: dict) -> dict:
    for key in CANONICAL_ARGUMENT_KEYS:
        if key in data:
            return data[key]
    return data  # fallback: 返回原dict
```

#### 任务2.3: 删除deprecated Parser

```bash
# 删除以下文件:
rm polaris/kernelone/llm/toolkit/parsers/prompt_based.py
rm polaris/kernelone/llm/toolkit/parsers/tool_chain.py
rm polaris/domain/services/parsing.py

# 合并xml_based到json_based
# xml格式和json格式都是文本解析，逻辑可以合并
```

#### 任务2.4: 统一provider hint优先

```python
# 修改parse_tool_calls()
def parse_tool_calls(
    text: str | None = None,
    tool_calls: list | None = None,
    response: dict | None = None,
    provider: str = "auto",  # 改为provider hint
    allowed_tool_names=None,
) -> list[ParsedToolCall]:
    if provider != "auto":
        return CanonicalToolCallParser(...).parse(
            raw=tool_calls or response,
            format_hint=provider,  # 使用hint而非auto-detect
            allowed_tools=allowed_tool_names
        )
    # auto模式保持现有逻辑，但使用CanonicalToolCallParser
    ...
```

### 5.3 验收条件

- [ ] `CanonicalToolCallParser` 实现完成
- [ ] `prompt_based.py` 删除
- [ ] `tool_chain.py` 删除
- [ ] `domain/services/parsing.py` 删除
- [ ] Argument key统一
- [ ] Provider hint优先于auto-detect
- [ ] 现有测试全部通过
- [ ] 新增Parser测试覆盖率 > 80%

---

## 6. Phase 3: Provider收敛

### 6.1 目标

```
当前: 2个ProviderRegistry
目标: 1个ProviderRegistry + CanonicalToolResult
```

### 6.2 迁移任务

#### 任务3.1: 创建CanonicalToolResult

```python
# polaris/kernelone/llm/toolkit/results.py

@dataclass(frozen=True)
class CanonicalToolResult:
    """统一Tool执行结果格式"""
    tool_name: str
    success: bool
    output: str  # 统一为string
    error: str | None
    execution_time_ms: int
    raw_result: Any

    def to_provider_native(self, provider: str) -> dict:
        """转换为provider-native格式"""
        adapters = {
            "openai": OpenAIResultAdapter(),
            "anthropic": AnthropicResultAdapter(),
            "ollama": OllamaResultAdapter(),
        }
        return adapters[provider].format(self)
```

#### 任务3.2: 消除kernelone ProviderRegistry

```python
# polaris/kernelone/llm/providers/registry.py

# 修改为:
class ProviderManager:
    """
    委托给infrastructure的ProviderManager
    消除双注册表
    """
    def __init__(self, infra_manager):
        self._infra = infra_manager

    def get_provider_instance(self, provider_type: str):
        return self._infra.get_provider_instance(provider_type)

    def register_provider(self, provider_type: str, provider_cls):
        # 记录到infra
        self._infra.register_provider(provider_type, provider_cls)
```

#### 任务3.3: 统一invoke()契约

```python
# polaris/kernelone/llm/providers/base_provider.py

class BaseProvider(ABC):
    def invoke(
        self,
        messages: list[dict],  # 改为ConversationState-like
        model: str,
        config: dict
    ) -> InvokeResult:
        # 不再接受单个prompt string
        # 要求调用者提供完整的messages
```

### 6.3 验收条件

- [ ] `CanonicalToolResult` 实现完成
- [ ] `ProviderManager` 合并完成
- [ ] MiniMax Provider支持原生tool calling（或明确deprecated）
- [ ] Tool Result格式统一
- [ ] invoke()契约改进
- [ ] 现有测试全部通过

---

## 7. Phase 4: 角色策略收敛

### 7.1 目标

```
当前: RoleToolGateway + PolicyLayer双路径
目标: Single RolePolicyEngine
```

### 7.2 迁移任务

#### 任务4.1: 创建RolePolicyEngine

```python
# polaris/kernelone/policy/role_engine.py

class RolePolicyEngine:
    """
    唯一角色策略执行点
    """
    def __init__(self, tool_spec_registry: ToolSpecRegistry):
        self._registry = tool_spec_registry

    def check_and_execute(
        self,
        role_profile: RoleProfile,
        tool_name: str,
        arguments: dict,
        context: ExecutionContext
    ) -> ExecutionResult:
        """
        1. canonical_name查表
        2. whitelist/blacklist检查
        3. category权限检查
        4. 路径遍历检测
        5. 危险命令检测
        6. 执行tool
        """
        # 实现统一的授权+执行逻辑
        ...

    def _check_authorization(
        self,
        role_profile: RoleProfile,
        spec: ToolSpec,
        arguments: dict
    ) -> AuthorizationResult:
        """统一的授权检查"""
        # 1. blacklist检查
        if tool_name in role_profile.tool_blacklist:
            return AuthorizationResult.denied("blacklist")

        # 2. whitelist检查 (支持通配符)
        if role_profile.tool_whitelist:
            if not self._match_whitelist(tool_name, role_profile.tool_whitelist):
                return AuthorizationResult.denied("whitelist")

        # 3. category权限检查
        for category in spec.categories:
            if category in ("code_write", "command_execution", "file_delete"):
                if not role_profile.category_permissions.get(category):
                    return AuthorizationResult.denied(f"category:{category}")

        return AuthorizationResult.allowed()
```

#### 任务4.2: RoleToolGateway委托RolePolicyEngine

```python
# polaris/cells/roles/kernel/internal/tool_gateway.py

class RoleToolGateway:
    def __init__(self, role_policy_engine: RolePolicyEngine):
        self._engine = role_policy_engine  # 委托而非独立实现

    def check_tool_permission(self, tool_name, tool_args, role_profile):
        # 委托给engine
        return self._engine.check_authorization(role_profile, tool_name, tool_args)

    def execute_tool(self, tool_name, tool_args, role_profile):
        # 委托给engine
        return self._engine.check_and_execute(role_profile, tool_name, tool_args, context)
```

#### 任务4.3: 消除YAML配置

```python
# polaris/cells/roles/profile/internal/config/core_roles.yaml
# → 删除

# polaris/cells/roles/profile/internal/builtin_profiles.py
# → 保留，作为唯一角色配置源
```

#### 任务4.4: 统一危险命令Patterns

```python
# polaris/kernelone/policy/dangerous_patterns.py (NEW)

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",           # 递归删除根目录
    r">\s*/dev/sd[a-z]",       # 直接写磁盘设备
    r"dd\s+if=.*of=/dev/",     # 直接写设备
    # ... 统一的危险命令模式
]

def is_dangerous_command(command: str) -> bool:
    """统一的危险命令检测"""
    import re
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return True
    return False
```

### 7.3 验收条件

- [ ] `RolePolicyEngine` 实现完成
- [ ] `RoleToolGateway` 委托engine
- [ ] `PolicyLayer` 保留（如果需要向后兼容）或删除
- [ ] `core_roles.yaml` 删除
- [ ] 危险命令Patterns统一
- [ ] `TOOL_NAME_ALIASES` 正确使用或删除
- [ ] 现有测试全部通过

---

## 8. Phase 5: 执行器收敛

### 8.1 目标

```
当前: Handler隐式lazy load + executor依赖两套Tool定义
目标: Handler显式注册 + executor单依赖ToolSpecRegistry
```

### 8.2 迁移任务

#### 任务5.1: Handler显式注册

```python
# polaris/kernelone/llm/toolkit/executor/registry.py

class ToolHandlerRegistry:
    """
    显式Handler注册表
    """
    _handlers: dict[str, Callable] = {}

    @classmethod
    def register(cls, tool_name: str, handler: Callable):
        cls._handlers[tool_name] = handler

    @classmethod
    def get(cls, tool_name: str) -> Callable | None:
        return cls._handlers.get(tool_name)


# polaris/kernelone/llm/toolkit/executor/handlers/repo.py

from polaris.kernelone.llm.toolkit.executor.registry import ToolHandlerRegistry

def _handle_repo_read_head(file: str, n: int = 100, **kwargs):
    ...

# 显式注册
ToolHandlerRegistry.register("repo_read_head", _handle_repo_read_head)
ToolHandlerRegistry.register("repo_rg", _handle_repo_rg)
# ...
```

#### 任务5.2: 修改executor/core.py

```python
# polaris/kernelone/llm/toolkit/executor/core.py

class AgentAccelToolExecutor:
    def __init__(self, tool_spec_registry: ToolSpecRegistry):
        self._registry = tool_spec_registry
        # 不再需要导入definitions和contracts

    def _load_handler_modules(self):
        # 不再需要lazy import
        # handler已在模块级别注册到ToolHandlerRegistry
        pass

    def execute(self, tool_name: str, arguments: dict):
        # 使用ToolSpecRegistry验证参数
        spec = self._registry.get(tool_name)
        if not spec:
            raise ToolNotFoundError(tool_name)

        # 使用ToolHandlerRegistry获取handler
        handler = ToolHandlerRegistry.get(tool_name)
        if not handler:
            raise HandlerNotFoundError(tool_name)

        return handler(**arguments)
```

### 8.3 验收条件

- [ ] `ToolHandlerRegistry` 实现完成
- [ ] Handler显式注册完成
- [ ] executor只依赖`ToolSpecRegistry`
- [ ] 现有测试全部通过

---

## 9. 迁移验证

### 9.1 CI门禁

```yaml
# .github/workflows/llm-tool-convergence.yml

name: LLM Tool Convergence

on:
  push:
    paths:
      - 'polaris/kernelone/llm/**'
      - 'polaris/kernelone/tools/**'
      - 'polaris/cells/roles/kernel/**'

jobs:
  consistency-check:
    runs-on: ubuntu-latest
    steps:
      - name: Check Tool Spec Consistency
        run: |
          python -c "
            from polaris.kernelone.tools.tool_spec_registry import ToolSpecRegistry
            from polaris.kernelone.llm.toolkit.definitions import STANDARD_TOOLS
            from polaris.kernelone.tools.contracts import _TOOL_SPECS

            # 检查definitions中的工具是否都在Registry中
            for tool in STANDARD_TOOLS:
                name = tool['function']['name']
                spec = ToolSpecRegistry.get(name)
                if not spec:
                    raise Exception(f'{name} not in Registry')

            # 检查contracts中的工具是否都在Registry中
            for name in _TOOL_SPECS:
                spec = ToolSpecRegistry.get(name)
                if not spec:
                    raise Exception(f'{name} not in Registry')

            print('All tool definitions consistent!')
          "

  parser-tests:
    runs-on: ubuntu-latest
    steps:
      - name: Run Parser Tests
        run: |
          pytest polaris/tests/unit/kernelone/llm/toolkit/parsers/ -v --cov

  role-policy-tests:
    runs-on: ubuntu-latest
    steps:
      - name: Run Role Policy Tests
        run: |
          pytest polaris/tests/unit/kernelone/policy/ -v
```

### 9.2 回归测试清单

| 测试项 | 验证内容 |
|--------|---------|
| Tool定义一致性 | definitions/contracts/registry指向同一源头 |
| Parser输出 | 所有Provider格式正确解析 |
| Provider结果 | CanonicalToolResult格式正确 |
| 角色授权 | whitelist/blacklist/category正确执行 |
| Handler执行 | 所有tool正确执行 |
| 路径安全 | 路径遍历攻击被阻止 |
| 危险命令 | 危险命令被阻止 |

---

## 10. 风险与缓解

| Phase | 风险 | 缓解措施 |
|-------|------|---------|
| Phase 1 | Tool定义迁移遗漏 | CI门禁检查一致性 |
| Phase 2 | Parser行为变化影响LLM | 增量替换，保持兼容 |
| Phase 3 | Provider注册变化影响调用 | 保持API兼容 |
| Phase 4 | 策略变化影响角色行为 | 保留旧路径，逐步迁移 |
| Phase 5 | Handler注册遗漏 | 强制显式注册，CI检查 |

---

## 11. 里程碑

| 里程碑 | 目标日期 | 验收标准 |
|--------|---------|---------|
| M1: Phase 1完成 | +2周 | ToolSpecRegistry上线，CI门禁通过 |
| M2: Phase 2完成 | +4周 | Parser收敛，测试覆盖>80% |
| M3: Phase 3完成 | +6周 | ProviderRegistry合并 |
| M4: Phase 4完成 | +8周 | 角色策略统一 |
| M5: Phase 5完成 | +10周 | Handler显式注册 |
| M6: 全系统验证 | +12周 | 所有测试通过，性能无退化 |

---

*蓝图版本*: v1.0
*技术总监*: Dains
*创建时间*: 2026-03-28
