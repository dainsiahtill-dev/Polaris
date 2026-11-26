# Polaris MCP 打通方案（2026-11-27）

## 背景：当前 MCP 支持为 0

实测当前代码树：

| 层 | 现状 |
|---|---|
| MCP 客户端 | **不存在**（旧 `infrastructure/tools/mcp_client.py` 已删，全仓无 `mcp*.py`） |
| MCP 服务端 | **不存在**（无 JSON-RPC / `tools/list` 传输层） |
| MCP 契约 | 有 2 个 JSON schema：`mcp_context_response`、`mcp_verify_events`（`infrastructure/accel/schema/`） |
| 数据源 | `accel/cli.py` 的 `cmd_context` / `cmd_verify`（正好对应那两个 schema） |
| 工程集成 | **零**（Director/QA 工具集不含任何 MCP 工具） |

结论：契约与数据源都在，传输层与集成都没了。**"Polaris 完整支持 MCP" = 0。**

## 目标

让 MCP 成为**通用能力**（不是 bench 特判）：

1. **服务端**：把 accel 已有的 context / verify 能力暴露为 MCP 工具（JSON-RPC 2.0，stdio）。
2. **客户端**：通用 MCP 客户端（配置驱动：`command` 或 `url`），把任意 MCP server 的工具接进 Director/QA。
3. **实战**：用真实仓库验证（不是 bench 目标项目）。

## 架构

```
外部工具（Claude Code / 任意 MCP client）
        │ MCP JSON-RPC (stdio)
        ▼
polaris.infrastructure.mcp.server
  ├─ accel_context(project, task, changed_files)  → mcp_context_response
  └─ accel_verify_events(project, changed_files)  → mcp_verify_events
        │ 直接调用
        ▼
accel（compile_context_pack / run_verify）

Director / QA
        │ 配置里的 MCP server（command/url）
        ▼
polaris.infrastructure.mcp.client（initialize → tools/list → tools/call）
        │ 物化为通用工具
        ▼
工程工具集（与 repo_rg/read_file 同级的普通工具）
```

## 模块

- `infrastructure/mcp/server.py`：JSON-RPC 循环、工具注册表、错误码
- `infrastructure/mcp/client.py`：stdio 客户端（spawn + initialize + tools/list + tools/call），带缓存
- `infrastructure/mcp/engineering_tools.py`：把 MCP 工具物化为工程工具（配置驱动）
- 配置：`KERNELONE_MCP_SERVERS`（JSON：`[{name, command|url, enabled_tools}]`）

## 不变量

- **通用**：任何 MCP server 都能接，不针对特定项目/仓库。
- **fail-closed**：server 不可用 → 工具不可用（不静默降级成"无工具"）。
- **授权不变**：MCP 工具调用仍受 `PermissionScope` 约束（读/写/执行边界不外扩）。
- **可审计**：MCP 调用作为普通工具观察落 journal。

## 验收

1. 起 server，`tools/list` 返回 2 个工具，`tools/call` 在真实项目上返回符合 schema 的结果。
2. 客户端配置任意 stdio server（含第三方），能列出并调用其工具。
3. Director 在真实仓库上**只通过 MCP 工具**完成一次代码理解 + 修改，证据质量不低于 `repo_rg` 基线。
