# 弱模型能力放大蓝图(2026-06-11)——从「不拖累模型」到「主动放大模型」

## 0. 目标与诚实校准

延续 [[WEAK_MODEL_HARNESS_HARDENING_BLUEPRINT_20260610]](ADR-0090,15 项全落地)。该蓝图的终点是本蓝图的起点:**链路自伤已实证清零**(空参重复执行 173→5、影子异常 114→0、散文逃逸 24→0、vLLM 400→0、输出塌缩 chars 1→584),剩余瓶颈是弱模型自身能力。

**战略目标**:任何接入 Polaris 的 LLM(当前靶机:qwen3.6-27b-int4@16k vLLM、gemma-4-12B)通过 Polaris 特有的 harness 技术逼近顶级模型(Fable5 级)的 agentic 编码水平。

**诚实校准(外部天花板实证)**:未微调 ~30B 模型自由 agentic 约 7%(SWE-Gym 隐含基线);管线化+执行类选择可到 35-50%(Agentless 51.3%、R2E-Gym 51%);**纯测试时手段(零微调、零训练 verifier)突破 ~50% 无公开先例**;50%+ 全部依赖微调权重(Devstral 2: 68%)或训练 critic(OpenHands critic: 68.2%);前沿闭源 75-95%。因此:

- 本蓝图的可达区间 = 把 27B int4 从当前 **0%(run20: 0/17)** 推进到 **35-50% 区间**(纯 harness 已验证上限);
- 「至少到达 Fable5 水平」按里程碑阶梯推进(§9),其中**定位子任务**是弱模型已被证明可达到甚至超过前沿模型的领域(LocAgent: Qwen-32B 文件级定位 92.7% > Claude-3.5 的 86.1%)——优先把它做满;
- 平台保持模型无关:画像/管线对任何接入模型生效;若用户接入已微调 agentic 权重的本地模型,同一套 harness 直接把可达区间抬到 68%+ 量级。

## 1. 调研方法与证据基础

2026-06-11 以 10-agent 工作流完成(6 路并行调研 → 3 视角设计 → 对抗性批评):

| 调研轴 | 核心产出 |
|---|---|
| kernelone 链路 | 升级阶梯/投机底座/并行采样物理可行性/请求白名单阻断点盘点 |
| roles 编排 | 回路插拔点全图;evidence-bound 地基(_session_read_files)已存在;QA/测试回路缺位确认 |
| 休眠能力 | 修正 UNWIRED 审计 B1 误判;5 件现成弹药;干扰项清单 |
| 评测度量 | 「弱 vs Fable5 差距今天不可度量」的缺件清单 |
| 实证失败 | run20(17 实例)失败分类学,逐类判定机械可防/需能力提升 |
| 外部 SOTA | 测试时算力/定位/记忆/约束解码的量化基准与适用性 |

证据源:~/Temp/swebench-work/normal/{diag5e,fix2-6,run20,run10a} 日志与审计、仓内代码逐点核实(file:line 见各节)、外部论文系统(arXiv 编号见 §3)。

## 2. 实证现状:残余失败分类学(run20,17 实例,11 非空 patch,0 resolved)

| # | 失败模式 | 量级证据 | 判定 |
|---|---|---|---|
| F1 | **通用项目吸附子**(项目身份幻觉) | not-found 560 次:README(80)/src/main(69)/main(45)/src/index(43)/index(28)/package(25)/app(25)/Cargo(14);fix6 证明 t=0 仍确定性产出 package.json 幻觉补丁 | 机械可防面大 |
| F2 | **did-you-mean 诱导错定位** | harness 把幻觉路径重定向到真实但无关文件(main.py→django/contrib/admin/views/main.py),弱模型盲从后即成最终错误 patch | 机械可防(harness 自伤残余) |
| F3 | **强制写压力下的破坏性盲写** | 539 行文件被 write_file 覆写成 32 行并通过 "Write scope validated" 进入提交;bootstrap-followup 强写调整 68 次 | 机械可防 |
| F4 | **验证回路缺失且被契约扼杀** | 全程 0 次真实 pytest;模型 execute_command-only 验证批次被 single_batch_contract_violation 拒绝;脚本轨 v11 实证测试反馈环 +10pts(30%→40%) | 机械可防+最大杠杆 |
| F5 | **scout 金锚点即得即忘** | scout_probe 37 次调用、run10a 已召回金文件 compiler.py 金区域,findings 一次性即丢,主代理在 mutation 压力下踏上金文件又被拽离 | 机械可防 |
| F6 | **修复正确性墙** | 11/17 非空 patch 0 resolved;脚本轨 v10:94% apply 率下 13/15 applied+compiling 仍不 resolve;盲改上限 ~30% | 需测试时算力 |
| F7 | 模型自产缺参调用 | 213 次/run(file_exists 126/edit_blocks 84),被拦截但白耗轮次 | 混合 |
| F8 | 问题陈述项目混淆 | pytest-11148:在 pytest 仓库找 pmxbot/core.py ×13 | 机械可防 |
| F9 | 重复搜索(轻度遗忘) | 同一 repo_rg/read 重复 3-4 次 | 机械可防(预算回收) |
| F10 | 疑似 tool_result call_id 错配 | run20.log:974-975:args=compiler.py 配 error='File not found: main.py'【未验证是否进模型上下文】 | 必须取证(若实则为未清零链路自伤) |
| F11 | 评测零分墙 | 0/17 resolved 使一切改进不可度量;无 partial credit、无失败打标、无 Fable5 基线 | 度量缺件 |

## 3. 外部 SOTA 基准线(量化锚点)

- **Best-of-N+执行类选择**:Agentless 消融 37.7%(greedy)→40.3%(多数投票)→44.7%(+回归测试)→51.3%(+复现测试)(arXiv:2407.01489);R2E-Gym 混合 verifier 32B 34.4%→51% 仅需 Best@26(arXiv:2504.07164)。
- **执行反馈回路**:Self-Debug 1 个带调试样本≈16 个盲采样本(arXiv:2304.05128);CodeMonkeys 57.4%(arXiv:2501.14723);收敛集中前 2-3 轮。仓内 v11 实证 +10pts。
- **定位**:LocAgent Qwen-32B 文件级 92.7% 超 Claude-3.5 86.1%(arXiv:2503.09089);Agentless 分阶定位文件级 77.7%/行级 50.8%,每阶段上下文有界,天然契合 16k。
- **反思**:纯自评在 13B 级失效(Reflexion/Self-Refine 弱模型局限);只有挂接真实执行证据才有效。
- **辩论**:等算力下多代理辩论普遍不敌自一致性投票(arXiv:2508.17536)——**不建辩论**,算力给采样+投票。
- **约束解码**:全约束计划类输出致 10-30% 推理退化(arXiv:2408.02442);选择题式全约束无损;draft-then-constrain 是已验证缓解。
- **记忆**:AWM +24.6%/+51.1%(web 域)、Learn-by-Interact ICL +12.2%、SWE-Exp 73%(均为强模型报告,**弱模型 SWE 增益无先例,必须自测**)。
- **自适应分配**:compute-optimal 按难度分配采样 ~4x 效率(arXiv:2408.03314);**模型画像→scaffold 全参数自动配置无完整先例 = Polaris 差异化自研空白**。

## 4. 休眠弹药库(现成可收割,调研核实)

| 弹药 | 现状 | 用途 |
|---|---|---|
| cognitive strategy_override 通道 | **live**(service.py:5924 构建→kernel/core.py:359 消费 blocked_tools 等),上游仅关键词启发式 | 失败模式驱动策略包的零管道成本注入点 |
| CognitiveGateway LOG_DISTILL / JSON_HEAL / QUERY_EXPAND | 建好零调用方(cognitive_gateway.py:629-662);坑:slm_enabled 双默认值冲突(ledger.py:60 True vs bootstrap/config.py:348 False)+硬编码公网 OLLAMA IP | 测试失败长输出降维 / 参数二层修复 / 检索扩展 |
| knowledge_distiller retrieve/distill 记忆环 | 仅 aggregate(Polaris-as-LLM)路径活(service.py:4975/5061/7220),Director 正常 turn 零接入 | 跨 session 仓库事实沉淀 |
| KERNELONE_USE_STRUCTURED_OUTPUT | **空壳**(core.py:299-308 恒返 None) | guided decoding 从 edit_blocks 泛化到定位/决策输出的预留位 |
| ACCEL 语义 ranker/reranker | 默认关(config_runtime.py:345-372);**依赖嵌入 provider,本机历史实证缺位** | 检索 grounding(先用 scout_matrix 量化增益再开) |
| **RepoIntelligenceFacade 免嵌入定位 ranker** | **已实证**(facade.py:140;V12:flask-4045 gold 排 #1、107 tests 绿) | 受约束定位的候选源首选——勿从 codegraph 重新造 |
| 跨 turn 投机缓存 | API 齐全(stream_shadow_engine.py:317-371) | 「上一 turn 预生成候选」结果缓存 |
| ContextOS 钩子总线 | live 被真实调用(state.py:329,464),注册插件为零 | 归因记录器/验证器零侵入挂载(注意 fail-open 边界) |
| retry 模型升级阶梯 | dormant(retry_orchestrator.py:80-105,env 即开) | 升级到同机更大本地模型(与「不切云」兼容) |
| Tri-Council 投票 | dormant(PM 层) | 裁决协议参考,turn 级另建 |

**裁决:认知管线不整体唤醒**(COGNITIVE_USE_LLM/evolution 为模板级实现,弱模型自反思与目标矛盾);收割三件:strategy_override 通道(换信号源)、evolution 学习协议设计(只取协议)、钩子总线位。**干扰项清理**:KERNELONE_COGNITIVE_ENABLED 及 5 相位死开关(config.py:126-131 零读取)、KERNELONE_CONTEXT_COMPACTION 死 flag、KERNELONE_ENABLE_LLM_TOOLS no-op;并修正 UNWIRED_TECH_POINTS_AUDIT B1 误判(真实门控=RUNTIME_MODE 默认 mainline 已开,而非 15.5k 行整体休眠)。

## 5. 计划 A:加强已有技术(10 项)

**A1. 度量底座统一实现(P0,一切之前)**——加强 agentic-eval/SWE-bench 评测体系
- pinned SWE-bench 子集入库版本管理(含 gold 可解性预检),替代仓外临时文件;
- pure_f2p_resolved 护盾 + gold 对照重评分从 arch_b_converge.py:604-630 移植进 swebench_normal_mode.py --score(:324-346),屏蔽 WSL2 网络型 P2P flaky;
- partial credit:gold-file-touched / gold-hunk-overlap,打破零分墙;
- 确定性失败打标器(零 LLM,输入 events.jsonl+checkpoints):定位失败/项目幻觉/建议诱导/编辑失败/预算溢出/漂移/破坏性盲写/验证被拒;注册进 _suite_runners(agentic_eval.py:268-276);**离线打标与未来在线策略包同一规则库**;
- paired 双模型 runner:两份 KERNELONE_LLM_CONFIG(runtime_config.py:88-125)× 同子集 × N repeats,复用 projection_adaptive_matrix 的 repeats+95% CI+inconclusive 机制;内建逐 sample 子进程隔离(scout provider teardown 坑);补 AGENTIC_EVAL_AUDIT per-case 全量持久化(现仅失败 case 有明细)并按 resolved_role_bindings 出模型轴归因;
- per-instance 算力账本(token/时延/重试/采样数);
- **度量可信度护栏三件(批评者新增)**:①分数尺度版本戳+compare-baseline 跨版本失效保护(渐变评分已造成历史断点);②benchmark 模式记忆/缓存隔离协议(防 B6 上线后跨 run 自我污染 repeats 统计);③vLLM 负载互斥(采样/评测/单测三类负载串行)+pytest-timeout(坑:vLLM 饱和时 unit 全树卡死 29min);
- **Fable5 基线一次性运行**:先出 baseline-only 隔离 ADR(独立 config、产物目录隔离、书面禁止回流开发循环),与「不切云掩盖本地缺陷」纪律书面区隔。

**A2. mutation 契约放行「写+验证」批次 + call_id 错配取证(P0)**——加强契约系统
- contract_guards 批级守卫对验证工具(execute_command 跑测试类)豁免或允许混合批次,只豁免验证类——解除 harness 对模型自发验证的扼杀(F4 直接成因);
- **取证 F10**(run20.log:974-975 tool_result 关联错配):审计 stream_orchestrator 结果回填与渲染层;若进模型上下文=未清零链路自伤必须根修;**在打标器定稿前完成,否则失败归因证据不可信**。

**A3. did-you-mean 任务相关性门控(P0)**——加强教学错误系统
- filesystem.py 建议生成处(:64-128)加门:候选须与已读文件目录前缀或任务关键词有交集;零关联时改为「该路径不存在,请用 repo_rg 搜索 <符号>」;
- 建议证据源升级为 repo_symbols_index/RepoIntelligence 符号命中(同治时代错位路径簇)。

**A4. evidence-bound 写门 + 覆写收缩比(P0,单一 owner)**——加强 read-before-edit 不变量
- 落点(唯一):cells/roles/kernel/internal/transaction/tool_batch_executor.py 预校验段(:955-983,_raise_contract_violation:298 现成);
- 规则:EDIT/WRITE 目标为已存在文件且 ∉ _session_read_files → 教学拒绝(error_type=ungrounded_edit,retryable)走现成 retry 修复;新文件豁免复用 _tool_requires_existing_file/_resolve_existing_workspace_file;
- 覆写收缩比:write_file 新内容 <40% 且原文件 >100 行 → fail-closed 拒绝并教学降级 edit_blocks(直接拦 F3);需显式 escape 通道(引用已读证据的大重构);
- **与强制写阶梯解环**(死锁仲裁,见 §8):门禁>强制写;强制写目标未读时一律先合成读 bootstrap(复用 contract_guards.py:437-525 模式)再写;补死循环回归测试;
- read-before-edit 双源权威裁决:session 级 _session_read_files(入 checkpoint)为权威源,kernelone executor 内存态(core.py:202-229)对齐消费(架构裁决记入 ADR)。

**A5. 写后确定性语法门(P1)**——加强验证链
- 编辑成功后零 LLM 检查被改文件(Python: py_compile/ast.parse;按扩展名注册检查器,数据驱动守 §8),失败即教学错误回灌走 retry;<1s/文件。

**A6. harness 强制 patch→测试→失败回灌闭环(P0,前置=A2)**——加强续轮/失败回灌系统
- 写成功后 harness 侧自动追加验证 batch(不依赖模型自觉);插点 session_orchestrator turn 后钩子(:884-942)或 tool_batch_executor 写成功路径;测试命令由 repo 指纹确定性推导(通用机制,不写死项目命令);
- 判定复用 ContinuationPolicy._parse_verification_output(:399-490)三层校验;
- 失败回灌:测试失败文本经 LOG_DISTILL(唤醒前先修 slm_enabled 双默认值+OLLAMA IP)或 head+tail 截断 → mandatory_instruction + _build_last_failure(session_orchestrator.py:313)进续轮 prompt;
- 修复回合硬上限 3 轮(外部收敛证据),超限走停滞恢复;反思必须引用执行证据,禁止裸自评;写侧守卫拦截「删测试」逃逸。
- 预期:复现脚本轨 +10pts 量级(唯一已实证的最大单项杠杆)。

**A7. scout 锚点持久化 + 写侧 recon 门(P1)**——加强 scout/ADR-0091
- scout_probe findings(file:line+confidence)写入 RoleSignalPlane 持久 grounding 锚并并入 _session_read_files,续轮可见(治 F5);
- 写侧 recon 门 = ADR-0091 finalize 门的镜像:复杂任务 delivery contract 下写批次要求 ledger 含成功 recon(谓词 has_successful_recon_execution contract_guards.py:604、RECON_TOOLS SSOT、blocked 路径全现成);
- 与 A4 写门合流成「先定位才能编辑」协议;策略包须有「已踏上正确定位则不干预」豁免(run10a 教训:金轨迹正是被 mutation 压力拽离的)。

**A8. 统一「失败类型→升级策略」调度表 + corrective re-ask 扩展(P1,单一注册表)**——加强 retry/decode 系统
- 把 resolve_retry_escalation(retry_orchestrator.py:220-257)、evaluate_decode_corrective、测试失败(A6)、QA verdict 失败(A9)收敛为注册表驱动调度层;从 4-5 个高频失败类型起步防矩阵膨胀;
- per-tool schema narrower 注册表落 toolkit/definitions.py(SSOT)旁,替换 edit_blocks 硬编码(:255-256),write_file/search_replace 同享 guided decoding 收窄;
- 参数校验失败纳入 corrective re-ask(带 schema 范例单次定向重问)+ JSON_HEAL 二层修复叠加——治 213 次/run 轮次浪费(F7);
- 进化式爬升(Satori 思想):测试失败 patch 作为种子注入下轮 retry prompt 突变,而非独立重采;
- decode policy 从单 float 扩为结构(temperature/top_p/n),依赖 B3 第二阶段白名单放行。

**A9. QA patch 门禁 + verdict 回灌(P1)**——加强 QA cell 接线
- session 收尾自动触发 run_qa_audit;**verdict 必须绑定执行证据(测试 exit code+失败摘要),无证据的 verdict 降级 advisory 不得 BLOCK**(QA 同为弱模型,纯语言评审=噪声放大器);
- director context_policy 默认开 include_verdict_history(现默认 False),失败 verdict 升 must-have 进下轮;
- verdict 失败→mandatory_instruction 注入修复 turn,与 A6 共享 3 轮硬上限;integration QA 失败→revision 登记升级为有界 re-dispatch 回路(上限 1 次);
- 「门禁误杀率」列为一等度量指标(gold-hunk-overlap 监控被拒的半成品价值)。

**A10. 休眠收割与干扰项清理(P2)**——加强代码库健康
- §4 裁决执行:死开关簇删除、UNWIRED 审计 B1/A1 修正、ExplorationBuilder/TurnEngineExecutor 按 Wave ADR 删除(注意 TurnEngineExecutor 与活体 TurnEngine 同名异物);
- 重复搜索抑制(F9):续轮 prompt 注入「已搜/已读清单」(ReceiptStore 现成),纯 16k 预算回收。

## 6. 计划 B:新增技术(7 项)

**B1. RepoIdentitySignal 仓库身份卡 + 问题陈述路径预比对(P0)**
- 新增确定性 RoleSignal provider(role_signals.py DEFAULT_SIGNAL_PROVIDERS:221 + SignalBuildContext accessor + gateway.py:862-880 接线):扫描顶层 manifest 存在性+语言占比+布局 → 正向断言(语言/构建系统/入口)+**负向断言按仓库实际文件集动态生成**(「本仓库不存在 package.json/app.py」,严禁静态幻觉文件名单,守 §8);must-have 级、≤500 token、走中段降级 user 通道(fix2 教训:vLLM 拒中段 system);
- 问题陈述路径预比对:提取任务文本路径与 repo 文件集比对,外来路径标注「属报告者环境,非本仓库」(治 F8);时代错位路径附「不存在时优先 repo_rg/符号索引」指引;
- 直击 F1(560 次 not-found 的第一道拦截);**注入≠遵从,须与 A4 写门配套才闭环**。

**B2. 受约束定位管线(Agentless 式,P1)**
- 候选级联:**首选复用已实证的 RepoIntelligenceFacade 免嵌入 ranker**(facade.py:140)+repo_rg 兜底产出 file→symbol→line 候选,模型只做选择题;
- 激活 KERNELONE_USE_STRUCTURED_OUTPUT 空壳(core.py:299-308),经 roles.profile 供 schema,vLLM guided decoding 全约束选择输出(选择类无推理损伤;**决策类一律 draft-then-constrain**,见 §8 仲裁);
- 做成 delivery-contract 可选协议(非硬编码流程),保持平台通用性;
- 召回率=硬上界,候选漏金文件即确定性失败,repo_rg 兜底通道必须保留。

**B3. patch 级 best-of-N + 确定性 verifier 阶梯(P1,gated on A1+A6)**
- 第一阶段(零契约改动):session/编排层对同一决策上下文 asyncio.gather N 次(信号量默认 100,物理可行)各带不同 temperature(复用 _transaction_kernel_temperature_override 通道);n=2-4 起步,≤8;
- verifier 阶梯(全免训练):①apply 干净(patch_apply_engine/git apply --check)→②语法/lint→③归一化 diff 多数投票→④回归测试子集→⑤复现测试(**须先过有效性门控:base-fail/patched-pass,R2E 实证仅 ~20% 生成测试有区分度,无门控则阶梯⑤空转**);
- 候选多样性:温度梯度(0.2/0.7/1.0)+参数化上下文裁剪视角(compress_chat_messages_to_budget 复用),对抗 fix6 式 t=0 同质幻觉;
- 裁决在 TransactionKernel commit 前,单 commit 不变量不破;
- 第二阶段(省算力,严格 gated 在第一阶段 paired A/B 证明增益后):_INVOKE_OPTION_KEYS/_STREAM_OPTION_KEYS 放行 n/top_p/request_overrides(_executor_base.py:19-44);InvokeResult/AIResponse 扩 candidates(response_parser.py:105-107 现仅 choices[0]);补 openai_compat 流式 request_overrides 合并;
- 风险:27B int4 单卡 n=8 时延×数倍且 KV cache 影响未验证;弱模型 N 路同质时 verifier 只能选「最不坏」。

**B4. 定位自一致性投票(P1,依赖 A1 的 gold-file-touched 指标)**
- scout N 路采样(不同查询扩展×温度;QUERY_EXPAND 唤醒做查询多样化)→文件级归一化投票→top-k 锚点;
- 投票锚点经 A7 通道持久化为 must-have 信号(≤300 token);
- 编辑目标 ∉ 锚点集∪_session_read_files → 教学拒绝(与 A4 合流);
- scout 多 sample 必须逐 sample 子进程隔离;ACCEL 语义 ranker 待嵌入 provider 落地后先经 scout_matrix 量化再开。

**B5. 模型能力画像 + 自适应 harness(P1 起步/P2 深化;差异化自研,无完整外部先例)**
- CapabilityProfile 契约(≤5 维:FC 合规率/定位准确率/编辑格式成功率/有效上下文/失败频谱);接入期校准探针 = tool_calling_matrix+scout_matrix 子集(~30 case,已实证可拉开强弱:gemma 64/78 vs deepseek 77/78);按 provider+model 键存储;
- 消费端:相位解码策略(温度/top_p/n)、schema 收窄级别、escape-hatch 阈值、任务微分解步长(plan_director_task/dispatch_pipeline 消费 profile)、特化技巧自动启停(textual recovery 等);
- **铁律:画像驱动参数,严禁模型名分支**(否则violate模型无关);失败频谱跨 session 回写画像。

**B6. 跨 session 仓库知识缓存 + 经验库(P2,gated on A1 paired A/B)**
- 仓库知识缓存:knowledge_distiller retrieve/distill 接进 Director 正常 turn(复用 role-signal 注入),按 workspace+commit 键沉淀框架/入口/关键模块事实;**沉淀门=验证过的读取回执**(错误事实持久化比无事实更糟);
- 技能蒸馏:strict resolved/QA 通过的轨迹 → 确定性轨迹摘要(工具序列+锚点,**不靠弱模型自由生成**)→ workspace .md 技能(create_skill_template, skill_system.py:184);挂点 record_session_outcome(continuation_policy.py:583);
- 确定性 top-k 注入(新 RoleSignal provider,弱模型不会主动调 load_skill);
- 接入期 Learn-by-Interact 式离线自探索合成「本仓示例」(治冷启动无轨迹可蒸);
- repo-map 投影器(codegraph PageRank,~1k token)作为 P2 增补;
- **弱模型增益无先例,必须 A1 paired A/B 证明增益>16k 预算挤占才保留;benchmark 模式强制记忆隔离**。

**B7. 自适应算力 governor(P2,gated on A1 账本+B3 增益证明)**
- 采样升级阶梯:首样 apply 干净+测试过→停(n=1);verifier 全拒→升 n(2→4→8);再拒→resolve_retry_model_override 升同机更大本地模型;
- logprobs 链路打通(白名单+InvokeResult/AIResponse 扩字段)做难度/置信信号(int4+vLLM 校准度未验证,先行小实验);
- 候选早期淘汰复用 CandidateDecoder parse_state/stability_score;
- 对标 compute-optimal ~4x 效率;**B3 未证明增益前此项无意义,严格后置**。

## 7. 实施相位与依赖

```
Phase 0 度量先行(其余一切的前置)
  A1 全部 + A2 取证(打标定稿前) + Fable5 基线(隔离 ADR 先行)
Phase 1 确定性零 LLM 护栏(与 Phase 0 大部并行,回归验证依赖打标)
  A2 契约放行 → A3 did-you-mean 门 → B1 身份卡 → A4 写门(含死锁仲裁表) → A5 语法门
Phase 2 验证回路(依赖 Phase 0 度量 + Phase 1 的 A2)
  A6 测试反馈环;A7 锚点持久化+写侧 recon 门(与 A4 合流)
Phase 3 测试时算力(依赖 Phase 2 测试基建作 verifier 证据层)
  B3 第一阶段(n=2-4)+ verifier ①-④;B4 定位投票;B2 受约束定位
  B3 第二阶段引擎改造 gated on paired A/B 增益证明
Phase 4 调度与自适应(依赖前序数据流)
  A8 统一调度表;A9 QA 门(advisory 起步);B5 画像 v1
Phase 5 学习与算力治理(严格 gated,先解决评测记忆隔离)
  B6 经验库;B7 governor;归因细化(outcome 分项化+logprobs);A10 清理
```

铁律:**度量先于技术落地;契约放行先于测试环;测试环先于 best-of-N 高阶 verifier;同一接线点先出 owner 仲裁再动工;离线打标与在线策略规则同源**。

## 8. 接线点冲突仲裁(单一 owner 表)

| 接线点 | 冲突 | 裁决 |
|---|---|---|
| tool_batch_executor.py:955-983 预校验段 | 三设计各自落写门 | **单 owner 单守卫**(A4);路径唯一真身=cells/roles/kernel/internal/transaction/(勿用 runtime 层错路径) |
| 覆写收缩比 | 三处重复落点 | 只落 A4 守卫区;**严禁放 strategy_override(fail-open 吞异常,违反 fail-closed)** |
| 写门 vs escape-hatch/强制写阶梯 | 「强制写→被拒→再强制写」振荡 | 优先级表:门禁>强制写;强制写一律先合成读 bootstrap(contract_guards.py:437-525 模式);死循环回归测试必备 |
| KERNELONE_USE_STRUCTURED_OUTPUT | 一物三用、约束级别冲突 | 单一 schema 供给契约:**选择/定位类全约束;计划/决策类 draft-then-constrain**(外部实证 10-30% 退化) |
| 失败调度表 | 双重建设+离线/在线规则漂移 | 单一注册表(A8)+单一规则库(与 A1 打标器同源) |
| 16k must-have 信号军备竞赛 | 身份卡 500+repo-map 1k+锚点 300+经验+verdict >2k vs 可用 ~13.6k | RoleSignalPlane 统一信号预算 governor(allocate_role_signals 扩展);新信号一律走中段降级 user 通道 |
| read-before-edit 双源 | kernelone 内存态 vs session 级 | session 级 _session_read_files 为权威(入 checkpoint),kernelone 守卫对齐消费;记入 ADR |
| Fable5 基线 vs 不切云纪律 | 仅口头 baseline-only | 机制隔离:独立 config+产物目录+ADR 书面禁止回流 |
| QA fail-closed vs partial credit | 门禁误杀半成品 | 误杀率列一等指标;QA 无执行证据只 advisory |

## 9. 度量与里程碑

北极星:**pinned SWE-bench 子集上,弱模型+Polaris 与 Fable5 基线的 paired pure_f2p resolved 率**(N repeats,95% CI)。辅指标:gold-file-touched / gold-hunk-overlap / 失败打标分布 / 门禁误杀率 / resolved-per-token。

| 里程碑 | 判据 | 主要依赖 |
|---|---|---|
| M0 度量在位 | 同一 run 产出四件套(pure_f2p+partial credit+打标+账本);Fable5 基线在手 | Phase 0 |
| M1 机械失败清零 | 打标器上 F1-F3/F5/F8/F10 簇趋零;验证执行次数/instance 由 0 转正 | Phase 1-2 |
| M2 零的突破 | pure_f2p resolved 显著>0(预期测试环 +10pts 量级) | Phase 2 |
| M3 harness 天花板区间 | pinned 子集 35-50%(对标 Agentless/R2E);**定位子任务 gold-file-touched 达到或超过 Fable5 基线**(LocAgent 证明可行) | Phase 3 |
| M4 逼近 Fable5 | 画像自动配置跨模型生效;接入更强本地权重时同 harness 直接受益;剩余差距持续以 paired 报告量化 | Phase 4-5 |

## 10. 风险与边界

- 纯 harness 无微调突破 ~50% 无先例——M3 后的差距处置依赖模型侧选项(平台保持无关性,harness 对任何接入模型生效);
- 本地算力硬墙:N 路采样×测试环×repeats 相乘;一切采样策略受 A1 账本与 B7 governor 约束;vLLM 饱和与本地 pytest 互斥调度(坑3);
- 16k 预算是所有注入类技术的公共敌人:统一信号预算 governor 先行,TokenEstimator 字符启发式偏差在紧打包场景需评估真分词器;
- 测试落位坑:窗口/预算敏感测试放 polaris/tests/unit/kernelone/(kernel tests conftest autouse mock_model_catalog=128k 会毒化);
- 评测污染:B6 记忆类技术上线即威胁 repeats 独立性,benchmark 隔离协议先行;
- §8 通用性验收门:一切机制必须数据驱动注册表/按仓库文件集动态计算,ADR 验收项,严禁静态项目名单与模型名分支;
- fail-closed 边界:strategy_override/钩子总线 fail-open 吞异常,守卫类不变量一律不放那里。

## 11. 治理

- Fable5 基线隔离 → `src/backend/docs/governance/decisions/adr-0092-swebench-baseline-isolation.md`(已落地 2026-06-11);
- 跨切面不变量(evidence-bound 写门、写侧 recon、验证豁免、信号预算)→ Phase 1 实施时立 ADR(0093 起);
- 分数尺度版本戳进 AGENTIC_EVAL_AUDIT schema;
- 触及 kernelone 公共行为跑 release gate;
- Benchmark 纪律不变:一次一个矩阵、--max-failed 3、先归因再继续。

## 12. 实施状态

### Phase 0(2026-06-11 当日落地)

| 项 | 状态 | 落点 |
|----|------|------|
| 评分护盾+partial credit | ✅ | `kernelone/benchmark/swebench_metrics.py`(pure_f2p_resolved/gold-file-hit/gold-hunk-overlap(±slack10)/schema 版本戳 `swebench-score/1`);接线 `swebench_normal_mode.py --score`,输出 `*.scores.json`;25+3 单测 |
| run20 离线回放校准 | ✅ | 18 实例:0 resolved / 0 pure_f2p / **0 gold-file-hit**——partial credit 基线为真实零,定位失败定量实锤 |
| call_id 错配取证 | ✅ verdict B | 仅渲染层(console_host FIFO 快照按序弹出);events.jsonl 写于富化前=干净真值;**新发现:批替换会静默丢弃模型请求**(repo_read_slice(compiler.py) 无回应→金文件脱轨的真实机制),Phase 1 待修 |
| 渲染层错配修复 | ✅ | console_host:ToolBatchEvent 透传 call_id、快照 dict[call_id] keyed+FIFO 兜底带工具名守卫、turn 边界清空;3 回归测试;顺带清零 roles.runtime.public lazy `__getattr__ -> object` 类型债(→Any) |
| 失败模式打标器 | ✅ | `kernelone/benchmark/swebench_failure_taxonomy.py`(10 标签,执行真值=tool_result+batch_receipt,绝不按序配对);run20 校准:no_verification 18/18、path_hallucination 14/18、localization_miss 12/18=全部非空 patch、**suggestion_induced_misedit 10/18(高于调研预估,A3 优先级上调)**、destructive_overwrite 7/18;17 单测;接线 --score 自动打标 |
| pinned 子集入库 | ✅ | `scripts/swebench/pinned_subsets.json`(pinned-v1=run20 20 实例,基线注记);harness 加 `--subset` |
| paired 双模型 runner | ✅ | `scripts/swebench/swebench_paired_runner.py`(子进程隔离=provider teardown 免疫;串行=vLLM 负载互斥;独立 work-dir=adr-0092 隔离);`build_paired_report`(`swebench-paired/1`)+3 单测 |
| 基线隔离 ADR | ✅ | adr-0092-swebench-baseline-isolation(baseline-only 五条纪律) |
| Fable5 基线实跑 | ⬜ 待用户授权 | 需云模型 config + 费用授权;runner/ADR 就绪,一条命令可跑 |
| CI 统计(repeats±95% CI) | ⬜ | v1 为 repeats 均值;t 分布 CI 复用 projection_adaptive_matrix 机制,Phase 0 增补项 |
| 算力账本(token/时延) | ⬜ | per-instance 账本待接(KERNELONE_TOKEN_LEDGER 仅旁路验证过) |

验证:`ruff`/`mypy` 全绿;新增 48 测试(25+3 metrics、17 taxonomy、3 console_host)全绿;touched 套件合计 61 passed。

### Phase 1(2026-06-11 启动,进行中)

| 项 | 状态 | 落点 |
|----|------|------|
| A3 did-you-mean 相关性门控 | ✅ | `filesystem.py _suggest_similar_paths`:多组件请求要求尾部重合≥2(必须佐证至少一个目录组件);裸名请求只允许深度≤2 候选;被滤光回落 `repo_rg("<stem>")` 定向引导。纯结构规则零名单(§8 合规)。旧测试按新契约更新+4 回归(run20 双形态:src/main.py 重定向、README.md 深层吸附);24 passed |
| A2 mutation 契约放行验证 | ✅ | 根因:`requires_verification` 仅看消息关键词("test"/"verify"),"修 bug"类任务永远 False → 升级收窄集排除 execute_command → 模型自发验证被判违约(run20 18/18 zero-verification 直接成因)。修复:`retry_tool_batch_after_contract_violation` 中 `requires_verification := keyword OR requires_mutation`(mutation 契约蕴含验证权利);阶梯终点仍是按名强制写工具,写义务不被验证拖延。`include_verification_tools`/`forbidden_tool_names` 既有机制复用,benchmark 禁用名单仍优先。+3 测试;kernel 全量回归 1697 passed |
| B1 仓库身份卡 | ✅(卡片+信号) | `context_gateway/repo_identity.py`(确定性:语言占比+根标记存在/缺失断言+顶层条目;negative 断言按 run20 频谱优先排序防截断丢失;≤1400 字符)+`RepoIdentitySignal`(seed 级默认开启,must-have,priority 2)+gateway accessor/flag(include_repo_identity 默认 True,opt-out)。真实 django-15213 workspace 实测 490 字符,精确否认 main.py/app.py。新增 8+4 测试;kernel 回归 1697 passed×2。**未含**:问题陈述路径预比对(B1 后半,需任务文本通道,下一增量) |
| 在线冒烟 #1(django-15213) | ✅ 完成取证 | **护栏生效实锤**:0 幻觉路径/0 did-you-mean(对照 run20 同实例满谱)、strict 集含 execute_command(A2 通道开通)、模型轨迹首次健康(scout→rg→read_slice×11 真实定位,read 了 expressions.py 正确邻域);标签从 run20 的 5 个降到 3 个(suggestion_induced_misedit/path_hallucination/destructive_overwrite 全消失)。仍 empty_patch:暴露新链路自伤(下行) |
| **P1-fix 大文件 whole-file 误判超时弹**(冒烟揪出) | ✅ | 根因:`_BOOTSTRAP_WHOLE_FILE_REPLACEMENT_MARKERS` 含 "todo:"/"notimplemented",真实大源文件(django expressions.py)必然含这些词 → bootstrap follow-up 强制 edit_blocks→write_file 整文件再生成 → 27B-int4 600s LLM 超时 → "did not materialize response" RuntimeError 炸穿 session。修复:标记扫描加 `_BOOTSTRAP_WHOLE_FILE_MAX_CHARS=4000` 尺寸门(真脚手架才小);+2 回归测试;kernel 1699 passed |
| **vLLM 并发饱和取证**(冒烟 #1-#3 超时的主根因,最终版) | ✅ 已定性,待用户仲裁 | 共享 GPU 上存在**另一个活跃 agent session** 在跑 run10a/run10b 批次(我方 17:30 清场后它 17:31 自动续跑 pred_resume.jsonl)→ decode 21tok/s 被瓜分 → 大 prompt 调用必撞 600s 超时级联。prefill 实测 906 tok/s 正常(排除);abort 机制正常(排除僵尸为主因);客户端 CLOSE-WAIT/agen 未 aclose 泄漏实锤但列加固项。**三次冒烟的 session 死亡均为争抢伪影;护栏内容级证据不受影响**。结论:单 GPU 多 session 并发评测不可行,「负载互斥」必须升级为跨 session 协调(用户仲裁 GPU 归属),平台侧远期可做 vLLM 信号量/锁文件 |
| 在线冒烟 #4(独占 GPU,干净读数) | ✅ | session 首次完整走完(saw_error=False);失败标签 run20 的 5 类→**2 类**(empty_patch+no_verification);幻觉/诱导/契约违约全为 0;模型自愿发写工具。**剩余唯一卡点**:edit_blocks 散文/空操作 6 连败(mutguard=0 → W1.10 强制收窄从未触发,它只挂在"无写批次"违约上) |
| **A8a 形状失败升级触发**(冒烟 #4 直接论据) | ✅ | `contract_guards.batch_write_results_all_failed_on_argument_shape`(严格谓词:有写调用+全部因形状锚点失败+无一成功;stale-edit 等非形状失败不触发防守卫重叠)+ executor 断路器旁后执行 raise(同款先例)→ 进既有 retry 阶梯(末段按名强制+line-range schema guided decoding)。+5 测试;kernel 1704 passed;冒烟 #5 验证中 |
| 在线冒烟 #5(A8a 生效验证) | ✅ 突破+新门论据 | **第一次产出可应用 patch**(applied=True,空 patch 时代结束),且模型自愿编辑 expressions.py(ExpressionWrapper 真实所在,语义邻域正确);但 patch=17 行换 1403 行的破坏性收缩(line-range 大范围重写) |
| **A4a 破坏性收缩门**(冒烟 #5 直接论据) | ✅ | `filesystem.py`:line-range(removed=end-start+1 vs replacement 行数)与 write_file(旧文件行数 vs 新内容行数)双路 fail-closed:removed≥100 且 added≤40%→教学拒绝(error_type=destructive_shrink,retryable,引导窄化范围);阈值与打标器 destructive_overwrite 一致(门与度量同义);+6 测试;toolkit 416+kernel 1704 全绿 |
| **W1.11 JSON-in-blocks 规范化**(冒烟 #6 实锤) | ✅ | 模型把结构完整的 line-range JSON(`[{"start_line":1019,"end_line":1020,"file":"...","replace":"..."}]`)塞进 blocks 参数,被 prose 守卫误杀——"想对的执行被废掉"新形态。修复:`_synthesize_blocks_from_json_payload`(raw 值优先解析——`_normalize_block_input` 的反转义会损坏合法 JSON;start_line/end_line 别名;多元素数组;file 顶层回退;经同一 line-range 路径=收缩门自动继承);+6 测试;toolkit 422+kernel 1704 全绿 |
| **在线冒烟 #7(七件护栏全开,Phase 1 收官读数)** | ✅ 质变 | **单行精确 patch**(expressions.py:997 elif conditional→else,1 行换 1 行,applied=True);同实例演化链:run20 破坏性错误 patch→#4 空 patch→#5 1403 行破坏→#7 单行手术刀。剩余标签 3 个,真实能力缺口只剩:**文件级定位差一步**(模型在 expressions.py 概念正确,gold 在 fields/__init__.py)+**无验证**(模型不自发跑测试)。下一阶段=A7/B2 定位协议+A6 测试环,正是 Phase 2/3 设计内容 |
| A4 evidence-bound 写门(read-receipt 部分) | ⬜ | tool_batch_executor.py:955-983 单 owner;收缩比部分已由 A4a 在 handler 层落地(行数学精确处) |

### Phase 2(2026-06-11 启动)

| 项 | 状态 | 落点 |
|----|------|------|
| A7 scout 锚点持久化 | ✅ | `kernelone/context/scout_anchor_store.py`(workspace/.polaris/runtime/scout_anchors.json;按 path 去重保最高置信;cap 8;置信≥0.2;损坏文件 fail-soft)+ scout handler 探测后写入(fail-soft)+`ScoutAnchorsSignal`(must-have,priority 3,默认开启)+gateway accessor。治「金锚点即得即忘」(run10a 实证)。+10 测试;kernel 1704+toolkit 422 全绿;冒烟 #8 验证中 |
| A6 测试反馈环 | ⬜ | 模型仍 0 自发验证;harness 强制写后验证 batch + 失败回灌(脚本轨实证 +10pts) |
| B2 受约束定位 | ⬜ | 文件级定位差最后一步的根治(RepoIntelligenceFacade 候选+选择题化) |

### Phase 2.5 工厂实战矩阵(2026-06-11 晚,用户指令:50 项目 L1-L8 全链路实跑+审计驱动改进)

| 项 | 状态 | 内容 |
|----|------|------|
| 基建 | ✅ | `scripts/factory_bench/projects_v1.json`(50 项目 L1-L8×软件/网页/游戏)+`kernelone/benchmark/factory_audit.py`(确定性审计:py_compile/html/js_syntax/min_files+产物收集,`factory-audit/1`,8 测试)+`run_factory_bench.py`(pm CLI 全链 runner,串行=GPU 互斥,--max-failed 早停) |
| 链路入口(侦察) | ✅ | `pm --workspace WS --iterations 1 --run-director --requirements-path ABS --director-workflow-execution-mode serial --timeout 1800`=PM→CE→Director→QA 进程内全链;Architect 文档仅 HTTP /v2/factory/runs;--director-iterations=死旋钮 |
| **L1-01 PASS(run #3)** | ✅ | 176 行 calculator.py **功能验证 100%**(优先级 14/括号 20/除零/括号不匹配/非法字符/quit 全过)——全程本地 27B qwen。run #1→#3 修复链:①全局配置角色绑定混杂(pm/CE/qa=MiniMax 云空响应!)→全角色本地 qwen 专用配置;②无 git→runner git init;③PM planning 360s 超时(云延迟假设)→--timeout 1800 |
| 实战驱动的链路修复 | ✅ | W1.11b 嵌套参数名解包(`[{"blocks":"<payload>"}]`)+新文件误用 edit_blocks→教学 write_file(`new_file_via_edit_blocks`);W1.11c 文件名+fence 全文形态→教学 write_file(`whole_file_via_edit_blocks`);各+测试,toolkit 全绿 |
| L1-01 残余 | 📝 | README 任务死于 edit_blocks 3 连败断路器(W1.11c 对症,下批验证);chain_exit=1 尾部噪声;blueprint/verdict 产物缺失待 CE/QA 阶段审计;bootstrap 占位 readme 写着"TypeScript scaffold"(模板与项目无关,PM 文档管线待修) |
| **ContextOS 摘要层三重修复**(批次#1 三连快败揪出) | ✅ | ①`tiered._get_summarizer` 无 TRUNCATION 分支——ADR-0067 Tier-3"绝对安全网"从未在场,SLM 不可用+sumy 缺库 → SummarizationError 炸死整条 planning 链;②force_strategy 剥安全网→强制时追加 TRUNCATION;③安全网产物被质量验证否决仍致命→last_resort 返回。套件 7 预存红测全救活(66 passed+3 sumy skip)。杀伤面:任何 SLM/sumy 不可用环境的所有链路 |
| L1 批次#2 | ✅ 出分 | L1-03(猜数字 Python) PASS;Web 三项目(02/04/05)FAIL——Python 项目通,Web 项目零产出 |
| **流遗弃僵尸修复**(Web 失败取证揪出) | ✅ | 超时在 async-for 体内 raise → 遗弃 provider 异步生成器 → aiohttp 连接不关 → vLLM 对无人读的 socket 继续生成 → **自产僵尸吃一半解码吞吐**(实测 7.7tok/s=21÷~3) → 级联超时/空响应(RAW_RESPONSE output_length=0)。修复:`engine/stream/executor.py` 双消费点改为显式持有 generator + finally aclose(GeneratorExit 穿透到 provider async-with 关闭连接);engine 100+kernel 1704 全绿。CLOSE-WAIT 泄漏挂账项就此关闭 |
| L1-02 复跑(僵尸修复验证) | ⏳ | 运行中 |
| A5 写后语法门 / 批替换静默丢弃修复 | ⬜ | 后者为 P0-2 取证新弹点(模型请求被替换批吞掉无回应) |
| **A5 写后语法门 SHIPPED**(L2-09 r1 取证: game.js:54 一字符滑误 `;`→`,` 废掉 167 行) | ✅ | `filesystem.py` `_syntax_check_file`(py_compile/node --check/json.load)+`attach_post_write_syntax_check`——成功 write_file 上附 syntax_check=passed/failed+窄修复建议,不阻塞;.py 由 PreWriteGuard 写前阻断、.js 走写后诊断的分工契约入测试。L2-09 r3 活体证明(结果带 syntax_check=passed) |
| **声明路径大小写伪失败修复**(L2-09 r2 取证: PM 声明 readme.md,Director 写 README.md,唯一产出被 diff 过滤→director_no_materialized_changes) | ✅ | `_path_matches_declared_candidate`/`_glob_path_matches` casefold(fnmatch 在 Linux 大小写敏感);TestDeclaredPathCaseInsensitiveMatching 5 用例 |
| **FB-2 分级退出码+chain_summary/1+QA 部分证据** | ✅ | `grade_director_exit_code`(部分进展→4/零成功→1)+`grade_qa_exit_code`(director 全成 QA 败→5,不压扁 4/1)+`build_chain_summary` 落盘 runtime/results/chain_summary.json(双写 run_dir);dispatch_pipeline failed>0 但 done>0 → QA 在已完成范围运行(scope=partial_completed_tasks),done=0 仍 fail-closed;runner 三态 clean/partial/fail 消费 exit_class。engine+dispatch+runner 测试 301+282 全绿 |
| **L2-09 r3 全绿** | ✅ | chain=clean, Director 2/2, **qa_ran=True qa_passed=True(首个真实 QA 验证的 L2)**, exit 0, 1100s;贪吃蛇功能审计:墙/自撞/增长/计分/暂停/重开全在 |
| traceback 假头(no running event loop) | ✅ | `_run_sync`×2+`_run_async_from_sync` 探测移出 except 块,真实错误不再背假根因 |
| **验收豁免(verify-exists)** | ✅ | Director 端首次消费 PM 质量门自产机器文法 `verify <path> exists`:no_materialized_changes 判败前评估,全部断言通过+写收据在场→verified_existing_workspace_scope 成功;大小写不敏感存在检查;6 用例 |
| **L2-10 r1 取证: scope 白名单误杀** | ✅ | `_PM_SCOPE_ROOTS` 枚举(无 vendor/assets/public)把合法相对目录判成"outside workspace"→strict 门 3 次重规划全毙→Director skipped。修复=证据驱动 `_is_directory_scope_evidenced`(兄弟具体路径前缀 或 工作区已存在目录);纯文字/无证据裸词仍拒;4 回归用例+救活 8 个 §8 门控遗留红测(域能力测试补 KERNELONE_PM_DOMAIN_TEXT_HINTS opt-in) |
| **L2-10 r2 五层因果链根修(紧急截断吃 user 轮)** | ✅ | 修复 re-ask 消息巨大→gateway emergency_truncate 保 system 弹 history **连最终 user 轮一起弹**→投影纯 system→契约解析空 user→默认 ANALYZE_ONLY(历史继承同因失效)→模型真实发出的 write_file 被 delivery-mode-filter 静默过滤。根修=truncate 摘出最后 user 轮、超限改内容截断(floor 400 chars),永不丢轮;+DELIVERY_CONTRACT_NO_USER_TURN tripwire。kernel 1713 绿 |
| **AGENTS.md 免 JSON 校验** | ✅ | role=pm 一律 _extract_json(```markdown 围栏被当 JSON 解析)烧 2 次云重试→invoke_pm_backend keyword-only validate_output 旗标,agents.py 草稿传 False;runtime 两构造器既有 metadata 消费点直连 |
| **L2-10 r3 取证: 修复轮选错目标** | ✅ | 修复轮把唯一写入花在重写 src/main.js(已存在)而非创建缺失的 src/styles.css——修复消息列出 changed_files 路径,被 extract_target_files_from_message 全文正则播种为候选目标。根修: `_missing_declared_target_files` 机器推导缺失目标(声明 vs 工作区,大小写不敏感)+修复消息 MISSING TARGET FILES 显式块+已存在文件仅报数量+quality_repair_attempts 持久化(法医轨迹)。4 用例 |
| **L2-10 r4 取证: 语义门误杀 placeholder 属性** | ✅ | 模型产出完整单文件 Markdown 预览器,被 `\bplaceholder\b` 命中 `<textarea placeholder="...">` 标准属性杀掉。根修=`\bplaceholder\b(?!\s*[=:])`(属性/键值放行,散文占位话术仍拦);测量公平=js_syntax 接受 HTML 内联 `<script>` 非空、L2-10 min_files 2→1。5 用例 |
| **L2-10 r5 取证: CSS 伪元素误杀 + A5 诊断闭环缺口** | ✅ | ①`.editor::placeholder` 又被语义门杀→placeholder 正则一般化(前缀 .:/引号/-、后缀 =:/-/引号 的代码 token 全放行);②A5 写后诊断是建议性,坏件存活→`check_source_file_syntax` SSOT 落 kernelone/quality,接入 `_scan_file` 首查,语法坏件=质量错误→自动进修复梯;A5 委托 SSOT。新门首杀: 仓内既有测试夹具的 mock package.json 即非法 JSON(潜伏至今)。1071 组合绿 |
| **L2-10 收案(r6 PASS)** | ✅ | chain=partial/exit=4 分级码+部分证据 QA(ran+passed)活体验证;js_syntax ok=语法闭环修复梯实修;功能终验=模型自写 191 行 Markdown 解析器(表格/列表/行内)。第 9 根因: PM 声明 lib/marked.min.js 为写目标(离线不可满足)→质量门 autofix `_strip_unfulfillable_vendored_targets_in_place`(*.min.js/css 剥离+验收清除+自包含指引)。九根因全谱见 memory;残余=任务3(README)因串行停止未运行(re-dispatch 排队) |
| **L2-11 r1: 修复梯收敛实证→进度感知预算** | ✅ | quality_repair_attempts 法医轨迹首秀: 缺失目标契约完美收敛(3缺→2缺→1缺,每次1文件),固定2次预算在差最后一个文件时切断→根修=缺失数严格递减则续命(基2/硬顶5);夹具 py_compile 误设→factory_audit 新 `runnable_any` 形态中立检查(py 或 web 任一可跑)。adapters 601+audit 15 绿 |
| **L2-11 r2: 低温整文件重写复现律** | ✅ | 语法闭环活体(typing.js `endTime: null;` 入质量错误)但修复1整文件重写**复现同一 bug**(W2.6 升级低温×同 prompt=确定性再生),修复2 edit_blocks 因噪声错误文本匹配失败。根修=①node 错误压缩为可操作核心(文件名:行+引用行+脱字符,去堆栈/绝对路径)②SYNTAX REPAIR DIRECTIVE(一个窄 edit_blocks/禁整文件重写)。adapters 631 绿 |
| **L2-11 r3: 语法阻断不升级** | ✅ | PreWriteGuard 阻断 main.py 缩进错后轮次即终(0 产出)——A8a 锚点缺 "Code syntax validation failed"→追加;滑误防御纵深闭合: 写前阻断→升级 re-ask(新)→写后诊断→质量扫描→窄编辑修复梯。kernel 1714 绿 |
| **L2-11 r4: 链绿产品断+QA 虚过** | ✅ | 输出预算截断 typing_test.html(无闭合标签)而链路全绿——①SSOT 增 html 截断签名检查(missing </html>/unclosed <script>),A5+质量扫描+修复梯三处同吃;②新 `workspace_check` CLI(SSOT 全形态查验,活体命中 r4 工作区),verify fallback 无 .py 时弃 compileall 空集虚过改用之;③queue: finish_reason=length 工具轮截断检测。35+54+297 测试绿 |
| **L2-11 r5: void 批吃单批预算** | ✅ | 畸形写(缺 file 参数)批零效应却计入 ADR-0071 单批预算→A8a 升级替换批=第 2 批→KernelGuardError 杀 turn(预存 bug,全 A8a 锚点适用)。根修=A8a 抛出前回滚 void 批计数;tripwire 测试。kernel 1715 绿 |
| **L2-11 r6: 输出顶硬约束+续写指令** | ✅ | A5 时间线实锤: index.html 三次整写 6.8-7.8KB 全截断(~7KB 单文件在 16k 窗口输出顶内整写永不收敛)。根修=截断签名→A5 建议与修复消息均改 append_to_file 续写(勿重写);修复预算进度度量双维化(missing OR errors)。组合 1038 绿 |
| **L2-11 r7: 墙钟经济学+PM 形状引导** | ✅ | PM 主动声明"单文件"=选择不可收敛形状(523s 写 11.5KB 仍截断,append 未及发生即 TimeoutError)。根修=质量门 `_steer_single_file_ui_tasks_in_place`(单 html+交互特征→拆 [html,css,js]+≤150行指引;静态页/已模块化不触发)。297 绿 |
| **L2-11 收案(8 跑 8 根因)** | ✅ | r8: 形状引导成功(合同 3 模块文件)但模型无视契约仍写单体 html——链路判定全程无假绿。残余=27B 指令服从上限(queue: Director 系统提示词常驻约束/W1.10 文件 enum 强制/finish_reason=length 检测)。按矩阵纪律进 L2-12 |
| **L2-12 r1: 空壳假绿+污染误报** | ✅ | 43 行空游戏循环过全部结构检查→新审计类 `content_any:<regex>`(域特征探针,L2-09..12 夹具补齐);英文 goal 触发 WRONG-PRODUCT 误旗(拉丁噪声 0.107 赢相对判定)→绝对下限 max(0.18, own+0.1)。audit 18 绿 |
| **L2-12 r2 + L2 梯队收官** | ✅ | r2 PASS(partial): 123 行真实挡板+球核心,内容探针/污染下限活体验证;L2 终板=09 CLEAN/10 partial/11 truthful-fail/12 partial,全程零假绿。当日 ≈21 根因修复全测试钉住。下阶段: Director 提示词常驻约束+W1.10 enum 强制(服从上限战场)/re-dispatch/链内 acceptance machine-checks |
