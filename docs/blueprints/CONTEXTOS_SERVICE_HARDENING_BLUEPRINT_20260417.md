# ContextOS 四层正式化硬化蓝图

状态: Closed / Implemented Structural Phase  
日期: 2026-04-17  
最后更新: 2026-04-17  
适用范围: `polaris/kernelone/context/**`、`polaris/cells/roles/kernel/internal/context_gateway/**`、`polaris/cells/roles/kernel/internal/**`

> 目标: 在不重新打开 `TransactionKernel` 主链路的前提下, 将 `TruthLogService`、`WorkingStateManager`、`ReceiptStore`、`ProjectionEngine` 从“有代码但仍部分占位”的状态, 升级为 ContextOS 的单一权威实现, 并替代残留的旧内联逻辑。
> 本文是下一阶段蓝图, 不覆盖 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 的当前事实裁决。
>
> 2026-04-17 交付结果:
> - 四层服务已完成 authoritative 接管并落地到 runtime + gateway 主路径。
> - `kernel + context` 全目录联合回归通过: `1745 passed, 5 skipped`。

---

## 0. 文档裁决

1. 当前现实仍以 `AGENTS.md`、`docs/AGENT_ARCHITECTURE_STANDARD.md`、`docs/graph/catalog/cells.yaml` 为准。
2. `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md` 与其 Closure Matrix 负责解释“事务内核已收口到哪里”。
3. 本文只负责下一阶段: `ContextOS` 四层服务正式化, 不重新定义 `TransactionKernel`、`ContextHandoffPack` 或 speculative execution 的权威语义。
4. 本阶段默认不新增第二套 schema、不新增第二条投影路径、不恢复 dual-track。

---

## 1. 问题定义

上一轮已经完成:

- `TransactionKernel` 切主
- `TurnEngine` 收敛为 facade / shim
- `StateFirstContextOS` dual-track 退役
- `ContextOSSnapshot.to_dict()` transcript nuke 修复
- `ToolSpecRegistry` 单一真相收口

但 `ContextOS` 四层仍处于“文件已存在, 权威性不足”的状态:

- `TruthLogService` 未完全接管 transcript append / trim / export
- `WorkingStateManager` 未完全接管 working state 真相
- `ReceiptStore` 已可用, 但尚未完全替代旧 receipt 内联处理
- `ProjectionEngine` 已接入, 但仍需彻底纯化为只读投影层

这意味着系统已经摆脱了“双主内核”, 但还没有完全摆脱“ContextOS 服务存在、内联逻辑也还在”的混合态。

---

## 2. 核心裁决

### 2.1 四层职责固定

| 层 | 唯一职责 | 允许写入 | 禁止行为 |
|---|---|---|---|
| `TruthLogService` | append-only 事实日志 | `truth_log` | 直接裁剪 prompt、混入 control-plane 字段 |
| `WorkingStateManager` | 可变工作态 | `working_state` | 直接回写 truth log、偷存 raw tool output |
| `ReceiptStore` | 大对象/回执承载 | `receipt_store` | 参与 prompt 组装、承载控制决策 |
| `ProjectionEngine` | 只读 prompt 投影 | 无持久化写权限 | 修改 truth/work state、私自持久化 |

### 2.2 单一权威原则

1. `truth_log` 的写入统一经 `TruthLogService`
2. `working_state` 的写入统一经 `WorkingStateManager`
3. transcript nuke / 大对象外置统一经 `ReceiptStore`
4. prompt 构建统一经 `ProjectionEngine`
5. `StateFirstContextOS` 只保留 orchestration, 不再自持业务逻辑

### 2.3 不做的事

本阶段明确不做:

1. 不重新设计 `TransactionKernel`
2. 不推进 `SpeculativeExecutor` / `StreamShadowEngine` 接主路径
3. 不新建第二套 `ContextHandoffPack` / receipt / snapshot schema
4. 不做大规模命名迁移
5. 不为了兼容旧 parity 测试恢复旧语义

---

## 3. 当前代码事实与硬化目标

### 3.1 当前事实

根据 2026-04-17 Closure Matrix:

- `ProjectionEngine`: **Auth**
- `ReceiptStore`: **Auth**
- `TruthLogService`: **Auth**
- `WorkingStateManager`: **Auth**
- `StateFirstContextOS`: 已只保留 pipeline 路径

### 3.2 硬化目标

本阶段完成后应达到:

1. 四层各自拥有清晰 public methods 与单一写权限
2. `StateFirstContextOS` 内部旧内联实现大幅缩减为 orchestration / wiring
3. snapshot / export / restore 只经 canonical service path
4. prompt projection 不再直接读取未净化的控制字段
5. 所有大对象外置逻辑统一落到 `ReceiptStore`

当前结果: 已达成。

---

## 4. Authority Map

### 4.1 TruthLogService

责任:

- 追加用户消息、assistant 输出、tool result 摘要、handoff 事实
- 维护 append-only 顺序与可导出形态
- 提供 transcript 读取视图给 `ProjectionEngine`

输入:

- user message
- committed assistant content
- tool execution summary
- handoff/export facts

输出:

- canonical truth entries
- exportable transcript view

收口目标:

- 所有 truth append 入口统一走 service
- `StateFirstContextOS` 不再手拼 transcript_log

### 4.2 WorkingStateManager

责任:

- 保存可变工作态
- 维护 task-local / turn-local 派生状态
- 为 projection 提供净化后的 state view

输入:

- normalized tool receipts
- derived state transitions
- handoff checkpoint state

输出:

- working state snapshot
- projection-ready state payload

收口目标:

- `working_state` 统一经 manager 读写
- 禁止其他组件直接操作内部 dict 结构

### 4.3 ReceiptStore

责任:

- 存储超大 transcript、tool artifacts、serialized exports
- 生成稳定 receipt refs
- 提供 export / import / round-trip 能力

输入:

- oversized transcript segments
- large tool outputs
- snapshot attachments

输出:

- `receipt_ref`
- externalized payloads
- importable materialized content

收口目标:

- 所有“大于阈值外置”逻辑统一走 `ReceiptStore`
- 删除分散的手工 `<receipt_ref:...>` 生成逻辑

### 4.4 ProjectionEngine

责任:

- 读取 truth log / working state / receipts
- 生产 prompt-safe projection
- 执行 prompt 污染隔离

输入:

- truth view
- working state view
- receipt refs / materialized snippets

输出:

- prompt projection
- summary blocks
- safe context slices

收口目标:

- `context_gateway` 不再内联拼 prompt
- raw tool output、thinking residue、policy metadata 不进入 projection

---

## 5. 实施顺序

采用“替换一层, 验证一层, 删除一层”的固定顺序。

### Phase 0: Blueprint + Verification Card

产物:

- 本蓝图
- 新 Verification Card
- 逐层 authority checklist

完成标准:

- 明确每层 owned responsibility
- 明确禁止旁路写入点

### Phase 1: TruthLogService Authoritative

工作:

- 找出所有 transcript / truth append 点
- 收口到 `TruthLogService`
- 让 `StateFirstContextOS` 只调用 service

完成标准:

- truth append 不再散落在 runtime 内联逻辑
- transcript export 只来自 canonical service

### Phase 2: ReceiptStore 收口

工作:

- 统一 snapshot nuke、tool artifact 外置、导入恢复
- 清理重复 receipt ref 生成逻辑

完成标准:

- 所有大对象外置都经过 `ReceiptStore`
- round-trip 回归稳定

### Phase 3: WorkingStateManager 收口

工作:

- 统一 working state mutation
- 把 runtime 内部 state dict 操作迁移到 manager

完成标准:

- working state 读写经过 manager
- runtime 不再直接散写 mutable state

### Phase 4: ProjectionEngine 纯化

工作:

- 将残留 prompt 组装逻辑迁入 `ProjectionEngine`
- 增加 control-plane / data-plane 隔离测试

完成标准:

- `ProjectionEngine` 成为唯一 projection builder
- `context_gateway` 仅负责 orchestration / delegation

### Phase 5: 删除残留内联逻辑

工作:

- 删除已被 service 接管的旧 helper / inline path
- 标记或移除无效 compatibility glue

完成标准:

- 四层服务成为唯一权威实现
- runtime 内只保留 wiring

执行状态: 已完成。

---

## 6. 受影响文件候选

优先检查:

- `polaris/kernelone/context/truth_log_service.py`
- `polaris/kernelone/context/working_state_manager.py`
- `polaris/kernelone/context/receipt_store.py`
- `polaris/kernelone/context/projection_engine.py`
- `polaris/kernelone/context/context_os/runtime.py`
- `polaris/kernelone/context/context_os/models.py`
- `polaris/cells/roles/kernel/internal/context_gateway/gateway.py`

可能联动:

- `polaris/cells/roles/kernel/internal/context_gateway/*`
- `polaris/cells/roles/kernel/internal/turn_materializer.py`
- `polaris/cells/roles/kernel/internal/exploration_workflow.py`

默认不应联动:

- `polaris/domain/cognitive_runtime/models.py`
- `polaris/cells/factory/cognitive_runtime/public/contracts.py`
- speculative execution 相关文件

---

## 7. 验证门禁

本阶段每个 phase 都必须执行最小门禁, 不允许长时间阻塞式“大跑”替代分层验证。

### 7.1 通用门禁

1. `python -m ruff check <paths> --fix`
2. `python -m ruff format <paths>`
3. `python -m mypy <paths>`

### 7.2 测试门禁

1. `python -m pytest polaris/kernelone/context/tests -q`
2. `python -m pytest polaris/cells/roles/kernel/tests -q`

---

## 8. 执行结果（2026-04-17）

### 8.1 代码落地

1. `TruthLogService`：增加 `append_many` / `replace` / 规范化导出路径，并在 runtime 中作为 transcript canonical writer。
2. `WorkingStateManager`：引入 canonical `WorkingState` 持有与 `replace/current/export`；runtime 回写统一经 manager。
3. `ReceiptStore`：增加 `offload_content` / `export_receipts` / `import_receipts`，并用于 snapshot round-trip 与 projection 外置。
4. `ProjectionEngine`：接管 payload 构建、control-plane 剥离、receipt 引用注入与 message 投影。
5. `StateFirstContextOS`：`project_messages` 与 `_project_via_pipeline` 统一委托四层服务，不再保留旁路写入路径。
6. `context_gateway`：`_build_projection_dict` + `build_context` 改为委托 `ProjectionEngine`，仅保留 orchestration。

### 8.2 验证证据

1. `python -m ruff check <changed-paths> --fix` ✅
2. `python -m ruff format <changed-paths>` ✅
3. `python -m mypy <changed-source-paths>` ✅
4. `python -m pytest -q polaris/kernelone/context/tests polaris/cells/roles/kernel/tests` ✅（`1745 passed, 5 skipped`）

如只改某一层, 先跑更小集合:

- `test_context_os_pipeline.py`
- `test_context_os_models.py`
- `test_attention_runtime_boundaries.py`
- 与 `context_gateway` / `kernel` 相关的最小回归集

### 7.3 阻塞策略

1. 任何单轮测试超过合理时长, 立即切回更小测试集定位
2. 先修局部, 再补联合回归
3. 禁止“卡一小时等大套件自己结束”式验证

---

## 8. 风险与防御

### 风险 1: service 名义存在, runtime 实际仍旁路写入

防御:

- 逐层搜写入点
- 用回归测试锁定唯一 authority

### 风险 2: ProjectionEngine 继续混入控制字段

防御:

- 为 projection 增加负向测试
- 明确禁止 telemetry / thinking / policy metadata 入 prompt

### 风险 3: ReceiptStore 替换过程中破坏 round-trip

防御:

- 保留 snapshot/export/import 回归
- 对 receipt ref 做稳定格式断言

### 风险 4: WorkingState 收口后引发隐式行为变化

防御:

- 逐层替换
- 先局部测试再联合回归

---

## 9. 完成定义

满足以下条件才视为本阶段完成:

1. Closure Matrix 中四层状态由 `Part` 收敛到 `Auth`
2. `StateFirstContextOS` 内部旧内联逻辑显著收缩为 orchestration
3. `context_gateway` 不再承担 prompt 组装职责
4. snapshot / receipt / projection / working state 各自只有一条权威路径
5. 目标测试与静态门禁全绿

---

## 10. 下一步执行建议

下一轮直接从 `Phase 0 + Phase 1` 开始:

1. 新建对应 Verification Card
2. 盘点所有 truth append 点
3. 先让 `TruthLogService` authoritative
4. 跑 context 最小门禁
5. 再进入 `ReceiptStore` 收口

这一步完成前, 不建议同时改 `ProjectionEngine` 与 `WorkingStateManager`。
