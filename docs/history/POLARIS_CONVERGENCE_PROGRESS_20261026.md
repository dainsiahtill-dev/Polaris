# Polaris 全仓收敛进展（2026-10-26）

状态：继续在 main 清理技术债务，尚未完成全仓资格审查或冻结。真实模型的新 Bench 运行数为0，原目录120项尚未重启；v28 L1-01目标目录仍不存在。历史FAILED／CANCELLED／UNKNOWN及原始失败记录均保留。

## 2026-10-27：当前验收与下一批

- **并行工作区事件：** 此后员工又未经协调将共享router测试文件切换为旧版来做in-place A/B；Root识别具体实验进程后仅向该实验进程发SIGINT，触发其finally恢复此前候选，未停止主服务。该A/B不能作为验收；员工停止实验后Root已重新执行原107项，全部通过，纳入指纹的源码和额外KFS底座前后未变，证据 `Root-fact-probe-lifespan-boundary/post-incident-1793044058652860187.json`。员工随后报告曾删除私有scratch副本并重新生成，原始数据是否完全复原仍未独立证明，作为重建件标记。记录 `Root-runtime-fixture-hot-swap-incident.json`。
- **运行时探针与相邻夹具联合回归通过。** 原14个因MagicMock Settings无法序列化而出错的节点改用隔离的真实Settings测试夹具；独立审查确认原14个测试及断言保留。Root重跑探针、FactStream与相邻集成共107项全部通过，纳入检查的源码指纹前后未变，探针两个文件Ruff、format、严格mypy通过。测试含认证/DI/模型等替身；此为指定组件验证，不证明完整生产运行或Bench交付。证据 `Root-fact-probe-lifespan-boundary/latest-independent.json`。
- **更广路由测试仅作待核实线索。** 员工筛查669节点中68失败、50初始化错误；50错误出现相同Mock Settings序列化异常，其他包括旧Factory 410与旧断言冲突。该次未固定所有输入，不得断言全部为既有问题或将事件计为独立根因；已纳入 `HTTP-ROUTER-SIBLING-REGRESSIONS`，后续按实际入口逐项复现与处理。
- **Factory 测试接口迁移仍未完成验收。** Hermes 将原文件33处替身改为核对实际run身份；Root在源码指纹稳定时独立重跑全部114节点：63通过、51失败，与原节点逐项状态一致。37个原 `unexpected keyword argument run` 失败现推进到 `dependency_preparation_requires_committed_ce_stage`；另外13个缺live cutoff port、1个 `recommended_owner` KeyError仍在。独立AST对照确认106个原测试函数及其566条断言保留。此结果证明接口偏差已跨越，不能声称该组通过或真实交付改进。证据 `Root-quality-check-1793043349248445564/report.json` 和对应原始XML；另派Hermes处理不重叠的三个run身份丢失替身，并只读调查旧CE依赖。
- **删除错误语义限定验收通过。** `LocalFileSystemAdapter.remove` 候选仅将所有OSError误吞改为只处理FileNotFoundError，保留既有missing_ok返回约定；独立审查实际验证正常删除、缺失以及注入PermissionError后原异常向KFS传播、文件不变。Root对固定源码独立复验47项通过，两个生产文件Ruff与严格mypy通过，测试文件format通过；原24个测试AST不变，测试文件仍有既存lint诊断、同口径未新增。验收证据 `Root-remove-ready-1793043760040784391/Root-acceptance.json`。该候选不解决KFS符号链接、并发竞争或原子写重放。
- **旧工具移动的部分效果已复现。** 在合成workspace使用真实旧 `AgentAccelToolExecutor.execute("apply_patch")` 与授权门禁，注入源文件删除PermissionError后，返回失败且 `effect_receipt.files_changed` 为空，但目标文件实际上已写入、源文件仍在；正常移动和只授权源文件时的拒绝对照均已执行。正常移动回执也仅列目标文件；是否影响新工程主循环尚未知。等待非重叠的Hermes执行修复与重新验收，证据 `Root-repo-move-effect-1793043684378962393/result.json`。
- **旧Scout网关黑名单旁路已独立复现。** 合成角色同列Scout与repo_rg白名单、并将repo_rg列入黑名单：真实旧RoleToolGateway直接拒绝repo_rg，Scout却通过嵌套执行两次repo_rg并返回合成内容。Root复跑时7份源码指纹未变；是否经当前EngineeringRunService可达未知。子调用是否计入turn预算仍为待确认合同，不臆称预算突破。证据 `scout-review-gateway-1793044717336349203/run-1793044751636855055/result.json`，已退回Hermes修正权威路径。
- **Scout测试文件截断事件已恢复。** 并行修改期间，Root观察到一份角色测试文件被暂时清空且旁有 `.mine`；先将该副本存入审计目录并核对原3个测试及断言，再要求员工恢复。恢复文件与保存副本的SHA一致，临时文件已移出源码目录。事件记录 `Root-scout-truncated-test-incident/incident.json`；Scout候选仍需冻结后复验。
- **Factory测试身份辅助函数迁移已核查，原失败保留。** Root与独立审查确认原34处、新3处均绑定对应run、原命令和测试断言保留，单一函数迁往已有共享测试辅助模块。Root固定源码复跑154节点：99通过、55失败，节点通过/失败状态与迁移前一致；其中37项仍受旧CE阶段依赖阻断。四文件Ruff/format通过；Root随后用 `mypy --shadow-file` 在不切换主仓源码下独立对比HEAD与当前：严格诊断均为90条，按路径与错误消息的多重集合一致，源码指纹未变；这说明相对HEAD未新增诊断，但这90条仍未解决，类型检查未通过。证据 `Root-factory-strict-delta-1793044730857444132/report.json`。证据 `Root-factory-shared-helper-1793044504476326310/Root-node-comparison.json`。
- **DEO静态化暂存候选已退回。** 独立审查发现当前10个生成模块均无法解析；此前通过记录早于最后一次改动，不适用于当前候选。剔除错误插入内容的内存审查还发现一处把类静态方法当模块函数导入，另有一处绑定语义待核实。未切入产品源码，需修复生成机制并重新验证，不以暂存报告算重构完成。
- **KFS并发边界须如实保留。** 受控线程将已打开的父目录移到工作区外后，现有guarded atomic写在移动后的目录发生效果，随后报告 `AtomicWriteEffectUnknownError`；这证明未知效果被识别，并不证明效果未越界。部署中攻击者是否具备这种目录操作权限尚未知。证据 `Root-kfs-design-review/controls.json`。

## 2026-10-27：KFS物理边界反例与优先修复

- Root和独立审查在项目私有合成workspace中实际复现：KFS逻辑runtime路径的叶symlink可读到工作区外文件，普通写／追加可修改外部文件；父目录symlink可让原子写在外部创建文件、删除操作删除外部文件。已有 `workspace_read_text` 静态检查会拒绝同一叶symlink，但受控真实线程在检查后替换叶路径，旧实现仍能读到外部；同文件系统硬链接也可让普通workspace读写触及外部inode。所有外部对象均是审计目录内的合成canary，未证明真实部署的攻击者具有创建链接能力，也未发现真实用户数据遭访问。原始实验 `Root-kfs-runtime-links/result.json`、`Root-kfs-runtime-symlink-audit/operations.json`、`threaded-race.json`、`hardlink.json`。
- Root以合成adapter先于效果抛 `TypeError`，发现逻辑原子写绕过adapter自行写盘并返回成功；独立审查还注入“实际rename后抛错”，观察到fallback第二次物理rename并声称成功。该故障为注入，不能说真实掉电已复现，但已证明旧机制不能安全推断无效果并重放。另对 `remove(missing_ok=True)` 注入PermissionError，旧adapter错误返回True而文件仍在；原FileNotFound的True／False现行约定需要保留或明确迁移。证据 `Root-kfs-typeerror-fallback/result.json`、`Root-kfs-runtime-symlink-audit/boundary-controls.json`。
- 第一偏差是logical `resolve_path` 经storage.layout做词法root检查，之后LocalFS普通open跟随链接；Scout anchor在故障注入KFS拒绝后还会用raw open兜底，必须一并关闭消费者绕过。`read_only_paths=True` 同样复现读取。独立审查跑既有guarded snapshot／managed atomic等17 PASS，说明现有底座可复用，不代表旧logical API已受保护。当前新工程LLM的native_request_capture确有runtime KFS读取消费者且另做内容hash校验；尚未证明实际工程run或用户数据受影响。
- 已将一名原处理技能隐式I/O的Hermes调整为优先准备KFS真实物理边界修复及直接Scout anchor消费者，原技能任务继续保留OPEN。目标是同时保持合法读写、当前项目runtime单根、真实独立的KERNELONE_HOME配置根、用户adapter授权和真实receipt语义，并以文件／目录symlink、hardlink、真实线程替换、效果未知和冷恢复做正反验证；不能只增加一次 `resolve` 检查或绕过旧adapter。 负责人原先提到“已有外置runtime opt-in”不符合当前源码：现写入解析拒绝外置runtime/RAM-disk，已更正，不能恢复双根或声称其已验证；见 `Root-kfs-design-review/root-storage-policy-correction.json`。源码切换将在相关员工验证窗口结束时由Root协调。`Root-current-defects.json` 分开记录物理包含、TypeError盲重放和删除伪成功这三个不同根因，避免把每个API症状重复计数。

## 2026-10-27：负责人复核新缺陷与后续派工

- **旧Scout读取范围丢失已真实复现。** 隔离合成workspace的父 `AgentAccelToolExecutor` 只允许读取 `allowed/`：直接 `repo_rg private` 被拒绝；同一父执行器 `scout_probe` 却通过未继承token的 `RegistryReadTool`／子执行器返回 `private/hidden.py` 的合成内容；该路径及查询还实际落入workspace级runtime Scout anchor，独立合成实验已确认真实旧 RoleContextGateway 会将该历史受限anchor注入system消息；实验未证明同一已认证role session或新工程入口，也未发送provider请求。证据 `.polaris/refactor-management/full-refactor-wave2-20261027-013741/Root-scout-executor-effects/scout-scope-result.json`。这是旧工具链事实；当前EngineeringTools固定工具列表没有Scout，不能声称新工程run已触发。Hermes正在修父权限经handler、Scout service到子读取器的传递，并验证合法读取、空权限和越界拒绝。
- **隐式技能写入与路径越界已真实复现。** 同一个旧执行器在 `allowed_tools` 明确为空时，仅构造即向 `.polaris/skills` 与 `.polaris/runtime/skills` 各写8个文件；`KERNELONE_SKILLS_ROOT=../escape-probe` 在隔离workspace外写8个合成文件。根因是构造、技能读取和默认安装路径的隐式副作用／未统一KFS边界。实验只使用项目私有审计workspace。证据 `Root-scout-executor-effects/result.json`；另一个Hermes正在准备消除隐式写并保留显式技能能力的方案，等待Scout权限候选验证后协调切换。
- **E2E探针入口修复完成限定验收。** Root以私有workspace和实际FastAPI lifespan、真实Auth／FactStream重验：未认证401且无登记，关闭E2E时404且无登记，合法两次请求均200并产生连续两条真实事实，隔离缺authority时503且事件文件不变。DI／服务装配、watchdog和NATS清理使用替身，不能称完整项目或模型验收。原5项probe断言30条全部保留；Root联合回归107节点93 PASS／14 ERROR，14个错误来自相邻test_runtime_v2.py同样的MagicMock Settings序列化根因，不另算14个缺陷，也不称整个回归绿色；已派Hermes迁移该相邻消费者。两个修改文件Ruff／format／显式严格mypy通过。原RED被员工覆盖一事留档，Root从只读原副本及原pytest配置独立复现5 ERROR。证据 `Root-fact-probe-lifespan-boundary/Root-acceptance.json`。
- **run身份测试接口限定验收。** 现有合成Factory authority port及其context consumer可传同一run id，默认旧行为保持；Root真实owner guard正例到达标注的下游替身，错run在下游前拒绝，71项旧测试通过。两个文件严格mypy基线／当前均有18条旧诊断，没有新增。原相邻triage测试被误删的fake已完整恢复；该旧test与真实TaskRuntime owner及当前triage策略的冲突仍OPEN。Root已独立复核原相邻测试恢复及71 PASS；另派Hermes一次性迁移同族33处旧callback，保留实际run身份和所有原断言，不能以单节点通过宣称整组完成。证据 `Root-review-quality-fixture/Root-acceptance.json`。
- **静态仓库架构债务已定位。** TaskRuntime public admit／claim／receipt等真实调用的repository通过 `exec` 加载6988行 `.source`，并伪造 `__file__` 和 `linecache` 给旧扫描器。独立只读审查在隔离workspace运行原始6个入口共10 PASS，含真实进程竞争和恢复反例；该基线不能证明迁移后安全。第四名Hermes正在准备普通Python类体与3个真实源码fence的迁移，源码切换需等其他读测结束，由Root协调；旧镜像、pickle兼容和真实新工程使用情况仍未知。证据 `Root-deo-static-audit/review.json`。
- **SCC审核纠正了误读。** 1条循环诊断是声明图中50成员、358条内部边组成的一个SCC，并非一条包含50步的调用路径。只移动 `roles.kernel -> roles.scout` 的测试专属声明可在合成图移走该成员，但实际KernelOne Scout handler仍直接调用Scout service，且该handler所在路径当前没有catalog owner；独立源码复核已找到 roles.kernel ToolGateway 经 AgentAccelToolExecutor／handler registry 实际间接调用Scout service 的链路，因此不能把这一依赖简单当成只有测试使用，也不能把单纯减图边当实际架构闭环。权限修复后继续核对真实owner/注册路径。证据 `employee-2-scc-facts/report.json`。

此阶段在main上进行，部分改动尚在员工实现中。最新249条诊断是此前扫描时的快照；真实代码与metadata变更后须重新扫描。所有局部验证均不等于平台整体重构完成或真实Bench交付。员工实施产生的模型费用及实际物理请求数未取得权威账单，保持未知。

## 2026-10-27 第二批：三项限定验收，下一债务已派工

本批基于 main `517230555e487e0883cff9429da2aab53ab92865`，真实 Hermes／MiniMax-M3.1-Flash 实施，Root 独立核对源码、原始结果及失败。整个项目仍未完成重构，未冻结，未恢复原120项 Bench。

- **旧 HTTP 测试合同与类型闭合。** 27个旧写入口场景改为已批准的410拒绝契约，原请求参数／变体逐一保持；其余原测试运行逻辑在准确fixture类型／Path输入归一化后相同，包括仍有效的cache-clear。85个原测试全部保留，没有改生产路由或恢复旧owner。Root联合正式工程入口正例、旧入口无效果及fixture回归：197 PASS；5个文件Ruff／format与显式pyproject严格mypy通过，检查期间对应源码无漂移。38条原类型诊断已关闭。员工原会话因工具迭代耗尽exit1、报告未交齐的事实保留；同一session仅恢复回收已有更宽cohort与报告，不把CLI最终自述当验收。证据 `Root-review-http/Root-acceptance.json`。
- **共享合成输入收敛。** CE纯library输入从test模块私有函数提取为同owner命名test-support模块，9个Factory直接消费者迁移；Factory使用`compileall .`、CE使用`compileall src`的真实语义差异保留。Root16组原／新数据和可变对象隔离、4组非法输入错误顺序／消息对照通过；原测试AST在导入／名称迁移归一化后均保留，私有carrier issuer函数内联相同typed tuple后AST与原版完全一致。12文件Ruff／format通过，新helper显式pyproject严格mypy通过，旧混合测试类型诊断63到62、无新增。Root17节点16 PASS／1原有mock签名失败，未把该失败隐藏；员工更宽Factory111失败基线、中间110／最终109失败原始结果保留，两个时序波动不计修复收益。证据 `Root-review-library/Root-acceptance.json`。仍导入私有issuer的债务继续OPEN，不能宣称对应catalog事件已全部减少。
- **后续两项已实际启动。** 共享输入验收后，新会话处理`_workspace_quality_commands(context, run=run)`与旧lambda不接受`run`的交接漂移；原RED复现，要求保留修后prepare／check顺序和run身份。HTTP验收及员工报告收尾后，新会话处理`test_runtime_fact_stream_probe.py`本地伪Settings造成的5个setupERROR，优先复用现有真实fixture／FactStream authority，不加字段补丁或伪receipt。见 `employee-3-quality-fixtures/`、`employee-1-fact-probe-fixtures/`。更宽HTTP原始回收为68 FAIL／544 PASS／55 ERROR，未作全面前后对照，仍是开放问题，不能仅因文件未改就声称全部已归因为历史问题。 该run-keyword员工随后工具迭代耗尽exit1，node1已绿但未交完整报告；其探针删除了相邻原test fake，Root已封存并恢复该原test的完整AST。新发现测试authority producer三处hardcoded run id，已批准同owner两函数参数化并恢复同一session继续生产者／消费者闭合；该家族仍未验收，不能把单node通过扩大为34处替身或52个旧失败已关闭。
- **ContextOS旧V1清理完成限定验收。** 私有V1实现无支持消费者，公开名原已绑定V2；删除千余行死实现，未增加metrics port／第二owner。Root检查enum／policy四个class原AST相同，全部历史公共别名仍为原V2对象，默认policy值保持；真实冷导入0个Cell，对实际旧facade注入ImportError能够如实传播。Root当前兼容／恢复／replay／原metric owner／既有fence联合104 PASS、3文件Ruff／format及2文件显式pyproject严格mypy通过，源码无漂移。原70节点检查的7条metadata／dataclass断言失败与XML保留，不能把当前V2接口与旧期望冲突直接抹除。Root删除models.py一条过期fence豁免，以正式generator仅更新kernelone.core现有descriptor，source hash／receipt匹配，其余114资产未变。员工承认曾两次热换共享main，Root已封存违规、核对最终恢复及无残留该ContextOS pytest；热换实验不作验收，源代码限定验收不抹掉操作违规。重复metric测试及新跨私有导入已撤回，仅保留4条必要公开兼容行为用例。证据 `Root-review-context-retirement/Root-acceptance.json`、`Root-worker-baseline-swap-incident.json`。

以上证据根为 `.polaris/refactor-management/full-refactor-wave2-20261027-013741/`。本批最新严格扫描输入前后无漂移：249条诊断（189跨私有、59依赖、1循环事件），manifest/catalog mismatch为0，未声明effect为0；这是当时输入的诊断快照，不是冻结候选资格。首次250条扫描曾发现新cold-import测试漏记process效果；Root只给该精确测试路径同步manifest/catalog的test-only声明，生产effects与其它YAML值不变，并重新生成kernelone.core pack；首次报告和修复后报告均保留。循环诊断包含50个Cell成员，不能解释成一条真实环路径或一个独立缺陷；已派只读SCC来源审计。其余私有边界、类型／功能失败、全仓回归与整体独立审查仍未闭合。外部Hermes费用未知，不能记零；主实例原PID/birth再次核对未变。源码仍未冻结，Bench未恢复，长期记忆功能不在范围内。

## 2026-10-27 负责人收尾验收

本轮三个接口机制已完成限定验收：

- **SLM owner交接**：Root实际发现并退回“显式model／base URL被旧warm gateway覆盖”和“producer TypeError被吞掉”，修后原反例GREEN；Root8文件Ruff／format／mypy通过、110 PASS／3可选依赖SKIP，冷导入0个Cell。具体配置仍由roles.kernel拥有，KernelOne只读typed协议，既有host装配；不证明真实模型质量或ContextOS优越性。见 `Root-review-employee-2/Root-acceptance.json`。
- **事实租约与fixture隔离**：canonical fact自己的期限在所有cache出口重新评估，不被session文件覆写；Root再复现并修复naive deadline读取异常，未知时间保留原事实状态。Root192 PASS及3文件静态通过。F2“跨public调用旧head”假设经独立publicOwner实验与Source set/reset核对未成立；人为把私有ContextVar跨写操作持有不是支持路径，未添加新cache／controller，不宣称任意并发无缺陷。见 `Root-review-fact-lease/Root-acceptance.json`。
- **旧CLI退役**：删除353行无实际生产消费者的冻结工具封装，保留所有原用户测试，仅移除治理扫描中该已删除模块的一项。员工后来新增的退役检查有错误扫描根、真空检查，修复又膨胀为重复scanner／HTTP fixture并引入跨Cell私有测试导入；Root撤回这个本轮新增文件，原始RED／改进记录全部归档。使用既有正式入口拒旧／合法推进和资源控制，Root46 PASS、原setup反例继续GREEN；canonical tool_execution的29份生产源码与派工前一致。见 `Root-review-retired-cli/Root-acceptance.json`。

公开SLM producer、必要测试入口已同步manifest/catalog，纠正roles.kernel文档把旧RoleExecutionKernel说成新运行必经owner的描述。3份descriptor经正式generator刷新（其它112份资产不变），随后仅为新cold-import子进程补精确test-only效果声明并再生roles.kernel（其它114份不变）；唯一已存在context pack就地同步，未虚构缺失pack，未扩大生产权限／效果。

当前稳定严格扫描仍为257条：197跨私有、59依赖、1循环事件，效果／schema／manifest mismatch为0。这些是诊断事件，不是独立缺陷数，也不是交付率。27旧HTTP合同失败、6TaskRuntime既有失败、28router类型诊断及全仓回归／SCC仍待实际关闭。源码未冻结、真实新Bench为0、120项目未恢复。员工违规stash／越界先改后批等操作记录完整保留，Root核对并行改动，不能据员工自述判合格。

证据根：`.polaris/refactor-management/hermes-20261026-212318-07f29/`，延续入口 `manager-state.json`。保护的主实例未重启。整体目标保持未完成。

## Hermes 委派重构与负责人验收（2026-10-26）

本轮按用户授权，由Codex负责拆任务、监督、独立验证与验收，3名真实Hermes员工使用MiniMax-M3.1-Flash并行实施。每人独立Hermes会话／缓存，在main互斥文件集合工作；员工报告不进入Polaris工程SQLite或其它产品事实源。实际派工与控制回执见 `.polaris/refactor-management/hermes-20261026-212318-07f29/manager-state.json`。

派工前在HEAD `0150b77f14d9fc56ca035ed3e96bca9849e87b10`及当时已有18份未提交声明改动上复跑严格扫描：257条（197跨私有、59依赖、1循环事件），manifest/catalog mismatch为0，扫描输入前后稳定。这是本轮起点，不能归功于本批员工，也不能代替后续修改后的资格审查。完整证据见该批次 `catalog-command.json`。

- **限定验收通过：共享合成HTTP provider夹具。** 共享实现从test模块迁出，5消费者改为明确资源上下文，关闭2条`__wrapped__`和1条假self类型错误。Root独立核18个旧test函数／192条断言及参数、默认值、装饰器保留，原endpoint类／PM数据AST迁移一致。第一候选曾在context进入前setup故障后留下真实线程与可连接端口，Root已封存RED并退回；第二候选由Root亲自复跑原反例，监听／线程均为0。Root独立7文件Ruff／format／mypy通过，38节点24 PASS／14既有retired失败。新增3条资源用例通过。结论仅限夹具接口与指定生命周期机制，完整消费者suite仍未通过；无真实付费模型或Bench交付证据。外部Hermes实施模型的费用另计，不能写成0。证据 `Root-review-employee-3/Root-acceptance.json`。
- **HTTP初始化仍在收尾。** 原67个setupERROR已消失，真实Settings／EngineeringRunService与私有SQLite创建得到验证；首轮最终90节点61 PASS／29 FAIL。27项仍断言旧writer行为，1项明确缺FactStream authority，另1项claim.success=false需核真实拒绝原因。实际sys.audit记录发现默认synthetic workspace_path="."仍会访问src/backend的legacy runtime，因此尚未接受整个fixture。已授权迁移2条过时默认路径期望和2条合法owner初始化，继续同一Hermes session修复，不放宽锁／权限、不恢复旧writer、不清理未知历史数据。
- **ContextOS SLM分层正在实施。** 已确认默认TieredSummarizer调用、Cell配置／gateway owner与bootstrap真实入口，原基线66 PASS／3 SKIP。批准由roles.kernel的公开producer向现有bootstrap装配提供窄KernelOne协议，保留自定义config／gateway、惰性模型连接和默认生产能力；源码、生产消费者与冷导入验证尚待交回。不能把scope提案当作实现完成，更不证明ContextOS优于历史上下文方案。

主后端／NATS／前端的原PID与birth已核对，未重启；Bench尚未启动。本轮剩余范围包括入口退役清理、原失败迁移、当前图谱／模块验证及全仓资格。模块通过不扩大为平台收敛或无人值守交付成功。

## 当前主线已核对

当前正式HTTP／UI／chat／CLI，以及取消／恢复共同进入已有EngineeringRunService。旧Factory、RoleRuntime及旧角色执行总控的写入口明确拒绝；新主循环仍复用KernelOne模型调用、最终native request捕获、ContextOS审计、KFS与进程沙箱。不能把仍可被trusted Python导入的旧类当成正式支持入口，也不能据此声称旧源码已全部删除。

ContextOS与简单历史上下文哪种更好，本轮没有对照结论。上述调用路径只证明已核对的底座复用与审计能力，不证明全部旧上下文策略都被新主循环消费，更不证明更可靠／更便宜。长期记忆未纳入本轮。

## 本轮真实闭合的边界

- **存在但损坏不再当缺失**：TaskRuntime复用KernelOne guarded regular snapshot，一次读取，损坏UTF8／JSON、不可读、非regular明确失败；真正缺失的数字外部alias仍能合法推进。默认旧读取路径不被全局替换。CRLF／CR真实CAS兼容已验。Root当前绑定／session／失败结算联合160 PASS，相关源码hash前后不变。
- **晚绑定不能丢执行身份**：新raw-row路径曾丢外层run，内层仍含真实run，导致失败任务不能释放资源。旧／新真实对照确认是本次producer回归；纯事件投影恢复已捕获的执行run，无session锁倒序、无Ledger跳过规则。原9个失败节点已逐个通过。失败任务可以closed／release=true，passed仍为false。
- **真实owner替代手写正例事实**：文件预写拒绝通过真实DEO、物理mutation、TurnLedger和canonical committer产生V3事实／receipt；跨Cell测试只消费命名test scenario与公共观察值。Root30 PASS，非作者独审30 PASS。非法范围的模块路径含明确synthetic policy输入，不证明生产preview会放行；另真实Gateway／PolicyGuard负控拒非法且字节不变、正控合法修改有实际receipt。故意伪造的负例不返回真实commit credit。
- **旧测试不复活旧写入口**：formal15保留身份、路径、防伪、异常与参数断言，fixture改为明确合成历史记录，经真实持久序列化／PM binding、cold read_only和显式CE技术工具检查。新配对测试先验证旧POST真实410且文件、SQL、设置、provider都无effects，再经当前正式Engineering HTTP使用真实write_file与实际Python文件内容断言验证，达accepted且同request重复保持同ID／deadline。Root联合54 PASS；模型仅ScriptedProvider，不算真实LLM或Bench成功。
- **测试本身的债务也清理**：publisher fixture保留真实TaskRuntime方法，只替换最底层NATS transport为record/no-send，不伪造published=true。随后移除35个unused绑定与1条多余noqa，修8处真实typing输入／守卫；39个测试名、签名、参数和旧断言保留。Root48 PASS，Ruff／format／mypy通过，另有非作者AST／动态消费者独审无发现。

此前存储／权限406 PASS＋1既有平台SKIP、空权限边界27／独审6、CFO109、HR311、FactStream公开历史183＋origin22、Source发现140与独立41输入等仍保留其原版本与范围。它们不相加成整仓覆盖，也不自动扩大成项目交付。

## 资源底座：限定范围独审已接受

进程模块曾真实复现空行／EOF混淆、500队列满后丢尾且不结束、真正TimeoutExpired未处理、父进程退出但子代仍持pipe／无pipe存活、停止与迟到wait的状态覆盖。候选复用现有KernelOne组协调：official spawn隔离并绑定group、唯一pipe reader／自然EOF、500有界背压与单cursor续传，保原同步communicate分支。

独审又发现drain阶段不能冒充已接受stop：信号被拒后自主exit2曾被错标CANCELLED。新候选以停止接受与终态提交线性化修正，另外修复真实Popen stdin.closed布尔property调用错误。作者联合180 PASS并保存XML；非作者原拒signal反例复跑转绿、独立15控制通过，Root已核180 XML与当前3源pins，接受该限定模块范围。UNKNOWN close／lookup连续性另由ExecutionFacade保留原owner句柄，真实同ID合法后续清理joint已进入该组；synthetic port测试不能独自证明物理清理。

进程证据只证明受控spawn组无活成员、leader reap与pipe EOF；harness／subreaper waitpid单列，不宣称产品全树reap或跨组逃逸containment。原被拒候选、竞态RED和所有首次失败保留。

## 后续收敛快照

上一阶段诊断快照为491条事件（205跨私有、219依赖、66效果、1循环事件，schema／test authority为0），不是491个独立缺陷，也不证明交付率。Root登记了30个此前漏归属的现有Cell路径，补齐Bootstrap实际24个装配依赖；没有扩大生产权限或cycle allowlist。新增历史夹具接口只返回公共CE结果／输入数据，QA与深度消费者Root联合57 PASS，旧HTTP准入没有恢复。

两个测试边界桶20／37处私有访问迁移已获非作者限定审阅，仍保留26／40个旧失败，不能把静态归零当整个suite通过；当前正对1条过时质量slice断言和第一组typing旧债逐项修复。workspace／projection首条真实反向引用已获Root197 PASS与非作者44 PASS限定接受；同当前catalog的源码对照证明只移除该真实边，元数据已同步且反向依赖保留，两Cell pack已刷新。完整范围仍未冻结，真实新Bench仍为0。

## 全仓仍未清零

历史完整严格扫描 `Root-after-owner-closures-current-hard-fail.json` 为741条事件：264跨私有引用、409依赖声明、67效果声明、1循环事件；schema及test authority错误为0，扫描输入前后未变。其中生产跨私有引用为0；依赖声明297处属生产、112处属测试，尚需逐项核对，不能只补声明换绿。事件不是独立缺陷数，净减少不等于交付能力提高。旧756／765／759／762快照保留，没有扩大baseline或cycle allowlist。

尚未完成：PM9与其余旧fixture的当前owner／历史边界迁移、剩余跨私有测试依赖、缺失声明／效果、完整SCC、必要backend／frontend／KernelOne回归、全候选独立审查与冻结、真实模型隔离L1-01零外部代修闭环、原120逐项运行及人工质量检验。不得将安全失败结算算成成功交付。

## 证据与下一步

私有证据根 `.polaris/audits/technical-debt-clearance-20261025/`：

- `Root-convergence-current-state.json`：可延续当前状态与开放问题。
- `Root-current-module-verification/latest-{binding,file-prewrite,formal}.json`：Root160／30／54实际命令、XML及输入pins。
- `factory-wq-bound-owner-fix/independent-binding-review/`、`late-binding-owner-audit/producer-fix/`：损坏行反例、strict修复、旧／新producer真对照。
- `file-prewrite-owner-test-fix/independent-review/`：真实owner与明确synthetic上游的适用边界。
- `current-vs-legacy-entry-reachability/`及其`formal15-e1/`：入口可达性、逐断言迁移、真实持久bytes和新入口推进。
- `Root-stage-fixture-cleanup/`：48、静态与非作者审阅。
- `process-group-stream-convergence/independent-review/`、`process-stop-cause-linearization-fix/`、`execution-facade-unknown-lookup/`：被拒版本／新候选／UNKNOWN引用连续性；当前限定接受见`process-stop-cause-linearization-fix/Root-acceptance.json`。
- `Root-owner-pack-refresh/`、`Root-test-owner-grants/`：精确资产与test-only声明；round4已刷新当前TR／Kernel／Adapters／RunLedger四份descriptor，输入hash和真实receipt核对，其他111份并集资产不变。不证明召回／交付，不扩大生产权限。

继续先关闭当前模块与相邻接口、完成全仓必要接受标准并冻结，再按唯一启动者串行跑原120项；每项由Root检查真实产物质量。主backend／NATS／frontend受保护，未重启、未改主workspace；源修改与临时模块测试不等于main服务已部署该版本。模型账单、真实provider质量与完整请求成本未在本轮量化，未知保留UNKNOWN。
