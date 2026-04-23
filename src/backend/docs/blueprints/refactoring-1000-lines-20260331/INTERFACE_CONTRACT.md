# 接口契约

**版本**: 2026-03-31
**状态**: Draft
**负责人**: E7 (Integration Architect)

---

## 1. 概述

本文档定义重构后各模块间的接口契约，确保：
1. 向后兼容性（通过 `__init__.py` 重导出）
2. 模块边界清晰
3. 依赖方向正确（无循环依赖）
4. 接口稳定性

---

## 2. 模块接口定义

### 2.1 turn_engine 模块

**路径**: `polaris/cells/roles/kernel/internal/turn_engine/`

**职责**: 统一角色执行循环引擎

#### 公共接口

```python
from polaris.cells.roles.kernel.internal.turn_engine import (
    TurnEngine,
    TurnEngineConfig,
    AssistantTurnArtifacts,
    SafetyState,
)
```

#### 类签名

```python
@dataclass
class TurnEngineConfig:
    max_turns: int = 64
    max_total_tool_calls: int = 64
    max_stall_cycles: int = 2
    max_wall_time_seconds: int = 900
    enable_streaming: bool = True

    @classmethod
    def from_env(cls) -> TurnEngineConfig: ...

@dataclass(frozen=True)
class AssistantTurnArtifacts:
    raw_content: str
    clean_content: str
    thinking: str | None = None
    native_tool_calls: tuple[dict[str, Any], ...] = ()
    native_tool_provider: str = "auto"

class TurnEngine:
    def __init__(
        self,
        kernel: RoleExecutionKernel,
        config: TurnEngineConfig | None = None,
    ) -> None: ...

    async def run(
        self,
        request: RoleTurnRequest,
        role: str,
        controller: ToolLoopController | None = None,
        system_prompt: str | None = None,
        fingerprint: PromptFingerprint | None = None,
        attempt: int = 0,
        response_model: type | None = None,
    ) -> RoleTurnResult: ...

    async def run_stream(
        self,
        request: RoleTurnRequest,
        role: str,
        controller: ToolLoopController | None = None,
        system_prompt: str | None = None,
        fingerprint: PromptFingerprint | None = None,
    ) -> AsyncIterator[dict[str, Any]]: ...
```

#### 依赖

| 模块 | 用途 | 类型 |
|------|------|------|
| `kernel._llm_caller` | LLM 调用 | 内部协作 |
| `kernel._output_parser` | 输出解析 | 内部协作 |
| `kernel._execute_single_tool` | 工具执行 | 内部协作 |
| `ToolLoopController` | Transcript 管理 | 外部依赖 |
| `ConversationState` | 预算追踪 | 外部依赖 |
| `PolicyLayer` | 策略评估 | 外部依赖 |

#### 数据流向

```
RoleTurnRequest → TurnEngine.run()
    ↓
ToolLoopController.build_context_request() → ContextRequest
    ↓
kernel._llm_caller.call() → LLMResponse
    ↓
_output_parser.parse_thinking() → AssistantTurnArtifacts
    ↓
_parse_tool_calls_from_turn() → List[ToolCallResult]
    ↓
_execute_single_tool() → Dict[str, Any]
    ↓
ToolLoopController.append_tool_result()
    ↓
RoleTurnResult
```

---

### 2.2 context_os/runtime 模块

**路径**: `polaris/kernelone/context/context_os/`

**职责**: State-First Context OS 运行时

#### 公共接口

```python
from polaris.kernelone.context.context_os.runtime import (
    StateFirstContextOS,
    DialogActClassifier,
)
from polaris.kernelone.context.context_os.models import (
    ContextOSSnapshot,
    ContextOSProjection,
    BudgetPlan,
    WorkingState,
    TranscriptEvent,
    DialogAct,
    DialogActResult,
    EpisodeCard,
    ArtifactRecord,
)
```

#### 类签名

```python
class DialogActClassifier:
    def __init__(
        self,
        *,
        enable_high_priority_fallback: bool = True,
        min_content_length_for_extended: int = 3,
    ) -> None: ...

    def classify(
        self,
        text: str,
        role: str = "user",
    ) -> DialogActResult: ...

class StateFirstContextOS:
    def __init__(
        self,
        policy: StateFirstContextOSPolicy | None = None,
        *,
        domain_adapter: ContextDomainAdapter | None = None,
        domain: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> None: ...

    @property
    def resolved_context_window(self) -> int: ...

    @property
    def dialog_act_classifier(self) -> DialogActClassifier: ...

    def project(
        self,
        *,
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        existing_snapshot: ContextOSSnapshot | dict[str, Any] | None = None,
        recent_window_messages: int = 8,
        focus: str = "",
    ) -> ContextOSProjection: ...

    def reclassify_event(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        *,
        event_id: str,
        new_route: str,
        reason: str,
        confidence: float = 1.0,
        recent_window_messages: int = 8,
        focus: str = "",
    ) -> ContextOSProjection: ...

    def search_memory(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        query: str,
        *,
        kind: str | None = None,
        entity: str | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]: ...

    def read_artifact(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        artifact_id: str,
        *,
        span: tuple[int, int] | None = None,
    ) -> dict[str, Any] | None: ...

    def get_state(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        path: str,
    ) -> Any: ...
```

#### 依赖

| 模块 | 用途 | 类型 |
|------|------|------|
| `models` | 数据模型 | 内部模块 |
| `domain_adapters` | 领域适配 | 内部模块 |
| `ModelCatalog` | 模型配置 | 外部依赖 |

#### 数据流向

```
Messages + ExistingSnapshot
    ↓
_merge_transcript() → TranscriptEvent[]
    ↓
_canonicalize_and_offload() → (TranscriptEvent[], ArtifactRecord[], PendingFollowUp)
    ↓
_patch_working_state() → WorkingState
    ↓
_plan_budget() → BudgetPlan
    ↓
_collect_active_window() → TranscriptEvent[]
    ↓
_seal_closed_episodes() → EpisodeCard[]
    ↓
ContextOSProjection(snapshot, head_anchor, tail_anchor, ...)
```

---

### 2.3 llm_caller 模块

**路径**: `polaris/cells/roles/kernel/internal/llm_caller/`

**职责**: LLM 调用抽象层

#### 公共接口

```python
from polaris.cells.roles.kernel.internal.llm_caller import (
    LLMCaller,
    LLMResponse,
    StructuredLLMResponse,
    PreparedLLMRequest,
)
```

#### 类签名

```python
@dataclass
class LLMResponse:
    content: str
    token_estimate: int = 0
    error: str | None = None
    error_category: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_provider: str = "auto"
    metadata: dict[str, Any] = field(default_factory=dict)

class LLMCaller:
    def __init__(
        self,
        workspace: str = "",
        enable_cache: bool = True,
    ) -> None: ...

    async def call(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
    ) -> LLMResponse: ...

    async def call_stream(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
    ) -> AsyncGenerator[dict[str, Any], None]: ...
```

#### 依赖

| 模块 | 用途 | 类型 |
|------|------|------|
| `AIExecutor` | LLM 执行器 | 外部依赖 |
| `ModelCatalog` | 模型配置 | 外部依赖 |
| `events` | 事件发射 | 外部依赖 |
| `llm_cache` | 响应缓存 | 外部依赖 |

#### 数据流向

```
RoleProfile + SystemPrompt + ContextRequest
    ↓
_resolve_provider_capabilities() → ProviderCapabilities
    ↓
_prepare_llm_request() → PreparedLLMRequest
    ↓
AIExecutor.execute() / execute_stream() → AIResponse
    ↓
LLMResponse
```

---

### 2.4 kernel 模块

**路径**: `polaris/cells/roles/kernel/internal/kernel/`

**职责**: 角色执行内核协调器

#### 公共接口

```python
from polaris.cells.roles.kernel.internal.kernel import (
    RoleExecutionKernel,
)
from polaris.cells.roles.kernel.public.config import (
    KernelConfig,
    get_default_config,
)
```

#### 类签名

```python
class RoleExecutionKernel:
    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        use_structured_output: bool | None = None,
        config: KernelConfig | None = None,
        tool_gateway: ToolGatewayPort | None = None,
    ) -> None: ...

    @property
    def config(self) -> KernelConfig: ...

    async def run(
        self,
        role: str,
        request: RoleTurnRequest,
    ) -> RoleTurnResult: ...

    async def run_stream(
        self,
        role: str,
        request: RoleTurnRequest,
    ) -> AsyncGenerator[dict[str, Any], None]: ...
```

#### 依赖

| 模块 | 用途 | 类型 |
|------|------|------|
| `TurnEngine` | 执行循环 | 内部协作 |
| `LLMCaller` | LLM 调用 | 内部协作 |
| `PromptBuilder` | 提示词构建 | 内部协作 |
| `OutputParser` | 输出解析 | 内部协作 |
| `QualityChecker` | 质量检查 | 内部协作 |
| `ToolLoopController` | 工具循环 | 内部协作 |
| `RoleProfileRegistry` | 角色配置 | 外部依赖 |

#### 数据流向

```
RoleTurnRequest
    ↓
registry.get_profile_or_raise() → RoleProfile
    ↓
_build_system_prompt_for_request() → system_prompt
    ↓
ToolLoopController.from_request() → controller
    ↓
TurnEngine.run() / run_stream()
    ↓
QualityChecker.validate_output() → QualityResult
    ↓
RoleTurnResult
```

---

### 2.5 tool_loop_controller 模块

**路径**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

**职责**: Transcript 历史与安全策略管理

#### 公共接口

```python
from polaris.cells.roles.kernel.internal.tool_loop_controller import (
    ToolLoopController,
    ToolLoopSafetyPolicy,
    ContextEvent,
)
```

#### 类签名

```python
@dataclass(frozen=True, slots=True)
class ContextEvent:
    event_id: str
    role: str  # "user" | "assistant" | "tool" | "system"
    content: str
    sequence: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str]: ...

    @classmethod
    def from_tuple(
        cls,
        tuple_event: tuple[str, str],
        sequence: int,
    ) -> ContextEvent: ...

    @property
    def dialog_act(self) -> str: ...

    @property
    def kind(self) -> str: ...

@dataclass(frozen=True, slots=True)
class ToolLoopSafetyPolicy:
    max_total_tool_calls: int = 64
    max_stall_cycles: int = 2
    max_wall_time_seconds: int = 900

@dataclass(slots=True)
class ToolLoopController:
    request: RoleTurnRequest
    profile: RoleProfile
    safety_policy: ToolLoopSafetyPolicy

    @classmethod
    def from_request(
        cls,
        *,
        request: RoleTurnRequest,
        profile: RoleProfile,
    ) -> ToolLoopController: ...

    def build_context_request(self) -> ContextRequest: ...

    def register_cycle(
        self,
        *,
        executed_tool_calls: list[ToolCallResult],
        deferred_tool_calls: list[ToolCallResult],
        tool_results: list[dict[str, Any]],
    ) -> str | None: ...

    def append_tool_cycle(
        self,
        *,
        assistant_message: str,
        tool_results: list[dict[str, Any]],
    ) -> None: ...

    def append_tool_result(
        self,
        tool_result: dict[str, Any],
    ) -> None: ...
```

#### 依赖

| 模块 | 用途 | 类型 |
|------|------|------|
| `ContextRequest` | 上下文请求 | 外部依赖 |
| `RoleTurnRequest` | 角色请求 | 外部依赖 |
| `RoleProfile` | 角色配置 | 外部依赖 |
| `ToolCallResult` | 工具调用结果 | 外部依赖 |

---

### 2.6 runtime/service 模块

**路径**: `polaris/cells/roles/runtime/public/service.py`

**职责**: 角色运行时服务门面

#### 公共接口

```python
from polaris.cells.roles.runtime.public.service import (
    RoleRuntimeService,
    ExecuteRoleTaskCommandV1,
    ExecuteRoleSessionCommandV1,
    GetRoleRuntimeStatusQueryV1,
    RoleExecutionResultV1,
    IRoleRuntime,
)
```

#### 类签名

```python
class RoleRuntimeService(IRoleRuntime):
    def __init__(self) -> None: ...

    # 策略相关
    def resolve_strategy_profile(
        self,
        domain: str | None = None,
        role: str | None = None,
        session_override: dict[str, Any] | None = None,
        prefer_domain_default: bool = False,
    ) -> ResolvedStrategy: ...

    def create_strategy_run(
        self,
        domain: str,
        role: str | None,
        session_id: str | None,
        budget: ContextBudget | None,
        workspace: str,
        domain_explicit: bool = False,
    ) -> StrategyRunContext: ...

    @staticmethod
    def emit_strategy_receipt(
        run_ctx: StrategyRunContext,
        workspace: str,
    ) -> Path: ...

    # 执行入口
    async def execute_role_task(
        self,
        command: ExecuteRoleTaskCommandV1,
    ) -> RoleExecutionResultV1: ...

    async def execute_role_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1: ...

    async def get_runtime_status(
        self,
        query: GetRoleRuntimeStatusQueryV1,
    ) -> Mapping[str, Any]: ...

    async def stream_chat_turn(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncGenerator[dict[str, Any], None]: ...

    # CLI 辅助
    async def run_interactive(
        self,
        role: str,
        workspace: str,
        welcome_message: str = "",
    ) -> None: ...

    async def run_oneshot(
        self,
        role: str,
        workspace: str,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def run_autonomous(
        self,
        role: str,
        workspace: str,
        goal: str,
        max_iterations: int = 10,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
```

#### 依赖

| 模块 | 用途 | 类型 |
|------|------|------|
| `RoleExecutionKernel` | 执行内核 | 内部协作 |
| `StrategyRegistry` | 策略注册 | 外部依赖 |
| `SessionContinuityEngine` | 会话连续性 | 外部依赖 |
| `RoleSessionService` | 会话存储 | 外部依赖 |

---

## 3. 模块间依赖关系图

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │                    Public API Layer                         │
                    │  RoleRuntimeService, RoleExecutionKernel, TurnEngine        │
                    └─────────────────────────────────────────────────────────────┘
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    │                           │                           │
                    ▼                           ▼                           ▼
          ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
          │   LLMCaller     │         │   ContextOS     │         │ ToolLoopCtrl    │
          │   (E3)          │         │   (E2)          │         │ (E6)            │
          │                 │         │                 │         │                 │
          │ - call()        │         │ - project()     │         │ - _history      │
          │ - call_stream() │         │ - search_memory │         │ - safety_policy │
          └────────┬────────┘         └────────┬────────┘         └────────┬────────┘
                   │                           │                           │
                   │                           │                           │
                   │    ┌──────────────────────┼──────────────────────┐    │
                   │    │                      │                      │    │
                   │    ▼                      ▼                      ▼    │
                   │  ┌──────────────────────────────────────────────────┐  │
                   │  │                  Shared Types                    │  │
                   │  │  LLMResponse, ContextOSSnapshot, ContextEvent    │  │
                   │  │  RoleTurnRequest, RoleTurnResult                 │  │
                   │  └──────────────────────────────────────────────────┘  │
                   │                                                         │
                   └─────────────────────────────────────────────────────────┘
```

---

## 4. 数据流总览

### 4.1 非流式执行路径

```
RoleRuntimeService.execute_role_session()
    │
    ▼
RoleExecutionKernel.run()
    │
    ├─► registry.get_profile_or_raise(role) → RoleProfile
    │
    ├─► _build_system_prompt_for_request() → system_prompt
    │
    ├─► ToolLoopController.from_request() → controller
    │
    ▼
TurnEngine.run()
    │
    ├─► _request_to_state() → ConversationState
    │
    ├─► controller.build_context_request() → ContextRequest
    │
    ├─► _llm_caller.call() → LLMResponse
    │       │
    │       ├─► _prepare_llm_request() → PreparedLLMRequest
    │       │
    │       └─► AIExecutor.execute() → AIResponse
    │
    ├─► _materialize_assistant_turn() → AssistantTurnArtifacts
    │
    ├─► _parse_tool_calls_from_turn() → List[ToolCallResult]
    │
    ├─► _execute_single_tool() → tool_result
    │
    ├─► controller.append_tool_result()
    │
    ├─► policy.evaluate() → PolicyResult
    │
    └─► RoleTurnResult
```

### 4.2 流式执行路径

```
RoleRuntimeService.stream_chat_turn()
    │
    ├─► create_strategy_run() → StrategyRunContext
    │
    └─► yield fingerprint_event
    │
    ▼
RoleExecutionKernel.run_stream()
    │
    ▼
TurnEngine.run_stream()
    │
    ├─► _llm_caller.call_stream() → AsyncGenerator[event]
    │       │
    │       ├─► yield {"type": "thinking_chunk", "content": ...}
    │       ├─► yield {"type": "content_chunk", "content": ...}
    │       └─► yield {"type": "tool_call", "tool": ...}
    │
    ├─► _execute_single_tool()
    │
    ├─► yield {"type": "tool_result", "result": ...}
    │
    └─► yield {"type": "complete", "result": RoleTurnResult}
```

---

## 5. 接口稳定性保证

### 5.1 向后兼容规则

1. **`__init__.py` 重导出**: 所有重构模块必须通过原路径导出公共 API
   ```python
   # polaris/cells/roles/kernel/internal/turn_engine/__init__.py
   from .engine import TurnEngine
   from .config import TurnEngineConfig
   from .artifacts import AssistantTurnArtifacts

   __all__ = ["TurnEngine", "TurnEngineConfig", "AssistantTurnArtifacts"]
   ```

2. **签名不变**: 公共方法签名必须保持兼容
   - 新参数必须有默认值
   - 返回类型不能改变
   - 异常类型不能扩展

3. **类型别名**: 内部重构不应影响外部类型注解

### 5.2 版本控制

| 变更类型 | 要求 |
|---------|------|
| 新增公共方法 | 直接添加 |
| 新增可选参数 | 默认值兼容 |
| 废弃方法 | 添加 `@deprecated` 警告，保留 2 个版本 |
| 移除方法 | 需要迁移指南和弃用周期 |

### 5.3 测试契约

1. **接口测试**: 每个公共方法必须有对应测试
2. **Mock 边界**: 测试应通过 mock 隔离依赖
3. **回归测试**: 重构前后输出必须一致

---

## 6. 错误处理契约

### 6.1 错误传播规则

```python
# LLMCaller 层
LLMResponse(error="...", error_category="timeout|network|rate_limit|provider|unknown")

# TurnEngine 层
RoleTurnResult(error="...", is_complete=False)

# RoleRuntimeService 层
RoleExecutionResultV1(ok=False, error_code="role_runtime_error", error_message="...")
```

### 6.2 异常类型

| 层级 | 异常类型 | 处理方式 |
|------|---------|---------|
| LLMCaller | `LLMResponse.error` | 返回错误响应 |
| TurnEngine | `RoleTurnResult.error` | 返回错误结果 |
| Kernel | `RoleTurnResult.error` | 返回错误结果 |
| Service | `RoleExecutionResultV1.error_message` | 返回错误结果 |

---

## 7. 性能契约

### 7.1 超时配置

```python
# 环境变量
KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS=64
KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES=2
KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS=900
KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS=600
```

### 7.2 内存约束

- `ToolLoopController._history`: 单次请求累积事件
- `ContextOSSnapshot.transcript_log`: 跨请求持久化
- 单条 `content` 最大: 16000 字符 (可配置)

---

## 8. 审计与可观测性

### 8.1 事件发射

```python
from polaris.cells.roles.kernel.internal.events import (
    LLMEventType,
    emit_llm_event,
)

# 事件类型
LLMEventType.CALL_START
LLMEventType.CALL_RETRY
LLMEventType.VALIDATION_FAIL
LLMEventType.VALIDATION_PASS
LLMEventType.TOOL_EXECUTE
LLMEventType.TOOL_RESULT
```

### 8.2 日志规范

- 使用 `logging.getLogger(__name__)`
- 结构化日志: `logger.info("[TurnEngine] %s", message)`
- 错误堆栈: `logger.exception(...)` 或 `logger.warning(..., exc_info=True)`

---

## 9. 附录

### A. 相关文件

- `BLUEPRINT.md` - 重构蓝图
- `EXECUTION_PLAN.md` - 执行计划
- `TEAM_ASSIGNMENTS.md` - 团队分配

### B. 参考资料

- `src/backend/AGENTS.md` - 后端权威入口
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` - 架构标准
- `docs/governance/ci/fitness-rules.yaml` - 质量门禁规则