# Polaris 文档阅读入口

最新更新：2026-10-21。

当前检查点：冻结 `bef24b386` 的首个动画网页运行 `a420e40bb7434b1e85bd50d2fbfadfcd` 已原生 completed/passed、文本 QA approved，资源关闭；主 Agent 实际查看桌面/手机多时相画面后判质量 FAIL：腿脚与鸟身没有形成连贯骑行关系。旧批准与失败证据均保留。当前按[Director／QA 原生视觉闭环](blueprints/POLARIS_NATIVE_VISUAL_OBSERVATION_20261021.md)及[实施计划](superpowers/plans/2026-10-21-native-director-qa-vision.md)贯通真实图片、当前候选与独立验收；组件通过不代表新视觉网页已交付。新内部任务变体位于 `src/backend/scripts/factory_bench/fixtures/pelican_web_visual/`，沿用原功能检查和质量量表，未改原目录。长期记忆暂不做。静态SVG两例通过、其余八例未运行；原目录 L1-01/02/03 的 root 质量 FAIL、其余117项未运行保留。以下历史检查点只适用于当时版本。

1. [历史经验报告全文](history/历史经验报告.md)：十章、68 个案例、历史验证边界与来源；保留原 06:58 UTC 检查点，后续 R13 核对见新重构方案第 2 节。
2. [完整重构方案](blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md)：保留角色分工，删除模型合同维护，明确架构、删留、权限、恢复、验证与迁移。
3. [逐批实施计划](superpowers/plans/2026-10-20-polaris-role-agent-refactor.md)：具体文件、接口、八批任务、测试与切换条件。

重构方案已采用，第 1–6 批及第 7 批组件验证完成，冻结版本的原 L1-01 已完成全新隔离真实交付与资源闭合。第 8 批默认入口组件及内部 Bench 正式入口迁移已通过；新默认 L1-01 产物已 accepted，runner 退出确认仍在收尾。其余正式样本、L1–L12 全 120 项扩域及本轮主实例部署尚未完成。实施进度见计划中的检查点及[当前进展](history/POLARIS_ENGINEERING_V2_PROGRESS_20261021.md)。旧的《科学收敛与渐进重构方案》保留为讨论历史；本轮目标以新的完整重构方案为准。

最新真实结果：[新默认 L1-01 R01](history/POLARIS_ENGINEERING_V2_DIRECT_L101_R01_20261021.md) 已由 Director 主导、按需咨询 CE，完成平台独立 QA 和项目 accepted；原 STOP UNKNOWN／CLI exit1 保留，后来只读证明资源已关闭。正在修复退出确认 owner，尚不将本次 runner 或 L1–L12 标为完成。

历史报告的页面版位于 [Launcher](http://127.0.0.1:59977/launcher?view=foundation&topic=history-experience-report)，带行号来源与文档目录入口见[历史文档说明](history/README.md)。

2026-10-20 主线整合：第 1–3 批已验证提交及第 4 批当前实现全部合入 `main`；第 4 批仍待主 Agent 联合验证和独立审查。后续直接在主仓主线研发，不再新建开发分支。

2026-10-21：第 4 批联合验证与独立审查完成；当前进度以最新计划检查点为准。

2026-10-21：第 5 批统一入口完成有界验证。主 Agent 最终 243 项相邻测试、20 文件质量检查通过；独立真实完整应用／冷进程／CLI 共 40 项原候选检查和窄修后 4 项复验通过。当前尚无新引擎远端模型或 Bench 交付结论。

2026-10-21：第 6 批组件完成。后端主线程 379 项、前端最终 240 项相邻测试通过；KernelOne ALL 十二阶段通过（368 passed、1 skipped）。后端和前端独立审查的问题已修复复验，真实模型、浏览器与原 L1-01 完整交付仍属于第 7 批。

2026-10-21 第 7 批：主线 `a8b820a3d` 经 521 项联合回归及 KernelOne ALL 十二阶段（368 passed、1 skipped）冻结后，[模型自选验证 L1-01 R04](history/POLARIS_ENGINEERING_V2_L101_AUTOVERIFY_R04_20261021.md) 已由正式角色链真实交付并释放：104 项业务断言、八项实际检查、当前独立 QA 与 ProjectOutcome 一致，零外部代修。保留交付后静态审阅发现的三项 P2；原 R01 FAILED、R02 CANCELLED、R03 CANCELLED/UNKNOWN 不变。详见[最新进展与剩余边界](history/POLARIS_ENGINEERING_V2_PROGRESS_20261021.md)和[历史首个 L1-01 记录](history/POLARIS_ENGINEERING_V2_L101_R02_20261021.md)。默认入口切换、旧写路径退役与 L1–L12 尚未完成。全部本地分支提交已包含于 `main`，后续只在主线研发，不新建分支或 worktree；历史冻结副本及未提交实验保留。
