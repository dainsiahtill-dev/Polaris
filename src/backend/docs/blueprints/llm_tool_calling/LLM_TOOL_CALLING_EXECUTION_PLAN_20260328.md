# LLM工具调用系统收敛执行计划

**计划版本**: v1.0
**创建时间**: 2026-03-28
**基于**: `docs/blueprints/llm_tool_calling/LLM_TOOL_CALLING_CONVERGENCE_BLUEPRINT_20260328.md`
**执行周期**: 12周 (2026-03-28 → 2026-06-20)

---

## 执行摘要

### 团队配置

| 工程师 | 代号 | 专长 | 负责Phase |
|--------|------|------|-----------|
| **工程师甲** | Platform-Infra | Platform/Infra | Phase 1 (ToolSpecRegistry) |
| **工程师乙** | Parser-Master | Parsing/Normalization | Phase 2 (Parser收敛) |
| **工程师丙** | Provider-Guru | Provider/Adapter | Phase 3 (Provider收敛) |
| **工程师丁** | Policy-Warden | Role/Policy | Phase 4 (角色策略收敛) |
| **工程师戊** | Executor-Forge | Executor/Handler | Phase 5 (执行器收敛) |
| **工程师己** | Test-Guardian | Testing/QA | 全Phase测试 + CI门禁 |

### 技术总监

**Dains** - 负责监督所有Phase执行，确保符合蓝图，验收质量

### 时间线

```
Week  1-2:  Phase 1  - ToolSpecRegistry
Week  3-4:  Phase 2  - Parser收敛
Week  5-6:  Phase 3  - Provider收敛
Week  7-8:  Phase 4  - 角色策略收敛
Week  9-10: Phase 5  - 执行器收敛
Week 11-12: 集成测试 + 回归验证
```

---

## Phase 1: 单一Tool Spec权威源头 (Week 1-2)

**负责人**: 工程师甲 (Platform-Infra)
**依赖**: 无
**目标**: 建立ToolSpecRegistry作为唯一权威源头

### 任务分解

#### Task 1.1: 创建ToolSpecRegistry基础结构

**文件**: `polaris/kernelone/tools/tool_spec_registry.py`

**实现内容**:
```python
# 1. ToolSpec dataclass
@dataclass(frozen=True)
class ToolSpec:
    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    parameters: dict[str, Any]
    categories: tuple[str, ...]
    dangerous_patterns: tuple[str, ...]
    handler_module: str
    handler_function: str

# 2. ToolSpecRegistry class
class ToolSpecRegistry:
    _specs: dict[str, ToolSpec]
    register(spec: ToolSpec)
    get(name: str) -> ToolSpec | None
    get_canonical(name: str) -> str
    generate_llm_schemas() -> list[dict]
    generate_handler_registry() -> dict[str, tuple[str, str]]
    get_by_category(category: str) -> list[ToolSpec]
```

**验收条件**:
- [ ] `ToolSpec` dataclass正确实现
- [ ] `ToolSpecRegistry` singleton正确实现
- [ ] 别名注册到同一spec
- [ ] `generate_llm_schemas()` 输出正确的OpenAI格式
- [ ] `generate_handler_registry()` 输出正确的handler映射
- [ ] 单元测试通过 (polaris/tests/unit/kernelone/tools/test_tool_spec_registry.py)

**完成标准**: 2026-04-04

---

#### Task 1.2: 迁移现有Tool定义到ToolSpecRegistry

**输入文件**:
- `polaris/kernelone/llm/toolkit/definitions.py` (16 tools)
- `polaris/kernelone/tools/contracts.py` (30+ tools)
- `polaris/kernelone/agent/tools/registry.py` (10 tools)

**迁移策略**:

```python
# scripts/migrate_tool_specs.py

# 步骤1: 读取_TOOL_SPECS作为主数据源
# 步骤2: 转换为ToolSpec格式
# 步骤3: 注册到ToolSpecRegistry
# 步骤4: 验证LLM schemas生成正确
# 步骤5: 验证handler映射正确
```

**验收条件**:
- [ ] 所有Tool定义从3处迁移到ToolSpecRegistry
- [ ] `definitions.py` 中的STANDARD_TOOLS从ToolSpecRegistry生成
- [ ] `contracts.py` 委托ToolSpecRegistry
- [ ] `registry.py` 委托ToolSpecRegistry
- [ ] 无数据丢失或不一致
- [ ] 迁移脚本可重复执行

**完成标准**: 2026-04-06

---

#### Task 1.3: 修改definitions.py使用ToolSpecRegistry

**文件**: `polaris/kernelone/llm/toolkit/definitions.py`

**修改内容**:
```python
# 修改前:
STANDARD_TOOLS = [
    {"type": "function", "function": {"name": "read_file", ...}},
    ...
]

# 修改后:
def get_standard_tools():
    return ToolSpecRegistry.generate_llm_schemas()

STANDARD_TOOLS = get_standard_tools()  # 延迟生成
```

**验收条件**:
- [ ] STANDARD_TOOLS从ToolSpecRegistry生成
- [ ] 现有调用definitions.py的代码无需修改
- [ ] LLM可正常获取tool schemas

**完成标准**: 2026-04-08

---

#### Task 1.4: 修改contracts.py使用ToolSpecRegistry

**文件**: `polaris/kernelone/tools/contracts.py`

**修改内容**:
```python
# 修改前:
_TOOL_SPECS = {...}
def normalize_tool_args(...): ...

# 修改后:
def get_tool_spec(name: str) -> ToolSpec | None:
    return ToolSpecRegistry.get(name)

def normalize_tool_args(name, args, ...):
    spec = ToolSpecRegistry.get(name)
    if spec:
        # 使用spec.parameters进行校验和归一化
        ...
```

**验收条件**:
- [ ] `contracts.py` 中的函数正确委托ToolSpecRegistry
- [ ] 归一化逻辑使用spec.parameters
- [ ] 现有调用contracts.py的代码无需修改

**完成标准**: 2026-04-10

---

#### Task 1.5: 修改registry.py使用ToolSpecRegistry

**文件**: `polaris/kernelone/agent/tools/registry.py`

**修改内容**:
```python
# 修改前:
_STANDARD_TOOL_DEFINITIONS = [...]

# 修改后:
def get_standard_tool_definitions():
    return ToolSpecRegistry.generate_handler_registry()

# 对外暴露:
STANDARD_TOOL_DEFINITIONS = get_standard_tool_definitions()
```

**验收条件**:
- [ ] registry.py从ToolSpecRegistry生成handler映射
- [ ] 现有调用registry.py的代码无需修改

**完成标准**: 2026-04-12

---

#### Task 1.6: 添加CI门禁

**文件**: `polaris/tests/unit/kernelone/tools/test_tool_spec_registry.py`

**门禁内容**:
```python
def test_tool_definitions_consistency():
    """验证definitions/contracts/registry指向同一源头"""
    schemas = get_standard_tools()
    schema_names = {s['function']['name'] for s in schemas}

    for name in schema_names:
        assert ToolSpecRegistry.get(name) is not None

def test_no_duplicate_definitions():
    """验证无重复定义"""
    ...

def test_handler_mapping_complete():
    """验证所有tool都有handler映射"""
    ...
```

**验收条件**:
- [ ] CI门禁测试存在并通过
- [ ] 门禁在git push时自动运行
- [ ] 不一致时CI失败

**完成标准**: 2026-04-14

---

### Phase 1验收

**日期**: 2026-04-14

| 验收项 | 标准 |
|--------|------|
| ToolSpecRegistry上线 | ✅ 实现完成 |
| 3处定义合并为1处 | ✅ definitions/contracts/registry都从Registry生成 |
| CI门禁通过 | ✅ 100% |
| 现有测试通过 | ✅ pytest通过 |
| 文档更新 | ✅ README更新 |

---

## Phase 2: Parser收敛 (Week 3-4)

**负责人**: 工程师乙 (Parser-Master)
**依赖**: Phase 1完成
**目标**: 4个Parser层收敛为2个

### 任务分解

#### Task 2.1: 创建CanonicalToolCallParser

**文件**: `polaris/kernelone/llm/toolkit/parsers/canonical.py`

**实现内容**:
```python
@dataclass(frozen=True)
class CanonicalToolCall:
    tool_name: str  # canonical name
    arguments: dict
    raw_format: str
    raw_data: Any

class CanonicalToolCallParser:
    def __init__(self, tool_spec_registry: ToolSpecRegistry):
        self._registry = tool_spec_registry

    def parse(self, raw, format_hint=None, allowed_tools=None) -> list[CanonicalToolCall]:
        """统一解析入口"""
        ...

    def _parse_with_hint(self, raw, hint, allowed_tools):
        """使用provider hint解析"""
        ...

    def _auto_parse(self, raw, allowed_tools):
        """自动检测格式解析"""
        ...
```

**验收条件**:
- [ ] CanonicalToolCall dataclass正确
- [ ] CanonicalToolCallParser正确处理format_hint
- [ ] format_hint优先于auto-detect
- [ ] 单元测试覆盖 > 80%

**完成标准**: 2026-04-20

---

#### Task 2.2: 统一Argument Key处理

**文件**: `polaris/kernelone/llm/toolkit/parsers/canonical.py`

**实现内容**:
```python
CANONICAL_ARGUMENT_KEYS = ["arguments", "args", "params", "parameters", "input"]

def extract_arguments(data: dict) -> dict:
    for key in CANONICAL_ARGUMENT_KEYS:
        if key in data:
            return data[key]
    return data
```

**验收条件**:
- [ ] 所有Adapter使用统一的argument key列表
- [ ] 各Parser行为一致

**完成标准**: 2026-04-20

---

#### Task 2.3: 删除prompt_based.py

**文件**: `polaris/kernelone/llm/toolkit/parsers/prompt_based.py`

**操作**: 删除文件

**验收条件**:
- [ ] 文件已删除
- [ ] 无残留引用
- [ ] 现有测试通过

**完成标准**: 2026-04-22

---

#### Task 2.4: 删除tool_chain.py

**文件**: `polaris/kernelone/llm/toolkit/parsers/tool_chain.py`

**操作**: 删除文件

**验收条件**:
- [ ] 文件已删除
- [ ] 无残留引用
- [ ] 现有测试通过

**完成标准**: 2026-04-22

---

#### Task 2.5: 合并xml_based到json_based

**文件**: `polaris/kernelone/llm/toolkit/parsers/xml_based.py`, `json_based.py`

**操作**: 合并XML解析逻辑到JSONTextAdapter

**验收条件**:
- [ ] xml_based.py已合并到json_based.py
- [ ] xml_based.py已删除
- [ ] MiniMax Provider正常工作

**完成标准**: 2026-04-24

---

#### Task 2.6: 删除domain/services/parsing.py

**文件**: `polaris/domain/services/parsing.py`

**操作**: 删除冗余的parse_tool_calls实现

**验收条件**:
- [ ] 文件已删除
- [ ] 无残留引用
- [ ] 领域检查功能正常（通过主流程）

**完成标准**: 2026-04-26

---

### Phase 2验收

**日期**: 2026-04-28

| 验收项 | 标准 |
|--------|------|
| CanonicalToolCallParser上线 | ✅ |
| prompt_based.py删除 | ✅ |
| tool_chain.py删除 | ✅ |
| domain/services/parsing.py删除 | ✅ |
| Parser测试覆盖率 | ✅ > 80% |
| 现有测试通过 | ✅ pytest通过 |

---

## Phase 3: Provider收敛 (Week 5-6)

**负责人**: 工程师丙 (Provider-Guru)
**依赖**: Phase 2完成
**目标**: 2个ProviderRegistry合并为1个，ToolResult格式统一

### 任务分解

#### Task 3.1: 创建CanonicalToolResult

**文件**: `polaris/kernelone/llm/toolkit/results.py`

**实现内容**:
```python
@dataclass(frozen=True)
class CanonicalToolResult:
    tool_name: str
    success: bool
    output: str  # 统一为string
    error: str | None
    execution_time_ms: int
    raw_result: Any

    def to_provider_native(self, provider: str) -> dict:
        """转换为provider-native格式"""
        ...
```

**验收条件**:
- [ ] CanonicalToolResult dataclass正确
- [ ] to_provider_native()正确转换OpenAI/Anthropic/Ollama格式
- [ ] 单元测试通过

**完成标准**: 2026-05-04

---

#### Task 3.2: 合并ProviderRegistry

**文件**: `polaris/kernelone/llm/providers/registry.py`, `polaris/infrastructure/llm/providers/provider_registry.py`

**实现策略**:
```python
# polaris/kernelone/llm/providers/registry.py

# 修改为委托模式:
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
        self._infra.register_provider(provider_type, provider_cls)
```

**验收条件**:
- [ ] kernelone ProviderManager委托给infrastructure
- [ ] provider_bootstrap.py可以删除或简化
- [ ] 现有Provider调用路径不变

**完成标准**: 2026-05-08

---

#### Task 3.3: 统一Tool Result格式

**文件**: `polaris/kernelone/llm/provider_adapters/`

**修改内容**:
```python
# 每个Adapter的build_tool_result_payload方法
def build_tool_result_payload(self, canonical_result: CanonicalToolResult) -> dict:
    return canonical_result.to_provider_native(self.provider_name)
```

**验收条件**:
- [ ] 所有Adapter使用CanonicalToolResult
- [ ] Tool Result格式统一
- [ ] 现有测试通过

**完成标准**: 2026-05-10

---

#### Task 3.4: MiniMax Provider原生tool calling支持

**文件**: `polaris/infrastructure/llm/providers/minimax_provider.py`

**选项A**: 让MiniMax支持原生tool calling
**选项B**: 如果MiniMax API不支持，明确标记为deprecated

**验收条件**:
- [ ] 决策已做出并执行
- [ ] 文档已更新

**完成标准**: 2026-05-12

---

#### Task 3.5: 改进invoke()契约

**文件**: `polaris/kernelone/llm/providers/base_provider.py`

**修改内容**:
```python
# 从:
def invoke(self, prompt: str, model: str, config: dict) -> InvokeResult

# 改为:
def invoke(self, messages: list[dict], model: str, config: dict) -> InvokeResult
```

**验收条件**:
- [ ] BaseProvider.invoke()接受messages而非prompt
- [ ] 所有Provider实现更新
- [ ] 向后兼容（config中可传递prompt）

**完成标准**: 2026-05-14

---

### Phase 3验收

**日期**: 2026-05-14

| 验收项 | 标准 |
|--------|------|
| CanonicalToolResult上线 | ✅ |
| ProviderRegistry合并 | ✅ |
| Tool Result格式统一 | ✅ |
| MiniMax决策已做 | ✅ |
| invoke()契约改进 | ✅ |
| 现有测试通过 | ✅ pytest通过 |

---

## Phase 4: 角色策略收敛 (Week 7-8)

**负责人**: 工程师丁 (Policy-Warden)
**依赖**: Phase 1完成
**目标**: RoleToolGateway + PolicyLayer收敛为单一RolePolicyEngine

### 任务分解

#### Task 4.1: 创建RolePolicyEngine

**文件**: `polaris/kernelone/policy/role_engine.py`

**实现内容**:
```python
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
        """统一的授权+执行"""
        auth_result = self._check_authorization(role_profile, tool_name, arguments)
        if not auth_result.allowed:
            return ExecutionResult.denied(auth_result.reason)
        return self._execute(tool_name, arguments)

    def _check_authorization(self, role_profile, tool_name, arguments):
        """统一的授权检查"""
        ...
```

**验收条件**:
- [ ] RolePolicyEngine正确实现
- [ ] 包含whitelist/blacklist/category/path检查
- [ ] 单元测试通过

**完成标准**: 2026-05-20

---

#### Task 4.2: 统一危险命令Patterns

**文件**: `polaris/kernelone/policy/dangerous_patterns.py`

**实现内容**:
```python
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r">\s*/dev/sd[a-z]",
    r"dd\s+if=.*of=/dev/",
    # ... 统一列表
]

def is_dangerous_command(command: str) -> bool:
    """统一的危险命令检测"""
    ...
```

**验收条件**:
- [ ] 危险命令Patterns统一
- [ ] RoleToolGateway和BudgetPolicy使用同一一定义
- [ ] 无重复定义

**完成标准**: 2026-05-22

---

#### Task 4.3: RoleToolGateway委托RolePolicyEngine

**文件**: `polaris/cells/roles/kernel/internal/tool_gateway.py`

**修改内容**:
```python
class RoleToolGateway:
    def __init__(self, role_policy_engine: RolePolicyEngine):
        self._engine = role_policy_engine

    def check_tool_permission(self, tool_name, tool_args, role_profile):
        return self._engine._check_authorization(role_profile, tool_name, tool_args)

    def execute_tool(self, tool_name, tool_args, role_profile):
        return self._engine.check_and_execute(role_profile, tool_name, tool_args, context)
```

**验收条件**:
- [ ] RoleToolGateway正确委托RolePolicyEngine
- [ ] 原有功能不变
- [ ] 单元测试通过

**完成标准**: 2026-05-24

---

#### Task 4.4: 消除YAML配置

**文件**: `polaris/cells/roles/profile/internal/config/core_roles.yaml`

**操作**: 删除文件

**验收条件**:
- [ ] core_roles.yaml已删除
- [ ] builtin_profiles.py作为唯一配置源
- [ ] 角色加载正常

**完成标准**: 2026-05-26

---

#### Task 4.5: 修复TOOL_NAME_ALIASES使用

**文件**: `polaris/cells/roles/kernel/internal/tool_gateway.py`

**修改内容**:
```python
# 修改授权逻辑，使用TOOL_NAME_ALIASES
def check_tool_permission(self, tool_name, tool_args, role_profile):
    # 先归一化
    normalized = self._normalize_tool_name(tool_name)
    spec = ToolSpecRegistry.get(normalized)
    if not spec:
        return ToolAuthorizationError(f"Unknown tool: {tool_name}")

    # whitelist匹配时也要检查别名
    for name in [normalized] + list(spec.aliases):
        if name in role_profile.tool_whitelist:
            return self._do_check(spec, tool_args, role_profile)

    return ToolAuthorizationError(f"Tool '{tool_name}' not in whitelist")
```

**验收条件**:
- [ ] TOOL_NAME_ALIASES正确使用
- [ ] whitelist匹配支持别名
- [ ] 单元测试通过

**完成标准**: 2026-05-28

---

### Phase 4验收

**日期**: 2026-05-28

| 验收项 | 标准 |
|--------|------|
| RolePolicyEngine上线 | ✅ |
| 危险命令Patterns统一 | ✅ |
| RoleToolGateway委托engine | ✅ |
| YAML配置删除 | ✅ |
| TOOL_NAME_ALIASES正确使用 | ✅ |
| 现有测试通过 | ✅ pytest通过 |

---

## Phase 5: 执行器收敛 (Week 9-10)

**负责人**: 工程师戊 (Executor-Forge)
**依赖**: Phase 1完成
**目标**: Handler显式注册，executor单依赖ToolSpecRegistry

### 任务分解

#### Task 5.1: 创建ToolHandlerRegistry

**文件**: `polaris/kernelone/llm/toolkit/executor/registry.py`

**实现内容**:
```python
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
```

**验收条件**:
- [ ] ToolHandlerRegistry正确实现
- [ ] register/get方法正确
- [ ] 单元测试通过

**完成标准**: 2026-06-03

---

#### Task 5.2: Handler显式注册

**文件**: `polaris/kernelone/llm/toolkit/executor/handlers/*.py`

**修改内容**:
```python
# repo.py
from polaris.kernelone.llm.toolkit.executor.registry import ToolHandlerRegistry

def _handle_repo_read_head(file: str, n: int = 100, **kwargs):
    ...

ToolHandlerRegistry.register("repo_read_head", _handle_repo_read_head)
```

**验收条件**:
- [ ] 所有handler模块使用显式注册
- [ ] 无隐式lazy load
- [ ] CI检查无遗漏注册

**完成标准**: 2026-06-07

---

#### Task 5.3: 修改executor/core.py

**文件**: `polaris/kernelone/llm/toolkit/executor/core.py`

**修改内容**:
```python
class AgentAccelToolExecutor:
    def __init__(self, tool_spec_registry: ToolSpecRegistry):
        self._registry = tool_spec_registry
        # 不再导入definitions和contracts

    def execute(self, tool_name: str, arguments: dict):
        spec = self._registry.get(tool_name)
        if not spec:
            raise ToolNotFoundError(tool_name)

        handler = ToolHandlerRegistry.get(tool_name)
        if not handler:
            raise HandlerNotFoundError(tool_name)

        return handler(**arguments)
```

**验收条件**:
- [ ] executor只依赖ToolSpecRegistry
- [ ] 不再依赖definitions.py和contracts.py
- [ ] 单元测试通过

**完成标准**: 2026-06-09

---

### Phase 5验收

**日期**: 2026-06-10

| 验收项 | 标准 |
|--------|------|
| ToolHandlerRegistry上线 | ✅ |
| Handler显式注册完成 | ✅ |
| executor单依赖Registry | ✅ |
| 现有测试通过 | ✅ pytest通过 |

---

## Phase 6: 集成测试与回归验证 (Week 11-12)

**负责人**: 工程师己 (Test-Guardian)
**依赖**: Phase 1-5全部完成
**目标**: 全系统集成测试，回归验证

### 任务分解

#### Task 6.1: 端到端集成测试

**文件**: `polaris/tests/integration/llm_tool_calling/`

**测试内容**:
```python
# test_end_to_end_tool_calling.py

def test_openai_native_tool_call_flow():
    """OpenAI原生tool calling端到端"""
    ...

def test_anthropic_native_tool_call_flow():
    """Anthropic原生tool calling端到端"""
    ...

def test_fallback_json_tool_call_flow():
    """JSON fallback端到端"""
    ...

def test_role_authorization_flow():
    """角色授权端到端"""
    ...

def test_handler_execution_flow():
    """Handler执行端到端"""
    ...
```

**验收条件**:
- [ ] 端到端测试覆盖所有主要场景
- [ ] 测试通过率 100%

**完成标准**: 2026-06-15

---

#### Task 6.2: 回归测试套件

**文件**: `polaris/tests/regression/`

**执行内容**:
```bash
# 运行所有现有测试
pytest polaris/tests/ -v --tb=short

# 确保无回归
```

**验收条件**:
- [ ] 所有现有测试通过
- [ ] 无回归问题

**完成标准**: 2026-06-17

---

#### Task 6.3: 性能基准测试

**文件**: `polaris/tests/benchmark/llm_tool_calling/`

**测试内容**:
```python
def test_parser_performance():
    """Parser性能基准"""
    ...

def test_provider_performance():
    """Provider性能基准"""
    ...

def test_end_to_end_latency():
    """端到端延迟基准"""
    ...
```

**验收条件**:
- [ ] 性能无退化
- [ ] 收敛后性能改善（如有）

**完成标准**: 2026-06-19

---

#### Task 6.4: 文档更新

**文件**: 相关文档

**更新内容**:
- [ ] `docs/KERNELONE_ARCHITECTURE_SPEC.md` 更新
- [ ] `docs/tools/TOOL_CALLING_PROTOCOL.md` 更新
- [ ] 代码注释更新

**完成标准**: 2026-06-20

---

## 最终验收 (2026-06-20)

### 验收清单

| 维度 | 指标 | 目标 | 实际 | 状态 |
|------|------|------|------|------|
| Tool定义 | 源头数量 | 1 | 待验证 | ⏳ |
| Parser层 | Parser层数 | 2 | 待验证 | ⏳ |
| ProviderRegistry | 数量 | 1 | 待验证 | ⏳ |
| Policy路径 | 数量 | 1 | 待验证 | ⏳ |
| 角色配置 | 系统数 | 1 | 待验证 | ⏳ |
| 测试覆盖 | 覆盖率 | >80% | 待验证 | ⏳ |
| 性能 | 无退化 | 0%退化 | 待验证 | ⏳ |

### 签署

```
技术总监: Dains
日期: 2026-06-20

确认所有Phase已完成并验收通过 □
```

---

## 风险与缓解

| Phase | 风险 | 缓解措施 | 负责人 |
|-------|------|---------|--------|
| Phase 1 | Tool定义迁移遗漏 | CI门禁 | 工程师甲 |
| Phase 2 | Parser行为变化 | 增量替换 | 工程师乙 |
| Phase 3 | Provider注册变化 | API兼容 | 工程师丙 |
| Phase 4 | 策略变化影响角色 | 保留旧路径 | 工程师丁 |
| Phase 5 | Handler注册遗漏 | CI检查 | 工程师戊 |
| Phase 6 | 测试覆盖不足 | 专项测试 | 工程师己 |

---

## 沟通机制

| 会议 | 频率 | 参与者 | 内容 |
|------|------|--------|------|
| Standup | 每日 | 所有工程师 | 进度阻塞 |
| Phase Review | 每2周 | 所有工程师 + 总监 | Phase验收 |
| 风险升级 | 随时 | 需要时 | 问题升级 |

---

*计划版本*: v1.0
*技术总监*: Dains
*创建时间*: 2026-03-28
*最后更新*: 2026-03-28
