# Blueprint — ContextOS / RepoIntelligence localization root-cause audit & fix

- 日期: 2026-06-06
- 作者: Polaris Meta-Architect (Claude Opus)
- 分类: 根因审计 + 修复（KernelOne ContextOS / repo_intelligence / repo_map）
- 触发: SWE-bench Lite 代表性 20 题（seed=42）5% 分数；16/20 空补丁；诉求"白盒审计 ContextOS / 认知运行时，勿归咎模型容量"。

## 1. 根因故障树 (Root-Cause Fault Tree) — 经测量

不是模型太弱，也不是 ContextOS 投影（ProjectionEngine 只投影**对话轮次**，不打包代码库文件）。真因是**定位上下文喂给模型前就已坏死**，三层缺陷叠加：

```
空补丁 (16/20)
└─ 根因 A：解题器静默截断候选文件清单（proximate cause，已测量）
│   scripts/swebench/polaris_solve_one.py: _repo_relpaths()[:400] → file_hint = candidates[:200]
│   django 仓 2545~2757 个 .py；按 git ls-files 字母序只喂前 200。
│   实测：gold 目标文件在"被展示的前 200"里 = 1/14；在 400 硬上限里 = 3/14。
│   ⇒ 13/14 实例模型**根本没见过**目标文件 → 定位不可能。
│
└─ 根因 B：Polaris 自带的定位引擎 RepoIntelligence 被绕过，且自身三处坏死
    （= 用户假设的"indexer emitting empty reference graphs"，逐条证实）
    B1 languages=None 扫 0 文件：facade._iter_source_files 的
       `if lang and self._languages and lang in self._languages` —— 默认 languages=None
       使整条 and 短路为假，scan_repository 产出 0 文件 → 空图。
    B2 bytes/str + tree-sitter API 漂移崩溃：tags._get_tags_tree_sitter
       `parser.parse(content.encode())` 抛 `TypeError: 'bytes' is not 'str'`（本机绑定要 str），
       且 `tree.root_node` 在本机绑定是方法 → `root.children` 抛 AttributeError；
       原 `except (RuntimeError, ValueError)` 两者都不接 → 整个角色调用崩。
    B3 relevance 被文件大小淹没：networkx 缺失 → PageRank 退化为 _compute_simple_ranking，
       而该 fallback 对"命中问题中提到的标识符"只加 +2.0，却对每个定义加 +1.0 →
       一个 200 定义的大文件（baseline 200）压过真正"定义了被提及符号"的目标文件。

认知运行时的 shadow/OSError 兜底**不是**这 16 个空补丁的近因：solver 跑在 SHADOW，
preflight 直接早退，认知中间件根本不在定位回路里。但其"吞错不回传"确实存在 telemetry 饥饿
（见 §3）：solver 拿到 target=(none) 时，没有任何"为什么检索失败"的信号。
```

## 2. 修复 (Implemented & Verified)

| # | 文件 | 修复 | 性质 |
|---|---|---|---|
| B1 | `repo_intelligence/facade.py:_iter_source_files` | `if lang and (self._languages is None or lang in self._languages)` —— None=全语言 | 直接根因 |
| B2 | `repo_intelligence/tags.py:_get_tags_tree_sitter` | parse 先 bytes 后 str 兜底；`get_tags` 整体 try/except (Runtime/Value/Type/Attribute)→regex fallback；`_read_text` 加 OSError | 直接根因 + 纵深 |
| B3 | `repo_intelligence/ranker.py:_compute_simple_ranking` | **relevance-first**：命中"被提及标识符的定义"权重 ×100 压过 log 阻尼的体量；不再被大文件淹没 | relevance 修复 |
| B2' | `repo_map.py`（第二套实现，safety_hardening 测试用） | 同 B2（parse bytes/str + 遍历降级 + OSError）+ fallback 正则改 Unicode 标识符 `[^\W\d]\w*` | 完整性 |
| B4 | `scripts/swebench/polaris_solve_one.py:_ranked_candidates` | get_ranked_files 填充的是 `RankedCandidate.fname`（rel 路径），`rel_fname` 是空默认；读 `fname`（rel_fname 兜底）。否则候选全为空字符串，ranker 形同虚设 | 接线 |

**验证（测量，非断言；fresh repos，已删 .polaris 缓存）**：
- gold 目标 recall@20（solver `_ranked_candidates` 真实路径，无缓存）：字母序[:200] **1/14** → **relevance-first 7/14**；其中 4 个命中在 **rank ≤ 2**（response.py / autoreload.py / tokens.py / util.py），证明 regex-fallback 抽取的 3.2 万 tags + relevance-first 排序真实生效，非缓存假象。
- 端到端：django-11133（原空补丁）现 `target=django/http/response.py`（rank #1）、官方 handler 落盘、compileall rc=0。
- repo_intelligence/safety_hardening/repo_map/intelligent_compressor 测试：**60 fail → 0 fail（70 passed）**。
- ruff/format/mypy：clean（facade/tags/ranker/repo_map/solver）。

## 3. Runtime Telemetry Refactor（高保真失败信号回传，0 崩溃前提下）

原则：检索/认知是增强项，失败必须**降级 + 发信号**，而非静默吞掉空状态。

1. **检索召回信号**：localization 调用 `get_repo_map` 后，记录 `ranked_files` 数、是否命中、是否走了 regex fallback（tree-sitter 降级计数）。当 `ranked_files==0` 或全部低分 → 显式 `degraded=True` 信号，触发 §4 的 grep 兜底，而不是返回空 target。
2. **中间件不再哑吞**：`CognitiveMiddleware.process` 的 except 在降级为 no-op 的同时，于返回 dict 增加 `degraded_reason`（异常类型/摘要），由 RoleRuntime 写入 metadata.context_os_audit → 上层可观测"为什么认知被跳过/失败"。
3. **rollback 不留脏态**：`prepare_rollback` 对坏路径记 `unreadable` 并返回，不写 snapshot（已确保不污染 ContextOS 跟踪内存）；新增计数 `unreadable_targets` 作为信号。

## 4. 解题器接线规范 (Solver Wiring Spec)

`polaris_solve_one.solve()` 定位段替换：
```
idents = extract_identifiers(problem_statement)   # CamelCase + 反引号代码 + 长 snake_case
ri = get_repo_intelligence(workspace)             # languages=None now works (B1)
ri.scan_repository(max_files=4000)
repo_map = ri.get_repo_map(mentioned_idents=idents, max_files=20)
candidates = [c.rel_fname for c in repo_map.ranked_files]   # 相关 top-20，非字母序前 200
# 空兜底（telemetry §3.1 degraded=True 时）：git grep 关键标识符补候选
file_hint = render(candidates_with_oneline_snippet)
```
模型从"200 个字母序裸路径里盲选"变为"20 个按相关度排序、带符号锚点的候选里精选"。

## 5. 风险与边界
- 仅 KernelOne cell 内实现 + 解题器脚本；无公开契约 / effect / Descriptor 变更。
- recall 7/14 是 regex-fallback（tree-sitter 绑定漂移仍降级）下取得；装回 networkx + 校准 tree-sitter 绑定可再升（PageRank + 精确符号），列为后续。
- 剩余 7/14 未命中者多为"问题未提及目标文件中出现的标识符"，需语义/embedding 检索（下一前沿）。

## 6. Vector 2 — 认知运行时盲点审计（2026-06-06 续；已实现并验证）

经 codegraph 逐行核对 `_apply_cognitive_runtime_preflight`（`cells/roles/runtime/public/service.py:1161`）、
`CognitiveMiddleware`（`kernelone/cognitive/middleware.py`）、`RollbackManager`
（`kernelone/cognitive/execution/rollback_manager.py`）。

### 6.1 Defensive Masking（防御性掩蔽）— 精确定性
- **SHADOW 模式**：preflight 在 `mode is not MAINLINE` 时**早退**（`applied=False, reason="shadow_mode"`），
  中间件根本不进定位回路。⇒ solver（跑 SHADOW）的 16/20 空补丁**与认知兜底无关**，证伪"shadow 掩蔽导致空补丁"。
- **MAINLINE 模式**：`middleware.process` 的 `except (RuntimeError, ValueError, OSError)` 把**具体异常**降级为
  `enabled=False`，preflight 随即 `raise RuntimeError("cognitive_runtime_mainline_unavailable")` ——
  **具体根因（哪个 OSError / 哪条路径）被吞掉并替换为泛化错误**。这才是"掩蔽变消音器"的真身：
  不是静默放过，而是**用泛化信号覆盖可执行的具体信号**（telemetry starvation）。

### 6.2 修复（已实现；ruff/mypy clean；cognitive middleware+rollback 30 passed）
| # | 文件 | 修复 |
|---|---|---|
| V2-1 | `middleware.py` | `process` 降级路径统一走 `_degraded_context(reason=...)`：成功=`degraded:False`；构造失败=`orchestrator_init:<Exc>`；process 异常=`process:<Exc>`。区分"主动禁用(reason=None，非故障)"与"尝试启用但失败"。`_get_orchestrator` 记录 `_last_init_error`。 |
| V2-2 | `service.py` preflight | `not enabled` 时把 `degraded_reason` 写入 `metadata["cognitive_runtime_preflight"]` 面包屑并拼进抛出的 RuntimeError（保留原前缀 + `:reason` 后缀），actionable 信号不再被吞。 |
| V2-3 | `rollback_manager.py` `prepare_rollback` | **State Leakage 修复**：unreadable 触发 `raise` 前先 `_cleanup_plan_snapshots(plan_id)`。原先：已为可读目标写入的 snapshot 因 plan 尚未登记（`self._plans[plan_id]=plan` 在 raise 之后）而永远无法被 plan 维度清理 → 脏快照泄漏；现拒绝路径不留脏态。 |

### 6.3 Solver 语义兜底（dense embedding 不可用 → 词法内容检索）
- **事实核验**：`get_default_embedding_port()` 未注入即 `raise RuntimeError`；vLLM 仅服务 gemma 生成模型、
  无 embedding 模型；ollama 未起。`AkashicSemanticMemory` 检索的是 `runtime/semantic/memory.jsonl` 记忆库、
  **非仓库索引**，且无 embedding 时退化为 Jaccard。⇒ "dense embedding 跨仓库向量检索"在本环境**不可建**（不能假造）。
- **可行替代（已实现；4 tests passed）**：`polaris_solve_one._content_ranked_candidates` —— `git grep -c -F`
  统计 issue 标识符在**文件内容**（非仅符号名）中的共现，按 presence+阻尼频次排序、过滤 test 路径。
  由 telemetry 信号驱动：`loc_tel.degraded or len(ranked) < CONTENT_FALLBACK_MIN_RANKED(8)` 时 branch，
  merge 进候选窗口与空目标兜底。直接针对"问题未提及目标文件中定义的符号"这一剩余 miss-mode。

### 6.4 Verification（measured，非断言）
- cognitive middleware+rollback：**30 passed**（+2 新：degraded_reason 信号、snapshot 无泄漏）。
- repo_intelligence（test_repo_intelligence_new + safety_hardening + intelligent_compressor）：**70 passed**。
- solver 内容兜底：**4 passed**。ruff/format/mypy：clean（middleware/service/rollback/solver/test）。
- **零回归（差分证明）**：telemetry/governance/contextos 既有 **17 failed / 5 errors** 在我的 3 个源文件
  `git stash` revert 前后**完全一致**；既有失败为 `telemetry.TracerProvider` mock 漂移等 optional-dep/环境问题，
  越界本审计，非本次引入。
