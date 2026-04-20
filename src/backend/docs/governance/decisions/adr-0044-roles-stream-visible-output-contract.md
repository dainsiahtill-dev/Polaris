---
status: 已实施
context: "plain CLI 角色对话把 provider 原始流和用户可见流混在一起，导致 `<thinking>` / `[TOOL_CALL]` 泄漏与答案重复"
decision: "roles.kernel.run_stream() 只向外发布净化后的 user-visible stream，provider 原始 chunk 保留在内核内完成解析与工具编排"
consequences: "console/runtime/http 统一消费同一份干净流；代价是可见 stream 粒度收敛到单轮投影，而不是 provider 原始 token"
---

# ADR-0044: roles.kernel 流式可见输出合同

## 上下文

2026-03-25 在 `python -m polaris.delivery.cli console --backend plain` 的角色对话中，
出现了稳定复现的两类异常：

1. provider 原始 `<thinking>...</thinking>` 文本直接打印到终端；
2. `[TOOL_CALL]...[/TOOL_CALL]` 先作为内容打印，随后工具 JSON 事件再打印，
   最终 `complete` 阶段还会给出净化后的同轮答案，形成“泄漏 + 重复”。

问题不是单个 renderer 的 bug，而是跨层合同错误：

- `roles.kernel.run_stream()` 直接转发 provider 原始 `chunk` / `reasoning_chunk`
- `roles.kernel` 在同一轮结束后才 materialize `clean_content`
- `delivery.cli.terminal_console` 正确地避免了重复打印 `complete.content`，
  但它无法阻止上游先把脏 chunk 打出来

因此，真正的边界应该在 `roles.kernel`，而不是 host/console。

## 决策

`roles.kernel.run_stream()` 对外只暴露“用户可见流”，不再透传 provider 原始文本。

### 新合同

1. `chunk` / `reasoning_chunk` 先在内核内缓存。
2. 一轮 LLM 调用结束后，由 `AssistantTurnArtifacts` 统一执行：
   - `<thinking>` 提取
   - `[TOOL_CALL]` wrapper 清洗
   - native tool call / textual fallback 解析
3. 对外发布的 `content_chunk` 必须来自 `turn.clean_content`。
4. 对外发布的 `thinking_chunk` 必须来自合并后的 thinking 投影。
5. provider 原始流仅作为解析输入，不得直接进入 CLI / HTTP / transcript 可见面。

### 为什么不在 console 层做过滤

那样只会修补 `plain CLI`，不会修补：

- `roles.runtime`
- `RoleConsoleHost`
- 未来任何消费 `run_stream()` 的 host / router / observer

根因在共享内核合同，必须在那里收敛。

## 后果

### 正向结果

- `<thinking>`、`[TOOL_CALL]`、provider 原始 chunk 不再泄漏到用户终端。
- `complete` 回到“结果收束”职责，不再承担可见文本去重补丁职责。
- `roles.runtime` / `delivery.cli` / 其他 host 统一消费同一份干净流。

### 代价

- 用户可见 stream 从 provider token 级别收敛为“单轮投影”级别。
- 如果未来要恢复更细粒度 streaming，必须提供可证明不会泄漏协议文本的增量 sanitize 合同。

## 实施说明

本 ADR 由以下改动落地：

- `polaris/cells/roles/kernel/internal/turn_engine.py`
  - 新增 stream thinking 合并与可见 turn 投影辅助方法
  - `run_stream()` 不再直接 yield provider 原始 `chunk` / `reasoning_chunk`
  - 改为在一轮结束后统一发布净化后的 `thinking_chunk` / `content_chunk`
- `polaris/cells/roles/kernel/tests/test_stream_visible_output_contract.py`
  - 覆盖泄漏与 reasoning 保留回归
- `polaris/delivery/cli/tests/test_terminal_console.py`
  - 覆盖 console 不重复打印 `complete.content` 回归

## 验证

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py polaris/cells/roles/kernel/tests/test_stream_visible_output_contract.py`
2. `python -m pytest -q polaris/delivery/cli/tests/test_terminal_console.py tests/test_director_console_host.py`
3. `python -m pytest -q tests/architecture/test_structural_bug_governance_assets.py`

## 残余风险

1. 目前的 user-visible stream 仍是“单轮投影”，不是 provider 原始 token 流。
2. 如果未来新增新的 host 直接消费 provider 适配层而绕过 `roles.kernel`，同类问题会重现。
3. `json-render pretty-color` 仍会按照用户显式选项打印结构化工具事件，这不属于本 ADR 的泄漏修复范围。
