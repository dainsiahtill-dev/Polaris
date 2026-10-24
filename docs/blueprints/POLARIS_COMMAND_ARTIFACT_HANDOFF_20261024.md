# 命令产物与独立QA视觉交接

2026-10-24。源码候选已实现；ROOT整候选554项相关回归、40项架构、KernelOne ALL十二阶段/51路径、目录159既有问题零新增及完整独审通过。模型判断收益和新冻结项目验收待证。不能据此称平台完成或升级旧结果。

## 为什么做

L1-10正式accepted，但QA没看到真实导出SVG。ROOT实际查看发现100%单一类别的彩色环消失，保留原生accepted与亲检FAIL。实际3秒模块反例还隔离出接口：readonly命令确实生成134字节SVG；下一调用文件消失、浏览器拒绝私有路径；同字节workspace SVG可渲染，空工具权限正确拒绝。夹具是普通矩形，未代修Bench。

## 当前机制

模型可选择`execute_command(artifacts=["report.svg"])`，把真正文件写入已有`POLARIS_SANDBOX_OUTPUT`。host沿KernelOne storage.layout分配唯一runtime/command-output/UUID资源；默认仍在workspace/.polaris。源码write_roots、网络、deadline和物理执行owner不变，control根仍隐藏只读。最多16文件、总导出1_000_000字节；导出上限不冒充硬磁盘配额。

物理进程及子进程drain后，KFS安全读取真实普通文件，拒绝路径穿越、链接、特殊、缺失与超限。host生成ArtifactObservation引用、原字节/hash和原输入摘要。原始字节、真实tool row/event、实际revision推进、捕获完成绑定在原sessions.sqlite3同一事务提交；失败回滚，原调用pending/UNKNOWN，保留scratch而不重放。正常清理在commit与drain后走KFS expected-identity删除；要求调用方独占父目录，不宣称抵御任意恶意host并发换根。

`browser_observe(artifact_ref=...)`从同一run已提交工具记录读取原始字节和原调用/候选绑定，重验当前权限与完整性。不会读同名workspace诱饵、任意control路径或模型提供的blob。真实JPEG继续走原图像journal、ContextOS最终provider请求和独立QA检查。重新渲染不能更新旧绑定；历史revisit仅保留原像素参考，不补当前候选验收。

不增加Cell、表、总账、第二controller或旧写链。临时目录是执行资源；原始blob只在原观察中，模型文本/公开投影只显示metadata。大保存文件以有界分块进入可信worker，避免单argv限制；返回的模型视觉输入是实际渲染图像。

## 已有证据和边界

证据根：`.polaris/audits/qa-artifact-handoff-20261024/`。

- `baseline/result.json`：真实Tools/K1/browser反例与正控，3.00秒、无模型调用。
- `impl-k1`和`review-k1`：真实RED及输出/drain/清理；最终47项Root实测通过。269相邻实施记录早于最后小调整，不当作最终整候选结果。
- `impl-journal`和`review-journal`：实际原字节/原调用同事务保存及冷查；28项Root实测、117项实施相邻记录。
- `impl-handoff`和`review-handoff`：真实Service.start、本地原生HTTP、OS命令、KFS、SQLite、保存字节浏览器、后续物理请求JPEG与QA/cold。当前分项60项和loop/recovery/events组66项；202浏览器相邻组始于最终原子改动之前，适用范围分列。

上述以本地script替换远程模型选择，证明传输/状态语义，不证明模型判断更好。之前夹具构造、注册、只读control与可选Pillow环境错误均保留，不算Bench尝试或已修生产缺陷。最终组合门禁记录在root-final与ROOT-acceptance.json；新冻结L2-11另报。

初版支持自包含静态SVG/HTML/PNG/JPEG；动态应用服务、完整资源bundle、长期进程、外置runtime执行资格和长期成本仍未证。workspace入口/web_root与历史比较保留，原Director修复与PM/CE协作不变。长期记忆没有引入。

真实模型是否因此更容易拒绝坏产物且保留健康产物，需要同模型/预算有限坏例正控；没有数据不宣称胜过旧方案、Codex或Claude。原120项仍串行冻结运行，每项结算后亲检。

设计与逐项计划分别在`docs/superpowers/specs/2026-10-24-qa-command-artifact-handoff-design.md`及`docs/superpowers/plans/2026-10-24-qa-command-artifact-handoff.md`。历史失败见[原L1-10报告](../history/POLARIS_ORIGINAL120_V16_L110_20261024.md)。


## 最终源码资格（2026-10-24）

Root在同一30文件指纹上实际执行554项/24文件相关回归、40项架构、KernelOne ALL十二阶段（suite51路径）、目录增量零新增；各检查期间源文件零变化。159条历史目录问题（128blocker/31high）未因本次通过被视作消失。Ruff/format和11源文件mypy通过。完整候选独立审阅APPROVED，无Critical/Important问题。

这关闭的是生产者、原始字节耐久提交、消费者、冷读/失效/权限及资源边界资格，不是模型收益或新项目交付。证据`.polaris/audits/qa-artifact-handoff-20261024/ROOT-acceptance.json`与`root-final/`保存实际argv、原始stdout/stderr、返回码和源码指纹。此前中间版本202/269等记录保留各自适用范围，不叠加为独立测试数。
