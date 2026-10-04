# Polaris Paperclip 外部工程团队

状态：组织设计已批准；员工创建与执行验收以同日工程报告为准。

这不是 Polaris 产品子系统。Paperclip 只负责外部工程派工、汇报和审查；不能成为 Factory gate、Run Ledger、ReceiptStore、ContextOS 或 Bench 成功事实源。

## 组织与模型

复用现有 CEO，新建 25 名员工，共 26 名、最多四层。CTO 管理五个技术部门；质量与验收主管直接向 CEO 汇报，保持独立。所有员工统一 `codex_local`、`gpt-6.1-sol`、`modelReasoningEffort=high`；使用负责用户的既有 OpenAI 订阅，不复制凭据。

| key | 岗位 | 汇报给 | 责任 |
|---|---|---|---|
| ceo | CEO | 董事会 | 总目标、里程碑、预算、跨部门阻塞 |
| cto | CTO 技术总监 | CEO | 架构、任务优先级、互斥写入、跨 Cell 契约 |
| quality | 质量与验收主管 | CEO | 独立验收和发布否决 |
| foundation | 底座与安全主管 | CTO | KernelOne、安全、可靠性 |
| execution | 自动化执行主管 | CTO | 开发与修复闭环 |
| context | 模型与 ContextOS 主管 | CTO | Provider、上下文及最终请求 |
| frontend | 前端与体验主管 | CTO | 工作台、实时投影、观测体验 |
| delivery | 交付与运维主管 | CTO | 实例、CI/发布、知识治理 |
| fs | 文件系统与存储工程师 | 底座主管 | KFS、锁、路径、runtime 单根 |
| process | 进程与沙箱工程师 | 底座主管 | 进程树、取消、drain、验证沙箱 |
| events | 事件与实时通信工程师 | 底座主管 | FactStream、NATS、runtime.v2 |
| planning | PM/CE 合同与蓝图工程师 | 执行主管 | 合同、蓝图、接口和目标归属 |
| factory | Factory 编排与恢复工程师 | 执行主管 | 阶段恢复、依赖、settlement |
| tools | Director 工具执行工程师 | 执行主管 | 归一化、授权、实际 effect |
| repair | 修复内核与语言适配工程师 | 执行主管 | diagnostic、coverage、planner、receipt |
| ledger | TaskRuntime 与交付账本工程师 | 执行主管 | identity、lease、broker、Run Ledger |
| provider | Provider 与模型协议工程师 | 模型主管 | 请求、原生工具、流式与物理截止 |
| contextos | ContextOS 与上下文存储工程师 | 模型主管 | 装配、卫生、快照可读 |
| request | 最终请求与证据审计工程师 | 模型主管 | role/tools/schema/coverage 审计 |
| ui | 工作台与交互工程师 | 前端主管 | 角色页面、设置、恢复交互 |
| projection | 实时投影与可观测界面工程师 | 前端主管 | Launcher、ContextOS、WS 实时事实 |
| ops | 实例、CI 与发布工程师 | 运维主管 | 隔离实例、端口、源码冻结、发布 |
| knowledge | 架构图谱与知识治理工程师 | 运维主管 | 图谱、ADR、经验与交接 |
| bench | Bench 执行与根因调查员 | 质量主管 | 串行 isolated Bench 与动态归因 |
| regression | 回归、故障注入与多语言测试工程师 | 质量主管 | 原生回归、故障注入、语言及游戏工具链 |
| reviewer | 独立代码与安全审计员 | 质量主管 | 只读独立审查，不自修自验 |

## 配置与权限

- 全员 timer disabled、interval 0、默认 on-demand wake disabled、每员工并发 1。
- 不启用审批/沙箱绕过。独立审计员和 Bench 调查员默认实际 `read-only` 沙箱；Bench 启动需根 Agent 单独明确授权与可执行能力，不能把只读账号当已可启动实例。
- 全员不得招聘或创建技能；主管可分配任务，员工不可自派任务。主管职责以拆解/审查为主，不与员工抢写。
- 主工作区 `/home/dains/Documents/polaris`，`project_primary` + `shared_workspace` + `serialize`，禁 issue override，不创建分支/工作树，不自动启停服务。
- 组织层级与职责文字不是 OS 文件权限；每工单精确列 owned files、禁止文件、动态复现和验收。初始共享主仓串行，不能声称已实现三写者的细粒度互斥。
- 公司编制 26 不等于 26 个自动运行循环。先一个只读 pilot 验证真实模型/effort/cwd/sandbox，其他员工保持未启动。

## 工作与验收

失败必须动态 debug，完整检查最终请求、工具分派、真实 effects、verifier、TaskRuntime、账本/QA 和 settlement。生成项目只读，修 Polaris 通用机制；不降低门禁、不伪造回执、不 invent stub、不重启已有效 PM/CE。

每轮进展/失败形成缺陷清单和经验链；实现员工提交精确 diff 与命令结果，独立审计员复核，主 Agent 亲自验收。Paperclip `done`、员工结论和本配置报告均不是 Polaris 交付事实。

当前预置三个待办：r11 诊断/repair 路由、跨 owner 修复交接、materialization verifier 旁路；必须先动态复现和分配互斥文件，再开写入任务。它们不因员工创建而自动修复。

## 实施证据

计划：`docs/superpowers/plans/2026-10-04-paperclip-polaris-team.md`。

工程报告目录：`.superpowers/sdd/2026-10-04-paperclip-polaris-team/`。`acceptance.json` 验证 API 回读，`pilot-observation.json` 验证真实执行，浏览器报告验证页面；三个级别分开报告。

入口：http://127.0.0.1:3100/POL/ 。
