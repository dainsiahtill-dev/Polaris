# 可靠工程执行系统实施与验收

依据《Polaris 可靠工程执行系统完整报告》第十二节，在 main 的现有 owner 内按以下证据链实施。

```text
HTTP / CLI → EngineeringRunService → 会话 SQLite 与真实工具观察
                                  → 当前候选验证与独立 QA
会话事件 → ContextOS 影子投影
内部评估 → 独立结果、覆盖率、风险与成本
变更路径 → 平台模块 registry → 消费者回归 → 四支柱 → 批次资格
```

| 阶段 | 实施边界 | 退出证据 |
|---|---|---|
| 0 基线 | 在现有 registry 标出当前运行时的实际文件与回归。未知路径扩大覆盖 | 可复现模块命令、依赖顺序、明确评估分母 |
| 1 控制与验收 | 在原会话事务和现有 QA 准入中验证必要证据 | 当前与陈旧证据、迟到回执、冲突状态的正反测试 |
| 2 验证与恢复 | 延用 host 权限、隔离命令、workspace revision 与显式恢复 | 真实隔离验证，可写构建输出，未知效果对账 |
| 3 ContextOS | 从现有工程事实生成可比较的影子投影 | 关键事实保留，最终请求绑定，任务结果与成本对照 |
| 4 修复与路由 | 优先复用现有反馈与预算策略；只按可观测的失败调整 | 冻结对照实验，按任务类别记录效果 |
| 5 资格 | 在内部评估中分开记录平台放行与独立核验 | 固定样本、留出项目、完整分母与成本 |

模块回归验证代码性质。独立运行、同候选 QA、冻结对照实验和隐藏评估各自需要单独证据。四支柱与 N-batch 维持原口径。

## 已确定的架构决策

| 责任 | 所有者 | 决策 |
|---|---|---|
| 工程执行与生命周期 | `roles.runtime.public.engineering_service.EngineeringRunService` | HTTP 和 CLI 共用工作区锁和 host 权限。Director 默认执行。显式协作与独立 QA 使用同一运行身份。 |
| 控制与持久事实 | `EngineeringSessionStore` | SQLite 事务记录工具派发、观察、验证 attempt 和状态迁移。当前 run 的 terminal 决议封存；迟到回执可对账，普通更新无法重新准入。 |
| 检查与验收 | `EngineeringVerification`、`engineering_acceptance` | 检查绑定候选 revision、命令、契约、环境范围和验证器身份。必要条款使用 PASS、FAIL 或 UNKNOWN；批准需要可解析的当前证据与成功的宿主检查。 |
| 工作上下文 | `engineering_state_projection`、ContextOS | run 行与选定事件在同一 SQLite 读事务中取出。影子 pack 用于比较和审计。最终 provider request 用于检验模型真正收到的身份、命令、工具和预算。 |
| 候选保留与恢复 | 原 Verifier 与 host 导出入口 | 验证通过时可保留授权文件的内容寻址副本。导出使用独立新目录。恢复外部效果时查询或对账，无法确认的写入保持未知。 |
| 经验和模型选择 | 同一工程循环及宿主固定的资格记录 | 跨运行经验按不可变事件引用和适用条件取用。资格路由只在现有授权绑定中选取，对显示有独立通过与费用证据的任务类别生效。 |
| 资格实验 | 内部 Bench CLI | 固定源码、配置、候选与试验计划，独立标签和全尝试账单通过摘要绑定。单项目门禁、全目录通过率、对照试验结果分别出具证据。 |

### 一次执行的提交边界

1. 宿主把目标、必跑检查、权限 profile、预算与 deadline 绑定到运行。工具先归一化，再检查权限和实际路径。
2. 工具开始时先记派发。物理执行返回后再记观察。检查在同一候选与验证 attempt 上完成；期间 revision 变化使该检查失效。
3. QA 根据实际交付入口、源文件和已执行检查提交条款报告。会话库按 frame、turn、call 和观察事件定位收到的那一份报告。
4. 验收读取当前候选并形成结论。完成、取消、失败或中断之后，迟到观察仍可查询。显式恢复重新校验宿主政策；待对账效果阻止盲目重试。
5. 验证包、ContextOS 影子数据和独立实验的结果各自保留来源。外部评估只读观察目标工作区，编译探针使用受隔离的临时副本。

### 推广条件

模块门禁检验实现是否遵守控制规则。一次真实任务检验一组具体配置、输入与交付。要启用依赖经验的默认选模，或声称成本、质量、稳定性提升，还需完成预先冻结的独立留出项目、风险与非劣界评价，并核对真实费用。验收能力可以先部署，模型资格用实际数据签发。

候选快照及安全导出见 [已验证候选的保留与恢复](../../src/backend/docs/ENGINEERING_VERIFIED_CHECKPOINTS.md)。终态与证据的事务设计见 [工程证据绑定决策](../../src/backend/docs/governance/decisions/adr-20261120-engineering-evidence-binding.md)。

## 宿主固定独立验收夹具

HTTP 和 CLI 共用 `server.engineering_permission_profiles`。宿主可在选定 profile 的 `verification_fixture_sha256` 中填写 `{绝对文件路径: 独立审核时计算的 64 位小写 SHA-256}`。例如把只读脚本安装为 `/opt/polaris-qa/verify.py`，把 `/opt/polaris-qa` 加入该 profile 的 `read_roots`，把脚本摘要加入映射，并在启动任务的 `required_checks` 中明确调用 `python3 -B /opt/polaris-qa/verify.py`。宿主应同时固定检查读取的独立输入文件，并从受信任来源取得期望摘要。权限 profile 和声明的检查随原运行持久化。

每个固定文件须位于工作区之外、显式只读根之内。KFS 逐级打开路径并拒绝符号链接、多硬链接和特殊文件。每次校验最多读取单文件 16 MiB。实际执行前、结束后及读取当前验收时会核对内容；设备、inode、大小、mtime 和 ctime 用于识别执行期间或验收后的替换、改动及还原。无法读取、内容变化、权限撤回均阻止当前证据放行，既有观察保留。更新夹具时需更新宿主 profile 并发起新运行。历史未配置该项的 profile 继续使用原检查绑定。

固定摘要验证夹具文件身份。检查是否真正覆盖业务条款，以及脚本的间接依赖、解释器、网络服务和系统环境，仍需独立设计和验证。配置不会自动生成检查或授予读取权限。

## 已接入的入口与验收证据

| 入口 | 本次变化 | 可复查的反例 |
|---|---|---|
| 平台模块门禁 | registry 的依赖生成执行顺序。传入多个 `--changed-path` 时选择全部 owner 及传递消费者；未登记路径扩大到整个 registry。 | 修改决策状态投影必须同时测工程运行和内部评估。 |
| EngineeringRunService 与 SQLite | 工具派发、显式恢复、迟到回执和验证提交分别在原宿主权限内执行。校验开始与结束必须对应同一候选和 attempt。 | 两次运行同一命令，晚到的第一次结果不得替换第二次结果；原写入状态未知时允许只读对账。 |
| QA | 按 frame、turn、call、event 读取被选中的报告和真实条款证据；浏览器检查绑定宿主提供的实际入口。 | 捏造调用 ID、旧轮截图、错误入口、短片段匹配多个要求均不能形成有效批准。 |
| ContextOS | 从同一 SQLite 快照读取目标、当前检查、待对账调用和预算，生成可比较的影子上下文。Director 的实际请求携带声明的验证命令。 | 旧 revision 的通过记录不能进入新候选的验证状态。 |
| 跨运行经验与模型资格 | 宿主显式固定同条件历史失败引用，部署可选择有独立结果与费用依据的已授权 Director 绑定。 | 未绑定样本、过期策略或不完整账单不授予新资格。 |
| 内部评估 CLI | 分别输出平台放行、独立结构检查、清理和成对隐藏评估。完整契约判定使用单独冻结的评审和账单。 | 平台通过但结构检查失败时 Bench 门禁失败；未执行样本的费用和风险保持未知。 |

模型路由的宿主字段和匹配边界见 [工程模型资格配置](../../src/backend/docs/ENGINEERING_QUALIFIED_ROUTING.md)。固定计划、两臂身份、独立证据和统计假设见 [成对评估操作说明](../../src/backend/scripts/factory_bench/PAIRED_EVALUATION.md)。

### 复测

从主仓执行下列命令。第一个命令包含当前工程链的级联模块。第二个命令还覆盖登记为 OPEN 的模块，耗时取决于工作机。

```bash
rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --changed-path src/backend/polaris/cells/roles/runtime/internal/engineering_state_projection.py
rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --mode all
```

统计资格和开关默认值需要各自的独立证据。一次真实 CLI 运行只能核验其自身目标、候选、权限、模型和时限。内部目录的 dry-run 只核验选择范围。
