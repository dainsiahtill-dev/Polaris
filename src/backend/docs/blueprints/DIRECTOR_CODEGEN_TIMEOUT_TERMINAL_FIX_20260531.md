# Director Codegen Timeout Terminal Fix

日期: 2026-05-31

## 问题

真实 PM -> Director 桌面端链路中，第三个 Director 子任务在 `gpt-5.3-codex` 调用中等待到 660 秒超时后，又被当成“无文件变更”继续重试，导致 PM 长时间保持 `running`，用户看到的是 PM 阻塞或空等。

## 根因

1. `roles.kernel` 的 LLM caller 只按角色默认值解析 timeout，未读取 `RoleTurnRequest.context_override.llm_call_timeout_seconds`。
2. Director codegen bridge 收到 provider timeout 响应时未把它视为终端错误，而是继续走 empty response retry 路径。
3. bridge 的用户消息含文件生成/格式控制措辞，容易被安全层包成 `UNTRUSTED_USER_MESSAGE`，削弱输出格式约束。
4. 多目标文件任务被塞进一次 proposal-mode LLM 调用，复杂文件生成容易撞上 workflow phase timeout。

## 修复方案

```text
Director Task
  -> code_generation_engine context.llm_call_timeout_seconds
  -> RoleTurnRequest.context_override
  -> roles.kernel LLMCaller request_options.timeout
  -> provider timeout response
  -> terminal timeout warning, no empty-response retry
```

同时将用户消息改为普通短句，把格式约束保留在 runtime appendix 和 context 中，避免误触发用户消息注入防护；多目标文件默认按 3 个文件一组拆成多个 codegen round。

## 验证

- 单元测试覆盖 request timeout override。
- 单元测试覆盖 provider timeout response 不再重试。
- 后续重跑 Electron PM -> Director 真实链路，确认 PM 能进入确定终态。
