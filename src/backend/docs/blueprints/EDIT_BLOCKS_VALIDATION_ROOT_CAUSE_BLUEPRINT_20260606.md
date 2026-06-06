# Blueprint — edit_blocks "second validation blocker" root-cause fix

- 日期: 2026-06-06
- 作者: claude-opus-automation-test-director
- 分类: Bug 根因修复（KernelOne 工具执行 / 语法校验门禁）
- 关联: Phase B Task 2；承接 `INTEGRATION_QA_REAL_VERIFICATION_BLUEPRINT_20260606.md` 与 edit_blocks 归一化器 `_edit_blocks.py`（Fix 2，第一处阻断）

## 1. 现象 (Symptom)

SWE-bench Phase A 精修回路中，云端模型起草的、SEARCH 文本与文件**逐字匹配**的合法
SEARCH/REPLACE 块，经官方 `edit_blocks` 处理器（`AgentAccelToolExecutor.execute`
→ `_handle_edit_blocks`）落盘时被拒：

```
Validation failed for 1 block(s). No files were modified.
```

这是继 Fix 2（弱模型把 `blocks` 发成 list 被 schema 拒绝）之后的**第二处校验阻断**，
此前只能让 solver 绕过官方处理器、自行 parse+fuzzy 落盘（硬编码 bypass）。

## 2. 根因 (Root Cause)

调用链：
`filesystem.py:_handle_edit_blocks` → `validate_code_syntax(new_content)`
→ `MultiLanguageCodeValidator.validate` → `PythonCodeValidator.validate`
→ `ast.parse` 成功 → `quick_check(fixed_code)`。

`PythonCodeValidator.quick_check` 在 **ast.parse 已成功之后**仍调用
`_has_indentation_issues(code)`。该启发式把"任意一行的前导空白不是 4 的整数倍"判为
缩进异常，但真实世界代码大量使用 **PEP 8 续行对齐**（实参对齐到左括号列），其前导空白
天然不是 4 的倍数 —— 于是合法文件（如 `requests/sessions.py`）被误判。

更糟的是 `validate()` 的错误聚合循环按 `error.split(":", 2)` 解析，
`_has_indentation_issues` 产生的那条消息没有 `Line N:` 前缀，被静默丢弃，
最终返回 `is_valid=False` 且 `errors=[]` —— **一个没有任何诊断信息的失败**
（违反 fail-closed-with-evidence：调用方据此拒绝编辑却拿不到任何理由）。

实测复现：`validate_code_syntax(<unedited requests/sessions.py>)` →
`is_valid=False, errors=[]`（原始合法文件即被拒）。

## 3. 修复方案 (Fix)

三处、互为纵深防御：

### Fix A — `quick_check` 不再做缩进启发式（`code_validator.py`）
`quick_check` 只在 `validate()` 中 `ast.parse` **成功之后**被调用。一旦 AST 解析通过，
缩进在 Python 语法层面即合法（制表符/空格歧义会抛 `TabError`，已在上游捕获）。因此
`_has_indentation_issues` 在此位置**冗余且必然误报**，移除其调用并删除该死方法。保留
`HALLUCINATION_PATTERNS`（`return0`/`if(` 等可解析但疑似笔误的真实信号）。

### Fix B — 失败必带诊断（`code_validator.py`）
`validate()` 聚合错误时，对不符合 `Line N: ...` 形态的错误串补 `else` 分支原样登记为
`CodeSyntaxError`，杜绝"`is_valid=False` 但 `errors=[]`"的静默拒绝（fail-closed-with-evidence）。

### Fix C — 门禁衡量"编辑的影响"而非"文件既有状态"（`filesystem.py`）
`_handle_edit_blocks` 仅在**本次编辑引入**语法错误时拒绝：`编辑前内容合法 且 编辑后非法`。
若编辑前文件本就不过校验，则编辑非肇因，不应拦截 —— 这使门禁对任何残余启发式误报免疫，
从根上消除"需要硬编码 bypass"的场景。

## 4. 数据流 (Data Flow)

```
draft (SEARCH/REPLACE)
  └─ AgentAccelToolExecutor.execute("read_file") ── 强制 read-before-edit
  └─ AgentAccelToolExecutor.execute("edit_blocks")
        └─ _handle_edit_blocks
              ├─ fuzzy_replace(current, search, replace) → new_content
              ├─ validate_code_syntax(new_content)   # Fix A/B: 合法文件=True，失败必带证据
              └─ introduced_error = (not new_valid) AND (current_valid)   # Fix C
                    └─ 仅此为真才拒绝；否则落盘
```

## 5. 验证 (Verification)

- ruff/format/mypy：clean（`code_validator.py`/`filesystem.py`/测试/swebench scripts）。
- 单元/回归：`tool_execution` + `llm/toolkit` + `editing` 套件 **769 passed**；
  新增 `TestRealCodeNotFalseRejected`（续行对齐合法、quick_check 不误报缩进、失败必带诊断、
  幻觉模式仍生效）。7 个 pre-existing 失败（ripgrep 解析 / nonexistent-path / stream length-audit）
  与本修复无关——stash 本修复后仍失败，已证伪归因。
- 端到端：官方 `AgentAccelToolExecutor.execute("edit_blocks")` 对 `psf__requests-2317`
  从 base 落盘成功（`blocks_applied=1, files_modified=1`），此前为 "Validation failed for 1 block(s)"。

## 6. 风险与边界 (Risks & Boundaries)

- 不改变对**真实**语法破坏编辑的拦截（broken code 仍 `is_valid=False` 且带诊断）。
- `HALLUCINATION_PATTERNS` 保留；`fix()` 在 quick_check 前已中和 `if(`/`return0` 等模式。
- Fix C 对每个代码块多一次"编辑前内容"校验，开销可忽略。
- 仅 KernelOne cell 内实现变更，无公开契约/状态拥有/effect 变化，无需 ADR。
