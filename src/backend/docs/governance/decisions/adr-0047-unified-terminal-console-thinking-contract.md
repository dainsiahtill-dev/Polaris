# ADR-0047: Unified Terminal Console Must Consume the Full Thinking Stream Contract

状态: Accepted
日期: 2026-03-25

---

## 1. 背景

在 `python -m polaris.delivery.cli console --backend plain` 的统一终端模式下，
用户反馈 think/thinking 完全不显示。

这不是模型不再输出 reasoning，也不是 `roles.kernel` 不再发 `thinking_chunk`。
实际情况是：

1. `roles.kernel.run_stream()` 仍然发出 `thinking_chunk`
2. `RoleConsoleHost.stream_turn()` 仍然把 thinking 保留到 `complete.data.thinking`
3. 统一入口 `polaris/delivery/cli/terminal_console.py` 在流式消费时只处理：
   - `content_chunk`
   - `tool_call`
   - `tool_result`
   - `error`
   - `complete`

它直接漏掉了 `thinking_chunk`

因此这是一个共享 stream contract 消费缺口，而不是单点角色或单点提示词问题。

---

## 2. 问题

统一 console 是 canonical CLI host。

如果它只消费一半 stream contract，就会产生两个后果：

1. `thinking_chunk` 被静默吞掉
2. 即使 `complete` 里仍带 thinking，也不会被可靠补显

这会让用户误以为：

1. 模型不再思考
2. 后端提前超时或截断
3. stream 协议失效

实际根因只是主消费方没有完整实现 contract。

---

## 3. 决策

统一终端 `terminal_console._stream_turn()` 必须完整消费 thinking 相关事件。

具体要求：

1. 收到 `thinking_chunk` 时立即输出，不做伪流式缓冲
2. 终端中以显式 `<thinking>` / `</thinking>` block 包裹 reasoning，可与 content 区分
3. 从 thinking 切到 content / tool / error / complete 时必须正确闭合 block
4. 即使没有单独的 `thinking_chunk`，只要 `complete.data.thinking` 存在，也必须 fallback 显示
5. 保持现有 `complete.content` 去重合同，避免把最终答案再打印一遍

---

## 4. 为什么不只修别处

### 4.1 不是修 kernel

kernel 当前 contract 是成立的；它已经产出 `thinking_chunk`。
继续改 kernel 只会偏离根因。

### 4.2 不是只修 RoleConsoleHost

console_host 也已经保留了 `complete.thinking`。
问题出在最终消费端。

### 4.3 不是做一个“显示补丁”

如果只是 final complete 阶段再塞一段文本，thinking 仍然不是实时可见，
而且在 tool/content 交错时仍会发生 block 不闭合或顺序错乱。

---

## 5. 后果

### 正面影响

1. 统一 CLI 重新具备真实可见的 thinking stream
2. reasoning / content / tool 三类流在终端内边界重新明确
3. 所有复用 `terminal_console` 的角色入口一起修复，不再分散补丁

### 代价

1. 终端输出会出现显式 `<thinking>` block
2. 需要维护 thinking/content/tool 相位切换的状态机

---

## 6. 验证

1. `python -m pytest -q polaris/delivery/cli/tests/test_terminal_console.py`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_stream_visible_output_contract.py`

---

## 7. 后续方向

当前先修 plain terminal 的 canonical contract 消费。

后续若继续演进 richer TUI / projection 层，仍必须遵守同一原则：

1. thinking stream 不能静默吞掉
2. 统一 host 必须完整消费 stream contract
3. 任何可视化优化都不能退化成伪流式或吞事件
