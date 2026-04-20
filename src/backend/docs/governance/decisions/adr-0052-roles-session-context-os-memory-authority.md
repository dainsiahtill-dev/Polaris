# ADR-0052: Make `roles.session` the Restore Authority for Session-Scoped Context OS Memory

状态: Accepted  
日期: 2026-03-26

---

## 1. 背景

Polaris 已经把 session continuity 和 State-First Context OS 下沉到了
`KernelOne`：

1. `polaris/kernelone/context/session_continuity.py`
2. `polaris/kernelone/context/context_os/**`

但真实运行链路仍有一个结构性断层：

1. Context OS 内部已经具备 `search_memory / read_artifact / read_episode / get_state`
   等恢复能力。
2. 这些能力没有通过 canonical role/tool path 暴露给角色执行链。
3. `roles.runtime` 在 turn 完成后，也没有形成稳定的“raw transcript +
   continuity projection + Context OS snapshot -> 回写 `roles.session`”闭环。

结果是：

1. Context OS 更像旁路投影视图，而不是 session 真实可恢复的 working-memory。
2. tool path 能“看到”新的抽象，但不能稳定“用到” session 持久化结果。
3. session continuity 与真实 session source-of-truth 可能再次漂移。

---

## 2. 问题

需要明确三个边界：

1. session-scoped memory restore 到底由谁拥有 authority
2. Context OS 快照应持久化到哪里
3. 角色工具链如何在不污染 KernelOne 通用性的前提下访问 session memory

---

## 3. 决策

### 3.1 `roles.session` 是 restore authority

`roles.session` 继续作为唯一的 session source-of-truth owner：

1. 原始消息 transcript
2. session-level `context_config`
3. 派生的 `session_continuity`
4. 派生的 `state_first_context_os`

`KernelOne` 提供 generic mechanics，但不拥有持久化 session truth。

### 3.2 Context OS 只持久化派生 working-state 视图

不在 `roles.session` 中再造第二份 transcript truth。

允许持久化：

1. `session_continuity`
2. `state_first_context_os`

禁止持久化为 canonical truth：

1. append-only raw transcript log 的替代品
2. 完整历史消息的第二份影子 source-of-truth

### 3.3 通过 canonical toolkit 暴露 memory restore

新增 canonical tool surface：

1. `search_memory`
2. `read_artifact`
3. `read_episode`
4. `get_state`

这些工具在 `KernelOne` executor 中保持通用接口，但真正的 session data
来源由 `roles.session` 的 `RoleSessionContextMemoryService` 提供。

### 3.4 Role tool gateway 改为 per-request

`RoleToolGateway` 不再按 role 长期缓存。

原因：

1. gateway 带有 request/session 相关的可变上下文
2. session-scoped memory tools 需要稳定的 request-local binding
3. 继续按 role 复用会制造跨 turn / 跨 session 状态泄漏风险

---

## 4. 为什么不是别的方案

### 4.1 不是让 `KernelOne` 直接持久化 session memory

因为这会破坏 `Single State Owner` 原则。  
`KernelOne` 负责通用 runtime mechanics，不负责 Polaris 的 session truth ownership。

### 4.2 不是继续把 restore 能力留在 host 层

因为 host 层只能做 UX/rendering，不应拥有 session memory 事实来源。
否则 CLI、API、Electron 会继续各自实现一套 recall 逻辑。

### 4.3 不是只增加几个工具定义

只加工具定义并不能解决真实问题。  
问题不是“工具名缺失”，而是“session-scoped persisted state 没有 canonical restore authority”。

---

## 5. 后果

### 正面影响

1. `roles.session` / `roles.runtime` / `roles.kernel` / `KernelOne toolkit`
   之间的 authority 链清晰了。
2. Context OS 开始成为真实可恢复的 session working-memory，而不是只存在于 prompt projection。
3. 新增 memory tools 后，Agent 可以通过标准工具面做可逆 recall，而不是依赖摘要猜测。
4. per-request gateway 消除了 role 级共享可变状态带来的 session 泄漏风险。

### 代价

1. 需要补齐 catalog/cell governance 资产。
2. 需要修复若干测试中的 `_execute_single_tool` 签名漂移。
3. 非 CLI host 还需要后续 UX 适配，才能把 recall 能力显式暴露给最终用户。

---

## 6. 验证

对应验证资产：

1. `docs/governance/templates/verification-cards/vc-20260326-roles-session-context-os-memory.yaml`
2. `polaris/cells/roles/session/tests/test_context_memory_service.py`
3. `polaris/kernelone/llm/toolkit/tests/test_session_memory_tools.py`
4. `polaris/cells/roles/runtime/tests/test_host_session_continuity.py`

---

## 7. 后续方向

1. 让 `history_materialization` 与 prompt composer 直接消费 artifact/episode restore path。
2. 为非 CLI host 增加显式 memory recall UX。
3. 把 session-scoped recall 评测纳入 context-engine evaluation harness。
