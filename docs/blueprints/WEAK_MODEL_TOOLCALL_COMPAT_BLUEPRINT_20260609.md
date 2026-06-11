# 弱模型工具调用兼容性 Blueprint (2026-06-09)

## 0. 目标
让本地能力弱 / 输出精度差的模型(如 director 绑定的 gemma-4-12B)也能在正常 Agent 链路里**产出可落地的代码编辑**。延续 [[swebench-normal-mode]] 与 SWE-bench 正常模式打通工作。

## 1. 根因(实测,django-11133 正常模式跑批 + ContextOS 真值日志)
1. gemma **定位正确**(命中 `django/http/response.py`,11× `repo_read_slice`)、**推理正确**(散文准确描述:改 `HttpResponse.__init__`,检测 `memoryview`,用 `.tobytes()`)。
2. gemma 工具调用格式 `<|tool_call>call:TOOL{key:val,key:<|"|>str<|"|>}`;简单参数工具(repo_rg / repo_read_slice)解析正常。
3. **gemma 全程从不发出任何编辑工具调用**——只发 repo_rg / repo_read_slice,把修复写成散文。
4. TransactionKernel mutation-contract 见"只读不写",`build_contract_retry_context` 强制要求写工具并(retry≥3)可切模型;但 gemma 仍只 narrate → 内核 force-inject `edit_blocks` 空参 → "No valid edit blocks found" → 9 次全废 → 空 patch → resolved=False。

**结论**:瓶颈是"弱模型只描述不动手 + 编辑工具门槛太高(必须精确复现 SEARCH 文本)"。

## 2. 方案 A+B(用户拍板,彻底)

### B — 编辑工具人体工学(隔离,低风险;先做)
落点:`polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py::_handle_edit_blocks` + `polaris/kernelone/editing/editblock_engine.py`。

1. **输入归一化** `_normalize_block_input`:
   - 若文本含字面 `\n`/`\t` 转义但无真实换行 → 反转义。
   - 去除包裹的 ```` ``` ```` / ````` ```python ````` code fence。
2. **line-range 编辑模式(核心)**:当调用带 `start`/`start_line` + `end`/`end_line`(+ 可选 `replace`/`new_text`/`new_content`/`code`/`content`)且不含 SEARCH 标记时:
   - 读目标文件,取第 [start,end] 行(1-based,闭区间)为**精确 SEARCH 文本**(由我们读取,必然字节匹配)。
   - 替换文本 = 提供的 replacement(无则用 content)。
   - 合成标准 `<<<< SEARCH:file ... ==== ... >>>> REPLACE` 块,**复用现有 apply/校验/事务路径**(`fuzzy_replace` + `validate_code_syntax` + 两阶段提交)。
   - 收益:消灭"弱模型复现不出精确 SEARCH"的死结——这正是 gemma 已用 `repo_read_slice{start,end}` 表达过的心智模型。
3. 别名键容错:`file|path|file_path|filepath`、`blocks|content|edits|diff`。

不变量:line-range 模式仍走 fail-closed 校验(文件存在、非测试文件由上层契约管、语法不回退)。

### A — "叙述→编辑"转写再问(增强既有 retry,非控制流手术;后做)
落点:`polaris/cells/roles/kernel/internal/transaction/retry_orchestrator.py::build_contract_retry_context`(已存在;现仅说"MANDATORY 包含写工具")。

增强 retry 上下文,把"再问"做成**弱模型可直接照抄的转写任务**:
1. 注入模型**上一轮自己的分析散文**(它已想对的方案)——"你已分析如下…现在把它落成一次编辑"。
2. 注入**目标文件精确切片**(它 `repo_read_slice` 读过的 [start,end] 与内容)。
3. 给**最简编辑形式**(B 的 line-range):"只需提供替换 [start,end] 行的新代码;或一个 edit_blocks SEARCH/REPLACE"。附**针对该文件的具体示例**。
4. 保留现有 force-write 控制流与 `resolve_retry_model_override` 逃生阀,不改。

## 3. 验证
- 单测:B 覆盖 line-range/反转义/去fence/别名键/精确匹配;A 覆盖 retry 上下文含分析+切片+示例。
- ruff/ruff format/mypy/pytest 全绿。
- 重跑 django-11133 正常模式冒烟:gemma 应落下真实编辑(patch_lines>0),观察 resolved。

## 4. 风险与边界
- A 只增强 retry **提示与上下文**,不动 force-write 控制流 → 低回归风险;仍 fail-closed。
- line-range 越界/反向 → 钳制并报错,不静默。
- 不切云模型掩盖本地模型缺陷(用户原则);A 的目的是让弱模型自己把已想对的方案落地。

---

# Phase 2(2026-06-10):自治陷阱级联(Autonomy Trap Cascade)

## 5. 现场实证(django__django-11630,Qwen3.6-27b,vLLM 工具调用已验证可用)

诊断性 1 题捕获运行(`EDIT_BLOCKS_RAW` 临时日志)还原出完整死亡级联——**模型每一步的自救行为都正确,是产品四道机制把它逐级锁死**:

1. **路径猜错**:模型推理正确(E028 db_table 检查),但猜路径 `src/django/core/checks/model_checks.py`(真实为 `django/core/checks/model_checks.py`)→ `read_file` not_found ×5。错误提示只说"用 repo_tree()/glob() 探索"——泛泛建议,弱模型继续盲猜。
2. **FailureBudget 工具级封锁**:第 4 次失败起 `read_file` 整个工具被 BLOCK(`tool_count > 3`,不分参数),错误文本被替换为"STOP attempting this tool"——**底层 not_found 信息被吞掉**,且纠正后的正确路径也无法再读。
3. **mutation-contract 自相矛盾**:implementing 阶段硬禁 glob/repo_tree/repo_rg("broad exploration not allowed")——而 FailureBudget 的 not_found ESCALATE 建议恰恰是"用 repo_tree()/glob()"。模型按产品自己的建议行动(retry attempt=2 发 repo_rg)→ 被合同违例打回。
4. **强制盲编辑终局**:read 被封、搜索被禁、从未见过文件内容 → 被 `tool_choice` 强制发 `edit_blocks` 的模型只能发空参 `{}` 或把叙述文本塞进 `blocks`(实捕:`blocks='Let me first read the main entry point...'`)→ "missing required argument: blocks or start"(135 字符)成为回合终态。bootstrap-read 逃生舱仅在 stale-edit 或纯只读批次时触发,此处不命中。

**结论**:diag5e 0/5 的主因不是模型输出形状不兼容,而是**自治恢复路径被产品机制级联封死**。

## 6. 修复集(全部模型无关、产品级人体工学)

### FIX-1 not_found 错误自带"Did you mean"候选路径(根因杀手)
落点:`filesystem.py` 新增 `_suggest_similar_paths`(有界 os.walk basename 扫描,skip-dirs/文件数上限,按尾部路径段重合度排序,top≤5),接入 read_file 与各编辑工具的 File-not-found 分支。错误即答案,一跳自纠,从源头消灭盲猜循环。

### FIX-2 FailureBudget:可参数自愈的读侧失败不再工具级 BLOCK
落点:`failure_budget.py::record_failure`。`error_type ∈ {not_found, no_match}` 且工具 ∉ WRITE_TOOLS 时,tool_count 超限不再 BLOCK 而持续 ESCALATE(带 FIX-3 纠正建议);**前提 `total <= max_total_per_turn`**——每回合总预算(10)仍是失控循环的最终熔断,写工具行为不变。`is_tool_blocked/get_blocked_tools` 保持计数语义(无行为消费方)。

### FIX-3 错误文案一致化 + BLOCK 不吞真因
- `_escalate_suggestion["not_found"]`:优先指向"Did you mean 候选路径,用 EXACT 路径重试";探索工具仅作"允许时"的次选——消除与 implementing 合同的指令冲突。
- `_block_suggestion`:删除虚假的"Informed user / manual intervention",改为自治可执行指令(换工具/换路径/在终答中如实报告阻塞)。
- `executor/core.py` BLOCK payload:`error = "<真实错误> | <封锁指引>"`,底层 not_found+候选路径对模型保持可见。

### FIX-4 edit_blocks 散文输入 → 教学型错误
落点:`_handle_edit_blocks`。blocks 无 SEARCH 标记且无 line-range 参数时,错误信息内嵌**两种可照抄的完整形式**(SEARCH/REPLACE 块示例 + line-range JSON 示例)+"blocks 里只放编辑内容,不放叙述"。弱模型对示例的模仿能力远强于对描述的理解能力。同时移除 TEMP DIAGNOSTIC 日志(已完成使命)。

### FIX-5 缺参校验错误附教学提示
落点:`contracts.py::validate_tool_step`。`_MISSING_ARG_HINTS["edit_blocks"]`:missing required argument 错误后追加最简 line-range 形式示例——强制 tool_choice 下空参终局从"死刑判决"变为"下一回合可照抄的纠正指引"。

## 7. 验证(Phase 2)
- 单测:not_found 候选路径(命中/无命中/上限)、FailureBudget 读侧豁免(4 次 not_found 不 BLOCK、写工具仍 BLOCK、总预算仍熔断)、散文 blocks 教学错误含两种形式、缺参提示。
- ruff / ruff format / mypy / pytest 全绿(含既有 147 回归)。
- 重跑 5 题诊断(Qwen3.6-27b):预期 read_file 路径一跳自纠、edit 物化率显著上升;以官方 harness 聚合分对比 diag5e。

## 8. 风险与边界(Phase 2)
- FIX-2 豁免仅限读侧 not_found/no_match,且受总预算熔断兜底 → 失控循环风险不升高。
- `_suggest_similar_paths` 有界扫描(≤30000 文件、跳过 .git/node_modules/.polaris 等)→ 大仓性能可控;无候选时行为同旧。
- 全部修复为错误信息/决策层增强,不动 apply/校验/事务路径,fail-closed 不变。

---

# Phase 2 / Wave 2(2026-06-10):bootstrap 逃生舱真正可用化

## 9. diag5f 实例 1 现场审计(wave-1 修复生效后)

FIX-1..6 生效证据:Did you mean ×4、budget 豁免、readonly→bootstrap 点火 ×10、教学提示送达;模型首次发出**路径正确、结构完整**的 SEARCH/REPLACE。但 patch 仍为 0,新一层根因浮出:

1. **SEARCH 文本是幻觉**:`def check_db_table_clash(model)` 在真实文件中不存在——模型凭预训练记忆默写 django 源码。
2. **读结果从未抵达模型可见上下文**:模型 4 次请求读 model_checks.py,读批次全部被拦截转 bootstrap;bootstrap 读结果只进 ledger(turn 级,易失),**不发事件**→ 后续 turn 无任何痕迹。
3. **bootstrap followup 上下文把读结果截到 1200 字符 JSON 碎片**(`build_retry_write_after_bootstrap_context`)——被强制写文件的模型结构性不可能转写出正确 SEARCH。
4. **确定性 write_file 兜底投毒**:`_synthesize_deterministic_bootstrap_write_content` 为 .py 写 `workspace_artifact_ready` 桩、为 dag.service.ts 等写脚手架模板(本身即内核中的业务模板,违反 §8 精神);目标取自失败读路径 → 凭空创建 main.py 等离题文件,"成功写入"回执进一步强化弱模型任务漂移(实测:模型随后自创 ragflow-template-sdk、重写 tests/runtests.py 为 48 行桩)。
5. **CONTENT_GATHERED 写门禁堵死校验性重读**:编辑失败后产品自己的建议是"MANDATORY read_file 校验精确内容",门禁却"Reading more files is blocked"。

## 10. Wave-2 修复

- **P0-A(a) 转写可行性**:followup 上下文对读回执优先提取**真实文件内容**(单文件 ≤9000 字符、总额 ≤16000,信封 `{"ok":..,"result":..}` 解包),非内容回执保留 1200 碎片形式。
- **P0-A(b) 持久可见性**:`execute_read_bootstrap_batch` 对每条读回执经 `emit_event` 发 `{"type":"tool_result", data:{tool,result,bootstrap_read:true}}` → 进入会话事件流/TruthLog,后续 turn 可见;附带修复 Mapping invocation 缺 `call_id` 的 pydantic 边界。
- **P0-B 兜底去毒**:确定性 write_file 仅在目标**由用户消息点名**且**文件不存在**时触发;失败读路径泄入的目标(main.py)一律跳过 → 返回 None → 干净失败交还上层重试。
- **P0-C 校验性重读豁免**:CONTENT_GATHERED 写门禁对"对话尾部存在编辑失败标记 + 纯直接读批次"放行(`_recent_edit_failure_in_context`,8 条回看,7 个失败标记串),消除"必须重读才能修对 vs 禁止再读"的自锁。

## 11. 验证(Wave 2)
- 新测试 `test_bootstrap_followup_weak_model.py` ×11(内容转写/信封解包/总额截断/碎片保留/兜底三态/标记检测/事件发射)。
- 既有 `test_bootstrap_followup_uses_deterministic_write...` 改用隔离 workspace(原用 "." 撞真仓 package.json,恰证明 exists-gate 生效)。
- ruff/mypy 全绿;kernel+tool_execution+handlers 合并回归 **2192 passed**(仅存 HEAD 上即红的 test_speculative_flags 环境性失败)。

## 12. 风险与边界(Wave 2)
- P0-A 内容预算有上限,超大文件截断尾注明确;事件发射 fail-safe(异常仅告警)。
- P0-B 收紧只会减少兜底触发(宁可干净失败,不可投毒);脚手架正用例(用户点名+新文件)保留。
- P0-C 豁免要求批次**纯直接读**(广探索仍拦),且受 max_turns_per_phase / max_auto_turns 总额兜底,无失控读循环。

---

# Phase 2 / Wave 3(2026-06-11):跨回合上下文的"失忆引擎"

## 13. diag5f 终局审计(0/5,但 3/5 产出合法 patch 且 applied=True)

patch 内容暴露纯任务漂移:django 实例升级 package.json 依赖版本、matplotlib 实例重写 web_backend/package.json、sympy 实例把 release/README.md 改成"Fixed critical issue"模板——模型从未触碰真实 bug。机理三连环(`session_orchestrator.py`):

1. **每个内回合 LLM 上下文从零重建**(`turn_context = [system, user(continuation_prompt)]`,无聊天历史)——上一回合的一切只经 envelope.batch_receipt → WorkingMemory 注入。
2. **读内容只携带 500 字符预览,且附虚假完整性声明**("完整内容已通过工具读取,可直接用于修改")——对每轮失忆的模型是假话,直接诱发凭预训练记忆默写 SEARCH;repo_read_slice/around/tail 甚至完全不在注入分支;工具结果总预算 3000 字符装不下一个中等函数。
3. **Instruction 区在 read_files 非空时命令"必须直接写、禁止再读"**——强推盲写。

附带发现:`instance_report` 从 arch_b_converge 导入,用其 `MODEL_NAME="Polaris-V1-Lightweight"` 拼报告路径,normal-mode 报告实际在 `polaris-director-normal/` 下 → **resolved/applied 历史上无条件 False**(评分器瞎了;diag5f 三个 patch 实为 applied=True)。

## 14. Wave-3 修复

- **W3-A 读内容携带可转写化**(session_orchestrator):覆盖全部读工具(`_READ_CONTENT_TOOLS` 含 slice/around/tail),单文件 4000 字符、总预算 12000;slice 读取带行号范围标签;截断诚实化("其余内容未包含…先用 repo_read_slice 精确读取目标行段");小文件全文携带 +"逐字复制"指引;Instruction 区改为"内容足够→立即编辑;不足→精确补读后立即编辑;禁止泛探索;SEARCH 禁止默写"。
- **W3-B bootstrap 回执并入权威回执**(retry_orchestrator `merge_bootstrap_receipt_into_result`):bootstrap 读结果前插进返回结果的 batch_receipt(followup 与确定性兜底两条返回路径)→ envelope → reducer(read_files 记账)→ 下回合 WorkingMemory 可见。
- **W3-C 评分器修复**(swebench_normal_mode):本地 `instance_report` 用本 harness 的 MODEL_NAME;diag5f 实测三例 applied=True 验证通过。
- 附带:MockKernel.execute_stream 签名补 `**kwargs`,复活 7 个因 parent_span_id 漂移而死的编排器测试。

## 15. 验证(Wave 3)
- 新测试:orchestrator 携带 ×3(slice 行号标签+2500 深度哨兵、诚实截断、小文件全文)、merge ×3;全套 39 orchestrator + 14 bootstrap 测试绿。
- 合并回归 **2376 passed**(5 个失败均 HEAD 上原样复现:skill_loader×4 + speculative_flags×1,环境性)。
- run20(20 题,wave-1+2+3 全栈 + 修复后评分器)进行中。

---

# Phase 2 / Wave 4(2026-06-11):幻觉异机绝对路径免疫

## 16. run20 中期审计(wave-3 全栈)
物化率大涨(14 题中 10 题产出 patch 且 applied=True;diag5e 为 0),但 patch 仍漂移(package.json 系)。django-11630 checkpoint 铁证:`read_files=['package.json']` — 10 个 turn 唯一读成功的文件,WorkingMemory 只有它,Instruction 又强制编辑 → 模型编辑了它唯一见过的文件。**漂移吸引子 = 唯一读成功的文件**。读取失败根因(events 实证):模型幻觉**异机绝对路径** `/Users/joey/workspace/polaris/main.py`(macOS 路径,本机为 Linux/dains;源码 grep 无 "joey",纯预训练痕迹)→ `UNSUPPORTED_PATH_PREFIX` 被分类为 unknown → 不在可恢复豁免集 → 3 次烧光 read 预算 → **两次正确路径的读取被连坐封锁**。

## 17. Wave-4 修复
- `error_classifier.py`:"unsupported_path_prefix"/"unsupported absolute path" 关键词归入 **not_found** → 落入 FIX-2 可恢复豁免,读预算不再被幻觉路径烧穿。
- `filesystem.py::_resolve_workspace_rel`:统一解析助手,UNSUPPORTED_PATH_PREFIX → 教学错误("Use a WORKSPACE-RELATIVE path")+ basename did-you-mean 候选;接线 read_file/search_replace/edit_file/line-range/edit_blocks(含 per-block)/append_to_file;write_file 教学错误(无候选,写目标可不存在)。
- 工作区内绝对路径仍正常通过(回归测试钉死)。

## 18. 验证(Wave 4)
新测试 ×4(异机绝对路径教学+候选、分类可恢复、工作区内绝对路径不回归、edit_blocks 路径教学);handlers+tool_execution 428 passed;ruff/mypy 绿。

---

# Phase 2 / Wave 5(2026-06-11):原始只读批次直通 bootstrap(决定性点火修正)

## 19. run10a 审计(wave-4 生效但格局未变)
wave-4 教学错误正常工作(幻觉路径——本轮为 Windows 风格 `C:\Users\user\Desktop\vue-element-admin\...`——均收到 did-you-mean)。但 `read_files` 仍只有 package.json,patch 漂移依旧(django 的 package.json 被重写成 vue-element-admin 模板)。事件流+turn_history 交叉验尸定位**精确机理**:

- 模型 6 次以**正确路径**调用读工具(model_checks.py)→ **0 次出现在任何回执**;
- 失败的 bootstrap 读**全是幻觉路径**;
- 即:原始干净读批次因 implementing 期"无写工具"违例被**整体丢弃**,retry 重新问 LLM,弱模型在重试压力下吐出**更漂移**的调用,FIX-6 把**退化后的批次**送进 bootstrap——点火晚了一步。W3-B 合并机制本身工作正常(turn_history 可见 bootstrap 读+followup 写的合并回执)。
- 另:P0-A(b) 的 dict 事件被事件持久层静默丢弃(0 bootstrap_read 落盘、0 handler 报错)——已知非致命(W3-B 是承重通道)。

## 20. Wave-5 修复
`retry_tool_batch_after_contract_violation(original_decision=...)`:入口处若**原始违例批次本身是安全只读批次**(`is_safe_readonly_bootstrap_invocations`),直接将原始决策设为 bootstrap 候选,**跳过全部 retry 重问**——模型的正确读取请求永不丢弃。接线 stream_orchestrator(TOOL_BATCH except 路径)与 turn_transaction_controller(非流 except 路径 + proxy 透传);非工具决策的 guard 路径无批次,不传。

## 21. 验证(Wave 5)
- 新测试:`test_readonly_original_batch_bootstraps_without_retry_reask`(原始调用原样进 bootstrap、零 LLM 重问);facade mock 签名加 **_kwargs。
- kernel 全量回归 **1805 passed**;ruff/mypy 绿。
- run10b(10 题标准集,wave-1..5 全栈)进行中;观测指标:`bootstrapping the ORIGINAL reads` 点火数、各实例 read_files 是否出现真实源码路径、patch 目标是否离开 package.json 系。
