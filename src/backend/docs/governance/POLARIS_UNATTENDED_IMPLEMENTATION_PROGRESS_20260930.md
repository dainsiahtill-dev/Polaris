# 无人值守底座实施进展：2026-09-30

本文件是工程交接记录，不是平台运行事实源。实施发生在当前 `main`
工作区，基线 HEAD 为 `0d475c60474f2b1ea4a06ea136c0f817d2c49bfa`；未建分支、未提交。
原有 `.serena/project.yml` 修改保留。生成 Bench 目标未被工程 Agent 修改。

## 当前结论

最新核对（2026-10-01 01:30 UTC）：MiMo r02 同阶段恢复已经真实完成两个 Director
任务、物化12个文件；第三任务被 `file=path` 未注册别名挡住，后续补写120s超时。
已动态复现并修复预算7000被策略覆盖到128000，以及这一具体工具参数断点。
owner411、请求预算285、工具/Registry最终658项回归通过；适配器拆分残缺恢复66项，
综合适配器/预算93项通过。包装参数冲突防御也经过额外RED2后修复。

01:31Z进行同 Factory Director-only验证：前两任务复用交付且未再次调用模型。
第三任务660s逻辑超时，但同一物理HTTP在852020ms才返回200，随后Factory失败并
完成physical drain，晚到响应没有写入任何文件。尚未达到COMPLETED_VERIFIED，
别名修复仍缺新live effect证据。不能将200或局部回归绿算作项目完成。

下一步是KernelOne物理请求截止/取消与持久恢复边界的受控复现；不立即重复同样
付费请求，不盲目延长超时。PM/CE与既有产物保留，后续fresh全预算验收仍待完成。
具体账本：`.superpowers/sdd/2026-09-30-polaris-unattended-development/task-P01-physical-timeout-current.md`。

证据与设计：

- [请求预算覆盖修复](../blueprints/POLARIS_DIRECTOR_CALL_BUDGET_PARITY_20261001.md)
- [原生参数别名修复](../blueprints/POLARIS_WRITE_ARGUMENT_ASSIGNMENT_ALIAS_20261001.md)
- [本轮动态缺陷与结算证据](audits/director-budget-alias-defects-20261001.json)

下面的2026-09-30/早期2026-10-01证据保留为历史，不是当前总计。

2026-09-30 已修复执行授权与停止证据的确定性缺陷。2026-10-01 已启动独立服务器、
基础 dry-run 和真实 fresh L1-01；fresh 在 CE 遭 MiniMax Token Plan 额度耗尽阻断。
不能宣称无人值守完成、L1-L12 全绿或 `COMPLETED_VERIFIED`。

计划入口：

- [总方案](../blueprints/POLARIS_UNATTENDED_DEVELOPMENT_MASTER_PLAN_20260930.md)
- [实施计划](../../../../docs/superpowers/plans/2026-09-30-polaris-unattended-development.md)
- 工程 ledger：`.superpowers/sdd/2026-09-30-polaris-unattended-development/progress.md`

## 已实现的代码改变

### 1. 普通 DEO replay 不再重新授予执行权限

动态基线：两个真实进程同时调用 public claim，持久流只有一条 claim 转移，
两个调用却都收到 `effect_claimed` 和 grant。这证明重复授权，未证明历史重复物理写入。

修复唯一 owner `runtime.task_runtime`：普通 durable replay 只返回
`idempotent_replay`，没有 grant。当前调用的持久 append 丢 ACK / post-commit drift
继续通过 nonce-bound confirmation/reconciliation 恢复；不新增 claim 事实。

### 2. 禁止损坏回执自证成功

独立审查动态复现：首次真实 append 后回执 digest 被损坏，第二次 public
confirmation 不可用。旧 fallback 仅比较 event ID/sequence，随后拿回执与自身比较，
仍可返回 grant。路径/时间戳也有同类缺口。

修复：必须获得独立 public canonical receipt；确认不可用时 fail-closed，
不授权物理 effect。已经落盘的 operation 保留给既有 recovery，不能转成虚假成功。
覆盖正常回执及七个字段损坏与 outage 的组合，且普通后续 replay 仍无 grant。

代价：确认服务暂不可用可能使 operation 等待 recovery；安全优先于未经证实的立即派发。

### 3. 调试/审计读取实际执行源码

动态探针曾同时观察到：`inspect.getsource(class)` 包含已删除 grant helper，
运行 class 却没有该方法。根因是 package linecache 读冻结的 monolith，方法执行
`_repository_class.source`。现在 introspection 使用同一实际源码；旧镜像不再是 authority。
显式 helper imports 同时消除实际执行 source 的 Ruff F405 漏检噪声。

### 4. 修复停止决定及预算消费证据

- 自然耗尽与扩展拒绝有 `repair_round_budget_exhausted`。
- deadline、未取得诊断、verifier 通过均有明确原因；prepare/check deadline detail 完整保留。
- 每轮记录 `budget_admission`；summary 记录原有两轮扩展上限及实际消费。
- 在实际 repair 开始时登记 in-flight round；结果投影前发生 deadline 也不丢执行与 effect 证据。
- 执行前拒绝不消费轮次；末轮 diagnostics 有进展仍不能冒充 verifier PASS。
- 基础额度、扩展条件与非进展熔断未放宽。

### 5. 拆分后基线修复

恢复 stranded parametrization、遗漏 test helper、实际 clock injection；架构扫描纳入
实际执行 `.source`，跟踪现役 mixin/adapter owner，不恢复被删除 monofile shim。

架构扫描保留唯一已声明的 owner-local `_late_bindings.append_fact_event` 动态注入 seam，
并按精确 tuple 审计；新增未知 writer/sentinel 仍拒绝。该 Python in-process 注入是明确
信任边界，不宣称为不可变 capability。

### 6. 目录 capability 与精确 effect target 正确下沉

受影响 cascade 动态触发两个真实物理写入失败；`pytest --pdb` 检查完整 bound
authorization 为 `capability_scope=('src/',)`，实际拒绝原因为
`Changed files exceed act.files scope: ['src/a.py']`。根因不是模型：目录 capability
被直接当成 exact-file WriteGate 的 act.files。

修复 adapter：从 immutable bound snapshot 获取 effect target，先用现役同 Cell
scope matcher 验证，再绑定物理 executor 的精确文件写权限。当前 effect 只能写该
target；同目录 sibling、scope 外目标、绝对/越界路径和 AGENTS 禁写仍拒绝。
dependency checks 保留原 capability；command/no-file-state 不伪造文件 target。
未修改全局 WriteGate，未放宽 QA、验收或 JobToken scope。

scope+integration+architecture 25 项通过；完整相关 adapter/receipt/settlement cascade
201 项通过，73.33s。两个原失败 physical receipt 集成场景均恢复成功。

## 当前验证

| 检查 | 当前证据 | 限制 |
| --- | --- | --- |
| TaskRuntime 全 owner + roles lifecycle | 893 passed，261.26s，10 fork warnings | 不是 fresh Bench；fork 警告仍需后续测试工具治理 |
| workspace quality + deadline + 新 stop evidence | 152 passed，64.06s | cancellation / 持久 cursor 尚未关闭 |
| corruption/outage + ACK-loss | 10 passed | 验证 grant/receipt，不宣称所有外部 effect exactly-once |
| 实际 `.source` 与改动 production Ruff | PASS | 不代表整个仓库 lint 绿 |
| Mypy wrapper + 新 stop evidence 测试 | 2 files PASS | exec-loaded source 未宣称完整静态类型覆盖 |
| 受影响 DEO/settlement 集成 cascade | 201 passed，73.33s | scratch 真实物理写入，不是 fresh 项目完成 |
| fresh Provider / Bench / 浏览器 | 2026-10-01 已运行，L1-01 0/1 | MiniMax429/2067额度阻断；Director/QA未到达 |

## 2026-10-01 最新部署与验证

- 控制实例：`unattended-local-20261001-control`，前端 `http://127.0.0.1:5174/`，backend49978。
- fresh 项目实例：`factory-bench-unattended-l1-01-20261001-fresh-run-09c333642ee9-a9691ec7c4da30d7`，前端5175/backend49979。
- 两者 workspace 独立，runtime 均为各自 `<workspace>/.polaris/runtime`；无 reload，main49977/5173未动。
- Playwright 验证控制页 API/WS 真实指向49978并携带正确 workspace，无49977请求及页面错误；identity/health200。
- 基础L1-01 dry-run通过；pre-fresh module cascade9/9通过。M05曾失败2项，已动态 PDB 定位并修复。

M05 根因：从未 seal 的空 DEO parent，没有任何合法执行权限，却无法以 failed/suspended
安全终结。新增极窄失败关闭：确认 child facts 为空或真正不存在、当前 registry 无 seal/ready，
再用既有 strict/fsync expected-sequence CAS 写 outcome-bound零效果证明。completed、sealed未ready、
未知/历史 child facts 仍拒绝。物理存在性只消费 FactStream public query 的错误证据，不跨 Cell
直接读存储、不自动 enroll、不伪造 receipt。

新分支15项覆盖两处 seal race、已有矛盾 child facts、伪造证明、持久后丢ACK、重建 service恢复、
成功完成拒绝。FactStream public157项通过，精确 writer owner guard通过，M05完整71项通过；
独立审查在 canonical append-only/owner 边界内未发现具体安全绕过。任意并发恶意目录替换未认证，
actor文本不充当授权，整个P04仍有后续证明义务。

fresh Factory `factory_03f36dec6eb1` 实跑44.7s，backend fingerprint
`7d902b5fc26cfbaf` fresh、无漂移。PM合同经显式确定性路径生成，但PM物理LLM请求同样429/2067失败；
CE随后也被额度拒绝，0source，Director/QA未执行，可运行率0/1。不是预算5400s不足或模型质量结论。

最终 provider-native wire 已读：PM身份正确2198估算tokens，route probe不应有工具；CE身份正确6120
tokens，PM合同/targets存在，`submit_structured_role_output`被明确要求，coverage通过。
两个实际 `/v2/context/{ref}/final-request`均返回200；没有用messages-only投影替代wire。

完整证据已归档至 `~/.polaris/audit_archives/unattended-foundation-20261001/fresh-l1-01-09c333642ee9`，
不依赖 `/tmp` 保留。详细缺陷与hash见
[额度阻断记录](audits/fresh-l1-01-20261001-r01-provider-quota.json)。

当前等待操作者恢复MiniMax额度，或明确授权另一个模型绑定。不得自动购买、启用积分自动消耗、
换模型或盲重跑。解阻后优先同run从chief_engineer_review续跑、保留PM；恢复证据与后续全链fresh资格分开。

### 2026-10-01 MiMo 授权切换与网络/页面测试修复

上段等待状态现由用户明确授权选择已有 MiMo Token Plan 绑定替代，不再要求恢复MiniMax。
PM/CE/Director/QA已绑定`mimo-v2.6-pro`，OpenAI Chat Completions路径，现有密钥保留；
隔离控制实例重启后已核对实际配置。其他角色未改。

动态基线不是MiMo断网：Windows/WSL TLS均可达；保存配置真实health通过。
Settings真实调用2.478s HTTP200、报告`1dd903e1` PASS，但页面47.58s仍running。
根因是测试事件全局subject与workspace-bound订阅映射不一致。修复统一到
`hp.runtime.<workspace-key>.llm.test.<id>`，并补HTTP启动前ID语法校验，避免接受不可订阅任务。
使用既有KernelOne根解析，不新增Cell、状态owner或轮询。

最终47项回归通过、Ruff/两production文件Mypy通过；独立审查问题已闭合。
真实Playwright点击MiMo测试后6.07s自动成功，收到start/suite_start/suite_result/
suite_complete/complete，workspace key一致，无reload或报告HTTP轮询。
详细记录：[MiMo连接及投递审计](audits/mimo-connectivity-20261001.json)。

当前Polaris入口`http://127.0.0.1:5174/`；5173实际被Starwave使用，未碰。
这只证明连接/结果投递；用户已要求进一步正常调用与深测通过。
下一步串行验证response/thinking/qualification/interview及工具能力，再核现役
isolated诊断Bench准入。重构全计划未完成，P02/P03与P04余下矩阵等仍保持开放。

### 2026-10-01 MiMo 深测与真实工具探针（不同证据等级）

串行真实深测完成：Director`0a871c28`、CE`48d90468`、PM`2b81037c`、QA`3bcd0b43`，
每角色connectivity/response/thinking/qualification/interview共8/8，合计32/32 PASS。
已读落盘报告核对实际model均为`mimo-v2.6-pro`。这证明正常调用与当前通用深测通过，
不是CE蓝图合同或Director真实工具/完整Factory项目资格。

真实工具小样本通过现役`python -m polaris.delivery.cli agentic-eval`启动，
run`b05c1476`在l1_directory_listing失败，678ms、0provider、0tool。
动态堆栈落在TransactionKernel身份入口：缺稳定execution_attempt_id/turn_request_id/
execution_id/task_runtime_session_id，旧run/task/session fallback被合法拒绝。
这是评测调用接线未适配新合同，不能按报告的repo_tree缺失症状归因给MiMo。
未改量具阈值、未跳安全门、未手改sandbox或目标项目；第二个编辑样本未运行。

旧文档`python -m polaris.delivery.cli.agentic_eval`无法执行，现役统一CLI帮助已实测；
不恢复legacy shim。详情：[MiMo深测与工具缺陷记录](audits/mimo-deep-qualification-20261001.json)。

本轮未启动新的Factory Bench。单项目isolated诊断不要求全计划封板，但下一步必须
先对齐合法调用入口的first-class identity、定点复测失败工具样本并刷新受影响准入，
不能把通用问答PASS替代工具/四支柱项目通过。全量L1-L12资格仍开放。

完整日志在 plan-owned `.superpowers/sdd/2026-09-30-polaris-unattended-development/`，
不在 `/tmp`。机器缺陷与候选状态见 `audits/defect-20260930-deo-claim-replay-duplicate-grant.json`
和 `audits/polaris-unattended-execution-status-20260930.json`。

## 独立审查与取舍

### 2026-10-01 Fresh MiMo L1-01 与 native-message 通用断点

先通过本轮 owner 集成门禁（389 passed）与 pre-bench cascade 9/9，再实际启动
5400s/6000s isolated L1-01，reporting off、单项目串行。run
`factory_9aa169804636` 原 fresh 结果 FAIL（684.7s）：PM 成功；CE 经一次有界
重构成功；Director 首次请求在发 HTTP 前被 `physical_wire_messages_drift` 拒绝。
没有源码落盘，四支柱未通过，不能称 MiMo 不可用或无人值守完成。

动态 exact-payload replay 证实：两套 OpenAI 消息归一化的操作顺序不一致，连续
mid-system 分组在资格化侧丢了两个标记（14 字符）。归一化收敛到 KernelOne，
adapter 薄消费；严格 wire equality 与真漂移 fail-closed 保留。5 个新测试先 RED；
修后相关组合 366 passed，M04 PASS，Ruff/Mypy 两生产文件 PASS。原始 frozen
请求经未改动的 B3.5 validator PASS；伪造内容仍拒绝。未手改任何目标代码。

本轮也恢复 physical gate 测试拆分损失的共享夹具、两个 decorator 与真实 patch
namespace；原 57 个测试完整保留，73 项门禁通过。stream-order 预期 metadata
通过 pytest 动态断点核对并增加 assembly 精确断言，不删证据或弱化断言。

下一步为 owned instance 同 run Director 续跑，PM/CE 不重跑。它是工程辅助验证，
不能覆盖或洗掉原 fresh FAIL。原始报告/上下文/阶段结果已保全到
`~/.polaris/audit_archives/unattended-mimo-20261001/r02-original/`。
记录：[native-message 缺陷清单](audits/defect-20261001-openai-native-message-drift.json)。
全计划与 L1-L12/N-batch 尚未封板。

同 run 续跑现已真实越过 wire 资格化：Director 请求
`5e00312ddc5947cbb375b096b82c3428`，最终物理快照
`ead2c81e423df57e2afd5c47`，MiMo HTTP 200、621449ms、29315 completion tokens。
返回 9 个 write_file native calls，但参数形状为 `file/package.json` 等单键映射，
部分路径键夹带 `</parameter`；缺合法 file/content 绑定，DEO 全部拒绝，0 effect。
后续修复请求 120s timeout；之后 sibling export coverage 因上游无落盘而被拒绝。
这不是连接不可用，也不是原 native message drift 再现；新工具协议断点尚未闭环。
原始 response 字节未在此次检查拿到，禁止据此盲目剥 XML 或扩宽路径/授权门。
记录：[MiMo native-write 断点](audits/defect-20261001-mimo-native-write-arguments.json)。

扩域验收如实保留：release gate 组件 18 passed；全 release suite 原结果
346 passed / 8 failed / 1 skipped。新增 session identity 测试的跨 Cell 私有导入
已移到 Kernel 消费者 owner，17 项定点测试及三个相关静态 fence 通过；其余全局
失败（旧 bench runner 拆分语法、环境 flag 登记、KFS direct-write 等）未关闭、
未以 baseline 更新掩盖，不能声称全仓绿。历史测试日志保留原路径；现役 identity
测试路径为 `roles/kernel/tests/test_role_runtime_session_identity.py`。

### 2026-10-01 P05 native-write 参数封装闭环（局部）

9 个原始参数字符串已按 raw/decoded SHA 完整复原；Invoker 证据明确
text_tool_parser_attempted=false，排除本次文本工具 fallback。真正的缺口是
`file/<path>` 单键写入封装未进入 snapshot-owned decoder，尾部参数协议片段
亦留在参数键里。现在只在 captured schema 恰好要求 file/content 两个 string
参数时解码这类单文件封装；正文不动，作用域/JobToken/DEO/receipt 门不变。

4 个正例先 RED；额外 required 字段反证又 RED，收窄为 exact required set。
组合回归 370 passed（两个历史 fork warning）；Ruff/Mypy PASS，M03/M04 PASS。
9/9 exact parser/normalizer 重放通过，9/9正文哈希未变、0模型调用、0目标手改。
真实 scratch 物理门验证：授权路径写入时 XML 原文保留；sibling、outside、
AGENTS、runtime 路径仍拒绝。现开始同 run Director-only retry，保留 PM/CE，
尚不能称项目通过。120s 修复预算、全 HTTP response 保全与全局 release 红项仍开放。

同 run 最新实测：Director call `23:11:37Z` HTTP200 /284854ms /19310 completion
tokens，9个write均成功且有authoritative effect receipt，TaskBoundary TASK-1达到
completed_verified并进入TASK-2。该批输入本身为canonical file/content，未触发新增
path-key decoder；不能把模型本轮参数改善独立归因该codec。codec的9个历史封装
输入仍以exact replay证明。Factory最终在23:51:13Z仍failed@director_dispatch，
保留已交付文件；本项目尚未COMPLETED_VERIFIED，禁止以9文件成功宣称整项目绿。

### 2026-10-01 P09 用户事件降噪

用户截图发现stage heartbeat反复占满最近8条，属于主动审计遗漏。采用已确认的
有界设计：纯保活从ContextOS用户事件选择中移除，且在截取窗口之前过滤；原始
遥测、租约事实、真实lastEventAt不变。错误/告警、阶段变迁、任务工具结果和交付
进展保留；没有在后端删除heartbeat，也没有增加产品轮询。

TDD先RED2，再GREEN；浏览器另发现“保活-only窗口等于模型未执行”的旧误判，
新增RED1后修为“本窗口暂无调用证据，不能据此断言未执行”。ContextOS全部296项
测试、前端tsc及build通过。真实5176/49980浏览器35秒收到401个WS帧（186保活），
界面0条心跳、无reload、无HTTP查询、无pageerror；真实Director错误仍可见。
CLAUDE.md与前端AGENTS已同步。记录：[UI降噪验收](audits/contextos-heartbeat-visibility-20261001.json)。
该UI子项关闭不代表P09或整平台封板，历史LLM初始观测缺口、修复预算与全局门禁仍开放。

一次 read-only 独立审查提出三项 Important：receipt 自证、deadline 漏原因、in-flight
扩展漏计。三项均由主 Agent 动态 RED/GREEN 修复，完整受影响 suite 再验；不以审查意见
代替平台证据。旧“原始回执 outage fallback 安全”裁决已明确撤回。

用户不建分支要求优先；代码留在主工作区。采用 owner suites 与受影响 cascade，
未认证无界全仓测试。read-only readiness 不因 heartbeat 的 lease 更新改变稳定 attempt
身份，且仍不能作为 mutation authority。unchanged-head cache 可复用，但真实 owner head
推进必须刷新；动态测试保留这个失效条件。

## 仍需执行，不得跳过

1. P04：补 serialized/new-process context、真实 effect-port 次数、过期旧 fence 晚到及完整 crash 矩阵；安全释放与业务完成分离。
2. P01/P03：取消出口、类型化停止/续跑决定、持久化 cursor、跨重启预算守恒。
3. P02：六轴 owner 读取期间发生 reopen/epoch/hash 变化的动态反证与一致性保护。
4. P05/P06/P08a：最终 provider request、工具表与 CE handoff、verifier 身份/诊断先通过。
5. P07/P08b：Director 只修失败 owner、成功 sibling 复用、QA 证据失效闭环。
6. P09-P12：实例/存储/观测、语言能力、调度及模块固化；最后 P13 fresh 单项目与 N-batch。

禁止因本文件单测绿就打开全量 Bench；准入由现役平台契约与完整 gate 决定。

## 经验教训

- 文件拆分不等于解耦；漏搬 decorator/helper、复制 shadow 源码会制造假失败和调试假象。
- 唯一 durable transition 不等于唯一执行 grant；必须检查真实 competing invocation 返回权限。
- ACK recovery 与普通 replay 不是同一权限语义。
- 回执与自身相等不是证明；要有独立 owner evidence。
- 目录 capability、PM 所有文件、单次 effect 的 exact target 是不同边界；不得互相代用。
- 先登记开始执行，再登记结果；否则早退出可能把已消费预算“洗掉”。
- 单测/静态门禁/恢复证明/fresh 无干预完成必须分别标记，不能互相替代。
