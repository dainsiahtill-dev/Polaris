# KernelOne Agent Foundation / Work Strategy Implementation Master Plan

状态: Draft  
日期: 2026-03-25  
范围: `polaris/kernelone/context/`、`polaris/cells/roles/session/`、`polaris/cells/roles/runtime/`、`polaris/cells/roles/kernel/`、`polaris/cells/context/catalog/`、`polaris/cells/workspace/integrity/`

> 本文是主实施计划，不是 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`、各 Cell manifest 为准。  
> 本文用于把“KernelOne Agent Foundation / Work Strategy Framework”从方向判断落成可执行路线，明确先后顺序、交付切片、度量基线与止血债务收口顺序。
> 第一阶段优先强化共享 Agent 基础能力，不先做角色专属打磨。

---

## 1. 最终决策

这条线不再继续走“单一 hardcoded canonical policy”的路线。

最终决策是：

`KernelOne Agent Foundation / Work Strategy Framework + Phase 1 共享基础能力 + code domain first + replay/shadow/eval 基线`

强制裁决如下：

1. `KernelOne` 拥有上下文策略框架本身。
2. `roles.session` 只拥有原始 session/message/source-of-truth 与 session 级策略覆盖配置。
3. `RoleRuntimeService` 是唯一 canonical 运行时接入面。
4. `canonical_balanced` 是 Phase 1 默认 foundation profile，不是唯一 profile。
5. 第一阶段先打磨共享 Agent 基础能力，角色属性优化延后。
6. 所有策略演化都必须通过 receipts、benchmarks、scorecards 量化，而不是靠 prompt 经验主义。

---

## 2. 为什么这是更好的长期方案

如果现在只把 canonical 策略写死，短期能止血，但半年后仍会退化为：

1. 每次改策略都要改核心 runtime 代码。
2. 不同模型、不同工作负载、不同上下文窗口无法独立优化。
3. 无法严肃比较“这次改动到底更好还是更差”。
4. 新的止血逻辑会继续散落在 `roles.kernel`、host、delivery、context gateway 之间。

框架化以后，机制和策略分离：

1. 机制层稳定：contracts、registry、receipts、benchmark、cache、budget gate。
2. 策略层可进化：profile 可切换，可灰度，可回放评测。
3. canonical 行为仍然稳定：默认 profile 固定，不因为实验而污染主路径。

---

## 3. 北极星目标

目标不是“让某个角色更聪明一点”，而是让 Polaris 获得一个统一、可解释、可度量的 Agent 基础能力系统。

完成态应为：

`roles.session truth -> RoleRuntimeService resolve profile -> KernelOne strategy run -> continuity/exploration/history assembly -> LLM turn -> receipts/metrics/scorecard`

到达这个完成态后，系统应同时满足：

1. 角色不再各自拼探索和 continuity 逻辑。
2. 工具回执不会无限堆进 history。
3. 大文件不会默认整文件读取。
4. compaction 不会每轮触发，只在 near-limit 执行。
5. 不同策略可以对比，而且结果可复盘。
6. 未来 role/domain 扩展不需要推翻底座。

---

## 4. 架构边界

### 4.1 `KernelOne` 负责什么

`KernelOne` 负责以下“机制”能力：

1. strategy bundle contracts
2. strategy profile schema / registry / resolver
3. strategy run context
4. exploration orchestration
5. read escalation gating
6. history materialization
7. continuity projection consumption
8. budget gate
9. cache manager
10. run receipts / metrics / benchmark replay
11. domain adapter seam
12. role overlay seam

### 4.2 `roles.session` 负责什么

1. session row
2. message row
3. context_config source-of-truth
4. session-level strategy override persistence
5. 不拥有通用策略机制

### 4.3 `roles.runtime` / `RoleRuntimeService` 负责什么

1. 每轮解析 effective profile
2. 创建 strategy run
3. 把 receipts 挂到 runtime event / audit 侧
4. 保持 canonical 运行入口单一

### 4.4 `roles.kernel` 负责什么

1. 执行 turn loop
2. 消费组装后的上下文
3. 不再自行发明 exploration / continuity / compaction 策略

### 4.5 `context.catalog` / `workspace.integrity` 负责什么

1. repo map
2. symbol evidence
3. code slice / snippet / search candidate
4. 这些是 provider/input，不是策略拥有者

### 4.6 Phase split

本计划把演进拆成两个层级：

1. Phase 1
   - 共享 Agent 基础能力
   - code domain 首落地
   - 不做深度角色定制

2. Phase 2
   - role overlays
   - 非 code domain adapters
   - 针对角色属性的精调

### 4.7 Role-family clarification

后续角色扩展不应无节制地新增顶层角色。

推荐固定如下裁决：

1. 顶层治理角色继续保持：
   - `PM`
   - `Architect`
   - `ChiefEngineer`
   - `QA`

2. 执行层保持：
   - `Director` 作为执行母角色

3. 未来执行专精角色例如：
   - `Coder`
   - `Writer`
   - 其他创作/生产型执行者
   应优先实现为：
   - `Director` line overlays
   - 或 `Director` managed subagents

4. 不建议把 `Coder`、`Writer` 直接提升为新的顶层治理角色

原因：

1. `Director` 的本质不是“写代码的人”，而是“负责落地与交付的执行层”。
2. `Coder`、`Writer` 等只是执行能力的 specialization，不是新的治理层职责。
3. 这样可以避免角色系统无限膨胀，同时保留统一的 execution-family foundation。

---

## 5. 当前状态与主要 gap

截至 2026-03-25，仓内已经有正确方向的雏形：

1. `polaris/kernelone/context/exploration_policy.py`
2. `polaris/kernelone/context/budget_gate.py`
3. `polaris/kernelone/context/cache.py`
4. `polaris/kernelone/context/working_set.py`
5. `polaris/kernelone/context/session_continuity.py`

但仍缺失真正可长期演化的关键结构：

1. 没有正式 `strategy bundle` 合同。
2. 没有 `profile registry / resolver`。
3. 没有 `history materialization strategy` 的一等建模。
4. 没有统一的 `run receipt / metrics / scorecard`。
5. 没有 replay/shadow/A-B 的评测机制。
6. 缺少显式 domain adapter / role overlay seam。
7. `roles.session + RoleRuntimeService + KernelOne` 的接入边界仍未完全收口。
8. 仍有一批止血版 host/protocol/compat surface 在线。

这意味着当前只是“局部能力存在”，还不是“统一系统成立”。

---

## 6. 计划原则

本计划遵守以下原则：

1. 先建机制，再改行为。
2. 先可观测，再优化。
3. 先收 canonical 入口，再谈多 profile。
4. 先把 receipt 和 benchmark 建起来，再做策略实验。
5. 不在同一切片里同时做 runtime 大重构和 delivery 协议大清洗。
6. 不允许 per-role 再长出新的 ad-hoc policy。
7. 先做 foundation，再做 role specialization。

---

## 7. 计划分解

整个计划分成七条工作流，但执行顺序不是并行平均推进，而是有强依赖的。

### WS1. Strategy Framework Foundation

目标：

1. 把“机制层”建出来。
2. 不急着改变行为，只先把 profile 和 receipt 跑通。

交付物：

1. `strategy_contracts.py`
2. `strategy_profiles.py`
3. `strategy_registry.py`
4. `strategy_run_context.py`
5. `strategy_receipts.py`

退出标准：

1. 能解析 profile
2. 能为每轮生成 stable strategy identity
3. 能产出基础 receipt

### WS2. Runtime Handshake Convergence

目标：

1. 让 `RoleRuntimeService` 成为唯一 strategy resolve 入口。
2. 让 `roles.session` 持有 session 级 override，而不是 host 临时拼接。

交付物：

1. runtime 侧 profile resolution
2. session context_config 中的 strategy override 读取/写入
3. `StrategyRunContext` 从 runtime 单点创建

退出标准：

1. host 不再私自决定策略
2. runtime receipt 里已有 `strategy_profile_id/hash`

### WS3. Shared Agent Foundation Convergence

目标：

1. 把 continuity / history materialization 先收成共享基础能力。
2. 收掉 `roles.kernel`、host、gateway 的重复逻辑。

交付物：

1. `SessionContinuityStrategy`
2. `HistoryMaterializationStrategy`
3. foundation-level prompt layering contract

退出标准：

1. continuity pack 进入正式组装流程
2. tool receipts 的 micro-compact 成为统一路径
3. 角色侧不再各自拼上下文

### WS4. Phase 1 Code Domain Convergence

目标：

1. 把 exploration、budget gate、cache、range-first read surface 接进策略框架。
2. 让 code domain 成为共享基础能力的第一个强验证场景。
3. 让 `canonical_balanced` 成为真实默认 foundation profile，而不只是文档口号。

交付物：

1. profile-driven budget thresholds
2. cache tier wiring
3. canonical read escalation rules
4. full-file read governed upgrade path

退出标准：

1. 大文件默认 slice-first
2. near-limit 才 compaction
3. cache hit/miss 被 receipt 记录

### WS5. Evaluation Harness

目标：

1. 给后续所有策略演化建立基线。

交付物：

1. `strategy_benchmark.py`
2. replay fixture schema
3. scorecard schema
4. shadow mode comparison model

退出标准：

1. 能离线跑一批固定 benchmark case
2. 能输出同构 scorecard
3. 能比较新旧 profile

### WS6. Role Overlays And Alternative Profiles

目标：

1. 在 foundation 路径稳定后，再引入 role overlays 和其他 profile。
2. 先扩展 execution-family，再考虑更细粒度的独立表现层。

首批 profile：

1. `speed_first`
2. `deep_research`
3. `cost_guarded`
4. `claude_like_dynamic`

首批 overlays：

1. `director.execution_overlay`
2. `architect.analysis_overlay`
3. `qa.review_overlay`
4. `director.coder_overlay`
5. `director.writer_overlay`

退出标准：

1. profile 切换不需要重写 runtime
2. 每个 profile 都能在 benchmark 上拿到量化结果

### WS7. Stopgap Retirement

目标：

1. 清理与本计划直接相关的止血/兼容债务，避免新框架被旧入口重新污染。

优先处理：

1. 冻结的 role runtime host surface
2. Agent V1 HTTP surface 兼容包装
3. runtime websocket 双协议并存
4. role dialogue 兼容响应形态
5. `roles.kernel` 内旧补丁/旧回执 fallback

退出标准：

1. canonical 入口成为默认主路径
2. 旧入口只剩边缘适配或明确退役路径

---

## 8. 推荐执行顺序

推荐采用下面的切片顺序，而不是按模块随意开工。

### Slice 1: 机制底座，零行为漂移

先做：

1. strategy contracts
2. registry
3. profiles
4. run context
5. receipts

原因：

1. 先建立稳定身份和证据链，后面任何行为变化才有参照。

### Slice 2: Runtime 接入

再做：

1. `RoleRuntimeService` resolve profile
2. `roles.session` 存储 override
3. receipts 进 runtime

原因：

1. 先让 canonical 入口拥有框架，再谈上下文行为改造。

### Slice 3: 共享 Agent 基础能力收口

再做：

1. continuity pack 正式接入
2. history/tool receipt materialization 收口
3. 去掉 host/kernel/gateway 双实现

原因：

1. 这是解决“旧话题回流”和“工具回执堆 history”的根因层。

### Slice 4: Phase 1 code domain 收口

再做：

1. MAP/SEARCH/SLICE/EXPAND
2. read_file 升级治理
3. budget gate 真正接入

原因：

1. 这是共享基础能力的第一个重负载验证场景，也是解决“循环读同一文件”和“整文件盲读”的根因层。

### Slice 5: Benchmark + Scorecard

再做：

1. offline replay
2. score weighting
3. baseline 固化

原因：

1. 没有基线，后面的 profile 多样化只会再次回到拍脑袋。

### Slice 6: Role overlays + alternative profiles

最后做：

1. `speed_first`
2. `deep_research`
3. `cost_guarded`
4. `claude_like_dynamic`
5. Director / Architect / QA overlays
6. Director-line execution overlays such as Coder / Writer

原因：

1. foundation 默认 profile 还没站稳之前，不应该扩散实验面，也不应该过早做角色专属调参。
2. `Coder`、`Writer` 之类未来角色本质上属于 execution-family specialization，必须建立在稳定的 `Director` 执行底座之上。

---

## 9. 这条线必须同时收口的止血债务

不是所有止血版都要现在一起做，但以下几项和当前计划直接耦合，必须纳入路线图。

### 9.1 立即相关

1. `P0-3` 冻结的 role runtime host surface  
   原因：只要 frozen host 还承接主路径，就会绕开统一 strategy runtime。

2. `P1-1` Agent V1 HTTP surface 兼容包装  
   原因：如果外部仍长期走 V1 兼容面，新的 runtime strategy receipt 很难变成统一事实。

3. `P1-2` runtime websocket 双协议并存  
   原因：后续 strategy receipt / metrics / shadow result 必须有统一实时协议承载。

4. `P1-8` role dialogue 兼容响应形态  
   原因：上下文策略结果不能继续被 legacy response mapping 污染。

5. `P2-2` roles.kernel 旧补丁协议 fallback  
   原因：如果输出协议仍旧双轨，evaluation 和 history materialization 会长期不稳定。

### 9.2 暂缓但要挂账

1. `P0-2` `infrastructure.compat.io_utils` 大杂烩  
2. `P1-5` PM planning placeholder state port  
3. `P1-6` PM dispatch host bridge  
4. `P2-4` KernelOne 品牌/env alias fallback

这些要继续治理，但不应阻塞 context strategy 的第一阶段。

---

## 10. 量化基线与门禁

这条线必须用量化门禁推进。首批建议固定以下指标。

### 10.1 行为指标

1. 首个读码动作中 full-file read 占比
2. 重复读取同一文件/同一区段的循环率
3. tool receipt 进入 prompt 的 token 占比
4. compaction 在 80% 之前触发的比例
5. cache hit rate

### 10.2 结果指标

1. 任务完成率
2. benchmark pass rate
3. 用户纠偏率
4. 误读文件/误用工具率

### 10.3 体验指标

1. TTFT
2. first relevant slice latency
3. model-finished 到 UI-finished 的滞后
4. 伪流式拖尾时间

### 10.4 成本指标

1. input tokens
2. output tokens
3. 每轮 tool 调用数
4. 平均上下文预算占用

首批 promotion 门槛建议：

1. 结果指标不得回退
2. 行为指标至少有两项显著改善
3. 成本或延迟不能出现不可接受级别恶化

---

## 11. 明确不做什么

这次计划明确不做：

1. 先拆成独立三方库
2. 先做多租户/多仓共享的超级策略平台
3. 把所有 role 特性立即抽象成通用 profile 参数
4. 在 profile 机制未稳定前就做大规模 A/B
5. 把 graph 更新和 runtime 大重构绑成一个超大提交

原因很简单：

1. 先把仓内 canonical 系统跑通，比追求抽象完美更重要。

---

## 12. 文档层级关系

从现在开始，文档层级建议固定为：

1. `docs/KERNELONE_CONTEXT_STRATEGY_FRAMEWORK_BLUEPRINT_2026-03-25.md`
   - 上位蓝图，定义为什么先做共享 Agent foundation / Work Strategy Framework

2. `docs/KERNELONE_CONTEXT_STRATEGY_IMPLEMENTATION_MASTER_PLAN_2026-03-25.md`
   - 主实施计划，定义 Phase 1 共享基础能力和后续 role/domain 扩展怎么落地

3. `docs/CANONICAL_CODE_EXPLORATION_CONTEXT_ASSEMBLY_BLUEPRINT_2026-03-25.md`
   - Phase 1 code domain 默认 profile 蓝图

4. `docs/SESSION_CONTINUITY_ENGINE_BLUEPRINT_2026-03-25.md`
   - 共享 Agent foundation 的 continuity 子域蓝图

5. `docs/SESSION_CONTINUITY_ENGINE_IMPLEMENTATION_PLAN_2026-03-25.md`
   - continuity 子域实施计划

这样可以避免再出现“多个文档都在定义主决策”的漂移。

---

## 13. 下一步真正应该怎么开工

如果按长期最优路线开工，第一刀不应该先去调 profile 细节。

第一刀应该是：

1. 建立 strategy contracts / registry / profiles / receipts
2. 在 `RoleRuntimeService` 上接入 resolve + receipt
3. 保持现有行为基本不变
4. 先不做角色专属调优

原因：

1. 这是后续所有收口和评测的底座。
2. 没有这一层，后面每一次“优化”都没有可验证的统一参照。

然后第二刀再做：

1. continuity/history materialization 收口

第三刀再做：

1. exploration/read escalation/budget/cache 真正接进默认 profile

这是最稳、最长远、也最不容易返工的顺序。

---

## 14. 最终判断

最优方案不是：

1. 单一策略硬编码到底
2. 每个 role 各自调 prompt
3. 先做很多 profile 再补治理

最优方案是：

`KernelOne 共享 Agent foundation 先行 -> Phase 1 code domain 落地 -> receipts/benchmark 固化 -> 再允许角色和 domain 多样化`

这条路线更慢一点，但它是真正可持续、可治理、可演化的路线。
