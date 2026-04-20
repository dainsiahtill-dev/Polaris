# polaris/kernelone/llm/tools

KernelOne 工具调用内核运行时：

- 统一工具调用契约（`ToolCall` / `ToolExecutionResult`）
- 策略收敛（allowlist / max calls / fail-fast）
- 单轮执行编排（解析 -> 执行 -> 反馈）

该目录仅作为 Agent OS 底层的工具执行引擎。**不放具体业务工具实现，不放 Polaris 角色业务策略。**
（旧版本中存在的一些对 `tools` 具体实现的直接依赖已随迁移废弃，所有具体 Tool 应由所在 Cell 自行实现并注册）。