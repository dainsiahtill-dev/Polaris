# Polaris engineering_v2 当前进展与边界

记录日期：2026-10-21。本记录区分历史交付、当前候选资格和本次真实运行。默认 Director 与旧执行写准入退役已进入主线源码；常驻 main 后端仍是旧进程，未部署重启。**原目录 120 项 L1–L12 尚未完成实测，不能由单项成功扩大为平台完成。** 历史自主验证交付详见 [R04 完整记录](POLARIS_ENGINEERING_V2_L101_AUTOVERIFY_R04_20261021.md)。

## 最新全量批次与命令超时反馈边界

`cd52d068b` 的全新 direct L1-01 R02 已完成实际检查、当前独立 QA、项目接受及原停止流程，原 CLI 返回 0；此前 R01 的停止 UNKNOWN 保留。R02 证据位于统一审计根 `task8-direct-l101-native-r02/`，不计入随后启动的 120 项新样本。

原目录全量批次使用同一源码、模型、host 与 5400/6000 秒预算，全新工作区逐项运行并由主 Agent 逐项亲自检查。首项 `05fb0a824db5431287913bb824c8f076` 第三条原生命令查询 npm 版本时达到命令的 30 秒期限。KernelOne 完成物理清理后，工具层仍将结果投影为任务中断；Director 因此未收到下一轮反馈。没有产物、正式验证或 QA，原资源停止成功。主 Agent 质量结论为 FAIL，队列停止，剩余 119 项未运行；该失败不改写，也不归因为已证实的网络问题。

修复区分已清理的命令期限与取消、未知清理：`ToolObservation.timed_out` 默认 false，已清理超时保留失败、缺失的退出码、部分输出及可能的部分写入提示；同一 Director 可以检查当前状态后决定下一步，不自动重放命令。可能写入的超时使旧验证失效。Verifier、耐久完成校验与恢复投影保留这一事实；真实超时是已存在的失败证据，不能接受为通过。取消和未证实的进程清理仍停止执行。

真实沙箱、SQLite 与合成原生 HTTP 模型的两个实验先失败、修后通过，覆盖部分写入不重放、下一轮反馈、当前验证、独立 QA 及冷读取。它们只证明边界机制；相邻回归、正式门禁和新冻结批次的实际结果以 `task9-command-timeout-boundary/` 为准。完整 120 项及每项质量仍待实际完成。

旧批次完整证据：`task8-full120-frozen-cd52/`，包括原始单项报告、亲自质量检查、最终 provider request 与 Launcher 运行可见性。Launcher 注册目录已与主实例统一为 `~/.polaris/instances`；未迁移旧注册记录或重启主服务。

## 已保留的真实结果

| 样本 | 原生运行 | 结果与限制 |
| --- | --- | --- |
| 原始 L1-01 首次隔离交付 | `4e26e6a9071e47ad95598bd6c6e3f16b` | `completed / passed / approved / accepted`，四个预设检查实际通过，资源关闭，零外部目标代修。冻结源码 `88524d4f8`，完整记录见下方链接。首次启动环境失败仍保留。 |
| 已有项目边界修复 | `de732ae2193242cf9aac631c6a9a3f8e` | 平台复现无效湿度输入、修复并执行负对照；95 项断言、四项检查与当前独立 QA 通过，资源关闭。 |
| L3-26 首候选 | `4345da9cb4d244e39b8417e6bbe38238` | 平台及 QA 曾接受，但外部只读审阅确认重复猎杀/能量结算 P1；保留原接受事实，独立产物质量轴失败，不计为盲验成功。 |
| L3-26 收到缺陷诊断后修复 | `fafb624f217c47bcba560e09940a81eb` | 一次外部只读诊断反馈后，由平台合法工具自行修复；82 项断言、负对照、四项检查和当前 QA 通过，资源关闭。零外部目标编辑，但有外部诊断，不宣称无反馈盲验成功。 |

[首个 L1-01 完整交付记录](POLARIS_ENGINEERING_V2_L101_R02_20261021.md)。其他样本的原始证据位于本文末统一审计根；不同源码和验收输入不合并成一个成功率。

## 自主选择验证的三次尝试

正式入口为内部验收 CLI → Instance Supervisor/Registry → 项目独立后端的 `/v2/engineering/runs`。原始目录 `projects_v2.json` 的 TypeScript 发光昆虫花园需求完整保留；原始 `required_checks=[]`，Director 自选配方，独立 QA 必需。模型仍为 MiniMax-M3，物理请求输出上限 3000，项目/外层预算 5400/6000 秒。没有外部目标编辑、安装、验收命令或补造收据。

### R01：输出截断直接终止

- 源码 `6dd7d2b1a9fcc6c160a9ea374da9f833712f6bca`，run `50d821e1b0a141d5b000e35e246f3d93`。
- 原生响应 `ok=true / stop_reason=max_tokens / output_tokens=3000`，包含两个写文件提案。
- 整批被拒且零执行，但异常直接令角色失败，模型没有收到可纠正反馈。构建、正式验证及 QA 均未到达。
- 结果为真实 FAILED，约 112.33 秒，实例随后 STOPPED。失败事实不被后续运行覆盖。

主线 `ecca1b4c5108ff22b025dd23089e7efb871ef59d` 修复这一机制：只对可纠正的截断/参数输出反馈；协议结构和工具 ID 损坏仍停止。一次既有 SQL message 事务保存拒绝事实和实际反馈，同一角色、原工具/授权/期限继续；不执行有效前缀、不插入未配对工具消息。

最终冻结候选 517 项联合回归通过；KernelOne ALL 12 阶段通过（368 passed、1 skipped）；独立 11 项有限故障实验通过。9370 个源码文件与模型配置在资格检查和后续真实运行中保持一致。这些是组件资格，不是项目交付。

### R02：真实恢复成立，验证阶段仍未收敛

- run `01e5302e17664fd78491c6fdaed10e25`，全新目录 `~/.polaris/projects/engineering-l1-01-autoverify-20261021-r02`。
- CE 在 turn 108 达到 3000 输出上限；raw event 112 后，原子拒绝消息 113 留下零 dispatch。下一同 CE turn 115 的最终物理请求完整包含该反馈，随后 CE 完成交接、Director 正常开始。这证明 CE 的真实恢复；未观察到远端 Director 截断反例，不能扩大该声明。
- 文件、依赖准备、编译修复、测试与入口均由平台自身工具完成。首个生产源码写入在 run 创建后 156.10 秒。
- 模型自行选择 `npm install`、`npm run build`、`npx tsc --noEmit`、`npm test`、`npm start -- --seed 20261021 --ticks 3`。
- 四次正式 Verifier 均实际执行：安装失败，其余 16 个构建/类型/测试/入口子检查返回 0（61 项测试）。安装尝试受只读输入范围限制，exit 254/226/226/226；这是已存在但失败的证据，不是 missing。
- 模型继续修改依赖/锁文件配置并研究只读安装。四次配方均由模型原生提交，平台没有重新添加已移除配方。没有直接证据证明模型把自选配方视为不可变合同。
- 原 ToolSpec 只明确 source 只读；反馈没有清楚呈现依赖、清单及锁文件同属只读输入，以及普通工具准备和当前候选验证之间的合法分工。

主 Agent 为结束这个重复环境限制实验，通过正式 cancel API 取消，随后执行正式 observe/stop。**终态 CANCELLED / NOT_ACCEPTED；QA 未调用。实际 908.17 秒，未耗尽 5400 秒；不是自然 FAILED 或预算超时。** 最后一次验证后又改过依赖输入，当前 revision 82 / verification not_run，旧的 16 项通过不能作为最终候选通过。

只读静态审阅确认 6 个生产 TS 文件/1206 物理行、2 个业务测试文件；仍保留高湿度垂直环绕 P2 和亮度 JSDoc P3，不伪造修复。无当前独立 QA 通过结论。

### R03：范围反馈形成推进，检查归属暴露迁移缺口

- 源码 `83fadd9efe94ada11822d88192a1b86604e93936`；9372 文件冻结，519 项联合回归与 KernelOne ALL 12 阶段通过后启动。
- run `1fe1f9d6df904bce93af2012af9e01d3`，全新隔离目录 `~/.polaris/projects/engineering-l1-01-autoverify-20261021-r03`。模型、原始需求、host 权限及 3000/5400/6000 预算保持。
- 新工具说明及 actual binding.scope 完整进入 Director 的最终物理请求。模型据此将输出调整到合法 `dist/build`，构建、业务测试和 CLI 形成真实通过；湿度灯光衰减的业务缺陷也由原生运行自行修正，外部没有注入诊断。
- 依赖安装仍被模型反复放进只读验证。不能据包含管道、tail 或后续 echo 的普通命令整体 exit 0，断言安装子步骤已成功。后来 vendor 工具链尝试又引入 npx 编译器错误；旧检查不能当最终候选证据。
- 已证另一处消费偏差：Director 自然结束后，finalizer attempt 1353（requested_by=null）自动复跑旧模型配方；新 Director frame 1371 的真实 user 消息称其为“Final required checks”，但不可变 host `required_checks=[]`。这发生在前五次原生验证失败之后，不能倒推解释最初五次失败。
- 共 9 次原生验证调用及 1 次自动复跑，10 次实际 attempt 中 9 次失败、取消时最后一次 UNKNOWN。QA 始终未到达。

本轮由主 Agent 正式取消，**CANCELLED / verification UNKNOWN / 未交付**。实际 1245.19 秒，未耗尽 5400 秒。2534 events、245 工具记录全部 observed、pending 为空，165 个 capture-dispatch ID；HTTP 总数、账单及人工分钟保持 UNKNOWN。原始 provider usage：input 150516、output 63037、cache_read_input_tokens 11363819，不能拿归一化缓存零值替代。

当前静态审阅为 8 个生产 TS 文件、929 物理行、61 个业务/CLI/种子测试及 2 个结构测试；`.probe`、vendor 和 node_modules 不计业务深度。目标 Web 的 Canvas arc 参数及缩放点击坐标仍有 P1/P2 静态问题，未动态代验，不能用工作台浏览器通过替代目标网页通过。工作台只读浏览器首试实际通过。

正式 observe 为 CANCELLED（exit 130），stop 为 STOPPED（exit 0）；独立进程与 49978/5174 关闭，主实例/NATS 保持；源码、模型及冻结配置全程未变。

## 资源、观测与计量

- R02 canonical SQL：1205 events、133 工具记录全部 observed，113 `ok=true`、20 `ok=false`，pending 为空；run_ended 明确允许释放。
- 102 个 capture-dispatch ID：PM 4、CE 10、Director 88。实际 HTTP 总发送数、账单、人工分钟仍 UNKNOWN；不能用 Context 数替代。
- 原始 provider usage 字段分别报告：input 73299、output 44959、cache_read_input_tokens 3974109。缓存字段与归一化 cached_tokens 存在差异，不把差异解释成零缓存或零成本。
- 浏览器首试因外部观察脚本更改缓存根而找不到已安装二进制，3ms 失败，未进入页面/API/WS。保留该失败；显式绑定现有 Chromium 后，只读重试 17.07 秒通过：水位 248 后收到真实事件 249–259，当前 Context/final-request 双 200，无轮询/SSE/跨项目订阅。
- observe 返回 CANCELLED（exit 130），stop 返回 STOPPED（exit 0）；自有后端/前端退出，49978/5174 关闭；主实例 PID 18128 与 NATS PID 18035 保持。
- R02 全程未热改源码或模型。只读浏览器环境修正没有改变平台执行配置或目标文件。

### R04：当前模型自选验证闭环已通过并释放

- 冻结主线 `a8b820a3d8ba9f3c1c7bc02ffb901a93dc9de1c6`；9373 文件和模型配置直到物理停止均未变。521 项联合回归、KernelOne ALL 十二阶段（368 passed、1 skipped）及四项独立机制实验通过。
- run `553ab2e9f19245499ce318a26d51b63b`，全新隔离目录 `~/.polaris/projects/engineering-l1-01-autoverify-20261021-r04`。原始目标、host、空 `required_checks`、模型与 3000/5400/6000 预算保持。
- 正式 PM → CE → Director → QA；约 91.96 秒首个业务源码落盘。模型在普通工具中准备依赖、自行处理编译和测试反馈，最终一次 native `verify_project` 自选八项检查全部真实 exit 0，包括 build、tsc noEmit、104 项业务断言及 CLI；无隐式自动验证。
- 当前 QA 639→785 approved，绑定 revision 49 / verifier 627，310 项输入与验证结果及现盘一致；ProjectOutcome accepted，terminal 787 completed/passed。798.146 秒，89 工具全部 observed（80 ok、9 false），65 capture/dispatch ID；实际 HTTP 发送、账单与人工分钟 UNKNOWN。
- 正式 observe 为 OBSERVED_ACCEPTED、stop 为 STOPPED，均 exit 0；无 active/pending，本次 PID 23465/24674 退出、49978/5174 关闭，主实例与 NATS 保持存活。零外部目标编辑、依赖安装、验收命令或诊断注入。
- 工作台首试浏览器实际通过，不替代目标验收。交付后静态审阅没有明确 P1，但保留 tick 数分配、文档连闪序列及畸形 seed 回退三项 P2，未外部修补。单次成功不是可靠率或文案改动的独立因果证明；失效后重新请求模型验证的分支本次未触发。

## 已集成修改与下一步

R02 后的两路径说明/范围反馈已通过 19 项相关、5 项独立及 519 项联合检查，并在 R03 真实验证了传递和目录调整；尚未证明其足够解决交付收敛。system 阶段证据说明不足属于合理假设；仅准备了同 request 单处 system 替换的 A/B，网络调用和工具执行均为零，不冒充效果数据。

R03 封存与资源释放后，当前只关闭**模型选择与宿主固定要求的归属边界**：同一 SQL 中已接受的 native `requested_by` 历史决定模型选择，后来的自动 attempt 不得覆盖归属；自选配方失效后只反馈给同 Director，等待新原生请求，不隐式重跑；当前失败反馈明确区分 `required_checks` 与 `verification_commands`。从未做过原生选择的用户固定检查保留原自动路径。UNKNOWN 的旧阻断先于继续执行，QA 与接受标准不放宽。

Director 的 system 说明同步区分真实准备证据与准备后验证，复用既有指导；不新增权限、数据表、公开 DTO、npm 特例、超时或重试控制器。原生选择、实际收据和交接消费者一起迁移。

独立真实计数器反例 RED 已转 GREEN；固定检查自动、UNKNOWN 不重放、历史自动 attempt 遮蔽后冷恢复仍为模型归属共 4 项有限独立实验通过。旧“修改产物后自动重验”的合成响应已迁移成显式 native 重验，保留旧检查/新检查及当前 QA 的所有断言。完整冻结资格和全新 R04 已完成；合成机制证据与本次远端模型单次交付证据分别保留，不能互相扩大。

下一步继续既定默认入口切换、旧生产写路径退役与证据页面同步，再冻结新批次逐步扩域 L1–L12。三项 P2 纳入平台自身的后续修复与独立复验，不外部代修、不改写原运行。全部失败/取消/UNKNOWN 保留；单项目成功不扩大为平台完成。后续只在 `main` 研发，不新建分支或 worktree；无关改动及旧冻结证据保留。

## 原始证据

统一根：`~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/`。

- `task7-l101-r02-final-audit/`、`task7-existing-repair-final-audit/`：历史首交付与已有项目修复。
- `task7-l326-initial-final-audit/`、`task7-l326-feedback-final-audit/`：L3 平台接受与独立质量结论、反馈修复。
- `task7-l101-autoverify-r01-final-audit/`：R01 第一失败与关闭。
- `task7-native-turn-feedback-candidate-r2/`、`task7-native-turn-feedback-qualified-r2-{joint,all}/`、`task7-native-turn-feedback-independent-review/`：主线输出恢复候选与组件资格。
- `task7-l101-autoverify-r02-final-audit/result-and-closure.json`：主 Agent 核对的当前运行与资源结论。
- `task7-l101-autoverify-r02-physical-observer/`：最终请求、原生响应、反馈、实际 Verifier 收据与限定归因。
- `task7-l101-autoverify-r02-artifact-review/`、`task7-l101-autoverify-r02-browser{,-r2}/`：静态产物与两次浏览器尝试。
- `task7-l101-autoverify-r02-{cancel,observe,stop}-root/`：正式取消、观察与停止收据。
- `task7-verifier-preparation-feedback-{red,green}/`：说明/范围反馈实验；后续完整 R03 未交付。
- `task7-l101-autoverify-r03-{physical-observer,artifact-review,final-audit}/`：R03 最终请求、迁移缺口、静态质量与关闭。
- `task7-l101-autoverify-r04-{physical-observer,product-static-audit,browser-audit,official-closure,immutable-delivery}/`：R04 当前验收、已知 P2、只读观测、正式释放及产物封存；完整说明见 R04 专项记录。
- `task7-verification-selection-owner-review/`、`task7-verification-ownership-root-red-r2/`、`task7-verification-selection-owner-independent-review/`：归属反例和有限修复证据。


## 第 8 批：默认入口与旧写路径收敛（实施中）

主线 `2aeeb9503` 已包含默认工程 UI、薄角色 CLI、旧 HTTP／角色执行准入退役、历史只读查看及规范／图谱迁移。其后继续修复历史任务读取：沿现有 TaskRuntime／TaskBoard 增加显式只读构造，不创建目录或锁，不修补损坏历史；CLI 查询直接消费原 observable projection，并绕开执行 bootstrap。全局 `--workspace` 曾被子命令默认 `.` 覆盖，已由真实查询反例定位并修正参数传递；不增加另一套任务事实源。

当前限定验证：CLI／架构及相邻回归 184 项通过，新增 TaskRuntime 只读用例 12 项通过。TaskRuntime 整 Cell 为 406 PASS／4 FAIL，四例在原始 `11db38743` owner 模块下同因复现；失败证据保留，没有降低断言或扩大本次修复。此前前端 155 项、HTTP／存储 573 项、catalog 92 项均是各自切片证据，有重叠且版本不同，不求和。catalog 仍有 159 项既有问题，不能把 audit-only exit 0 称为清洁门禁。

冻结 R1 的 9383 个源码文件与模型配置保持一致，1338 项联合回归通过（512.00 秒）；KernelOne ALL 原有三项旧 CE 执行夹具失败已保留。随后只迁移该测试文件到真实新工程原生请求入口：原三反例、完整文件及相邻 ContextOS 合计 76 项通过。冻结 R2 的 KernelOne ALL 十二阶段通过（368 PASS／1 SKIP，命令 333.31 秒）。R1→R2 仅该测试文件变化，1338 项仍是原候选证据，生产文件相同，不将其改标为一次新的全量执行。

默认 URL 浏览器首试 **FAILED**：真实创建请求 200、工程工具完成写读、SQL 事件越过初始水位 7，但没有收到工程推送。日志逐条显示发布时受管 NATS 客户端未连接；WebSocket 后来才建立连接，启动依赖正在现有技术 owner 内修复。独立核对六份真实最终物理请求快照，Director、run／workspace、读写工具和落盘 hash 均一致；快照数不作为 HTTP 次数。量具还误收集了一份归档测试，第二次创建 409／pending-blocked 事实保留。四端口与私有进程已物理关闭，不把这一结论扩大为项目交付或所有 SQL row 已终结。

**下述初次失败后的默认页面组件验收已通过；新版本真实模型项目与 L1–L12 仍未完成。** 主后端 49977 仍为旧进程，OpenAPI 无 `/v2/engineering`；未部署或重启主服务。内部旧 Bench runner 及剩余旧执行实现仍须按真实消费者收尾，之后另起冻结批次扩大 L1–L12。旧版本 R04 的交付与三项 P2 保持原样，不转移为本批验收。

本批证据：统一审计根下 `task8-root/`、`task8-http/`、`task8-frontend/`、`task8-governance/`、`task8-independent-review/`、`task8-task-history-read-fix/`。临时量具为合成夹具，不能计为 Bench 成功。


### 默认入口最终组件检查点

受管 NATS 连接与既有 JetStream runtime stream 声明现由技术启动显式等待，完成后才开放 HTTP；既有 stream 管理器的原生枚举和秒单位映射在原 owner 内修正。没有恢复旧 Factory／settlement 控制循环。真实私有 NATS 实验证明先发布、后订阅能读取同一事件，重复初始化保留消息，应用退出后借用的 server 仍存活。

冻结 R4 的 9383 文件和主模型配置稳定，KernelOne ALL 十二阶段通过：368 PASS／1 SKIP，命令 325.08 秒。相关 147 项回归通过。诊断接口单元夹具的四项冷启动错误已保留；仅隔离无关 app／LLM 装配，原四项测试体逐字保留，冷／暖配置各四项通过，既有配置 hash 不变。

新鲜默认页面 R3 使用合成 loopback provider，真实 App、HTTP、工具、SQLite、NATS 与 runtime.v2；一次测试通过（浏览器命令 24.50 秒）。run `c1b13c7a7d0940e6baef44c7470424c9` 在同一身份下完成创建、真实写读、推送可见、当前 ContextOS 查看、显式刷新失败与恢复、取消、刷新后恢复同一运行、续跑并再次取消。三个只读 SQL 检查点分别位于 seq 33／46／50，均通过；没有旧写 POST、产品轮询、跨 workspace 或全局 Bench 订阅。

第一轮零推送和第二轮审计时点错误均原样保留。第二轮将取消前 prepared 引用与取消后 terminal 引用混比，虽为同一物理请求，引用已变化；第三轮在实际 terminal WS 后重新打开当前 UI，再执行完全相同的严格 SQL 核对，没有放宽断言、超时或重试。主 Agent 独立核对八份真实物理请求快照的 hash、角色、run／workspace 和工具，以及实际文件与原生写入参数一致。快照数不作为实际 HTTP 发送次数。

私有后端／前端／NATS／provider 四端口和进程均关闭，主实例 18128 与共享 NATS 18035 存活。最终语义为合成组件运行 cancelled、资源 CLOSED，**不计为真实模型项目或 Bench 交付**。主后端仍有 330 个 OpenAPI 路径、无 `/v2/engineering`，未部署本轮源码。

最终证据：`task8-qualified-r4-all/`、`task8-default-ws-fix/`、`task8-diagnostics-cold-fixture/`、`task8-default-browser-r3-prep/`、`task8-root/default-final-root-acceptance.json`。第 8 批整体仍需收尾内部 Bench runner／剩余旧消费者及原理页面，再用正式新入口另起冻结真实模型批次。原目录有 120 个 L1–L12 项目，尚未完成全范围验证。

### 内部 Bench 复用正式入口与未知结果恢复

当前 runner 的活动路径已改为同一 `engineering_acceptance`／`EngineeringRunService`，默认 Director 直接工作，显式选择时才前置 PM／CE。保留原始 catalog 的 120 项、全部业务／量化要求和 5400／6000 秒预算。冻结输入及报告仅写目标外；不再由 harness 写目标需求、初始化 Git、安装依赖、执行项目门禁或制造旧 Factory／ledger 证据。平台接受、独立产物质量和资源闭合分别报告；独立质量未测保持 UNKNOWN。

接口实验发现两个推进缺口：Supervisor 已提交实例但返回失败时，外部 binding 仍为空；正式创建已持久化但 HTTP 响应丢失时，外部缺少 run ID。修复保留原阶段和 request-attempt 状态，以已有实例 owner 查询精确归属，并复用已有 `find_request` 暴露认证、workspace 绑定的只读查询。恢复核对原指令摘要、workflow、检查和 profile，不扩大权限、不重发创建；404 不证明操作没有发生。已证明尚未提交请求的清理仍要求 canonical SQL 可读且无活跃运行／未结工具，已提交运行仍须终态。

主 Agent 的适配器／HTTP 相邻 77 项通过；独立审查经真实认证 ASGI、现有服务及 SQLite 的四项有限实验通过，包括 ACK 丢失、查询不存在、启动返回丢失和 runner 超时取消／清理组合。48 项旧协议消费者迁移后联合 298 项通过，其余原有测试保留。更大范围首次检查为 561 PASS／23 FAIL；剩余旧入口消费者与辅助测试正在归因收尾，尚不称全组通过。上述服务／进程端口均使用明确合成替身，不计为远端模型或 Bench 交付。

证据位于统一审计根 `task8-bench-runner/`、`task8-bench-catalog/`、`task8-request-lookup/`、`task8-acceptance-lifecycle-audit/`、`task8-bench-recovery-final-review/`、`task8-bench-consumer-tests/` 和 `task8-root/recovery-*`。下一步冻结当前完整候选并运行必要检查，再用新默认入口启动全新 L1-01；旧 R04 的真实交付仅适用于其原冻结源码，不转移成本批结论。

### 新默认 L1-01 已验收，资源确认仍需收尾

主线 `2c07bfca2` 经冻结 9384 文件、834 项相关回归、KernelOne ALL 十二阶段 368 PASS／1 SKIP、17 文件格式／静态检查及 5 个生产文件类型检查后，已通过实际新 Bench CLI 运行全新 L1-01。Director 按需咨询 CE，PM 未调用；原生失败修复、构建、类型、33 项业务测试、42 项核心检查和当前独立 QA 成立，ProjectOutcome accepted。277 份当前文件 hash 及 Verifier／QA 同 revision62 manifest 由主 Agent 直接核对。

原 STOP 仍为 UNKNOWN／exit2，整 CLI exit1，不回标全绿；后来独立读取证明自有 PID／端口已关闭，主服务／共享 NATS 保持。首次收尾偏差已用私有真实 owned child 复现为 owner wait／reap 缺口及未确认仍清 PID，正在已有进程底座修复。两项静态 P2、安装管道不能证明安装通过、HTTP 总数／账单未知均保留。源码、模型、host 和 catalog 在本次运行直到原停止后保持不变；后续修改另起候选。完整结果、量具边界与证据见[新默认入口 R01](POLARIS_ENGINEERING_V2_DIRECT_L101_R01_20261021.md)。全 120 项尚未运行，不标整体目标完成。


### e700 原目录前三例：平台接受与质量结论分离

L1-01、L1-02、L1-03 均由正式新工程入口生成、验证、独立 QA accepted，并由原 CLI 确认 STOPPED/exit0；没有外部代写、代装依赖或补造原生证据。主 Agent 事后亲自只读复验分别发现核心问题，质量结论均 FAIL：L1-01 闪烁时钟将 tick 当毫秒；L1-02 CLI 提醒基准固定2024年；L1-03 watch 七步有八标题但只有两份相同最终画面，真正云图对风向不敏感。有效领域逻辑和真实测试存在，但不能据此称成品质量合格。原目录其余117项未运行。

实例页空白已定位为交接缺口：Launcher URL 只有 workspace/instance 绑定，页面缺 run ID 时既不查当前运行，也不订阅工程事件。修复复用原 EngineeringRunService/SQLite 查询当前身份；初始只读查询及同 workspace runtime.v2 事件引导 UI，不把当前 run 写进 Registry，不新增控制器或轮询。后端独立45项检查通过；合成 native provider、真实工具/SQLite/NATS/浏览器已经从实际 Launcher 按钮打开，看到原运行、工程推送与真实写读结果；该组件证据不计为远端模型交付。前端独立审查发现的 workspace 往返及订阅停用期间旧响应两个生命周期反例已修复，独立34项反例/相邻检查与121项前端检查通过；最终候选再次从实际 Launcher 按钮完成当前运行发现和真实 WebSocket 写读进展，8762个前后端文件在浏览器观察期间相同，私有四类资源已关闭。

下一批遵循[角色组合预试验](../experiments/POLARIS_ROLE_COMBINATION_PILOT_20261021.md)。保留规划与设计职责，先在相同质量要求下比较咨询策略，不根据三个不同任务就断言缺文档是根因。源码变化另起批次；主后端18128未重启，本轮独立实例加载新代码。

证据：统一审计根 `task9-full120-timeout-recovery-r02/root-quality/L1-01/`、`task9-full120-e700-cohort/root-quality/L1-02/`、`root-quality/L1-03/`、`task11-current-run-bootstrap/`、`task12-role-comparison/`。快照、请求准备事件不计为实际 HTTP 总数，账单保持 UNKNOWN。

本接口最终相邻 CLI、实例、runner 与旧入口拒绝共215项通过，后端当前查询25项及相邻 owner/store/完整 HTTP 53项通过；检查存在重叠，不求和。KernelOne ALL 十二阶段368 PASS/1 SKIP，其8129个后端文件与最终候选相同；运行期间仅前端hook及测试变化使严格全源码守护返回2，原报告保留，不称同一次全局冻结资格。最终前端另经121项、类型/构建/静态检查及浏览器验证。完整前端53个失败及3个Resident错误在HEAD替换对照复现，后端扩展检查351项后停滞中断；不声称全仓测试通过。

最终独立审查无当前Launcher/App阻塞；保留潜在P2：hook的显式prop经历r1、启动new、r2、再r1时可能选回new。当前App只读取一次初始URL，无法触发同挂载导航；将来接入该消费路径前必须关闭反例。本次不将无当前因果的风险继续扩为角色实验前置条件。
