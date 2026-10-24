# 工程命令产物交给QA视觉审查：设计与有限验收

状态：源码实现、整候选真实相关回归/架构/KernelOne ALL/catalog及完整独审通过；模型收益与新冻结项目验收待证。2026-10-24，基线 main `4f1e367`。用户已授权自主完成通用、可逆工程选择；本次是已有QA视觉路径的缺口修复，不恢复旧合同或增加角色必经阶段。

## 问题与范围

L1-10正式接受，但QA无法把私有命令产出的SVG交给浏览器。ROOT实际查看后发现单一情绪100%时圆环消失。目标项目只读，缺陷与原生accepted均保留。当前实际3秒模块反例：只读命令生成134字节SVG并返回真实SHA；下一调用文件消失，浏览器拒绝该私有路径；原工作区同字节正常SVG可真实渲染，显式空工具权限仍拒绝。证据 `.polaris/audits/qa-artifact-handoff-20261024/baseline/result.json`。先前夹具失败单独保留，不计作产品失败。

目标是完成一个通用交接：授权命令生成的有限文件，在沙箱退出前成为可读取的真实输出；实际字节与来源存入现有工程工具观察；之后浏览器和QA能读取该保存字节、得到真实图像，冷读取不再执行原命令。源文件权限、工程身份、当前候选和未知效果语义保持有效。该模块不会自动修目标项目、决定QA结论、启动长期服务或增加新的任务owner。

本候选支持由现有KernelOne storage.layout解析的唯一runtime与有限、自包含静态SVG/HTML/PNG/JPEG产物。新临时目录固定在该runtime的command-output命名空间；默认是workspace/.polaris/runtime，显式外置根沿用原workspace隔离规则，不另猜路径。动态应用服务、静态bundle和跨调用进程句柄另行验证；不据此宣称GUI交互已全面覆盖。

## 方案比较与选择

1. 单次命令执行后立即浏览，只有图像：最小，但无法在重启后检查原始字节或绑定原调用，不足以关闭交接。
2. 引入旧SessionArtifactStore或managed-process写链：旧存储/压缩或另有journal/receipt/Run Ledger，不适合新工程owner。
3. **选择：KernelOne限定输出挂载＋现有ToolObservation保存原始字节＋浏览器消费同一工程记录。** 不另建产物总账或控制器。工程事实仍在sessions.sqlite3；临时目录只是执行资源，不是事实源。

## 状态与数据所有权

- EngineeringRunService仍持有run、角色、权限、当前候选、取消和恢复。
- KernelOne sandbox仅增加一个由host提供的可选捕获输出绑定。默认参数缺省时原argv/权限语义不变。绑定必须属于本workspace下已存在、无symlink的专用runtime输出目录；不是把.polaris开放给命令。
- 命令仍只看到原先的`POLARIS_SANDBOX_OUTPUT=/sandbox-output`。请求捕获时将一次性host目录映射到这个私有别名。workspace/.polaris继续隐藏只读，原write_roots原样检查，显式空不扩展为源码写权限。
- host按原有storage.layout/会话runtime根分配一次性目录，名称由host生成。KernelOne公共参数只能接收被验证的本地专用目录，任意host路径、其他workspace、root本身、symlink与特殊文件必须拒绝。
- 命令及子进程必须先完成或被既有物理owner drain，然后host经KFS按请求的相对文件名读取实际字节。拒绝路径穿越、symlink、外部hardlink、缺失、特殊文件与超限。不通过stdout提取所谓产物证明。
- 临时目录清理后，数据留在原工具观察中。新增轻量host事实字段/值对象可用于原始文件字节、媒体类型、SHA、显示名和不透明引用；模型只选择输出文件，不生成hash或合同。旧观察缺省为空，旧编码兼容须保留。
- 所有持久写入经KFS/现有SQLite，落在当前项目.polaris。export字节有上限；不得把“导出上限”声称为整个命令的硬磁盘配额。该区别与剩余风险必须写入实验结果。

## 模型交互与授权

`execute_command`可选择有限相对输出文件（最终字段名在ToolSpec迁移时统一，建议artifacts；同义output_files/capture_artifacts先归一化）。未选择时行为不变。返回实际exit code/stdout/stderr及host产生的产物引用，原始blob不灌入提示词或UI文本。

`browser_observe`新增可选保存产物引用，与workspace entry互斥；原entry路径与.polaris拒绝规则不放宽。引用必须在当前run已提交的工具观察中存在，通过原始字节完整性、workspace、当前host权限与候选检查；不接受模型提供的任意blob或control路径。图片由现有真正浏览器渲染，返回现有ImageObservation，仍走当前图像SQLite、物理provider request与独立QA流程。

文件捕获不意味着命令或项目成功。命令失败、缺失文件、过期产物、撤销权限、尚未提交和UNKNOWN分别报告，不得吞已解码调用。用户没有要求视觉时不新增全项目硬门禁；已启用且实际使用的QA视觉证据仍执行原有当前角色/当前候选/后续物理请求约束。

## 候选绑定、耐久与恢复

复用现有review/visual输入绑定和工程工具journal，避免第二套版本解释。产物记录必须绑定原tool call和当前workspace。生产期间源码变化不能被当作稳定候选证据；沿用现有前后输入比较/失效语义，保留实际字节但标明当前候选不适用，不能凭新hash把旧图升级。

保存原始字节和工具观察在同一耐久观察提交中发生；只有observed且完整的记录可被消费。冷启动从同一记录读取，不重放原命令。取消、超时、故障时不自动重新执行；既有进程drain/释放边界优先。提交前遗留临时文件不是已观察产物，不能推断成功；只能在确认无活跃writer后做资源清理。

## 实验、反例与停止条件

先写真实RED：readonly QA命令产生SVG，实际文件应在下一调用/冷打开后以同一字节供浏览器读取；现版本不能实现。夹具是普通几何SVG，不包含Bench业务实现。合法正控是已有workspace SVG可渲染、合法捕获后可推进；负控是空权限、跨项目/伪造引用、路径穿越、symlink/hardlink、超限、撤销、候选变化和未知命令效果不重放。

再实现生产者、持久化、消费者与冷读取整条路径。至少一个实验运行真实EngineeringRunService/loop、ToolSpec归一化、真实OS命令、实际KFS输出、SQLite冷打开、真实浏览器和实际最终请求图像编码。可用明确标注的scripted provider/本地HTTP替身测试调度与请求装配；它不证明模型能发现视觉缺陷。模型能力另用同模型同预算的有限坏/好产物对照，不以拒绝所有候选算收益。

何种结果推翻方案：合法readonly调用仍无法到达真实保存字节；浏览器读取的是workspace诱饵而非保存字节；权限被扩大；冷读重放命令；原始字节/当前候选绑定丢失；资源无法闭合。发生即回到对应owner，不增加旁路或次数掩盖。

达到以下条件即结束本主题、集成并开新冻结原L2-11：真实反例修后通过；相邻老路径、持久化/冷恢复与必要安全推进反例通过；独立审阅无阻断问题；后端要求的架构/KernelOne门禁与目录增量验证完成。新项目真实结果另报，模块通过不算Bench交付成功。不要重跑已经停止的SVG截图A/B，也不把长期经济性/所有角色组合研究加为本模块前置。

## 重要修改的九项说明

1. 用户问题：QA不能查看其真实命令产物，实际坏产物被接受。
2. 第一偏差：命令结果只保留文本，私有文件随沙箱消失。
3. owner：K1执行输出边界；EngineeringRunService/现有SQLite；现有浏览器与图像消费者。
4. 事实/假设/未知：丢失与静态拒绝有真实反例；补通可视通路能改善模型判断是假设；收益、成本及动态应用能力未证。
5. 最小复现：readonly命令生成SVG；下一调用看不到；同字节正常source正控可渲染。
6. 可证伪结果：上面的字节/权限/恢复/假候选/释放任一失败。
7. 机制变化：导出真正文件字节并沿唯一现有journal交给viewer，而非要求QA写源码。
8. 收益/副作用：实际当前产物可视、冷读推进；验证旧路径、拒绝/失效、体积、清理与运行耗时；不捏造费用。
9. 接受/停止：按有限生产交接实验与必要门禁集成，再开新冻结项目；不宣称平台整体完成。

## 实施前已确定的接口选择

独立审阅 `.polaris/audits/qa-artifact-handoff-20261024/design-review/report.json` approved，无需用户补充。Root决定如下，均为工程实现选择：

- KernelOne public `open_command_output_capture(workspace)` 分配唯一runtime/command-output/UUID临时资源；`build_workspace_sandbox_argv(..., capture_output_root=None)`仅接受此专用命名空间内真实空目录。原参数缺省完全不动。绑定/sandbox-output并非扩展源码write_roots。
- `ArtifactObservation(artifact_ref,name,media_type,data_base64,sha256,source_digest)`为host事实。引用artifact_加32位hex，原始文件最多16份，总解码字节不超过1_000_000；source_digest可为空表示当前候选绑定未知。模型不填写这些事实字段。
- ToolObservation.artifacts缺省为空，历史payload不增加空键；同一观察JSON保留原始字节。原schema/SQLite不加表。公共投影和模型文本只给引用/名称/媒体/体积/hash，不给原始base64。
- 模型字段确定为execute_command.artifacts（相对/sandbox-output文件名列表），browser_observe.artifact_ref（与entry/web_root互斥）。原Scope与真实命令结果保留；若捕获失败也不能隐去已执行命令的exit code/stdout/stderr。
- 沿用现有输入manifest的规范摘要计算source_digest。稳定生产输入才能作为当前候选来源；重画保存字节不更新原source_digest。绑定读取由当前EngineeringRunService/Verifier提供，捕获模块不另解释状态。
- 初版查看自包含静态文件；不承诺HTML外部资源bundle、动态服务或保留运行进程。已有普通workspace浏览路径保持不变。

最终实现补充：捕获完成绑定与真实tool观察/revision同一SQLite事务提交，未增加unmatched-prepared恢复门禁；失败回滚留下原工具pending/UNKNOWN与scratch，沿用原恢复规则。
