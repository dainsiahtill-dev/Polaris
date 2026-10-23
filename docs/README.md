# Polaris 文档阅读入口

最新更新：2026-10-23。

当前新增：[v9 L1-05运行与亲检](history/POLARIS_ORIGINAL120_V9_L105_20261023.md)已终态 cancelled／QA not_run／结果unknown，ROOT实际运行现成CLI10次、查看四张原SVG后判建议与区域不一致，质量FAIL；资源闭合。与v8合计5个不同项目已终态亲检、严格通过0、未启动115。[Director与独立QA交接](blueprints/POLARIS_DIRECTOR_QA_HANDOFF_20261023.md)七场景控制已执行，生产最小迁移已完成组件回归与独审，待新版本正式项目验证；不得据此声称新版本真实交付或费用收益。

当前原120项进展：[v8 四项运行与亲检报告](history/POLARIS_ORIGINAL120_V8_L101_L104_20261023.md)。冻结 `16c8dbf67a96c0960e323aba617d24209960629f` 已结束并亲检 L1-01 至 L1-04：3 项原生 accepted、1 项模型连接失败且未进入最终验证/QA；严格质量通过 0，四项资源均闭合，其余116项尚未在本批启动。真实公开交互暴露6个产品机制缺陷，未代修目标或改写原生事实。宿主工具链与实际沙箱环境的最小反例已验证；[显式工具链绑定设计](blueprints/POLARIS_HOST_TOOLCHAIN_BINDING_20261023.md)已完成 host、执行、Verifier、QA、持久化与恢复接口资格及一次真实Go编译组件验证，740项联合回归/K1 ALL/架构/目录增量和独立审查通过；尚无新版本项目交付或成本收益。

历史v7记录：[v7运行与亲检报告](history/POLARIS_ORIGINAL120_V7_NATIVE_20261023.md)。本批冻结 `f71eb109a` 实际尝试L1-01，**0项有效交付、其余119项未在本批启动**；因验证后反复重写预览形成失效循环而正式取消，QA未调用、ProjectOutcome unknown，原生资源闭合。ROOT亲检94张原图中的11张，质量FAIL；历史[v5](history/POLARIS_ORIGINAL120_V5_NATIVE_20261023.md)等失败不改写。[离线Web入口方案](blueprints/POLARIS_OFFLINE_WEB_ORIGIN_20261023.md)已完成ToolSpec→KFS→隔离浏览器→持久化/冷重访接口、346项相关／40项架构回归、KernelOne ALL及独立复审，尚未取得新冻结交付。[实施计划](superpowers/plans/2026-10-23-offline-web-origin.md)与[机制实验记录](experiments/POLARIS_FEATURE_EVALUATION_20261022.md)分别记录组件和原生证据。

此前特殊动画原生结果：[运行预算可见性 r06](experiments/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md)在冻结 `b41245103c47dcfae9dc7f4063861cc1ee9b1cac` 下完成 `7dd30e15505e42c098daf90602625d94`：原生 completed／passed／QA approved／accepted，ROOT task_success=yes、质量中等，车2分、其余五维3分；后齿轮传动和 reduced-motion 控件不一致两项缺陷保留。原生与 ROOT 已知资源闭合，无外部代修或代跑原生验收。17行机制只呈现原host deadline与准备时刻余量，不增停止规则；预算已送达并被注意，但 r05/r06 同时存在 T22 和随机输出差异，不能证明 H2 因果收益。401条工具观察含16失败、0pending；397 prepared／397 terminal 相位不是HTTP数，harness用量仅覆盖18观察，总用量／HTTP／账单／人工分钟仍 UNKNOWN。资格记录仍为588通过／1消费测试失败后相关9项通过，没有单次589通过；详见[蓝图](blueprints/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md)。

最新对照：[Director 交接说明实验](experiments/POLARIS_HANDOFF_SEMANTICS_20261022.md)完成 3 组真实请求比较；两臂均 3/3 继续工具诊断，未观察到转向验证／交回的收益，暂不合入该提示词。全量原始请求、先评分后解盲及独立核证已保存；没有执行工具，不构成项目交付或架构优胜证据。

最新底座改进：[工程事件按需读取](blueprints/POLARIS_ENGINEERING_EVENT_SELECTION_20261022.md)已完成同一 SQLite 内四处过滤迁移；532 项相关回归、KernelOne ALL 12 阶段及独立审查通过，严格目录历史 159 条保持。真实新接口与全读后过滤的局部对照输出一致，视觉读取与生成约 2.13→0.43 秒；这不是整个工程耗时或交付率提升的证明。r06 是结合 T22／T24 后的一次新交付，未单独隔离 T22 收益，原 r05 失败不改。

此前实测：冻结 `cb6bef15` 后，鹈鹕动画网页 r05 `4e04cb507e4d4ff3965c39ac32f14870` 达到原项目截止期限，权威结果 failed／verification not_run／QA not_called，交付失败；原实例与资源已关闭，9442 份源码及 690 份执行资产未变。主 Agent 亲自查看最终网页并操作：车轮转动、实际调速和暂停连续性在本次采样中成立，骑行操纵姿态仍有限；外部检查不能补成产品验收。完整取舍、负面结果与有限研究队列见[能力取舍与质量实验](experiments/POLARIS_FEATURE_EVALUATION_20261022.md)。

最新实现：[QA 只读执行与当前候选](experiments/POLARIS_QA_READONLY_EXECUTION_20261022.md)已接通现有工具与工程 owner，覆盖当前授权、候选版本及冷恢复。相关回归 509 passed，K1 ALL 十二阶段通过（368 passed、1 skipped）；独立源码审查未发现 P0/P1。严格目录审计的 159 条既存诊断与修改前完全相同，增量检查通过，旧债未清零。两臂真实模型比较未证验收收益；随后 r04 正式运行因历史图片累计触发本地预算拒绝，根检查也判骑行质量 FAIL。原始请求和资源均已封存，本次不构成动画网页交付成功，也未验证主实例运行时已加载新代码。

L1–L12 题库阅读入口：[120 项项目提示词全集](benchmarks/L1-L12项目提示词全集.md)与[项目说明与评测指南](benchmarks/L1-L12项目说明与评测指南.md)。本轮修订需求表达、领域机制和质量验收场景，保留原检查项；文案与组件验证不代表真实 Bench 已完成。

历史检查点：全新冻结原生网页 r03 `b713cc21a2bd4c33995f63851a6bfe6f` 完成真实 PM／CE／Director／QA 链，首轮 QA 拒绝后原 Director 自主修复，最终四项实际检查（含一项原定检查）与 44/44 smoke 通过、平台 accepted，资源闭合；9436 份源码／工作树文件和 687 份执行资产未变。但主 Agent 亲自检查实际画面与交互后仍判质量 FAIL：车轮近乎静止，调速倍率与标签不符，暂停后调速导致姿态跳变。完整实验及证据见[能力取舍与质量实验](experiments/POLARIS_FEATURE_EVALUATION_20261022.md)。不把平台接受扩大为动画网页交付成功。

[按模型上限执行的输出预算](blueprints/POLARIS_OUTPUT_LIMIT_POLICY_20261021.md)、空截断反馈和 UNKNOWN 显示修复已集成。上一运行 `eebef9650cb34189a0720740846a3245` 保留平台 accepted、根质量失败及整仓冻结失败；原零源码失败 `1be9405400f34a8aa5c3ff12cd692218` 也保留，不被 r03 覆盖。

[历史全特性盘点](history/POLARIS_ALL_FEATURES_INVENTORY_20261022.md)保留 174 条记录、21 类、176 份不可变来源；功能、机制、研究设想和当前接入分开。保留专业分工、执行反馈与现有可靠底座；额外预审、全量 DOM 转储未证收益，不新增必经流程。派发领取、上下文选择、独立计算和专业能力逐项实验，长期记忆暂不做。新工程尚未整体接回旧 TaskMarket／StateFirstContextOS／TurnEngine 链；[此前组成实验](blueprints/POLARIS_CAPABILITY_EXPERIMENTS_20261022.md)不能冒充新运行交付。

更早 `bef24b386` 网页 `a420e40bb7434b1e85bd50d2fbfadfcd` 的文本 QA approved 与 root 质量 FAIL 均保留。新数据变体 `fixtures/pelican_web_visual/` 沿用原功能检查及质量量表。长期记忆不做；静态 SVG 两例通过、其余八例未运行；原目录 L1-01/02/03 的 root 质量 FAIL、其余117项未运行不变。以下历史检查点只适用于当时版本。

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
