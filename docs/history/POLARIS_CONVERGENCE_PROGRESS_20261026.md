# Polaris 全仓收敛进展（2026-10-26）

状态：继续在 main 清理技术债务，尚未完成全仓资格审查或冻结。真实模型的新 Bench 运行数为0，原目录120项尚未重启；v28 L1-01目标目录仍不存在。历史FAILED／CANCELLED／UNKNOWN及原始失败记录均保留。

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

最新诊断为491条事件（205跨私有、219依赖、66效果、1循环事件，schema／test authority为0），不是491个独立缺陷，也不证明交付率。Root登记了30个此前漏归属的现有Cell路径，补齐Bootstrap实际24个装配依赖；没有扩大生产权限或cycle allowlist。新增历史夹具接口只返回公共CE结果／输入数据，QA与深度消费者Root联合57 PASS，旧HTTP准入没有恢复。

两个测试边界桶20／37处私有访问迁移已获非作者限定审阅，仍保留26／40个旧失败，不能把静态归零当整个suite通过；当前正对1条过时质量slice断言和第一组typing旧债逐项修复。workspace／projection首条真实反向引用已获Root197 PASS与非作者44 PASS限定接受；同当前catalog的源码对照证明只移除该真实边，元数据已同步且反向依赖保留，两Cell pack已刷新。完整范围仍未冻结，真实新Bench仍为0。

## 全仓仍未清零

最新完整严格扫描 `Root-after-owner-closures-current-hard-fail.json` 为741条事件：264跨私有引用、409依赖声明、67效果声明、1循环事件；schema及test authority错误为0，扫描输入前后未变。其中生产跨私有引用为0；依赖声明297处属生产、112处属测试，尚需逐项核对，不能只补声明换绿。事件不是独立缺陷数，净减少不等于交付能力提高。旧756／765／759／762快照保留，没有扩大baseline或cycle allowlist。

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
