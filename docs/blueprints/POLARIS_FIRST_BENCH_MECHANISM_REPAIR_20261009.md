# POL-62 首轮机制修复

## POL-82：PM/CE 输出预算 60000（已审实现范围）

mode=implementation；owner=b121；独立验收=Root。用户明确将 PM 与 CE 默认
输出预算统一为 60000；其他角色仍为 4000，显式可信覆盖及 CE 部署环境覆盖保留。
源码依据为已审 PLAN.md / CODE_DESIGN.json / SOURCE_BASELINE.json；191 个
baseline hash 已逐一吻合。仅授权 19 个源码、测试、本文档路径及指定外部工程报告。

数据流：可信 context 中 canonical aliases/nested policy 解析一次 → pin llm_max_tokens
→ PM 初次/一次质量修订，CE portfolio/schema repair/semantic patch/reconstruction/fission
→ 原角色 runtime/kernel/preparer → 最终模型容量与窗口检查 → 原物理派发与审计。
预算共用 KernelOne policy；解析复用 roles.kernel public contract，禁止跨 Cell internal。
请求要求超过模型 output cap 或剩余窗口时，PM/CE 在 transport 前明确失败，禁止降档。
保留原 prompt 压缩、完整需求、角色身份、tool/schema、retry、receipt 和质量门禁。

CE admission 使用同一 resolved output 的 80 tokens/s floor；60000 对应 750 秒。
只推导无显式 timeout 的默认值；显式 context/env timeout 不改。以最终 allocated timeout
比较 floor，保留 Director/QA/finalization/safety 预留与原 TTL=timeout+30/heartbeat。
PM inherited timeout 与 fission timeout 的兼容性在报告中单独验证，不全角色加时。

Assumption register：PM prospective budget 未 pin；CE 独立 8192/task-scaled policy
与主预算分叉；executor 两处 clamp 可静默降档；admission 只检查 available stage。
证据为 CodeGraph 调用源、已审 source design 和本地动态 RED。Pre-mortem：最可能
误修为覆盖可信低预算、把 PM 8000 input policy 当 model window、放宽质量/身份门禁，
或仅检查 available stage 却给 600 秒 lease。验证先 RED 后 GREEN，使用实际本地 fake HTTP、
最终 request/snapshot 对账和超时/lease admission；不运行真实 Provider、Bench 或服务。
无新 public DTO/state owner/effect/graph edge；报告属于外部工程证据，非产品事实源。

2026-10-09 本轮交付状态：`blocked_scope_review`，Root 为补充授权和独立验收负责人。
授权范围内已统一 PM/CE 60000，保留可信低/高/嵌套覆盖；非流式实际 fake HTTP
证明 65536 容量发送 60000、32768 容量拒绝且零 wire、窗口不足拒绝且不删 system
身份消息。最终受影响回归 266 passed；18 Python 文件 Ruff/lint/format 通过。
CE 无显式 timeout 默认 750 秒，显式 600 或 deadline 裁剪到 600 均 BLOCK，
Director/QA/safety 预留及原 lease 身份断言保留。真实 Provider/Bench/Native 未运行。

两处授权外缺口禁止宣称完整闭环：
1. `kernelone/llm/engine/stream/executor.py` 的真实序列化流式探针仍把 CE 请求
   60000 静默降到 32768，需 Root 授权该执行器和对应 stream 测试。
2. `factory_stage_executor/_mixin_01.py` 的实际 PM entry 等待参数探针仍为默认
   600 秒（显式 820 仍保留），低于 60000 的 750 秒建模下限；需 Root 授权此文件。
Mypy 仍有 13 条既存区域诊断，catalog audit-only exit 0 不代表治理全绿：
59 条报告项及 1 条 manifest/catalog mismatch 未闭环。未捕获修前 type/catalog 执行，
不得据此宣称本轮通过；相关 metadata hash 与 191 基线保持一致。
完整请求、命令/退出码、before/after hash、动态探针和缺陷清单见指定
`planning-output-60000/REPORT.md` / `REPORT.json`，交 Root 独立审查。


## POL-71：完整候选与原生停止原因（实现待 Root 独立验收）

mode=implementation；owner=b121；Root 独立验收；单次 2400 秒，预留末段验证。
基线 HEAD、136 个 dirty 条目及授权文件 before 保存在
`pm-candidate-continuity-native-reason/BASELINE.json`；禁止改历史目标或运行事实。
Root 已确认 POL-70 真 released。本轮仅五个授权生产文件、三个测试和指定文档。

数据流：完整首次 response → 既有严格 syntax/shape parser → 原质量门禁 →
调用局部原文候选与诊断 → 既有一次完整 JSON 修订 → runtime/kernel/preparer →
Factory guard → fake HTTP wire → 原质量/coverage/verifier 门禁 → 合格任务登记。
严格 parser 返回合法任务时传原 response，不传 normalization/autofix 后摘要；
无合法候选仍给有界错误/excerpt，不 salvage。4000、child/grant/调用数保持原样。
最终请求容纳失败应显式阻断，不用裁剪候选、加预算或新 delta 协议补救。

初始与修订使用同一任务字段规范。projection 示例只对显式
execution_backend=projection_generate 且非空字符串 scenario_id 的 hint 展开；
普通 code_edit 不要求 projection，不增加 design_doc-only 限制。
等价 HTML 检查只读结构化 target_files 与同任务入口 verifier/脚本声明；
路径须是规范工作区相对文件，不把正文、scope 目录或任意 HTML basename 当入口。
实际 TASK-3 子目录 HTML+脚本及 entrypoint 行是正例；真实 tsconfig 缺失继续拒绝。
成功 invoker 复用 KernelOne ResponseNormalizer.extract_finish_reason，从 AIResponse.raw
只投影短标量 finish_reason 到现有 return/event metadata；缺失为 unknown。
不保留 raw body/headers/key，不把 physical completed 当 end_turn，不触发重试。

Assumption register：1400 输入裁剪在提示构建层；根 HTML 假缺失在 PM coverage；
stop reason 留存缺口在成功投影。以上由 Root 实际输入/physical/独立审计确认。
Pre-mortem：最可能误修为全文坏 JSON 获权、使用已 autofix 摘要、接受任意 HTML、
凭 usage 推断 stop 或修改预算。验证卡和逐项缺陷清单落在授权报告目录。
RED/GREEN：实际五任务与 Factory prompt、完整 wire/snapshot/身份/4000，
坏修订零任务、首次合格不重试、坏首答无 salvage、projection 条件、原生原因/未知/秘密。
随后复跑 POL-69 boundary、POL-67 identity、PM purea/pureb、quality polarity 和
受影响 invoker 组合；Ruff/format/Mypy。禁止 Provider/Bench/服务/API 写入。
Cell public contracts、state owner、effects、graph 边界不变；无需新增公共 schema。
`product_completed_verified=false`；源码验收不能保证 fresh L1-L12 交付。

2026-10-09 本轮实现证据：授权五个生产文件及三个测试已修复/新增，
完整候选仍走原 full JSON、一次修订与全量质量门禁。Root 的实际五任务 fixture
在真实 runtime/kernel/preparer/Factory guard 后，第二次 fake physical body 完整保留
原始 7938 字候选；任务 ID、scope、target、steps、acceptance、argv/cwd 未被摘要替代。
实际嵌套 HTML 正例只剩真实 tsconfig 缺失；修订仅在隔离内存 fixture 声明配置。
小窗口由原上下文门禁显式拒绝，未出第二次 wire；坏 JSON 前缀没有候选授权。
测试匿名角色能力与 fake transport 的 262144 窗口对齐仅存在于测试注册表，
生产模型配置、输出 4000、wallclock、grant 和同 child 身份均未改变。

RED：26 failed/13 passed（含实际 wire 的 1400 裁剪），另保留 HTML 与 unit RED。
GREEN：最终 PM boundary/identity/purea/native reason 230 passed；
purea/pureb/polarity/invoker characterization/receipt/binding 247 passed（组合有重叠）；
native reason 最终 16 passed，return/canonical event/lifecycle 原因一致且不泄漏 raw。
Ruff、format、Mypy（8 文件）及 diff check PASS。
额外 caller/Factory authority 组合为 295 passed、19 failed、1 setup error，不能称全绿：
缺失 helper/参数化 fixture、隔离环境无全局角色绑定在修前成功投影对照中保持相同
20 个失败签名。原组合另有旧测试 workspace="." 审计路径写盘被权限拒绝；
对照改在 fresh scratch cwd 执行，不改模型/鉴权或超出范围的测试。
这些未授权修复由 Root 决定另给精确工单。

固定报告与全部命令、退出码、before/after diff、最终 physical snapshot、原生事件
保存在 `.superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-candidate-continuity-native-reason/REPORT.md`
及 `REPORT.json`。Root 的 137 源码 hash 与本轮 138 dirty 文件 hash 的范围检查
未发现 allowlist 外变化，HEAD 不变。POL-70 历史停止原因仍未知，未补写。
未运行真实 Provider、Bench、服务或 Paperclip 写 API；外部报告不成为平台事实源。

## POL-69：PM 合同边界（修前设计）

mode=implementation；唯一源码修改者 b121；Root 独立验收。只修当前工单精确路径。
POL-68 原始两份完整 response 是非法 JSON，保持历史失败；不推断 provider stop_reason。

数据流：完整 response → JSON syntax → 已知 envelope/task shape → normalization
→ 现有 PM quality → 既有一次 revision → 合格任务登记 → CE。坏语法优先反馈，
不得搜索内层对象、任意 list 或从 prose/section/bullet 重建执行合同。
明确兼容完整 raw JSON、单个完整 JSON fence、顶层任务列表/映射及已知 data envelope。
task shape 验证字段类型与任务标识；不把 modality/argv/cwd 行当作任务。
解析器提供调用局部 diagnostics，避免 adapter 状态污染下一次规划。

验证命令保持原五枚举及精确 argv/cwd 权限，不映射 read/exec、不补造命令。
normalization 保留 present-invalid 的非权威诊断，质量门禁止混合坏行被丢弃后通过；
路径、错误类型和允许枚举通过现有合同、quality/signals 与 retry prompt 传递。
首次和修订 prompt 共用 normalization 中的现有枚举，不扩大 public API 或日志系统。
占位检测按短句与匹配位置决定否定/cleanup；独立许可仍拒绝。原阈值与 cleanup 保留。

假设：错误来自 permissive extraction、局部极性缺失和 silent row dropping；
证据为 ROOT_REPRODUCTION、INDEPENDENT_AUDIT 与生产函数 RED。最易修错：
全句否定免检、忽略非法尾部、从验证行合成任务、混合坏行悄然通过、诊断冒充权限。
验证：最小负例及实际输入先 RED；再 focused GREEN、PM parsing/normalization/quality
与 POL-67 real runtime/kernel/Factory guard 组合、Ruff/format/Mypy。网络仅本地 fake，
不 stub 身份/guard/预算。无 Provider/Bench/历史目标写入；product_completed_verified=false。
现有 Cell public service 继续承载质量判断，KernelOne runtime/权限层不改。
graph/public schema/state owner/effects 均无扩张；本轮 verification card 在授权报告目录。

POL-69 最终验证：相关 PM parsing/normalization/quality、真实 PMAdapter/runtime/kernel/
Factory authority 与 workflow 组合 `529 passed`，exit 0；9 个改动 Python 文件
Ruff、format check、Mypy 和精确范围 diff check 均 exit 0。全部 RED 及中间失败日志保留。
实际 POL-68 两份全文仍在 char 5797 / 11567 报 JSON 错误，均产生零合同；独立完整
8-task 诊断夹具保留 16 条 invalid_modality 路径、类型及原五枚举，不再称为 missing。
真实 caller 的四种首次/修订组合验证一次修订、同 child、不同 call/freeze/turn、单 grant
累计次数及 timeout 不变；坏修订无任务登记。最终请求快照和 transport body 一致。
字段形状、混合合法/非法命令、autofix 分裂和过滤任务不得隐藏非法声明均有负例。
完整 JSON 的既有 fence 标签保留；prose/section/bullet、内层 salvage、多对象、非法尾部
不再构成可执行合同，这是授权边界收紧。Root 原输入及原上下文/日志哈希保全。
固定报告位于 `pm-contract-boundary-repair/REPORT.md` / `REPORT.json`；仅源码交验，
Root 在本次真实 release 后独立决定接受。未调用 Provider/Bench，不追认历史交付；
`product_completed_verified=false`。多样自然语言极性仍是有界规则，真实 L1-L12 交付未验证。

## POL-67：受控 PM 质量修订身份（修前设计）

mode=implementation；owner=Factory 编排与恢复工程师；独立验收=Root。
输入实证包 manifest 与 12 个调用链源码 fingerprint 均核对吻合。原失败为
PMAdapter 丢弃编排 context.run_id，首次与一次质量修订生成不同 llm_pm child；
同一 Factory grant 的 controlled_child_run_mismatch 正确拒绝后者。首轮确切
score/issues 未保留，不推断首轮合格，也不把 truthlog 空 visible_content 当丢输出。

数据流：server-owned orchestration context.run_id → PMAdapter 首次/修订调用
→ 既有 runtime_dialogue → ExecuteRoleSessionCommandV1 → RoleExecutionKernel
→ preparer/cutoff → Factory authority。只选择必要可信运行字段，不合并 input_data、
prompt 字符串或 factory_run_id 作为 child。保留每次 call/freeze 唯一性和同 grant
累计预算；不改 public schema、state owner、guard、80 分阈值或一次修订上限。
现有 quality_signals 在修订前记录首次 score/critical keys/解析任务数，避免失败覆盖。

验证先 RED：真实 PMAdapter/runtime/kernel/preparer/Factory authority，网络 transport
返回本地工具批次、首次可解析低质合同及合格修订；再 GREEN 并覆盖首次合格、修订
非法/失败、独立任务/standalone、foreign child 及既有 Factory/stage/workspace/revoked
拒绝门禁。仅测试临时 workspace；禁止实际 Provider、Bench、目标、服务操作。
最易修错点为把 parent 当 child、沿用用户输入身份、stub 身份生成/guard、为修订新发
grant 或伪造质量。固定目录保存 before/RED/GREEN/manifest/report；项目交付未验证。

POL-67 验证结果：修复前身份传递行为的 RED-VERIFIED 为 exit 1；首次工具批次与
终结已成功，解析 1 个合同、score=40、critical=verification_commands_missing，修订被
真实 controlled_child_run_mismatch 拒绝。修复后 focused 8 passed，最终相关 caller/
Factory authority 组合 316 passed（exit 0），Ruff/check/format 与 Mypy 均 exit 0。
同一 child、2 个独立 session turn_request_id、3 个独立 call/freezes、同 grant 消耗 3 次
物理预算均断言通过。三个最终物理快照的 body 与传输入口完全一致，审计 scope 为
provider_native_wire；一次修订/质量门/负例均保留。完整失败迭代与本地测试证据见
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-controlled-child-revision/REPORT.md`。
这只是源码实现交接，Root 尚需独立验收；未恢复原 Factory，product_completed_verified=false。

## 第二次纠正运行（2026-10-09）

本轮保留首轮全部证据；更正 Director 工具 P0 为独立审查确认的假阳性。
精确六项 Node 文档执行 RED 为 6 failed / 33 passed，根因为 terminator 和 loader 参数被跳过。
修复仅校验真实 Node 参数边界，不授予执行权限。
本轮复用既有 FactStream strict_integrity 与 captured head；Run Ledger 公共查询增加明确
canonical-only 资格模式，禁止兼容投影补票。TaskRuntime 新写入要求完整性，历史无 digest
记录仍可诊断/恢复，但不能作为资格证据。读取 storage root 与初始化写入分离，公共读取
不 mkdir、不 compact、不切根。Factory typed failure 从 materialization producer 经 StageResult
进入原 audit chain；缺 typed 数据维持通用失败。D03 复用 canonical physical replay 与最终
冻结请求中 hash 覆盖的选择身份，不从当前配置追认。各项先 RED，再对应组合门禁。
风险：历史无 digest/无冻结 binding 只能返回资格不可用；不能重写历史来使其通过。
范围严格采用工单 attempt2 列表；跨 Cell 通过既有 public contracts，报告不成为产品事实源。

mode=implementation；唯一源码写者：Factory 编排与恢复工程师；独立验收：Root。
证据运行：Bench `9a21fda4ddf8` / Factory `factory_b1581d701cf3` / L1-01。
本工单不调用 Provider、不运行 Bench、不更改目标或 runtime，不追认项目完成。

## 修前假设与架构

1. D01：pure-static scanner 必须区分文档与执行源码。Node 测试调用仅支持可界定语义，未知参数、动态展开、执行文档仍阻塞。所有读取继续使用 WorkspaceReadSnapshot；语法、污染、trivial assertion 检查保留。
2. D02：Bench 元数据不是合同来源授权。真实 PM 必须调用任务规划，显式 fixture/synthesized 与 route probe 分开标识。超时和不合格输出不合成成功，不登记可执行任务。
3. D03：实际包是 bench_gates/_gates.py。既有 roles.kernel public lifecycle replay 是物理 attempt 来源；客户端本地配置不能代表 target。先检查已有公开状态能否携带完整 target/run/workspace/binding 身份；缺口报 Root，不创造旁路。
4. D05：HTTP summary 与 causal audit 必须消费同一 public scoped ledger；scope 由已加载 Factory run 的身份给出，不由查询方自报项目决定。结构化 materialization failure 的 producer 位于 factory_director_dispatch_impl.py；若 typed metadata 缺失，不能由 delivery 从 prose 推导。

数据流：WorkspaceReadSnapshot / package script 静态覆盖 → 既有受控 materialization verifier；PM adapter 的真实规划 / 质量门禁 → PM StageResult → CE；TaskRuntime 与 execution.control_plane canonical facts → ReadRunLedgerProjectionQueryV1(parent, factory, project, workspace) → HTTP audit bundle → exact causal audit。

职责：KernelOne 负责静态分类与覆盖；roles.adapters 负责规划输出质量和来源；factory.pipeline 负责 stage 成败；Run Ledger public service 负责严格事实关联；delivery 仅组合投影。无新增状态 owner、实时轨道、修复执行权限或模型配置。

## 验证计划

新回归先 RED 后 GREEN：
- test_pol62_static_coverage.py：Node test 正例、未知 flag/后续 clause/文档执行/读边界/污染负例。
- test_pol62_bench_qualification.py：metadata alone、probe、timeout、畸形/空/低质输出不得替代规划。
- test_pol62_factory_audit_scope.py：parent+children、foreign factory/project/workspace、无事实、损坏与 causal audit 一致性。

组合运行直接相关已有 suite；不执行收费操作或全仓测试。Ruff、mypy 使用原 .venv 环境，失败保持原日志与归因。完整 L1-L12 交付未验证，product_completed_verified=false。

## 最易修错的位置

- 修改被 package 遮蔽的 bench_gates.py。
- 把 scan_complete 或 route probe 当执行/规划通过。
- 文档扩展名放行被声明执行的路径。
- 在 delivery 解析 prose 以补造 typed failure。
- parent-only 查询或兼容事件重复计数掩盖 canonical 缺口。

精确范围和基线在固定报告目录 BASELINE.json；任何超范围请求先写报告，交 Root 指定唯一修改者。
# 授权扩展请求（尚未修改下列文件）

## D03：目标绑定与 canonical physical attempt 的公开证据连接

已核对实际执行包 `internal/bench_gates/__init__.py -> _gates.py`。
`build_llm_status()` 的公开 HTTP 响应包含 roles/bindings，却没有 workspace、
Factory run 或配置冻结身份；`ProviderAttemptLifecycleFactV1` 有严格的
start/terminal、role/run/request/hash/context 身份，却没有 binding_id。
因此不能通过空本地配置、provider/model 猜测或 compatibility terminal 补足资格。

请求 Root/CTO 指定唯一 owner 后，增加以下最小范围（非整目录授权）：

- `src/backend/polaris/cells/roles/kernel/public/provider_attempt_lifecycle_replay.py`：
  定义对现有 lifecycle replay 的只读、无密钥资格投影契约，明确 Factory/workspace/
  role/binding/config-freeze 身份和 physical-attempt/hash/context 校验结果。
- `src/backend/polaris/cells/runtime/projection/internal/llm_status.py` 与
  `src/backend/polaris/delivery/http/schemas/common.py`：公开返回 server-owned
  workspace 与有效 binding 身份，禁止返回凭据；不得把当前配置追认成历史配置。
- 若现有冻结记录没有 binding 身份，必须由 Root 进一步指定实际请求冻结生产者的
  精确写授权，而非让本工单在读端推断。当前先保留此项阻塞。

已有授权的 HTTP audit bundle、client 与 `_gates.py` 在契约确定后可以消费同一证据，
按 physical attempt 去重；无 terminal、非法/不可读 context/hash、foreign/orphan/
tamper 必须拒绝，PM probe 不构成 planning，未运行 QA 不构成 pass。
本轮不新增未经授权的公开字段，也不复制宿主配置。

binding 选择的实际生产者是 `roles/kernel/internal/llm_caller/invoker/_binding.py`，
而 `internal/llm_caller/final_provider_attempt_gate.py` 的物理冻结/快照 108–125、
918–963 只有 provider/model。两者连接的精确合同和生产者修改也须先由 Root 指定，
不能在 Bench 客户端按 provider/model 反推 binding。

## D05：materialization typed failure 信号生产者

精确缺口在
`src/backend/polaris/cells/factory/pipeline/internal/factory_director_dispatch_impl.py`
1705–1724、1786–1796：失败被写入 dispatch_payload，但没有对应 error signal，
最终 `StageResult.metadata` 只有 settlement_metadata。请求仅扩展该文件与本工单
已授权新测试文件，补充 error_code/failure_class/root_cause_hint/signals。

第二处在
`src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_stage.py`
523–533：`metadata.failure` 固定为 FACTORY_STAGE_FAILED/prose；952–958 的 audit
event 已保留 `StageResult.to_dict()`。请求仅扩展该文件来消费生产者 typed metadata，
保留无 typed metadata 时的通用失败（不能从 prose 解析权威原因）。

另记录 TaskRuntime 完整性边界：其实际 `_mixin_facts_events.py` 488–500 写入
`task_runtime.execution` 未设置 strict_integrity，公开账本 reader 613–620 也未设置。
不能单独在 HTTP 读端强制 digest 而使当前真实生产记录全部失效；该迁移由 Root
另定 owner 和 writer/reader 兼容策略。本轮仅严格验证 control-plane canonical facts。

## 附加审计缺陷与旧测试扩展

三次 Director 的 system prompt 宣称可用 read_file/repo_tree/execute_command 等工具，
实际 tools 只有 write_file，context quality 的 findings 却为空。按 AGENTS.md 第 13 条
记 P0，保留真实十个 effect receipts，不把该遗漏解释为模型故障。证据在本轮
`retained-evidence-audit.json` 的 advertised_but_absent_tools。
`roles/kernel/internal/llm_caller/request_preparer.py` 627–673 只投影显式 required_tools
和 materialization scope，是定位入口，尚未宣称完整根因。请求 Root 指定角色提示/
工具裁剪和最终请求审计 owner；本轮不横向修改。

旧测试需要小范围授权更新 fixture/期望，不能靠放宽生产门禁解决（相对 src/backend/polaris）：

- `cells/factory/pipeline/tests/test_characterization_text_helpers.py` 的
  `TestPMDeterministicContractMetadata.test_factory_bench_metadata_enables_preemptive_deterministic_pm`
  明确期待本轮禁止的 Bench 隐式 deterministic。
- `cells/roles/adapters/tests/test_pm_adapter_pureb.py` 的
  `TestCreateBoardTasksRows.test_pm_stage_surfaces_dedup_cancel_execution_event_failure_signal`
  给出缺少 structured verifier、低质量的合同且 fake TaskRuntime 没有失败 API。
  应使用合格合同继续测试 dedup 失败信号，不绕过质量门禁。
- `tests/unit/delivery/http/routers/test_factory_v2.py` 的
  `test_get_factory_run_audit_bundle_success` 缺 canonical project identity/facts，
  却期待 causal audit 成功。应设置真实范围事实并保留缺证据负例。

本轮未修改这些既有测试。

## 实测与限制

本轮 D01、D02 与 D05 scoped ledger 已实现；D03 与 D05 typed materialization failure
等待精确扩展授权。POL-62 按 POL-55 下的新通用源码任务执行；POL-20 未重跑，上限未修改。

- 最终新增回归 52 passed（D01 33、D02 12、D05 7），与相关静态覆盖 suite 合计
  209 passed，exit 0。末次增量又补了分隔符/env wrapper/缺少 shell clause 的 8 项 RED→GREEN。
- 此前较广组合回归 479 passed / 3 failed，exit 1；失败为上面的旧 fixture/期望。
- 受影响十个 Python 文件 Ruff/format：exit 0。
- Mypy baseline shadow 与最终版本均 10 errors，exit 1。没有新增剩余错误，
  不宣称 type gate 通过；既有 scan kwargs 类型和 router 动态 namespace 声明待 owner 处理。
- 原 L1-01 只读 pure-static 扫描 scan_complete=true、checks_complete=false，
  没有 errors/incomplete_reasons；README/requirements 显式分类为文档。
- 原 control-plane 文件 digest 校验与纯函数投影复现父 0/0、Factory/project
  10 effects/3 boundaries；child scope 从原 TaskRuntime 原始事实复核。
- 五次 provider-native wire/semantic/snapshot/pin hash 均重新核验，PM 是 probe，QA 无运行。
- 原目标及 runtime 630 文件哈希前后一致。公开 query 在只读挂载被 storage.layout
  的可写 runtime 检查拒绝，记录为缺陷，不改权限、不切根、不把原始文件审计冒充 HTTP 重测。
- 未调用 Provider，未运行新 Bench，未启动服务，未安装依赖，未写 Paperclip API。

固定交付 REPORT.md/REPORT.json、first RED、验证命令退出码、baseline/before、增量 diff
与缺陷矩阵。Root 独立验收；本报告不是产品控制面事实源。product_completed_verified=false。

## 第二轮交验记录（2026-10-09，独立目录 attempt2）

本轮 source-only 组合为 1003 passed / 4 warnings，退出码 0。最后补强 PM 正向
fixture 后，相关 18 项复测通过：合格 Provider 风格合同经过真实 adapter 落盘，
保留 provider_planning origin，再由真实 Factory PM stage 校验并成功。该 fixture
替换传输和等待边界，没有调用 Provider，不构成 fresh PM 运行资格。
22 个已修改生产文件 mypy 通过；33 个 Python 文件格式通过；除两个旧测试文件外，
31 个 Python 文件 Ruff 通过。完整 Ruff 仍退出 1：characterization 的 57 条与
pm_adapter_pureb 的 1 条告警均存在于本轮 before；未扩大修改为清理旧导入。

D01 精确六项文档执行负例已由 RED 转 GREEN。D05 公共 canonical_only 查询从
同一稳定捕获的 strict TaskRuntime 和 control-plane 事实集生成投影，不再补票。
新 TaskRuntime producer 产出 digest；合法无 digest 历史只允许兼容恢复，不能追认资格；
损坏历史 append fail-closed 且不改原字节。typed materialization failure 经 StageResult、
既有 stage failure 和 audit bundle 保留 signals；无 typed 数据保留通用失败。
D03 的实际选择身份进入既有 hash 覆盖的最终请求；公开读端严格校验 pin、ContextOS、
semantic/native hash 和 terminal，按 physical request 去重。HTTP 与 Bench client 消费
同 workspace / Factory / project 的公开证据。Director 的 explicit degraded 调用仍只要求
已实际选定且成功的路线；没有生成完整配置目录覆盖证明。历史缺冻结身份、PM probe、
QA 无请求、缺 terminal 和 foreign lineage 不可计为资格通过。

原 L1-01 只读实物门禁仍失败，退出码 1。634 个当前挂载文件前后完整 hash 一致。
读 API 已不探测可写、不初始化 cursor/cache、不迁移根；但现存平台 lock authority
的 stream key 仍由 LockedRegularFileSetV1._acquire 用 O_RDWR 打开，在只读挂载返回
errno=30 / EROFS。该 FS 原语不在本轮范围，未绕过锁或更改权限。
Root 的最小下一步见 attempt2/SCOPE_REQUEST.json：授权/指定 FS owner 修改
kernelone/fs/locked_regular_file.py 和其同目录 tests/test_locked_regular_file.py，
增加显式只读 lease 能力，保留所有 authority/inode/no-follow/lock 与 writer/CAS 门禁。
修后仍需在原挂载运行公开查询并复核全部 hash；旧无 digest 与无冻结绑定不得回填。

对首轮 Director 工具 P0 的结论在此明确撤回：精确 physical 请求的 write_file
schema 有阶段性 override，全文关键词审计忽略否定/阶段边界产生假阳性。
首轮原报告保持原件，本轮没有恢复额外工具、改变提示或新增 Bench blocker。
本轮五次原始最终请求另行只读复核，hash/角色标记一致；PM 是 probe、QA 未运行。
测试日志的 NATS connection refused 是既有测试 publisher 尝试，未启动服务，不能据此
宣称 WS/NATS/UI 或真实 Provider 已验证。Root 在 namespace 释放后独立验收；
本轮报告 status=partial_implementation_scope_blocked，product_completed_verified=false。

## 第三轮纠正交验（2026-10-09，独立目录 attempt3）

mode=implementation；唯一写者 b121c1ba-aa6e-48d5-adf3-5fe973b3ae1e，Root 独立验收。
本轮仅执行明确分配的第三次源码纠正；POL-20 上限与原运行未改，未调用 Provider、
未启动 Bench/服务/NATS、未修改原目标/runtime，也未调用 Paperclip 写 API。
第三轮 REPORT、before、增量 diff、命令与日志单独保留，第一、二轮原件不覆盖。

真实只读 lease 使用已有 authority 与同一 stream-key inode 的 O_RDONLY/LOCK_SH；
writer 继续 O_RDWR/LOCK_EX。读 lease 拒绝 writable open、append/create 以及 inheritance；
authority、anchor、realm、key 的 UID 与类型/link/inode/binding 校验均保留或补强，
失败释放描述符。两个独立 reader 共存、单个释放后仍挡 writer、writer 挡 reader、
缺失/损坏/漂移/symlink/hardlink 与既有 writer/CAS/inheritance 回归通过。
只读 namespace 复用 writer 的无写入 env/tilde/default root 归一化；冷缓存读端不探测可写。

原 L1-01 挂载仍为只读（statvfs ST_RDONLY）。公开兼容账本读取成功，消费原 parent 与
三个 Director；生命周期公开 replay 返回 10 条原事实。634 个原文件 hash 前后完全相同。
canonical_only/route qualification 明确拒绝旧 TaskRuntime 的 missing_integrity_digest；
缺少冻结 binding/新义务的历史仍为 legacy，未补写、未追认为交付成功。
另复核五个原始 physical provider request：PM 仍是 probe、QA 未运行；CE 原请求确为 stream。
真实 ContextOS ref 从 frozen attempt 的 final_request_qualification 取得，不能把 frozen
attempt 自身 hash/ref 当作 provider request 上下文。五份角色标记、原 frozen hash 均吻合；
token/window/tool_choice/schema 从对应完整最终 physical request 读取，工具 P0 撤回结论不变。

native TaskBoundary 的 task project 仅在可信 canonical TaskRuntime 的同 factory/catalog/
workspace/child/task lineage 下接受；event/token task 与 run 必须一致。一个合法 child 可
执行多个已验证 task，不能把这种拓扑错误判成 lineage conflict；foreign 负例仍拒绝。

stream/structured 从既有 primary 或显式 worker override 携带身份进入 prepare/freeze；
不为审计另选模型、按 provider/model 反查槽位或覆盖已有异常身份。普通 invoke、真实
transaction stream/invoke 与 structured 的本地 fake transport 测试实际经过最终快照和
physical lifecycle；实际 Factory fanout→caller→freeze→公有审计组合也通过。

期望路由来自 dispatch/selection 时独立声明，进入现有最终 ContextOS pin/hash，禁止由
observed 自证配置覆盖。进程内修改冻结 selection/义务/fallback 声明也会 fail-closed。
A+B 只完成 A 无合法 skip 拒绝；既有 readiness/quarantine/task partition 的显式 skip
携带 Factory producer 来源。失败 A 的终态不删除；只有同 run/call、已结算且顺序正确的
既有 binding fallback/semantic retry 授权可关联 B。相同物理请求的重试只能消费同一
control-plane grant、同 turn/freeze/binding/semantic/wire 和递增且预算内的 ordinal。
历史失败保留，授权 retry/degraded/skip 证据明确投影；orphan、failed-only、未授权与篡改拒绝。
scoped gate 的 require_all_director_routes=True 现在检查 all_routes_ok，不再忽略该义务。

HTTP 根据 server-owned 实际 Factory stage 事实派生 required roles。未到达 QA 明确
not_reached；到达 QA 或 completed 交付仍要求真实 QA。路由审计不改变项目 failed 状态。

最终相关组合 1197 passed / 5 warnings，exit 0，含原 1003 范围及新增 FS/身份/路由验证；
25 个生产文件 mypy、20 个本轮改动 Python 文件 Ruff/format 均 exit 0。
native gate 最后一次等价 lint 合并条件后，22 项 canonical identity 复测通过。
NATS unavailable 与 fork 警告保留，不能计为 NATS/WS/UI/Provider 原生集成通过；
第二轮旧 lint 58 条未横向清理。本轮状态 implemented_pending_independent_review，
product_completed_verified=false。Root 释放后复核源码/产物 hash、增量与真实查询，再决定
是否授权 fresh isolated L1-L12；本外部工程报告不是平台事实源。

## 第四轮精确补修（2026-10-09，独立目录 attempt4）

Root 第三轮 release 后仍复现三项缺口；本轮仅按工单精确范围纠正，不重做此前设计。
canonical 资格读端检查实际被投影消费的 `task_boundary_verdict` 内层 task/run，
与有效外层、job token 和可信 lineage 不得矛盾；同 child 的另一个合法 task 也不能
冒充本条外层 task。缺字段继续采用已有合同默认值，兼容诊断语义不变。

FS 获取锁后保存规范化路径集合，复验与继承沿用同一 key/FD 身份。反斜杠和
首尾空白别名不会选择另一个 key；inode、UID、realm、no-follow、CAS、读拒写/继承
及 reader/writer 排斥仍执行。NFD 在 Root 原复现正常，本轮不将其描述为已修缺陷。

route reader 先验证失败候选的冻结回退授权，再按相同原始 freeze、run/call/turn/
role、grant/budget、semantic request 与递增有序物理 ordinal 结算成功候选之前的
合法失败候选。所有失败 terminal 保留；全失败、orphan、未授权、foreign、坏 freeze、
无关语义请求仍拒绝。不同 provider 保留各自物理 wire/hash，不要求 endpoint 相同；
同一物理请求 retry 的原 wire 身份约束保留。无需修改 `_binding.py` 或历史冻结声明。

本轮 RED/GREEN、Root 原夹具复验、必要组合、类型/lint/format、原只读查询与
保留性证据单独写入 attempt4。唯一源码写者与 Root 独立验收边界不变；2400s 补修
不增加 POL-20 预算，不调用 Provider/Bench/服务/Paperclip 写 API。
`product_completed_verified=false`；最终结果以 attempt4/REPORT.md 与 REPORT.json 为准。

本轮最终相关组合 `1216 passed / 5 warnings`，exit 0；6 个改动 Python 文件
Ruff/format、3 个生产文件 mypy 均 exit 0。Root 原夹具12项通过、别名8组合均可读；
原只读634文件hash不变。三槽公开只读复查为 integrity_ok/ok=true、degraded=true，
失败终态保留且 failed_attempts=[]；不构成真实 Provider 或 fresh 项目交付。

## POL-72：PM 原预算 typed 修订（2026-10-09，待 Root 独立验收）

mode=implementation；唯一源码写者 b121；Root 是独立 reviewer。保留 POL71 的
源码与原报告。本次仅实现 Root 已批准的 v2 私有 PM 修订机制，不运行 Provider、
Bench、旧实例或服务，不修改目标项目，也不调用 Paperclip 写 API。

首次完整 JSON/任务形状与唯一规范 ID 合格的候选仍不具执行资格。私有
`pm/revision.py` 用不可变字符串保存模型原始任务、响应和可信 workspace/run/task
绑定；server-owned base_hash 绑定原需求/响应 hash、任务全文及权限 manifest。
原候选在唯一一次修订请求发出前，已通过既有 storage.layout/atomic writer 留存，
预算拒绝也不丢失原草案。坏 JSON、歧义或无稳定 ID 仍走既有有限完整重答，不恢复片段。

修订输入保留完整原需求、所有诊断、明确字段子集的完整原值及 server manifest。
模型只能返回 `pm.task_revision.v1` 的显式 set/append/add 操作；set 权限与展示分离，
未展示字段不可覆盖，append 保留旧顺序元素。原 id/assigned_to/backend/projection/
metadata 不可修改；依赖别名只在完整展示后授权。当前既有质量门没有可信全文删除
授权，因此 remove manifest 为空，所有原 ID 永久保留为新任务冲突集。新增任务要求
完整原合同，不能扩大 projection/backend 路由。坏值、命令、图依赖、未知字段或
冲突操作整体拒绝；不自动补配置、路径、argv 或依据 prose 发明任务。

新副本合并后继续经过完整 parser、normalization/autofix、quality、coverage、verifier
与 dependency gate；通过前不登记任务、不派 CE。base、实际模型回复、raw merged
candidate 与 normalized plan 分别留存和哈希，PM 层信号链接真实回复 hash 与身份。
现有 normalization 变化明确标为既有 pipeline 推导，不冒充模型修改或单次原始回复，
不生成 provider receipt 或 context_snapshot_ref。

实际 POL70 五任务及真正 3006 字符 Factory directive 在真实 PMAdapter/runtime/
kernel/preparer/Factory guard 下完成功能 RED/GREEN。fake model 从实际第二 wire
读取 base_hash，明确替换 TASK-5 的 scope_paths/target_files 加入 tsconfig.json；
整个候选合格后才登记，其他 raw 字段深度相等。role policy=8000、output=4000 不变；
仅对 fake local-provider/local-model 注入匹配的 native 1M 能力并调用原 ModelCatalog.resolve。
配置正例和 25 项对抗修订均保持两次实际 fake transport、稳定 child、不同 call/freeze。
空修订、错/跨 run base、隐藏/路由字段、冲突、非法 verifier/依赖及损坏 JSON 均拒绝。
原 full-retransmission budget 负例经真实编译器保留，拒绝时 base 仍可读。

最终相关组合 **296 passed**，exit 0；8 个授权 Python 文件 Ruff/format/strict mypy
均 exit 0，包含原 8 项身份测试和 16 项 native stop-reason 测试。无全库绿色声明；
POL71 已记录的广域 caller 20 项 fixture baseline 问题留原日志，本 scope 未横向修复。
证据、before、增量 diff、命令退出码、最终 provider request 与 derivation manifest 见
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-bounded-task-revision/`。
模型仍可能返回错误 base、坏/空编辑、截断或完整重答；这些继续失败。更大的强制输入
仍可被原预算明确拒绝，没有隐式裁剪或预算扩张。product_completed_verified=false；
正常退出/真实 release 后交 Root 复验，源码与 fake-network 证据不构成 fresh isolated
L1-L12 项目交付资格。

## POL-72 第二次有界返工：语义定位与可信身份（2026-10-09）

Root 第一轮独立验收后的 P1/P2 保留在原报告，不改第一轮证据。本次只关闭
这两项缺口，累计第二次执行，2400s 授权；源码 owner b121，独立 reviewer Root。

`make_revision_base` 在任何 workspace 解析前拒绝 None、空串和纯空白的可信
workspace/run/task。caller 不再把缺失 run 转为空串用于派生。standalone/no-context
保持原一次完整重答，不生成 typed base 或空 run 归属的 revision derivation。

语义定位只匹配 public PM quality gate 程序生成的四种明确 acceptance critical
消息，并与原始规范 task ID 精确相等；按现有 `acceptance or acceptance_criteria`
归一化来源定位原字段。完整展示原值后，服务端 manifest 才授予该字段 set 权限。
模型字段内的诊断文字、请求权限和自由文本均不能授予权限。未知消息、非原始 task
ID 或不能定位的失败，在修订调用前选原完整重答；没有猜字段或降 gate。
保留既有路径诊断、absent 标记、依赖 alias、隐藏字段及原子拒绝规则。

真实三条验收中的“占位输出即可”经原 parser/quality 复现 score85/blocked；
明确模型字段替换后同一 gate score97/ok。完整五任务和 3006 字 directive 的实际
caller 同时修复验收及原缺配置，在原 role8000/native1M/output4000 下过原全量门禁。
no-op、append 保留坏项，原 gate 继续拒绝；坏 set、模型自授权及无可信身份均有回归。
控制 child、两次物理调用、不同 call/freeze、原 grant 和提前零 TaskRuntime 登记均复验。

新增核心 RED 29 项失败后 GREEN 29 项通过；最终原296组合加新增31边界检查，
**327 passed**，exit0。8 个授权 Python 文件 Ruff/format/strict mypy 全 exit0。
首次组合的 4 个断言投影误读和 1 个 standalone fake-response 协议不匹配失败保留，
只纠正测试断言与 fake fixture；原 identity/guard/request 编译断言没有删改。
最终语义 caller 8 案有16个完整 physical request；standalone 保留其实际 ContextOS
快照为辅助证据，不重建或冒充 Factory physical-request。四阶段共保留64个完整请求。

全部本轮 before/diff/hash/命令/失败/最终请求与机器缺陷清单位于原证据目录的
`attempt2/REPORT.md` 和 `attempt2/REPORT.json`。前轮证据与非授权源码保全。
支持范围是明确诊断字段 set、既有 append/add 和默认拒绝删除；没有任意任务删除授权。
未知语义失败或更大输入仍可能被完整重答/原预算明确拒绝，模型坏输出仍失败。
`source_accepted=false`、`product_completed_verified=false`；正常 release 后交 Root
独立复验，不构成 Provider、fresh isolated L1-L12 或最终项目交付证明。

## POL-74：PM 目标文件语言角色分类（有界设计，待 Root 独立验收）

mode=implementation；唯一写者 b121；Root 独立复核。POL73 namespace generation
`18725ee4-cb10-4ee9-9511-13952396a036` 于 `2026-10-09T03:57:58.047Z`
真实释放，ROOT_DISPOSITION 与 release 一致；原 HEAD 与 142 文件基线逐项吻合。
授权只含 `_mixin_00.py` 的本地分类、原语言测试文件与本蓝图窄附录。

数据流：原计划文件经既有 loader，逐个 owning task / target occurrence 分类，
仍由既有 PM language gate 返回诊断，既有 `_mixin_01` 阻断任务登记及后续 CE。
没有新增 public contract、跨 Cell internal 依赖、状态 owner 或副作用。
复用既有语言映射、计划加载与 storage.layout；本地纯函数只核对结构化声明。

假设与防御：TypeScript runnable JS 输出需要同任务实际 TS 源目标、emit-capable
typed tsc build 与精确 direct Node entrypoint；QA 辅助脚本需要 verification/QA
任务、直接绑定 typed test 命令，以及仍存在主语言产品源码任务。每次文件出现
独立验证，另一任务的 QA 声明不能隐藏实现任务里的外语源码。
路径/cwd 必须在 workspace 内且无歧义；不得用 title/prose、目录名或扩展名全放行。
`--noEmit`、`--emitDeclarationOnly`、跨任务借用、`node -e`、shell 文本不能作证。
原 `tests/` 全目录 shortcut 收窄，保留精确已有 `tests/test_product.py` 测试注入兼容；
该兼容不授予其它外语 business target 豁免。真实 build、QA 与实现深度仍由下游验证。

修前验证卡：根因是目标角色信息丢失（局部 classifier），最大误修风险是将辅助声明
扩成语言/目录白名单。先运行原测试与完整八任务计划 RED，再添加单独输出/QA 正例、
混合外语、无 emit、无主语言、跨任务/冲突所有权、坏 argv/cwd/path 反例；再实际走
Factory PM caller 证明错误保持 failed 且不登记任务。只运行相关测试、Ruff/format/
strict mypy；不运行全仓、Provider、Bench、服务或改目标/runtime。
证据位于 `.superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-artifact-language-classification/`。
`source_accepted=false`，`product_completed_verified=false`；源码测试不构成交付资格。

本次完成证据：原 13 项测试先通过，完整计划及边界 RED 为 10 failed / 89 passed；
malformed modality、Node test/entrypoint 混淆与 compiler argv 组合各有额外 RED，
全部失败日志保留。语言集合 133 项及 11 个相关 executor/stage/PM contract 合计 559 项
先通过；交接复核增加 QA lint 不能替代 test binding 的 RED，收窄后最终语言
**134 passed**，相关集合 **560 passed**；失败日志保留。
真实 Factory PM caller 的 5 个离线用例确认：合法完整
计划登记 8 任务；外语源码、noEmit、未绑定 QA 或不合格来源仍 failed 且零任务登记。
仅替换测试传输，不执行真实 Provider、Bench、CE/Director/QA 交付。

两授权 Python 文件 Ruff 与 format 检查 exit0。strict mypy exit1，仅有原
`_Mixin00.execute` 的 `no-any-return`；before snapshot 的 shadow-file 检查复现同一
既有错误（原 129 行，修后 293 行）。该方法不在本次 classifier 修改授权内，未改。
原测试 class AST 与旧蓝图全文前缀保留，142 基线中其余 139 文件哈希未变。
原 POL73 111 文件、历史 620 文件与 5 项 Python/browser 缓存记录只读比对一致。
完整命令、退出码、RED/GREEN 日志、动态诊断、before/after 哈希与增量 diff 见
上述目录 `REPORT.md` / `REPORT.json`。交 Root 独立复验；尚未验证 fresh isolated
L1-L12，未自批准、未更新 Paperclip 写 API。

## POL-74 第二次有界修订：显式编译输出绑定（2026-10-09）

Root 在首次实际 release 后独立 loader 验证为 18 passed / 2 failed；源码未接受。
本轮仅处理显式 `--outDir` / `--outFile` 与声明的 JS 入口冲突，保留首轮分类及证据。
源码 owner b121，独立 reviewer Root；1800s、累计第二次自动执行。无 POL75 扩围。

根因：原 parser 仅校验输出选项路径，随后丢弃取值；auxiliary 分类只保留 build cwd。
局部设计：同任务 typed build argv/cwd 传至入口分类，继续执行原 TS source/emit 校验。
显式 outDir 必须按 compiler cwd 解析后包含入口（完整目录边界），outFile 必须精确相等；
重复或同时声明两种输出选项按歧义拒绝。无输出选项保留既有 cwd 兼容。
不推断完整 tsconfig、不运行编译、不宣称文件已生成；无 public contract、状态 owner、
跨 Cell 依赖或生产副作用变更。复用既有路径、loader、语言 gate 与 Factory 阻断顺序。

修前验证：实际 plan-file loader 的输出绑定 RED 为 34 failed / 38 passed，真实 Factory
PM caller 两个冲突 RED 为 2 failed / 5 passed；完整日志保留。覆盖匹配正例、nested cwd、
目录边界、重复/坏输出声明及原无输出兼容；原 noEmit、declaration-only、project/source、
unsafe path、QA direct-test、跨任务和所有权反例保留。新正例中的尾斜杠假设已纠正为
原规则支持的 `./dist`，尾斜杠保留拒绝用例，不扩大路径规则。
本轮全部新报告仅写入原报告目录的 `attempt2/`；首轮目录逐文件 hash 校验且保持不变。
`source_accepted=false`，`product_completed_verified=false`；Root 在正常退出/release 后复验。

本轮最终相关 Factory 集合 **300 passed**（含语言集合 **208 passed**、真实离线 PM
caller 7 案）；未改动 Root 原 20 案由实施方复验 **20 passed**，不冒充独立验收。
两 Python 文件 Ruff/format exit0。strict mypy exit1，仅原 `execute` 的 no-any-return；
本轮 before shadow 仍复现同一错误（before293 / after305），无新增诊断，未越权修该方法。
完整命令/退出码、RED/GREEN/final 日志、before/after hash、增量 diff、动态结果和保全清单
见 `pm-artifact-language-classification/attempt2/REPORT.md` 与 `REPORT.json`。
无 Provider、Bench、服务或原目标/runtime/SQLite 操作；首轮证据与其余 dirty 源码保留。

## POL-75 有界修复：TaskRuntime 登记声明与执行身份（2026-10-09）

POL74 namespace 已真实 released，Root 已验收其源码；开工核对 HEAD
`9bb5bd1b4eddca9c7d28495cc1de3bf7f4689f32` 与输入 142 个源码 hash 全部一致。
本轮 mode=implementation，唯一源码 writer b121，独立 reviewer Root；一次 2400s。
仅修改 run_ledger/public/service.py、本次现有 HTTP 审计 test 与本蓝图追加段。

实际 ensure_task_row / bind_task_to_factory_run 在 session=None 时登记 created、
materialized、factory_run_bound；旧 consumer 只排除无 run 的 created，故后来真实
claimed 仍被不可变历史中的 materialized/bound 阻断。这是独立 producer/consumer
分类缺陷，不改变 POL73 因语言拒绝而未物化的原始失败归因。

设计：复用 FactStream strict digest、两次稳定捕获、TaskRuntime public 事件契约和
现有 scoped canonical reader。私有分类只认 producer 定义的五类 task-row 声明
（含普通 updated 与 governed runtime_reset_removed），先验证 source/workspace、
task、Factory/project、payload/envelope 与 snapshot 身份，再排除其执行 authority。
无 session 的历史 workflow_run_id 只是声明来源，不可生成 child；空身份不成为
通用跳过条件。原执行身份、ownership/ancestry、TaskBoundary、CP 及 digest 门禁保留。
无新增 public API、schema、producer、状态 owner、跨 Cell internal 依赖或写副作用。

验证计划：实际 producer/bind/claim/public reader 两案先 RED；真实 ordinary update、
历史 runtime metadata、governed reset 与 no-claim 反例；malformed/unknown/execution/
terminal、session/run/task/envelope/scope 矛盾、digest 和 ancestry 反例；保留旧 POL62。
最小集合先跑，再跑相关 canonical projection、Factory route qualification、TaskRuntime
consumer 与 scoped Ruff/format/strict mypy。不默认跑全仓、不跑 Provider/Bench/服务，
不打开原 SQLite，不写原目标/runtime；CP fixture 显式 synthetic，仅抑制 outbound
notification，阻断 socket，并将所有本地写与只读 lock authority 放入诊断路径。

证据目录：`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/canonical-task-lifecycle-lineage/`。
工程状态与报告不是平台事实源；`source_accepted=false`，
`product_completed_verified=false`；正常退出后由 Root 独立验收。

本轮 RED：Root 原实际 producer 两案均为 run identity invalid；新增 lifecycle 集合
12 failed，历史/执行身份、native schema/task 与普通 ready/大小写更新又分别先复现。
失败输出完整保留，不改原历史。最终 HTTP 审计 72 案（旧 12 案保留），连同相关
canonical projection、TaskBoundary、Factory route qualification 和只读消费者
**344 passed**；相关 TaskRuntime 恢复/绑定消费者 **37 passed**；Root 未改原两案
由实施方复验 **2 passed**。这些离线结果不构成独立验收或项目交付。

真实 updated 的合法非执行状态按现有 TaskBoard 大小写/空白规则识别；异常类型、
执行/terminal 请求仍拒绝。历史 session 可留在 snapshot，不能生成 child；若身份
已提供而互相矛盾仍拒绝。缺少或损坏 digest、source/workspace/scope/envelope、
ancestry 与 native TaskBoundary 反例保持 fail-closed，事实流 bytes 保持不变。
新诊断包装器将临时 lock authority 与禁止 socket 的约束传给 Python 子进程；
初次包装器路径差异失败及纠正结果均保留，不改原消费者测试断言。

两 Python 文件 Ruff/format exit0。strict mypy exit1，精确修前源码在内存复现
同一 5 条既有诊断（两 no-any-return、两旧 dict type-arg、一旧 await_args union）；
无新增诊断，未越权修旧方法/断言。HEAD 不变，142 登记源码仅授权三文件变化；
旧 test AST 断言与蓝图原前缀保持，POL74 首轮 327 / 第二轮 1763 证据 hash 未变。
完整命令/退出码、RED/GREEN、动态 fact、保全清单及增量 diff 见证据目录
`REPORT.md` / `REPORT.json` / `ARTIFACT_MANIFEST.json`。未运行 Provider、Bench、
服务、原 SQLite 或目标/runtime 操作；fresh isolated L1-L12 尚未在本工单验收。
完成本次唯一源码修复后正常退出并交 Root；不写 Paperclip API，不自批准。

## POL-75 第二次有界修订：声明 transition identity（2026-10-09）

Root 在首轮真实 release 后复验 74 passed / 3 failed；142 个当前源码 hash 与
HEAD 已逐项核对一致。原 owner b121 本次仅追加一次 1800s 修订，三文件授权不变。
原报告、已通过真实 claim、普通状态/历史身份与严格 scope 路径均保留。

根因：现有 `_canonical_task_row_declaration` 在排除 native 声明前遗漏 producer
必需的 `payload.transition_id`。拓扑仍为真实 TaskRuntime producer，经 FactStream
完整性验证与稳定捕获，再到 scoped canonical reader；仅补消费者私有校验。
该字段必须是非空字符串，坏值直接拒绝；不创建/恢复身份，不取 outer event_id
作为替代，也不要求两个独立身份相等。无新契约、依赖、状态 owner 或写副作用。

验证：现有审计 test 用实际 producer/bind/claim 与 public digest writer 注入单字段
坏声明，证明正常 projection 后 missing/empty/non-string 等坏值 fail-closed，先
RED 再 GREEN。原五种声明 positives、历史 update/reset 与所有旧严格门禁保持。
运行 Root 原五案与 focused canonical/lifecycle 集合、scoped Ruff/format/strict
mypy；五条既有类型诊断单列。报告仅写原证据目录 `attempt2/`，正常退出后由 Root
独立验收；`source_accepted=false`，`product_completed_verified=false`。

第二次修订实测：五类声明 × 七类非法 transition 的 35 案先全部 RED，Root 原五案
先 2 passed / 3 failed；补私有非空字符串校验后，focused canonical/lifecycle
集合 **318 passed**（含旧 HTTP 72 案与新增 35 案），Root 原五案实施方复验
**5 passed**。合法声明的 payload transition 与 outer event_id 明确不同仍通过，
读前后全部诊断 event bytes 与查询事实保持原样。两 Python 文件 Ruff/format
通过；strict mypy 仍是精确修前 5 条旧诊断，只有插入代码后的行号移动，无新增。
本次工程复验不代表独立验收或 fresh isolated L1-L12 项目完成；最终哈希、增量
diff、完整日志与首轮证据保全见 `canonical-task-lifecycle-lineage/attempt2/REPORT.md`
及 `REPORT.json`。Root 在本次进程真实退出/release 后独立复核。

## POL-77：PM 产物与动作合同实施交接（2026-10-09）

本次只在 Root 批准的 25 路径内实施 v4：PM 与 Factory 共用纯公共产物
validator；停止原生合同隐式造测试；generated_outputs 按同任务 typed build
结构值与自身/祖先 authored 输入绑定。生成清单与写权限、源码指标分离，CE
组合摘要及 Director 只读上下文保留字段，缺失生成文件仍阻断真实验收。
一次受控修订保留完整原需求和不可变基稿，只授权被诊断字段，隐藏字段拒绝。

原始 12073 字符 raw10 / 3006 字符需求经真实 PM、Gateway、Kernel、Factory
authority/freezes 和最终 guard 运行，只有 provider transport/reply 使用假响应。
合法四任务 patch 与七种拒绝情形复测；最终物理请求重新核对原 8000/4000
政策，不以设计容量代替实现接受。完整请求、RED/GREEN、增量差异、哈希及
命令结果见 `pm-artifact-action-contract/REPORT.md` 与 `REPORT.json`。

完整相关集合仍有三个真实失败：JS synthesis 的 package.json 读写冲突；旧质量
路径判断拒绝 pom.xml；旧测试要求 quality autofix 发明未授权脚本。前两处实现
超出本次授权，第三项不在批准的唯一旧预期迁移范围，因此未加豁免或弱化测试。
最终请求还暴露 roles.kernel 给只读 PM 下发 mutation/write 提示的既有矛盾，
单列范围外缺陷。Root 需指定范围与 owner 后独立审查，当前不可启动下一 Bench。

本次仅为工程交接：`source_accepted=false`、`product_completed_verified=false`。
没有修改原目标项目、runtime 或收据，没有新 Bench、配置/预算变更或自我批准。

## POL-77 第二次有界修订：结构化提议与 live 完成阻断（2026-10-09）

Root 在首轮真实 release 后要求六项修订；本轮加载已批准 v4.3 的 30 路径，
而非首轮使用的 25 路径。唯一源码 writer b121，独立 reviewer Root；3600s，
累计第二次自动执行。开工 HEAD 与 159 个源码 hash 一致，首轮 188 份证据保留。
首轮“缺失生成文件仍阻断真实验收”仅覆盖旧 helper；Root 证明实际 live 已收敛
分支仍返回 success。本轮补 live 最终 success guard，不能将首轮报告当作该证据。

删除 prose、文件同 stem 与任务顺序推导的生成物声明义务；只对结构化错误写入
目标，按公共祖先/source/build 图给出可编辑字段候选，仍由模型选择声明及 owner。
显式 tsc noEmit/outDir/outFile 矛盾不依赖可选语言 catalog；按 JS、声明及 map
实际产物类型校验。Root 追加的 .d.ts/.js.map 两失败先 RED，修后与合法
declaration-only、正确 map、build-info 正例均通过；新增 24 案覆盖两种 catalog。
JS synthesis TASK2
保留 package.json 写入，去掉生产端重复 readonly；XML 相对路径规则保留逃逸反例。
迁移旧隐式造脚本测试，保留 capability containment，新增显式脚本写入正例。
新公共模块与 test 补入已授权 catalog 镜像；没有新增豁免、工具或权限。

live guard 在 settlement/recovery 后执行：缺少声明生成文件将最终状态改为 failed，
投影精确错误与缺失清单；已有文件仍须通过原 canonical/verifier authority。
存在检查不构成 build evidence。实际 live probe 和四种恢复/存在组合均覆盖。
Root 原 18 语义案、实际受控 caller 六案由实施方复验通过；合法四任务 patch
绑定新 base_hash，不添加 TASK6 的无依据依赖。完整 raw10、需求、同 child、两次
独立调用/freezes、隐藏字段及坏修订拒绝、CE hash 绑定与 Director 只读边界保留。
最终请求最高 7787/8000 tokens，输出 4000，窗口 1M；这是最终 provider request
快照实测估算，provider transport/reply 仍为离线 fixture。

补齐 Root 后续 R3 反馈后，最终相关合并集合 **864 passed，exit 0**；
产物/Root 语义集合 **77 passed，exit 0**。首个 836 与中间 43 案日志保留。
五个修改生产模块 mypy 与 public strict mypy 通过。相关测试既有 mypy 106 项、
Ruff 59→57 项及 catalog 47 项审计发现均无新增；格式无新增失败。不越权清理旧项。
测试结束后日志保留 NATS localhost:4222 连接拒绝；本轮未启动服务、浏览器或新
Bench，未验证真实 NATS/UI 与 fresh isolated L1-L12 项目交付。B4 按 Root 结论
保留为非阻塞提示文案问题，未修改 roles.kernel 或重新打开 sealed tool-surface P0。

完整 RED/GREEN、命令退出码、最终请求、增量 diff、before/after hash 与保全结果
见 `pm-artifact-action-contract/attempt2/REPORT.md` / `REPORT.json`。
`source_accepted=false`、`product_completed_verified=false`；仅提交工程修订供 Root
在真实退出/release 后独立复验。遵守禁止 Paperclip 写 API 的边界，由 Root 协调状态。

## POL-79：PM 产品语义、真实输出预算与截断恢复实施交接（2026-10-09）

本轮唯一 writer b121，在已审 30 路径内顺序实施 DESIGN/PLAN；输入基线为
170 个既有源码文件与 7 个预期新增路径。Factory 通过既有 metadata 传递
`PmProductPlanningInputV1`，其 SHA-256 绑定完整 wrapped directive，test_focus
保留标量字符串。有效投影只用于产品复杂度建议，不替代原需求、权限或门禁。
过期/畸形投影不可用，普通独立文档请求仍保留。模板路径增长不再膨胀产品任务
建议；原生 PM→Chief Engineer→Director 的规划/蓝图不重复变成 Director 文档任务。

Kernel 的公共 `resolve_role_output_budget` 复用既有 resolver 与默认 4000，
支持的 alias、嵌套优先级和 clamp 不变。PM 先解析可信执行上下文，再渲染初次
与唯一修正提示；自由 input_data 不提升为预算或生成事实。完整原需求超过旧
18k/6k 提示字符阈值仍保留，原 8000 estimated input policy 不变。物理 fakeHTTP
序列化预算与提示逐次核对；该估算不证明真实 provider tokenizer 的容量上限。

新 immutable `RoleGenerationOutcomeV1` 绑定实际 nonstream call/run、native
reason、final-request sampling 与 producer-visible content hash；Factory freeze
仅接受同一 typed prepared request 的匹配身份。两层过滤保留整个对象，controller
只选最后一次 ledger call。后次缺失元数据/同文本不得继承前次终止原因。文本
变换导致 digest 不同只表示 binding unavailable，不证明串线或重建未知 wire 尾部。

严格解析及 quality 80、语言、HTML、产物、路径、QA 辅助策略和修订权限不变。
完整候选仍走不可变有界修订；不完整 JSON 无 base、无候选产品准入，只允许原有
一次完整 replacement，附解析诊断及已知生成证据，删除误导的 1400 字符前缀块。
ContextOS 合法历史继续保留。完整有效 JSON 的 max_tokens reason 不单独拒绝。

受控九案例通过真实 Factory admission、Kernel/Gateway、PM 严格解析与本地 HTTP
边界；成功响应明确标为新 synthetic fixture，历史 POL-78 初次截断响应只读复制。
公共 replay 的 physical completed 与 PM planning qualified 分开，无效 retry 的
product tasks 为零。原需求、只读工具面、同 child/不同 call/freezes 与最终请求
快照均有落盘证据。Root 追加发现正常 execute 的 strip 使尾部换行哈希失配；修复
保留非空原文，新增真实 execute 到 physical request 的正控与篡改 digest 负控，
防止只测私有阶段入口而遗漏产品绑定。空白-only 仍按空输入处理。

91 项旧有 bounded revision/acceptance polarity 回归通过。完整命令、相关合并
回归、Ruff/type/catalog delta、RED/GREEN、30 路径 before/after 与历史保全见
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-planning-capacity/REPORT.md`
和 `REPORT.json`。额外执行的既有 components_sa 文件有 missing helper 与同名
未装饰测试覆盖缺陷（与 HEAD 相同）；未扩大范围、删断言或宣称全仓 green。

历史 POL-78 原生失败仍是失败；本轮无 Provider/Bench、NATS/UI 或 fresh isolated
L1-L12 验收。`source_accepted=false`、`product_completed_verified=false`。
实现交给 Root 在真实 run release 后独立复验；未写 Paperclip API 或自我批准。

Root 后续 plan revision 2 补充 `LEGACY_IDENTITY_SCOPE_V1.json`，有效范围增为
31 路径／171 既有基线＋7 新路径，只增加 components_sa 测试修复授权。按原提交
恢复三个 helper 和最终遮蔽定义的原七组参数／asyncio 装饰器；全原 AST/body/
assertions 不变，历史 helper 源文与 AST 完全相等。最终相关合并 **589 passed**，
第 31 路径另行复测 **33 passed、8 failed**，七组 mutation 与 UUID 身份断言实际执行。
剩余八项是旧 patch 指向拆分后不存在的 `invoker` 包级 helper，发生在 fixture
patch 建立阶段；不属于 native Provider 失败。原样恢复补充不授权迁移这些 patch
目标或修改生产兼容层，精确断点及 owner/下一步写入本轮 DEFECTS/REPORT。
该测试既有 Ruff 59→41 项；原样 helper 恢复引入历史 object 类型断言的 mypy
诊断，未改其签名或弱化断言。原 26 Python 路径 scoped lint/type/format 通过。
两项 Factory lock anchor 初始化错误在报告私有 KERNELONE_HOME 下完整合并复跑
通过；原失败日志保留，锁校验未绕过。以上仍是有残余验证阻塞的工程交接。

## POL-82 attempt2：60000 流式执行与 PM 等待预算对齐（2026-10-09）

按 Root plan revision4 的 26 路径续作，保留首轮报告及无关 dirty bytes。
PM/Chief Engineer 共用纯 exact-output guard，流式及非流式都在物理传输前检查
模型 cap、联合上下文窗口、固定 system 与当前原需求；不足时明确失败，禁止降预算。
真实 CE RoleRuntime/Kernel 的初次流式请求在本地假传输边界序列化为 60000，
配置 65536/1M 正例、32768 cap 与上下文短缺反例均保留 freeze/快照/请求证据。

PM 通过既有 Factory deadline policy 为初次、唯一纠正和结算分配阶段预算。
默认 60000 对应 750 秒/调用、1530 秒阶段；Factory outer wait、workflow metadata
及 RoleEntry 消费同一最终准入预算。可信 metadata/nested override 与直接 context
沿同一预算解析器处理，显式短 timeout/不足 deadline 在提交前阻断；纠正请求
还受绝对 deadline 约束，预留结算时间，不重启运行、不增加尝试或改变严格质量门。
5400 Factory horizon 及 CE/Director/QA/safety 下游预留保留，CE TTL 仍由准入预算派生。

受影响集合 379 passed，最终 PM deadline/入口/初次与纠正集合 68 passed。
扩展严格控制集合为 158 passed / 47 failed；内存加载开工 before bytes 复跑也为
158/47，失败用例完全一致，主要旧 fixture 模型 cap4000 在解析前阻断60000。
这些额外测试不在授权范围，未修改其配置、删除或弱化断言。13 个修改 Python
文件 Ruff 通过；PM diagnostics 测试保留54项既有 lint，完整修改集合无新增。
14 个修改 Python 文件格式通过；9 个修改生产模块 strict mypy 前后均11项旧错误。

Catalog audit-only 虽 exit0，不能算通过：59→60项（blocker45不变、high14→15）。
新增项是 orchestration.pm_dispatch 使用 roles.kernel 公共预算解析器但未声明依赖。
Root 需授权并指定 writer 同步 pm_dispatch/cell.yaml 与 graph/catalog/cells.yaml
相应 depends_on；本轮26路径不包含两份治理文件，未隐藏 import 或越权修改。
相关 before/after、实际请求、deadline/lease、缺陷清单和所有命令退出码保存在
planning-output-60000/attempt2/REPORT.md 与 REPORT.json。首轮报告保持不可变。
工程交付仍 blocked，Root 负责剩余精确授权及独立验收；无真实 Provider/Bench、
服务、模型配置或目标项目变更，未写 Paperclip API，未证明 fresh isolated L1-L12 交付。
# POL-84 bounded CE native contract repair

Mode: implementation. Owner: PM/CE contract engineer; Root reviews after source
namespace release. Exact scope is PLAN revision 2's 17 paths; existing dirty
changes and 60000 token budgets are preserved. No Provider/Bench/target writes.

Design: Factory reads committed PM command DTOs and supplies a safe planning
table. roles.kernel filters and serializes that table ahead of optional context,
then compares complete typed rows to the actual final request at the existing
pretransport evidence boundary. The table conveys planning provenance only;
Factory live authority, owner/freeze checks and execution receipts stay owners
of permission and success. No new cross-Cell dependencies are needed.

Assumptions confirmed by frozen requests/source: ordinary context values clip at
1500 chars; evidence pass lacks row coverage; residual removals can lose nested
task data; completion normalization assumes one test command per artifact owner.
Pre-mortem: hash-only coverage, producer templates mistaken for model output,
or test-row deduplication dropping an independent receipt could falsely qualify.

Verification plan: red/green projection and final-wire rejection tests, strict
residual schema tests including nonempty semantic removals, distinct test binding
tests, then affected Factory/CE/context/gate and overlapping budget regressions.
Full reconstruction retains the immutable draft as untrusted input when a
diagnostic patch would discard nonempty containers. Consumer validation remains
strict and all committed required tests receive separate exact bindings.

POL-84 source handoff (2026-10-10): implementation is ready for Root's independent
review after genuine namespace release; this is not native delivery acceptance.
The exact 17 authorized paths changed; the other 195 frozen source paths remain
byte-identical. No Cell manifest, catalog, allowlist, model or production config
changed. Required command rows travel through the existing PM evidence payload;
no new request-field whitelist or CE/Factory dependency in roles.kernel is needed.

Final bounded validation: 280 affected tests and 225 budget regressions passed;
all 16 authorized Python paths pass Ruff, formatting and Mypy. Local real HTTP
fixtures compare each CE physical body with its workspace snapshot, retain
60000 output tokens, and exercise strict patch acceptance, foreign-owner and
forged-hash rejection, settlement and absence of target writes. Eight computed
row-coverage failures cross the existing Factory enforcing boundary with zero
transport/effects; malformed closing markers and missing/duplicate table rows
also reject. Ambiguous reconstruction enforces nonempty coverage of all PM task
IDs after structural recovery, and cannot qualify through advisory fallback.

Frozen POL-81 input replay preserves all 28 command rows for eight tasks. TASK-3
retains separate required bindings for `npx tsc --noEmit` and
`node --test dist/tests/verify.test.js`, their exact owner/cwd/hash and independent
receipt requirements. Duplicate identities share one binding; valid existing
IDs are reserved before deterministic new IDs are minted. A second missing or
failed test remains a completion residual despite the first test passing.

Expanded checks remain visibly non-green: 597 passed/1 failed (the previously
accepted helper role-binding environment failure), and 6 passed/3 failed in
architecture checks (existing catalog dependency noise and the retired snapshot
regex file expectation). Their files/gates were not weakened. The PM-only
eight-task projection still fails the existing shared behavior-invariant gate;
command binding is not qualification. Historical repair arguments and the
model-versus-decoder origin of the displaced task remain unknown. No real
Provider, fresh Bench, Director/QA delivery or L1-L12 qualification was run.
Full safe local captures, input/source hashes, defects and commands/exit codes:
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/ce-native-contract-repair/REPORT.md`
and `REPORT.json`. Root owns Paperclip disposition and the next native canary.
