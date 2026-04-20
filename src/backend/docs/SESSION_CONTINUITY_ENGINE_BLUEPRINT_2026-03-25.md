# Session Continuity Engine Blueprint

状态: Draft  
日期: 2026-03-25  
范围: `polaris/kernelone/context/`、`polaris/delivery/cli/`、`polaris/cells/roles/kernel/`

> 这是目标蓝图，不是当前 graph truth。  
> 本文不得替代 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`。  
> 当前正式边界仍以 graph/catalog/subgraph/cell manifest 为准。  
> 本文的作用是为 session/history/context continuity 收口提供统一的目标架构与落点裁决。
>
> 2026-03-25 更新：
> 本文现在应被理解为共享 Agent foundation 的 continuity 子蓝图。
> 它不是 Director 私有能力，也不是 code domain 私有能力，而是未来所有角色与多种创作/生产 domain 的共同底座之一。

---

## 1. 结论

Polaris 不应继续把 session continuity 做成：

1. 宿主层里的 ad-hoc summary dict
2. 各入口重复实现的 history compaction 规则
3. “最近几轮 + 旧 history 原样回灌”的混合物

最终目标应收敛为：

`Session source-of-truth + KernelOne Session Continuity Engine + Continuity Pack projection + Role-facing recent window`

换成仓库内的表述就是：

1. `roles.session` 只拥有原始会话和消息
2. `kernelone.context` 负责 continuity policy、pack、projection、compaction
3. `delivery.cli` / `roles.runtime` 只请求 continuity projection 并持久化
4. `roles.kernel` 只消费 continuity pack，而不自己重新造策略
5. 未来其他角色或非 code domain 也复用同一 continuity foundation

---

## 2. 当前事实

截至 2026-03-25，当前仓库已经完成的正确收口只有第一阶段：

1. 默认新 session，不隐式复用 active session
2. `history` 与 `context_override` 的重复注入已移除
3. summarize 策略已不再是假滑窗

但结构上仍然不对：

1. `RoleConsoleHost` 仍直接决定 continuity pack 的生成细节
2. `RoleContextGateway` 仍直接调用 continuity summary helper
3. continuity 仍然偏向“字符串摘要 + 若干上下文字段”，不是正式 runtime contract

---

## 3. 这次蓝图明确拒绝什么

以下方向明确否决：

1. 在每个宿主入口继续手写 continuity summary
2. 把完整旧 history 当作 continuity memory 回灌给模型
3. 把 session continuity 做成纯字符串黑盒，没有结构化字段
4. 把 role/session 生命周期逻辑迁进 `KernelOne`
5. 为了“更智能”把 continuity 做成必须依赖 LLM 的不可预测黑盒

---

## 4. 目标架构蓝图

### 4.1 总体分层

```text
roles.session
  -> raw session row / raw conversation rows / context_config

kernelone.context.session_continuity
  -> policy
  -> pack builder
  -> projection engine
  -> deterministic summarizer fallback

delivery.cli / roles.runtime
  -> request projection
  -> persist continuity pack
  -> pass recent window + prompt context downstream

roles.kernel
  -> consume continuity summary / pack
  -> never own session continuity policy
```

### 4.2 关键原则

1. 原始消息和 continuity projection 必须分离
2. recent window 和 persisted continuity pack 必须分离
3. stable facts / open loops / noise decay 必须显式建模
4. continuity pack 只能是派生资产，不能反向改写 session source-of-truth
5. continuity engine 必须作为共享 Agent foundation 能力存在，而不是单角色技巧

---

## 5. Canonical Continuity Pack

### 5.1 最小结构

建议最小结构如下：

```text
version
mode
summary
stable_facts[]
open_loops[]
omitted_low_signal_count
generated_at
compacted_through_seq
source_message_count
recent_window_messages
```

### 5.2 字段语义

#### `summary`

面向 LLM 的 continuity 摘要，保留历史工程语义，不回灌低价值元话题。

#### `stable_facts`

从旧对话中提取的稳定事实，例如：

1. 当前任务约束
2. 核心故障信号
3. 关键文件/路径
4. 已确认的边界判断

#### `open_loops`

从旧对话中提取的未完成动作，例如：

1. “继续抽离 continuity engine”
2. “补测试和治理资产”
3. “跑 kernelone release gate”

#### `omitted_low_signal_count`

表示被丢弃的低价值消息数量，用于审计 continuity engine 的压缩行为，而不是把噪声重新注入 prompt。

---

## 6. Session Continuity Engine

### 6.1 输入

```text
session_id
role
session_title
workspace
messages[]
session_context_config
incoming_context
history_limit
```

### 6.2 输出

```text
recent_messages[]
prompt_context
persisted_context_config
continuity_pack
changed
```

### 6.3 职责

1. 决定 recent window 大小
2. 生成或增量更新 continuity pack
3. 过滤保留给模型的 prompt context
4. 移除 reserved/internal keys
5. 判断 continuity pack 是否需要持久化更新

### 6.4 不负责

1. session/message 行存储
2. role task orchestration
3. 具体 CLI UI 展示
4. graph truth 决策

---

## 7. 落点蓝图

### 7.1 KernelOne

主实现落在：

1. `polaris/kernelone/context/session_continuity.py`
2. `polaris/kernelone/context/__init__.py`

### 7.2 Role / Delivery 接入

接入点：

1. `polaris/delivery/cli/director/console_host.py`
2. `polaris/cells/roles/kernel/internal/context_gateway.py`

### 7.3 治理资产

继续保留在：

1. `docs/governance/templates/verification-cards/`
2. `docs/governance/decisions/`

---

## 8. 迁移步骤

### Phase 1

把 `console_host.py` 中 continuity projection 逻辑移动到 `kernelone.context.session_continuity`。

### Phase 2

让 `context_gateway.py` 通过 continuity pack 生成系统摘要消息，停止直接依赖 ad-hoc summary helper。

### Phase 3

补专门的 engine 单测，并回归现有 role streaming / leak guard / governance tests。

---

## 9. 验证蓝图

### 9.1 单测

1. continuity pack 生成正确
2. reserved keys 被过滤
3. recent window 与 compacted watermark 正确
4. 低价值元话题不会进入 continuity summary/stable facts/open loops

### 9.2 回归

1. `RoleConsoleHost` 默认新 session 语义不回退
2. `roles.kernel` summarize 行为不回退
3. visible output / tool loop / stream parity 不回退

### 9.3 治理

1. Verification Card 存在
2. ADR 存在
3. 引用路径有效

---

## 10. 目标完成态

完成后，continuity 处理应当具备以下性质：

1. `session` 和 `continuity` 不再混成一个概念
2. continuity 是 `KernelOne` 的 canonical runtime capability
3. roles 侧只接 continuity projection，不再到处拼规则
4. 旧话题不会因为 session/history/context layering 失控而反复回流
5. 该能力可被未来多角色、多 domain 共同复用
