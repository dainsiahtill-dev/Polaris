# ADR-0046: Promote Session Continuity to a KernelOne Capability

状态: Accepted  
日期: 2026-03-25

---

## 1. 背景

Polaris 已经修复过一轮 session/history/context layering 失控问题：

1. 默认新 session
2. 移除 history 双重注入
3. summarize 改成真实 continuity summary

但第一轮修复仍然主要落在 `RoleConsoleHost`，并没有把 continuity 变成统一的底座能力。

结果是：

1. `console_host` 仍直接决定 continuity projection
2. `roles.kernel` 仍直接生成 continuity summary
3. continuity 仍然偏向“ad-hoc dict + summary string”

这不符合 `KernelOne` 作为 Agent/AI 运行时底座的职责划分。

---

## 2. 问题

我们需要回答三个问题：

1. session continuity 到底属于 roles 还是 KernelOne
2. continuity 应该只是一个 summary 字符串，还是结构化 runtime asset
3. 如何避免之后又在不同入口重复实现 continuity 策略

---

## 3. 决策

### 3.1 归属

把 session continuity 提升为 `KernelOne` 能力。

canonical 落点：

- `polaris/kernelone/context/session_continuity.py`

### 3.2 数据模型

不再把 continuity 视为单纯 summary 字符串，而是结构化 `SessionContinuityPack`：

1. `summary`
2. `stable_facts`
3. `open_loops`
4. `omitted_low_signal_count`
5. `compacted_through_seq`
6. `source_message_count`
7. `recent_window_messages`

### 3.3 边界

1. `roles.session`
   继续拥有原始 session/message source-of-truth
2. `kernelone.context`
   拥有 continuity pack / projection / policy / deterministic summary fallback
3. `delivery.cli` 与 `roles.runtime`
   只调用 continuity engine，并持久化 projection
4. `roles.kernel`
   只消费 continuity pack，不再自己重复实现策略

---

## 4. 为什么不是别的方案

### 4.1 不是继续放在 `console_host`

因为 continuity 不是 CLI 私有逻辑，而是所有 role 宿主都可能需要的 runtime 能力。

### 4.2 不是做成三方独立库

因为当前还需要与仓内的 session 模型、治理资产、verify pack、KernelOne release gate 一起演进。
过早包外抽离会冻结接口，反而更难收口。

### 4.3 不是纯 LLM summary

因为 continuity 必须有 deterministic fallback，不能把会话恢复能力建立在不可控黑盒之上。

---

## 5. 后果

### 正面影响

1. continuity policy 有了统一实现点
2. roles 层只保留 source-of-truth 和接入逻辑
3. continuity pack 具备结构化字段，后续可做更智能的 ranking/decay/persistence
4. 新入口接入 continuity 时不再需要复制 role-host 逻辑

### 代价

1. 需要补 KernelOne 侧单测与 release gate 回归
2. 当前 graph truth 还没有 dedicated continuity 节点，文档必须持续标明“目标蓝图，不是现状”

---

## 6. 验证

本 ADR 对应的验证资产：

1. `docs/governance/templates/verification-cards/vc-20260325-session-continuity-engine-kernelone.yaml`
2. `polaris/kernelone/tests/test_session_continuity_engine.py`
3. `polaris/delivery/cli/director/tests/test_stream_protocol.py`
4. `polaris/cells/roles/kernel/tests/test_transcript_leak_guard.py`

---

## 7. 后续方向

本次只完成 deterministic continuity engine 收口。

后续可在不改变 ownership 的前提下继续增强：

1. topic decay / TTL
2. hybrid deterministic + LLM summarization
3. continuity pack ranking
4. 更多宿主入口统一接入
