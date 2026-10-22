# 原生工具与当前工作状态闭环

日期：2026-10-23。分类：pattern。基线：main `ab592bdf6b10b31e362e463e9f3775b2c4fe6db0`。原120仍为目标，本文件不表示已交付。v5两条原生运行均已关闭、源码与输入未变；L1-01为预算结束 interrupted，ROOT亲检FAIL；L1-02因重复检查主动取消，原5400秒预算未改，ROOT亲检PARTIAL（38PNG，亲看8张，真实下载与状态隔离成立，保留中等缺陷）。历史事实不覆盖。

## 用户问题与第一处偏差

1. 工具接口：真实原生请求丢失嵌套参数约束，模型看不到四动作/viewport边界；合法默认capture与只含revisit IDs的请求又被schema误拒。已证实两处首偏差：raw参数投影先丢约束并把nested required列表误判为父参数必填；有效快照序列化又以bool覆盖对象required列表。真实producer已有部分约束，未到达物理schema。独立复审还发现权威parameters存在时仍先解码legacy type，顶层union会崩溃，必须直接消费原完整schema，不能仅修纯builder。
2. 浏览器能力：当前观察器只支持click/keypress、每次新页面、四动作；L1-02已实际产生unsupported action fields。第一偏差是工具可执行词汇与完整用户操作能力不足；单改schema不能补fill/select。
3. 请求成本：CJK逐字符Python扫描被完整消息/图像序列化估算放大。纯函数ABBA有因果CPU证据；原生准备总延迟的具体热点仍UNKNOWN。无图像时输入准入和输出预算使用不同消息成本，合成2.8MB tool arguments已复现代理窗口不一致。
4. QA收敛：L1-01、L1-02均有成功工具反馈但没有及时形成独立验收；L1-02固定命令和结果精确重复111次。实际auto choice、正确身份、完整反馈及report_review工具已核实。共同策略根因仍未知。当前工作状态不显式可见是待检假设，不能先当结论。

## 现有模块与数据流

```text
PM → CE → Director → 当前产物真实验证 → 独立QA → 同Director修复
                          ↓                    ↓
                  EngineeringRunService / 同一SQLite journal
角色ToolSpec producer → K1通用schema投影 → 最终provider request
模型调用 → 归一化/原权限 → KFS与原K1 sandbox/process owner → 实际观察
角色消息/图像 → 同一保守输入成本 → 原模型窗口/输出预算 → 最终请求审计
```

- roles.runtime负责角色工具定义、浏览器观察、当前帧及角色事实；工程唯一写owner仍EngineeringRunService，记录仍workspace/.polaris/runtime/engineering/sessions.sqlite3。
- KernelOne原tool_spec_registry负责忠实投影参数schema；原token_estimator和invoke/stream预算负责技术估算与容量，不读取业务状态。
- 浏览器继续通过原K1进程沙箱，保持授权read roots、控制根隔离、网络边界、取消/排空及图像来源绑定。DOM操作不扩大项目源码写权。
- 不创建任务表、控制器、账本、旧准入或长期记忆。TaskMarket接入、跨调用浏览器会话及下载留待独立需求证据。
- Graph已核roles.runtime及两运行subgraph；现catalog没有逐文件owned_paths覆盖这两既有KernelOne技术文件，本次沿现技术模块修复，不虚构Cell或补造owner映射。

## 已有实验与反证

| 边界 | 原机制与实验 | 本次可接受结论 |
| --- | --- | --- |
| ToolSpec | 8个固定案例：实际4507 schema与当前投影相同；5actions/height1201漏约束；默认capture/revisit误拒；混合revisit/capture漏拒 | 忠实投影、bool/list required区分与原parameter_any_of必须一起完成；仅修投影仍漏互斥的阴性保留 |
| 浏览器 | 6条件/8实际dispatch/4真实K1隔离Chromium：旧词汇拒绝，fill/select短流程成功，四动作仍拒长流程，原子7步成功；权限/网络负控制、取消排空通过 | 支持原子中文输入/选择/连续操作的模块可行性；不证明模型自主使用、下载、跨调用持久化或项目成功 |
| 估算 | 36差分一致，含Unicode全码点和比例边界；单语料ABBA CPU约0.2883→0.0150秒 | 原函数计数可等价优化；19.2倍仅是该纯函数的一次有限实验 |
| 无图像预算 | 大tool arguments代理输入708119，旧flat4223，原输入+输出超1M；共享成本可降有效输出并合原窗口 | invoke/stream都消费同一成本，不能只修单消费者；不冒称实际provider token数 |
| QA工作状态 | 两个冻结历史实际body的2pair next-decision实验，计划A原body，B只加有出处的当前帧事实 | 四次真实HTTP200、0工具执行：L101 A/B均rename-only重复，未见收益；L102 A重复原命令，B选择新browser检查。仅支持有限候选，不证明收敛或成功。原历史/图片/模型/工具参数不变，负结果保留 |

证据分别位于主仓`.polaris/audits/toolspec-browser-boundary-20261023/`、`browser-complete-flow-20261023/`、`native-request-estimator-profile-20261023/`、`native-budget-scope-20261023/`，及两个项目各自`.polaris/audits/`。所有替身与不能证明的范围以原报告为准。

## 最小修改机制与界限

- 通用schema builder保留嵌套JSON Schema约束，区分参数级bool required与对象级required列表。producer复用既有parameter_any_of表达capture/revisit互斥，不写工具名特判。
- 原browser_observe补充fill/select_option，保留精确文本与合法空字符串；去掉固定四动作上限，沿原deadline、进程隔离、总等待/帧与输出边界执行完整原子流程。返回只读公开DOM控件观察，不允许通过修改私有应用状态冒充操作成功。原始像素、当前候选绑定、独立QA规则沿现机制。
- 等价CJK计数优化不改变token估算结果、模型上限或统计口径。无图像同样使用现有共享消息成本进行输入与输出预算计算，invoke与stream一并迁移。
- QA工作状态已有一正一负的单步样本，选择作为待原生验证的候选；复用原journal派生当前候选/QA frame/证据计数/精确及可见结果重复次数/原预算的短期请求观察；不把状态提示持久化成第二事实源，不删除历史、变权限、加重试上限或强迫给结论。当前仅观察到一个case的下一工具选择改变，完整QA/交付收益仍UNKNOWN。不得由重复计数推断同效果、自动重放或强迫决策。

## 副作用、恢复与证据失效

工具拒绝必须显式返回，不执行或吞调用；未知效果保留UNKNOWN。恢复继续使用原身份、范围、deadline与原journal。revisit仅恢复同role/workspace原像素，不刷新旧候选的适用性。实验的候选改动此前仅独立worker内存；正式集成后另冻结版本，旧v5证据只适用ab592。主实例/NATS不重启，新隔离实例加载新源码。

## 验证与停止条件

先把原反例变成真实producer/consumer回归，覆盖合法推进和非法拒绝；再验证原生invoke/stream请求、read-only QA、reinspection、取消/恢复、既有权限/资源及相关架构。完成Ruff/format/mypy、相关pytest、必需架构与KernelOne ALL，并作独立审阅。所有失败和未测范围保留。

当模块反例、相邻消费者与必要负控制满足要求即停止扩题，集成主线并冻结新候选。然后以原catalog全新隔离L1-01完整运行和ROOT亲检验证真实闭环，再按原120逐项扩大。一个成功只能证明相应任务/版本/环境，不声称Polaris优于Codex/Claude、任意项目必成或模型费用已知。源码/配置再变即新批次。

## 集成验证记录（不是项目交付）

2026-10-23，ROOT对最终14份Python源码／测试建立SHA清单，验证期间未变：标准mypy14文件通过，Ruff／format通过；21个相关模块组合436项通过（5个原警告），40项架构检查通过；KernelOne ALL12阶段通过，368项通过／1项既有跳过。目录检查第一次误用仓库根，missing catalog的调用失败保留；改用后端根的audit-only为159项既有问题、0新增／0manifest mismatch，显式使用原有空基线仍将159条全记为new并失败，该结果保留。ROOT核对了T22封存原报告的115个唯一指纹与当前集合完全相等；以该只读历史报告为基线的增量命令通过、0新增／0mismatch。没有改规则或重建当前基线来隐藏问题，仓库默认空基线的历史失败仍未清债。

独立审查发现的顶层union P1已通过ROOT最小修复和4个RED→GREEN真实registry／JSON恢复反例关闭，全ToolSpec模块55项通过；原审查与修复复审分别封存。QA工作状态独立9项通过，13份原生fixture最终快照与同SQLite新Store恢复一致。该恢复模拟丢失发生在同一解释器，不宣称真实OS崩溃恢复。浏览器实际两协议final tools／DOM／像素、权限与取消证据保持原范围。

作者先前较广K1组为1294通过／1既有失败（reserved256与旧测试期望1024），strict模式8条已复现原基线诊断，原始结果均保留；不能写成全部无失败。浏览器先前186项组为181通过／5缺PIL环境失败，原5条在已有3.12环境复验通过，不能捏造一次186全绿。ROOT最终436组合是另一次明确环境与源码的独立结果。

证据在主仓`.polaris/audits/native-tool-context-integration-20261023/`：`root-source-manifest.json`、各作者原报告、两项独立审查、`root/final-results.json`及后续更正目录调用；`defects.json`保留QA策略根因OPEN。原有能力的下一步准备另核对了120身份／brief／checks和918处原文引用，位于`.polaris/bench/original120-next-preparation/`；不是新门禁或已接入能力证明。

本次停止继续扩研究，提交main并另起v6冻结。新原生L1-01完整交付／独立QA／ROOT亲检仍待实测；先前v5两条终态和产物不改写。
