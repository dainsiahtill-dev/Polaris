# SWE-bench V12 — RepoIntelligence Localization in the Arch B Convergence Loop

状态: Active (Phase 一 Blueprint)
日期: 2026-06-07
作者: AI 编码代理 (ultracode program「完善并彻底打通所有技术」, 子项目 #1)
权威依赖: `AGENTS.md §10.1`（两阶段执行）, project memory `swebench-phaseb-status`

---

## 0. 一句话

把已存在但悬空的 **RepoIntelligence 仓库地图定位器（#10）+ 图约束符号检索（#9）** 接入 Arch B 收敛循环的每轮定位级联，专治「断言失败测试 → 无 traceback 源帧（`implicated==[]`）→ 落到字母序截断的 `ce_localize`」这一主导失败模式，目标把官方 harness 严格 RESOLVED 从 **8/20 (40%) 提升到 ≥9/20 (45%)**，且 **不引入 embedding、不增加 Docker 轮次、不增加云端 token**。

## 1. 背景与根因（已 codegraph + 多智能体对抗验证）

- 收敛循环 `arch_b_converge.py:384-389` 的定位级联：
  `impl[0]`（traceback 源帧）→ `patched_files()[0]`（seed 补丁文件）→ `ce_localize()`。
- `ce_localize()`（`:177-200`）是本地 gemma 在 `sorted(repo_files)[:200]`（`:183`）上的猜测——**大仓上按字母序截断**，正是 `_ranked_candidates` 当初被造出来修复的 bug。
- 断言失败测试（期望异常未抛出）的 traceback 只指向测试文件，`implicated_files()`（`:146-159`）要求 `path.py:line:` 源帧，故 `implicated==[]`，直接落到弱 `ce_localize`。
- 确定性、**无 embedding**、扫描目标仓的排序器 `RepoIntelligenceFacade`（`polaris/kernelone/context/repo_intelligence/facade.py`，tree-sitter tags + PageRank）已在 `polaris_solve_one.py` 的 `_ranked_candidates`（`:118-148`）/ `_content_ranked_candidates`（`:163-201`）证明可用，但 **未接入** `arch_b_converge.py`（后者只从 solver import 了 apply/role/budget 原语，`:43-48`）。
- 已排除的不可行杠杆（对抗验证发现「可行」结论建立在错误前提上）：
  - codegraph 定位（`codegraph_impact`/`callees`）——codegraph 只索引 Polaris 自身，不索引 `/Temp/swebench-work` 下的临时目标克隆；逐实例建索引未接入且成本过高。
  - 任何 dense/embedding 检索——本环境无 embedding 模型（`get_default_embedding_port()` headless 抛错）。
  - 增大 `max_rounds`——V11 轨迹显示无实例触达轮次上限，瓶颈不在轮数。

## 2. 文本架构图

```
round N harness (real in-container pytest)
        │  test_output.txt → extract_tracebacks → tb
        ▼
   implicated_files(tb)              ── 有源帧? ──► target = impl[0]      (路径不变, 0 成本)
        │ implicated == []
        ▼
   patched_files(patch)             ── seed 触过源文件? ──► target = pf[0]
        │ 都为空（断言失败主导模式）
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ NEW: _candidate_files(problem, f2p_fail, repo_files, ws)         │
 │   ├ _extract_test_symbols(ws, f2p_fail)  ← AST 解析失败测试函数体 │
 │   │     收集被测符号 (Name/Attribute/Call), 过滤测试框架噪声     │
 │   ├ _ranked_candidates(ws, problem⊕test_symbols)  ← RepoIntel    │
 │   │     (#10 tree-sitter + PageRank 仓库地图, 无 embedding)       │
 │   └ degraded/thin → _content_ranked_candidates (git grep 共现)   │
 │   → 排序后的非测试源文件列表 (确定性, 稳定顺序)                  │
 └─────────────────────────────────────────────────────────────────┘
        │  跨轮假设级联: _next_hypothesis(cand, tried)
        ▼
   target = 第一个未试过的候选        （仅 empty-frame 路径, 云成本不变）
        │ 候选耗尽
        ▼
   ce_localize()                     ── 降级为最后兜底
```

## 3. 模块职责

| 符号 | 位置 | 职责 | 复用 |
|------|------|------|------|
| `_resolve_test_file(node_id, repo_files)` | arch_b_converge.py (新) | pytest node id → 仓库相对测试文件路径（支持 `path.py::...` 与 dotted 模块两种形态、testbed 前缀、后缀匹配） | — |
| `_test_func_nodes(tree, func_name)` | arch_b_converge.py (新) | 在 AST 中定位具体失败测试函数（剥离 `[param]` 参数化后缀） | — |
| `_extract_test_symbols(ws, failing, repo_files)` | arch_b_converge.py (新) | AST 收集失败测试体内被测符号作为定位 ident（无源帧时的最强信号） | — |
| `_next_hypothesis(candidates, tried)` | arch_b_converge.py (新) | 跨轮选取下一个未试候选（纯函数, 可单测） | — |
| `_candidate_files(problem, failing, repo_files, ws)` | arch_b_converge.py (新) | 融合 issue ident + 测试符号 → RepoIntelligence 排序 + git-grep 兜底 → 非测试源文件候选 | `_ranked_candidates`, `_content_ranked_candidates`, `_extract_identifiers`, `_is_test_path`, `CONTENT_FALLBACK_MIN_RANKED` (solver) |
| `converge()` refine 段 | arch_b_converge.py (改) | 接入候选定位 + 跨轮假设推进 + 错误假设回退 + 把定位结果写入 ContextOS projection 的 confirmed_facts | `RepoIntelligenceFacade` (#10), `ProjectionEngine` (#9 控制面) |

## 4. 核心数据流（关键不变量）

1. **公共路径零成本**：有 traceback 源帧或 seed 补丁文件时，定位与今天完全一致——不触发 RepoIntelligence、不增加云 token。新逻辑仅在 `implicated==[] 且 patched_files==[]`（断言失败主导模式）时启动。
2. **跨轮假设级联**：候选列表对一个实例计算一次并缓存（确定性、稳定顺序）。每个 empty-frame 轮取 `_next_hypothesis` 的首个未试候选，记入 `tried_targets`。
3. **错误假设回退**：若上一轮的假设目标未带来 FAIL_TO_PASS 进展（`len(f2p_pass)==0`），在本轮 refine 起始处 `git checkout <base_commit> -- <last_hypothesis_target>` 回退该错误编辑——否则 `patched_files` 下一轮会一直返回该错误文件，假设级联无法推进（这是回退的根因，不是洁癖）。一旦某假设带来进展，`patched_files` 接管，系统锁定该文件继续精修。
4. **控制面真实生效**：所选 target 与其来源（`traceback`/`patch`/`repomap`/`ce`）写入 ProjectionEngine 的 `confirmed_facts` 与 `tail_hint`，使 ContextOS 投影真实反映「系统在哪定位」——这正是让 #9/#10 在控制面「真正发挥作用」。

## 5. 技术理由

- **最高 (期望增益 / 成本)**：在可行杠杆中，把确定性本地排序器接入 empty-frame 路径，直接命中主导失败模式，且 `ce_localize` 在该路径被移除（略省）。~100–150 LOC，单文件改动，低爆炸半径。
- **完善并彻底打通**：`get_repo_intelligence`/`get_repo_map` 当前仅 1 个调用方（solver）、无覆盖测试；接入收敛循环让 #10 在 Phase-B 每轮控制面路径上首次生效，并统一 Phase-A/Phase-B 的定位信号。
- **Reuse First（AGENTS.md §4.2.1）**：不新造定位器，复用 solver 既有原语；新增仅为「测试符号 AST 抽取 + 跨轮假设状态机」这两块收敛循环特有的编排。

## 6. 验证计划（fail-closed）

1. `ruff check <files> --fix` + `ruff format <files>` 静默。
2. `mypy <files>` Success（新函数全类型注解）。
3. `pytest scripts/swebench/test_arch_b_converge.py -q` 全绿：
   - `_resolve_test_file`：`path.py::Cls::m`、dotted、testbed 前缀、后缀匹配。
   - `_extract_test_symbols`：合成测试文件 → 命中被测类/函数符号、过滤框架噪声。
   - `_next_hypothesis`：跳过已试、耗尽返回 ""。
   - `_candidate_files`：在非 git 临时目录下优雅降级返回 `[]`（不抛异常）。
4. **测量门禁（真凭实据，下一步执行）**：对 V11 未解决的 ~11 个实例跑官方 harness A/B，证明 empty-frame 实例现在拿到非字母序、非空 target，且严格 RESOLVED 跨过 40%（目标 ≥45%）。该步需 isolated harness venv + Docker + 云 Kimi + 本地 gemma，作为子项目收尾的独立测量轮次。

## 7. 风险与边界

- AST 解析失败/找不到测试函数/非 git 目录：全部 `try/except` 降级，定位级联永不因新代码崩溃（与 solver 同纪律）。
- `_ranked_candidates` 每次 `clear+scan`：仅 empty-frame 路径触发且按实例缓存，最多每实例一次扫描。
- 回退误伤：仅回退「我们经 RepoIntelligence 选中的假设文件」，不碰 seed/traceback 命中的文件。
- 不改共享内核 `_edit_blocks` / RepoIntelligence 本体（零跨 Cell 契约变更）；全部改动落在 `scripts/swebench/`（活跃 testbed，AGENTS.md §5 旧根工具区，新逻辑就近且低爆炸半径）。
