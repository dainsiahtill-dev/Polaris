# SUPER Mode Pipeline Production Architecture Blueprint

日期: 2026-04-27
状态: Draft
作者: Codex
继承自: `CLI_SUPER_MODE_FULL_PIPELINE_ORCHESTRATION_BLUEPRINT_20260423.md`

---

## 1. 现状 vs 目标

### 1.1 现状（2026-04-27 实测验证）

SUPER 模式完整链路已可跑通（Architect → PM → CE → Director），但存在以下系统性问题：

| 问题 | 根因 | 当前绕过方式 |
|------|------|-------------|
| PM 在 `stream_chat_turn` 路径静默失败 | 旧路径缺少 orchestrator 支持 | env var 切换到 `RoleSessionOrchestrator` |
| Architect/PM 陷入 exploring 死循环 | LLM 在空工作区反复调用 `repo_tree` | prompt 里硬编码 "Do NOT explore" |
| Director 不调用 write_file | MiniMax 模型 `tool_choice=auto` 倾向文本输出 | `stream_orchestrator.py` 里硬编码 `tool_choice="required"` |
| PM 失败后无重试 | degraded handoff 给 Director 裸奔 | 无（Director 靠运气完成） |

### 1.2 目标

一个**数据驱动、可配置、可审计**的 SUPER 管道，不依赖硬编码 prompt 字符串或 env var hack。

---

## 2. 架构原则

### 2.1 Orchestrator-first

```
当前：_run_super_turn() → _run_streaming_turn() → host.stream_turn()
                   ↑ 手动拼消息、手动管理状态、每个角色独立 asyncio.run()

目标：_run_super_turn() → SuperPipelineOrchestrator → RoleSessionOrchestrator
                   ↑ 声明式阶段定义、共享状态机、统一错误处理
```

`_run_streaming_turn` 不删除，但降级为**单角色独立调试入口**。SUPER 管道使用 `SuperPipelineOrchestrator` 统一编排。

### 2.2 Constraint-as-data

当前的防探索指令和 tool_choice 是散落在代码里的字符串/硬编码值。目标是统一为声明式约束描述：

```python
@dataclass(frozen=True)
class StageConstraint:
    max_exploration_turns: int = 0     # 0 = 禁止探索
    tool_choice: str = "auto"          # auto | required | {"type":"function",...}
    allowed_tool_categories: tuple[str, ...] = ("read", "write", "execute")
    forbidden_tools: tuple[str, ...] = ("repo_tree", "glob", "list_directory")
    delivery_mode: str = "analyze_only"  # analyze_only | materialize_changes
```

每个阶段（Architect/PM/CE/Director）关联一个 `StageConstraint`。约束注入到 LLM 的 system prompt 和 API request 中，不依赖自然语言指令。

### 2.3 Failure-as-first-class

```python
@dataclass(frozen=True)
class StageResult:
    role: str
    success: bool
    content: str
    error: str | None = None
    retry_count: int = 0
    duration_seconds: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
```

每个阶段产出 `StageResult`，管道据此决策：重试、降级、跳过、中止。

---

## 3. 模块设计

### 3.1 新增：`polaris/delivery/cli/super_pipeline.py`

```python
class SuperPipelineOrchestrator:
    """声明式多阶段管道编排器。

    替代 _run_super_turn 中的手动 for 循环。
    """

    def __init__(self, config: SuperPipelineConfig): ...

    async def run(self, request: str) -> PipelineResult:
        """执行完整管道，返回每个阶段的 StageResult。"""
        ...

    async def _execute_stage(self, stage: PipelineStage) -> StageResult:
        """执行单个阶段，包含约束注入、重试、超时。"""
        ...
```

### 3.2 新增：`polaris/delivery/cli/super_pipeline_config.py`

```python
@dataclass(frozen=True)
class PipelineStage:
    role: str
    handoff_builder: Callable[..., str]       # build_pm_handoff_message 等
    constraint: StageConstraint
    max_retries: int = 1
    timeout_seconds: int = 300
    skip_condition: Callable[[SuperPipelineContext], bool] | None = None
    on_failure: Literal["retry", "skip", "degrade", "abort"] = "retry"


@dataclass(frozen=True)
class SuperPipelineConfig:
    stages: tuple[PipelineStage, ...]
    max_total_duration_seconds: int = 1200
    enable_metrics: bool = True
    persist_blueprints: bool = True
    orchestrator_mode: Literal["session_orchestrator", "stream_chat"] = "session_orchestrator"
```

默认配置：

```python
DEFAULT_SUPER_PIPELINE = SuperPipelineConfig(
    stages=(
        PipelineStage(
            role="architect",
            handoff_builder=build_super_readonly_message,
            constraint=StageConstraint(
                max_exploration_turns=1,
                tool_choice="auto",
                forbidden_tools=(),
                delivery_mode="analyze_only",
            ),
            max_retries=1,
            timeout_seconds=180,
        ),
        PipelineStage(
            role="pm",
            handoff_builder=build_pm_handoff_message,
            constraint=StageConstraint(
                max_exploration_turns=0,
                tool_choice="auto",
                forbidden_tools=("repo_tree", "glob", "list_directory", "repo_rg"),
                delivery_mode="analyze_only",
            ),
            max_retries=2,  # PM 是瓶颈，多给一次机会
            timeout_seconds=240,
            on_failure="retry",
        ),
        PipelineStage(
            role="chief_engineer",
            handoff_builder=build_chief_engineer_handoff_message,
            constraint=StageConstraint(
                max_exploration_turns=0,
                tool_choice="auto",
                forbidden_tools=("repo_tree", "glob", "list_directory", "repo_rg"),
                delivery_mode="analyze_only",
            ),
            max_retries=1,
            timeout_seconds=180,
            skip_condition=lambda ctx: not ctx.pm_output.strip(),
            on_failure="skip",
        ),
        PipelineStage(
            role="director",
            handoff_builder=build_director_task_handoff_message,
            constraint=StageConstraint(
                max_exploration_turns=0,
                tool_choice="required",  # 强制工具调用
                forbidden_tools=("repo_tree", "glob", "list_directory", "repo_rg"),
                delivery_mode="materialize_changes",
            ),
            max_retries=1,
            timeout_seconds=600,  # Director 需要更多时间
            on_failure="degrade",
        ),
    ),
)
```

### 3.3 约束注入机制

约束通过两个渠道注入：

**A. System prompt 注入**（`StageConstraint` → prompt）

```python
def build_constraint_prompt(constraint: StageConstraint) -> str:
    lines = []
    if constraint.max_exploration_turns == 0:
        lines.append("CRITICAL: Do NOT call repo_tree, glob, list_directory, or any exploration tools.")
    if constraint.forbidden_tools:
        lines.append(f"Forbidden tools: {', '.join(constraint.forbidden_tools)}")
    if constraint.tool_choice == "required":
        lines.append("You MUST call at least one tool. Text-only responses are invalid.")
    return "\n".join(lines)
```

**B. API request 注入**（`StageConstraint` → `tool_choice_override`）

```python
# 在 RoleSessionOrchestrator 或 stream_orchestrator 中：
if constraint.tool_choice == "required":
    request_options["tool_choice"] = "required"
```

这比在 `stream_orchestrator.py` 里硬编码 `[SUPER_MODE_DIRECTOR_TASK_HANDOFF]` 检测优雅得多。

### 3.4 Orchestrator 统一路径

移除 env var `KERNELONE_ENABLE_SESSION_ORCHESTRATOR`。SUPER 管道始终使用 `RoleSessionOrchestrator`：

```python
# 在 _run_super_turn 中：
orchestrator = RoleSessionOrchestrator(
    session_id=session_id,
    kernel=tx_controller,
    workspace=workspace,
    role=stage.role,
    max_auto_turns=stage.timeout_seconds // 30,  # 大约每 30 秒一轮
)
```

单角色独立调试仍可用 `_run_streaming_turn`，但 SUPER 管道不再走这条路径。

### 3.5 韧性机制

```python
async def _execute_stage_with_retry(self, stage: PipelineStage) -> StageResult:
    for attempt in range(1, stage.max_retries + 1):
        result = await self._execute_stage_single(stage)
        if result.success:
            return result
        if attempt < stage.max_retries:
            logger.warning("RETRY: role=%s attempt=%d/%d error=%s",
                           stage.role, attempt, stage.max_retries, result.error)
            await asyncio.sleep(2 ** attempt)  # exponential backoff
    # 所有重试失败
    return self._handle_stage_failure(stage, result)
```

PM 失败后的降级策略：
- `on_failure="retry"`: 重试（默认，PM 最多 2 次）
- `on_failure="skip"`: 跳过该阶段（CE 在 PM 无输出时跳过）
- `on_failure="degrade"`: 降级到简化 handoff（Director 在 PM/CE 失败时仍尝试执行）
- `on_failure="abort"`: 中止管道

---

## 4. 改动范围

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `super_pipeline.py` | **新增** | `SuperPipelineOrchestrator` + `StageConstraint` + `PipelineStage` |
| `super_pipeline_config.py` | **新增** | `SuperPipelineConfig` + 默认配置 |
| `super_mode.py` | **修改** | handoff builders 使用 `StageConstraint` 生成约束提示 |
| `terminal_console.py` | **修改** | `_run_super_turn` 委托给 `SuperPipelineOrchestrator` |
| `stream_orchestrator.py` | **回滚** | 移除硬编码的 `tool_choice="required"` 检测 |
| `service.py` | **保留** | `RoleSessionOrchestrator` import 修复已到位 |
| `test_super_pipeline_e2e.py` | **修改** | 测试 `SuperPipelineOrchestrator` 而非手动管道 |

---

## 5. 不动的边界

1. 不改 `RoleSessionOrchestrator` 内部逻辑
2. 不改 `task_market` 的 FSM
3. 不改各角色自身的 prompt 模板（core_roles.yaml）
4. 不改 LLM provider 层

所有改动在 `polaris/delivery/cli/` 目录内完成。

---

## 6. 验证方式

### 6.1 单元测试

```python
def test_stage_constraint_generates_correct_prompt():
    constraint = StageConstraint(max_exploration_turns=0, forbidden_tools=("repo_tree",))
    prompt = build_constraint_prompt(constraint)
    assert "Do NOT call repo_tree" in prompt

def test_pipeline_config_default_has_4_stages():
    assert len(DEFAULT_SUPER_PIPELINE.stages) == 4
    assert [s.role for s in DEFAULT_SUPER_PIPELINE.stages] == ["architect", "pm", "chief_engineer", "director"]
```

### 6.2 E2E 测试

```bash
# 简单项目
echo "创建hello.py打印Hello World，请先制定计划蓝图然后落地执行" | \
  python -m polaris.delivery.cli console --workspace /tmp/test --super --batch

# 复杂项目
echo "用Python实现Todo CLI，支持增删改查、标签、JSON持久化。先制定计划蓝图然后落地执行" | \
  python -m polaris.delivery.cli console --workspace /tmp/test2 --super --batch
```

### 6.3 质量门禁

```bash
ruff check polaris/delivery/cli/super_pipeline*.py polaris/delivery/cli/super_mode.py polaris/delivery/cli/terminal_console.py
ruff format polaris/delivery/cli/super_pipeline*.py polaris/delivery/cli/super_mode.py polaris/delivery/cli/terminal_console.py
mypy polaris/delivery/cli/super_pipeline*.py
pytest polaris/delivery/cli/tests/ -q
```

---

## 7. 实施顺序

1. **Phase 1**: 新增 `super_pipeline_config.py`（StageConstraint + PipelineStage + SuperPipelineConfig + 默认配置）
2. **Phase 2**: 新增 `super_pipeline.py`（SuperPipelineOrchestrator 核心循环）
3. **Phase 3**: 修改 `super_mode.py`（handoff builders 使用 StageConstraint）
4. **Phase 4**: 修改 `terminal_console.py`（_run_super_turn 委托给 orchestrator）
5. **Phase 5**: 回滚 `stream_orchestrator.py` 硬编码（改由 StageConstraint 注入）
6. **Phase 6**: 更新测试
