# Polaris engineering_v2 首个隔离 L1-01 交付记录

日期：2026-10-21。结论：**原始 v2 L1-01 首次完成全新隔离、零外部目标代修的完整交付及资源闭合**。证据范围限于下述版本、环境和项目；第 7 批整体、默认切换、第 8 批及 L1–L12 扩域仍未完成。

## 冻结版本与入口

- 源码：主线 `88524d4f869c3cf9214d00b1fa2ec453ac4908ab`；9366 个源文件在真实运行及释放后保持一致。
- 模型：各角色 MiniMax-M3；原模型配置 SHA256 `75cd934790d7af7ecc6a7bc90860007d34820f417ed9f894dbe8fbe7efe8244a` 未变；实际请求预算 360 秒，项目预算 5400 秒，外层预算 6000 秒。
- 环境：Linux/WSL2；显式 `KERNELONE_HOME=/home/dains/.polaris/services/main-system` 共享既有消息服务归属。项目 runtime 独立位于 `<workspace>/.polaris/runtime`。
- 正式入口：内部测试 CLI 经 Instance Supervisor/Registry 创建独立实例，再且仅一次向 `/v2/engineering/runs` 提交 `workflow=collaborative`，要求当前候选独立 QA。
- 任务：原 `projects_v2.json` 的 TypeScript 发光昆虫花园模拟器。catalog SHA256 `f8a1af935a9b58d49a099453180a73175abc4a6fedf10917c10cb92609771af3`；原始完整需求 SHA256 `3408e55eba3d5337254a587f39451ab60b08a310c1a5d218ea27387f4d51a8ef`。
- 本轮保留原产品、深度和行为验收要求；按已采用的新架构，不要求模型维护旧合同 hash、覆盖 ID、蓝图 JSON 或语义补丁协议。

## 全部尝试

| 尝试 | 真实结果 | 首偏差及处理 |
| --- | --- | --- |
| R01，独立目录与实例 `engineering-l1-01-20261021-r01` | `FAILED_LAUNCH`，尚未提交工程任务，runs/events/tool_calls 均为零 | CLI 调用环境未带共享系统 home，新后端查找了另一套 NATS authority。三个 Supervisor 子尝试同因失败，未计成三个独立缺陷。原日志保留；实例 stopped、租约释放、自有六个端口关闭、无遗留后端进程。 |
| R02，全新目录与实例 `engineering-l1-01-20261021-r02` | `completed / passed / approved / accepted`，随后 `STOPPED` | 仅在启动前显式绑定既有共享系统 home；源码、模型、原任务、检查与预算不变。普通工具、编译及入口错误由 Director 吸收反馈、自行修复。 |

没有重用 R01 目录、改写其失败事实、热改当前候选、外部修改目标、代装依赖、代跑目标验收、初始化目标 Git 或补造收据。

## R02 的交付事实

- Run：`4e26e6a9071e47ad95598bd6c6e3f16b`。
- Workspace：`/home/dains/.polaris/projects/engineering-l1-01-20261021-r02`。
- PM、CE、Director、QA 均实际运行。各自最终请求的角色、任务、工作区和完整原始需求已核对；CE 消费 PM 建议，Director 消费 PM/CE 建议。
- 产物：8 个生产 TS 文件、837 物理行，README、种子数据、4 个测试文件。月相影响亮度，花朵情绪影响目标与高度，湿度影响闪烁；CLI 实际推进状态并渲染 ASCII 帧。
- 依赖由 Director 的合法工具实际安装；写读、修改及执行均有同一 SQLite 的实际观察。

| 平台实际执行的最终检查 | 结果 |
| --- | --- |
| `npm run build` | exit 0 |
| `./node_modules/.bin/tsc --noEmit` | exit 0 |
| `npm test` | exit 0，77 条业务断言通过 |
| `npm start` | exit 0，实际运行花园模拟 |

早期命令即使外层 shell 返回零，其内部编译或入口失败仍保留；以上最终结论来自 host Verifier 的独立实际执行。

QA 仅读取与报告，没有写入或执行工具。其批准绑定 Verifier seq 734、workspace revision 59；QA start seq 735 与 end seq 938 的完整输入绑定一致。主 Agent 从 canonical SQLite 独立重算了 17 份当前产品文件 hash，全部同时匹配 Verifier 结果及 QA 输入。ProjectOutcome 的接受依据为 `independent_qa`；run_ended seq 940 明确允许释放。

真实运行的只读 Playwright 观察通过：统一 WebSocket 更新及当前 ContextOS 可读，浏览器没有控制运行或修改目标。模块验收使用的合成 provider 浏览器用例单独保存，不计为本次真实模型证据。

## 资源与量化

- 工程 run 耗时 **374.627 秒**；首个生产源码写入 `src/random.ts` 在 run 创建后 **40.417 秒**。该耗时不含实例启动和事后审计。
- canonical SQLite：940 个事件，112 次工具调用全部有观察；105 个工具结果 `ok=true`，7 个 `ok=false`。工具成功计数不等于产品检查成功次数。
- 完整 SQL 可辨识 **75 次 provider dispatch attempt**，150 个 prepared/terminal observation；**实际 HTTP 发送总数仍 UNKNOWN**，不能用快照数替代。
- Provider 报告的 usage 小计：input 85400、output 37473、total 122873 tokens；实际账单、人工介入分钟和成功交付成本仍 UNKNOWN。
- 公共压缩快照只保留部分调用细节，CLI 该次观测仅识别 15 次、coverage=unknown。上述 75 次来自完整同源 SQL，未把公共子集冒充总量。
- 实例 stopped；原后端 PID 71620、前端 PID 72217 已退出；49978/5174 端口关闭；canonical active_runs 和 pending_tool_calls 均为空。
- 主实例 PID 18128、Launcher 59977 及共享 NATS PID 18035 保持；NATS generation 未变。未停止或重启主服务。

## 已验证机制与限制

已证明当前循环能在一次真实项目中复用角色建议、从合法工具失败继续、根据编译和入口错误修改产物，再执行真实检查及当前候选独立 QA，提交结果并释放资源。这个结果不证明可重复成功率、经济性、任意故障恢复或 L1–L12 能力。

保留的非阻断限制：CLI 批量 ASCII 展示与简化队形；部分 README 校验描述强于实现；execute_command 的 schema 未暴露运行时 `[1,120]` 秒范围。两次缺少 file 参数的输出恰好触及 3000 output tokens，但原始 stop_reason 缺失，根因保留 UNKNOWN；模型后续分拆输出并继续成功。当前冻结候选未为这些观察增加规则或放宽验收。

下一步：保持本次版本、环境和验收输入冻结，完成已有项目修复和复杂协作的正式样本，再按计划切默认并退役旧生产准入，逐步扩到约定 L1–L12。实质源码变化另起批次。

## 证据位置

统一审计根：`~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/`。

- `task7-committed-candidate/`：冻结源码及模型指纹。
- `task7-l101-r01-startup-first-failure/`、`task7-l101-nats-owner-audit/`：R01 首失败、归因及关闭。
- `task7-l101-r02-frozen-launch.json`、`task7-l101-r02/`：冻结入口、正式请求、绑定及操作历史。
- `task7-l101-r02-final-observe-root/`：权威项目投影和真实检查观察。
- `task7-l101-r02-physical-observer/`：完整请求、失败反馈与当前 QA 绑定审计。
- `task7-l101-r02-browser/`、`task7-l101-r02-artifact-review/`：真实只读浏览器与产物审阅。
- `task7-l101-r02-stop-root/`、`task7-l101-r02-final-audit/`：停止收据、完整 SQL 量化、主 Agent 候选重算及资源／源码闭合。
