# Polaris 全仓收敛进展（2026-10-26）

状态：继续在 main 清理技术债务，尚未完成全仓资格审查或冻结。真实模型的新 Bench 运行数为0，原目录120项尚未重启；v28 L1-01目标目录仍不存在。历史FAILED／CANCELLED／UNKNOWN及原始失败记录均保留。

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
