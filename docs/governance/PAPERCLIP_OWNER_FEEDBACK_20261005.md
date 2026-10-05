# Paperclip 项目组改进反馈：Polaris 26 人接入与真实任务

范围：本机 http://127.0.0.1:3100/POL/，创建组织、模型配置、只读试验和首次平台实施任务。此报告仅为外部工程反馈，不是 Polaris 产品事实源。

## 已完成的接入验收

- 复用 CEO、新建 25 员工，共 26；五个 CTO 技术部门加独立质量部门，最多四层。
- 全员实际 API 回读 `codex_local`、`gpt-6.1-sol`、`modelReasoningEffort=high`；心跳/自动唤醒关闭，单员工并发 1，禁止 sandbox bypass。
- 项目主仓 `/home/dains/Documents/polaris`，project_primary/shared_workspace/serialize，禁 issue override，不建分支。
- 只读 pilot `8f72065e-8722-469f-9b6b-d21174c73a68` 成功 exit0，约99秒；真实参数证明模型、高推理、read-only、主仓 cwd，6720个源码哈希不变。一次 python 名称缺失被真实记录，改用现有 python3 后继续；未安装依赖。
- 员工页面最终亲自浏览验收：HTTP200、26人可见、无页面异常；前面的冷加载失败完整保留。
- POL-2 已启动实际平台实现，run `3f459cc2-6c16-42e9-9c9a-5aae7ab259a9`；只授权两个诊断源码、一个新回归测试及报告。其阶段结果需单独验收，不因开跑而宣称完成。

## A. 已确认的行为与改进请求

### PC-01：默认并发与沙箱配置偏宽（P1，安全默认值）

证据：`packages/shared/src/constants.ts:77` 默认单员工并发20；`packages/adapters/codex-local/src/index.ts:14` 默认审批/沙箱绕过为true。接入前 CEO 真实配置也携带该绕过字段；当前公司已由我们显式收紧。

问题：同一份共享代码主仓，普通默认配置容易放大并发写和不可控副作用。不是漏洞利用证明，但不适合作为研发组织的统一默认。

建议：提供明确岗位预设（只读审计、主管、实施、测试）；默认并发1、保留沙箱，绕过必须单独明确选择。公司/项目并发预算与共享 workspace 策略应可见。

验收：最小创建请求不产生20路或默认bypass；审计员工不获写入模式；同一共享workspace两写者请求遵守配置的串行策略。

### PC-02：managed AI 安全配置扩展粒度不足（P1，能力缺口）

动态复现：对 CEO 仅添加 CodeGraph MCP 的非认证 `extraArgs=["-c", "mcp_servers.codegraph.command=...", ...]`，PATCH稳定返回422/code=`ai_connection_incompatible`。`server/src/services/ai-connection-runtime.ts:74-94` 按任意 `-c/--config` 一律拒绝。

问题：安全鉴权防线正确，但无凭据工具配置与认证覆盖没有一等分离，用户很难正确接入现有工具。

建议：保留对 model_provider、auth/secret/env override 的严格拒绝，增加受控 MCP/tool-settings 一等字段或受治理 connector；不要直接全局允许 `-c`。

本次安全处理：没有绕过鉴权或修改 Paperclip；使用受信任项目 `.codex/config.toml`，仅 CodeGraph stdio命令，无模型/凭据字段。独立空CODEX_HOME的 `codex mcp list --json` 和真正stdio initialize/tools/list均PASS。最新岗位指令说明项目trust与不可用降级；没有为此额外付费重跑模型。

验收：安全工具设置可保存、最终生效；auth覆盖仍拒绝；UI/运行证据显示实际工具来源与授权，不混淆全局/项目/隔离home。

### PC-03：隔离员工缺少统一工具链/Skill 就绪清单（P1，接入能力）

证据：pilot 隔离 CODEX_HOME 的日志只显示 Paperclip skill 注入；员工报告工具清单缺少 CodeGraph/Superpowers，且第一次 `python` 调用退出1，现有 `python3` 成功。

问题：主 Agent 机器上已安装的工具与技能不会自动等价于员工运行环境可用。创建成功、订阅连通和真实工作就绪是不同层次。

建议：增加声明式岗位要求及只读preflight：解释器/版本、MCP注册与实际tools/list、Skill文件可读、cwd、sandbox、模型/effort和项目绑定。Skill是指令文件，不应要求同名“工具”才能使用。缺失时返回精确路径/能力原因。

本次已补充现有 Superpowers/caveman 绝对路径、python3说明及CodeGraph开发配置；没有安装新软件或泄露凭据。

验收：无需真实付费请求即可验证基础工具；真实小任务还需单独资格验证；不得以MCP元数据存在冒充连接成功。

### PC-04：本机部署UI冷加载负担大（P2，已确认本地现象，根因待性能剖析）

证据：多次浏览器导航/渲染等待未完成；初始工程报告 HTTP200但ready=false、body为空、截图超时。后续成功观察共1369个响应，其中1031个 `/src/` 模块、48个Vite预构建依赖；最终无HTTP错误和页面异常，26人正常显示。

不能据此断言生产build同样慢、后端故障或网络根因。本机当前走Vite开发模块，冷/热、转换耗时与浏览器请求瀑布需进一步测量。

建议：正式本机部署优先构建产物；按路由懒加载，减少员工页加载无关全应用模块；提供可靠加载/错误界面；测量cold/warm可用时间，而不是只看首页200。

验收：保存相同26人cold/warm Playwright与HAR，首屏可交互，错误可解释；阈值根据团队测量制定，不拍脑袋调整超时。

### PC-05：URL短名碰撞改变人类展示名称（P2，已确认）

输入为不同中文名称“模型与 ContextOS 主管”和“ContextOS 与上下文存储工程师”；员工页/API后者被改为“ContextOS 与上下文存储工程师 2”，title仍是原名。

证据：`server/src/services/agents.ts:339-354` 的 `deduplicateAgentName` 使用规范化URL key检测碰撞并给name本身加后缀，create在914/950调用和持久化。稳定metadata identity未受损，当前仅展示瑕疵。

建议：分离 `displayName` 与唯一 `urlKey/slug`；slug去重不得静默改不同的人类名称。真正重复姓名需提示，不替用户猜员工身份。

验收：这两个中文姓名保持输入显示值，urlKey不同，关系/链接按稳定ID解析。

## B. 需要进一步验证，不冒充已发现缺陷

### PC-08：禁用唤醒后的 scheduled retry 重复空转（P1，已动态复现）

POL-2 首次实施被本次配置的600秒工程预算终止；此前已产生两个授权源码改动和一个回归测试，但无完整收尾报告。这是本次任务预算/收尾策略问题，不能据此说模型无能力，也不能说硬截止本身错误。

更明确的平台问题：终态 timed_out 后，on-demand wake=false 时，同一run约每30秒新增一条 `Scheduled retry suppressed because on-demand agent wakes are disabled`，retryAttempt持续为1、reason=`transient_failure`；16:50:41至16:54:11等连续事件已保全。没有新物理请求，但重复写日志/事件且未形成清晰blocked disposition。

定位：`server/src/modules/run-dispatch/domain/policy.ts:297`。建议禁用唤醒时使retry进入稳定blocked/withdrawn等待状态，只记录一次状态变化；恢复启用后必须重新检查旧任务、源码版本、scope和显式恢复意图，避免陈旧重试突然复活。保留原超时历史，不靠删除事件“降错误数”。

对工程长任务：保留总预算，但增加阶段checkpoint与准确residual/next_action，续跑仅未完成的验证/报告，不重复读完整规范或重写已验证代码。已有源码由root独立复测：91项聚焦测试、Ruff和mypy均通过。更广契约当前511项为505通过/6失败；精确旧源码SHA匹配的独立内存重放495项为489通过/相同6失败，没有新增失败签名；未宣称广域全绿。

两项源码修复已由root动态证明：3条通用原始诊断以前只保留2条（不同import被合并且JSON TS诊断退成generic），现在保留3条，TS5110的path/line/column正确。仍没有新增TS5110 executable修复、修复跨owner交接、闭合旧verifier旁路或完成fresh Bench。不要把本次代码进展写成这些后续目标已达成。

验收：disabled wake等待至少三个调度周期，仅一次blocked事件、无新物理调用；闭合工单不复活；显式恢复复用正确issue/版本、失败历史仍在；取消后进程实际退出。本次已观察process group无剩余成员及 `legacy.local_process_stopped`，没有把逻辑终态冒充drain。

### PC-06：工单领取与源码写入边界（P1，待故障注入）

reportsTo、岗位职责、工单checkout不等于OS文件授权；本次用了共享主仓serialize和明确文件清单，尚未验证文件级排他、同路径不同项目碰撞、过期lease继续写、被取消后晚到工具effect。

建议：提供可验收的 workspace/write lease、fence与取消后drain机制；能按issue限定文件范围，或明确隔离环境。测试两个工单同文件、相同cwd不同项目、取消期间慢edit。不要让提示词承担唯一权限防线。

### PC-07：运行成功与工程交付验收的默认关联（P1，配置/验收课题）

Paperclip已存在 `verified_delivery`、criteria、reviewerAgentIds/managerAgentIds契约，不能说它完全没有交付验收。此次 `run=succeeded/exit0` 只用于只读pilot，不能证明代码/build/test/项目完成。

建议：研发项目提供默认独立review模板，将源码版本/哈希、真实命令及退出码、修复后验证和验收者绑定；普通员工不能自批。确认已有verified_delivery是否覆盖这些实际边界，而不是再造第二个系统。

验收：退出0但未交代码、测试未跑、证据过期、verifier失败、员工自批，都不得得到“已验证交付”。

## 不归咎于 Paperclip 的事项

- 我们自己的工程配置客户端漏校验 canAssignTasks/完整workspace policy，由独立review复现、RED/GREEN修复（14/14测试）；不是Paperclip授权失效。
- root一次把public DTO的diagnostic_id当直接属性导致审计脚本失败；修正按public DTO投影读取，不是模型或Paperclip缺陷。
- 单次CodeGraphstdio提示版本可升级，不是工具连接失败；不自动升级。
- Polaris修复路由、任务owner和verifier旁路属于Polaris机制问题，不归Paperclip。
- 源码已有dirty、尚未验收的Bench、模型极限等不能从本轮只读pilot推断。

## 建议顺序

安全岗位预设/工具链preflight与受控配置优先；再做共享写入与取消故障注入、研发验收模板；UI性能和名称修正在独立小范围推进。不要为改进外部管理平台而改动Polaris的事实源。

所有原始工程报告在 `.superpowers/sdd/2026-10-04-paperclip-polaris-team/`；API/浏览器/真实run证据层次分开，凭据不进入此交接。
