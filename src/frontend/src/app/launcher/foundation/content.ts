import type { FoundationContent } from './schema';
import { historyExperiencePaper, historyExperienceTopics } from './historyExperience';

export const foundationContent: FoundationContent = {
  "title": "Polaris 底座原理",
  "subtitle": "事务执行、上下文编译与工程交付的源码研究",
  "reviewedOn": "2026-10-14",
  "basis": "当前工作区源码与封存执行证据；设计、默认接入、运行结果分别讨论。",
  "topics": [
    ...historyExperienceTopics,
    {
      "id": "overview",
      "title": "从决策到工程结果",
      "subtitle": "先看完整闭环，再进入每一层",
      "summary": "谁拥有事实、谁能执行、谁负责接受。",
      "markdown": "# 从生成式决策到可验证的工程结果\n\nPolaris 将模型决策、工具效果、运行状态、上下文和验收证据分开，再通过执行身份与公开契约把它们连接起来。理解底座的关键是：谁拥有事实，哪一步真正产生副作用，哪一种完成能被下一层消费。\n\n## 两条相互连接的路径\n\n执行路径由 PM 的任务合同、CE 的蓝图与 handoff、Director 的工具执行及修复、Verifier 与 QA 的验收组成。模型可以提出方案与调用，但文件、进程、权限及验收状态由确定性实现控制。\n\n上下文路径把会话与状态提取为当前角色的输入，加入合同、蓝图、目标文件、失败反馈与工具规范，再经过窗口预算和 provider 协议转换。上下文投影说明模型可能看到什么；最终 wire 审计才说明受治理发送路径实际发送了什么。\n\n## 必须区分的四类对象\n\n| 对象 | 回答的问题 | 示例 |\n| --- | --- | --- |\n| 事实 | 什么真实发生，谁负责 | owner event、DEO commit、进程终态 |\n| 投影 | 当前如何理解多个事实 | TaskBoundary、Run Ledger、ContextOS UI |\n| 缓存或引用 | 如何避免重复搬运内容 | ContentStore、ReceiptStore、大文本指针 |\n| 权限 | 当前可以做什么 | 合同派生 JobToken、fresh claim、一次 fence |\n\n投影可以重建，缓存可以过期，权限可以失效。它们与事实相连，但不能互相替代。\n\n## 至少六种完成\n\n一次模型请求结束、一次效果收据提交、一轮内存状态完成、任务完成、Factory 编排结束与 ProjectOutcome 接受，分别回答不同问题。TransactionTurn 的内存 COMPLETED 早于持久 OutcomeCommit；项目接受需要当前多个 owner 的证据。失败的运行可以安全释放资源，但不会因此变成验收通过。\n\n## 本研究如何阅读\n\n总体架构说明 Cell 和 KernelOne 的关系；TransactionTurn 展开一次执行的状态与提交屏障；ContextOS 展开模型输入；控制与交付解释多个拥有者如何接受、修复和收尾。模块索引说明默认机制、基础支撑、可选组件和研究性实现；论文集提供完整专题与源码证据。\n\n当前研究以 2026-10-14 的源码及封存失败记录为依据。已经运行过构建、类型检查与测试，不代表指定入口、独立 QA 与完整交付已经完成；未完成验证会明确保留。\n",
      "diagram": "architecture"
    },
    {
      "id": "architecture",
      "title": "Cell 与 KernelOne",
      "subtitle": "能力边界与图谱",
      "summary": "Graph、公开契约、单一状态拥有者和上下文资产。",
      "markdown": "# Cell 与 KernelOne：能力边界、事实拥有者与上下文资产\n\nPolaris 的架构应从能力图谱理解，而不是从目录数量推导。当前 catalog 自己声明为迁移期目录，包含目标拥有权与 verification gaps；Cell 在图谱中出现，不代表其所有内部实现、默认接入与运行效果已经验收。\n\n## 六个阅读层\n\n| 层 | 职责 | 关键边界 |\n| --- | --- | --- |\n| KernelOne 通用底座 | 文件与事件存储、锁、工具规范、模型适配、上下文基础组件 | 提供原语；不接管各 Cell 的工程语义 |\n| Cell 能力与契约 | owned_paths、depends_on、state_owners、effects_allowed、公开 DTO | 单一拥有者；跨 Cell 通过 public 契约 |\n| 角色执行协议 | 角色身份、上下文、决策、工具批次、DEO、回合结果 | 模型输出是候选；执行与提交由代码控制 |\n| 控制与交付 | PM 合同、CE 交接、任务执行、验证、QA、项目结果 | 一个 completed 不能代表全部验收 |\n| 上下文与观测 | StateFirst、投影、预算、快照、读模型 | 解释与展示事实；不凭展示授予权限 |\n| 传输与实例 | HTTP 一次查询、runtime.v2 WebSocket、Launcher | 每个实例绑定一个 workspace；实时订阅有范围 |\n\n这些是阅读分类，不是新增六个服务，也不意味着每一层都是独立进程。项目运行通常是一个绑定 workspace 的后端，多个能力在同一进程中通过受约束契约组合。\n\n## Cell 的最小职责是什么\n\n一个 Cell 同时声明代码拥有路径、对外能力、依赖、状态拥有权、允许的副作用与验证缺口。查询描述读模型，命令描述变更，event 表示拥有者发布的变化；result 和 error 使失败可以跨边界传播。目录 public/internal 是实现屏障，Graph 是声明所有权的依据。\n\n遵循顺序是 existing Cell public contract、KernelOne 契约、必要的新实现。若每个角色再造一套文件写入、命令执行或验收协议，就会出现不一致的能力授权和第二事实源。反过来，单一拥有者也不表示只有一份物理文件：原始拥有者事件、可重建投影、缓存、UI 都可以有各自存储，但不能互相冒充权威。\n\n## Descriptor、Context Pack、Verify Pack 的分工\n\nDescriptor 用于发现与排序，Context Pack 用于某项工作，Verify Pack 用于验证。它们应绑定源与图谱版本，不能用一个检索结果直接扩大写权限，也不能因为某个生成包说模块已完成就免除真实执行验收。\n\nDescriptorPack 记录 cell_id、source_hash、capabilities 和 embedding_runtime_fingerprint。现有 source_hash 是所选文件内容组合的 SHA-256 截断值，适合变化标记，不是业务正确性证明。实际 generator 的 schema 检查也有具体范围，不能把浅字段存在检查宣传成完整形式验证。\n\n## 可组合不等于已验证可移植\n\npublic DTO、依赖注入与 source owner 分离有利于替换实现。能否把某个 Cell 移到另一部署环境，仍需验证环境依赖、文件布局、事件语义、鉴权、进程生命周期和版本兼容。当前论文只说明可重组的结构，没有进行跨机器迁移实验。\n\n## 源码与图谱入口\n\n- src/backend/AGENTS.md：当前后端权威关系与 Cell/KernelOne 复用原则。\n- src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md：契约、状态拥有者、上下文与验证资产的分工。\n- src/backend/docs/graph/catalog/cells.yaml：声明能力目录和迁移缺口。\n- src/backend/docs/graph/subgraphs/roles_execution_pipeline.yaml：角色执行子图。\n- src/backend/docs/graph/subgraphs/context_assembly_pipeline.yaml：上下文装配子图。\n- src/backend/polaris/cells/context/catalog/internal/descriptor_pack_generator.py:32：DescriptorPack。\n- src/backend/polaris/cells/context/catalog/internal/descriptor_pack_generator.py:111：当前 schema 校验范围。\n- src/backend/polaris/cells/context/catalog/internal/descriptor_pack_generator.py:392：生成入口，不代表自动进入每次角色执行。\n",
      "diagram": "architecture"
    },
    {
      "id": "transaction",
      "title": "TransactionTurn",
      "subtitle": "事务驱动的角色执行",
      "summary": "一次回合的模型决策、工具、效果与持久提交。",
      "markdown": "# Polaris 事务回合协议：生成式决策、效果授权与持久提交\n\n## 摘要\n\n以实际 TransactionTurnExecutor、TransactionKernel、DEO 与 OutcomeCommit 调用链为对象，分析角色执行如何将模型候选转换为受约束的工具批次，区分推测读取、持久 claim、一次消费 fence、效果收据、回合落盘与项目接受，并说明这些机制与数据库事务的差异。\n\n关键词：Polaris；工程 Agent；状态拥有者；源码分析；执行证据\n\n研究日期：2026-10-14。方法为源码、调用链与封存失败记录分析。以下机制的实现存在性、默认接入和端到端可靠性分别讨论。行号以本次工作区为准，源文件指纹随研究证据包提供。\n\n## 1. 核心论点与命名\n\nPolaris 将不确定的模型输出放入确定性的执行协议：先确定角色、任务与执行身份，再把模型产生的调用转成可验证的工具批次；具有副作用的成员需通过范围授权、意图登记和即时执行许可，执行后的效果收据与整轮结果分别落盘。其主要价值是让失败能够归因到具体边界，并防止同一许可被重复消费。它不会替模型证明方案正确，也不会把任意文件、进程和外部服务变成一个数据库事务。\n\n真实源码名是 **TransactionTurnExecutor** 和 **TransactionKernel**。前者是角色内核的应用服务；后者位于 `roles.kernel` Cell，继承 `TurnTransactionController` 并委托执行。`TransactionTurn` 适合作为本文的概念名；`Transation` 不是这些符号的拼写。KernelOne 为该协议提供通用存储、锁、工具规范、模型适配等底座，不能把整个执行协议归为一个 KernelOne 数据库事务。源码依据：[T01]、[T02]、[T03]。\n\n还存在名为 `TaskBoard.transaction()` 的接口，其实现仅持有 `RLock` 并 `yield`。这保护同进程临界区，不代表任意异常可回滚全部文件与外部操作。[T40]\n\n## 2. 术语与四种不能混同的状态\n\n| 术语 | 具体含义 | 不能推导的结论 |\n|---|---|---|\n| Turn / 事务轮 | 一次角色内核调用、模型决策、工具批及收口 | 项目已完成 |\n| Invocation / Attempt | 稳定上游执行范围下的逻辑调用及确定性尝试身份 | 身份本身赋予写权限 |\n| Session | 原任务范围内连续调用多个 Turn 的会话 | 另一个工具执行权威或新的任务 |\n| TaskRuntime execution attempt | 当前任务执行会话、owner/claim/session 等约束绑定 | Factory 或 QA 已通过 |\n| ToolBatch | 解码后按效果与执行模式分组的工具调用 | 整批文件写入具有数据库原子性 |\n| DEO（Directed Effect Operation） | 对一项有副作用的操作登记意图、claim、收据与恢复状态 | 收据证明业务行为正确 |\n| Prepared inventory | 不可变的完整成员清单及授权快照 | 此时已经执行写入 |\n| Fence | 与 PID、原始对象身份、精确哈希绑定的一次消费许可 | 可跨进程重建或随意重放 |\n| Effect receipt | 真实操作结果及其身份、策略、状态哈希绑定 | 质量验收 verdict |\n| TurnOutcome / SealedTurn | 已持久记录的整轮结果及引用 | 任务或项目验收成功 |\n| Settlement | 任务/工具/副作用义务已终止，具备释放资源的证据 | `passed=true` |\n| Verdict | 在当前合同、当前输入及 verifier/QA 证据下的验收判断 | 能代替真实执行收据 |\n\n四个状态层分别是：单轮 `TurnState`；跨轮 Session 的进展；TaskRuntime 的任务/执行尝试；Factory 与 Run Ledger 的项目接受/结算。它们应关联，但不能仅凭同名 `completed` 相互替代。[T09]、[T29]、[T31]、[T41]\n\n## 3. 真实路径：LLM 决策在哪里，确定性代码在哪里\n\n| 步骤 | 当前入口与工作 | LLM 参与 |\n|---|---|---|\n| Bind / Prepare context | `TransactionTurnExecutor` 验绑定 attempt，检查 DEO 装配，构建上下文与最终工具表 | 该步骤自身不决定业务动作 |\n| Freeze invocation facts | 创建 kernel 时重新验证当前 attempt，并固定该轮工具与预算事实 | 无 |\n| Decision | 请求 provider，解码为 `TurnDecision`，检查 native 调用是否丢失、delivery mode 是否允许写 | 模型产生候选回答/调用；解码与门禁由代码执行 |\n| Plan ToolBatch | 使用显式的 parallel readonly、serial readonly、serial writes、async 分组；检查 scope 和调用效果 | 模型提出调用；执行计划和效果分类不由文字声明授权 |\n| Prepare DEO | 授权每个成员，登记 parent，封存完整 inventory，再登记 operation | 无 |\n| JIT claim | 刷新当前 attempt，读严格最新 stream head，以 CAS 领取精确成员许可，捕获当前策略 | 无 |\n| Execute / receipt | 注册并消费 fence，重新验证策略，调用私有物理执行器，观察结果并提交效果收据 | 无 |\n| Finalize | NONE 返回工具结果；LOCAL 本地整理；LLM_ONCE 生成摘要，工具被禁用 | 仅 LLM_ONCE 再调用模型 |\n| OutcomeCommit | 构造 canonical outcome、落事件、得到 commit receipt 与 sealed turn | 无 |\n| Continue / verify / settle | 会话消费已提交轮结果；外层任务与 Factory 执行 verifier/QA、结算及释放 | 下一轮决策或语义 QA 可调用模型；状态和释放依据由代码投影 |\n\n源码顺序可直接定位 `TransactionTurnExecutor.execute_turn()`、`TurnTransactionController.execute()` 和 `_execute_turn()`。[T01]、[T05]、[T06]、[T07]。这里的 **Plan** 是执行计划的概念，不等于每轮必有一个独立 Planning Agent 或额外规划 LLM。Chief Engineer 的项目蓝图与这一轮工具批次的执行计划处于不同层。\n\n`Speculate → Plan → ToolBatch → DEO → OutcomeCommit → settlement` 可用于教学，但不是全部运行必须串行经历的固定状态序列。推测执行可与流式模型输出重叠；非流式核心不以一个独立 Speculate 状态开头；settlement 在轮协议外，读取已落事实。\n\n## 4. 单轮状态机与持久提交屏障\n\n下图实线来自 `TurnState` 的允许转换。简化展示主要成功路径；省略移交与部分 FAILED 边，但没有添加不存在的 COMMITTED 枚举。\n\n```mermaid\nstateDiagram-v2\n    [*] --> IDLE\n    IDLE --> CONTEXT_BUILT\n    CONTEXT_BUILT --> DECISION_REQUESTED\n    DECISION_REQUESTED --> DECISION_RECEIVED\n    DECISION_RECEIVED --> DECISION_DECODED\n    DECISION_DECODED --> FINAL_ANSWER_READY: 直接回答\n    FINAL_ANSWER_READY --> COMPLETED\n    DECISION_DECODED --> TOOL_BATCH_EXECUTING: 工具批\n    TOOL_BATCH_EXECUTING --> TOOL_BATCH_EXECUTED\n    TOOL_BATCH_EXECUTING --> DECISION_DECODED: 有限合同纠正\n    TOOL_BATCH_EXECUTED --> COMPLETED: NONE / LOCAL\n    TOOL_BATCH_EXECUTED --> FINALIZATION_REQUESTED: LLM_ONCE\n    FINALIZATION_REQUESTED --> FINALIZATION_RECEIVED\n    FINALIZATION_RECEIVED --> COMPLETED\n    DECISION_DECODED --> SUSPENDED: ASK_USER\n    DECISION_REQUESTED --> FAILED\n    TOOL_BATCH_EXECUTING --> FAILED\n    COMPLETED --> [*]\n    FAILED --> [*]\n    SUSPENDED --> [*]\n```\n\n`TOOL_BATCH_EXECUTED` 返回 `DECISION_REQUESTED`、回到 `CONTEXT_BUILT` 或再次解码的普通循环被明确禁止；finalization 发起工具执行也被禁止。有限合同纠正、独立后继轮或显式 workflow 是不同机制，不能把禁止同一轮无限续跑理解为禁止整个任务持续执行。[T09]\n\n**持久化位于状态机之外的提交屏障**：`execute()` 先 `await _execute_turn()`，再 `_commit_canonical_outcome()`，成功后把 `turn_outcome`、`commit_receipt` 和 `sealed_turn` 加入结果。因而内存 `COMPLETED` 不能证明 outcome 已写入，更不能证明项目通过。[T05]\n\n```mermaid\nflowchart LR\n    A[单轮状态机结束] --> B{需要 durable commit?}\n    B -->|否: 临时聊天| C[返回临时结果]\n    B -->|是: 原任务轮| D[append_fact_event 严格写入]\n    D -->|成功| E[CommitReceipt + SealedTurn]\n    D -->|失败| F[TurnOutcomeCommitError]\n    E --> G[原 Session 消费已提交结果]\n    G --> H[下一轮或 TaskBoundary / QA / settlement]\n```\n\n异常发生后，controller 可尝试提交 error outcome；若原异常就是提交错误，不会伪造成功 receipt。当前 task outcome 的 idempotency key 绑定 workspace token、run、task、turn 与 transition；sessionful 写入要求 fsync，sessionless 路径采用 buffered durability。[T05]、[T27]。因此“所有结果均已 fsync”也是过度概括。\n\n## 5. Speculation：提前读，写入仅提前准备\n\n`ENABLE_SPECULATIVE_EXECUTION` 优先于兼容变量 `KERNELONE_ENABLE_SPECULATIVE_EXECUTION`，默认值为 **enabled**；领养结果重算审计 `SPECULATION_AUDIT_ADOPTIONS` 默认关闭。必须区分“能力默认打开”和“此次路径实际执行、领养并经审计证明正确”。[T22]\n\n流式 shadow 能提前执行便宜的只读调用，并按工具、归一化参数及环境指纹登记结果。正式调用可 ADOPT 已结束结果、JOIN 在跑结果或 REPLAY 正式执行。写工具的 shadow 仅做只读 Prepare：当前实现用 `file_exists` 探测路径，私有 sentinel key 防止与模型真实 read 调用碰撞；它不检查所有语法，更不是文件系统写入。[T23]、[T24]\n\n正式执行前仍完成 DEO inventory；WRITE/ASYNC 不会因为旧工具名称表未识别就作为 READ 领养。写 Prepare 缺失或优化失败可回到同一个正式授权批次执行，不能绕过权限。启用 adoption audit 时，领养读结果会和权威重算比较；默认关闭时，没有每次双算保证。[T25]\n\n推测不会自动构成隔离性：提前读到的文件可能随后变化。系统通过参数/环境指纹、当前策略及目标状态哈希约束复用与写前基线，但这不等于为整个项目创建数据库级一致性快照。\n\n## 6. Prepare、Carry、Freeze、Claim、Commit、Abort、Recovery\n\n这些词在不同层有具体对象，不能只看名字相同就认为协议相同。\n\n**Prepare** 有两种：可选 shadow 的只读探测；正式 DEO 的成员授权与 inventory 准备。后者先验证 execution attempt，登记 parent registry，封存有序成员清单，再绑定成员策略、登记 operation 并确认 inventory readiness。完整清单在该批次任何正式 read、shadow adoption 或 effect 前准备。[T11]、[T12]、`_executor_execute.py:800`。\n\n**Carry / Freeze** 指把已捕获的 ToolSpec、normalized arguments、能力限制、policy snapshot、目标状态及成员 hash 通过类型化 DTO 传到下一层。Freeze 防止授权后再用不同 registry 或可变 metadata 偷换输入；它不冻结操作系统或 workspace，执行前仍要重新检查当前状态。调用 setup 与 batch member 的固定对象是两层事实。[T01]、[T03]、[T13]、[T34]、[T35]\n\n**Claim** 是即时授权边界：读取当前严格 operation，检查仍为 INTENT_COMMITTED 及成员身份；从当前 operation stream head 求下一 seq，再用 expected version/seq 执行 CAS。成功将意图转为 EFFECT_STARTED，并获得本次 grant。精确重复 claim 可观察已写状态，但不获得新的可执行 grant；角色层明确拒绝 `idempotent=True` 作为新派发权。[T13]、[T44]\n\n**Execute / effect Commit**：claim context 在进程内 fence 注册；adapter 验 scope、参数、当前 policy、目标状态；一次消费 fence 后才能调用物理工具。写入成功需收据，修复绑定还需正确 after state；物理操作完成而 receipt commit 失败会进入 recovery，而不是直接报成功。[T14]、[T15]、[T16]\n\n**Abort** 只适用于未 claim 的 INTENT_COMMITTED；它证明没有按该许可开始操作，不能撤销已发生效果。失败批会终止未执行兄弟成员。提前封存的 rollback contingency 也作为有授权的独立操作，仅在已证明 forward 成功且需要补偿时执行；补偿失败不得宣称 restored。未知的已开始操作保留恢复义务。[T17]、[T19]\n\n**Recovery** 处理 EFFECT_STARTED 后的歧义、无法捕获当前策略、物理失败、结果观察失败或 receipt commit 失败。它追加状态证据，不能重造已失去的进程内 fence、删历史失败或无证据地再执行旧效果。[T13]、[T15]、[T19]\n\n**OutcomeCommit** 在整轮结束后封存轮结果，是另一项 append；它不是 effect receipt 的别名，也不是把此前所有 effect 和后继 QA 一次性提交。[T27]\n\nDEO 状态图严格来自运行时加载的 `.source` 文件；`_repository.py` 显式 UTF-8 读取并 compile/exec 该文件。引用 `.source` 是实际生产执行体，并非将历史文本文档误认活代码。[T20]、[T21]\n\n```mermaid\nstateDiagram-v2\n    [*] --> INTENT_COMMITTED: admit\n    INTENT_COMMITTED --> EFFECT_STARTED: claim + CAS\n    INTENT_COMMITTED --> ABORTED: 尚未开始\n    EFFECT_STARTED --> RECEIPT_COMMITTED: 真实效果收据\n    EFFECT_STARTED --> RECOVERY_PENDING: 不确定或失败\n    RECOVERY_PENDING --> RECEIPT_COMMITTED: 严格恢复收据\n    RECOVERY_PENDING --> DEAD_LETTER: 负结果闭合证明\n    RECEIPT_COMMITTED --> CLOSED_BY_PARENT: 父任务结算\n    ABORTED --> [*]\n    DEAD_LETTER --> [*]\n    CLOSED_BY_PARENT --> [*]\n```\n\n该图没有 EFFECT_STARTED 直接 ABORTED 的边，也没有 CLOSED_BY_PARENT 回到 INTENT_COMMITTED 的边。恢复后的新执行应属于合法新 attempt/新操作，并保留原 owner 与历史，不是修改旧终态。\n\n## 7. 工具归一化与权限的先后关系\n\n工具不由模型记住某一个内部拼写来获取权限。ToolSpecRegistry 是规范工具、aliases、arg_aliases、效果分类和 provider schema 的 owner。`capture_effective_spec(raw_name)` 取得一份一致、不可变的规范名称与别名快照；参数归一化随后只消费该快照，重新校验 hash，按 schema 别名映射和专用复杂转换生成 canonical arguments。[T34]、[T35]\n\n例如 `run_command` 与 `execute_command` 可以是同一工具的名称别名，但相同名称归一化成功不证明命令已获准。危险参数、跨 workspace 路径、未授权 command/cwd、冲突别名或未注册工具仍需拒绝。参数别名是同义适配，不是推断新的业务权限。\n\n有三类容易混同的 token：\n\n1. **LLM token / window budget**：上下文和输出容量、调用预算，不赋予操作权限。\n2. **JobToken / execution envelope capability**：从项目合同与 CE 交接/控制面派生，约束 allowed paths、write paths、commands、stage 和合同/蓝图 hash。读取完整已提交 JobToken 优先于根层的有损 envelope 投影。[T33]\n3. **Execution claim / fence**：精确 task attempt、parent、member、arguments hash、当前 policy、PID/对象身份的一次执行许可。[T13]、[T14]\n\nrun/task/turn/transition ID 是定位身份；哈希是绑定与一致性校验；授权还需可信 owner/当前 attempt/策略/能力作用域。普通 SHA digest 本身并不是针对恶意重写者的数字签名，不能把“哈希相同”独立解释为“任何来源都可信”。\n\n## 8. 与数据库 ACID 和两阶段提交的差别\n\n这是基于源码边界的概念对照，不是数据库基准实验。\n\n| 属性 | 当前协议的机制 | 实际限制 |\n|---|---|---|\n| Atomicity | 单 stream append 与 seq/idempotency 分配同临界区；单操作有 claim/receipt 状态 | 整轮多个文件、子进程和外部请求没有共同 rollback transaction |\n| Consistency | DTO、合同、scope、哈希、状态合法转换和 verifier 约束 | 平台合同一致不等于用户需求正确；仍需独立验收 |\n| Isolation | stream 锁、CAS、owner lease、写前 policy/目标状态检查，批内写串行 | 未证明全 workspace 可串行化；并发读和外部主体仍可改变环境 |\n| Durability | canonical event、严格 digest、按路径选择 flush/fsync、commit receipt | buffered 不是 fsync；物理效果与整轮 append 存在独立失败窗口 |\n| 2PC 类比 | prepare inventory 后 claim/execute，具备先登记后执行的思想 | 不是所有参与者 vote prepared、全局 commit 的数据库 2PC；工具没有统一资源管理器 |\n| Exactly once | durable intent、CAS、精确 idempotency、一次 fence、恢复不盲重放 | 防重复派发约束不等于所有文件/网络/进程效果全球恰好一次；崩溃窗口可产生 UNKNOWN |\n| Rollback | 已授权、可验证的补偿操作和 after hash 检查 | 补偿也是新副作用，可能失败；不能自动撤销任意远程请求 |\n\n`JsonlEventStore.append()` 的原子性范围是一个事件流的 sequence 分配、idempotency 查找和持久写入锁边界。[T37]、[T38]。典型歧义窗口是“物理工具已执行，receipt 尚未确认”以及“效果收据已确认，TurnOutcome 写入失败”。正确处理是保留原证据、只读确认并走现有恢复协议；不能凭失败异常推出效果未发生。\n\n## 9. Event sourcing、CAS、哈希及恢复的成本\n\nFactStream public API 解析 provenance、workspace/runtime identity、run/task，转发到 KernelOne JSONL event store。传入 expected_seq 时要求精确 CAS；重复 idempotency key 必须与原语义一致，expected_seq 不符也不能静默命中另一条事件。新严格事件包含 digest；普通 `.seq` cursor 不是权威。[T36]、[T37]、[T38]\n\n严格 read 验完整尾部、每帧原始 shape、digest、连续 seq、descriptor 身份，保留一个有界页；不因为尾部损坏就接受前页为已合格证据。其成本随历史字节与帧数增长。read 缓冲上界不等于整个历史只有一次常数成本；重复跨 attempt、跨 gate 冷扫描会累计。[T39]\n\n轮提交源码说明其转换与序列化近似 `O(e+p+n)`：`e` 为执行证据、`p` 为 payload、`n` 为 idempotency stream 扫描；空间 `O(e+p)`，此外严格 append 当前会加载并验证整个历史。DEO 每次 claim 读取最新共享 operation head，前序成员产生 receipt/abort 后不能用成员序号算 head。因此这一精确性会带来更多 I/O 与锁竞争。[T13]、[T27]、[T38]\n\n这些是源码级成本分析，不代表已有正式性能测量或吞吐保证。此前外部工程审计使用全 Python tracing 曾放大一次只读查询时长；该污染探针已停止并单独记录，不能当成平台扫描性能样本。\n\n## 10. 连续自主执行：循环属于 Session，接受属于项目\n\n当前正式连续路径由 `KernelSessionTurnBridge` 接现有 `RoleSessionOrchestrator`：bridge 每轮调用 canonical role kernel，不执行工具。已提交的 prior results 形成后继 history、当前质量反馈和 source evidence；物理 turn timeout 受原 TaskRuntime 会话 deadline 约束。预算型会话可没有单独固定 turn 上限，但仍有 deadline；无预算会话保留默认 ceiling，显式 max_auto_turns 也仍有效。[T29]、[T30]、[T31]\n\n失败轮只有在携带真实 commit receipt、批次结果和 follow-up 语义时才可作为可继续观察。聚合的 `batch_projection` 标记 `projection_only=true`，同时保留各轮 receipt；Session 摘要不产生新的效果权威。[T30]、[T31]\n\n外层 PM 合同、CE 蓝图、Director 执行、verifier/QA 与 Factory 结算负责项目推进。LLM 的“完成”文字、单轮 finalization、Task1 completed、入口进程 exit 0 和 QA 接受属于不同断言。工具结果或某个 phase 通过不能替代完整项目成功。\n\nRun Ledger settlement DTO 明确：`closed` 表示义务终止，失败也能闭合；`release_allowed=closed`；`passed` 另要求 projection ok、失败计数为零、required evidence 无 missing/failed。[T41]、[T42]。释放资源不能反向修改 QA FAIL。缺 required evidence 和真实 required evidence FAIL 也不可合并，前者是未产证据，后者是产物/验收失败。\n\n## 11. 设计意图、实际能力与已暴露的断点\n\n| 设计意图 | 本次源码确认的能力 | 未证明或已知限制 |\n|---|---|---|\n| 模型决策与效果权威分离 | 解码、scope、policy、DEO、receipt 均有明确代码 owner | prompt/body/tool schema 仍需真实最终请求审计；源码 whitelist 不能代替 wire body |\n| 安全连续交付 | 原 task/session 连续 turn、当前失败反馈、真实 verifier 后继 | 不代表所有项目都能收敛；模型可能持续只读，平台预算/存储也可能先失败 |\n| 不重复副作用 | idempotency/CAS、一次 fence、精确 claim 与 recovery | 未证明任意外部副作用 exactly once 或完整隔离 |\n| 失败可释放资源 | 严格 terminal dispatch/effect 证据闭合 | UNKNOWN 仍阻塞；资源闭合与验收成功分开 |\n| 无污染的产物验收 | 当前 clean Group 执行 CE exact recipes，历史产物不能代替当前输入 | 当前入口 FAIL；独立 QA 尚未完成，不能宣称 内部评测 通过 |\n| 持久结果推进下一轮 | outcome commit 和 source before/after 绑定 | 当前 stream 总字节 cap 与读页预算语义不同，已阻断正常 append |\n\n**已确认案例 F35（目前未实施修复）**：原 actual epoch12 的质量修复失败为 `failed to commit turn outcome: append_fact_event failed: strict event stream append exceeds the configured byte limit`。只读检查 canonical `roles.kernel.turn_outcomes.jsonl` 得 **8,354,716 bytes、47 帧**；当前默认 append cap 是 **8,388,608 bytes**，仅剩 **33,892 bytes**；末帧 **552,213 bytes**，最大帧 **1,184,096 bytes**。严格 read 以每帧/保留页预算校验，而严格 append 仍对整个文件 `len(lease.read_bytes()) + len(encoded)` 施加同一 cap。[T38]、[T39]\n\n此断点说明存储复杂度防护被当作总历史增长上限后，会破坏正常长期会话推进。本研究快照中该问题尚未修复，不能把私有 RED 或已定位根因写成产品已修。该 epoch 观测到多次 Director 语义调用和只读工具结果，沒有可证明的新写修复；这些计数也不等于已确认新 physical provider wire request。原先 55 个 physical calls 是历史基线。\n\n完整原项目验收当前仍不成立：clean ENV、BUILD、TYPECHECK、TEST 执行观测通过，exact ENTRY 路径缺失；随后质量修复 outcome commit 阻断。该入口应由正常 Director 修改已授权源码/config，再复验，不能由外部研究 Agent 修改生成目标。\n\n另一个静态风险只涉及清理用 settlement barrier 的 passed：该字段要求历史聚合失败计数为零，即使历史义务已资源闭合、当前 unresolved 为零，也可能持续为 false。当前 ProjectOutcome 的权威适配器不消费这个 passed，而是读取精确范围内的当前 owner 证明，因此不能从这一计数直接推出最终项目验收被历史失败阻断。[T42] 交付专题给出其不同 consumer 的源码依据。\n\n\n\n## 13. 可复核源码索引\n\n下列行号对应审计时实盘，不是正式接口稳定性承诺。完整 source SHA 与 before/after 对账见论文集的源码证据清单。\n\n- **[T01]** `TransactionTurnExecutor.execute_turn` ； cells/roles/kernel/internal/kernel/transaction_turn_executor.py:62–183（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_executor.py:62）\n- **[T02]** `TransactionKernel` ； cells/roles/kernel/internal/transaction_kernel.py:21–89（src/backend/polaris/cells/roles/kernel/internal/transaction_kernel.py:21）\n- **[T03]** `_resolve_directed_effect_composition/create_transaction_kernel` ； cells/roles/kernel/internal/kernel/transaction_factory.py:220–298（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:220）\n- **[T04]** `invocation/attempt identity binding` ； cells/roles/kernel/internal/kernel/transaction_turn_id.py:182–397（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_id.py:182）\n- **[T05]** `_commit_canonical_outcome/execute` ； cells/roles/kernel/internal/turn_transaction_controller.py:729–876（src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:729）\n- **[T06]** `_execute_turn` ； cells/roles/kernel/internal/turn_transaction_controller.py:1025–1214（src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:1025）\n- **[T07]** `run_decision_pipeline` ； cells/roles/kernel/internal/transaction/decision_pipeline.py:299–483（src/backend/polaris/cells/roles/kernel/internal/transaction/decision_pipeline.py:299）\n- **[T08]** `TransactionConfig` ； cells/roles/kernel/internal/transaction/ledger.py:47–139（src/backend/polaris/cells/roles/kernel/internal/transaction/ledger.py:47）\n- **[T09]** `TurnState and transition rules` ； cells/roles/kernel/internal/turn_state_machine.py:34–133（src/backend/polaris/cells/roles/kernel/internal/turn_state_machine.py:34）\n- **[T10]** `execute_tool_batch/read-write barrier` ； cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:225–385（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:225）\n- **[T11]** `_prepare_directed_effect_dispatch` ； cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_core.py:300–425（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_core.py:300）\n- **[T12]** `prepare_batch/inventory admission` ； cells/roles/kernel/internal/directed_effect_lifecycle.py:420–760（src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:420）\n- **[T13]** `claim_execution_context` ； cells/roles/kernel/internal/directed_effect_lifecycle.py:886–1127（src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:886）\n- **[T14]** `PID-bound fence register/consume` ； cells/roles/kernel/internal/directed_effect_dispatch.py:77–236（src/backend/polaris/cells/roles/kernel/internal/directed_effect_dispatch.py:77）\n- **[T15]** `policy revalidation/physical effect/receipt` ； cells/roles/adapters/internal/director/directed_effect_mutation_port.py:608–917（src/backend/polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py:608）\n- **[T16]** `_execute_directed_effect` ； cells/roles/kernel/internal/tool_batch_runtime.py:596–753（src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:596）\n- **[T17]** `execute_batch/contingency rollback` ； cells/roles/kernel/internal/tool_batch_runtime.py:755–970（src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:755）\n- **[T18]** `execution plan/parallel readonly` ； cells/roles/kernel/internal/tool_batch_runtime.py:1021–1109（src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:1021）\n- **[T19]** `operation transitions/reconciliation` ； cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:2345–2419（src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:2345）\n- **[T20]** `_legal_transition` ； cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:6707–6721（src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:6707）\n- **[T21]** `actual assembled source loader` ； cells/runtime/task_runtime/internal/directed_effect_operation/_repository.py:51–90（src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository.py:51）\n- **[T22]** `speculative/adoption audit defaults` ； cells/roles/kernel/internal/speculative_flags.py:14–41（src/backend/polaris/cells/roles/kernel/internal/speculative_flags.py:14）\n- **[T23]** `speculate_tool_call` ； cells/roles/kernel/internal/stream_shadow_engine.py:148–270（src/backend/polaris/cells/roles/kernel/internal/stream_shadow_engine.py:148）\n- **[T24]** `Prepare/Validate/Commit write semantics` ； cells/roles/kernel/internal/speculation/write_phases.py:23–130（src/backend/polaris/cells/roles/kernel/internal/speculation/write_phases.py:23）\n- **[T25]** `adopt/join/replay routing` ； cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:974–1045（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:974）\n- **[T26]** `LLM_ONCE advisory closing` ； cells/roles/kernel/internal/transaction/finalization.py:137–220（src/backend/polaris/cells/roles/kernel/internal/transaction/finalization.py:137）\n- **[T27]** `commit_turn_result` ； cells/roles/kernel/internal/transaction/outcome_commit.py:55–195（src/backend/polaris/cells/roles/kernel/internal/transaction/outcome_commit.py:55）\n- **[T28]** `to_turn_outcome` ； cells/roles/kernel/internal/transaction/ledger.py:435–497（src/backend/polaris/cells/roles/kernel/internal/transaction/ledger.py:435）\n- **[T29]** `KernelSessionTurnBridge and deadline` ； cells/roles/runtime/internal/kernel_session_turn.py:566–685（src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:566）\n- **[T30]** `canonical invocation/recoverable committed failures` ； cells/roles/runtime/internal/kernel_session_turn.py:687–750（src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:687）\n- **[T31]** `stream_kernel_session derived aggregate` ； cells/roles/runtime/internal/kernel_session_turn.py:829–905（src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:829）\n- **[T32]** `RoleSessionOrchestrator cross-turn loop` ； cells/roles/runtime/internal/session_orchestrator.py:711–875（src/backend/polaris/cells/roles/runtime/internal/session_orchestrator.py:711）\n- **[T33]** `derive_role_turn_capability_token` ； cells/roles/kernel/internal/kernel/tool_executor.py:241–345（src/backend/polaris/cells/roles/kernel/internal/kernel/tool_executor.py:241）\n- **[T34]** `capture_effective_spec` ； kernelone/tool_execution/tool_spec_registry.py:446–487（src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py:446）\n- **[T35]** `snapshot-bound normalization` ； kernelone/llm/toolkit/tool_normalization/__init__.py:410–494（src/backend/polaris/kernelone/llm/toolkit/tool_normalization/__init__.py:410）\n- **[T36]** `append_fact_event` ； cells/events/fact_stream/public/service.py:244–338（src/backend/polaris/cells/events/fact_stream/public/service.py:244）\n- **[T37]** `JsonlEventStore.append` ； kernelone/events/sourcing/file_store.py:231–307（src/backend/polaris/kernelone/events/sourcing/file_store.py:231）\n- **[T38]** `_append_locked CAS/idempotency/durability` ； kernelone/events/sourcing/file_store.py:613–709（src/backend/polaris/kernelone/events/sourcing/file_store.py:613）\n- **[T39]** `strict read integrity/bounded page` ； kernelone/events/sourcing/file_store.py:399–550（src/backend/polaris/kernelone/events/sourcing/file_store.py:399）\n- **[T40]** `TaskBoard.transaction lock` ； cells/runtime/task_runtime/internal/task_board.py:514–517（src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py:514）\n- **[T41]** `closure versus acceptance contract` ； cells/control_plane/run_ledger/public/settlement_barrier.py:76–105（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:76）\n- **[T42]** `closed/passed/release_allowed projection` ； cells/control_plane/run_ledger/public/settlement_barrier.py:793–855（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:793）\n- **[T43]** `strict terminal failure dispatch closure` ； cells/control_plane/run_ledger/public/settlement_barrier.py:529–708（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:529）\n- **[T44]** `public DEO claim/abort/receipt authority` ； cells/runtime/task_runtime/public/service.py:217–250（src/backend/polaris/cells/runtime/task_runtime/public/service.py:217）\n- **[T45]** `production TransactionConfig mapping` ； cells/roles/kernel/internal/kernel/transaction_factory.py:742–785（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:742）\n",
      "diagram": "transaction"
    },
    {
      "id": "contextos",
      "title": "ContextOS",
      "subtitle": "状态优先的上下文编译",
      "summary": "TruthLog、WorkingState、投影、预算和三种请求证据。",
      "markdown": "# Polaris ContextOS：状态优先的上下文编译、预算与请求证据\n\n## 摘要\n\n从 TruthLog、WorkingState、StateFirst 七阶段投影、RoleSignalPlane、内容引用及预算出发，逐层分析模型输入的生成与观测。区分默认链路、可选记忆组件、规范请求与最终 wire，说明信息损失、快照保留、并发验证和接入覆盖的实际限制。\n\n关键词：Polaris；工程 Agent；状态拥有者；源码分析；执行证据\n\n研究日期：2026-10-14。方法为源码、调用链与封存失败记录分析。以下机制的实现存在性、默认接入和端到端可靠性分别讨论。行号以本次工作区为准，源文件指纹随研究证据包提供。\n\n## 1. 核心定位：有约束的上下文编译与观测\n\nContextOS 将原始会话、结构状态、资产、蓝图和工具结果转换成某个角色当前可使用的模型输入。其技术重点是**状态提取、选择、引用、预算、角色隔离、审计和回放**。它不是另一个操作系统，也没有仅凭名称就存在的独立“意识引擎”。执行权、任务状态、真实副作用与交付判定仍由各自的 owner、TaskRuntime、DEO 和 Run Ledger 管理；上下文中的一句“已完成”不能替代它们。\n\n默认角色路径确实构造 `StateFirstContextOS`，后者构造普通七阶段 `PipelineRunner`，并组合 TruthLogService、WorkingStateManager、ReceiptStore、ProjectionEngine。RoleSignalPlane 位于网关的 supplemental turns 装配点。E12 · gateway.py:130（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:130） E03 · engine.py:491（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:491） E05 · runner.py:97（src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:97） E14 · projection_dict_builder.py:115（src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115）\n\n```mermaid\nflowchart TD\n  U[当前用户目标 / 角色输入] --> R[ContextRequest + 当前角色 Profile]\n  H[Session turn_events / 持久化 ContextOS 状态] --> S[StateFirst 七阶段投影]\n  R --> S\n  S --> W[结构状态 + 活动窗口 + artifacts / episodes]\n  W --> P[ProjectionEngine 排序 / 内容引用]\n  A[当前 PM 合同 / CE 蓝图 / 任务历史 / repo 身份] --> G[RoleSignalRegistry 只读信号]\n  G --> P\n  P --> B[RoleContextGateway 角色预算 / 压缩]\n  U --> B\n  B --> F[RequestPreparer 身份 / 工具面 / 必需数据重新绑定]\n  F --> N[Engine 模型窗口 / structured messages / 输出余量]\n  N --> X[Provider 协议转换：system / messages / tools]\n  X --> T[实际传输]\n  F --> O1[装配投影快照]\n  N --> O2[规范请求快照]\n  X --> Q{是否有治理型 physical dispatch port}\n  Q -->|Factory 绑定| O3[原生 endpoint + body + wire hash + audit pin]\n  Q -->|显式 role session gate| OS[冻结 semantic request + physical wire + session 生命周期]\n  Q -->|无| T\n  T --> E[LLM / 工具 / 当前 owner 结果]\n  E --> H\n  E --> FB[投影 outcome 反馈 / 失败证据]\n  FB --> P\n  O1 --> UI[ContextOS UI 只读展示]\n  O2 --> UI\n  O3 --> UI\n```\n\n图中箭头表示数据流，不授予执行权限。会话状态和最终请求审计是不同持久化对象，不能混用 hash。E20 · request_preparer.py:1007（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007） E22 · executor.py:630（src/backend/polaris/kernelone/llm/engine/executor.py:630） E25 · factory_dispatch_propagation.py:390（src/backend/polaris/cells/roles/kernel/internal/llm_caller/factory_dispatch_propagation.py:390）\n\n## 2. 四个底层模块分别保存什么\n\n### TruthLog：会话上下文的原始记录与活动视图\n\n**输入**是规范化会话条目。`append` 深拷贝后同时加入 `_journal_entries` 与 `_entries`；`replace_projection_view` 仅更新活动投影视图，保留 append journal。**变换**包括可选语义/关键词索引与按角色、类型、时间搜索。**持久化**必须谨慎理解：这个服务自身的两个列表在内存中，“journal”注释不表示它已成为平台磁盘 FactStream。**输出**是深拷贝 replay、journal 与搜索结果。**不变量**是投影替换不能覆写 journal、外部对象不能反向改内部数据。**失效边界**是进程内记录的生命周期、可选索引失败/队列满，以及是否由调用者实际 append。E01 · truth_log_service.py:360（src/backend/polaris/kernelone/context/truth_log_service.py:360） E60 · truth_log_service.py:99（src/backend/polaris/kernelone/context/truth_log_service.py:99）\n\n另有真正磁盘写入的 `TurnTruthLogRecorder`：事务控制器根据 workspace 创建 `events/kernel.turn.truthlog.events.jsonl`，队列 writer 以 UTF-8 append；flush/shutdown 才显式 `fsync`。上游 recorder 初始化与记录调用是 best-effort，失败写 warning 后不必然中断角色执行。这是 turn 观察日志，也不能替代严格效果提交事实。E33 · correlation.py:111（src/backend/polaris/cells/roles/kernel/internal/transaction/correlation.py:111） E34 · truthlog_recorder.py:159（src/backend/polaris/cells/roles/kernel/internal/transaction/truthlog_recorder.py:159）\n\n### WorkingState：结构化理解，不是执行状态机\n\n**输入**为 transcript 和 domain adapter 的 state hints。**变换**提取 current goal、accepted plan、open loops、blocked-on、deliverables、偏好、事实、实体与决定，保存 source turns/confidence。**持久化**首先是 WorkingStateManager 内存状态；跨会话由 SessionContinuity 返回的 persisted context config 和上层会话保存流程承担。**输出**是结构状态、Run Card/头尾锚点及活动窗口的 pinning 来源。**不变量**是读写深拷贝、结构字段与来源可回查。**失效边界**是领域提取规则、字段上限、重建/序列化遗漏；结构中的“completed”仍只是上下文理解。E02 · working_state_manager.py:1（src/backend/polaris/kernelone/context/working_state_manager.py:1） E06 · stages.py:645（src/backend/polaris/kernelone/context/context_os/pipeline/stages.py:645） E32 · session_continuity.py:493（src/backend/polaris/kernelone/context/session_continuity.py:493）\n\nUI 的 **WorkingMem** 是活动上下文窗口的展示名称。默认链路的实际窗口由 WindowCollector 选择，结构状态由 WorkingStateManager 管理。不能把 UI 卡上的 `WorkingMemoryWindow` 名称当作 Akashic 工作记忆已经被实例化的证明。另一个 `WorkingSetAssembler` 是仓库地图、符号、代码切片的有预算容器；本次全后端静态引用检查仅发现其实现、导出与示例，未发现默认角色运行构造点。E07 · stages.py:957（src/backend/polaris/kernelone/context/context_os/pipeline/stages.py:957） E48 · _model.ts:780（src/frontend/src/app/components/contextos/contextOSData/_model.ts:780） E51 · working_set.py:106（src/backend/polaris/kernelone/context/working_set.py:106）\n\n### ReceiptStore：大文本内容引用\n\n**输入**为搜索输出、文件切片、diff、工具结果或大信号。**变换**超过字符阈值后写 ContentStore，模型消息改成摘要/占位符及 receipt id。**持久化**默认为进程内 ContentStore 与 receipt-id 索引；可 export/import 内容，并由 offload hook 镜像到 workspace 命名空间的 OriginalPayloadCache。**输出**为可取回的原文引用。**不变量**是内容 hash/dedup、相同引用取回相同内容、工作区隔离。**失效边界**是容量回收、进程重启、best-effort 镜像失败、工具未提供取回能力。引用存在不意味着模型已阅读原文。E10 · receipt_store.py:16（src/backend/polaris/kernelone/context/receipt_store.py:16） E11 · content_store.py:58（src/backend/polaris/kernelone/context/context_os/content_store.py:58） E31 · original_payload_cache.py:364（src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364）\n\n这里的“receipt”与 DEO 的不可变 effect receipt 是两个概念。ReceiptStore 的工具输出缓存可以保存失败信息，但不能凭字符串创建命令成功证明、写入授权或任务完成证明。\n\nContentStore 以 UTF-8 内容的 SHA-256 前 24 位作键，默认 500 项/50MB；先驱逐零引用，再按较低引用数与较旧访问时间驱逐。第二阶段可驱逐仍有引用的条目，因此引用计数不是永久存活承诺。OriginalPayloadCache 默认实例是进程内缓存，可选 SQLite 能力不能自动当作默认磁盘可靠性。E11 · content_store.py:58（src/backend/polaris/kernelone/context/context_os/content_store.py:58） E31 · original_payload_cache.py:364（src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364）\n\n### ProjectionEngine：模型可读视图\n\n**输入**为活动事件、头尾锚点、Run Card、补充信号、当前目标。**变换**去控制面噪声，按 patch/summarize/archive/clear、confidence、recency、dialog act、role priority 排序，工具调用因果组内部保持序号顺序；大结果引用化。**持久化**排序权重/outcome 使用模块级进程内桶；模型消息本身只是输出投影。**输出**为 system/user/assistant/tool messages。**不变量**是投影不修改 TruthLog/WorkingState，用户和 assistant 原话保留，模型 proposal 作为非权威引用处理。**失效边界**是排序、删选及引用造成的信息不可见，以及质量反馈只证明本地启发式效果。E08 · projection_engine.py:375（src/backend/polaris/kernelone/context/projection_engine.py:375） E09 · projection_engine.py:551（src/backend/polaris/kernelone/context/projection_engine.py:551）\n\n自适应排序当前默认开启，可由 `ENABLE_PROJECTION_ADAPTIVE_ORDERING` 关闭。每个 learning key 保留最近 100 个 outcome，至少 5 个后调整权重；它是规则权重反馈，不是在线训练模型权重，也不等于已验证强化学习系统。网关 key 主要为 role id，状态进程内共享且不落磁盘。E58 · projection_engine.py:202（src/backend/polaris/kernelone/context/projection_engine.py:202） E09 · projection_engine.py:551（src/backend/polaris/kernelone/context/projection_engine.py:551）\n\n## 3. StateFirst 七阶段如何把历史变成当前状态\n\n| 阶段 | 真实输入与变换 | 输出与约束 |\n|---|---|---|\n| TranscriptMerger | 当前消息 + 已有 snapshot；以 event id 合并，增加 sequence，提取 native tool-call 元数据 | typed transcript；保留工具调用与结果的可追踪关联 |\n| Canonicalizer | transcript + pending follow-up + 旧 artifacts；dialog-act 与领域路由 | patch / summarize / archive / clear、artifact、follow-up；不是执行 verdict |\n| StatePatcher | 非 clear 事件的 domain hints | 带来源/confidence 的结构状态；规则提取质量限制理解能力 |\n| BudgetPlanner | model window + 当前 transcript/artifacts + reserve policy | input/retrieval/output/tool/safety budgets、阈值与 validation error |\n| WindowCollector | 结构状态根、最近消息、reopened/artifact roots、预算 | pinned/recent 活动事件与排除原因；有界窗口，必要时截短根内容 |\n| EpisodeSealer | 退出活动窗口的事件 + open loops | episode cards；pending 与 seal guards 控制封存 |\n| ArtifactSelector | 状态、artifacts、episodes、预算 | stubs、episode cards、head/tail anchor、Run Card、slice plan |\n\n普通 runner 的顺序与 typed ProjectionReport 可回查。每个 stage 的异常可能触发 typed fallback，并记录 `ok=False/fallback_used/errors`；因此“成功返回 projection”不等于所有阶段执行成功。E05 · runner.py:97（src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:97） E50 · runner.py:265（src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:265）\n\nStateFirst 的并发模式是 lock 下 snapshot、线程池 compute、lock 下 commit。源码注释称最后一步 CAS，但当前 `_validate_projection` 仅判 projection/snapshot 非空，没有比较源版本；而 compute 参数中的 engine snapshot 没进入 PipelineInput，后者依赖传入 `existing_snapshot`。报告不能把它描述为已实现强版本 CAS 或透明 replay 所有内存 journal。该问题是本次静态发现，未在原运行做并发反例，也未修改源码。E03 · engine.py:491（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:491） E04 · engine.py:694（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:694）\n\n另有 `ImmutableSnapshot/SnapshotStore` 写一次加 SHA 校验的实现，`_create_snapshot` 只保存输入 hash 与有限 projection summary。在当前非测试源码引用中没有找到该方法被 `project` 调用；默认每次 project 自动落此审计快照的说法不成立。真正会话状态保存与真正 LLM 请求快照分别走 SessionContinuity 与 Engine/role invoker。E49 · engine.py:245（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:245） E59 · snapshot.py:94（src/backend/polaris/kernelone/context/context_os/snapshot.py:94） E32 · session_continuity.py:493（src/backend/polaris/kernelone/context/session_continuity.py:493） E57 · _helpers.py:566（src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker/_helpers.py:566）\n\n## 4. RoleSignalPlane：当前资产的只读补充管线\n\n**输入**是角色、任务、context_policy flags，以及 project structure/task history/CE overview/blueprint step/verdict/repo identity/ownership/resident 的只读 reader。**变换**是 registry 按角色与开关选 provider，priority 排序，读取 content/freshness，逐块分配预算。**持久化**只记 injected signal 的 freshness；默认有界 256-key LRU，key 来自 task id。**输出**仍是 `supplemental_turns`，并记录 sources、offloaded/dropped/skipped 列表。**不变量**是信号不修改源资产，模型只有投影，源 owner 仍唯一。**失效边界**是 provider 缺失返回 None、任务 key 复用、压缩后仍记“已注入”与模型最终看到版本的偏差。E14 · projection_dict_builder.py:115（src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115） E15 · role_signals.py:563（src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signals.py:563） E16 · role_signal_freshness.py:23（src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signal_freshness.py:23）\n\n默认每信号 4000 字符、总 12000 字符，再按角色实际预算约 5%/15% 缩放。压力下未变化的 nice-to-have 不再注入；must-have 可卸载成原文引用。这会减少重复 CE/历史信息，但“来源未变所以不重发”只在模型保留此前信息时有效，后续截断可能破坏该假设。E14 · projection_dict_builder.py:115（src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115） E15 · role_signals.py:563（src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signals.py:563）\n\nResident 能力信号来自配置注入的 provider；缺数据则不注入，roles.kernel 不反向拥有 resident 数据。能力面卡片表示可用接口与边界，不表示 Resident 已经运行过决策或自动改善模型。E53 · signal_sources.py:293（src/backend/polaris/cells/roles/kernel/internal/context_gateway/signal_sources.py:293）\n\n## 5. 预算分层，为什么 8k 与 1M 能同时成立\n\n1. **StateFirst selection budget**：实际模型窗口优先来自 ModelCatalog，再模型表，再调用者 fallback/默认 policy。普通 policy 输出 reserve 18%、工具 reserve 10%、安全 5%（均有下限）；另外分检索和活动窗口预算。这一阶段选择上下文。E56 · policies.py:36（src/backend/polaris/kernelone/context/context_os/policies.py:36）\n2. **RoleContextGateway enforcement**：`min(role_policy.max_context_tokens, model_window * 0.85)`，先扣角色 system，再保留当前用户目标；超过当前目标剩余预算直接报错，历史/信号可以压缩。角色预算可以是 8k，而模型配置窗口为 1M，两者限制不同。E12 · gateway.py:130（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:130） E13 · gateway.py:281（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:281）\n3. **RequestPreparer 必需数据再绑定**：当前 source/evidence 作为独立 user 数据消息加入，而不是变成巨大“当前指令”；检查 UTF-8 hash、重复路径、status/drift，再重新 pin sibling exports/write guidance/core role identity。它不凭数据赋予权限。E20 · request_preparer.py:1007（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007） E62 · request_preparer.py:437（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:437）\n4. **Engine 模型预算**：再计 native tools、消息包装和输出余量，对真实 structured `chat_messages` 再压缩，按最终窗口 clamp 输出。角色装配结束之后仍可能改变真正发送内容，因此前置 audit 不能自动当最终 wire。E22 · executor.py:630（src/backend/polaris/kernelone/llm/engine/executor.py:630）\n5. **Final request audit**：分别计 message/tool schema/response_format，合为最终输入估算和利用率，附 coverage/required refs。估算使用启发式，Gateway 的 CJK-aware 算法与审计字符算法不是同一个 tokenizer；这不是 provider 实计 usage。E19 · token_estimator.py:12（src/backend/polaris/cells/roles/kernel/internal/context_gateway/token_estimator.py:12） E24 · _builders.py:102（src/backend/polaris/cells/roles/kernel/internal/llm_caller/context_audit/_builders.py:102）\n\n实际已发生的失败说明分层必要：原 epoch10 把完整 source dossier 拼进 current instruction，触发 8k 指令 guard，QA 在 provider 前失败。后续 source-only修复将其放独立数据 slot，并确认来源/hash/CE 正文和 coverage；prepared-only 成功仍不等于 QA 真实调用通过。\n\nGateway CompressionEngine 当前 `apply_compression` 主分支直接做 smart truncation/emergency fallback；docstring 的“先语义摘要”不能取代实际控制流。StateFirst 路径使用 emergency 截历史、必要时截大的 system 数据平面，当前角色身份 system 在后面插入以免被裁。故无损保存、模型当前可见、预算内可发送是三件不同的事。E17 · compression_engine.py:58（src/backend/polaris/cells/roles/kernel/internal/context_gateway/compression_engine.py:58） E18 · compression_engine.py:428（src/backend/polaris/cells/roles/kernel/internal/context_gateway/compression_engine.py:428） E13 · gateway.py:281（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:281）\n\n## 6. 工具面和身份怎样进入模型\n\nRole Profile 提供角色/模型/工具候选，运行态 ToolSpec/能力与当前合同形成 effective tool surface，transaction 可能提供 forced schema/choice。Preparer 格式化 native tools、response transport、温度/输出预算；最终确保当前目标、角色 system identity、当前 source/合同数据一致。Engine 和 provider 又做协议兼容转换。所有最终可调用工具必须与 runtime allowset 对账，角色 whitelist 不能替代实际 schema。E20 · request_preparer.py:1007（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007） E52 · tool_surface.py:146（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_surface.py:146）\n\n弱模型/legacy write-only/edit-only/slim 仍有显式分支，但受 legacy-tool-slices 与 committed continuation 条件控制；有 tool-filter audit 记录原因、原集合、裁后集合及与 prompt 必需工具的冲突。最终 request 若提供工具而 runtime 拒同一工具，属于面不一致；若 prompt 要求工具但 schema 没有，属于上下文与工具合同矛盾。模型输出经过 ToolSpec alias/argument normalization 后才进权限层，不能把自然字段差异直接归因模型能力。E52 · tool_surface.py:146（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_surface.py:146）\n\nAnthropic provider 把角色消息转换成顶层 `system` 和原生 `messages`，再附 tools/options、overrides 与 sanitization；OpenAI Responses 使用 input，Chat 使用 messages。查看 `messages[0]` 并不足以审 Anthropic 真实 system，必须查原生 body 的 protocol。E27 · anthropic_provider.py:543（src/backend/polaris/infrastructure/llm/providers/anthropic_provider.py:543） E28 · service.py:188（src/backend/polaris/cells/context/engine/public/service.py:188）\n\n## 7. 三种请求快照必须分清\n\n| 证据级别 | 实际存储 | 能证明 | 不能证明 |\n|---|---|---|---|\n| 角色装配投影 | invoker 在 call_start 前保存 messages + provider_request/audit | 角色准备输入、来源与审计；可缺失并标 degraded | Engine 后续裁剪、provider 转换与真实 HTTP body |\n| Engine 规范请求 | `llm.provider_request_snapshot.v1`，post-compression messages + schema 摘要/choice/format/token audit | Engine 准备传给 provider 的规范内容 | 原生 endpoint/body 最终字节、headers/options 转换、实际发送成功 |\n| 原生 wire 资格快照 | `llm.final_physical_provider_request_context.v1`，exact redacted endpoint/body/transport/wire hash + route authority/pin | 发送边界冻结内容与资格绑定；原生 system/messages/tools | Provider 实际收到/处理完成、工具效果或产品交付成功 |\n\n上述 `llm.final_physical_provider_request_context.v1` 展示快照来自绑定的 Factory semantic propagation port：preparer 的 Factory candidate + signed binding 创建端口，Engine 将其带到 transport；没有端口时所审HTTP helper直接 raw send。**这不等于非Factory没有wire治理能力**。平台还实现了 `FinalProviderAttemptGate.for_role_session`：必须传显式 snapshot_store、session生命周期与drain协调器，保存 `llm.frozen_final_provider_attempt.v1` 的 canonical semantic request及physical_wire，记录terminal失败/确认；它不能冒称Factory scope。其当前wire-equivalence按messages/tools/choice/format逐键比对，区别于Factory provider-native协议资格。此次非测试引用检查未找到默认角色入口构造该session gate的调用点，因此结论限于具体传播/集成缺口，不能把潜在能力不存在当根因。E66 · final_provider_attempt_gate.py:285（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:285）、E67 · final_provider_attempt_gate.py:946（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:946）E21 · request_preparer.py:1453（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1453） E22 · executor.py:630（src/backend/polaris/kernelone/llm/engine/executor.py:630） E23 · executor.py:1045（src/backend/polaris/kernelone/llm/engine/executor.py:1045） E25 · factory_dispatch_propagation.py:390（src/backend/polaris/cells/roles/kernel/internal/llm_caller/factory_dispatch_propagation.py:390） E26 · _http_pooling.py:270（src/backend/polaris/infrastructure/llm/providers/provider_helpers/_http_pooling.py:270） E57 · _helpers.py:566（src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker/_helpers.py:566）\n\n`context_snapshot_ref` 是真实落盘的 24 hex 内容地址键，位置为同 workspace runtime 的 `contexts/<shard>/<hash>`；并非 call id、request hash、prompt hash 或路径。`request_hash` 绑定请求身份，`final_physical_wire_hash` 绑定 wire 内容，而 `receipt://` 与 ContentRef 绑定文本资产；DEO效果收据另有op/owner/attempt/physical payload hash等绑定。文本引用与24hex快照即使长得像效果hash，也不能作效果提交/封闭证明；原样保留各契约字段，不从长度猜用途。E30 · context_store_retention.py:114（src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114） E31 · original_payload_cache.py:364（src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364）\n\n`/v2/context/{hash}/final-request` 可返回 available 的规范投影，也可返回实际 wire。API 会从真实物理 schema 中解析 Anthropic system/messages、Responses input；没有物理 schema则 `final_physical_request_body=null`。HTTP200 或“最终请求”标题不是第三层证明。E28 · service.py:188（src/backend/polaris/cells/context/engine/public/service.py:188）\n\n原 epoch12 的审计反例已封存：7 次新角色 start/end 与 7 个可读磁盘语义快照，11 个成功只读结果及 6 个 file_exists scope 拒绝；所有新快照缺 `final_physical_request.body` 与 wire hash。真实物理发送数量未知，不能写成 0，也不能把 7 份语义快照当 7 个 qualified wire。独立 QA 尚未执行；当前项目未交付。报告：`native18-epoch12-independent-qa-wire-gap.json`，SHA `6f19d7bab910450168470897ac06867c5c82e9ca64621f11fa7e4ca08160ca2c`。\n\n## 8. 记忆、RAG 与常驻能力：哪些真正接入\n\n| 名称 | 真实实现/接入分类 | 限制 |\n|---|---|---|\n| 默认 StateFirst | 角色 gateway 与会话连续性生产路径 | 规则 hints、有限窗口、fallback；不自动等于长期语义记忆 |\n| ProjectionEngine 自适应排序 | 默认开启，当前 turn outcome 有反馈调用 | 进程内启发式权重，不是模型训练；未证明跨重启或全工作区隔离 |\n| ContextOS MemoryManager | aggregate role 路径按触发/limit召回会话 snapshot | 当前是关键词 Jaccard + 英文规则冲突检查；不是 embedding RAG |\n| role_session provider gate | 显式snapshot store + scope/drain/lifecycle，保存物理wire | 平台能力存在；默认入口传播/捕获需单独证明，不可冒Factory资格 |\n| Akashic 四层 MemoryManager | Working/SemanticCache/Episodic/Semantic ports、promotion/demotion 与 sync 实现完整存在 | create/default/inject API未见默认角色主链调用；不能宣称所有调用有后台记忆维护 |\n| AkashicSemanticMemory | JSONL + 可选 embedding/LanceDB，embedding失败时关键词回退；TruthLogIndex可调用 | 依赖已配置 embedding服务；vector_search=True不证明可用、也不证明被模型消费 |\n| Knowledge Pipeline | 文档 extract/chunk/enrich/embed/idempotent store，CLI/显式消费组件 | 文档入库与当前模型检索/引用是两步；未证明默认角色每次自动RAG |\n| EnhancedPipelineRunner | 有 phase-aware/attention/graph/memory/predictive组合实现 | 默认StateFirst选择普通Runner；本次未找到生产实例化 |\n| phase/attention/predictive | aggregate role另有直接构建 pack的路径 | 局部提示/计划投影存在，不等于所有角色热路径预测压缩 |\n| IntelligentCompressor/crushers | greedy score/LLM summary/确定性内容类型压缩实现 | 本次未找到默认角色主链构造IntelligentCompressor；不能替代现CompressionEngine真实流程 |\n| WorkingSetAssembler | repo map/symbol/code slices、policy-gated预算组件 | 生产构造点未证，不等于UI WorkingMem |\n| Resident | workspace service、显式start/stop、tick生成insights/skills/proposals、RoleRuntime决策入口 | 默认active=False；tick是确定性证据生产者，不允许sidecar LLM，提案需治理与执行链 |\n| ContextOS UI | WebSocket runtime事件、context.build/LLM audit派生视图与按需读完整快照 | 估算窗口/调用数、provider-ready卡不能证明底层算法活跃或语义通过 |\n\nAkashic 与 ContextOS 两个不同的 `MemoryManager` 必须分别介绍。aggregate 实际 import的是 `context.context_os.memory.MemoryManager`；后者的候选从会话 manifest/snapshot读取、按当前事实做 conflicts，再决定是否注入。当前规则允许“possible conflict但高相关”注入，不能宣传完整逻辑一致性证明。E35 · integration.py:48（src/backend/polaris/kernelone/akashic/integration.py:48） E36 · memory_manager.py:138（src/backend/polaris/kernelone/akashic/memory_manager.py:138） E37 · semantic_memory.py:64（src/backend/polaris/kernelone/akashic/semantic_memory.py:64） E38 · manager.py:43（src/backend/polaris/kernelone/context/context_os/memory/manager.py:43） E39 · candidates.py:85（src/backend/polaris/kernelone/context/context_os/memory/candidates.py:85） E40 · candidates.py:220（src/backend/polaris/kernelone/context/context_os/memory/candidates.py:220） E41 · conflict_checker.py:66（src/backend/polaris/kernelone/context/context_os/memory/conflict_checker.py:66） E42 · _memory.py:153（src/backend/polaris/cells/roles/runtime/public/aggregate_chat/_memory.py:153） E43 · _memory.py:294（src/backend/polaris/cells/roles/runtime/public/aggregate_chat/_memory.py:294） E44 · enhanced_runner.py:62（src/backend/polaris/kernelone/context/context_os/pipeline/enhanced_runner.py:62）\n\n文档入库的真实流程是 DocumentInput，经 extractor registry、semantic chunker、importance/hash/tags、embedding，再写 IdempotentVectorStore；默认workspace runtime JSONL，可选LanceDB。输出的 memory_ids 是入库记录，不是当前角色已经召回的上下文，也不是 Provider 已看到的证据。无 fragments/chunks 直接 failed；源文件/行号/hash随记录保留。E63 · pipeline.py:311（src/backend/polaris/kernelone/akashic/knowledge_pipeline/pipeline.py:311）、E64 · pipeline.py:235（src/backend/polaris/kernelone/akashic/knowledge_pipeline/pipeline.py:235）\n\nAkashic CompressionDaemon 根据 WorkingMemory usage ratio触发压缩，调用 RoleContextCompressor，失败后可用确定性压缩/eviction并重写窗口；create_memory_manager内 enable_daemon=True只是该API默认，不证明默认Factory/角色运行调用过该API。它是可显式启用的后台组件，本文没有启动或查询原daemon。E65 · compression_daemon.py:234（src/backend/polaris/kernelone/akashic/compression_daemon.py:234）\n\n静态还有一个具体绑定风险：`context_os.memory.MemoryManager.workspace` 未传入默认 `_provider=MemoryCandidateProvider()`，后者默认workspace='.'；aggregate 构造 manager(workspace=...) 不自动改变 provider 的默认工作区。此处未做原namespace查询或调用反例，只列源码已见风险，不能以“workspace隔离完全可靠”宣传。E38 · manager.py:43（src/backend/polaris/kernelone/context/context_os/memory/manager.py:43） E39 · candidates.py:85（src/backend/polaris/kernelone/context/context_os/memory/candidates.py:85）\n\nResident 的持久化服务真实存在；默认inactive，tick只产 evidence/proposals，execution-impacting判断必须经独立 runtime contract gate。能力信号说明可调用哪些治理接口，不代表自动代码修改、在线训练或后台已常驻；当前是否start必须查实际运行状态，本文不查询或改变它。E45 · autonomy_boundary.py:8（src/backend/polaris/cells/resident/autonomy/internal/autonomy_boundary.py:8） E46 · resident_runtime_service.py:116（src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py:116） E47 · resident_runtime_service.py:464（src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py:464） E53 · signal_sources.py:293（src/backend/polaris/cells/roles/kernel/internal/context_gateway/signal_sources.py:293）\n\n## 9. 回放与保留：三个存储等级\n\n**会话连续性**保存 ContextOS transcript/artifacts/episodes/pending/working state 到上层 session context config，后续 rehydrate；这是重建当前状态的数据。**内容引用缓存**保存大文本，进程内容量/TTL可能让原引用失效。**LLM请求审计存储**按内容地址写 runtime/contexts，完整 wire pin 使用独占锁、workspace storage token、不可变写入和读后核对。E32 · session_continuity.py:493（src/backend/polaris/kernelone/context/session_continuity.py:493） E10 · receipt_store.py:16（src/backend/polaris/kernelone/context/receipt_store.py:16） E11 · content_store.py:58（src/backend/polaris/kernelone/context/context_os/content_store.py:58） E30 · context_store_retention.py:114（src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114）\n\n请求 retention 有 TTL、文件数和总字节上限，默认7天/500MB，pin repository决定是否可删除；过期不等于可删当前受保护资格证据。查询按当前storage runtime、同workspace Instance Registry runtime、旧system cache候选链找文件，旧根只读发现，不复制或活跃切根。E29 · snapshot_paths.py:24（src/backend/polaris/cells/context/engine/public/snapshot_paths.py:24） E30 · context_store_retention.py:114（src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114）\n\n上下文 replay 只能重建模型当时可见视图及来源；若副作用要重试，仍须 TaskRuntime/DEO 独立证据，不能 replay 缓存工具结果就再次认定命令执行。当前 epoch12 严格 append总字节限制阻 turn outcome提交，也说明工具/read发生、观察日志存在、严格提交完成是不同边界。\n\n## 10. 独特性与当前瓶颈\n\n有实装价值的组合是：领域状态优先而非盲堆历史；保留 source-turn根与工具因果组；当前资产 freshness减少重复；大文本引用化并能同workspace取回；角色策略预算与真实模型窗口分层；最终 raw provider body绑定资格/pin；UI只读事件投影。每一项都可以由当前代码确认。它们组合成可解释的上下文治理层，但不能据此推导“全自动交付已经完成”或“记忆系统无损理解一切”。\n\n当前主要瓶颈分别是：多层裁剪可能使已保存的内容不再可见；引用取回并非永久承诺；默认 StateFirst版本校验不足；部分长期记忆组件尚未进入默认链路；关键词/规则理解能力有限；角色key/taskkey的进程内缓存隔离范围较粗；Final-request API仍兼容只含规范投影的快照；内部修复的最终wire证据覆盖缺口已在真实epoch12出现。优先解决顺序需由后续工程授权决定，本次仅解释与记录，不启动修复。\n\n## 11. 证据与方法\n\n所有源码 discovery 先调用 CodeGraph；本次多个查询输出未覆盖目标模块，随后用 RTK UTF-8 精确范围读取补足。无 Transport closed、无伪造图结果。负面接入分类来自明确构造点/引用检查，是静态证据；不能证明动态插件永远不可能接入。没有新增测试、provider probe、原目标命令、公共冷查询或控制动作。当前保存各相关源码 SHA，外部报告不是产品事实源。\n",
      "diagram": "context"
    },
    {
      "id": "tools",
      "title": "工具与效果权威",
      "subtitle": "从自然调用到受权操作",
      "summary": "ToolSpec、JobToken、持久 claim 与进程内一次 fence。",
      "markdown": "# 工具、能力与效果证据\n\n## 6. Prepare、Carry、Freeze、Claim、Commit、Abort、Recovery\n\n这些词在不同层有具体对象，不能只看名字相同就认为协议相同。\n\n**Prepare** 有两种：可选 shadow 的只读探测；正式 DEO 的成员授权与 inventory 准备。后者先验证 execution attempt，登记 parent registry，封存有序成员清单，再绑定成员策略、登记 operation 并确认 inventory readiness。完整清单在该批次任何正式 read、shadow adoption 或 effect 前准备。[T11]、[T12]、`_executor_execute.py:800`。\n\n**Carry / Freeze** 指把已捕获的 ToolSpec、normalized arguments、能力限制、policy snapshot、目标状态及成员 hash 通过类型化 DTO 传到下一层。Freeze 防止授权后再用不同 registry 或可变 metadata 偷换输入；它不冻结操作系统或 workspace，执行前仍要重新检查当前状态。调用 setup 与 batch member 的固定对象是两层事实。[T01]、[T03]、[T13]、[T34]、[T35]\n\n**Claim** 是即时授权边界：读取当前严格 operation，检查仍为 INTENT_COMMITTED 及成员身份；从当前 operation stream head 求下一 seq，再用 expected version/seq 执行 CAS。成功将意图转为 EFFECT_STARTED，并获得本次 grant。精确重复 claim 可观察已写状态，但不获得新的可执行 grant；角色层明确拒绝 `idempotent=True` 作为新派发权。[T13]、[T44]\n\n**Execute / effect Commit**：claim context 在进程内 fence 注册；adapter 验 scope、参数、当前 policy、目标状态；一次消费 fence 后才能调用物理工具。写入成功需收据，修复绑定还需正确 after state；物理操作完成而 receipt commit 失败会进入 recovery，而不是直接报成功。[T14]、[T15]、[T16]\n\n**Abort** 只适用于未 claim 的 INTENT_COMMITTED；它证明没有按该许可开始操作，不能撤销已发生效果。失败批会终止未执行兄弟成员。提前封存的 rollback contingency 也作为有授权的独立操作，仅在已证明 forward 成功且需要补偿时执行；补偿失败不得宣称 restored。未知的已开始操作保留恢复义务。[T17]、[T19]\n\n**Recovery** 处理 EFFECT_STARTED 后的歧义、无法捕获当前策略、物理失败、结果观察失败或 receipt commit 失败。它追加状态证据，不能重造已失去的进程内 fence、删历史失败或无证据地再执行旧效果。[T13]、[T15]、[T19]\n\n**OutcomeCommit** 在整轮结束后封存轮结果，是另一项 append；它不是 effect receipt 的别名，也不是把此前所有 effect 和后继 QA 一次性提交。[T27]\n\nDEO 状态图严格来自运行时加载的 `.source` 文件；`_repository.py` 显式 UTF-8 读取并 compile/exec 该文件。引用 `.source` 是实际生产执行体，并非将历史文本文档误认活代码。[T20]、[T21]\n\n```mermaid\nstateDiagram-v2\n    [*] --> INTENT_COMMITTED: admit\n    INTENT_COMMITTED --> EFFECT_STARTED: claim + CAS\n    INTENT_COMMITTED --> ABORTED: 尚未开始\n    EFFECT_STARTED --> RECEIPT_COMMITTED: 真实效果收据\n    EFFECT_STARTED --> RECOVERY_PENDING: 不确定或失败\n    RECOVERY_PENDING --> RECEIPT_COMMITTED: 严格恢复收据\n    RECOVERY_PENDING --> DEAD_LETTER: 负结果闭合证明\n    RECEIPT_COMMITTED --> CLOSED_BY_PARENT: 父任务结算\n    ABORTED --> [*]\n    DEAD_LETTER --> [*]\n    CLOSED_BY_PARENT --> [*]\n```\n\n该图没有 EFFECT_STARTED 直接 ABORTED 的边，也没有 CLOSED_BY_PARENT 回到 INTENT_COMMITTED 的边。恢复后的新执行应属于合法新 attempt/新操作，并保留原 owner 与历史，不是修改旧终态。\n\n## 7. 工具归一化与权限的先后关系\n\n工具不由模型记住某一个内部拼写来获取权限。ToolSpecRegistry 是规范工具、aliases、arg_aliases、效果分类和 provider schema 的 owner。`capture_effective_spec(raw_name)` 取得一份一致、不可变的规范名称与别名快照；参数归一化随后只消费该快照，重新校验 hash，按 schema 别名映射和专用复杂转换生成 canonical arguments。[T34]、[T35]\n\n例如 `run_command` 与 `execute_command` 可以是同一工具的名称别名，但相同名称归一化成功不证明命令已获准。危险参数、跨 workspace 路径、未授权 command/cwd、冲突别名或未注册工具仍需拒绝。参数别名是同义适配，不是推断新的业务权限。\n\n有三类容易混同的 token：\n\n1. **LLM token / window budget**：上下文和输出容量、调用预算，不赋予操作权限。\n2. **JobToken / execution envelope capability**：从项目合同与 CE 交接/控制面派生，约束 allowed paths、write paths、commands、stage 和合同/蓝图 hash。读取完整已提交 JobToken 优先于根层的有损 envelope 投影。[T33]\n3. **Execution claim / fence**：精确 task attempt、parent、member、arguments hash、当前 policy、PID/对象身份的一次执行许可。[T13]、[T14]\n\nrun/task/turn/transition ID 是定位身份；哈希是绑定与一致性校验；授权还需可信 owner/当前 attempt/策略/能力作用域。普通 SHA digest 本身并不是针对恶意重写者的数字签名，不能把“哈希相同”独立解释为“任何来源都可信”。\n\n## 3. JobToken、claim、grant、receipt 是四件事\n\nJobToken 表达由控制面合同派生的 capability，不是新的合同事实源，也不是某个任务已成功的证据。执行时还需要当前 owner/session/attempt 和 lease。DEO claim 产生一次操作的 fresh grant，grant 约束允许执行的 effect，receipt 才描述实际发生的 effect 与结果。\n\n身份至少同时跨越 `workspace/project/factory_run/child_run/task/external_task/session/attempt/role/worker`，并绑定合同、蓝图、policy、目标与输入 hash。只保留 `task_id=1` 或只保留 `run_id` 都不足以重新获得权限。模型输出的“已写入”也不能替代真实 before/after hash、DEO member/parent 与 durable commit。\n\n命令有两个不同判断。KernelOne command capability 可以判断一个自然命令是否在允许集合内；该集合存在兼容型 prefix 与空集合语义。正式 verifier 则必须进一步绑定 CE 的 exact argv/cwd、obligation、输入闭包、可信可执行文件和 proof profile。不能从“工具层允许运行”直接推出“这次运行可计入验收”。\n\n源码：command capability（src/backend/polaris/kernelone/llm/toolkit/executor/command_capability.py:201）、正式 verifier authority（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1310）。\n",
      "diagram": "transaction"
    },
    {
      "id": "authority",
      "title": "控制与交付",
      "subtitle": "多个拥有者如何协作",
      "summary": "完整角色链、TaskRuntime、Run Ledger 与项目接受。",
      "markdown": "# Polaris 控制与交付：多拥有者证明、验证与恢复\n\n## 摘要\n\n以 PM、Chief Engineer、Director、QA、TaskRuntime、ExecutionBroker、Run Ledger 和 ProjectOutcome 的实际所有权为对象，解释合同、能力、执行、收据、验收、结算与恢复如何组合，并区分当前产物接受与全部历史资源闭合。\n\n关键词：Polaris；工程 Agent；状态拥有者；源码分析；执行证据\n\n研究日期：2026-10-14。方法为源码、调用链与封存失败记录分析。以下机制的实现存在性、默认接入和端到端可靠性分别讨论。行号以本次工作区为准，源文件指纹随研究证据包提供。\n\n## 1. 谁拥有哪一种真相\n\nGraph catalogue 规定 single state owner 与跨 Cell public contract 边界。目录位置不能替代 Graph 的权属定义；bootstrap 可以组装多个 public owner ports，但消费者不能因此直接接管上游状态。\n\n| 组件 | 拥有的状态/事实 | 提供的能力 | 不能单独证明 |\n| --- | --- | --- | --- |\n| PM / orchestration.pm_planning | 任务意图、验收约束、拆分 | 规划合同 | 实现已正确、命令已执行 |\n| Chief Engineer / chief_engineer.blueprint | 蓝图、接口、产物与命令义务、handoff | 校验完整合同、派生执行范围 | 目标当前字节已达标 |\n| Factory / factory.pipeline | run/stage、checkpoint、workspace owner claim、stage/lifecycle transaction | 组织完整角色链、恢复及终态清理 | Factory `completed` 不是 verified delivery |\n| runtime.task_runtime | numeric task、session/attempt、DEO parent/inventory/operation/receipt、执行事实 | 持久 claim、工具副作用授权与收据、同任务恢复；进程内一次消费 fence 由角色内核执行 | 一个 task completed 不代表项目 QA 与全部合同通过 |\n| events.fact_stream / KernelOne | 序号、完整性、幂等、append/query/CAS 的底座 | 保存、验证与重放 owner 发布的事实 | 不替 owner 判断业务结果 |\n| control_plane.run_ledger | 控制面事实的读模型及可重建 ledger 投影 | capability/effect/gate/TaskBoundary/evidence 聚合 | 某条 JSONL 行或 summary 不能替上游 owner 收据 |\n| control_plane.verifier_policy | 可用 verifier profile、proof semantics、workspace policy | 判断命令能否成为验收证据 | policy authorized 不是执行权或 PASS |\n| runtime.execution_broker | 真实进程、当前产物及 verifier 收据 | 受权启动、快照隔离、执行与终止、持久化 proof | 进程启动或 exit 0 不一定满足指定 proof |\n| director.runtime | repair catalog、coverage、planner/composer/scheduler、policy execution/revalidation | 在批准范围内实施并验证修复 | coverage match、plan 或 advisory 不是已修改/已通过 |\n| QA / qa.audit_verdict、roles.adapters | 按配置选择的独立判断与 QA owner 证据 | 审核当前实现及验收结果，提出 rework | 自测、准备好的 prompt、候选 verdict 不是 committed QA |\n| runtime.projection | 无上游事实写权限；有纯 reducer 与 owner observation/binding | 六轴 ProjectOutcome | caller 自填全绿不能得到 authoritative completed_verified |\n\n目录与 contract 的原始 Graph 证据已保存于 `control-analysis-baseline.json` 的 `cells`。当前大多数跨层查询通过 public DTO/port；少数兼容型 service/class 入口仍使调用面比 Graph 的精简 query inventory 更大，不能把目录迁移或 public 命名自动视为全部边界已证实。\n\n## 2. 正常执行链不是四个模型顺序聊天\n\n```mermaid\nflowchart LR\n    PM[PM 任务合同] --> CE[CE 蓝图与完整义务]\n    CE --> JT[派生 JobToken / execution authority]\n    JT --> D[Director 工具与修复]\n    D --> TR[TaskRuntime DEO / owner receipts]\n    CE --> VP[Verifier Policy]\n    VP --> EB[ExecutionBroker 当前输入快照与真实命令]\n    D --> EB\n    TR --> RL[Run Ledger / TaskBoundary]\n    EB --> RL\n    RL --> QA[独立 QA / committed verdict]\n    QA --> RW[有 authority 的同任务 rework]\n    RW --> D\n    QA --> PO[ProjectOutcome owner binding]\n    F[Factory 完整阶段链与终态事件] --> PO\n    RL --> PO\n    TR --> PO\n```\n\nPM 合同决定“做什么”。CE 明确“谁拥有哪些产物、如何验收、哪些上下文可读”，并产生 Director 可消费的 handoff。Director 必须在已验证 CE 范围内执行。正常执行允许复用已经验证的产物、完成任务或确定性修复，因此某阶段可以没有新 LLM 请求；这不等于跳过该角色的权属。若本次明确要求语义 QA，则必须选择实际 LLM QA 能力，不能用 physical verifier QA 模式替代。\n\nCE 的 `_control_plane_job_token` 不只拼路径：它从完整合同提取 required artifact owners、每任务 command authority hashes、批准的 read context；验证 run/task 身份、interface hash、target/scope 是否仍属于该 owner。token id 从这些事实派生。可读上下文与可写产物是两种权限，不能因模型需要阅读兄弟接口就扩大写范围。\n\n源码：CE token 派生（src/backend/polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py:200）、JobToken DTO（src/backend/polaris/cells/control_plane/run_ledger/public/job_token.py:10）。\n\n## 3. JobToken、claim、grant、receipt 是四件事\n\nJobToken 表达由控制面合同派生的 capability，不是新的合同事实源，也不是某个任务已成功的证据。执行时还需要当前 owner/session/attempt 和 lease。DEO claim 产生一次操作的 fresh grant，grant 约束允许执行的 effect，receipt 才描述实际发生的 effect 与结果。\n\n身份至少同时跨越 `workspace/project/factory_run/child_run/task/external_task/session/attempt/role/worker`，并绑定合同、蓝图、policy、目标与输入 hash。只保留 `task_id=1` 或只保留 `run_id` 都不足以重新获得权限。模型输出的“已写入”也不能替代真实 before/after hash、DEO member/parent 与 durable commit。\n\n命令有两个不同判断。KernelOne command capability 可以判断一个自然命令是否在允许集合内；该集合存在兼容型 prefix 与空集合语义。正式 verifier 则必须进一步绑定 CE 的 exact argv/cwd、obligation、输入闭包、可信可执行文件和 proof profile。不能从“工具层允许运行”直接推出“这次运行可计入验收”。\n\n源码：command capability（src/backend/polaris/kernelone/llm/toolkit/executor/command_capability.py:201）、正式 verifier authority（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1310）。\n\n## 4. 物理请求控制为什么需要独立的一层\n\n语义 request 与最终 wire body 不同。provider 适配器会转换 messages、tools、tool choice 等；只审准备阶段的 request 会漏掉最终工具被裁剪、角色串线、参数变更或预算差异。\n\n现有 `FinalProviderAttemptGate` 的正确顺序为：冻结最终 wire → 持久化并 pin snapshot → 获得 start permit → durable start receipt → commit started → 才允许 transport dispatch → cleanup → durable terminal receipt → settle。取消调用协程不能提前把仍运行的 blocking transport 标为结束；worker 保留 terminal settlement 的所有权。start persistence 含糊时也不能重新发一次请求碰碰运气。\n\nFactory coordinator 用 execution authority hash 绑定 grant/budget、角色、run、request freeze、wire hash 和 ordinal。reserve 既不表示请求已发送，也不表示可忽略 START_AMBIGUOUS；close/revoke/drain 是防止失败之后继续产生新请求的独立控制。\n\n当前 actual12 的七个语义 callback 与 call_end 是存在的，但它们的 ContextOS snapshot 缺 final wire body/hash。缺 snapshot 不能推断物理请求为零；它说明该路径尚未提供完整物理证明。`FinalProviderAttemptGate` 也具有 role_session 模式，因此不能未经调用链审计就断言“平台仅支持 Factory wire capture”。应核对各入口如何传播 execution authority 并安装最终 gate。\n\n源码：snapshot owner（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:99）、freeze/start/commit（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:591）、worker owns terminal（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:546）、physical reserve（src/backend/polaris/cells/factory/pipeline/internal/factory_physical_attempt_coordinator.py:624）。\n\n## 5. 真实验证：许可、执行、证明与当前性分开\n\nVerifier Policy 只评估 proposal，不 mint execution authority。ExecutionBroker 从 bootstrap-bound owner port 解析 exact DTO，验证 workspace/project/run/contract/obligation、argv/cwd 与 executable identity；spawn 前重新解析 authority，若已变化则拒绝。随后冻结全部声明输入，绑定 input hash，启动受控进程并产出 owner 收据。\n\n“命令执行成功”仍可能只是 candidate observation。正式验收需要收据 seal、当前输入/authority、指定 proof 与稳定输出。长驻入口还可以按已声明 readiness profile 判断 ready 后受控终止；不能把它等同于任意进程存活，也不能把 CLI 缺失替换成宿主旧 dist 能跑。\n\nVerificationGroup 的用途是让 build→type/test→entry 在同一个干净候选上共享派生产物。开始时只复制批准的 authored inputs 和依赖，避免 stale dist。entry 不通过时前面的 exit 0 是实际进展，但未 seal 的整组并不是完整 authority proof。actual12 正是 ENV/build/type/test 为 0、exact ENTRY 为 1；它比旧 host npm start 更真实，也仍是失败。\n\n源码：policy proposal（src/backend/polaris/cells/control_plane/verifier_policy/public/service.py:856）、spawn 前再授权（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1367）、receipt proof（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1045）。\n\n## 6. 至少六种“完成”，不能压成一个绿色状态\n\n| 判断 | 它实际回答的问题 | 与全项目交付的关系 |\n| --- | --- | --- |\n| provider terminal | transport/worker 是否结束，terminal 是否持久化 | 无正确实现含义 |\n| effect receipt committed | 受权副作用是否真实、可追责地发生 | 有效果仍可编译/测试失败 |\n| TaskBoundary completed_verified | 当前任务声明的产物、依赖、证据、verifier 条件是否满足 | 范围来自当前任务边界；不能替全项目 QA |\n| TaskRuntime completed/settled | owner attempt 是否终态、还能否写；任务投影是否 converged | completed task 可以伴随项目 QA 未运行或失败 |\n| Factory completed | orchestration session/阶段链是否结束 | `complete_run` 明确写 verified=False |\n| ProjectOutcome completed_verified | 六轴当前 owner 证明是否全部满足且 authority_bound | 这是验收汇总结果；仍需将资源释放单独报告 |\n\nTaskBoundary 保留 missing/failed evidence 与 missing/failed verifier 两组字段。单个 `status` 按优先级取一个主要原因，因此 UI/诊断不能只读 status 丢掉其他并存失败。它还会用当前磁盘存在性修正 completed/pending artifact 列表；这属于读模型对齐，不是自动生成 authoritative effect receipt。强验收仍应消费 Broker/DEO 与当前合同证明。\n\n`complete_run` 的 success 参数只闭合 orchestration lifecycle，不升级已经 FAILED 的 run，不授权 verified。成功 QA 后通知项目 completion 要等 Factory completed 事件先 durable，避免在 Factory chain 尚未闭合时查询最终汇总器。\n\n源码：TaskBoundary（src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py:815）、Factory 完成定义（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1212）、post-terminal notify（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_physical.py:382）。\n\n## 7. operational closed 与 passed 的真正差异\n\n当前 Factory settlement barrier 的公式可直接从源码得到：\n\n```text\nclosed = 找到该 Factory 的事实\n         && open_lifecycle_count == 0\n         && (没有 missing required evidence || 失败情况下所有 dispatch 有严格闭合证明)\nrelease_allowed = closed\npassed = closed && run_projection.ok && failed_lifecycle_count == 0\n         && missing_required_modalities 为空 && failed_required_modalities 为空\n```\n\n所以失败 run 完全可以 `closed=true/release_allowed=true/passed=false`。这是安全清理的正确结果：失败的工具/命令确实终态，证明仍保留失败，不需要补成功收据才能释放工作区。反过来，active_task_count=0 也不充分：曾经 dispatch 的 effect 或 provider attempt 可能仍未闭合。\n\n当前 `_lifecycle_counts` 用最新 task facts 求 task 状态，却给 failed_count 加上历史 tool lifecycle failed_count。这个混合使 barrier.passed 可能长期保持 false，值得审计其名称、consumer 和 acceptance revision。**不能由此直接推出最终交付必然被历史失败毒化**：当前 ProjectOutcome adapter 并不读取这个 passed，而是读另一组 exact-scope ledger/current physical owner 结果。需要分别证明最终 consumer 是否使用历史失败计数，不能删旧失败、只扫描新 attempt、或伪造 pass。\n\n源码：counts（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:217）、barrier 公式（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:726）、最终 owner adapter 判据（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:782）。\n\n## 8. current acceptance 与 all-history resource closure\n\n完整历史有两个用途，必须分别投影。\n\n资源安全需要检查每个 stable execution identity。新 session 覆盖任务当前 row，不会抹掉旧 session 的 effects；全部旧 attempts 都需要 strict closure。RunLedger public query 明确构造每个 task/external/session/attempt/role/worker/run tuple 的 observer query。仅检查最新成功 attempt 会遗漏旧未完成副作用。\n\n验收当前版本则需要 CE 合同和当前 input/authority revision 的证明。旧失败必须留在历史中，不能继续代表当前候选结果。ProjectOutcome adapter 会将 Factory frozen terminal rows 与新 live repair rows 按 owner/seq 合并，剔除 removed live rows，再按 CE covered task ids 限定；它也可以用当前、全义务闭合的 physical diagnostics 证明任务已完成。这里必须保持 exact current owner bundle，不可凭“最后一条时间更晚”覆盖另一合同或项目。\n\n原终态清理的顺序为：physical attempts closure → child settlement → stage claim reconciliation → ledger release barrier → 保存 frozen TaskRuntime projection → 正式 reset(keep_plan=True) → lease CAS release。清理删除 live row/session 后还要从 immutable facts/frozen projection 找到原任务、CE 和 session；这就是 completed/failed/sparse-heartbeat/reset 历史组合为何在恢复中暴露过不同错误。\n\n源码：全部 attempts（src/backend/polaris/cells/control_plane/run_ledger/public/service.py:1497）、frozen/live merge（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:146）、freeze-before-reset-before-release（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:427）。\n\n## 9. Workspace lease、stage claim 与 lifecycle claim\n\n工作区独占 lease 防止多个 Factory owner 同时写同一 workspace。fencing_token 划分 authority epoch；过期只说明旧 owner 不再可申请新权限，不是旧物理资源已经关闭。stage claim 绑定一次 stage 执行，lifecycle operation claim 绑定 cancel/settle/recover/retry 等控制操作；nonce、sequence、fence 和 run 身份必须一起匹配。\n\n```mermaid\nstateDiagram-v2\n    [*] --> ACTIVE: 正式 admission / fresh fence\n    ACTIVE --> DRAINING: cancel / failed / complete closeout\n    DRAINING --> RELEASED: 严格物理与child/effect证明 + CAS\n    DRAINING --> DRAINING: proof unknown / conflicting owner\n    RELEASED --> ACTIVE: 正式 retry / fresh epoch\n```\n\n状态图只表示资源生命周期，省略业务 pending/running/failed/completed。每个控制操作也有自己的 claim/commit/rollback。两个操作都认为对方的 expired claim 不能动，就会产生 orphan deadlock；支持 exact terminal settle nonce 恢复的通用路径应同时保留 proof 重读与 single CAS winner，而不是清锁。\n\nrestart 后 process-local coordinator 不再存在，当前实现需要 reconstruct/replay 所有 immutable physical starts/terminals，并在受权 lease lock 下读两次 stable views/fence。空的新进程不能替旧请求闭合证明，旧 GET_run.metadata 也不是 fresh lease 的事实源。\n\n源码：exact orphan resume（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1409）、stale owner route（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1525）、restart replay（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:114）。\n\n## 10. Repair 与 recovery 的区别及收敛边界\n\nRepair 改善目标实现，recovery 恢复原控制权限与继续执行资格。ordinary compiler/test/entry failure 应产生 typed diagnostic，送回同一个 Director owner，在 CE 允许范围修复；hash/auth/symlink/contract drift 则应拒绝候选或阻塞，不能当 ordinary feedback 自动吞掉。\n\nrepair kernel 的规则、coverage、计划、composer、调度顺序和 cycle breaker 归 director.runtime。roles.adapters 的 bridge 只绑定 runtime 宣布的 step；Factory 不应私自加语言 regex 或另一份 scheduler。coverage_gap 只说明没有 deterministic rule，不代表普通 LLM repair 不允许，也不意味着 advisory 可以注册规则或写文件。\n\n保存候选与完成 owner 是两种 verdict。某轮修复让 TEST 通过但 ENTRY 暴露新问题，可以在其它已通过 gates 保持、scope/authority/cmd 不漂移的条件下保存增量，并继续同任务；不能把 accepted candidate 当 Task completed，也不能在全部 verifier 尚未完成时一律 rollback 有价值进度。现有 materialization/quality 共享 convergence 已处理这一语义，但 ordinary no-progress、平台存储失败和 provider failure 仍要分开。\n\nrouter 自动 rework 不能只把 FAILED 改成 RUNNING。`_stage_local_rework_is_authorized` 要求 Service 投影的 durable workflow owner action，包含 v2 deferred-drain、action id、diagnostic id、正确 decision owner；Service 的 strict stage transaction guard 与 physical replay fence 再防止 stale callback 覆盖当前 run。QA rework、Director local rework、官方用户 retry 是不同入口，应复用 owner authority 并保留 PMCE。\n\n源码：repair ownership（src/backend/polaris/cells/director/runtime/public/service/_execution.py:825）、router rework authority（src/backend/polaris/delivery/http/routers/_factory_handlers/mapping.py:129）、guarded mutation（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_core.py:971）。\n\n## 11. 最终 ProjectOutcome 是多 owner 绑定，纯 reducer 不是 authority\n\n纯 reducer 只计算 completion_candidate，始终 authority_bound=false/completed_verified=false。bootstrap-bound owner adapter 查询当前 CE completion contract、TaskRuntime、RunLedger、physical diagnostics；校验身份、projection hash 与 evidence refs。另一 public owner port 查询 Factory chain 和 durable completed 事件。authority binder 验证两组 exact DTO 与所有 axis hash/ref，才把候选升级为 completed_verified。\n\n六轴是 delivery、chain、QA、TaskBoundary、TaskRuntime、RunLedger。当前 adapter 还要求账本 available、ready、integrity_ok/outcome_ok/capability.ok、无有效 missing/failed、QA 与 TaskBoundary 通过。physical diagnostics 必须 authority_bound、覆盖全适用 obligation、无 missing/failed；它不能从文件存在或四个 exit 0 推出全绿。\n\n这个设计避免“caller success flag 就是交付”，也避免简单重新造一个万能 completion service。需要继续核对每个 producer 怎样给这些 owner 产生当前、可重读的事实。现有控制面最复杂的点是 fact gathering、恢复后的当前性与异步顺序，而不是纯 reducer。\n\n源码：纯 reducer（src/backend/polaris/cells/runtime/projection/internal/project_outcome.py:15）、owner authority binding（src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py:69）、当前 owner bundle（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:431）。\n\n## 12. 已证明的组合缺口与尚未证明的风险\n\n| 类别 | 证据强度 | 结论及影响 |\n| --- | --- | --- |\n| 长历史 strict append 硬上限 | 原生产 first-failure + 当前 root/source trace | turn_outcomes 原流 8,354,716bytes，8MiB 剩33,892，末条已提交帧为552,213bytes；后继 outcome 被拒，未提交 encoded 帧的精确大小未知；工具结果之后 outcome 无法 durable commit。严格读取分页已改不解决 append lifetime cap。不能关 strict、删历史或只调巨大 limit。F35 未实施。 |\n| FINAL wire capture 路径覆盖 | 实际七个语义快照缺 final body；callback 全end | observability/authority evidence gap 已存在；实际 transport 数/具体遗漏入口还应沿各调用链确认，不能报0或已qualify。 |\n| 层级 success 混用 | 原 epoch6 real receipt 与 settlement conflict；当前源码已分离 | 保存增量曾被当 owner完成，引发 completed 与负命令冲突。修复不能反向把失败验收降成成功。 |\n| reset→原任务恢复的 terminal-state 组合 | 原 actual9/11 fail + 原Task1 producer修复 | sparse辅助facts、removed successor、prevcompleted 路径曾不一致。当前148的组件与独审证据已通过，不代表完整原项目交付。 |\n| historical_failed 累加 | 当前静态源码 | barrier.passed 可能保留历史失败，但 finalProjectOutcome 不消费它；最终poison尚未完成因果证明。 |\n| 重复全历史读 | query源码声明 O(A*T)；原单次strict query156s、服务GET超时 | byte分页限制工作集，却仍多次验证历史；CPU/复杂度与事件循环/transport响应预算要分开优化。此次只读未重新跑冷扫描。 |\n| DTO/Graph/API多种兼容入口 | Graph与当前bootstrap/query源码 | 增量类型和旧service façade使矩阵难以穷尽。Graph精简query inventory没有完整展示当前authority查询，应更新发现/治理资产；不能仅按module名字证明所有调用合规。 |\n| 简略状态覆盖多原因 | TaskBoundary字段与优先级源码 | missing/failed均有字段，主status单值；只用它投影会丢掉并存的真实失败。 |\n\n证明包引用：`role-delivery-full-20261013/native18-actual-epoch12-quality-append-first-failure.json` SHA `00f7e4b7b94fbc385c2bdb816058d3d007f22ac255b212939231830bc1d5dd7c`；`native18-actual-epoch12-engineering-quiescence.json` SHA `c589fef374268b8d0a74b50160f1e65e2f5a63051e68d2c5a33afe27a3f18b25`。后者证明原进程停止，不把缺 wire 或工程 STOP 当 namespace release；原资源真实 DRAININGf28。\n\n## 13. 建议的收敛顺序（未实施）\n\n1. **先画清 authority/read-model/operational 三张边界图。** 复用已有 Cell 和 public contracts，给每个 producer→consumer 明确身份、hash、revision、单次效应、证据新鲜性、失败类型。不要另造第二事实源或把审计包写进平台账本。\n2. **将跨路径执行证明收敛到现有入口。** 主执行、Factory quality repair、QA rework、role session 的 final provider freeze/生命周期，工具→DEO→outcome_commit，exactCE→Broker→QA 都应消费同一类当前授权。先解决缺 wire 与 durable append 的已证组合瓶颈，再继续付费真实运行。\n3. **把资源闭合与当前验收投影显式分开。** 资源 closure 扫全历史；acceptance 使用真实合同/current input authority revision。失败历史保留。明示 barrier.passed 的适用范围，ProjectOutcome仍是现有多owner汇总器。\n4. **有界的是每次工作与单帧，不应意外成为合法项目的短寿命。** 用既有 FactStream 的 seq、完整性和 CAS 原理评估长流 append/query；分帧/分页/immutable-prefix方案必须仍验证全链和同一稳定EOF，不能以普通cache替strict authority。snapshot全量回灌的尺寸也应在 producer 原理层审计。\n5. **组合状态矩阵比增加单点flag更重要。** 覆盖 failed/completed/reset、restart coordinator absent、transport取消但worker仍运行、read-only outcome、negative command receipt、多次batch rollover、QA fail/rework、当前通过+旧失败、Append失败在effect前后等 producer真实场景；不要只stubsummary来证明闭环。\n6. **先组件与架构反证，最后一条真正项目链。** 若未来获准实现，源互斥STOP后相应focused/negative＋必要正式K1 gate，再真实fresh/current完整链。当前 CE 合同的全部 required 验收义务（本案例为五项）、已配置的独立 QA、owner-bound ProjectOutcome 和资源闭合才能称完整交付；API200、源码批准、host CLI0、可释放资源都不是替代品。\n\n本分析没有宣称实现已全部收敛，也没有展开任何源修复。机器可读事实、确定性结论/静态推断分级及 source refs 见 `control-evidence.json`；CodeGraph 原始回包保留在 `control-codegraph/`。\n",
      "diagram": "delivery"
    },
    {
      "id": "verification",
      "title": "验证与恢复",
      "subtitle": "当前输入、完整义务、同任务修复",
      "summary": "真实命令与 proof、独立 QA、资源闭合和最终结果。",
      "markdown": "# 当前验证、同任务修复与最终接受\n\n## 5. 真实验证：许可、执行、证明与当前性分开\n\nVerifier Policy 只评估 proposal，不 mint execution authority。ExecutionBroker 从 bootstrap-bound owner port 解析 exact DTO，验证 workspace/project/run/contract/obligation、argv/cwd 与 executable identity；spawn 前重新解析 authority，若已变化则拒绝。随后冻结全部声明输入，绑定 input hash，启动受控进程并产出 owner 收据。\n\n“命令执行成功”仍可能只是 candidate observation。正式验收需要收据 seal、当前输入/authority、指定 proof 与稳定输出。长驻入口还可以按已声明 readiness profile 判断 ready 后受控终止；不能把它等同于任意进程存活，也不能把 CLI 缺失替换成宿主旧 dist 能跑。\n\nVerificationGroup 的用途是让 build→type/test→entry 在同一个干净候选上共享派生产物。开始时只复制批准的 authored inputs 和依赖，避免 stale dist。entry 不通过时前面的 exit 0 是实际进展，但未 seal 的整组并不是完整 authority proof。actual12 正是 ENV/build/type/test 为 0、exact ENTRY 为 1；它比旧 host npm start 更真实，也仍是失败。\n\n源码：policy proposal（src/backend/polaris/cells/control_plane/verifier_policy/public/service.py:856）、spawn 前再授权（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1367）、receipt proof（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1045）。\n\n## 6. 至少六种“完成”，不能压成一个绿色状态\n\n| 判断 | 它实际回答的问题 | 与全项目交付的关系 |\n| --- | --- | --- |\n| provider terminal | transport/worker 是否结束，terminal 是否持久化 | 无正确实现含义 |\n| effect receipt committed | 受权副作用是否真实、可追责地发生 | 有效果仍可编译/测试失败 |\n| TaskBoundary completed_verified | 当前任务声明的产物、依赖、证据、verifier 条件是否满足 | 范围来自当前任务边界；不能替全项目 QA |\n| TaskRuntime completed/settled | owner attempt 是否终态、还能否写；任务投影是否 converged | completed task 可以伴随项目 QA 未运行或失败 |\n| Factory completed | orchestration session/阶段链是否结束 | `complete_run` 明确写 verified=False |\n| ProjectOutcome completed_verified | 六轴当前 owner 证明是否全部满足且 authority_bound | 这是验收汇总结果；仍需将资源释放单独报告 |\n\nTaskBoundary 保留 missing/failed evidence 与 missing/failed verifier 两组字段。单个 `status` 按优先级取一个主要原因，因此 UI/诊断不能只读 status 丢掉其他并存失败。它还会用当前磁盘存在性修正 completed/pending artifact 列表；这属于读模型对齐，不是自动生成 authoritative effect receipt。强验收仍应消费 Broker/DEO 与当前合同证明。\n\n`complete_run` 的 success 参数只闭合 orchestration lifecycle，不升级已经 FAILED 的 run，不授权 verified。成功 QA 后通知项目 completion 要等 Factory completed 事件先 durable，避免在 Factory chain 尚未闭合时查询最终汇总器。\n\n源码：TaskBoundary（src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py:815）、Factory 完成定义（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1212）、post-terminal notify（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_physical.py:382）。\n\n## 7. operational closed 与 passed 的真正差异\n\n当前 Factory settlement barrier 的公式可直接从源码得到：\n\n```text\nclosed = 找到该 Factory 的事实\n         && open_lifecycle_count == 0\n         && (没有 missing required evidence || 失败情况下所有 dispatch 有严格闭合证明)\nrelease_allowed = closed\npassed = closed && run_projection.ok && failed_lifecycle_count == 0\n         && missing_required_modalities 为空 && failed_required_modalities 为空\n```\n\n所以失败 run 完全可以 `closed=true/release_allowed=true/passed=false`。这是安全清理的正确结果：失败的工具/命令确实终态，证明仍保留失败，不需要补成功收据才能释放工作区。反过来，active_task_count=0 也不充分：曾经 dispatch 的 effect 或 provider attempt 可能仍未闭合。\n\n当前 `_lifecycle_counts` 用最新 task facts 求 task 状态，却给 failed_count 加上历史 tool lifecycle failed_count。这个混合使 barrier.passed 可能长期保持 false，值得审计其名称、consumer 和 acceptance revision。**不能由此直接推出最终交付必然被历史失败毒化**：当前 ProjectOutcome adapter 并不读取这个 passed，而是读另一组 exact-scope ledger/current physical owner 结果。需要分别证明最终 consumer 是否使用历史失败计数，不能删旧失败、只扫描新 attempt、或伪造 pass。\n\n源码：counts（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:217）、barrier 公式（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:726）、最终 owner adapter 判据（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:782）。\n\n## 8. current acceptance 与 all-history resource closure\n\n完整历史有两个用途，必须分别投影。\n\n资源安全需要检查每个 stable execution identity。新 session 覆盖任务当前 row，不会抹掉旧 session 的 effects；全部旧 attempts 都需要 strict closure。RunLedger public query 明确构造每个 task/external/session/attempt/role/worker/run tuple 的 observer query。仅检查最新成功 attempt 会遗漏旧未完成副作用。\n\n验收当前版本则需要 CE 合同和当前 input/authority revision 的证明。旧失败必须留在历史中，不能继续代表当前候选结果。ProjectOutcome adapter 会将 Factory frozen terminal rows 与新 live repair rows 按 owner/seq 合并，剔除 removed live rows，再按 CE covered task ids 限定；它也可以用当前、全义务闭合的 physical diagnostics 证明任务已完成。这里必须保持 exact current owner bundle，不可凭“最后一条时间更晚”覆盖另一合同或项目。\n\n原终态清理的顺序为：physical attempts closure → child settlement → stage claim reconciliation → ledger release barrier → 保存 frozen TaskRuntime projection → 正式 reset(keep_plan=True) → lease CAS release。清理删除 live row/session 后还要从 immutable facts/frozen projection 找到原任务、CE 和 session；这就是 completed/failed/sparse-heartbeat/reset 历史组合为何在恢复中暴露过不同错误。\n\n源码：全部 attempts（src/backend/polaris/cells/control_plane/run_ledger/public/service.py:1497）、frozen/live merge（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:146）、freeze-before-reset-before-release（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:427）。\n\n## 10. Repair 与 recovery 的区别及收敛边界\n\nRepair 改善目标实现，recovery 恢复原控制权限与继续执行资格。ordinary compiler/test/entry failure 应产生 typed diagnostic，送回同一个 Director owner，在 CE 允许范围修复；hash/auth/symlink/contract drift 则应拒绝候选或阻塞，不能当 ordinary feedback 自动吞掉。\n\nrepair kernel 的规则、coverage、计划、composer、调度顺序和 cycle breaker 归 director.runtime。roles.adapters 的 bridge 只绑定 runtime 宣布的 step；Factory 不应私自加语言 regex 或另一份 scheduler。coverage_gap 只说明没有 deterministic rule，不代表普通 LLM repair 不允许，也不意味着 advisory 可以注册规则或写文件。\n\n保存候选与完成 owner 是两种 verdict。某轮修复让 TEST 通过但 ENTRY 暴露新问题，可以在其它已通过 gates 保持、scope/authority/cmd 不漂移的条件下保存增量，并继续同任务；不能把 accepted candidate 当 Task completed，也不能在全部 verifier 尚未完成时一律 rollback 有价值进度。现有 materialization/quality 共享 convergence 已处理这一语义，但 ordinary no-progress、平台存储失败和 provider failure 仍要分开。\n\nrouter 自动 rework 不能只把 FAILED 改成 RUNNING。`_stage_local_rework_is_authorized` 要求 Service 投影的 durable workflow owner action，包含 v2 deferred-drain、action id、diagnostic id、正确 decision owner；Service 的 strict stage transaction guard 与 physical replay fence 再防止 stale callback 覆盖当前 run。QA rework、Director local rework、官方用户 retry 是不同入口，应复用 owner authority 并保留 PMCE。\n\n源码：repair ownership（src/backend/polaris/cells/director/runtime/public/service/_execution.py:825）、router rework authority（src/backend/polaris/delivery/http/routers/_factory_handlers/mapping.py:129）、guarded mutation（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_core.py:971）。\n\n## 11. 最终 ProjectOutcome 是多 owner 绑定，纯 reducer 不是 authority\n\n纯 reducer 只计算 completion_candidate，始终 authority_bound=false/completed_verified=false。bootstrap-bound owner adapter 查询当前 CE completion contract、TaskRuntime、RunLedger、physical diagnostics；校验身份、projection hash 与 evidence refs。另一 public owner port 查询 Factory chain 和 durable completed 事件。authority binder 验证两组 exact DTO 与所有 axis hash/ref，才把候选升级为 completed_verified。\n\n六轴是 delivery、chain、QA、TaskBoundary、TaskRuntime、RunLedger。当前 adapter 还要求账本 available、ready、integrity_ok/outcome_ok/capability.ok、无有效 missing/failed、QA 与 TaskBoundary 通过。physical diagnostics 必须 authority_bound、覆盖全适用 obligation、无 missing/failed；它不能从文件存在或四个 exit 0 推出全绿。\n\n这个设计避免“caller success flag 就是交付”，也避免简单重新造一个万能 completion service。需要继续核对每个 producer 怎样给这些 owner 产生当前、可重读的事实。现有控制面最复杂的点是 fact gathering、恢复后的当前性与异步顺序，而不是纯 reducer。\n\n源码：纯 reducer（src/backend/polaris/cells/runtime/projection/internal/project_outcome.py:15）、owner authority binding（src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py:69）、当前 owner bundle（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:431）。\n",
      "diagram": "delivery"
    },
    {
      "id": "runtime",
      "title": "实例、事件与存储",
      "subtitle": "让观察对应正确的项目",
      "summary": "workspace 单根、Launcher、runtime.v2 与重放边界。",
      "markdown": "# 实例、存储与 runtime.v2：让观察对应正确的项目\n\n## 单进程单 workspace\n\nPolaris 的一个后端实例绑定一个 workspace。Launcher 通过 Instance Registry 与 Supervisor 管理多个独立实例，每个实例有自己的 workspace、runtime root 与端口。Registry 是发现与运维视图，不是 TaskRuntime、效果收据或项目结果的替代拥有者。\n\n实例入口需要保持 backend、token、workspace、instance 绑定。打开某个项目后静默改用默认后端，会使页面看起来在线而事实属于另一项目。本文原理页保留这些 URL 参数，内容本身是静态研究文档。\n\n## 文件布局是解析契约\n\nstorage.layout 统一解析工作区路径。默认项目 runtime 根为 workspace/.polaris/runtime，显式外部缓存属于配置选择，并继续按 workspace 隔离。旧 namespace 可以用于只读查找，但不应在活跃执行中自动双写或切根。\n\nContext 快照读取还存在按同 workspace 的候选根查找链。返回一个 hash 不代表快照一定可读；读取失败要能说明 workspace、hash 和已搜索位置。内容快照引用、请求 hash、任务 ID 和轮 ID 有不同语义。\n\n## 命令与实时观察分工\n\nHTTP 承担初始快照、显式刷新和一次性命令或查询。运行变化通过 Nats-JetStream 与 /v2/ws/runtime 统一传输，前端 RuntimeTransportProvider 和 runtimeSocketManager 管理连接、订阅与重连。\n\n推送页面必须保留项目范围、序号和事实新鲜度。连接成功只证明传输可用，不证明模型调用、工具写入或任务验收已完成；重连也不能凭空产生新的运行事实。\n\n## 消息恰好一次不等于工具效果恰好一次\n\nJetStream 提供持久化与至少一次投递；去重和双重确认能在规定条件下改善消息交付保证。即使消息只被交付一次，消费者执行了外部命令后、确认前崩溃，仍可能产生效果与消息状态分离的窗口。Polaris 的 DEO、幂等键、当前 owner、CAS 和效果收据处理的是另一层义务，不能把 NATS 的交付保证当作任意外部副作用保证。\n\n## 事件溯源的成本和边界\n\n严格事件存储提供序号、完整性、幂等与预期版本检查。拥有者决定某条事件的工程语义，存储原语不替拥有者认定目标正确。可重放的数据也必须带有当前权限与输入身份，不能重放模型上下文就重新执行已完成的操作。\n\n长时间会话需要审视每帧大小、总历史增长、重放与投影复杂度。当前真实失败样本中，回合结果日志总量达到 8 MiB 附近，追加端按整段历史应用限制，拒绝正常后继回合；该问题在研究快照中仍未修复。\n\n## 核查入口\n\n- src/frontend/src/app/launcher/LauncherWorkspace.tsx：实例查询与 status.instances 订阅。\n- src/frontend/src/runtime/transport/RuntimeTransportProvider.tsx：统一前端传输。\n- src/backend/polaris/delivery/ws/runtime_endpoint.py：runtime.v2 服务入口。\n- src/backend/polaris/cells/storage/layout/public：工作区物理根解析。\n- src/backend/polaris/cells/context/engine/public/snapshot_paths.py:24：快照候选根。\n- src/backend/polaris/kernelone/events/sourcing/file_store.py:613：严格追加与持久化边界。\n- https://docs.nats.io/concepts/jetstream：NATS 官方交付语义。\n",
      "diagram": "architecture"
    },
    {
      "id": "extensions",
      "title": "扩展与研究组件",
      "subtitle": "默认链路和可选能力分别证明",
      "summary": "Akashic、Resident、增强投影、协作库与接入限制。",
      "markdown": "# 非默认能力与研究性组件\n\n仓库里有实现、存在公开接口、某次执行启用了它、它带来了可重复的收益，是四个不同断言。这里按实际接入证据分类，不以模块名称、UI 卡片或构造函数的默认参数作为运行证明。\n\n## 默认主链中的上下文能力\n\nStateFirst 默认使用普通七阶段 PipelineRunner，并组合结构状态、活动窗口、ProjectionEngine、内容引用和角色补充信号。ProjectionEngine 的自适应反馈是进程内规则或评分窗口，不是模型参数训练。\n\n## 按条件消费的能力\n\n| 组件 | 有什么实现 | 接入限制 |\n| --- | --- | --- |\n| ContextOS MemoryManager | 会话快照候选、Jaccard 相关度、时间与规则冲突检查 | aggregate 条件路径；不同于 Akashic MemoryManager，不能称完整 embedding RAG |\n| Akashic | 分层工作记忆、情景和语义存储、可选向量检索、压缩 daemon | 需要显式初始化与生命周期；默认角色 daemon 接入未由本研究证明 |\n| Knowledge Pipeline | 抽取、切块、丰富、embedding、幂等入库 | 入库和当前角色召回是两步，memory_id 不证明模型已看到原文 |\n| Resident | workspace 服务、显式 start/stop、tick、insights 与 proposals | 默认 inactive；提案不接管效果权威，也不等于自动在线训练 |\n| AGI repair advisory | 建议标准化与资格投影 | 建议不能写文件、注册规则、生成权威计划或给验收结论 |\n\n## 未证明默认构造的增强组件\n\nEnhancedPipelineRunner 有 phase、attention、graph、memory 与 predictive 组合；默认 StateFirst 当前选择普通 Runner。IntelligentCompressor 及部分内容 crushers 存在实现，也不能替代默认 CompressionEngine 的实际路径。WorkingSetAssembler 描述仓库地图、符号和代码切片，但其存在不证明 UI WorkingMem 已使用它。\n\nNeural Syndicate 的 OrchestratorAgent、broker、blackboard 与可选 critic consensus 是另一组协作库。当前读到的构造器与 CodeGraph 关系没有建立其作为 PM→CE→Director 主调度的接入证明。BlackboardClient 的基本 KV 是进程内字典，TTL 参数注明未实现；产物持久化需要注入 ArtifactServicePort。资源配额的默认 100 只是配置，本文没有做 100 Agent 并发实验，也不把 confidence 合并当作项目验收。\n\n## Cognitive 与可复现评测模块\n\n认知、intent graph、推理、感知、evolution 与 holographic 相关包需要逐条查看实际调用点。部分能力位于内部评测或测试目录，服务于复现、评价与模拟；它们没有因为名字独特就变成生产中的自我意识、常驻规划或自主研究能力。\n\n## 如何证明某扩展确实工作\n\n需要实际构造与依赖注入证据、当前实例的启用配置、从主调用路径进入它的调用、结果如何被消费，以及相同条件下的失败与收益观察。只有这些证据齐全，才适合把该能力写成当前默认行为。\n\n源码定位：ContextOS 专题 E35–E47、E51、E63–E65；src/backend/polaris/kernelone/multi_agent/neural_syndicate/orchestrator.py:335、569；相关 Cell 目录的公开契约与 graph verification gaps。\n",
      "diagram": "none"
    },
    {
      "id": "limits",
      "title": "失效模式与收敛",
      "subtitle": "安全之后还需要可继续",
      "summary": "真实失败案例、存储容量、恢复组合及 wire 证据缺口。",
      "markdown": "# 失效模式与架构收敛：安全性之外还要可继续\n\n这些是封存执行与源码反例，说明跨层假设如何阻断工作。它们不是性能对照实验，也不证明所有模型或所有项目的整体能力。\n\n## 上下文位置与预算不同\n\n完整源码曾被放入受保护的当前用户指令，而该字段上限为 8k。角色模型整体窗口可以更大，但字段仍先失败。修复采用现有独立证据消息，让用户目标、完整源码、CE 蓝图和验证数据各自保持正确角色，不上调指令上限来掩盖装配错误。\n\n该案例证明容量、证据覆盖和装配位置需要分别验证。后续 prepared request 检查是组件证据，尚不能代替独立 QA 真正发到 provider 的最终请求与判断。\n\n## 子任务完成，但外层验收失败\n\nDirector Task1 已 completed，外层 QA 却可能在请求前失败。早期清理判据只接受最新失败任务或失败命令，未进入严格效果闭合检查，导致已经终止的运行保留租约。修复仅在原始 canonical 失败生命周期事件精确匹配后进入原有严格检查，仍保留验收失败与缺失证据。\n\n失败资源可以释放，项目仍不通过。不能通过把 completed 任务改成 failed 来凑进入恢复路径的条件。\n\n## 正式移除之后恢复原拥有者\n\n正式清理保留计划并移除 live task/session。后继恢复原先只接受 failed/cancelled，拒绝合法 completed-reset。修复用准确的 terminal event ID、序号、transition、outcome、合同与 old owner proof 找回原 numeric task，再走正常 fresh claim。历史完成事实没有改写，新执行也没有伪造旧会话。\n\n完整私有 Factory 回归与实际后继执行分别证明了这一边界。组件成功只覆盖该恢复机制，不能外推后续验收必定完成。\n\n## 推断命令与合同命令不同\n\n只运行 npm build/test/start 不能代替全部 CE signed argv/cwd。改变可变 package script，让某个旧产物入口能启动，也不证明 CE 的声明入口已生成。当前 VerificationGroup 使用干净 authored inputs 运行完整义务，真实暴露了声明入口缺失：环境、构建、类型检查、测试可结束，而 exact ENTRY 仍为非零。\n\n这些中间观察为真；在整组没有完整 seal 和当前 proof 前，它们不是全部验收 PASS。独立语义 QA 也不能凭自测通过忽略原始需求、接口或行为差异。\n\n## 回合提交被总历史容量限制阻断\n\n封存 roles.kernel.turn_outcomes 日志为 8,354,716 bytes、47 条记录；strict append 配置为 8,388,608 bytes，余量 33,892 bytes。已有末条记录为 552,213 bytes。这个末条大小不是被拒的新帧大小；未提交 encoded frame 的精确尺寸没有封存，本文不推断它。\n\n确定的机制是：追加端比较整个文件长度加新编码帧，读取端已按单帧与页校验。累计历史容量到达边界时，单条本来合法的后继记录会被拒绝。已经返回工具观察，不能因此声称 OutcomeCommit 成功。此问题仍未实施修复。\n\n## 调用日志和真实请求证据之间仍有缺口\n\n本次后继质量修复留下 7 对角色调用开始与结束记录。对应普通快照能证明规范投影存在，但没有完整最终 wire body/hash；不能把角色调用数当成已验证的物理请求数，也不能把缺 wire 当成没有调用。\n\n平台有 Factory 治理型 wire gate 和显式 role_session gate。问题应检查每个调用是否实际安装和传播它们，而不是宣称能力不存在或所有调用天然已被覆盖。\n\n## 收敛原则\n\n保留明确状态拥有者，统一跨层身份与证明契约；让普通产物失败回到原执行拥有者；从最新输入和最新义务修复；独立验证后继续任务；让资源闭合与结果接受分别投影。必需证据和权限边界继续有效，存储、防护、观察与恢复的预算则应与长任务的合法生命周期匹配。\n\n最重要的验收是从最初需求到当前产物、真实命令、独立 QA、多拥有者结果和正常释放的一次完整闭环。当前材料还没有这个最终成功结果。\n",
      "diagram": "none"
    }
  ],
  "modules": [
    {
      "id": "transactionturn",
      "name": "TransactionTurn",
      "category": "主链机制",
      "principle": "绑定角色与执行身份，解码模型决策、调度工具批次，再持久提交回合结果。",
      "location": "roles.kernel / TransactionTurnExecutor",
      "limitation": "内存 COMPLETED 早于 OutcomeCommit；不是项目接受。",
      "topicId": "transaction"
    },
    {
      "id": "deo",
      "name": "DEO",
      "category": "主链机制",
      "principle": "封存成员 inventory，精确持久 claim，执行后绑定真实效果收据。",
      "location": "runtime.task_runtime + roles.kernel",
      "limitation": "不提供任意外部副作用的数据库原子回滚或全局 exactly once。",
      "topicId": "tools"
    },
    {
      "id": "toolspecregistry",
      "name": "ToolSpecRegistry",
      "category": "基础支撑",
      "principle": "先归一化自然工具名与参数，再按规范类别、快照和授权执行。",
      "location": "kernelone/tool_execution + llm/toolkit",
      "limitation": "能归一化不等于有执行权限；危险歧义继续拒绝。",
      "topicId": "tools"
    },
    {
      "id": "factstream",
      "name": "FactStream",
      "category": "基础支撑",
      "principle": "拥有者事件的序号、严格完整性、预期版本与幂等持久化。",
      "location": "events.fact_stream / kernelone/events",
      "limitation": "存储不替拥有者判断工程语义；总流追加容量仍有已证缺口。",
      "topicId": "runtime"
    },
    {
      "id": "statefirstcontextos",
      "name": "StateFirstContextOS",
      "category": "主链机制",
      "principle": "用状态提取与七阶段 PipelineRunner 生成当前上下文。",
      "location": "kernelone/context/context_os",
      "limitation": "结构状态不是任务 authority；投影校验不等于完整版本 CAS。",
      "topicId": "contextos"
    },
    {
      "id": "truthlog",
      "name": "TruthLog",
      "category": "基础支撑",
      "principle": "保留原始会话 journal 与可替换活动视图。",
      "location": "kernelone/context/truth_log_service.py",
      "limitation": "本服务列表是内存；与磁盘 TurnTruthLogRecorder、严格 FactStream 不同。",
      "topicId": "contextos"
    },
    {
      "id": "workingstate",
      "name": "WorkingState",
      "category": "主链机制",
      "principle": "将目标、计划、open loops、偏好与事实结构化并标记来源。",
      "location": "kernelone/context/working_state_manager.py",
      "limitation": "启发式理解及字段上限，不提供任务完成权威。",
      "topicId": "contextos"
    },
    {
      "id": "projectionengine",
      "name": "ProjectionEngine",
      "category": "主链机制",
      "principle": "排序活动内容与锚点，稳定因果组并投影模型消息。",
      "location": "kernelone/context/projection_engine.py",
      "limitation": "选择会损失信息；进程内自适应评分不是模型参数训练。",
      "topicId": "contextos"
    },
    {
      "id": "rolesignalplane",
      "name": "RoleSignalPlane",
      "category": "主链机制",
      "principle": "按角色、优先级与新鲜度读入合同、蓝图和当前资产信号。",
      "location": "roles.kernel/context_gateway",
      "limitation": "网关补充管线，不是五个独立常驻模型；后续压缩仍可能改变可见性。",
      "topicId": "contextos"
    },
    {
      "id": "receiptstore",
      "name": "ReceiptStore",
      "category": "基础支撑",
      "principle": "大文本内容地址化、摘要与原文引用，减少重复搬运。",
      "location": "kernelone/context/receipt_store.py",
      "limitation": "内容缓存可以过期；并非 DEO 效果收据或验收 verdict。",
      "topicId": "contextos"
    },
    {
      "id": "final-request-audit",
      "name": "Final Request Audit",
      "category": "主链机制",
      "principle": "分层记录消息、工具、格式、窗口及证据覆盖；治理路径冻结 wire。",
      "location": "roles.kernel/llm_caller + kernelone/llm/engine",
      "limitation": "普通规范快照不自动等于最终发送 body；接入覆盖仍需逐调用核实。",
      "topicId": "contextos"
    },
    {
      "id": "pm-→-ce-→-director",
      "name": "PM → CE → Director",
      "category": "主链机制",
      "principle": "任务合同经蓝图与 handoff 才派生执行范围与验收义务。",
      "location": "orchestration.pm_planning / chief_engineer.blueprint / director",
      "limitation": "复用上游成果不等于跳过拥有者；不存在 PM 直连 Director 的产品回退。",
      "topicId": "authority"
    },
    {
      "id": "jobtoken",
      "name": "JobToken",
      "category": "主链机制",
      "principle": "从当前控制面合同派生 capability、身份与 scope。",
      "location": "control_plane/run_ledger/public/job_token.py",
      "limitation": "token 是权限载体，不是第二合同事实源或成功证据。",
      "topicId": "authority"
    },
    {
      "id": "taskruntime",
      "name": "TaskRuntime",
      "category": "主链机制",
      "principle": "当前任务、session、attempt、DEO 和同拥有者恢复。",
      "location": "runtime.task_runtime",
      "limitation": "任务 completed 与项目 QA、当前完整义务和资源结算分别判断。",
      "topicId": "authority"
    },
    {
      "id": "executionbroker",
      "name": "ExecutionBroker",
      "category": "主链机制",
      "principle": "真实受控进程、当前输入快照、exact argv/cwd 与 verifier 收据。",
      "location": "runtime.execution_broker",
      "limitation": "candidate observation 与正式 sealed proof 不同。",
      "topicId": "verification"
    },
    {
      "id": "verificationgroup",
      "name": "VerificationGroup",
      "category": "主链机制",
      "principle": "在同一干净 authored inputs 上运行关联 build、type、test 与 entry。",
      "location": "runtime.execution_broker / Factory quality",
      "limitation": "中间 exit 0 不等于整组全部当前义务已通过。",
      "topicId": "verification"
    },
    {
      "id": "run-ledger",
      "name": "Run Ledger",
      "category": "主链机制",
      "principle": "投影能力、工具、效果、TaskBoundary 与 missing/failed evidence。",
      "location": "control_plane.run_ledger",
      "limitation": "清理 barrier 和最终接受拥有者绑定具有不同 consumer。",
      "topicId": "authority"
    },
    {
      "id": "projectoutcome",
      "name": "ProjectOutcome",
      "category": "主链机制",
      "principle": "bootstrap 绑定多个原生 owner 的当前证明，形成六轴接受结果。",
      "location": "runtime.projection / bootstrap owner adapters",
      "limitation": "纯 reducer 输出不是 authority；全部当前证明必须完整。",
      "topicId": "verification"
    },
    {
      "id": "repair-kernel",
      "name": "Repair Kernel",
      "category": "主链机制",
      "principle": "Diagnostic、Plan、Compose、Policy/Execute、Receipt、Revalidate。",
      "location": "director.runtime",
      "limitation": "coverage、规划和 advisory 都不能代替真实受权修改与复验。",
      "topicId": "verification"
    },
    {
      "id": "runtime.v2",
      "name": "runtime.v2",
      "category": "基础支撑",
      "principle": "统一 JetStream 事件与 WebSocket 订阅的项目观察通路。",
      "location": "delivery/ws / RuntimeTransportProvider",
      "limitation": "连接健康、事件可见与执行/交付成功是不同事实。",
      "topicId": "runtime"
    },
    {
      "id": "instance-registry",
      "name": "Instance Registry",
      "category": "基础支撑",
      "principle": "Launcher 发现并管理单 workspace 的独立后端实例。",
      "location": "cells.instances / Supervisor",
      "limitation": "运维注册不能冒充任务或项目验收事实。",
      "topicId": "runtime"
    },
    {
      "id": "contextos-memorymanager",
      "name": "ContextOS MemoryManager",
      "category": "可选能力",
      "principle": "会话候选召回、Jaccard 相关度及规则冲突检查。",
      "location": "context/context_os/memory",
      "limitation": "aggregate 条件路径；不同于 Akashic，也不保证完整逻辑一致性。",
      "topicId": "extensions"
    },
    {
      "id": "akashic",
      "name": "Akashic",
      "category": "可选能力",
      "principle": "分层记忆、语义与情景存储、可选向量检索和压缩 daemon。",
      "location": "kernelone/akashic",
      "limitation": "默认角色常驻接入未建立，必须显式初始化与消费。",
      "topicId": "extensions"
    },
    {
      "id": "knowledge-pipeline",
      "name": "Knowledge Pipeline",
      "category": "可选能力",
      "principle": "文档抽取、切块、丰富、embedding 与幂等入库。",
      "location": "kernelone/akashic/knowledge_pipeline",
      "limitation": "入库成功不等于当前角色已召回，更不等于最终 provider 消费。",
      "topicId": "extensions"
    },
    {
      "id": "resident",
      "name": "Resident",
      "category": "可选能力",
      "principle": "显式生命周期、tick、目标、insights 与建议的 workspace 服务。",
      "location": "resident.autonomy",
      "limitation": "默认 inactive；提案受治理，不能绕过原执行 owner。",
      "topicId": "extensions"
    },
    {
      "id": "enhanced-pipeline",
      "name": "Enhanced Pipeline",
      "category": "研究性模块",
      "principle": "phase、attention、graph、memory 和 predictive 的增强组合。",
      "location": "context_os/pipeline/enhanced_runner.py",
      "limitation": "默认 StateFirst 构造普通 Runner，生产构造点在本次范围未证。",
      "topicId": "extensions"
    },
    {
      "id": "neural-syndicate",
      "name": "Neural Syndicate",
      "category": "研究性模块",
      "principle": "worker 委派、blackboard 与可选 critic consensus 的协作库。",
      "location": "kernelone/multi_agent/neural_syndicate",
      "limitation": "默认 PM/CE 主链接入未证；KV TTL 尚未实现，配额不是并发实测。",
      "topicId": "extensions"
    },
    {
      "id": "graph-context-assets",
      "name": "Graph Context Assets",
      "category": "基础支撑",
      "principle": "Descriptor 发现、Context Pack 工作、Verify Pack 验证，各自版本绑定。",
      "location": "context.catalog / docs/graph",
      "limitation": "图谱声明与生成包不能代替默认实例化或端到端正确性。",
      "topicId": "architecture"
    }
  ],
  "papers": [
    historyExperiencePaper,
    {
      "id": "system",
      "title": "Polaris：生成式决策与可审计工程执行之间的系统协议",
      "subtitle": "系统设计主论文",
      "abstract": "综合执行协议、上下文编译与多拥有者交付机制，分析安全性、活性和跨层证明的组合，以及当前实盘限制与收敛方向。",
      "markdown": "# Polaris：生成式决策与可审计工程执行之间的系统协议\n\n> 工程系统分析论文 · 中文版 · 2026-10-14 观察基线\n>\n> 本文分析已有实现及已封存失败，未宣称经过同行评审、提出经证明的新算法、获得比较实验优势，或完成全部项目交付。基线为工作区 HEAD `cc0cb98315ff2903783feb33e2739d43ee1c4f9d` 与 148 文件源码集合，集合 manifest 为 `4a0357bed646c94aeb15fb41021e147b0d475e3a71951ab6531623d438d42df7`。这个工作区包含已核实的增量实现，不能将该组合写成一个完全已提交、可直接 checkout 的发布 tag。\n\n## 摘要\n\n大型语言模型可以提出方案、编辑代码和解释失败，但自然语言中的成功声明并不说明操作已经获得权限、确实发生、结果已经提交，或当前产物通过独立验收。Polaris 将这些判断分配给具有单一状态拥有者的组件，并通过身份、合同、能力范围、当前输入、工具效果及收据连接生成式决策与确定性执行。角色链采用 PM→Chief Engineer→Director，并由 QA 与多 owner ProjectOutcome 汇总交付；单轮执行采用 TransactionTurnExecutor、工具批次、Directed Effect Operation（DEO）与独立 OutcomeCommit；ContextOS 则负责把历史和当前事实编译成受预算约束、可追踪的角色输入。\n\n本文依据当前源码、公开契约和封存观测，说明这些层如何协同，以及为什么“有文件”“工具成功”“轮状态完成”“任务完成”“Factory 结束”“资源可释放”均不足以单独证明交付。分析发现，现有机制的主要难点位于组合边界：上下文装配与最终 wire 并非同一对象；效果收据与轮结果存在独立失败窗口；当前验收与全历史资源闭合需要不同投影；严格存储的工作预算可能变成长期会话的增长上限。原项目第 12 个执行 epoch 仍因 exact ENTRY 失败及后继轮提交受阻而失败，独立语义 QA 未完成。这些反例支撑对协议边界的分析，不构成端到端可靠性认证。\n\n**关键词：** 多角色工程系统；TransactionTurn；ContextOS；DEO；事件溯源；能力授权；执行收据；ProjectOutcome；失败恢复。\n\n## 1. 问题、范围与证据等级\n\n一个模型回答“修好了”，至少涉及四个不同命题：它提出了可用方案；平台允许方案中的操作；操作产生了预期副作用；当前项目满足合同及独立验收。它们的证明来源不同。提示词约束模型行为，却不能替代运行时路径门禁；工具返回值描述一次调用，却不能替代编译、测试与语义 QA；状态投影便于观察，却不能因为字段名为 completed 就获得上游事实的写权限。\n\n本文把 Polaris 视为工程执行与治理系统，而不是新的通用操作系统或语言模型内核。研究对象包括 KernelOne 底座、Cell/public 契约、角色事务轮、上下文编译、任务执行、物理进程、控制面与交付汇总。内部 Bench 只作为故障生产者和观察手段，不承担生产项目状态的所有权。Launcher 中的原理图和论文是静态解释视图，不能产生运行事件或成为成功证据。\n\n证据分为三层。第一层是当前源码行为，例如状态转换、参数校验、append 与实际构造点。第二层是封存的生产路径观测，例如真实命令退出码和提交错误。第三层是由源码推导的风险，例如某个汇总字段可能混合历史与当前窗口。第三层不能直接提升为实际故障，更不能把已发现根因提升为已修复。组件测试和独立源码评审提供局部反证，但不能证明所有任务、模型、语言与崩溃时序均可收敛。\n\n## 2. 与已有方法的关联\n\nReAct 研究模型推理与动作交替；SWE-agent 强调为软件工程代理设计适合其使用的计算机接口；Reflexion 利用语言反馈和情节记忆影响后继尝试，而非更新模型权重。它们为决策、交互和反馈提供相关视角。本文关注的额外工程问题是：动作由谁授权、如何绑定当前事实、执行到哪一步、失败后还有哪些义务，以及谁有权给出交付结论。本文没有复现这些论文的实验，也不据此比较 Polaris 的性能或能力。[ReAct](https://arxiv.org/abs/2210.03629)、[SWE-agent](https://arxiv.org/abs/2405.15793)、[Reflexion](https://arxiv.org/abs/2303.11366)。\n\n事件溯源有助于分离已发生事件与派生状态，保留历史并重建不同视图；外部系统重放还必须避免再次产生副作用。Polaris 的 FactStream、幂等键、执行收据和恢复协议可以在这一视角下理解，但系统也保留 mutable session、工作区文件与进程内状态，不能将全部系统称为纯事件溯源数据库。[Focusing on Events](https://martinfowler.com/eaaDev/EventNarrative.html)。\n\nNATS JetStream 提供持久消息与基于确认的消费及重放。持久消息传输不能自动授予工具写权限，也不能将消费者重投递变成业务副作用恰好一次。Polaris 的实时观察使用 JetStream 与 runtime WebSocket；合同、效果和验收的事实仍由各 owner 提供。消息通道是运输与观察机制，不是万能事实裁决器。[JetStream 官方文档](https://docs.nats.io/concepts/jetstream)。\n\n## 3. 分层架构与单一状态拥有者\n\nGraph First 表示先从组件图确定合法依赖，再进入目录实现。Cell 是最小自治边界，跨 Cell 通过 public command/query/event/result/error 协作。Single State Owner 要求同一权威状态只有一个拥有者；读模型、缓存和 UI 可以复制必要信息，但不能自行修改原 owner 的事实。Bootstrap 负责组装 public ports，避免汇总组件通过 import 私有实现取得隐含权限。\n\n```mermaid\nflowchart TB\n    UI[Launcher / 项目工作台 / ContextOS 观察] --> F[Factory 组织完整角色链]\n    F --> PM[PM 任务合同]\n    PM --> CE[Chief Engineer 蓝图与验收义务]\n    CE --> D[Director 执行与同任务修复]\n    D --> QA[QA 当前验收与回修建议]\n    C[ContextOS 上下文编译] --> D\n    C --> QA\n    D --> T[角色事务轮 / ToolBatch]\n    T --> R[TaskRuntime DEO / execution attempt]\n    D --> B[ExecutionBroker 产物与真实命令]\n    CE --> P[Verifier Policy]\n    P --> B\n    R --> L[Run Ledger / TaskBoundary 读模型]\n    B --> L\n    QA --> O[多 owner ProjectOutcome]\n    L --> O\n    F --> O\n    K[KernelOne 存储 / 工具规范 / 锁与运行原语] --> R\n    K --> T\n    K --> C\n    K --> B\n```\n\n| 层与 owner | 保存或决定什么 | 本层不能单独证明什么 |\n| --- | --- | --- |\n| PM、Chief Engineer | 任务意图、蓝图、接口、产物与 exact command 义务、handoff | 当前实现正确、命令已运行 |\n| roles.kernel | 一轮决策、解码、工具批次与轮结果提交 | 整个项目已完成 |\n| ContextOS | 历史、结构状态、活动视图、内容引用和请求审计 | 操作获得权限、引用内容已被模型阅读 |\n| runtime.task_runtime | task/session/attempt、DEO intent/claim/receipt、任务执行事实 | QA 通过、全项目义务满足 |\n| runtime.execution_broker | 真实进程、当前产物与 verifier owner 收据 | 任意 exit 0 都可作为合同证明 |\n| control_plane.run_ledger | capability、effects、gates、TaskBoundary 与 evidence 的聚合读模型 | 上游收据可以由 summary 或缓存补造 |\n| factory.pipeline | run/stage/checkpoint、工作区 claim、控制及终态清理 | operational completed 等于 verified delivery |\n| runtime.projection | 只读 owner observation、纯 reducer 与 authority binding | 自填全绿字段即可获得 authoritative outcome |\n\nKernelOne 为这些组件提供通用运行原语，角色协议仍归 roles.kernel，DEO 持久状态归 TaskRuntime，修复 catalog 与 scheduler 归 director.runtime。已有 ProjectOutcome 是多 owner 汇总器；因此不能把当前缺口描述成“平台没有统一状态机”，更不能另造第二套任务、授权或完成事实源。[S01]、[S17]、[S23]、[S24]\n\n## 4. 形式对象：身份、授权、输入、效果与结果\n\n为解释现有 DTO，本节使用分析记号，不引入新的产品 schema。一次执行的身份记为：\n\n```text\nA = (workspace, project, factory_run, child_run,\n     role, task, external_task, session, attempt, worker)\nC = (PM contract, CE blueprint, completion contract, handoff)\nP = (capability scope, gate policy, policy revision)\nI = (authored inputs, dependency identity, executable identity, input hashes)\nE = (operation/member identity, normalized arguments, before/after state, receipt)\nO = (TurnOutcome, commit receipt, task verdict, QA verdict, ProjectOutcome)\n```\n\nworkspace 同时是存储和权限边界，一个后端进程绑定一个 workspace。多项目通过多个独立实例观察，不把 active workspace 拼成临时多租户事实层。task 是业务义务的身份，session 承载同一任务连续轮，attempt 区分一次 claim 与执行资格。新 session 可以替换当前 row，但不能抹掉旧 attempt 的效果。role 决定上下文身份与能力策略，不能仅靠一个 prompt 标签重建授权。\n\ncontract 描述义务，policy 描述允许的执行，input hash 描述本次实际验证的字节。effect receipt 证明副作用及结果绑定；outcome 汇总一轮或任务的结果。普通 SHA 只能校验绑定与一致性，不能独立证明来源可信，更不是针对任意恶意改写者的数字签名。授权还需可信 owner、当前 attempt、能力范围与精确状态检查。[S02]、[S05]、[S06]\n\nCE 的 JobToken 从已验证项目合同派生 target/write/read paths、required artifacts、command authority hashes 与合同/蓝图身份。它不是另一个合同事实源。一个自然命令通过通用 capability 判断，仍不能直接计入验收：正式 verifier 必须绑定 exact argv/cwd、obligation、当前 input closure 与可信可执行文件。[S16]、[S18]、[S19]\n\n应保持几个不同 token 的语义：LLM token 是窗口和输出容量；JobToken 是派生 capability；claim/fence 是精确执行许可；content ref 是文本取回索引；context_snapshot_ref 是真实落盘请求快照键。它们有时都包含 hash，却不能互相替代。字段含义应由契约决定，而不是从字符串长度猜测。\n\n## 5. TransactionTurn 与 DEO 的实际协议\n\n正式入口是 TransactionTurnExecutor 与 TransactionKernel，后者委托 TurnTransactionController。一次轮调用先验证 attempt 与装配，构建上下文和工具面，请求模型并解码，再按工具效果分组。模型提出候选调用；工具分类、授权和执行顺序由确定性代码决定。工具表、别名与参数先经 ToolSpecRegistry 归一化，再进入 scope、path、command 与 policy guard。自然参数兼容不是权限扩张。[S01]、[S02]、[S03]、[S38]\n\n```mermaid\nsequenceDiagram\n    participant Session as 原任务 Session\n    participant Turn as TransactionTurnExecutor\n    participant Model as LLM 决策\n    participant DEO as TaskRuntime / DEO\n    participant Tool as Policy-gated 工具\n    participant Facts as FactStream\n    Session->>Turn: 当前 attempt / context / budget\n    Turn->>Model: 冻结工具面的角色请求\n    Model-->>Turn: 候选回答 / 工具调用\n    Turn->>Turn: 归一化、解码、形成 ToolBatch\n    Turn->>DEO: parent + immutable inventory + operations\n    Turn->>DEO: JIT claim / expected seq CAS\n    DEO-->>Turn: 精确 fresh grant\n    Turn->>Tool: consume fence、重验 policy、真实操作\n    Tool-->>DEO: 结果、before/after、effect receipt\n    Turn->>Turn: NONE / LOCAL / LLM_ONCE 收口\n    Note over Turn: 内存状态可已 COMPLETED\n    Turn->>Facts: OutcomeCommit / idempotency / durability\n    Facts-->>Turn: CommitReceipt + SealedTurn\n    Turn-->>Session: 已提交结果 / 后继反馈\n```\n\n这里的 Plan 是工具批次执行计划，不意味着每轮还有独立规划模型。Speculate 也不是必经状态。流式 shadow 可以提前执行便宜只读调用，之后 ADOPT、JOIN 或 REPLAY；写工具的 shadow 只做只读 Prepare 探测，不能提前写入。推测能力默认启用，领养重算审计默认关闭，且是否实际触发受调用路径控制。“默认开关启用”不能证明每轮已推测、更不能证明推测写入。[S07]\n\n正式 DEO Prepare 则是不同协议：先校验原 attempt，登记 parent，封存完整有序 inventory，绑定成员参数、scope 与 policy，再确认 readiness。JIT claim 在严格当前 stream head 上以 CAS 将 INTENT_COMMITTED 转为 EFFECT_STARTED，成功才获得可执行 grant。重复 claim 可以观察已发生状态，但 idempotent 命中不能被当成新的派发许可。[S04]、[S05]\n\nfence 绑定精确 parent/member、normalized arguments、policy、当前状态、PID 和对象身份；在进程内注册并单次消费后，私有执行器才实施效果。物理操作已发生而效果收据提交失败，需要 recovery，而不是报成功或假设没有效果。尚未 claim 的操作可以 ABORTED；已开始操作不能通过 abort 撤销现实。rollback contingency 是另一个预授权补偿副作用，只有观察证明补偿成功才可宣称 restored。[S06]、[S08]\n\n```mermaid\nstateDiagram-v2\n    [*] --> INTENT_COMMITTED: admit\n    INTENT_COMMITTED --> EFFECT_STARTED: claim + CAS\n    INTENT_COMMITTED --> ABORTED: 未开始\n    EFFECT_STARTED --> RECEIPT_COMMITTED: 真实效果收据\n    EFFECT_STARTED --> RECOVERY_PENDING: 未知或失败\n    RECOVERY_PENDING --> RECEIPT_COMMITTED: 严格恢复\n    RECOVERY_PENDING --> DEAD_LETTER: 负结果闭合\n    RECEIPT_COMMITTED --> CLOSED_BY_PARENT: 父结算\n    ABORTED --> [*]\n    DEAD_LETTER --> [*]\n    CLOSED_BY_PARENT --> [*]\n```\n\n整轮完成与整轮提交是两条边界。controller 先执行 `_execute_turn()`，再调用 canonical outcome commit。因此内存 TurnState.COMPLETED 可以早于持久化。effect receipt、TurnOutcome、CommitReceipt 和 SealedTurn 也不是同一个对象；一个轮提交失败不说明此前所有工具都没发生。sessionful outcome 选择 fsync，sessionless 路径可能采用 buffered durability，不能概括为所有写入都立即 fsync。[S09]、[S10]\n\n| 数据库术语类比 | 当前可确认机制 | 必须保留的限制 |\n| --- | --- | --- |\n| 原子性 | 单流锁下的 append/seq/idempotency；单操作 claim/receipt | 多文件、进程、网络没有共同原子 rollback |\n| 一致性 | DTO、合同、hash、scope 与合法转换检查 | 合同一致不等于业务行为正确 |\n| 隔离性 | stream 锁、CAS、owner lease、写前状态重验 | 未证明整个 workspace 可串行化 |\n| 持久性 | durable events、按路径选择 flush/fsync、commit receipt | buffered 和物理效果之间仍有失败窗口 |\n| 两阶段提交 | 先登记 inventory 后领取许可再执行 | 不是全部参与者 vote prepared 后的数据库 2PC |\n| exactly once | 稳定 intent、幂等/CAS、一次 fence、恢复避免盲重 | 不保证任意外部副作用全球恰好一次 |\n\n此外，`TaskBoard.transaction()` 的当前实现只持有进程内 RLock 并 yield；接口名称不能证明任意文件或外部操作可共同回滚。[S42]\n\n这些限制不否定协议价值。其价值是缩小权限与重复派发窗口，并在无法确定现实结果时保留 UNKNOWN 和恢复义务，而不是把“不知道”强制转换成成功或未发生。\n\n## 6. ContextOS：上下文编译与三个请求层\n\nContextOS 把 transcript、结构状态、项目资产、当前目标和工具结果编译为角色输入。默认 StateFirstContextOS 构造普通七阶段 PipelineRunner，并组合 TruthLogService、WorkingStateManager、ReceiptStore 和 ProjectionEngine。它不拥有任务执行、DEO 或交付状态，结构状态里一句 completed 不能替上游事实。[S11]、[S12]\n\n七阶段依次是 TranscriptMerger、Canonicalizer、StatePatcher、BudgetPlanner、WindowCollector、EpisodeSealer、ArtifactSelector。它们合并事件、提取领域 hints、选择活动根与近邻、分配预算、封存退出窗口的片段，并形成 artifact stubs、anchors 与 Run Card。错误可能返回带 fallback 标记的投影；“返回了 messages”不等于每个阶段成功。\n\nTruthLogService 保留进程内 journal 与可替换的活动视图；另一个 TurnTruthLogRecorder 才写磁盘观察日志，且记录是 best-effort。WorkingState 管理目标、计划、阻塞与来源 confidence，是结构化理解，不是 execution state machine。ReceiptStore 将大文本内容引用化，其 receipt 是内容取回索引，不能与 DEO effect receipt 混同。ProjectionEngine 排序、删选与压缩输出模型可读视图，不改变原事实。[S13]、[S14]\n\n```mermaid\nflowchart LR\n    H[历史 / 当前 owner 资产] --> SF[StateFirst 七阶段选择]\n    SF --> G[角色 Gateway 预算与压缩]\n    G --> A[角色装配投影]\n    A --> P[Preparer 必需数据与工具面重绑定]\n    P --> E[Engine 规范请求 / 窗口处理]\n    E --> N[Provider 原生协议转换]\n    N --> W[最终 wire body / 资格与 pin]\n    A --> SA[装配快照]\n    E --> SE[规范请求快照]\n    W --> SW[物理请求快照]\n    SA --> UI[按证据层展示]\n    SE --> UI\n    SW --> UI\n```\n\n第一层角色装配快照证明准备了哪些 messages、来源与工具候选；第二层 Engine 快照证明 post-compression 规范请求；第三层 wire 快照才绑定 provider-native endpoint/body、transport、wire hash 和执行资格。Engine 和 provider 在装配后仍可改变内容，所以前两层不能替第三层。Anthropic 的 system 是顶层字段，Responses 使用 input，不能只审统一 messages[0]。[S15]、[S20]、[S21]\n\n`/v2/context/{hash}/final-request` 兼容返回规范投影；没有真实物理 schema 时 final body 可以为空。HTTP200 或接口名称带 final 不足以证明物理请求已资格化。role_session final gate 能力存在，但需要显式 snapshot、生命周期和 drain 协调器；默认角色入口是否实际构造与传播必须沿调用链证明，不能把未接入说成能力不存在。[S22]\n\n预算也有多层。StateFirst 选择预算保留输出、工具与安全余量；角色 Gateway 使用角色策略和模型窗口的较小上界，并优先保留当前指令；Preparer 将源码与验证数据放入独立数据消息并核对来源；Engine 再计工具 schema、消息包装与输出余量。角色指令预算 8k 和模型窗口 1M 可以同时成立，因为它们限制的对象不同。[S12]、[S15]\n\n因此，无损保存、当前可见和预算内可发送必须分别判断。引用存在不意味着模型取回原文；缓存可被驱逐；freshness 标记未变不意味着旧内容经历后继裁剪后仍在窗口里。当前 token audit 是估算，不能代替 provider 实计 usage；窗口利用率也不能代替 required refs、角色身份和工具覆盖度。\n\nRoleSignalPlane 是网关 supplement 装配点：按角色、flags、priority 与 freshness 读取已有 owner 资产并分配预算，不是常驻独立模型。EnhancedPipelineRunner、Akashic 四层 MemoryManager 和 IntelligentCompressor 有实现，但默认 StateFirst 使用普通 runner；默认角色主链的实际构造点未得到同等证明。ProjectionEngine 的自适应排序是进程内启发式权重反馈，不能写成在线模型训练或已认证的强化学习系统。[S14]、[S26]、[S27]\n\n## 7. PM→CE→Director→QA 与多 owner 交付\n\n完整链路通过合同传递责任，而不是把 PM 的文本直接交给 Director。PM 提供任务意图；CE 将其变成覆盖任务、接口与验收命令的蓝图，生成可消费的 handoff 与能力约束；Director 执行并产生真实效果；QA 对当前实现和验证证据给出独立判断。一次 stage 可以复用已验证范围并不调用模型，但明确要求语义 QA 时，physical verifier 模式和自测结果不能作为该语义判断的替代品。\n\n修复并不总要重启 PM/CE。普通编译、测试或入口错误，应在原任务、原合同和已授权范围内产生 typed feedback，再由 Director 修复。若需要改变合同义务或跨 owner 扩大范围，则需要正式 rework authority，不能从错误信息临时推测新权限。`director.runtime` 拥有 repair catalog、coverage、planner、composer、scheduler 与 revalidation；adapter bridge 只绑定已声明 step，Factory 不另写语言规则或调度事实源。[S28]、[S29]\n\n候选接受与 owner 完成也要分开。一个补丁修好 TEST，却暴露 ENTRY 缺失，可以在其它已通过 gates 保持、scope/command/authority 不漂移时保存增量，再继续同任务。候选有净进展并不意味着 task 应终态成功。反之，一律在所有验证尚未完成时 rollback，会丢失可验证进展。当前实现已有相关 convergence 机制；如何在每个调用路径正确消费其 typed verdict，仍是组合审计问题。\n\nExecutionBroker 在正式 verifier spawn 前重新解析 exact CE authority，再冻结声明输入、绑定 executable identity、input hash 和 proof profile。Verifier Policy 只判断 proposal 是否属于启用、可信的证明方式，不单独 mint 执行资格。一个脚本被允许执行、一个进程成功启动、一个命令 exit 0、一个 owner receipt 被 seal，是不同证据等级。[S18]、[S19]\n\n干净 VerificationGroup 从 authored inputs 和当前依赖建立候选，先 build 再 type/test/entry，后续命令消费同一组派生输出。它避免宿主旧 dist 偷渡进新候选。即使前几个命令为 0，整组仍可能因为最后入口失败而未 seal；这些前置成功应保留为真实进展，却不能被称为全组权威通过。\n\n项目最终接受已经由 ProjectOutcome 实现。纯 reducer 对调用者提供的六轴只计算 completion_candidate，保持 authority_bound=false 和 completed_verified=false。Bootstrap-bound adapters 通过各 public owner port 观察事实，校验 exact workspace/project/run/contract 身份、projection hashes 与 evidence refs，然后 authority binder 才能授予 completed_verified。[S23]、[S24]\n\n| ProjectOutcome 轴 | 实际问题 | 需要的 owner 证据 |\n| --- | --- | --- |\n| delivery | 当前产物与物理义务满足了吗 | 当前合同、输入、全部适用 obligation、Broker/verification owner proof |\n| chain | Factory 全链是否正式完成 | 配置 stages、成功 stage 与 durable completion event |\n| QA | 当前独立审查通过了吗 | 当前 scope 下的 committed QA verdict 与约束 |\n| TaskBoundary | 每个任务局部边界完整了吗 | 产物、依赖、missing/failed evidence 与 verifier 列表 |\n| TaskRuntime | owner 任务是否收敛 | 原 task 身份、当前执行事实或严格全义务 physical proof |\n| RunLedger | 证据与完整性是否闭合 | exact scope、capability、integrity/outcome、有效 missing/failed 集合 |\n\n当前非 Factory owner adapter 不只检查绿色枚举，还要求每个 axis 的 owner hash 绑定在 evidence refs 中，并核对 CE covered tasks、当前 Director child runs 与全部适用义务。因此现有机制不是“模型自评后完成”，也不是“把任意六个 true 拼成接受”。同时，六轴汇总仍依赖 producer 正确产出当前事实；纯 reducer 的确定性无法弥补上游遗漏。[S24]、[S30]\n\n## 8. 正常交付时序及其证明链\n\n```mermaid\nsequenceDiagram\n    participant PM as PM\n    participant CE as Chief Engineer\n    participant D as Director / 原任务\n    participant EB as ExecutionBroker\n    participant QA as QA\n    participant F as Factory\n    participant PO as ProjectOutcome\n    PM->>CE: 任务合同与约束\n    CE->>D: 验证蓝图 / handoff / capability\n    D->>D: 连续事务轮 / DEO / durable outcomes\n    D->>EB: exact CE recipes + 当前输入\n    EB-->>D: artifact / command owner receipts\n    D-->>F: 当前 TaskBoundary / TaskRuntime 状态\n    F->>QA: 当前合同、源码与完整验证证据\n    QA-->>F: committed verdict 或有授权 rework\n    F->>F: stage commit / durable terminal chain\n    F->>PO: post-terminal completion notification\n    PO->>PO: 多 owner 身份与 hash/ref 绑定\n    PO-->>F: authoritative outcome 或阻塞原因\n    F->>F: 严格 resource drain / lease release\n```\n\n图示的是已有接口的主要依赖，不是把所有 I/O 放入一个大原子事务。某一步的异常可能晚于更低层效果发生；因此后继恢复必须先确认事实，而非从上层失败字符串推断“没有写过”。同样，QA 接受不能消除另一个尚未终止的请求，lease release 也不能把 QA 失败改成成功。\n\nFactory 的 `complete_run(success)` 明确只闭合 orchestration lifecycle，保存 verified=false，且不能把已经 FAILED 的 run 升成成功。项目 completion notification 要等 Factory terminal event 已持久化再触发；否则汇总器读到未完成 chain，会把一个准备完成的候选正确判为 incomplete。通知失败应保留为控制面阻塞，不回滚已真实完成的低层效果。[S31]、[S32]\n\n一个可追责链条可写为：最终请求 → 响应/工具生命周期 → 受权 effect receipt → 当前 verifier proof → TaskBoundary/TaskRuntime → RunLedger/QA → owner-bound ProjectOutcome → protected resource release。每个箭头都需要具体身份与引用。源码 whitelist、日志 preview、prepared QA prompt、成功状态摘要只属于辅助说明，不能跳过中间证明。\n\n## 9. 失败恢复、安全性与活性\n\n资源生命周期和业务验收不是同一条状态轴。工作区 lease 防止多个 Factory owner 同时修改同一 workspace，fencing_token 区分权限 epoch；stage claim 绑定一次阶段执行；lifecycle claim 则绑定 cancel、settle、recover、retry 等控制操作的 nonce、sequence 和 run。过期表示旧资格失效，不表示它启动的 provider、进程或效果已经停止。\n\n```mermaid\nstateDiagram-v2\n    [*] --> ACTIVE: 当前 owner admission\n    ACTIVE --> DRAINING: 失败 / 取消 / 正式收口\n    DRAINING --> DRAINING: 存在 live 或 UNKNOWN 义务\n    DRAINING --> RELEASED: 严格资源证明 + CAS winner\n    RELEASED --> ACTIVE: 正式 retry / fresh fence\n```\n\n当前 settlement barrier 中 release_allowed=closed。closed 要求观察到该 Factory、open lifecycle 为零，以及 missing evidence 时严格的 terminal failure dispatch closure。passed 另要求 run projection 正常、failed count 为零、required evidence 无 missing/failed。因此 closed=true、release_allowed=true、passed=false 可以是完全正确的失败清理结果。[S33]\n\n资源安全需要检查全部 stable execution identities：新 attempt 覆盖当前 task row，并不删除旧 attempt 的 effects。验收当前版本则需要当前 CE contract、input 与 authority revision 的证明。旧失败要保留为历史，却不能仅因保留就假定它代表当前候选。当前 barrier 的 failed_count 混合最新任务状态与历史 tool failed_count，是需要明确消费语义的静态风险；最终 ProjectOutcome adapter并不直接读取该 barrier.passed，所以“历史失败 16 次”不足以证明最终接受必被毒化。[S30]、[S33]、[S34]\n\n原终态清理先确认 physical requests、child sessions 与 effect 义务，再对齐 stage claim 和 ledger barrier；保存 frozen terminal TaskRuntime authority 后，才执行正式 reset(keep_plan=True) 和 lease CAS release。恢复需要从 immutable facts 与 frozen projection 找回原 task、session、CE tuple，不能因为 live row 已删除就新建另一任务掩盖问题。[S35]、[S36]\n\nrestart 后进程内 coordinator 不再存在，必须 replay immutable start/terminal，并在持有相应 lease authority 的情况下重读稳定 views/fence。一个空的新进程不能证明旧请求已结束。两种恢复 route 若互相拒绝对方的 expired claim，会造成 orphan deadlock；通用解法是精确 resume 原 terminal nonce，并保持 child quiescence、strict closure 与单次 CAS winner，不能手工清锁或改 TTL。[S35]\n\n安全性要求宁可阻塞也不重复派发未知效果；活性要求可证明的终态与同 owner 后继修复能继续。两者并不天然等价：UNKNOWN 正确阻塞，是安全机制的表现；若已有严格负结果却永久不能释放，则是恢复组合缺口。有限模型预算、单轮 ceiling、deadline、资源 lease 和存储限额也分别限制推进。本文没有证明这些约束下所有任务必能最终成功。\n\n## 10. 观察方法与真实失败案例\n\n本次分析先从 Graph、public contracts 与 CodeGraph call-path 定位，再用专家核实的源码索引解释实际执行体；只对已封存的 first-failure 和 closure 观测作因果整理。研究没有重新启动原实例、请求 provider、执行目标命令或进行新的冷历史扫描。历史 read-only query 的耗时仅描述那次观察，不是可泛化性能实验。\n\n| 案例 | 已观察边界 | 机制解释 | 当前能声称的范围 |\n| --- | --- | --- | --- |\n| F30：QA 指令预算 | 原第 10 epoch 将完整源码证据拼入 current instruction，QA 在 provider 前失败 | 角色指令预算与模型窗口不同；数据 dossier 应作为来源受控的独立 data slot | 当前源码已经有对应数据 slot 与覆盖校验；prepared 成功不等于语义 QA 通过 |\n| F32：失败 run 的清理入口 | Factory 已失败、Task1 却 completed，缺 QA 等证据时旧 failure-only 谓词不能进入严格负结果闭合 | 任务局部终态与项目失败可以同时成立，closure 应按 exact canonical failure/dispatch proof 判定 | 局部修复与原严格只读 closure 已有证据；passed=false及历史失败保留，不冒称交付 |\n| F34：completed owner 恢复 | 正式 reset 记录 prevcompleted，后继恢复仍要求 owner failed，原 Task1 在 admission 前失败 | reset、failed/completed 与恢复 checkpoint 需要同一 canonical identity 规则 | 当前148源码已支持对应同任务、同 CE 恢复，原第12 epoch确已进入后续质量路径；项目仍失败 |\n| F35：轮结果 append | 原第12 epoch 质量修复出现 strict event stream append byte limit 错误 | 严格读页预算与 append 整个流的 lifetime cap 不同 | 已定位，当前未实施修复，不能写成已闭环 |\n\nF35 的 `roles.kernel.turn_outcomes` 已落流为 8,354,716 bytes、47 帧；默认严格 append cap 为 8,388,608 bytes，仅剩 33,892 bytes。已落最后帧为 552,213 bytes。**最后帧大小不是本次未提交 encoded 帧大小；本文没有后者的独立字节证据，不作等同推断。** 当前源码仍在 append 时以“既有整流字节＋新编码帧”检查上限，合法长期历史会接近这一有限容量。[S10]、[S37]\n\n原第12 epoch 的 clean authored-input Group 实际观察到 ENV、BUILD、TYPECHECK、TEST 退出为 0，exact ENTRY 为 1，编译产物路径不能满足已声明 CLI 入口。后续 Director 有七组新语义 start/end callback及只读结果，但新快照缺 final physical body/wire hash；真实物理请求数未知，不能报 0，也不能把七份快照当作七份合格 wire 证明。独立语义 QA 尚未执行，原项目仍 FAILED，整组未 seal。\n\n该案例不是对模型能力的直接否定。入口配置是普通目标缺陷，本应由授权 Director 修复；轮结果提交失败和最终请求证据缺口属于平台组合边界。将二者都写为“模型没按格式输出”会阻止正确归因。同样，局部组件测试通过并不消除原项目尚未完成的 ENTRY、QA 与交付义务。\n\n## 11. 架构成本与收敛原则\n\n协议可靠性来自更多检查，也承担更多成本。一次 outcome commit 需要整理执行证据、序列化 payload、查幂等历史并 append；DEO claim 需要当前 head、成员身份与 policy 重验；strict closure 还要观察全部历史 attempts。源码中的 Factory barrier 查询说明，每个独立 attempt 的 owner-history 校验可累积为 O(A*T) 工作。分页限制内存页大小，不自动消除多次历史遍历。[S34]、[S37]\n\n第一项成本是事实重复。task execution event 可能携带完整 row snapshot，角色 history、上下文、工具结果和 summary又包含相似内容。持久化每一步的全量视图便于恢复，却会扩大 I/O、序列化和扫描；一个 8MiB lifetime cap 与长期同任务运行可能因此发生冲突。后续优化应从 producer 的记录粒度、稳定 immutable prefix、per-frame/work budget 与完整验证边界出发，而不是关 strict 或无限放大上限。\n\n第二项成本是身份传播。workspace、合同、task、session、attempt、authority、input hash 必须跨主执行、质量修复、QA rework、role session 和 restart replay保持一致。少传一个字段可能使正确效果被拒，多信一个缓存字段又可能绕过当前 owner。这里更需要 producer→consumer 的明确契约与真实组合反例，而不是继续加入路径专用 flag。\n\n第三项成本是观察误差。内存 COMPLETED、best-effort truthlog、语义 snapshot、未 seal 的验证组都能提供进展，却缺少另一层完成证明。UI 应展示当前证据等级、缺失和失败，不应只选择最显眼的绿色字段。角色上下文也要保留当前失败与已通过 frontier，减少过期错误反复回灌。[S09]、[S15]、[S33]\n\n可收敛的原则是：复用已有 Cell public 能力；使各执行路径使用同类 authority 与最终请求屏障；将资源 closure 与当前 acceptance 明确分开；保留失败历史与真实 UNKNOWN；为有进展候选提供 typed continuation；对压缩、引用与可选模块声明实际接入状态。最终仍要用一条当前合同下的真实项目链验证效果，而非将解释文档、局部测试或原理图当作运行成功。\n\n## 12. 局限与结论边界\n\n本文没有完成模型、语言或项目全集的比较实验，没有提供吞吐、延迟或成功率指标，也没有形式化证明全 workspace isolation、任意远程副作用 exactly once 或恢复必然终止。观察包含多个源码波次；已修的局部生产者、当前源码能力与旧失败记录分别标注，不能把旧记录当成新修复后的真实端到端成功。\n\nContextOS 的某些更强主张尚无依据。StateFirst snapshot→compute→commit 的注释提到 CAS，但当前验证并未完成强源版本比较；这是静态风险，本文未新增并发反例。缓存 eviction、best-effort 原文镜像与模块级进程内权重，也限制重启与多 workspace 情况下的保持性。Enhanced/Akashic 的组件实现、默认主调用接入和本次实际执行，是三个不同问题。[S11]、[S26]、[S27]\n\n形式对象和状态图是现有协议的解释抽象，不是新的执行实现。DEO 动态加载 `.source` 文件是实际生产执行体；论文引用它表示可追到执行实现，不能反向把所有带 source/log/receipt 字样的对象当权威。源码行号可能随后续修订变化，应同时核对文件内容与工作区基线。[S08]\n\nPolaris 已有将生成式 proposal 与副作用授权分离、保存效果及轮结果、按当前输入验证、通过多 owner 汇总交付的工程机制。尚未闭合的重点是这些机制在长期执行、不同调用路径及恢复时序中的一致组合。本论文记录这种已有能力及真实缺口，不宣称原项目已交付，也不以研究出版、页面上线或源码评审代替真实验收。\n\n## 13. 源码复核路径\n\n下列定位来自当前源码或专题专家核实索引；它们是可复核实现入口，不是接口永久稳定性承诺。复核顺序为角色调用→工具批次→DEO→OutcomeCommit→ContextOS请求层→verifier→Task/Factory→ProjectOutcome。只读查看源码与已落证据不需要运行原项目；若要执行新的复现，必须另行取得对应工程范围、实例绑定与控制授权。\n\n\n- **[S01] TransactionTurnExecutor.execute_turn**：`src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_executor.py:62`。\n- **[S02] kernel 装配与 attempt 冻结**：`src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:220`；`src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_id.py:182`。\n- **[S03] ToolSpecRegistry 规范与别名快照**：`src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py:446`。\n- **[S04] DEO parent / inventory admission**：`src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:420`。\n- **[S05] JIT claim 与当前 stream head**：`src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:886`。\n- **[S06] PID / object-bound fence**：`src/backend/polaris/cells/roles/kernel/internal/directed_effect_dispatch.py:77`；`src/backend/polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py:608`。\n- **[S07] 推测与 adoption audit 默认**：`src/backend/polaris/cells/roles/kernel/internal/speculative_flags.py:14`；`src/backend/polaris/cells/roles/kernel/internal/stream_shadow_engine.py:148`；`src/backend/polaris/cells/roles/kernel/internal/speculation/write_phases.py:23`。\n- **[S08] DEO 合法状态、实际 loader 与补偿**：`src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:6707`；`src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository.py:51`；`src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:755`。\n- **[S09] 轮状态执行后 canonical outcome commit**：`src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:729`。\n- **[S10] TurnOutcome 提交与收据**：`src/backend/polaris/cells/roles/kernel/internal/transaction/outcome_commit.py:55`。\n- **[S11] 默认 StateFirst 与 projection 验证**：`src/backend/polaris/kernelone/context/context_os/runtime/engine.py:491`；`src/backend/polaris/kernelone/context/context_os/runtime/engine.py:694`。\n- **[S12] 普通七阶段 runner 与角色预算**：`src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:97`；`src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:281`。\n- **[S13] TruthLog journal 与内容 ReceiptStore**：`src/backend/polaris/kernelone/context/truth_log_service.py:360`；`src/backend/polaris/kernelone/context/receipt_store.py:16`。\n- **[S14] ProjectionEngine 与只读 RoleSignalPlane**：`src/backend/polaris/kernelone/context/projection_engine.py:375`；`src/backend/polaris/kernelone/context/projection_engine.py:551`；`src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signals.py:563`。\n- **[S15] Preparer 必需数据、工具与 Factory candidate**：`src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007`；`src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1453`。\n- **[S16] CE JobToken 派生与 scope 约束**：`src/backend/polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py:200`。\n- **[S17] Cell 权属与 public 契约目录**：`src/backend/docs/graph/catalog/cells.yaml:106`。\n- **[S18] Broker exact authority / spawn 再验证**：`src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1310`。\n- **[S19] Verifier policy proposal 与 proof profile**：`src/backend/polaris/cells/control_plane/verifier_policy/public/service.py:856`。\n- **[S20] Engine post-compression 规范请求**：`src/backend/polaris/kernelone/llm/engine/executor.py:630`；`src/backend/polaris/kernelone/llm/engine/executor.py:1045`。\n- **[S21] Anthropic 原生 system/messages 转换**：`src/backend/polaris/infrastructure/llm/providers/anthropic_provider.py:543`。\n- **[S22] Role-session gate 与 final-request API**：`src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:285`；`src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:946`；`src/backend/polaris/cells/context/engine/public/service.py:188`。\n- **[S23] 无 authority 的 ProjectOutcome 纯 reducer**：`src/backend/polaris/cells/runtime/projection/internal/project_outcome.py:15`。\n- **[S24] 多 owner ProjectOutcome authority binder**：`src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py:69`。\n- **[S25] TaskRuntime canonical child settlement**：`src/backend/polaris/cells/runtime/task_runtime/internal/service/_mixin_recovery_reexec.py:620`。\n- **[S26] EnhancedPipelineRunner 可选实现**：`src/backend/polaris/kernelone/context/context_os/pipeline/enhanced_runner.py:62`。\n- **[S27] Akashic 显式 integration 与 memory manager**：`src/backend/polaris/kernelone/akashic/integration.py:48`；`src/backend/polaris/kernelone/akashic/memory_manager.py:138`。\n- **[S28] director.runtime 的 repair schedule 与 coverage**：`src/backend/polaris/cells/director/runtime/public/service/_execution.py:825`。\n- **[S29] 有 durable owner action 的 router rework**：`src/backend/polaris/delivery/http/routers/_factory_handlers/mapping.py:129`；`src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_core.py:886`。\n- **[S30] 当前 ProjectOutcome 多 owner 汇总**：`src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:431`。\n- **[S31] Factory complete_run 的 operational 定义**：`src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1212`。\n- **[S32] Factory terminal 后 completion notify**：`src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_physical.py:382`。\n- **[S33] 资源 barrier 的 counts / closed / passed**：`src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:217`；`src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:726`。\n- **[S34] 全部历史 attempt 的只读 closure**：`src/backend/polaris/cells/control_plane/run_ledger/public/service.py:1429`。\n- **[S35] strict orphan resume 与 freeze/reset/release**：`src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1409`；`src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:66`。\n- **[S36] 最新 task facts 与当前 head 的 projection**：`src/backend/polaris/cells/runtime/task_runtime/internal/service/_mixin_task_rows.py:1510`。\n- **[S37] 严格 read 与整流 append 字节上限**：`src/backend/polaris/kernelone/events/sourcing/file_store.py:613`；`src/backend/polaris/kernelone/events/sourcing/file_store.py:399`。\n- **[S38] 捕获快照后的参数归一化**：`src/backend/polaris/kernelone/llm/toolkit/tool_normalization/__init__.py:410`。\n- **[S42] TaskBoard.transaction 的进程内锁范围**：`src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py:514`。\n\n## 14. 参考文献\n\n以下均为原始论文、作者原文或官方文档；它们支持相关方法与术语的讨论，不是 Polaris 的实验结果来源。\n\n1. Yao, S. 等. **ReAct: Synergizing Reasoning and Acting in Language Models**. arXiv:2210.03629，2022，v3 2023。[原始论文](https://arxiv.org/abs/2210.03629)。\n2. Yang, J. 等. **SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering**. arXiv:2405.15793，2024。[原始论文](https://arxiv.org/abs/2405.15793)。\n3. Shinn, N. 等. **Reflexion: Language Agents with Verbal Reinforcement Learning**. arXiv:2303.11366，2023。[原始论文](https://arxiv.org/abs/2303.11366)。\n4. NATS Authors. **JetStream**. 官方概念文档，访问日期 2026-10-14。[官方文档](https://docs.nats.io/concepts/jetstream)。\n5. Fowler, M. **Focusing on Events**. 作者工程架构草稿，2006。[作者原文](https://martinfowler.com/eaaDev/EventNarrative.html)。\n\n本论文与源码索引构成解释性材料。真实执行权限、效果、验收和释放仍由现有平台 owner 与契约决定；下载或打印本文不改变任何运行事实。\n",
      "filename": "polaris-system-paper.zh.md"
    },
    {
      "id": "transaction-turn",
      "title": "Polaris 事务回合协议：生成式决策、效果授权与持久提交",
      "subtitle": "机制专题 / 源码分析",
      "abstract": "以实际 TransactionTurnExecutor、TransactionKernel、DEO 与 OutcomeCommit 调用链为对象，分析角色执行如何将模型候选转换为受约束的工具批次，区分推测读取、持久 claim、一次消费 fence、效果收据、回合落盘与项目接受，并说明这些机制与数据库事务的差异。",
      "markdown": "# Polaris 事务回合协议：生成式决策、效果授权与持久提交\n\n## 摘要\n\n以实际 TransactionTurnExecutor、TransactionKernel、DEO 与 OutcomeCommit 调用链为对象，分析角色执行如何将模型候选转换为受约束的工具批次，区分推测读取、持久 claim、一次消费 fence、效果收据、回合落盘与项目接受，并说明这些机制与数据库事务的差异。\n\n关键词：Polaris；工程 Agent；状态拥有者；源码分析；执行证据\n\n研究日期：2026-10-14。方法为源码、调用链与封存失败记录分析。以下机制的实现存在性、默认接入和端到端可靠性分别讨论。行号以本次工作区为准，源文件指纹随研究证据包提供。\n\n## 1. 核心论点与命名\n\nPolaris 将不确定的模型输出放入确定性的执行协议：先确定角色、任务与执行身份，再把模型产生的调用转成可验证的工具批次；具有副作用的成员需通过范围授权、意图登记和即时执行许可，执行后的效果收据与整轮结果分别落盘。其主要价值是让失败能够归因到具体边界，并防止同一许可被重复消费。它不会替模型证明方案正确，也不会把任意文件、进程和外部服务变成一个数据库事务。\n\n真实源码名是 **TransactionTurnExecutor** 和 **TransactionKernel**。前者是角色内核的应用服务；后者位于 `roles.kernel` Cell，继承 `TurnTransactionController` 并委托执行。`TransactionTurn` 适合作为本文的概念名；`Transation` 不是这些符号的拼写。KernelOne 为该协议提供通用存储、锁、工具规范、模型适配等底座，不能把整个执行协议归为一个 KernelOne 数据库事务。源码依据：[T01]、[T02]、[T03]。\n\n还存在名为 `TaskBoard.transaction()` 的接口，其实现仅持有 `RLock` 并 `yield`。这保护同进程临界区，不代表任意异常可回滚全部文件与外部操作。[T40]\n\n## 2. 术语与四种不能混同的状态\n\n| 术语 | 具体含义 | 不能推导的结论 |\n|---|---|---|\n| Turn / 事务轮 | 一次角色内核调用、模型决策、工具批及收口 | 项目已完成 |\n| Invocation / Attempt | 稳定上游执行范围下的逻辑调用及确定性尝试身份 | 身份本身赋予写权限 |\n| Session | 原任务范围内连续调用多个 Turn 的会话 | 另一个工具执行权威或新的任务 |\n| TaskRuntime execution attempt | 当前任务执行会话、owner/claim/session 等约束绑定 | Factory 或 QA 已通过 |\n| ToolBatch | 解码后按效果与执行模式分组的工具调用 | 整批文件写入具有数据库原子性 |\n| DEO（Directed Effect Operation） | 对一项有副作用的操作登记意图、claim、收据与恢复状态 | 收据证明业务行为正确 |\n| Prepared inventory | 不可变的完整成员清单及授权快照 | 此时已经执行写入 |\n| Fence | 与 PID、原始对象身份、精确哈希绑定的一次消费许可 | 可跨进程重建或随意重放 |\n| Effect receipt | 真实操作结果及其身份、策略、状态哈希绑定 | 质量验收 verdict |\n| TurnOutcome / SealedTurn | 已持久记录的整轮结果及引用 | 任务或项目验收成功 |\n| Settlement | 任务/工具/副作用义务已终止，具备释放资源的证据 | `passed=true` |\n| Verdict | 在当前合同、当前输入及 verifier/QA 证据下的验收判断 | 能代替真实执行收据 |\n\n四个状态层分别是：单轮 `TurnState`；跨轮 Session 的进展；TaskRuntime 的任务/执行尝试；Factory 与 Run Ledger 的项目接受/结算。它们应关联，但不能仅凭同名 `completed` 相互替代。[T09]、[T29]、[T31]、[T41]\n\n## 3. 真实路径：LLM 决策在哪里，确定性代码在哪里\n\n| 步骤 | 当前入口与工作 | LLM 参与 |\n|---|---|---|\n| Bind / Prepare context | `TransactionTurnExecutor` 验绑定 attempt，检查 DEO 装配，构建上下文与最终工具表 | 该步骤自身不决定业务动作 |\n| Freeze invocation facts | 创建 kernel 时重新验证当前 attempt，并固定该轮工具与预算事实 | 无 |\n| Decision | 请求 provider，解码为 `TurnDecision`，检查 native 调用是否丢失、delivery mode 是否允许写 | 模型产生候选回答/调用；解码与门禁由代码执行 |\n| Plan ToolBatch | 使用显式的 parallel readonly、serial readonly、serial writes、async 分组；检查 scope 和调用效果 | 模型提出调用；执行计划和效果分类不由文字声明授权 |\n| Prepare DEO | 授权每个成员，登记 parent，封存完整 inventory，再登记 operation | 无 |\n| JIT claim | 刷新当前 attempt，读严格最新 stream head，以 CAS 领取精确成员许可，捕获当前策略 | 无 |\n| Execute / receipt | 注册并消费 fence，重新验证策略，调用私有物理执行器，观察结果并提交效果收据 | 无 |\n| Finalize | NONE 返回工具结果；LOCAL 本地整理；LLM_ONCE 生成摘要，工具被禁用 | 仅 LLM_ONCE 再调用模型 |\n| OutcomeCommit | 构造 canonical outcome、落事件、得到 commit receipt 与 sealed turn | 无 |\n| Continue / verify / settle | 会话消费已提交轮结果；外层任务与 Factory 执行 verifier/QA、结算及释放 | 下一轮决策或语义 QA 可调用模型；状态和释放依据由代码投影 |\n\n源码顺序可直接定位 `TransactionTurnExecutor.execute_turn()`、`TurnTransactionController.execute()` 和 `_execute_turn()`。[T01]、[T05]、[T06]、[T07]。这里的 **Plan** 是执行计划的概念，不等于每轮必有一个独立 Planning Agent 或额外规划 LLM。Chief Engineer 的项目蓝图与这一轮工具批次的执行计划处于不同层。\n\n`Speculate → Plan → ToolBatch → DEO → OutcomeCommit → settlement` 可用于教学，但不是全部运行必须串行经历的固定状态序列。推测执行可与流式模型输出重叠；非流式核心不以一个独立 Speculate 状态开头；settlement 在轮协议外，读取已落事实。\n\n## 4. 单轮状态机与持久提交屏障\n\n下图实线来自 `TurnState` 的允许转换。简化展示主要成功路径；省略移交与部分 FAILED 边，但没有添加不存在的 COMMITTED 枚举。\n\n```mermaid\nstateDiagram-v2\n    [*] --> IDLE\n    IDLE --> CONTEXT_BUILT\n    CONTEXT_BUILT --> DECISION_REQUESTED\n    DECISION_REQUESTED --> DECISION_RECEIVED\n    DECISION_RECEIVED --> DECISION_DECODED\n    DECISION_DECODED --> FINAL_ANSWER_READY: 直接回答\n    FINAL_ANSWER_READY --> COMPLETED\n    DECISION_DECODED --> TOOL_BATCH_EXECUTING: 工具批\n    TOOL_BATCH_EXECUTING --> TOOL_BATCH_EXECUTED\n    TOOL_BATCH_EXECUTING --> DECISION_DECODED: 有限合同纠正\n    TOOL_BATCH_EXECUTED --> COMPLETED: NONE / LOCAL\n    TOOL_BATCH_EXECUTED --> FINALIZATION_REQUESTED: LLM_ONCE\n    FINALIZATION_REQUESTED --> FINALIZATION_RECEIVED\n    FINALIZATION_RECEIVED --> COMPLETED\n    DECISION_DECODED --> SUSPENDED: ASK_USER\n    DECISION_REQUESTED --> FAILED\n    TOOL_BATCH_EXECUTING --> FAILED\n    COMPLETED --> [*]\n    FAILED --> [*]\n    SUSPENDED --> [*]\n```\n\n`TOOL_BATCH_EXECUTED` 返回 `DECISION_REQUESTED`、回到 `CONTEXT_BUILT` 或再次解码的普通循环被明确禁止；finalization 发起工具执行也被禁止。有限合同纠正、独立后继轮或显式 workflow 是不同机制，不能把禁止同一轮无限续跑理解为禁止整个任务持续执行。[T09]\n\n**持久化位于状态机之外的提交屏障**：`execute()` 先 `await _execute_turn()`，再 `_commit_canonical_outcome()`，成功后把 `turn_outcome`、`commit_receipt` 和 `sealed_turn` 加入结果。因而内存 `COMPLETED` 不能证明 outcome 已写入，更不能证明项目通过。[T05]\n\n```mermaid\nflowchart LR\n    A[单轮状态机结束] --> B{需要 durable commit?}\n    B -->|否: 临时聊天| C[返回临时结果]\n    B -->|是: 原任务轮| D[append_fact_event 严格写入]\n    D -->|成功| E[CommitReceipt + SealedTurn]\n    D -->|失败| F[TurnOutcomeCommitError]\n    E --> G[原 Session 消费已提交结果]\n    G --> H[下一轮或 TaskBoundary / QA / settlement]\n```\n\n异常发生后，controller 可尝试提交 error outcome；若原异常就是提交错误，不会伪造成功 receipt。当前 task outcome 的 idempotency key 绑定 workspace token、run、task、turn 与 transition；sessionful 写入要求 fsync，sessionless 路径采用 buffered durability。[T05]、[T27]。因此“所有结果均已 fsync”也是过度概括。\n\n## 5. Speculation：提前读，写入仅提前准备\n\n`ENABLE_SPECULATIVE_EXECUTION` 优先于兼容变量 `KERNELONE_ENABLE_SPECULATIVE_EXECUTION`，默认值为 **enabled**；领养结果重算审计 `SPECULATION_AUDIT_ADOPTIONS` 默认关闭。必须区分“能力默认打开”和“此次路径实际执行、领养并经审计证明正确”。[T22]\n\n流式 shadow 能提前执行便宜的只读调用，并按工具、归一化参数及环境指纹登记结果。正式调用可 ADOPT 已结束结果、JOIN 在跑结果或 REPLAY 正式执行。写工具的 shadow 仅做只读 Prepare：当前实现用 `file_exists` 探测路径，私有 sentinel key 防止与模型真实 read 调用碰撞；它不检查所有语法，更不是文件系统写入。[T23]、[T24]\n\n正式执行前仍完成 DEO inventory；WRITE/ASYNC 不会因为旧工具名称表未识别就作为 READ 领养。写 Prepare 缺失或优化失败可回到同一个正式授权批次执行，不能绕过权限。启用 adoption audit 时，领养读结果会和权威重算比较；默认关闭时，没有每次双算保证。[T25]\n\n推测不会自动构成隔离性：提前读到的文件可能随后变化。系统通过参数/环境指纹、当前策略及目标状态哈希约束复用与写前基线，但这不等于为整个项目创建数据库级一致性快照。\n\n## 6. Prepare、Carry、Freeze、Claim、Commit、Abort、Recovery\n\n这些词在不同层有具体对象，不能只看名字相同就认为协议相同。\n\n**Prepare** 有两种：可选 shadow 的只读探测；正式 DEO 的成员授权与 inventory 准备。后者先验证 execution attempt，登记 parent registry，封存有序成员清单，再绑定成员策略、登记 operation 并确认 inventory readiness。完整清单在该批次任何正式 read、shadow adoption 或 effect 前准备。[T11]、[T12]、`_executor_execute.py:800`。\n\n**Carry / Freeze** 指把已捕获的 ToolSpec、normalized arguments、能力限制、policy snapshot、目标状态及成员 hash 通过类型化 DTO 传到下一层。Freeze 防止授权后再用不同 registry 或可变 metadata 偷换输入；它不冻结操作系统或 workspace，执行前仍要重新检查当前状态。调用 setup 与 batch member 的固定对象是两层事实。[T01]、[T03]、[T13]、[T34]、[T35]\n\n**Claim** 是即时授权边界：读取当前严格 operation，检查仍为 INTENT_COMMITTED 及成员身份；从当前 operation stream head 求下一 seq，再用 expected version/seq 执行 CAS。成功将意图转为 EFFECT_STARTED，并获得本次 grant。精确重复 claim 可观察已写状态，但不获得新的可执行 grant；角色层明确拒绝 `idempotent=True` 作为新派发权。[T13]、[T44]\n\n**Execute / effect Commit**：claim context 在进程内 fence 注册；adapter 验 scope、参数、当前 policy、目标状态；一次消费 fence 后才能调用物理工具。写入成功需收据，修复绑定还需正确 after state；物理操作完成而 receipt commit 失败会进入 recovery，而不是直接报成功。[T14]、[T15]、[T16]\n\n**Abort** 只适用于未 claim 的 INTENT_COMMITTED；它证明没有按该许可开始操作，不能撤销已发生效果。失败批会终止未执行兄弟成员。提前封存的 rollback contingency 也作为有授权的独立操作，仅在已证明 forward 成功且需要补偿时执行；补偿失败不得宣称 restored。未知的已开始操作保留恢复义务。[T17]、[T19]\n\n**Recovery** 处理 EFFECT_STARTED 后的歧义、无法捕获当前策略、物理失败、结果观察失败或 receipt commit 失败。它追加状态证据，不能重造已失去的进程内 fence、删历史失败或无证据地再执行旧效果。[T13]、[T15]、[T19]\n\n**OutcomeCommit** 在整轮结束后封存轮结果，是另一项 append；它不是 effect receipt 的别名，也不是把此前所有 effect 和后继 QA 一次性提交。[T27]\n\nDEO 状态图严格来自运行时加载的 `.source` 文件；`_repository.py` 显式 UTF-8 读取并 compile/exec 该文件。引用 `.source` 是实际生产执行体，并非将历史文本文档误认活代码。[T20]、[T21]\n\n```mermaid\nstateDiagram-v2\n    [*] --> INTENT_COMMITTED: admit\n    INTENT_COMMITTED --> EFFECT_STARTED: claim + CAS\n    INTENT_COMMITTED --> ABORTED: 尚未开始\n    EFFECT_STARTED --> RECEIPT_COMMITTED: 真实效果收据\n    EFFECT_STARTED --> RECOVERY_PENDING: 不确定或失败\n    RECOVERY_PENDING --> RECEIPT_COMMITTED: 严格恢复收据\n    RECOVERY_PENDING --> DEAD_LETTER: 负结果闭合证明\n    RECEIPT_COMMITTED --> CLOSED_BY_PARENT: 父任务结算\n    ABORTED --> [*]\n    DEAD_LETTER --> [*]\n    CLOSED_BY_PARENT --> [*]\n```\n\n该图没有 EFFECT_STARTED 直接 ABORTED 的边，也没有 CLOSED_BY_PARENT 回到 INTENT_COMMITTED 的边。恢复后的新执行应属于合法新 attempt/新操作，并保留原 owner 与历史，不是修改旧终态。\n\n## 7. 工具归一化与权限的先后关系\n\n工具不由模型记住某一个内部拼写来获取权限。ToolSpecRegistry 是规范工具、aliases、arg_aliases、效果分类和 provider schema 的 owner。`capture_effective_spec(raw_name)` 取得一份一致、不可变的规范名称与别名快照；参数归一化随后只消费该快照，重新校验 hash，按 schema 别名映射和专用复杂转换生成 canonical arguments。[T34]、[T35]\n\n例如 `run_command` 与 `execute_command` 可以是同一工具的名称别名，但相同名称归一化成功不证明命令已获准。危险参数、跨 workspace 路径、未授权 command/cwd、冲突别名或未注册工具仍需拒绝。参数别名是同义适配，不是推断新的业务权限。\n\n有三类容易混同的 token：\n\n1. **LLM token / window budget**：上下文和输出容量、调用预算，不赋予操作权限。\n2. **JobToken / execution envelope capability**：从项目合同与 CE 交接/控制面派生，约束 allowed paths、write paths、commands、stage 和合同/蓝图 hash。读取完整已提交 JobToken 优先于根层的有损 envelope 投影。[T33]\n3. **Execution claim / fence**：精确 task attempt、parent、member、arguments hash、当前 policy、PID/对象身份的一次执行许可。[T13]、[T14]\n\nrun/task/turn/transition ID 是定位身份；哈希是绑定与一致性校验；授权还需可信 owner/当前 attempt/策略/能力作用域。普通 SHA digest 本身并不是针对恶意重写者的数字签名，不能把“哈希相同”独立解释为“任何来源都可信”。\n\n## 8. 与数据库 ACID 和两阶段提交的差别\n\n这是基于源码边界的概念对照，不是数据库基准实验。\n\n| 属性 | 当前协议的机制 | 实际限制 |\n|---|---|---|\n| Atomicity | 单 stream append 与 seq/idempotency 分配同临界区；单操作有 claim/receipt 状态 | 整轮多个文件、子进程和外部请求没有共同 rollback transaction |\n| Consistency | DTO、合同、scope、哈希、状态合法转换和 verifier 约束 | 平台合同一致不等于用户需求正确；仍需独立验收 |\n| Isolation | stream 锁、CAS、owner lease、写前 policy/目标状态检查，批内写串行 | 未证明全 workspace 可串行化；并发读和外部主体仍可改变环境 |\n| Durability | canonical event、严格 digest、按路径选择 flush/fsync、commit receipt | buffered 不是 fsync；物理效果与整轮 append 存在独立失败窗口 |\n| 2PC 类比 | prepare inventory 后 claim/execute，具备先登记后执行的思想 | 不是所有参与者 vote prepared、全局 commit 的数据库 2PC；工具没有统一资源管理器 |\n| Exactly once | durable intent、CAS、精确 idempotency、一次 fence、恢复不盲重放 | 防重复派发约束不等于所有文件/网络/进程效果全球恰好一次；崩溃窗口可产生 UNKNOWN |\n| Rollback | 已授权、可验证的补偿操作和 after hash 检查 | 补偿也是新副作用，可能失败；不能自动撤销任意远程请求 |\n\n`JsonlEventStore.append()` 的原子性范围是一个事件流的 sequence 分配、idempotency 查找和持久写入锁边界。[T37]、[T38]。典型歧义窗口是“物理工具已执行，receipt 尚未确认”以及“效果收据已确认，TurnOutcome 写入失败”。正确处理是保留原证据、只读确认并走现有恢复协议；不能凭失败异常推出效果未发生。\n\n## 9. Event sourcing、CAS、哈希及恢复的成本\n\nFactStream public API 解析 provenance、workspace/runtime identity、run/task，转发到 KernelOne JSONL event store。传入 expected_seq 时要求精确 CAS；重复 idempotency key 必须与原语义一致，expected_seq 不符也不能静默命中另一条事件。新严格事件包含 digest；普通 `.seq` cursor 不是权威。[T36]、[T37]、[T38]\n\n严格 read 验完整尾部、每帧原始 shape、digest、连续 seq、descriptor 身份，保留一个有界页；不因为尾部损坏就接受前页为已合格证据。其成本随历史字节与帧数增长。read 缓冲上界不等于整个历史只有一次常数成本；重复跨 attempt、跨 gate 冷扫描会累计。[T39]\n\n轮提交源码说明其转换与序列化近似 `O(e+p+n)`：`e` 为执行证据、`p` 为 payload、`n` 为 idempotency stream 扫描；空间 `O(e+p)`，此外严格 append 当前会加载并验证整个历史。DEO 每次 claim 读取最新共享 operation head，前序成员产生 receipt/abort 后不能用成员序号算 head。因此这一精确性会带来更多 I/O 与锁竞争。[T13]、[T27]、[T38]\n\n这些是源码级成本分析，不代表已有正式性能测量或吞吐保证。此前外部工程审计使用全 Python tracing 曾放大一次只读查询时长；该污染探针已停止并单独记录，不能当成平台扫描性能样本。\n\n## 10. 连续自主执行：循环属于 Session，接受属于项目\n\n当前正式连续路径由 `KernelSessionTurnBridge` 接现有 `RoleSessionOrchestrator`：bridge 每轮调用 canonical role kernel，不执行工具。已提交的 prior results 形成后继 history、当前质量反馈和 source evidence；物理 turn timeout 受原 TaskRuntime 会话 deadline 约束。预算型会话可没有单独固定 turn 上限，但仍有 deadline；无预算会话保留默认 ceiling，显式 max_auto_turns 也仍有效。[T29]、[T30]、[T31]\n\n失败轮只有在携带真实 commit receipt、批次结果和 follow-up 语义时才可作为可继续观察。聚合的 `batch_projection` 标记 `projection_only=true`，同时保留各轮 receipt；Session 摘要不产生新的效果权威。[T30]、[T31]\n\n外层 PM 合同、CE 蓝图、Director 执行、verifier/QA 与 Factory 结算负责项目推进。LLM 的“完成”文字、单轮 finalization、Task1 completed、入口进程 exit 0 和 QA 接受属于不同断言。工具结果或某个 phase 通过不能替代完整项目成功。\n\nRun Ledger settlement DTO 明确：`closed` 表示义务终止，失败也能闭合；`release_allowed=closed`；`passed` 另要求 projection ok、失败计数为零、required evidence 无 missing/failed。[T41]、[T42]。释放资源不能反向修改 QA FAIL。缺 required evidence 和真实 required evidence FAIL 也不可合并，前者是未产证据，后者是产物/验收失败。\n\n## 11. 设计意图、实际能力与已暴露的断点\n\n| 设计意图 | 本次源码确认的能力 | 未证明或已知限制 |\n|---|---|---|\n| 模型决策与效果权威分离 | 解码、scope、policy、DEO、receipt 均有明确代码 owner | prompt/body/tool schema 仍需真实最终请求审计；源码 whitelist 不能代替 wire body |\n| 安全连续交付 | 原 task/session 连续 turn、当前失败反馈、真实 verifier 后继 | 不代表所有项目都能收敛；模型可能持续只读，平台预算/存储也可能先失败 |\n| 不重复副作用 | idempotency/CAS、一次 fence、精确 claim 与 recovery | 未证明任意外部副作用 exactly once 或完整隔离 |\n| 失败可释放资源 | 严格 terminal dispatch/effect 证据闭合 | UNKNOWN 仍阻塞；资源闭合与验收成功分开 |\n| 无污染的产物验收 | 当前 clean Group 执行 CE exact recipes，历史产物不能代替当前输入 | 当前入口 FAIL；独立 QA 尚未完成，不能宣称 内部评测 通过 |\n| 持久结果推进下一轮 | outcome commit 和 source before/after 绑定 | 当前 stream 总字节 cap 与读页预算语义不同，已阻断正常 append |\n\n**已确认案例 F35（目前未实施修复）**：原 actual epoch12 的质量修复失败为 `failed to commit turn outcome: append_fact_event failed: strict event stream append exceeds the configured byte limit`。只读检查 canonical `roles.kernel.turn_outcomes.jsonl` 得 **8,354,716 bytes、47 帧**；当前默认 append cap 是 **8,388,608 bytes**，仅剩 **33,892 bytes**；末帧 **552,213 bytes**，最大帧 **1,184,096 bytes**。严格 read 以每帧/保留页预算校验，而严格 append 仍对整个文件 `len(lease.read_bytes()) + len(encoded)` 施加同一 cap。[T38]、[T39]\n\n此断点说明存储复杂度防护被当作总历史增长上限后，会破坏正常长期会话推进。本研究快照中该问题尚未修复，不能把私有 RED 或已定位根因写成产品已修。该 epoch 观测到多次 Director 语义调用和只读工具结果，沒有可证明的新写修复；这些计数也不等于已确认新 physical provider wire request。原先 55 个 physical calls 是历史基线。\n\n完整原项目验收当前仍不成立：clean ENV、BUILD、TYPECHECK、TEST 执行观测通过，exact ENTRY 路径缺失；随后质量修复 outcome commit 阻断。该入口应由正常 Director 修改已授权源码/config，再复验，不能由外部研究 Agent 修改生成目标。\n\n另一个静态风险只涉及清理用 settlement barrier 的 passed：该字段要求历史聚合失败计数为零，即使历史义务已资源闭合、当前 unresolved 为零，也可能持续为 false。当前 ProjectOutcome 的权威适配器不消费这个 passed，而是读取精确范围内的当前 owner 证明，因此不能从这一计数直接推出最终项目验收被历史失败阻断。[T42] 交付专题给出其不同 consumer 的源码依据。\n\n\n\n## 13. 可复核源码索引\n\n下列行号对应审计时实盘，不是正式接口稳定性承诺。完整 source SHA 与 before/after 对账见论文集的源码证据清单。\n\n- **[T01]** `TransactionTurnExecutor.execute_turn` ； cells/roles/kernel/internal/kernel/transaction_turn_executor.py:62–183（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_executor.py:62）\n- **[T02]** `TransactionKernel` ； cells/roles/kernel/internal/transaction_kernel.py:21–89（src/backend/polaris/cells/roles/kernel/internal/transaction_kernel.py:21）\n- **[T03]** `_resolve_directed_effect_composition/create_transaction_kernel` ； cells/roles/kernel/internal/kernel/transaction_factory.py:220–298（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:220）\n- **[T04]** `invocation/attempt identity binding` ； cells/roles/kernel/internal/kernel/transaction_turn_id.py:182–397（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_id.py:182）\n- **[T05]** `_commit_canonical_outcome/execute` ； cells/roles/kernel/internal/turn_transaction_controller.py:729–876（src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:729）\n- **[T06]** `_execute_turn` ； cells/roles/kernel/internal/turn_transaction_controller.py:1025–1214（src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:1025）\n- **[T07]** `run_decision_pipeline` ； cells/roles/kernel/internal/transaction/decision_pipeline.py:299–483（src/backend/polaris/cells/roles/kernel/internal/transaction/decision_pipeline.py:299）\n- **[T08]** `TransactionConfig` ； cells/roles/kernel/internal/transaction/ledger.py:47–139（src/backend/polaris/cells/roles/kernel/internal/transaction/ledger.py:47）\n- **[T09]** `TurnState and transition rules` ； cells/roles/kernel/internal/turn_state_machine.py:34–133（src/backend/polaris/cells/roles/kernel/internal/turn_state_machine.py:34）\n- **[T10]** `execute_tool_batch/read-write barrier` ； cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:225–385（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:225）\n- **[T11]** `_prepare_directed_effect_dispatch` ； cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_core.py:300–425（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_core.py:300）\n- **[T12]** `prepare_batch/inventory admission` ； cells/roles/kernel/internal/directed_effect_lifecycle.py:420–760（src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:420）\n- **[T13]** `claim_execution_context` ； cells/roles/kernel/internal/directed_effect_lifecycle.py:886–1127（src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:886）\n- **[T14]** `PID-bound fence register/consume` ； cells/roles/kernel/internal/directed_effect_dispatch.py:77–236（src/backend/polaris/cells/roles/kernel/internal/directed_effect_dispatch.py:77）\n- **[T15]** `policy revalidation/physical effect/receipt` ； cells/roles/adapters/internal/director/directed_effect_mutation_port.py:608–917（src/backend/polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py:608）\n- **[T16]** `_execute_directed_effect` ； cells/roles/kernel/internal/tool_batch_runtime.py:596–753（src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:596）\n- **[T17]** `execute_batch/contingency rollback` ； cells/roles/kernel/internal/tool_batch_runtime.py:755–970（src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:755）\n- **[T18]** `execution plan/parallel readonly` ； cells/roles/kernel/internal/tool_batch_runtime.py:1021–1109（src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:1021）\n- **[T19]** `operation transitions/reconciliation` ； cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:2345–2419（src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:2345）\n- **[T20]** `_legal_transition` ； cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:6707–6721（src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:6707）\n- **[T21]** `actual assembled source loader` ； cells/runtime/task_runtime/internal/directed_effect_operation/_repository.py:51–90（src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository.py:51）\n- **[T22]** `speculative/adoption audit defaults` ； cells/roles/kernel/internal/speculative_flags.py:14–41（src/backend/polaris/cells/roles/kernel/internal/speculative_flags.py:14）\n- **[T23]** `speculate_tool_call` ； cells/roles/kernel/internal/stream_shadow_engine.py:148–270（src/backend/polaris/cells/roles/kernel/internal/stream_shadow_engine.py:148）\n- **[T24]** `Prepare/Validate/Commit write semantics` ； cells/roles/kernel/internal/speculation/write_phases.py:23–130（src/backend/polaris/cells/roles/kernel/internal/speculation/write_phases.py:23）\n- **[T25]** `adopt/join/replay routing` ； cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:974–1045（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:974）\n- **[T26]** `LLM_ONCE advisory closing` ； cells/roles/kernel/internal/transaction/finalization.py:137–220（src/backend/polaris/cells/roles/kernel/internal/transaction/finalization.py:137）\n- **[T27]** `commit_turn_result` ； cells/roles/kernel/internal/transaction/outcome_commit.py:55–195（src/backend/polaris/cells/roles/kernel/internal/transaction/outcome_commit.py:55）\n- **[T28]** `to_turn_outcome` ； cells/roles/kernel/internal/transaction/ledger.py:435–497（src/backend/polaris/cells/roles/kernel/internal/transaction/ledger.py:435）\n- **[T29]** `KernelSessionTurnBridge and deadline` ； cells/roles/runtime/internal/kernel_session_turn.py:566–685（src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:566）\n- **[T30]** `canonical invocation/recoverable committed failures` ； cells/roles/runtime/internal/kernel_session_turn.py:687–750（src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:687）\n- **[T31]** `stream_kernel_session derived aggregate` ； cells/roles/runtime/internal/kernel_session_turn.py:829–905（src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:829）\n- **[T32]** `RoleSessionOrchestrator cross-turn loop` ； cells/roles/runtime/internal/session_orchestrator.py:711–875（src/backend/polaris/cells/roles/runtime/internal/session_orchestrator.py:711）\n- **[T33]** `derive_role_turn_capability_token` ； cells/roles/kernel/internal/kernel/tool_executor.py:241–345（src/backend/polaris/cells/roles/kernel/internal/kernel/tool_executor.py:241）\n- **[T34]** `capture_effective_spec` ； kernelone/tool_execution/tool_spec_registry.py:446–487（src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py:446）\n- **[T35]** `snapshot-bound normalization` ； kernelone/llm/toolkit/tool_normalization/__init__.py:410–494（src/backend/polaris/kernelone/llm/toolkit/tool_normalization/__init__.py:410）\n- **[T36]** `append_fact_event` ； cells/events/fact_stream/public/service.py:244–338（src/backend/polaris/cells/events/fact_stream/public/service.py:244）\n- **[T37]** `JsonlEventStore.append` ； kernelone/events/sourcing/file_store.py:231–307（src/backend/polaris/kernelone/events/sourcing/file_store.py:231）\n- **[T38]** `_append_locked CAS/idempotency/durability` ； kernelone/events/sourcing/file_store.py:613–709（src/backend/polaris/kernelone/events/sourcing/file_store.py:613）\n- **[T39]** `strict read integrity/bounded page` ； kernelone/events/sourcing/file_store.py:399–550（src/backend/polaris/kernelone/events/sourcing/file_store.py:399）\n- **[T40]** `TaskBoard.transaction lock` ； cells/runtime/task_runtime/internal/task_board.py:514–517（src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py:514）\n- **[T41]** `closure versus acceptance contract` ； cells/control_plane/run_ledger/public/settlement_barrier.py:76–105（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:76）\n- **[T42]** `closed/passed/release_allowed projection` ； cells/control_plane/run_ledger/public/settlement_barrier.py:793–855（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:793）\n- **[T43]** `strict terminal failure dispatch closure` ； cells/control_plane/run_ledger/public/settlement_barrier.py:529–708（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:529）\n- **[T44]** `public DEO claim/abort/receipt authority` ； cells/runtime/task_runtime/public/service.py:217–250（src/backend/polaris/cells/runtime/task_runtime/public/service.py:217）\n- **[T45]** `production TransactionConfig mapping` ； cells/roles/kernel/internal/kernel/transaction_factory.py:742–785（src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:742）\n",
      "filename": "polaris-transaction-turn.zh.md"
    },
    {
      "id": "contextos",
      "title": "Polaris ContextOS：状态优先的上下文编译、预算与请求证据",
      "subtitle": "机制专题 / 源码分析",
      "abstract": "从 TruthLog、WorkingState、StateFirst 七阶段投影、RoleSignalPlane、内容引用及预算出发，逐层分析模型输入的生成与观测。区分默认链路、可选记忆组件、规范请求与最终 wire，说明信息损失、快照保留、并发验证和接入覆盖的实际限制。",
      "markdown": "# Polaris ContextOS：状态优先的上下文编译、预算与请求证据\n\n## 摘要\n\n从 TruthLog、WorkingState、StateFirst 七阶段投影、RoleSignalPlane、内容引用及预算出发，逐层分析模型输入的生成与观测。区分默认链路、可选记忆组件、规范请求与最终 wire，说明信息损失、快照保留、并发验证和接入覆盖的实际限制。\n\n关键词：Polaris；工程 Agent；状态拥有者；源码分析；执行证据\n\n研究日期：2026-10-14。方法为源码、调用链与封存失败记录分析。以下机制的实现存在性、默认接入和端到端可靠性分别讨论。行号以本次工作区为准，源文件指纹随研究证据包提供。\n\n## 1. 核心定位：有约束的上下文编译与观测\n\nContextOS 将原始会话、结构状态、资产、蓝图和工具结果转换成某个角色当前可使用的模型输入。其技术重点是**状态提取、选择、引用、预算、角色隔离、审计和回放**。它不是另一个操作系统，也没有仅凭名称就存在的独立“意识引擎”。执行权、任务状态、真实副作用与交付判定仍由各自的 owner、TaskRuntime、DEO 和 Run Ledger 管理；上下文中的一句“已完成”不能替代它们。\n\n默认角色路径确实构造 `StateFirstContextOS`，后者构造普通七阶段 `PipelineRunner`，并组合 TruthLogService、WorkingStateManager、ReceiptStore、ProjectionEngine。RoleSignalPlane 位于网关的 supplemental turns 装配点。E12 · gateway.py:130（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:130） E03 · engine.py:491（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:491） E05 · runner.py:97（src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:97） E14 · projection_dict_builder.py:115（src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115）\n\n```mermaid\nflowchart TD\n  U[当前用户目标 / 角色输入] --> R[ContextRequest + 当前角色 Profile]\n  H[Session turn_events / 持久化 ContextOS 状态] --> S[StateFirst 七阶段投影]\n  R --> S\n  S --> W[结构状态 + 活动窗口 + artifacts / episodes]\n  W --> P[ProjectionEngine 排序 / 内容引用]\n  A[当前 PM 合同 / CE 蓝图 / 任务历史 / repo 身份] --> G[RoleSignalRegistry 只读信号]\n  G --> P\n  P --> B[RoleContextGateway 角色预算 / 压缩]\n  U --> B\n  B --> F[RequestPreparer 身份 / 工具面 / 必需数据重新绑定]\n  F --> N[Engine 模型窗口 / structured messages / 输出余量]\n  N --> X[Provider 协议转换：system / messages / tools]\n  X --> T[实际传输]\n  F --> O1[装配投影快照]\n  N --> O2[规范请求快照]\n  X --> Q{是否有治理型 physical dispatch port}\n  Q -->|Factory 绑定| O3[原生 endpoint + body + wire hash + audit pin]\n  Q -->|显式 role session gate| OS[冻结 semantic request + physical wire + session 生命周期]\n  Q -->|无| T\n  T --> E[LLM / 工具 / 当前 owner 结果]\n  E --> H\n  E --> FB[投影 outcome 反馈 / 失败证据]\n  FB --> P\n  O1 --> UI[ContextOS UI 只读展示]\n  O2 --> UI\n  O3 --> UI\n```\n\n图中箭头表示数据流，不授予执行权限。会话状态和最终请求审计是不同持久化对象，不能混用 hash。E20 · request_preparer.py:1007（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007） E22 · executor.py:630（src/backend/polaris/kernelone/llm/engine/executor.py:630） E25 · factory_dispatch_propagation.py:390（src/backend/polaris/cells/roles/kernel/internal/llm_caller/factory_dispatch_propagation.py:390）\n\n## 2. 四个底层模块分别保存什么\n\n### TruthLog：会话上下文的原始记录与活动视图\n\n**输入**是规范化会话条目。`append` 深拷贝后同时加入 `_journal_entries` 与 `_entries`；`replace_projection_view` 仅更新活动投影视图，保留 append journal。**变换**包括可选语义/关键词索引与按角色、类型、时间搜索。**持久化**必须谨慎理解：这个服务自身的两个列表在内存中，“journal”注释不表示它已成为平台磁盘 FactStream。**输出**是深拷贝 replay、journal 与搜索结果。**不变量**是投影替换不能覆写 journal、外部对象不能反向改内部数据。**失效边界**是进程内记录的生命周期、可选索引失败/队列满，以及是否由调用者实际 append。E01 · truth_log_service.py:360（src/backend/polaris/kernelone/context/truth_log_service.py:360） E60 · truth_log_service.py:99（src/backend/polaris/kernelone/context/truth_log_service.py:99）\n\n另有真正磁盘写入的 `TurnTruthLogRecorder`：事务控制器根据 workspace 创建 `events/kernel.turn.truthlog.events.jsonl`，队列 writer 以 UTF-8 append；flush/shutdown 才显式 `fsync`。上游 recorder 初始化与记录调用是 best-effort，失败写 warning 后不必然中断角色执行。这是 turn 观察日志，也不能替代严格效果提交事实。E33 · correlation.py:111（src/backend/polaris/cells/roles/kernel/internal/transaction/correlation.py:111） E34 · truthlog_recorder.py:159（src/backend/polaris/cells/roles/kernel/internal/transaction/truthlog_recorder.py:159）\n\n### WorkingState：结构化理解，不是执行状态机\n\n**输入**为 transcript 和 domain adapter 的 state hints。**变换**提取 current goal、accepted plan、open loops、blocked-on、deliverables、偏好、事实、实体与决定，保存 source turns/confidence。**持久化**首先是 WorkingStateManager 内存状态；跨会话由 SessionContinuity 返回的 persisted context config 和上层会话保存流程承担。**输出**是结构状态、Run Card/头尾锚点及活动窗口的 pinning 来源。**不变量**是读写深拷贝、结构字段与来源可回查。**失效边界**是领域提取规则、字段上限、重建/序列化遗漏；结构中的“completed”仍只是上下文理解。E02 · working_state_manager.py:1（src/backend/polaris/kernelone/context/working_state_manager.py:1） E06 · stages.py:645（src/backend/polaris/kernelone/context/context_os/pipeline/stages.py:645） E32 · session_continuity.py:493（src/backend/polaris/kernelone/context/session_continuity.py:493）\n\nUI 的 **WorkingMem** 是活动上下文窗口的展示名称。默认链路的实际窗口由 WindowCollector 选择，结构状态由 WorkingStateManager 管理。不能把 UI 卡上的 `WorkingMemoryWindow` 名称当作 Akashic 工作记忆已经被实例化的证明。另一个 `WorkingSetAssembler` 是仓库地图、符号、代码切片的有预算容器；本次全后端静态引用检查仅发现其实现、导出与示例，未发现默认角色运行构造点。E07 · stages.py:957（src/backend/polaris/kernelone/context/context_os/pipeline/stages.py:957） E48 · _model.ts:780（src/frontend/src/app/components/contextos/contextOSData/_model.ts:780） E51 · working_set.py:106（src/backend/polaris/kernelone/context/working_set.py:106）\n\n### ReceiptStore：大文本内容引用\n\n**输入**为搜索输出、文件切片、diff、工具结果或大信号。**变换**超过字符阈值后写 ContentStore，模型消息改成摘要/占位符及 receipt id。**持久化**默认为进程内 ContentStore 与 receipt-id 索引；可 export/import 内容，并由 offload hook 镜像到 workspace 命名空间的 OriginalPayloadCache。**输出**为可取回的原文引用。**不变量**是内容 hash/dedup、相同引用取回相同内容、工作区隔离。**失效边界**是容量回收、进程重启、best-effort 镜像失败、工具未提供取回能力。引用存在不意味着模型已阅读原文。E10 · receipt_store.py:16（src/backend/polaris/kernelone/context/receipt_store.py:16） E11 · content_store.py:58（src/backend/polaris/kernelone/context/context_os/content_store.py:58） E31 · original_payload_cache.py:364（src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364）\n\n这里的“receipt”与 DEO 的不可变 effect receipt 是两个概念。ReceiptStore 的工具输出缓存可以保存失败信息，但不能凭字符串创建命令成功证明、写入授权或任务完成证明。\n\nContentStore 以 UTF-8 内容的 SHA-256 前 24 位作键，默认 500 项/50MB；先驱逐零引用，再按较低引用数与较旧访问时间驱逐。第二阶段可驱逐仍有引用的条目，因此引用计数不是永久存活承诺。OriginalPayloadCache 默认实例是进程内缓存，可选 SQLite 能力不能自动当作默认磁盘可靠性。E11 · content_store.py:58（src/backend/polaris/kernelone/context/context_os/content_store.py:58） E31 · original_payload_cache.py:364（src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364）\n\n### ProjectionEngine：模型可读视图\n\n**输入**为活动事件、头尾锚点、Run Card、补充信号、当前目标。**变换**去控制面噪声，按 patch/summarize/archive/clear、confidence、recency、dialog act、role priority 排序，工具调用因果组内部保持序号顺序；大结果引用化。**持久化**排序权重/outcome 使用模块级进程内桶；模型消息本身只是输出投影。**输出**为 system/user/assistant/tool messages。**不变量**是投影不修改 TruthLog/WorkingState，用户和 assistant 原话保留，模型 proposal 作为非权威引用处理。**失效边界**是排序、删选及引用造成的信息不可见，以及质量反馈只证明本地启发式效果。E08 · projection_engine.py:375（src/backend/polaris/kernelone/context/projection_engine.py:375） E09 · projection_engine.py:551（src/backend/polaris/kernelone/context/projection_engine.py:551）\n\n自适应排序当前默认开启，可由 `ENABLE_PROJECTION_ADAPTIVE_ORDERING` 关闭。每个 learning key 保留最近 100 个 outcome，至少 5 个后调整权重；它是规则权重反馈，不是在线训练模型权重，也不等于已验证强化学习系统。网关 key 主要为 role id，状态进程内共享且不落磁盘。E58 · projection_engine.py:202（src/backend/polaris/kernelone/context/projection_engine.py:202） E09 · projection_engine.py:551（src/backend/polaris/kernelone/context/projection_engine.py:551）\n\n## 3. StateFirst 七阶段如何把历史变成当前状态\n\n| 阶段 | 真实输入与变换 | 输出与约束 |\n|---|---|---|\n| TranscriptMerger | 当前消息 + 已有 snapshot；以 event id 合并，增加 sequence，提取 native tool-call 元数据 | typed transcript；保留工具调用与结果的可追踪关联 |\n| Canonicalizer | transcript + pending follow-up + 旧 artifacts；dialog-act 与领域路由 | patch / summarize / archive / clear、artifact、follow-up；不是执行 verdict |\n| StatePatcher | 非 clear 事件的 domain hints | 带来源/confidence 的结构状态；规则提取质量限制理解能力 |\n| BudgetPlanner | model window + 当前 transcript/artifacts + reserve policy | input/retrieval/output/tool/safety budgets、阈值与 validation error |\n| WindowCollector | 结构状态根、最近消息、reopened/artifact roots、预算 | pinned/recent 活动事件与排除原因；有界窗口，必要时截短根内容 |\n| EpisodeSealer | 退出活动窗口的事件 + open loops | episode cards；pending 与 seal guards 控制封存 |\n| ArtifactSelector | 状态、artifacts、episodes、预算 | stubs、episode cards、head/tail anchor、Run Card、slice plan |\n\n普通 runner 的顺序与 typed ProjectionReport 可回查。每个 stage 的异常可能触发 typed fallback，并记录 `ok=False/fallback_used/errors`；因此“成功返回 projection”不等于所有阶段执行成功。E05 · runner.py:97（src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:97） E50 · runner.py:265（src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:265）\n\nStateFirst 的并发模式是 lock 下 snapshot、线程池 compute、lock 下 commit。源码注释称最后一步 CAS，但当前 `_validate_projection` 仅判 projection/snapshot 非空，没有比较源版本；而 compute 参数中的 engine snapshot 没进入 PipelineInput，后者依赖传入 `existing_snapshot`。报告不能把它描述为已实现强版本 CAS 或透明 replay 所有内存 journal。该问题是本次静态发现，未在原运行做并发反例，也未修改源码。E03 · engine.py:491（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:491） E04 · engine.py:694（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:694）\n\n另有 `ImmutableSnapshot/SnapshotStore` 写一次加 SHA 校验的实现，`_create_snapshot` 只保存输入 hash 与有限 projection summary。在当前非测试源码引用中没有找到该方法被 `project` 调用；默认每次 project 自动落此审计快照的说法不成立。真正会话状态保存与真正 LLM 请求快照分别走 SessionContinuity 与 Engine/role invoker。E49 · engine.py:245（src/backend/polaris/kernelone/context/context_os/runtime/engine.py:245） E59 · snapshot.py:94（src/backend/polaris/kernelone/context/context_os/snapshot.py:94） E32 · session_continuity.py:493（src/backend/polaris/kernelone/context/session_continuity.py:493） E57 · _helpers.py:566（src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker/_helpers.py:566）\n\n## 4. RoleSignalPlane：当前资产的只读补充管线\n\n**输入**是角色、任务、context_policy flags，以及 project structure/task history/CE overview/blueprint step/verdict/repo identity/ownership/resident 的只读 reader。**变换**是 registry 按角色与开关选 provider，priority 排序，读取 content/freshness，逐块分配预算。**持久化**只记 injected signal 的 freshness；默认有界 256-key LRU，key 来自 task id。**输出**仍是 `supplemental_turns`，并记录 sources、offloaded/dropped/skipped 列表。**不变量**是信号不修改源资产，模型只有投影，源 owner 仍唯一。**失效边界**是 provider 缺失返回 None、任务 key 复用、压缩后仍记“已注入”与模型最终看到版本的偏差。E14 · projection_dict_builder.py:115（src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115） E15 · role_signals.py:563（src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signals.py:563） E16 · role_signal_freshness.py:23（src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signal_freshness.py:23）\n\n默认每信号 4000 字符、总 12000 字符，再按角色实际预算约 5%/15% 缩放。压力下未变化的 nice-to-have 不再注入；must-have 可卸载成原文引用。这会减少重复 CE/历史信息，但“来源未变所以不重发”只在模型保留此前信息时有效，后续截断可能破坏该假设。E14 · projection_dict_builder.py:115（src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115） E15 · role_signals.py:563（src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signals.py:563）\n\nResident 能力信号来自配置注入的 provider；缺数据则不注入，roles.kernel 不反向拥有 resident 数据。能力面卡片表示可用接口与边界，不表示 Resident 已经运行过决策或自动改善模型。E53 · signal_sources.py:293（src/backend/polaris/cells/roles/kernel/internal/context_gateway/signal_sources.py:293）\n\n## 5. 预算分层，为什么 8k 与 1M 能同时成立\n\n1. **StateFirst selection budget**：实际模型窗口优先来自 ModelCatalog，再模型表，再调用者 fallback/默认 policy。普通 policy 输出 reserve 18%、工具 reserve 10%、安全 5%（均有下限）；另外分检索和活动窗口预算。这一阶段选择上下文。E56 · policies.py:36（src/backend/polaris/kernelone/context/context_os/policies.py:36）\n2. **RoleContextGateway enforcement**：`min(role_policy.max_context_tokens, model_window * 0.85)`，先扣角色 system，再保留当前用户目标；超过当前目标剩余预算直接报错，历史/信号可以压缩。角色预算可以是 8k，而模型配置窗口为 1M，两者限制不同。E12 · gateway.py:130（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:130） E13 · gateway.py:281（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:281）\n3. **RequestPreparer 必需数据再绑定**：当前 source/evidence 作为独立 user 数据消息加入，而不是变成巨大“当前指令”；检查 UTF-8 hash、重复路径、status/drift，再重新 pin sibling exports/write guidance/core role identity。它不凭数据赋予权限。E20 · request_preparer.py:1007（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007） E62 · request_preparer.py:437（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:437）\n4. **Engine 模型预算**：再计 native tools、消息包装和输出余量，对真实 structured `chat_messages` 再压缩，按最终窗口 clamp 输出。角色装配结束之后仍可能改变真正发送内容，因此前置 audit 不能自动当最终 wire。E22 · executor.py:630（src/backend/polaris/kernelone/llm/engine/executor.py:630）\n5. **Final request audit**：分别计 message/tool schema/response_format，合为最终输入估算和利用率，附 coverage/required refs。估算使用启发式，Gateway 的 CJK-aware 算法与审计字符算法不是同一个 tokenizer；这不是 provider 实计 usage。E19 · token_estimator.py:12（src/backend/polaris/cells/roles/kernel/internal/context_gateway/token_estimator.py:12） E24 · _builders.py:102（src/backend/polaris/cells/roles/kernel/internal/llm_caller/context_audit/_builders.py:102）\n\n实际已发生的失败说明分层必要：原 epoch10 把完整 source dossier 拼进 current instruction，触发 8k 指令 guard，QA 在 provider 前失败。后续 source-only修复将其放独立数据 slot，并确认来源/hash/CE 正文和 coverage；prepared-only 成功仍不等于 QA 真实调用通过。\n\nGateway CompressionEngine 当前 `apply_compression` 主分支直接做 smart truncation/emergency fallback；docstring 的“先语义摘要”不能取代实际控制流。StateFirst 路径使用 emergency 截历史、必要时截大的 system 数据平面，当前角色身份 system 在后面插入以免被裁。故无损保存、模型当前可见、预算内可发送是三件不同的事。E17 · compression_engine.py:58（src/backend/polaris/cells/roles/kernel/internal/context_gateway/compression_engine.py:58） E18 · compression_engine.py:428（src/backend/polaris/cells/roles/kernel/internal/context_gateway/compression_engine.py:428） E13 · gateway.py:281（src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:281）\n\n## 6. 工具面和身份怎样进入模型\n\nRole Profile 提供角色/模型/工具候选，运行态 ToolSpec/能力与当前合同形成 effective tool surface，transaction 可能提供 forced schema/choice。Preparer 格式化 native tools、response transport、温度/输出预算；最终确保当前目标、角色 system identity、当前 source/合同数据一致。Engine 和 provider 又做协议兼容转换。所有最终可调用工具必须与 runtime allowset 对账，角色 whitelist 不能替代实际 schema。E20 · request_preparer.py:1007（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007） E52 · tool_surface.py:146（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_surface.py:146）\n\n弱模型/legacy write-only/edit-only/slim 仍有显式分支，但受 legacy-tool-slices 与 committed continuation 条件控制；有 tool-filter audit 记录原因、原集合、裁后集合及与 prompt 必需工具的冲突。最终 request 若提供工具而 runtime 拒同一工具，属于面不一致；若 prompt 要求工具但 schema 没有，属于上下文与工具合同矛盾。模型输出经过 ToolSpec alias/argument normalization 后才进权限层，不能把自然字段差异直接归因模型能力。E52 · tool_surface.py:146（src/backend/polaris/cells/roles/kernel/internal/transaction/tool_surface.py:146）\n\nAnthropic provider 把角色消息转换成顶层 `system` 和原生 `messages`，再附 tools/options、overrides 与 sanitization；OpenAI Responses 使用 input，Chat 使用 messages。查看 `messages[0]` 并不足以审 Anthropic 真实 system，必须查原生 body 的 protocol。E27 · anthropic_provider.py:543（src/backend/polaris/infrastructure/llm/providers/anthropic_provider.py:543） E28 · service.py:188（src/backend/polaris/cells/context/engine/public/service.py:188）\n\n## 7. 三种请求快照必须分清\n\n| 证据级别 | 实际存储 | 能证明 | 不能证明 |\n|---|---|---|---|\n| 角色装配投影 | invoker 在 call_start 前保存 messages + provider_request/audit | 角色准备输入、来源与审计；可缺失并标 degraded | Engine 后续裁剪、provider 转换与真实 HTTP body |\n| Engine 规范请求 | `llm.provider_request_snapshot.v1`，post-compression messages + schema 摘要/choice/format/token audit | Engine 准备传给 provider 的规范内容 | 原生 endpoint/body 最终字节、headers/options 转换、实际发送成功 |\n| 原生 wire 资格快照 | `llm.final_physical_provider_request_context.v1`，exact redacted endpoint/body/transport/wire hash + route authority/pin | 发送边界冻结内容与资格绑定；原生 system/messages/tools | Provider 实际收到/处理完成、工具效果或产品交付成功 |\n\n上述 `llm.final_physical_provider_request_context.v1` 展示快照来自绑定的 Factory semantic propagation port：preparer 的 Factory candidate + signed binding 创建端口，Engine 将其带到 transport；没有端口时所审HTTP helper直接 raw send。**这不等于非Factory没有wire治理能力**。平台还实现了 `FinalProviderAttemptGate.for_role_session`：必须传显式 snapshot_store、session生命周期与drain协调器，保存 `llm.frozen_final_provider_attempt.v1` 的 canonical semantic request及physical_wire，记录terminal失败/确认；它不能冒称Factory scope。其当前wire-equivalence按messages/tools/choice/format逐键比对，区别于Factory provider-native协议资格。此次非测试引用检查未找到默认角色入口构造该session gate的调用点，因此结论限于具体传播/集成缺口，不能把潜在能力不存在当根因。E66 · final_provider_attempt_gate.py:285（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:285）、E67 · final_provider_attempt_gate.py:946（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:946）E21 · request_preparer.py:1453（src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1453） E22 · executor.py:630（src/backend/polaris/kernelone/llm/engine/executor.py:630） E23 · executor.py:1045（src/backend/polaris/kernelone/llm/engine/executor.py:1045） E25 · factory_dispatch_propagation.py:390（src/backend/polaris/cells/roles/kernel/internal/llm_caller/factory_dispatch_propagation.py:390） E26 · _http_pooling.py:270（src/backend/polaris/infrastructure/llm/providers/provider_helpers/_http_pooling.py:270） E57 · _helpers.py:566（src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker/_helpers.py:566）\n\n`context_snapshot_ref` 是真实落盘的 24 hex 内容地址键，位置为同 workspace runtime 的 `contexts/<shard>/<hash>`；并非 call id、request hash、prompt hash 或路径。`request_hash` 绑定请求身份，`final_physical_wire_hash` 绑定 wire 内容，而 `receipt://` 与 ContentRef 绑定文本资产；DEO效果收据另有op/owner/attempt/physical payload hash等绑定。文本引用与24hex快照即使长得像效果hash，也不能作效果提交/封闭证明；原样保留各契约字段，不从长度猜用途。E30 · context_store_retention.py:114（src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114） E31 · original_payload_cache.py:364（src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364）\n\n`/v2/context/{hash}/final-request` 可返回 available 的规范投影，也可返回实际 wire。API 会从真实物理 schema 中解析 Anthropic system/messages、Responses input；没有物理 schema则 `final_physical_request_body=null`。HTTP200 或“最终请求”标题不是第三层证明。E28 · service.py:188（src/backend/polaris/cells/context/engine/public/service.py:188）\n\n原 epoch12 的审计反例已封存：7 次新角色 start/end 与 7 个可读磁盘语义快照，11 个成功只读结果及 6 个 file_exists scope 拒绝；所有新快照缺 `final_physical_request.body` 与 wire hash。真实物理发送数量未知，不能写成 0，也不能把 7 份语义快照当 7 个 qualified wire。独立 QA 尚未执行；当前项目未交付。报告：`native18-epoch12-independent-qa-wire-gap.json`，SHA `6f19d7bab910450168470897ac06867c5c82e9ca64621f11fa7e4ca08160ca2c`。\n\n## 8. 记忆、RAG 与常驻能力：哪些真正接入\n\n| 名称 | 真实实现/接入分类 | 限制 |\n|---|---|---|\n| 默认 StateFirst | 角色 gateway 与会话连续性生产路径 | 规则 hints、有限窗口、fallback；不自动等于长期语义记忆 |\n| ProjectionEngine 自适应排序 | 默认开启，当前 turn outcome 有反馈调用 | 进程内启发式权重，不是模型训练；未证明跨重启或全工作区隔离 |\n| ContextOS MemoryManager | aggregate role 路径按触发/limit召回会话 snapshot | 当前是关键词 Jaccard + 英文规则冲突检查；不是 embedding RAG |\n| role_session provider gate | 显式snapshot store + scope/drain/lifecycle，保存物理wire | 平台能力存在；默认入口传播/捕获需单独证明，不可冒Factory资格 |\n| Akashic 四层 MemoryManager | Working/SemanticCache/Episodic/Semantic ports、promotion/demotion 与 sync 实现完整存在 | create/default/inject API未见默认角色主链调用；不能宣称所有调用有后台记忆维护 |\n| AkashicSemanticMemory | JSONL + 可选 embedding/LanceDB，embedding失败时关键词回退；TruthLogIndex可调用 | 依赖已配置 embedding服务；vector_search=True不证明可用、也不证明被模型消费 |\n| Knowledge Pipeline | 文档 extract/chunk/enrich/embed/idempotent store，CLI/显式消费组件 | 文档入库与当前模型检索/引用是两步；未证明默认角色每次自动RAG |\n| EnhancedPipelineRunner | 有 phase-aware/attention/graph/memory/predictive组合实现 | 默认StateFirst选择普通Runner；本次未找到生产实例化 |\n| phase/attention/predictive | aggregate role另有直接构建 pack的路径 | 局部提示/计划投影存在，不等于所有角色热路径预测压缩 |\n| IntelligentCompressor/crushers | greedy score/LLM summary/确定性内容类型压缩实现 | 本次未找到默认角色主链构造IntelligentCompressor；不能替代现CompressionEngine真实流程 |\n| WorkingSetAssembler | repo map/symbol/code slices、policy-gated预算组件 | 生产构造点未证，不等于UI WorkingMem |\n| Resident | workspace service、显式start/stop、tick生成insights/skills/proposals、RoleRuntime决策入口 | 默认active=False；tick是确定性证据生产者，不允许sidecar LLM，提案需治理与执行链 |\n| ContextOS UI | WebSocket runtime事件、context.build/LLM audit派生视图与按需读完整快照 | 估算窗口/调用数、provider-ready卡不能证明底层算法活跃或语义通过 |\n\nAkashic 与 ContextOS 两个不同的 `MemoryManager` 必须分别介绍。aggregate 实际 import的是 `context.context_os.memory.MemoryManager`；后者的候选从会话 manifest/snapshot读取、按当前事实做 conflicts，再决定是否注入。当前规则允许“possible conflict但高相关”注入，不能宣传完整逻辑一致性证明。E35 · integration.py:48（src/backend/polaris/kernelone/akashic/integration.py:48） E36 · memory_manager.py:138（src/backend/polaris/kernelone/akashic/memory_manager.py:138） E37 · semantic_memory.py:64（src/backend/polaris/kernelone/akashic/semantic_memory.py:64） E38 · manager.py:43（src/backend/polaris/kernelone/context/context_os/memory/manager.py:43） E39 · candidates.py:85（src/backend/polaris/kernelone/context/context_os/memory/candidates.py:85） E40 · candidates.py:220（src/backend/polaris/kernelone/context/context_os/memory/candidates.py:220） E41 · conflict_checker.py:66（src/backend/polaris/kernelone/context/context_os/memory/conflict_checker.py:66） E42 · _memory.py:153（src/backend/polaris/cells/roles/runtime/public/aggregate_chat/_memory.py:153） E43 · _memory.py:294（src/backend/polaris/cells/roles/runtime/public/aggregate_chat/_memory.py:294） E44 · enhanced_runner.py:62（src/backend/polaris/kernelone/context/context_os/pipeline/enhanced_runner.py:62）\n\n文档入库的真实流程是 DocumentInput，经 extractor registry、semantic chunker、importance/hash/tags、embedding，再写 IdempotentVectorStore；默认workspace runtime JSONL，可选LanceDB。输出的 memory_ids 是入库记录，不是当前角色已经召回的上下文，也不是 Provider 已看到的证据。无 fragments/chunks 直接 failed；源文件/行号/hash随记录保留。E63 · pipeline.py:311（src/backend/polaris/kernelone/akashic/knowledge_pipeline/pipeline.py:311）、E64 · pipeline.py:235（src/backend/polaris/kernelone/akashic/knowledge_pipeline/pipeline.py:235）\n\nAkashic CompressionDaemon 根据 WorkingMemory usage ratio触发压缩，调用 RoleContextCompressor，失败后可用确定性压缩/eviction并重写窗口；create_memory_manager内 enable_daemon=True只是该API默认，不证明默认Factory/角色运行调用过该API。它是可显式启用的后台组件，本文没有启动或查询原daemon。E65 · compression_daemon.py:234（src/backend/polaris/kernelone/akashic/compression_daemon.py:234）\n\n静态还有一个具体绑定风险：`context_os.memory.MemoryManager.workspace` 未传入默认 `_provider=MemoryCandidateProvider()`，后者默认workspace='.'；aggregate 构造 manager(workspace=...) 不自动改变 provider 的默认工作区。此处未做原namespace查询或调用反例，只列源码已见风险，不能以“workspace隔离完全可靠”宣传。E38 · manager.py:43（src/backend/polaris/kernelone/context/context_os/memory/manager.py:43） E39 · candidates.py:85（src/backend/polaris/kernelone/context/context_os/memory/candidates.py:85）\n\nResident 的持久化服务真实存在；默认inactive，tick只产 evidence/proposals，execution-impacting判断必须经独立 runtime contract gate。能力信号说明可调用哪些治理接口，不代表自动代码修改、在线训练或后台已常驻；当前是否start必须查实际运行状态，本文不查询或改变它。E45 · autonomy_boundary.py:8（src/backend/polaris/cells/resident/autonomy/internal/autonomy_boundary.py:8） E46 · resident_runtime_service.py:116（src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py:116） E47 · resident_runtime_service.py:464（src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py:464） E53 · signal_sources.py:293（src/backend/polaris/cells/roles/kernel/internal/context_gateway/signal_sources.py:293）\n\n## 9. 回放与保留：三个存储等级\n\n**会话连续性**保存 ContextOS transcript/artifacts/episodes/pending/working state 到上层 session context config，后续 rehydrate；这是重建当前状态的数据。**内容引用缓存**保存大文本，进程内容量/TTL可能让原引用失效。**LLM请求审计存储**按内容地址写 runtime/contexts，完整 wire pin 使用独占锁、workspace storage token、不可变写入和读后核对。E32 · session_continuity.py:493（src/backend/polaris/kernelone/context/session_continuity.py:493） E10 · receipt_store.py:16（src/backend/polaris/kernelone/context/receipt_store.py:16） E11 · content_store.py:58（src/backend/polaris/kernelone/context/context_os/content_store.py:58） E30 · context_store_retention.py:114（src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114）\n\n请求 retention 有 TTL、文件数和总字节上限，默认7天/500MB，pin repository决定是否可删除；过期不等于可删当前受保护资格证据。查询按当前storage runtime、同workspace Instance Registry runtime、旧system cache候选链找文件，旧根只读发现，不复制或活跃切根。E29 · snapshot_paths.py:24（src/backend/polaris/cells/context/engine/public/snapshot_paths.py:24） E30 · context_store_retention.py:114（src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114）\n\n上下文 replay 只能重建模型当时可见视图及来源；若副作用要重试，仍须 TaskRuntime/DEO 独立证据，不能 replay 缓存工具结果就再次认定命令执行。当前 epoch12 严格 append总字节限制阻 turn outcome提交，也说明工具/read发生、观察日志存在、严格提交完成是不同边界。\n\n## 10. 独特性与当前瓶颈\n\n有实装价值的组合是：领域状态优先而非盲堆历史；保留 source-turn根与工具因果组；当前资产 freshness减少重复；大文本引用化并能同workspace取回；角色策略预算与真实模型窗口分层；最终 raw provider body绑定资格/pin；UI只读事件投影。每一项都可以由当前代码确认。它们组合成可解释的上下文治理层，但不能据此推导“全自动交付已经完成”或“记忆系统无损理解一切”。\n\n当前主要瓶颈分别是：多层裁剪可能使已保存的内容不再可见；引用取回并非永久承诺；默认 StateFirst版本校验不足；部分长期记忆组件尚未进入默认链路；关键词/规则理解能力有限；角色key/taskkey的进程内缓存隔离范围较粗；Final-request API仍兼容只含规范投影的快照；内部修复的最终wire证据覆盖缺口已在真实epoch12出现。优先解决顺序需由后续工程授权决定，本次仅解释与记录，不启动修复。\n\n## 11. 证据与方法\n\n所有源码 discovery 先调用 CodeGraph；本次多个查询输出未覆盖目标模块，随后用 RTK UTF-8 精确范围读取补足。无 Transport closed、无伪造图结果。负面接入分类来自明确构造点/引用检查，是静态证据；不能证明动态插件永远不可能接入。没有新增测试、provider probe、原目标命令、公共冷查询或控制动作。当前保存各相关源码 SHA，外部报告不是产品事实源。\n",
      "filename": "polaris-contextos.zh.md"
    },
    {
      "id": "control-and-delivery",
      "title": "Polaris 控制与交付：多拥有者证明、验证与恢复",
      "subtitle": "机制专题 / 源码分析",
      "abstract": "以 PM、Chief Engineer、Director、QA、TaskRuntime、ExecutionBroker、Run Ledger 和 ProjectOutcome 的实际所有权为对象，解释合同、能力、执行、收据、验收、结算与恢复如何组合，并区分当前产物接受与全部历史资源闭合。",
      "markdown": "# Polaris 控制与交付：多拥有者证明、验证与恢复\n\n## 摘要\n\n以 PM、Chief Engineer、Director、QA、TaskRuntime、ExecutionBroker、Run Ledger 和 ProjectOutcome 的实际所有权为对象，解释合同、能力、执行、收据、验收、结算与恢复如何组合，并区分当前产物接受与全部历史资源闭合。\n\n关键词：Polaris；工程 Agent；状态拥有者；源码分析；执行证据\n\n研究日期：2026-10-14。方法为源码、调用链与封存失败记录分析。以下机制的实现存在性、默认接入和端到端可靠性分别讨论。行号以本次工作区为准，源文件指纹随研究证据包提供。\n\n## 1. 谁拥有哪一种真相\n\nGraph catalogue 规定 single state owner 与跨 Cell public contract 边界。目录位置不能替代 Graph 的权属定义；bootstrap 可以组装多个 public owner ports，但消费者不能因此直接接管上游状态。\n\n| 组件 | 拥有的状态/事实 | 提供的能力 | 不能单独证明 |\n| --- | --- | --- | --- |\n| PM / orchestration.pm_planning | 任务意图、验收约束、拆分 | 规划合同 | 实现已正确、命令已执行 |\n| Chief Engineer / chief_engineer.blueprint | 蓝图、接口、产物与命令义务、handoff | 校验完整合同、派生执行范围 | 目标当前字节已达标 |\n| Factory / factory.pipeline | run/stage、checkpoint、workspace owner claim、stage/lifecycle transaction | 组织完整角色链、恢复及终态清理 | Factory `completed` 不是 verified delivery |\n| runtime.task_runtime | numeric task、session/attempt、DEO parent/inventory/operation/receipt、执行事实 | 持久 claim、工具副作用授权与收据、同任务恢复；进程内一次消费 fence 由角色内核执行 | 一个 task completed 不代表项目 QA 与全部合同通过 |\n| events.fact_stream / KernelOne | 序号、完整性、幂等、append/query/CAS 的底座 | 保存、验证与重放 owner 发布的事实 | 不替 owner 判断业务结果 |\n| control_plane.run_ledger | 控制面事实的读模型及可重建 ledger 投影 | capability/effect/gate/TaskBoundary/evidence 聚合 | 某条 JSONL 行或 summary 不能替上游 owner 收据 |\n| control_plane.verifier_policy | 可用 verifier profile、proof semantics、workspace policy | 判断命令能否成为验收证据 | policy authorized 不是执行权或 PASS |\n| runtime.execution_broker | 真实进程、当前产物及 verifier 收据 | 受权启动、快照隔离、执行与终止、持久化 proof | 进程启动或 exit 0 不一定满足指定 proof |\n| director.runtime | repair catalog、coverage、planner/composer/scheduler、policy execution/revalidation | 在批准范围内实施并验证修复 | coverage match、plan 或 advisory 不是已修改/已通过 |\n| QA / qa.audit_verdict、roles.adapters | 按配置选择的独立判断与 QA owner 证据 | 审核当前实现及验收结果，提出 rework | 自测、准备好的 prompt、候选 verdict 不是 committed QA |\n| runtime.projection | 无上游事实写权限；有纯 reducer 与 owner observation/binding | 六轴 ProjectOutcome | caller 自填全绿不能得到 authoritative completed_verified |\n\n目录与 contract 的原始 Graph 证据已保存于 `control-analysis-baseline.json` 的 `cells`。当前大多数跨层查询通过 public DTO/port；少数兼容型 service/class 入口仍使调用面比 Graph 的精简 query inventory 更大，不能把目录迁移或 public 命名自动视为全部边界已证实。\n\n## 2. 正常执行链不是四个模型顺序聊天\n\n```mermaid\nflowchart LR\n    PM[PM 任务合同] --> CE[CE 蓝图与完整义务]\n    CE --> JT[派生 JobToken / execution authority]\n    JT --> D[Director 工具与修复]\n    D --> TR[TaskRuntime DEO / owner receipts]\n    CE --> VP[Verifier Policy]\n    VP --> EB[ExecutionBroker 当前输入快照与真实命令]\n    D --> EB\n    TR --> RL[Run Ledger / TaskBoundary]\n    EB --> RL\n    RL --> QA[独立 QA / committed verdict]\n    QA --> RW[有 authority 的同任务 rework]\n    RW --> D\n    QA --> PO[ProjectOutcome owner binding]\n    F[Factory 完整阶段链与终态事件] --> PO\n    RL --> PO\n    TR --> PO\n```\n\nPM 合同决定“做什么”。CE 明确“谁拥有哪些产物、如何验收、哪些上下文可读”，并产生 Director 可消费的 handoff。Director 必须在已验证 CE 范围内执行。正常执行允许复用已经验证的产物、完成任务或确定性修复，因此某阶段可以没有新 LLM 请求；这不等于跳过该角色的权属。若本次明确要求语义 QA，则必须选择实际 LLM QA 能力，不能用 physical verifier QA 模式替代。\n\nCE 的 `_control_plane_job_token` 不只拼路径：它从完整合同提取 required artifact owners、每任务 command authority hashes、批准的 read context；验证 run/task 身份、interface hash、target/scope 是否仍属于该 owner。token id 从这些事实派生。可读上下文与可写产物是两种权限，不能因模型需要阅读兄弟接口就扩大写范围。\n\n源码：CE token 派生（src/backend/polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py:200）、JobToken DTO（src/backend/polaris/cells/control_plane/run_ledger/public/job_token.py:10）。\n\n## 3. JobToken、claim、grant、receipt 是四件事\n\nJobToken 表达由控制面合同派生的 capability，不是新的合同事实源，也不是某个任务已成功的证据。执行时还需要当前 owner/session/attempt 和 lease。DEO claim 产生一次操作的 fresh grant，grant 约束允许执行的 effect，receipt 才描述实际发生的 effect 与结果。\n\n身份至少同时跨越 `workspace/project/factory_run/child_run/task/external_task/session/attempt/role/worker`，并绑定合同、蓝图、policy、目标与输入 hash。只保留 `task_id=1` 或只保留 `run_id` 都不足以重新获得权限。模型输出的“已写入”也不能替代真实 before/after hash、DEO member/parent 与 durable commit。\n\n命令有两个不同判断。KernelOne command capability 可以判断一个自然命令是否在允许集合内；该集合存在兼容型 prefix 与空集合语义。正式 verifier 则必须进一步绑定 CE 的 exact argv/cwd、obligation、输入闭包、可信可执行文件和 proof profile。不能从“工具层允许运行”直接推出“这次运行可计入验收”。\n\n源码：command capability（src/backend/polaris/kernelone/llm/toolkit/executor/command_capability.py:201）、正式 verifier authority（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1310）。\n\n## 4. 物理请求控制为什么需要独立的一层\n\n语义 request 与最终 wire body 不同。provider 适配器会转换 messages、tools、tool choice 等；只审准备阶段的 request 会漏掉最终工具被裁剪、角色串线、参数变更或预算差异。\n\n现有 `FinalProviderAttemptGate` 的正确顺序为：冻结最终 wire → 持久化并 pin snapshot → 获得 start permit → durable start receipt → commit started → 才允许 transport dispatch → cleanup → durable terminal receipt → settle。取消调用协程不能提前把仍运行的 blocking transport 标为结束；worker 保留 terminal settlement 的所有权。start persistence 含糊时也不能重新发一次请求碰碰运气。\n\nFactory coordinator 用 execution authority hash 绑定 grant/budget、角色、run、request freeze、wire hash 和 ordinal。reserve 既不表示请求已发送，也不表示可忽略 START_AMBIGUOUS；close/revoke/drain 是防止失败之后继续产生新请求的独立控制。\n\n当前 actual12 的七个语义 callback 与 call_end 是存在的，但它们的 ContextOS snapshot 缺 final wire body/hash。缺 snapshot 不能推断物理请求为零；它说明该路径尚未提供完整物理证明。`FinalProviderAttemptGate` 也具有 role_session 模式，因此不能未经调用链审计就断言“平台仅支持 Factory wire capture”。应核对各入口如何传播 execution authority 并安装最终 gate。\n\n源码：snapshot owner（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:99）、freeze/start/commit（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:591）、worker owns terminal（src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:546）、physical reserve（src/backend/polaris/cells/factory/pipeline/internal/factory_physical_attempt_coordinator.py:624）。\n\n## 5. 真实验证：许可、执行、证明与当前性分开\n\nVerifier Policy 只评估 proposal，不 mint execution authority。ExecutionBroker 从 bootstrap-bound owner port 解析 exact DTO，验证 workspace/project/run/contract/obligation、argv/cwd 与 executable identity；spawn 前重新解析 authority，若已变化则拒绝。随后冻结全部声明输入，绑定 input hash，启动受控进程并产出 owner 收据。\n\n“命令执行成功”仍可能只是 candidate observation。正式验收需要收据 seal、当前输入/authority、指定 proof 与稳定输出。长驻入口还可以按已声明 readiness profile 判断 ready 后受控终止；不能把它等同于任意进程存活，也不能把 CLI 缺失替换成宿主旧 dist 能跑。\n\nVerificationGroup 的用途是让 build→type/test→entry 在同一个干净候选上共享派生产物。开始时只复制批准的 authored inputs 和依赖，避免 stale dist。entry 不通过时前面的 exit 0 是实际进展，但未 seal 的整组并不是完整 authority proof。actual12 正是 ENV/build/type/test 为 0、exact ENTRY 为 1；它比旧 host npm start 更真实，也仍是失败。\n\n源码：policy proposal（src/backend/polaris/cells/control_plane/verifier_policy/public/service.py:856）、spawn 前再授权（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1367）、receipt proof（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1045）。\n\n## 6. 至少六种“完成”，不能压成一个绿色状态\n\n| 判断 | 它实际回答的问题 | 与全项目交付的关系 |\n| --- | --- | --- |\n| provider terminal | transport/worker 是否结束，terminal 是否持久化 | 无正确实现含义 |\n| effect receipt committed | 受权副作用是否真实、可追责地发生 | 有效果仍可编译/测试失败 |\n| TaskBoundary completed_verified | 当前任务声明的产物、依赖、证据、verifier 条件是否满足 | 范围来自当前任务边界；不能替全项目 QA |\n| TaskRuntime completed/settled | owner attempt 是否终态、还能否写；任务投影是否 converged | completed task 可以伴随项目 QA 未运行或失败 |\n| Factory completed | orchestration session/阶段链是否结束 | `complete_run` 明确写 verified=False |\n| ProjectOutcome completed_verified | 六轴当前 owner 证明是否全部满足且 authority_bound | 这是验收汇总结果；仍需将资源释放单独报告 |\n\nTaskBoundary 保留 missing/failed evidence 与 missing/failed verifier 两组字段。单个 `status` 按优先级取一个主要原因，因此 UI/诊断不能只读 status 丢掉其他并存失败。它还会用当前磁盘存在性修正 completed/pending artifact 列表；这属于读模型对齐，不是自动生成 authoritative effect receipt。强验收仍应消费 Broker/DEO 与当前合同证明。\n\n`complete_run` 的 success 参数只闭合 orchestration lifecycle，不升级已经 FAILED 的 run，不授权 verified。成功 QA 后通知项目 completion 要等 Factory completed 事件先 durable，避免在 Factory chain 尚未闭合时查询最终汇总器。\n\n源码：TaskBoundary（src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py:815）、Factory 完成定义（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1212）、post-terminal notify（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_physical.py:382）。\n\n## 7. operational closed 与 passed 的真正差异\n\n当前 Factory settlement barrier 的公式可直接从源码得到：\n\n```text\nclosed = 找到该 Factory 的事实\n         && open_lifecycle_count == 0\n         && (没有 missing required evidence || 失败情况下所有 dispatch 有严格闭合证明)\nrelease_allowed = closed\npassed = closed && run_projection.ok && failed_lifecycle_count == 0\n         && missing_required_modalities 为空 && failed_required_modalities 为空\n```\n\n所以失败 run 完全可以 `closed=true/release_allowed=true/passed=false`。这是安全清理的正确结果：失败的工具/命令确实终态，证明仍保留失败，不需要补成功收据才能释放工作区。反过来，active_task_count=0 也不充分：曾经 dispatch 的 effect 或 provider attempt 可能仍未闭合。\n\n当前 `_lifecycle_counts` 用最新 task facts 求 task 状态，却给 failed_count 加上历史 tool lifecycle failed_count。这个混合使 barrier.passed 可能长期保持 false，值得审计其名称、consumer 和 acceptance revision。**不能由此直接推出最终交付必然被历史失败毒化**：当前 ProjectOutcome adapter 并不读取这个 passed，而是读另一组 exact-scope ledger/current physical owner 结果。需要分别证明最终 consumer 是否使用历史失败计数，不能删旧失败、只扫描新 attempt、或伪造 pass。\n\n源码：counts（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:217）、barrier 公式（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:726）、最终 owner adapter 判据（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:782）。\n\n## 8. current acceptance 与 all-history resource closure\n\n完整历史有两个用途，必须分别投影。\n\n资源安全需要检查每个 stable execution identity。新 session 覆盖任务当前 row，不会抹掉旧 session 的 effects；全部旧 attempts 都需要 strict closure。RunLedger public query 明确构造每个 task/external/session/attempt/role/worker/run tuple 的 observer query。仅检查最新成功 attempt 会遗漏旧未完成副作用。\n\n验收当前版本则需要 CE 合同和当前 input/authority revision 的证明。旧失败必须留在历史中，不能继续代表当前候选结果。ProjectOutcome adapter 会将 Factory frozen terminal rows 与新 live repair rows 按 owner/seq 合并，剔除 removed live rows，再按 CE covered task ids 限定；它也可以用当前、全义务闭合的 physical diagnostics 证明任务已完成。这里必须保持 exact current owner bundle，不可凭“最后一条时间更晚”覆盖另一合同或项目。\n\n原终态清理的顺序为：physical attempts closure → child settlement → stage claim reconciliation → ledger release barrier → 保存 frozen TaskRuntime projection → 正式 reset(keep_plan=True) → lease CAS release。清理删除 live row/session 后还要从 immutable facts/frozen projection 找到原任务、CE 和 session；这就是 completed/failed/sparse-heartbeat/reset 历史组合为何在恢复中暴露过不同错误。\n\n源码：全部 attempts（src/backend/polaris/cells/control_plane/run_ledger/public/service.py:1497）、frozen/live merge（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:146）、freeze-before-reset-before-release（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:427）。\n\n## 9. Workspace lease、stage claim 与 lifecycle claim\n\n工作区独占 lease 防止多个 Factory owner 同时写同一 workspace。fencing_token 划分 authority epoch；过期只说明旧 owner 不再可申请新权限，不是旧物理资源已经关闭。stage claim 绑定一次 stage 执行，lifecycle operation claim 绑定 cancel/settle/recover/retry 等控制操作；nonce、sequence、fence 和 run 身份必须一起匹配。\n\n```mermaid\nstateDiagram-v2\n    [*] --> ACTIVE: 正式 admission / fresh fence\n    ACTIVE --> DRAINING: cancel / failed / complete closeout\n    DRAINING --> RELEASED: 严格物理与child/effect证明 + CAS\n    DRAINING --> DRAINING: proof unknown / conflicting owner\n    RELEASED --> ACTIVE: 正式 retry / fresh epoch\n```\n\n状态图只表示资源生命周期，省略业务 pending/running/failed/completed。每个控制操作也有自己的 claim/commit/rollback。两个操作都认为对方的 expired claim 不能动，就会产生 orphan deadlock；支持 exact terminal settle nonce 恢复的通用路径应同时保留 proof 重读与 single CAS winner，而不是清锁。\n\nrestart 后 process-local coordinator 不再存在，当前实现需要 reconstruct/replay 所有 immutable physical starts/terminals，并在受权 lease lock 下读两次 stable views/fence。空的新进程不能替旧请求闭合证明，旧 GET_run.metadata 也不是 fresh lease 的事实源。\n\n源码：exact orphan resume（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1409）、stale owner route（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1525）、restart replay（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:114）。\n\n## 10. Repair 与 recovery 的区别及收敛边界\n\nRepair 改善目标实现，recovery 恢复原控制权限与继续执行资格。ordinary compiler/test/entry failure 应产生 typed diagnostic，送回同一个 Director owner，在 CE 允许范围修复；hash/auth/symlink/contract drift 则应拒绝候选或阻塞，不能当 ordinary feedback 自动吞掉。\n\nrepair kernel 的规则、coverage、计划、composer、调度顺序和 cycle breaker 归 director.runtime。roles.adapters 的 bridge 只绑定 runtime 宣布的 step；Factory 不应私自加语言 regex 或另一份 scheduler。coverage_gap 只说明没有 deterministic rule，不代表普通 LLM repair 不允许，也不意味着 advisory 可以注册规则或写文件。\n\n保存候选与完成 owner 是两种 verdict。某轮修复让 TEST 通过但 ENTRY 暴露新问题，可以在其它已通过 gates 保持、scope/authority/cmd 不漂移的条件下保存增量，并继续同任务；不能把 accepted candidate 当 Task completed，也不能在全部 verifier 尚未完成时一律 rollback 有价值进度。现有 materialization/quality 共享 convergence 已处理这一语义，但 ordinary no-progress、平台存储失败和 provider failure 仍要分开。\n\nrouter 自动 rework 不能只把 FAILED 改成 RUNNING。`_stage_local_rework_is_authorized` 要求 Service 投影的 durable workflow owner action，包含 v2 deferred-drain、action id、diagnostic id、正确 decision owner；Service 的 strict stage transaction guard 与 physical replay fence 再防止 stale callback 覆盖当前 run。QA rework、Director local rework、官方用户 retry 是不同入口，应复用 owner authority 并保留 PMCE。\n\n源码：repair ownership（src/backend/polaris/cells/director/runtime/public/service/_execution.py:825）、router rework authority（src/backend/polaris/delivery/http/routers/_factory_handlers/mapping.py:129）、guarded mutation（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_core.py:971）。\n\n## 11. 最终 ProjectOutcome 是多 owner 绑定，纯 reducer 不是 authority\n\n纯 reducer 只计算 completion_candidate，始终 authority_bound=false/completed_verified=false。bootstrap-bound owner adapter 查询当前 CE completion contract、TaskRuntime、RunLedger、physical diagnostics；校验身份、projection hash 与 evidence refs。另一 public owner port 查询 Factory chain 和 durable completed 事件。authority binder 验证两组 exact DTO 与所有 axis hash/ref，才把候选升级为 completed_verified。\n\n六轴是 delivery、chain、QA、TaskBoundary、TaskRuntime、RunLedger。当前 adapter 还要求账本 available、ready、integrity_ok/outcome_ok/capability.ok、无有效 missing/failed、QA 与 TaskBoundary 通过。physical diagnostics 必须 authority_bound、覆盖全适用 obligation、无 missing/failed；它不能从文件存在或四个 exit 0 推出全绿。\n\n这个设计避免“caller success flag 就是交付”，也避免简单重新造一个万能 completion service。需要继续核对每个 producer 怎样给这些 owner 产生当前、可重读的事实。现有控制面最复杂的点是 fact gathering、恢复后的当前性与异步顺序，而不是纯 reducer。\n\n源码：纯 reducer（src/backend/polaris/cells/runtime/projection/internal/project_outcome.py:15）、owner authority binding（src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py:69）、当前 owner bundle（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:431）。\n\n## 12. 已证明的组合缺口与尚未证明的风险\n\n| 类别 | 证据强度 | 结论及影响 |\n| --- | --- | --- |\n| 长历史 strict append 硬上限 | 原生产 first-failure + 当前 root/source trace | turn_outcomes 原流 8,354,716bytes，8MiB 剩33,892，末条已提交帧为552,213bytes；后继 outcome 被拒，未提交 encoded 帧的精确大小未知；工具结果之后 outcome 无法 durable commit。严格读取分页已改不解决 append lifetime cap。不能关 strict、删历史或只调巨大 limit。F35 未实施。 |\n| FINAL wire capture 路径覆盖 | 实际七个语义快照缺 final body；callback 全end | observability/authority evidence gap 已存在；实际 transport 数/具体遗漏入口还应沿各调用链确认，不能报0或已qualify。 |\n| 层级 success 混用 | 原 epoch6 real receipt 与 settlement conflict；当前源码已分离 | 保存增量曾被当 owner完成，引发 completed 与负命令冲突。修复不能反向把失败验收降成成功。 |\n| reset→原任务恢复的 terminal-state 组合 | 原 actual9/11 fail + 原Task1 producer修复 | sparse辅助facts、removed successor、prevcompleted 路径曾不一致。当前148的组件与独审证据已通过，不代表完整原项目交付。 |\n| historical_failed 累加 | 当前静态源码 | barrier.passed 可能保留历史失败，但 finalProjectOutcome 不消费它；最终poison尚未完成因果证明。 |\n| 重复全历史读 | query源码声明 O(A*T)；原单次strict query156s、服务GET超时 | byte分页限制工作集，却仍多次验证历史；CPU/复杂度与事件循环/transport响应预算要分开优化。此次只读未重新跑冷扫描。 |\n| DTO/Graph/API多种兼容入口 | Graph与当前bootstrap/query源码 | 增量类型和旧service façade使矩阵难以穷尽。Graph精简query inventory没有完整展示当前authority查询，应更新发现/治理资产；不能仅按module名字证明所有调用合规。 |\n| 简略状态覆盖多原因 | TaskBoundary字段与优先级源码 | missing/failed均有字段，主status单值；只用它投影会丢掉并存的真实失败。 |\n\n证明包引用：`role-delivery-full-20261013/native18-actual-epoch12-quality-append-first-failure.json` SHA `00f7e4b7b94fbc385c2bdb816058d3d007f22ac255b212939231830bc1d5dd7c`；`native18-actual-epoch12-engineering-quiescence.json` SHA `c589fef374268b8d0a74b50160f1e65e2f5a63051e68d2c5a33afe27a3f18b25`。后者证明原进程停止，不把缺 wire 或工程 STOP 当 namespace release；原资源真实 DRAININGf28。\n\n## 13. 建议的收敛顺序（未实施）\n\n1. **先画清 authority/read-model/operational 三张边界图。** 复用已有 Cell 和 public contracts，给每个 producer→consumer 明确身份、hash、revision、单次效应、证据新鲜性、失败类型。不要另造第二事实源或把审计包写进平台账本。\n2. **将跨路径执行证明收敛到现有入口。** 主执行、Factory quality repair、QA rework、role session 的 final provider freeze/生命周期，工具→DEO→outcome_commit，exactCE→Broker→QA 都应消费同一类当前授权。先解决缺 wire 与 durable append 的已证组合瓶颈，再继续付费真实运行。\n3. **把资源闭合与当前验收投影显式分开。** 资源 closure 扫全历史；acceptance 使用真实合同/current input authority revision。失败历史保留。明示 barrier.passed 的适用范围，ProjectOutcome仍是现有多owner汇总器。\n4. **有界的是每次工作与单帧，不应意外成为合法项目的短寿命。** 用既有 FactStream 的 seq、完整性和 CAS 原理评估长流 append/query；分帧/分页/immutable-prefix方案必须仍验证全链和同一稳定EOF，不能以普通cache替strict authority。snapshot全量回灌的尺寸也应在 producer 原理层审计。\n5. **组合状态矩阵比增加单点flag更重要。** 覆盖 failed/completed/reset、restart coordinator absent、transport取消但worker仍运行、read-only outcome、negative command receipt、多次batch rollover、QA fail/rework、当前通过+旧失败、Append失败在effect前后等 producer真实场景；不要只stubsummary来证明闭环。\n6. **先组件与架构反证，最后一条真正项目链。** 若未来获准实现，源互斥STOP后相应focused/negative＋必要正式K1 gate，再真实fresh/current完整链。当前 CE 合同的全部 required 验收义务（本案例为五项）、已配置的独立 QA、owner-bound ProjectOutcome 和资源闭合才能称完整交付；API200、源码批准、host CLI0、可释放资源都不是替代品。\n\n本分析没有宣称实现已全部收敛，也没有展开任何源修复。机器可读事实、确定性结论/静态推断分级及 source refs 见 `control-evidence.json`；CodeGraph 原始回包保留在 `control-codegraph/`。\n",
      "filename": "polaris-control-and-delivery.zh.md"
    }
  ],
  "references": [
    {
      "id": "T01",
      "title": "TransactionTurnExecutor.execute_turn",
      "location": "src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_executor.py:62",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T02",
      "title": "TransactionKernel",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction_kernel.py:21",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T03",
      "title": "_resolve_directed_effect_composition/create_transaction_kernel",
      "location": "src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:220",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T04",
      "title": "invocation/attempt identity binding",
      "location": "src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_turn_id.py:182",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T05",
      "title": "_commit_canonical_outcome/execute",
      "location": "src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:729",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T06",
      "title": "_execute_turn",
      "location": "src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py:1025",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T07",
      "title": "run_decision_pipeline",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/decision_pipeline.py:299",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T08",
      "title": "TransactionConfig",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/ledger.py:47",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T09",
      "title": "TurnState and transition rules",
      "location": "src/backend/polaris/cells/roles/kernel/internal/turn_state_machine.py:34",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T10",
      "title": "execute_tool_batch/read-write barrier",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:225",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T11",
      "title": "_prepare_directed_effect_dispatch",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_core.py:300",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T12",
      "title": "prepare_batch/inventory admission",
      "location": "src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:420",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T13",
      "title": "claim_execution_context",
      "location": "src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py:886",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T14",
      "title": "PID-bound fence register/consume",
      "location": "src/backend/polaris/cells/roles/kernel/internal/directed_effect_dispatch.py:77",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T15",
      "title": "policy revalidation/physical effect/receipt",
      "location": "src/backend/polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py:608",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T16",
      "title": "_execute_directed_effect",
      "location": "src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:596",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T17",
      "title": "execute_batch/contingency rollback",
      "location": "src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:755",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T18",
      "title": "execution plan/parallel readonly",
      "location": "src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py:1021",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T19",
      "title": "operation transitions/reconciliation",
      "location": "src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:2345",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T20",
      "title": "_legal_transition",
      "location": "src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source:6707",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T21",
      "title": "actual assembled source loader",
      "location": "src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository.py:51",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T22",
      "title": "speculative/adoption audit defaults",
      "location": "src/backend/polaris/cells/roles/kernel/internal/speculative_flags.py:14",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T23",
      "title": "speculate_tool_call",
      "location": "src/backend/polaris/cells/roles/kernel/internal/stream_shadow_engine.py:148",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T24",
      "title": "Prepare/Validate/Commit write semantics",
      "location": "src/backend/polaris/cells/roles/kernel/internal/speculation/write_phases.py:23",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T25",
      "title": "adopt/join/replay routing",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor/_executor_execute.py:974",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T26",
      "title": "LLM_ONCE advisory closing",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/finalization.py:137",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T27",
      "title": "commit_turn_result",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/outcome_commit.py:55",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T28",
      "title": "to_turn_outcome",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/ledger.py:435",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T29",
      "title": "KernelSessionTurnBridge and deadline",
      "location": "src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:566",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T30",
      "title": "canonical invocation/recoverable committed failures",
      "location": "src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:687",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T31",
      "title": "stream_kernel_session derived aggregate",
      "location": "src/backend/polaris/cells/roles/runtime/internal/kernel_session_turn.py:829",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T32",
      "title": "RoleSessionOrchestrator cross-turn loop",
      "location": "src/backend/polaris/cells/roles/runtime/internal/session_orchestrator.py:711",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T33",
      "title": "derive_role_turn_capability_token",
      "location": "src/backend/polaris/cells/roles/kernel/internal/kernel/tool_executor.py:241",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T34",
      "title": "capture_effective_spec",
      "location": "src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py:446",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T35",
      "title": "snapshot-bound normalization",
      "location": "src/backend/polaris/kernelone/llm/toolkit/tool_normalization/__init__.py:410",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T36",
      "title": "append_fact_event",
      "location": "src/backend/polaris/cells/events/fact_stream/public/service.py:244",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T37",
      "title": "JsonlEventStore.append",
      "location": "src/backend/polaris/kernelone/events/sourcing/file_store.py:231",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T38",
      "title": "_append_locked CAS/idempotency/durability",
      "location": "src/backend/polaris/kernelone/events/sourcing/file_store.py:613",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T39",
      "title": "strict read integrity/bounded page",
      "location": "src/backend/polaris/kernelone/events/sourcing/file_store.py:399",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T40",
      "title": "TaskBoard.transaction lock",
      "location": "src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py:514",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T41",
      "title": "closure versus acceptance contract",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:76",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T42",
      "title": "closed/passed/release_allowed projection",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:793",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T43",
      "title": "strict terminal failure dispatch closure",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:529",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T44",
      "title": "public DEO claim/abort/receipt authority",
      "location": "src/backend/polaris/cells/runtime/task_runtime/public/service.py:217",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "T45",
      "title": "production TransactionConfig mapping",
      "location": "src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py:742",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E01",
      "title": "truth_log_service.py",
      "location": "src/backend/polaris/kernelone/context/truth_log_service.py:360",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E02",
      "title": "working_state_manager.py",
      "location": "src/backend/polaris/kernelone/context/working_state_manager.py:1",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E03",
      "title": "engine.py",
      "location": "src/backend/polaris/kernelone/context/context_os/runtime/engine.py:491",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E04",
      "title": "engine.py",
      "location": "src/backend/polaris/kernelone/context/context_os/runtime/engine.py:694",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E05",
      "title": "runner.py",
      "location": "src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:97",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E06",
      "title": "stages.py",
      "location": "src/backend/polaris/kernelone/context/context_os/pipeline/stages.py:645",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E07",
      "title": "stages.py",
      "location": "src/backend/polaris/kernelone/context/context_os/pipeline/stages.py:957",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E08",
      "title": "projection_engine.py",
      "location": "src/backend/polaris/kernelone/context/projection_engine.py:375",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E09",
      "title": "projection_engine.py",
      "location": "src/backend/polaris/kernelone/context/projection_engine.py:551",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E10",
      "title": "receipt_store.py",
      "location": "src/backend/polaris/kernelone/context/receipt_store.py:16",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E11",
      "title": "content_store.py",
      "location": "src/backend/polaris/kernelone/context/context_os/content_store.py:58",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E12",
      "title": "gateway.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:130",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E13",
      "title": "gateway.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py:281",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E14",
      "title": "projection_dict_builder.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/projection_dict_builder.py:115",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E15",
      "title": "role_signals.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signals.py:563",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E16",
      "title": "role_signal_freshness.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/role_signal_freshness.py:23",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E17",
      "title": "compression_engine.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/compression_engine.py:58",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E18",
      "title": "compression_engine.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/compression_engine.py:428",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E19",
      "title": "token_estimator.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/token_estimator.py:12",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E20",
      "title": "request_preparer.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1007",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E21",
      "title": "request_preparer.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:1453",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E22",
      "title": "executor.py",
      "location": "src/backend/polaris/kernelone/llm/engine/executor.py:630",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E23",
      "title": "executor.py",
      "location": "src/backend/polaris/kernelone/llm/engine/executor.py:1045",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E24",
      "title": "_builders.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/context_audit/_builders.py:102",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E25",
      "title": "factory_dispatch_propagation.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/factory_dispatch_propagation.py:390",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E26",
      "title": "_http_pooling.py",
      "location": "src/backend/polaris/infrastructure/llm/providers/provider_helpers/_http_pooling.py:270",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E27",
      "title": "anthropic_provider.py",
      "location": "src/backend/polaris/infrastructure/llm/providers/anthropic_provider.py:543",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E28",
      "title": "service.py",
      "location": "src/backend/polaris/cells/context/engine/public/service.py:188",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E29",
      "title": "snapshot_paths.py",
      "location": "src/backend/polaris/cells/context/engine/public/snapshot_paths.py:24",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E30",
      "title": "context_store_retention.py",
      "location": "src/backend/polaris/kernelone/llm/engine/context_store_retention.py:114",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E31",
      "title": "original_payload_cache.py",
      "location": "src/backend/polaris/kernelone/llm/toolkit/original_payload_cache.py:364",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E32",
      "title": "session_continuity.py",
      "location": "src/backend/polaris/kernelone/context/session_continuity.py:493",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E33",
      "title": "correlation.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/correlation.py:111",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E34",
      "title": "truthlog_recorder.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/truthlog_recorder.py:159",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E35",
      "title": "integration.py",
      "location": "src/backend/polaris/kernelone/akashic/integration.py:48",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E36",
      "title": "memory_manager.py",
      "location": "src/backend/polaris/kernelone/akashic/memory_manager.py:138",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E37",
      "title": "semantic_memory.py",
      "location": "src/backend/polaris/kernelone/akashic/semantic_memory.py:64",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E38",
      "title": "manager.py",
      "location": "src/backend/polaris/kernelone/context/context_os/memory/manager.py:43",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E39",
      "title": "candidates.py",
      "location": "src/backend/polaris/kernelone/context/context_os/memory/candidates.py:85",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E40",
      "title": "candidates.py",
      "location": "src/backend/polaris/kernelone/context/context_os/memory/candidates.py:220",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E41",
      "title": "conflict_checker.py",
      "location": "src/backend/polaris/kernelone/context/context_os/memory/conflict_checker.py:66",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E42",
      "title": "_memory.py",
      "location": "src/backend/polaris/cells/roles/runtime/public/aggregate_chat/_memory.py:153",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E43",
      "title": "_memory.py",
      "location": "src/backend/polaris/cells/roles/runtime/public/aggregate_chat/_memory.py:294",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E44",
      "title": "enhanced_runner.py",
      "location": "src/backend/polaris/kernelone/context/context_os/pipeline/enhanced_runner.py:62",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E45",
      "title": "autonomy_boundary.py",
      "location": "src/backend/polaris/cells/resident/autonomy/internal/autonomy_boundary.py:8",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E46",
      "title": "resident_runtime_service.py",
      "location": "src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py:116",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E47",
      "title": "resident_runtime_service.py",
      "location": "src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py:464",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E48",
      "title": "_model.ts",
      "location": "src/frontend/src/app/components/contextos/contextOSData/_model.ts:780",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E49",
      "title": "engine.py",
      "location": "src/backend/polaris/kernelone/context/context_os/runtime/engine.py:245",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E50",
      "title": "runner.py",
      "location": "src/backend/polaris/kernelone/context/context_os/pipeline/runner.py:265",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E51",
      "title": "working_set.py",
      "location": "src/backend/polaris/kernelone/context/working_set.py:106",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E52",
      "title": "tool_surface.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/tool_surface.py:146",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E53",
      "title": "signal_sources.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/context_gateway/signal_sources.py:293",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E54",
      "title": "search_gateway.py",
      "location": "src/backend/polaris/cells/context/engine/internal/search_gateway.py:81",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E55",
      "title": "intelligent_compressor.py",
      "location": "src/backend/polaris/kernelone/context/intelligent_compressor.py:492",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E56",
      "title": "policies.py",
      "location": "src/backend/polaris/kernelone/context/context_os/policies.py:36",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E57",
      "title": "_helpers.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker/_helpers.py:566",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E58",
      "title": "projection_engine.py",
      "location": "src/backend/polaris/kernelone/context/projection_engine.py:202",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E59",
      "title": "snapshot.py",
      "location": "src/backend/polaris/kernelone/context/context_os/snapshot.py:94",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E60",
      "title": "truth_log_service.py",
      "location": "src/backend/polaris/kernelone/context/truth_log_service.py:99",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E61",
      "title": "contextOSTelemetry.ts",
      "location": "src/frontend/src/app/components/contextos/contextOSTelemetry.ts:1",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E62",
      "title": "request_preparer.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py:437",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E63",
      "title": "pipeline.py",
      "location": "src/backend/polaris/kernelone/akashic/knowledge_pipeline/pipeline.py:311",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E64",
      "title": "pipeline.py",
      "location": "src/backend/polaris/kernelone/akashic/knowledge_pipeline/pipeline.py:235",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E65",
      "title": "compression_daemon.py",
      "location": "src/backend/polaris/kernelone/akashic/compression_daemon.py:234",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E66",
      "title": "final_provider_attempt_gate.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:285",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "E67",
      "title": "final_provider_attempt_gate.py",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:946",
      "note": "审閱时工作区源码定位；行号与哈希用于复核，不是验收证明。"
    },
    {
      "id": "C_CE_TOKEN",
      "title": "JobToken derived from validated completion/interface/owner scope",
      "location": "src/backend/polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py:200",
      "note": "JobToken derived from validated completion/interface/owner scope"
    },
    {
      "id": "C_JOB_TOKEN",
      "title": "DTO identity/scope is not standalone success evidence",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/job_token.py:10",
      "note": "DTO identity/scope is not standalone success evidence"
    },
    {
      "id": "C_COMMAND_CAPABILITY",
      "title": "Generic command allow semantics distinct from exact verifier authority",
      "location": "src/backend/polaris/kernelone/llm/toolkit/executor/command_capability.py:201",
      "note": "Generic command allow semantics distinct from exact verifier authority"
    },
    {
      "id": "C_PHYSICAL_RESERVE",
      "title": "Authority hash, role, run, budget, request freeze, wire hash, ordinal",
      "location": "src/backend/polaris/cells/factory/pipeline/internal/factory_physical_attempt_coordinator.py:624",
      "note": "Authority hash, role, run, budget, request freeze, wire hash, ordinal"
    },
    {
      "id": "C_WIRE_SNAPSHOT",
      "title": "Durable snapshot + audit pin; Factory or explicit role-session scope",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:99",
      "note": "Durable snapshot + audit pin; Factory or explicit role-session scope"
    },
    {
      "id": "C_START_TERMINAL",
      "title": "Worker-owned terminal; snapshot then permit then durable start then commit/dispatch",
      "location": "src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py:546",
      "note": "Worker-owned terminal; snapshot then permit then durable start then commit/dispatch"
    },
    {
      "id": "C_TASK_SETTLEMENT",
      "title": "Public child settlement query from authoritative projection/session conflicts",
      "location": "src/backend/polaris/cells/runtime/task_runtime/internal/service/_mixin_recovery_reexec.py:620",
      "note": "Public child settlement query from authoritative projection/session conflicts"
    },
    {
      "id": "C_TASK_FACT_PROJECTION",
      "title": "Canonical execution facts/current-head projection caches and full-history latest per task",
      "location": "src/backend/polaris/cells/runtime/task_runtime/internal/service/_mixin_task_rows.py:1510",
      "note": "Canonical execution facts/current-head projection caches and full-history latest per task"
    },
    {
      "id": "C_BROKER_AUTHORITY",
      "title": "Exact CE authority and spawn-time reread; sealed command/receipt",
      "location": "src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1310",
      "note": "Exact CE authority and spawn-time reread; sealed command/receipt"
    },
    {
      "id": "C_VERIFIER_PROOF",
      "title": "Output/proof/process identity bound receipt; policy/readiness semantics",
      "location": "src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1045",
      "note": "Output/proof/process identity bound receipt; policy/readiness semantics"
    },
    {
      "id": "C_VERIFIER_POLICY",
      "title": "Read-only proposal policy, no execution authority, executable/input closure identity",
      "location": "src/backend/polaris/cells/control_plane/verifier_policy/public/service.py:856",
      "note": "Read-only proposal policy, no execution authority, executable/input closure identity"
    },
    {
      "id": "C_REPAIR_OWNER",
      "title": "Runtime owns schedule, exact adapter runner set, convergence and coverage",
      "location": "src/backend/polaris/cells/director/runtime/public/service/_execution.py:825",
      "note": "Runtime owns schedule, exact adapter runner set, convergence and coverage"
    },
    {
      "id": "C_TASK_BOUNDARY",
      "title": "Explicit missing/failed obligations and typed local verdict",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py:815",
      "note": "Explicit missing/failed obligations and typed local verdict"
    },
    {
      "id": "C_BARRIER_COUNTS",
      "title": "Latest task state plus accumulated tool failed count",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:217",
      "note": "Latest task state plus accumulated tool failed count"
    },
    {
      "id": "C_BARRIER_FORMULA",
      "title": "release_allowed=closed, separate passed conditions",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:726",
      "note": "release_allowed=closed, separate passed conditions"
    },
    {
      "id": "C_BARRIER_OWNER_OBSERVATIONS",
      "title": "Exact Factory scope; all stable historical attempt effect closures",
      "location": "src/backend/polaris/cells/control_plane/run_ledger/public/service.py:1429",
      "note": "Exact Factory scope; all stable historical attempt effect closures"
    },
    {
      "id": "C_FACTORY_OPERATIONAL_COMPLETE",
      "title": "complete_run has no verified delivery authority, notify only after terminal persistence",
      "location": "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1212",
      "note": "complete_run has no verified delivery authority, notify only after terminal persistence"
    },
    {
      "id": "C_FACTORY_DRAIN_RESET_RELEASE",
      "title": "Replay physical closure; child/ledger; freeze terminal authority before reset/release CAS",
      "location": "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:66",
      "note": "Replay physical closure; child/ledger; freeze terminal authority before reset/release CAS"
    },
    {
      "id": "C_FACTORY_ORPHAN_RECOVER",
      "title": "Exact nonce/fence orphan recovery with strict proof and child quiescence",
      "location": "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1409",
      "note": "Exact nonce/fence orphan recovery with strict proof and child quiescence"
    },
    {
      "id": "C_ROUTER_AUTHORITY",
      "title": "Committed workflow owner action, not arbitrary failure/status flag",
      "location": "src/backend/polaris/delivery/http/routers/_factory_handlers/mapping.py:129",
      "note": "Committed workflow owner action, not arbitrary failure/status flag"
    },
    {
      "id": "C_ROUTER_GUARD",
      "title": "Strict current stage transaction and physical replay before guarded mutation",
      "location": "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_core.py:886",
      "note": "Strict current stage transaction and physical replay before guarded mutation"
    },
    {
      "id": "C_PROJECT_PURE",
      "title": "Pure completion_candidate remains authority_bound=False/completed_verified=False",
      "location": "src/backend/polaris/cells/runtime/projection/internal/project_outcome.py:15",
      "note": "Pure completion_candidate remains authority_bound=False/completed_verified=False"
    },
    {
      "id": "C_PROJECT_BIND",
      "title": "Exact owner DTO/identity/all axis hashes+refs before completed_verified",
      "location": "src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py:69",
      "note": "Exact owner DTO/identity/all axis hashes+refs before completed_verified"
    },
    {
      "id": "C_PROJECT_CURRENT_OWNER",
      "title": "Current completion contract and all-owner proof; does not read Factory barrier.passed",
      "location": "src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:431",
      "note": "Current completion contract and all-owner proof; does not read Factory barrier.passed"
    },
    {
      "id": "C_CURRENT_FROZEN_MERGE",
      "title": "Merge frozen terminal/live repair rows by owner and fact seq",
      "location": "src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:146",
      "note": "Merge frozen terminal/live repair rows by owner and fact seq"
    },
    {
      "id": "C_OUTCOME_COMMIT",
      "title": "Every role shared authoritative outcome stream strict append and commit receipt",
      "location": "src/backend/polaris/cells/roles/kernel/internal/transaction/outcome_commit.py:148",
      "note": "Every role shared authoritative outcome stream strict append and commit receipt"
    },
    {
      "id": "C_APPEND_CAP",
      "title": "Whole current stream plus legal new frame checked against configured byte cap",
      "location": "src/backend/polaris/kernelone/events/sourcing/file_store.py:698",
      "note": "Whole current stream plus legal new frame checked against configured byte cap"
    },
    {
      "id": "R01",
      "title": "ReAct: Synergizing Reasoning and Acting in Language Models",
      "location": "Yao et al., ICLR 2023",
      "note": "交替推理与环境动作的 Agent 方法；不提供 Polaris 的效果授权证明。",
      "url": "https://arxiv.org/abs/2210.03629"
    },
    {
      "id": "R02",
      "title": "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
      "location": "Yang et al., NeurIPS 2024",
      "note": "将可编辑、可导航、可测试的界面作为编码 Agent 的关键能力。",
      "url": "https://arxiv.org/abs/2405.15793"
    },
    {
      "id": "R03",
      "title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
      "location": "Shinn et al., NeurIPS 2023",
      "note": "语言反馈与记忆改善后继尝试；不同于持久执行事实与 authority。",
      "url": "https://arxiv.org/abs/2303.11366"
    },
    {
      "id": "R04",
      "title": "JetStream 官方交付语义",
      "location": "NATS Documentation",
      "note": "持久化消息与副作用幂等是不同层的保证。",
      "url": "https://docs.nats.io/concepts/jetstream"
    },
    {
      "id": "R05",
      "title": "Focusing on Events",
      "location": "Martin Fowler, 2005",
      "note": "事件导致状态变化及重建的基础思路。",
      "url": "https://martinfowler.com/eaaDev/EventNarrative.html"
    }
  ]
};
