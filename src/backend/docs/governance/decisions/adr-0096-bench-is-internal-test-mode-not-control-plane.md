# ADR-0096: Bench Is Internal Test Mode, Not Control Plane

- 状态: Accepted
- 日期: 2026-06-24
- 关联: ADR-0095(runtime realtime single rail); Run Ledger; Job Token; ContextOS; Verifier Policy

## 背景

Polaris 正在从“多角色 Agent 流水线”升级为“账本驱动的无人值守软件工厂”。在这个架构里，`Run Ledger`、`Job Token`、`Verifier Policy`、`ContextOS` 和 `ReceiptStore` 都是平台级基础设施。它们必须服务正式工作台、桌面端、Web SaaS 模式和内部压力测试。

`Bench`、`Factory Bench`、`factory_bench`、`L1-L12 bench`、benchmark harness 只是内部测试态设施，用于压测平台、暴露通用根因和生成审计证据。Bench 可以消费平台事实源，也可以在测试态产出压力样本，但 Bench 不是生产控制面、不是正式项目体验、不是 UI 事实源、不是 QA 成功条件。

如果把 Bench 路由、字段、账本路径或状态模型上升为正式语义，平台会重新出现多头事实源：正式 UI 看一套状态，QA 看一套状态，Bench 看另一套状态。无人值守控制面必须避免这种漂移。

## 决策

### 1. 平台事实源必须使用平台命名

正式能力必须落在平台级契约和命名空间：

- `runtime/control_plane/ledger`
- `/v2/control-plane/*`
- `status.control_plane`
- `RunLedgerProjection`
- `VerifierPolicy`
- `JobToken`
- `ContextOS`
- `ReceiptStore`

禁止在正式产品路径中使用 Bench 命名表达生产语义，包括但不限于：

- `benchService`
- `factory_bench`
- `Factory Bench`
- `bench session`
- `runtime/factory/ledger` 作为默认事实源
- `event.bench` 作为正式实时通道

### 2. Bench 只能是内部测试态消费者或生产者

Bench 可以在内部测试态调用平台级 API，也可以读取平台级 projection 验证控制面质量。但正式 UI、正式 QA、正式工作流和生产环境不得依赖 Bench 专属报告、audit 文件、session 状态或 runner 结果作为成功条件。

允许：

- 内部测试 runner 读取 `RunLedgerProjection` 来做压力测试报告。
- Bench 在测试态生成 synthetic workspace、failure trace、audit package。
- 迁移期显式读取旧兼容账本作为只读审计输入。

禁止：

- 正式 UI 从 `benchService` 读取控制面状态。
- QA gate 用 `factory_bench` audit 文件替代 Run Ledger receipt。
- Run Ledger projection 默认扫描 `runtime/factory/ledger`。
- Bench 成功被写成正式项目 resolved。
- 统一 runtime WebSocket 默认订阅 `event.bench`。

### 3. 兼容账本必须显式开启

迁移期允许保留旧路径 `runtime/factory/ledger` 作为兼容输入，但只能通过显式开关读取，并且 projection 必须暴露来源边界，例如 `compat_ledgers_included=true`。

默认行为必须是：

```text
RunLedgerProjection -> runtime/control_plane/ledger only
```

这保证正式环境不会因为内部测试态残留文件而显示假成功。

### 4. 可选验收能力属于平台 Verifier Policy

浏览器验证、视觉验收、多模态 QA、用户自定义脚本、物理引擎验证、算法正确性检查等能力属于平台级 `Verifier Policy`，不是 Bench 功能。Bench 只能在内部测试态覆盖这些能力是否可用。

用户不启用 browser、没有 Playwright 环境、没有多模态 LLM、没有自定义脚本时，平台必须显示能力为 disabled/unavailable，而不是 fail-open 或静默降级为 Bench 检查。

## 后果

### 正面

- 正式控制面只有一个事实源：平台 Run Ledger。
- UI、ContextOS、QA、TaskBoard、ReceiptStore 可以收敛到同一 projection。
- Bench 保留内部压力测试价值，但不能污染生产语义。
- 迁移期兼容路径可审计、可关闭、可删除。

### 负面

- 旧测试如果默认依赖 `runtime/factory/ledger`，必须改为显式兼容读或迁移到 `runtime/control_plane/ledger`。
- 一些历史 UI 文案和测试 fixture 需要从 Bench 命名改为 Control Plane/Verifier 命名。

## 验收

- `ReadRunLedgerProjectionQueryV1.include_compat_ledgers` 默认必须为 `false`。
- `/v2/control-plane/ledger/projection` 不得默认读取 Bench/Factory 兼容账本。
- 正式前端服务不得通过 `benchService` 获取控制面 projection。
- Runtime realtime projection 必须走 `status.control_plane`，不得走 `event.bench`。
- `useRuntimeConnection` 默认订阅通道不得包含 `event.bench`；只有内部测试态显式开启时才能订阅。
- Bench 文案、路由、状态模型不得出现在生产模式导航或正式项目工作台。
