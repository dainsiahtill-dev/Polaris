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
