# Polaris engineering_v2 当前进展与边界

记录日期：2026-10-21。本记录区分历史交付、当前候选资格和本次真实运行。**无人值守 L1–L12 尚未证明；自主选择验证命令的全新 L1-01 尚未交付。**

## 已保留的真实结果

| 样本 | 原生运行 | 结果与限制 |
| --- | --- | --- |
| 原始 L1-01 首次隔离交付 | `4e26e6a9071e47ad95598bd6c6e3f16b` | `completed / passed / approved / accepted`，四个预设检查实际通过，资源关闭，零外部目标代修。冻结源码 `88524d4f8`，完整记录见下方链接。首次启动环境失败仍保留。 |
| 已有项目边界修复 | `de732ae2193242cf9aac631c6a9a3f8e` | 平台复现无效湿度输入、修复并执行负对照；95 项断言、四项检查与当前独立 QA 通过，资源关闭。 |
| L3-26 首候选 | `4345da9cb4d244e39b8417e6bbe38238` | 平台及 QA 曾接受，但外部只读审阅确认重复猎杀/能量结算 P1；保留原接受事实，独立产物质量轴失败，不计为盲验成功。 |
| L3-26 收到缺陷诊断后修复 | `fafb624f217c47bcba560e09940a81eb` | 一次外部只读诊断反馈后，由平台合法工具自行修复；82 项断言、负对照、四项检查和当前 QA 通过，资源关闭。零外部目标编辑，但有外部诊断，不宣称无反馈盲验成功。 |

[首个 L1-01 完整交付记录](POLARIS_ENGINEERING_V2_L101_R02_20261021.md)。其他样本的原始证据位于本文末统一审计根；不同源码和验收输入不合并成一个成功率。

## 自主选择验证的两次尝试

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

## 资源、观测与计量

- R02 canonical SQL：1205 events、133 工具记录全部 observed，113 `ok=true`、20 `ok=false`，pending 为空；run_ended 明确允许释放。
- 102 个 capture-dispatch ID：PM 4、CE 10、Director 88。实际 HTTP 总发送数、账单、人工分钟仍 UNKNOWN；不能用 Context 数替代。
- 原始 provider usage 字段分别报告：input 73299、output 44959、cache_read_input_tokens 3974109。缓存字段与归一化 cached_tokens 存在差异，不把差异解释成零缓存或零成本。
- 浏览器首试因外部观察脚本更改缓存根而找不到已安装二进制，3ms 失败，未进入页面/API/WS。保留该失败；显式绑定现有 Chromium 后，只读重试 17.07 秒通过：水位 248 后收到真实事件 249–259，当前 Context/final-request 双 200，无轮询/SSE/跨项目订阅。
- observe 返回 CANCELLED（exit 130），stop 返回 STOPPED（exit 0）；自有后端/前端退出，49978/5174 关闭；主实例 PID 18128 与 NATS PID 18035 保持。
- R02 全程未热改源码或模型。只读浏览器环境修正没有改变平台执行配置或目标文件。

## 当前唯一修改与下一步

R02 已封存并释放后，仅在既有 `roles.runtime` 收紧工具说明与反馈边界：明确现有输入（包括依赖和锁文件）只读；准备先使用原有授权普通工具；模型可调整自选验证配方，用户 required checks 保留；反馈投影实际绑定范围，缺失范围保持未知。Verifier 执行、权限、五个公开 DTO、SQL 状态 owner 和结果规则不变，不增加 npm 特例或重试控制器。

本记录写入时，该修改已有对应真实只读拒绝→合法准备→修订自选检查且保留用户检查→当前 QA 的合成模型实验：19 项相关回归及 5 项独立有限故障实验通过，类型与格式检查通过；尚待冻结候选正式检查与全新 R03。合成响应只替换远端模型，不能证明提示一定改善 MiniMax 判断。

下一步是这一个边界的有限验证、主线提交和全新隔离 R03。之后再完成默认入口/旧生产准入退役，逐步扩大冻结 L1–L12。不得把历史单项目成功、组件测试数或安全取消扩大成平台完成。所有后续研发直接在 `main`，不新建分支；旧冻结证据和无关未提交实验保留。

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
- `task7-verifier-preparation-feedback-{red,green}/`：当前新边界实验；完整真实 R03 尚未开始。
