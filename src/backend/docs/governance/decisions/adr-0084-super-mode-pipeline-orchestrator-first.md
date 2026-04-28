# ADR-0084: SUPER Mode Pipeline — Orchestrator-First Architecture

日期: 2026-04-27
状态: Proposed
关联: `docs/blueprints/SUPER_MODE_PIPELINE_PRODUCTION_ARCHITECTURE_20260427.md`

## 背景

CLI SUPER 模式需要编排 Architect → PM → CE → Director 四个角色完成完整管道。

当前实现（`_run_super_turn`）手动拼消息、手动调 LLM、手动管理阶段状态。
测试暴露出以下系统性问题：

1. PM 在旧 `stream_chat_turn` 路径静默失败（空输出），需切换到 `RoleSessionOrchestrator` 路径
2. Architect/PM 在空工作区陷入 `repo_tree` 探索死循环
3. Director 在 `tool_choice=auto` 下不调用 write_file
4. PM 失败后无重试，直接 degraded handoff

## 决策

### D1: SUPER 管道统一使用 `RoleSessionOrchestrator` 执行

`RoleSessionOrchestrator` 已具备多 turn LLM 执行、PhaseManager 集成、工具调用约束等能力。
`_run_super_turn` 不再手动调用 `_run_streaming_turn`，而是为每个阶段创建独立的 orchestrator 实例。

**理由**：重复造轮子导致两个路径行为不一致（PM 在旧路径失败、在新路径正常）。

### D2: 约束系统（exploration limit, tool_choice, forbidden tools）通过 `StageConstraint` 数据类声明式表达

不再依赖自然语言 prompt 字符串传递约束。

```python
StageConstraint(
    max_exploration_turns=0,
    tool_choice="required",
    forbidden_tools=("repo_tree", "glob"),
    delivery_mode="materialize_changes",
)
```

约束同时注入到 system prompt（给 LLM 看）和 API request（给 API 强制执行）。

**理由**：prompt 约束容易被 LLM 忽略；API 级约束（如 `tool_choice="required"`）100% 生效。

### D3: PM 阶段默认重试 2 次

PM 是管道中输出最不稳定的阶段（LLM 有时产出空内容）。给 PM 2 次重试机会，
比直接 degraded handoff 到 Director 靠谱得多。

**理由**：实测 PM 在相同输入下，第一次可能产出空内容，第二次通常正常。

### D4: 移除 env var `KERNELONE_ENABLE_SESSION_ORCHESTRATOR`

SUPER 管道始终使用 orchestrator 路径。单角色独立调试保留 `_run_streaming_turn`。

**理由**：env var 是隐式配置，难以发现、难以调试。显式优于隐式。

## 后果

### 正面

- 管道行为可预测（声明式配置而非隐式逻辑）
- PM 失败可恢复（重试机制）
- Director 工具调用 100% 可靠（API 级 `tool_choice="required"`）
- 约束与 prompt 解耦，便于调整

### 代价

- 新增 2 个文件（~400 行）
- 修改 3 个文件（~100 行改动）
- 回滚 `stream_orchestrator.py` 的硬编码检测（~15 行删除）

### 风险

| 风险 | 缓解 |
|------|------|
| Orchestrator 路径有未知 bug | 保留 `_run_streaming_turn` 作为 fallback，可通过 config 切换 |
| StageConstraint 注入时机不对 | 单元测试覆盖每个阶段的约束生成 |
| PM 重试增加执行时间 | 设置 `max_total_duration_seconds=1200` 硬上限 |
