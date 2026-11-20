# Polaris 可靠工程执行系统交接

截至 2026-11-20 19:09 台北时间。接手目标是审查并收口现有 main 工作区、完成真实项目及独立资格评估。先读取本文件，再读取[实施蓝图](RELIABLE_ENGINEERING_EXECUTION_20261120.md)和[用户原始报告](../Polaris_可靠工程执行系统_完整报告.md)。

## 接手位置

- 主仓 `/home/dains/Documents/polaris`，分支 `main`，HEAD `d559f262fda5194524d594a11fd339fdab18be85`，领先 `origin/main` 27 个提交。本次工作保存在未提交的工作区。仅按 HEAD 重新 clone 会缺少本次实现。
- 交接时的变动及逐文件 SHA-256 已保存到[文件清单](/home/dains/.polaris/audits/reliable-engineering-20261120/handoff-inventory.json)。原始报告和 Windows 的 `Zone.Identifier` 在开工前已存在。按 diff 核实归属并保留其他人的工作。
- 审计目录 `/home/dains/.polaris/audits/reliable-engineering-20261120`。运行的最终 provider request 保存在各自 workspace 的 ContextOS。处理原始日志时保护物理请求头、配置中的密钥和实例 token。交付报告记录引用和摘要即可。
- 继承根 AGENTS.md、src/backend/AGENTS.md、src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md。直接在 main 开发，文本显式 UTF-8，shell 使用 RTK。先查询 CodeGraph；本轮 MCP 未提供，已用本地 CodeGraph 索引核查调用链。目标工程只读，平台缺陷回 Polaris owner 修复。

## 架构与剩余工程范围

| 层 | 已落地的位置与机制 | 接手后要证明的结果 |
|---|---|---|
| 0 基线、模块与级联 | `kernelone/platform_modules/registry.py`、`scripts/platform_modules/run_module_gates.py`。M11 为状态投影与条款，M12 为工程运行，M13 为内部评估。依赖生成顺序，变更路径选 owner 和传递消费者，未知路径扩大到全 registry。保留 M09 原四支柱。 | 在交付时的固定源码上重跑模块与全 runtime；测试报告记录 HEAD 和未提交 diff 身份。 |
| 1 生命周期 | `roles/runtime/public/engineering_service.py`、`internal/engineering_session.py`。单工程 owner、workspace 写锁、会话 SQLite；普通终态迁移封存，迟到事件只作对账，授权恢复原子写状态与事件。 | 冷读、并发验证、取消、重入与恢复均不能更改已封存的放行决定或重放未知效果。 |
| 1 验收证据 | `engineering_verification.py`、`engineering_acceptance.py`、`engineering_roles.py`、`engineering_tools.py`。检查关联候选、attempt、命令、契约、验证器和所声明的宿主环境。条款三值判定，QA 报告按 frame、turn、call、event 及真实观察定位。物理浏览器证据绑定实际入口和当前候选。 | 用户必跑命令保留；旧版本、假引用、错误入口、过期 QA、缺失或失败的必要条款均不能放行。独立检查需真正覆盖原需求。兼容单条款无矩阵的历史行为。 |
| 2 隔离、夹具和恢复 | 新增宿主 `verification_fixture_sha256`，复用 host PermissionScope、KFS、OS sandbox 与声明的验证输出。内部目录编译检查在临时副本隔离执行。中断写入可有界读回。 | 复测正例、EROFS、夹具替换/撤权、未知副作用。检查子进程及缓存的清理回执。 |
| 2 候选保留 | `engineering_checkpoint.py` 及原 Verifier 出口。宿主可开启 `retain_verified_candidate`。文件归档按内容寻址，SQLite 绑定已验证版本。CLI 按 run、可选 verification seq，导出到全新目录。 | 后续编辑后仍可导出旧候选原字节。该能力覆盖所授予范围内的文件，当前上限 4096 项、64 MiB；不完整或无法安全捕获时必须有明确原因。外部效果逐项对账。[操作说明](../../src/backend/docs/ENGINEERING_VERIFIED_CHECKPOINTS.md) |
| 3 ContextOS | `engineering_state_projection.py`、`engineering_loop.py`。Director 与 QA 从同一 SQLite 读事务取得当前状态和事件；影子 pack 保存目标、检查、预算、待对账调用和溢出原因。Director 的实际物理请求包含声明检查。 | 角色、工具 schema、检查及失败反馈以最终 provider body 核对。跨配置的任务质量非劣、关键事实保留和成本收益，仍要靠对照实验。 |
| 4 反例、预算与路由 | `engineering_lessons.py` 使用宿主固定的历史 run 和事件引用；按条件匹配，影子记录。QA 在剩余时间不足时预留报告机会，保留未知 verdict。真实引用支持有说明的原生调用 ID。可选 `engineering_qualified_routing.py` 只在既有授权 Director 绑定中选取。 | 核对原始角色、真实生效的模型/工具/预算及单独的全尝试账单。规则路由的启用需要同条件独立样本。拟学习路由属于取得校准与独立收益证据之后的研究项。[路由说明](../../src/backend/docs/ENGINEERING_QUALIFIED_ROUTING.md) |
| 5 独立实验 | `scripts/factory_bench/_bench_lib/{catalog_quality,reliability,comparison,routing_evidence,cli}.py`。区分平台放行、结构下限、完整契约独立标签、资源闭合；预注册 paired 请求绑定实际源码/宿主配置，过长运行通过同 run 重叠快照还原事件。支持 P95、费用、风险及非劣界、首次通过/至少一次/全部重复通过的描述统计。 | 完成真实固定留出集的两臂执行、独立评审、实付账单与盲评。用真实物理选模暴露归因，影子或同一实际绑定保留 UNKNOWN。[评估协议与命令](../../src/backend/scripts/factory_bench/PAIRED_EVALUATION.md) |

### 已确定的控制边界

生产链继续使用 runtime.v2、Nats-JetStream 和项目绑定的 `/v2/ws/runtime`。工程事实由 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3` 提供；ContextOS、页面、内部 Bench 和对照实验分别读取可验证投影。内部测试使用独立实例和端口。PM/Chief Engineer 按工作流调用，默认 Director，QA 按宿主要求执行。旧 Factory/ledger 读路径、独立 repair 工具以及模型天花板历史契约维持各自边界。

通用权限和版本不变量先于模型策略。工作区文件恢复仅恢复可证实的候选内容。可幂等外部操作要核对原请求/幂等键；不可确认的效果保留 UNKNOWN。检查通过与当前验证器/夹具环境的覆盖度分开判断。[事务决策](../../src/backend/docs/governance/decisions/adr-20261120-engineering-evidence-binding.md) · [验证卡](../../src/backend/docs/governance/templates/verification-cards/vc-20261120-engineering-evidence.yaml)

## 已有证据与未关闭项

| 范围 | 结果与边界 | 本机证据 |
|---|---|---|
| 模块门禁 | 原有级联基线 9/9；扩展注册后的 `--mode all` 13/13。后续长运行路由快照修正有专项测试，集成提交前需要同一最终源码上的复测。 | [基线](/home/dains/.polaris/audits/reliable-engineering-20261120/baseline-cascade.json) · [13 模块](/home/dains/.polaris/audits/reliable-engineering-20261120/frozen-all-modules.json) |
| 入口与浏览器 | HTTP/CLI 入口 80 通过；前端 3 文件 38 通过；真实 Chromium 48 通过，截图故障注入另有 3 项。检查曾发现并修复 CLI 进程内跨 workspace 环境残留、读回被终态门阻断及 Chromium 首帧截图失败。 | [入口](/home/dains/.polaris/audits/reliable-engineering-20261120/entry-final.xml) · [前端](/home/dains/.polaris/audits/reliable-engineering-20261120/frontend.log) · [Chromium](/home/dains/.polaris/audits/reliable-engineering-20261120/compositor-recheck.xml) |
| 全 runtime | 首轮 1381 项中 1367 通过、14 失败。失败项留有首证，相关根因已有修复和专项复测。交接时第二轮还在执行，日志中已有失败标记。期间源码发生变化，不能将该轮报成最终全绿。冷进程恢复 4 项已在稳定版本单独通过。 | [首轮](/home/dains/.polaris/audits/reliable-engineering-20261120/runtime.xml) · [第二轮实时日志](/home/dains/.polaris/audits/reliable-engineering-20261120/runtime-final.log) · [冷恢复专项](/home/dains/.polaris/audits/reliable-engineering-20261120/cold-final.log) |
| 内部评估器 | 一轮全目录 269 项通过。长运行重叠窗口的后续专项与 paired 测试 20 项通过。合成数据证明测量器按预期区分 PASS/FAIL/UNKNOWN。 | [Bench 测试](/home/dains/.polaris/audits/reliable-engineering-20261120/bench-tests-final.xml) · [归因修复](/home/dains/.polaris/audits/reliable-engineering-20261120/evaluation-p1-closure.json) |
| 原生最小交付 | MiniMax-M3.1-Flash。前两次为检查通过、QA 未放行。第三次 `374574b5eb9b458aa1d74ec8fb1eaa95` 获独立 QA，`completed / passed / approved / accepted`；另外读取文件确认 `Polaris` 加换行。首轮物理请求含完整必跑命令。40 个快照引用不等于 40 次计费请求；实付金额未知。三个尝试之间修过代码，不能算同条件成功率。 | [第一轮](/home/dains/.polaris/audits/reliable-engineering-20261120/native-cli/audit.json) · [第二轮](/home/dains/.polaris/audits/reliable-engineering-20261120/native-retest-1/audit.json) · [第三轮](/home/dains/.polaris/audits/reliable-engineering-20261120/native-retest-2/audit.json) |
| 目录资格 | 120 项 dry-run 的实际项目执行数为 0。当前 L1-04 已真实启动，handoff 时尚未结算。该例为调试样本。隐藏集通过率、误放行率、跨项目收益、预算下的模型资格均 OPEN。 | [缺陷索引](/home/dains/.polaris/audits/reliable-engineering-20261120/defects.json) · [静态门禁](/home/dains/.polaris/audits/reliable-engineering-20261120/static-final.json) |

## 下一步执行顺序

### 1. 只读接管现有运行，保留原身份

批次 `0e3a45ea0f954d489fae1402733f740e`，项目 `L1-04`，run `50112d8304d045ee98a4bc3916fd02f4`，实例 `engineering-0e3a45ea0f95-l1-04`。19:09 的 SQLite 观察为 `running / verification=not_run / workspace_revision=50`。实际工具已运行过编译和修复，正式 verifier 尚未据此放行。取当前事实时重新读取。

```bash
rtk proxy ps -p 52586,52587,52590 -o pid,ppid,etimes,stat,comm
rtk proxy jq '{records:[.records[]|{project_id,bench_gate,catalog_quality,resources_closed,status:.observation.harness_status,run:.observation.platform.run_id,cleanup:.cleanup.harness_status}],batch_error}' /home/dains/.polaris/audits/reliable-engineering-20261120/catalog-live/reports/0e3a45ea0f954d489fae1402733f740e/summary.json
rtk proxy tail -n 15 /home/dains/.polaris/audits/reliable-engineering-20261120/runtime-final.log
```

PID 可能复用，需结合 run、binding、workspace、已存在的 snapshots/operations 判断身份。原 wrapper 正常时它自行 observe、settle、stop 并写清理回执，无需第二观察器。若 wrapper 已退出且无终态，只使用原 binding 查询一次并对账，再按原 adapter 的资源闭合规则处理。该命令会追加观察档案：

```bash
rtk proxy env PYTHONPATH=src/backend .venv/bin/python -m polaris.delivery.cli.engineering_acceptance observe --internal-test --binding-file /home/dains/.polaris/audits/reliable-engineering-20261120/catalog-live/reports/0e3a45ea0f954d489fae1402733f740e/L1-04/binding.json
```

第一个必须拿到的结果是原 run 的终态、当前候选、平台验收、独立结构检查、`resources_closed` 和 cleanup。启动预算保持 5400/6000，现有实例继续使用自己的端口。第一批已留有空工具参数拒绝、真实 Go 编译错误及同一 Director 的后续修复，按对应 final provider request 和工具收据归因。

### 2. 给 L1-04 做真正独立的产物检查

已经在读取候选源码之前冻结 8 组场景，交接时执行 0/8。计划 SHA-256 为 `cde93aa334b3dc5088942e545577c0ee4d814b840c8e56a7d038eb2baf5e0332`。先核实资源闭合、记录实际最终文件清单与摘要，在测试目录创建独立副本。只在副本的沙箱中运行。场景覆盖启动帮助、两次学习、施法/拒绝/恢复和情绪、精力不足的原子性、三种动画、公用数值封顶、stdin/PTY/EOF、源码与测试。分别保留输入、输出、退出码、候选 SHA 和 PASS/FAIL/UNKNOWN。[冻结场景](/home/dains/.polaris/audits/reliable-engineering-20261120/l1-04-independent/frozen-scenarios.json) · [独立沙箱入口](/home/dains/.polaris/audits/reliable-engineering-20261120/l1-04-independent/sandbox_run.py)

### 3. 在一个固定版本上关闭工程回归

读取 `runtime-final.xml`（仍在运行时先看日志），从首个失败提取证据，先复测精确节点。特别核查冷进程验证器身份、QA 冷恢复及夹具/终态语义。若单测穿过了两份源码，固定版本后整体重跑。冻结目录下已有的首失败证据继续保留，新尝试另设路径。

```bash
rtk git status --short --branch
rtk git diff --check
rtk proxy .venv/bin/python -m pytest src/backend/polaris/cells/roles/runtime/tests -q --tb=short
rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --mode all
rtk proxy .venv/bin/python -m pytest src/backend/scripts/factory_bench -q --tb=short
rtk proxy .venv/bin/python -m pytest src/backend/polaris/delivery/http/tests/test_engineering_runs.py src/backend/polaris/tests/integration/delivery/routers/test_engineering_chat_entry_adapters.py src/backend/polaris/tests/unit/delivery/cli/test_engineering_cli_entry_adapters.py -q
rtk proxy node_modules/.bin/vitest run src/frontend/src/runtime/engineeringRunProjection.test.ts src/frontend/src/services/engineeringRunService.test.ts src/frontend/src/app/components/contextos/engineeringContextRecords.test.ts
```

运行 Ruff、format check、mypy，逐项核对变更的生产文件和测试，单独复核新 CLI export、权限收窄/撤回、路由证据重建以及只读副本的边界。模块门禁和整个 test 目录均应报出真实通过数。完成后才提交本次审查通过的代码到 main，并把新 commit、diff/证据摘要写入交付报告。当前没有本次提交或推送。

### 4. 按因果链关闭剩余缺陷

从用户目标到最终 provider body，核对身份、tools、tool_choice、response_format、目标和必跑检查。再核对归一化、host 授权、物理效果、SQL 观察、候选 revision、QA 条款、最终结算。每个缺口写清首失败、owner、修复、对应反例和同入口复测。实例仍有未决效果时维持 UNKNOWN，重新使用原 request key 查询。修改范围留在平台。

### 5. 预注册真实的独立评价，再决定默认策略

指定互不重叠的开发、校准、隐藏项目；固定项目顺序、两臂的源码/配置/实际模型、权限、预算、完整契约评审规程和独立评审者。运行前确定风险阈值、非劣界、样本数和停止规则。已用于调试的 L1-04 和最小 CLI 样例属于开发证据。预注册后从相同初始快照生成不同候选，冻结经验和资格文件，逐项目记录尝试、资源、所用绑定及实付账单。使用既有 `arm-identity`、`compare`、`repeatability` 命令，分别报告覆盖率、错误风险、有界成本、P95、首次/重试/重复表现；项目是独立计数单位。具体字段和 CLI 示例见[配对试验手册](../../src/backend/scripts/factory_bench/PAIRED_EVALUATION.md)。

只有实际暴露了所比较的策略、成本/标签齐全、独立风险与非劣界均达到预注册标准，才有资格调整默认值。保留失败或证据不足的模型配置结论、适用范围、复验触发条件与反例。诸如 32K、59 个全成功样本或学习路由均应按原报告的假设和任务类单独验证。

### 可直接发给接手 Agent

> 在原主仓 main 读取本交接和实施蓝图，保留所有未提交工作。先接管 L1-04 的原 run 和正在执行的 runtime 回归，逐项关闭真实失败，在已结算候选的独立副本执行冻结的 8 组 QA。固定同一源码完成模块、runtime、Bench、入口、前端投影和静态门禁，审查并提交到 main。最后按预注册的留出计划完成对照与成本核验。分别报告平台代码完成度、真实产物验收、资源结算及模型资格。使用原绑定和真实证据推进，所有未实测结论保留 UNKNOWN。
