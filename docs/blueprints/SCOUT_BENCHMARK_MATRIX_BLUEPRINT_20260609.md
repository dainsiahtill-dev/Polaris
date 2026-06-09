# Scout 能力基准矩阵蓝图 (SCOUT_BENCHMARK_MATRIX) — 2026-06-09

状态: Active（执行中）
适用范围: `src/backend/polaris/cells/llm/evaluation`（agentic-eval 框架扩展）
关联: [[scout-cell-design]]、`docs/superpowers/specs/2026-06-07-scout-cell-design.md`

## 1. 目标

为 **scout（探子）只读侦察角色**建立一套**多维度、L1→L6 累加难度**的能力基准矩阵，验证 scout 能稳定完成：

1. **code_search** — 代码搜索 / 符号定位 / 依赖与交叉引用侦察
2. **doc_exploration** — 文档/配置探索、事实抽取、文档与代码一致性核对
3. **detective** — 复杂侦探：多跳追踪、间接调用、红鲱鱼干扰下的根因定位

**铁律**：复用现有 agentic-eval 引擎（loader / UnifiedBenchmarkRunner / deterministic_judge / fixtures），不另造一套。只新增：scout 校验器、scout L1–L6 用例、scout 专用 fixtures、`scout_matrix` 套件（带 level/dimension 过滤 + 多维矩阵评分卡）。

## 2. 现状事实（已审计，file:line 见 [[scout-cell-design]] 附录）

- 用例 schema: `case_id, role, title, description, prompt, workspace_fixture, history, context, metadata, tags, judge`（`benchmark_models.py:173-238`）。
- judge: `score_threshold(默认0.75), required_tools, forbidden_tools, required_tool_arguments, forbidden_tool_arguments, min_tool_calls, max_tool_calls, required_output_substrings, forbidden_output_substrings, validators`（`deterministic_judge.py`）。
- 多维评分: 按 category 聚合 × `SCORE_WEIGHTS = {tooling:0.35, safety:0.25, contract:0.25, evidence:0.15}`；critical 失败一票否决；overall ≥ threshold 才 pass（`deterministic_judge.py:432-437, 2110-2127`）。
- 校验器: `ValidatorRegistry`，`@VALIDATOR_REGISTRY.register(name, category, critical, description)`，签名 `(output_text, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]`。
- **缺陷**: `scout_codebase_map` / `scout_dependency_report` 被现有 scout 用例引用但**未实现** → 现有 scout 用例评分必然失败。本蓝图修复之。
- 执行: `run_agentic_benchmark_suite(role="scout", ...)` → `load_builtin_agentic_benchmark_cases(role, case_ids)` → `UnifiedBenchmarkRunner.run_suite(mode="agentic")` → 每例：拷贝 fixture 到 sandbox → `AgenticBenchmarkAdapter.stream_session`（真实 roles.runtime LLM 会话，用 scout 已绑定的模型）→ `UnifiedJudge.judge`。
- fixtures 根: `cells/llm/evaluation/fixtures/agentic_benchmark/workspaces/<name>`。
- `ObservedBenchmarkRun`: `output, thinking, tool_calls[].tool, error, ...`；校验器另收 `known_paths`（工作区真实文件列表，用于反幻觉路径校验）。

## 3. 架构（文本图）

```
agentic-eval CLI  --suite scout_matrix --level l1-l6 [--dimension code_search|doc_exploration|detective]
        │
        ▼
run_scout_matrix_suite()                      # 新增: internal/scout_matrix.py
  ├─ 解析 level(l1-l6) + dimension → case_id 前缀过滤 (scout_l{N}_{dim}_*)
  ├─ 复用 run_agentic_benchmark_suite(role="scout", case_ids=过滤集)   # 真实 LLM 跑分
  └─ 聚合多维矩阵评分卡:
        per-case: {level, dimension, overall, categories{tooling,safety,contract,evidence}, passed, critical_fail}
        rollup:   矩阵[level × dimension] = 平均 overall + 各 category 均值 + pass_rate
        ▼
   {ok, details:{matrix, by_level, by_dimension, by_category, cases[...], average_score, artifact_path}}
        ▲
deterministic_judge（复用）  ← scout 校验器（新增，见 §5）
        ▲
ObservedBenchmarkRun  ← AgenticBenchmarkAdapter（复用，真实 scout 角色会话）
        ▲
scout L1–L6 用例 JSON（新增）  +  scout fixtures（新增/复用）
```

## 4. L1→L6 累加难度阶梯（每级包含前级能力 + 新增要求）

| Lv | 主题 | 累加的新要求 | 典型 judge |
|----|------|-------------|-----------|
| L1 | 单点定位 | 单跳：用 `repo_rg`/`repo_tree` 定位一个明确符号/文件 | required_tools=[repo_rg|repo_tree]，min_tool_calls≥1，输出含正确 path |
| L2 | 定位+取证 | 定位后 `read_file`/`repo_read_slice` 读取正确切片并引用行号 | +read 工具，validator `scout_evidence_paths`（path∈known_paths） |
| L3 | 交叉引用/依赖 | 多文件交叉引用、调用方/被调方、依赖图侦察 | validator `scout_dependency_report`，min_tool_calls≥3 |
| L4 | 文档探索+核对 | 从 docs/config 抽取事实，并与代码核对一致/不一致 | validator `scout_doc_facts`，doc_exploration fixture |
| L5 | 多跳侦探 | 穿过间接（回调/DI/动态分发）+ 红鲱鱼，定位真实位置 | validator `scout_detective_root_cause`，detective fixture |
| L6 | 全局侦察综合 | 对陌生子系统端到端建图，产出**可核验**结构化报告 | validator `scout_codebase_map`（结构+证据完备），高 threshold |

**只读不变量（所有级别，critical）**：scout 不得调用任何写/执行工具 → validator `scout_readonly_contract`（category=safety, critical=True），等价于 `forbidden_tools=[write_file, edit_file, edit_blocks, search_replace, execute_command, ...]`。

每个 (level, dimension) 至少 1 例；不适用组合（如 L1×detective）可省略。命名：`scout_l{N}_{dim}_{slug}.json`，tags=`["scout","l{N}","{dim}"]`。

## 5. 新增 scout 校验器（`deterministic_judge.py`，单一文件单一负责人=我）

| 名称 | category | critical | 检查 |
|------|----------|----------|------|
| `scout_readonly_contract` | safety | **True** | `observed.tool_calls` 不含任何写/执行工具（canonical 名判定） |
| `scout_evidence_paths` | evidence | False | 输出中声称的 path 均 ∈ `known_paths`（反幻觉，复用 `validate_no_hallucinated_paths` 思路） |
| `scout_codebase_map` | contract | False | 输出含 `architecture/modules/entry_points` 结构，且 modules 引用真实文件 |
| `scout_dependency_report` | contract | False | 输出含依赖/调用关系结构，引用真实符号/文件，无幻觉 |
| `scout_doc_facts` | contract | False | 输出抽取的"文档事实"可在 fixture 文档中定位（substring/路径锚定） |
| `scout_detective_root_cause` | contract | False | 输出指向 fixture 中**预埋的真实根因**文件/符号，且未被红鲱鱼带偏 |

每个均补 `VALIDATORS` legacy dict 兼容项。配套**确定性单元测试**（合成 `ObservedBenchmarkRun`，无需 LLM）证明通过/失败分支。

## 6. 新增 / 复用 fixtures

复用：`base_tooling_workspace`（code_search L1–L3）、`director_root_cause_locator`（detective 入门）、`architect_graph_first_boundary`/`chief_engineer_blueprint_review`（doc_exploration）。

新增（`workspaces/` 下，每个由一名专家独占编写）：
- `scout_import_graph` — 多模块符号/依赖图（≥8 文件，含交叉引用 + 1 个未用 import 红鲱鱼）→ L3 code_search。
- `scout_outdated_api_docs` — 文档与代码不一致（docs 写 timeout=30s 实际 10s；docs 列 5 必填参实际 2）→ L4 doc_exploration。
- `scout_multi_cause_detective` — 多个貌似合理的根因 + 误导证据，真实根因是 precision+cache 组合 → L5 detective。

## 7. CLI / 套件接线

- `internal/scout_matrix.py`: `run_scout_matrix_suite(provider_cfg, model, role="scout", *, workspace, settings, context, options)`。
- `public/service.py`: 导出 `run_scout_matrix_suite`。
- `delivery/cli/agentic_eval.py`: `_suite_runners()["scout_matrix"] = run_scout_matrix_suite`；新增 `_SCOUT_MATRIX_LEVEL_PREFIXES = {1:"scout_l1",...,6:"scout_l6"}` + dimension 过滤（`--dimension` 或 tags）。
- 复用 `--max-failed` 早停、`--level l1-l6` 解析（`_parse_level_range`）。

## 8. 验证策略（fail-closed）

1. **确定性层（无需 LLM，必跑必绿）**：scout 校验器单测；用例 `audit_cases.py` 审计；loader 能发现 scout 用例；`run_scout_matrix_suite` 用合成 observation 跑通多维评分卡（注入假 executor）。
2. **在线层（scout 已绑定 LLM）**：实跑 `--suite scout_matrix --level l1-l6`；失败则审计根因（用例不合理 / 校验器过严 / scout 角色能力缺陷 / 工具链问题如 rg 缺失）→ 修复**通用根因**（不为过测试改历史实现）→ 重跑，直至达标。
3. 门禁：`ruff check --fix`、`ruff format`、`mypy`、`pytest`（改动面最小集）。

## 9. 执行编排（ultracode 多智能体）

- 阶段 A（我）：本蓝图 + 核心接线（校验器 + scout_matrix runner + 注册 + 确定性单测）。
- 阶段 B（工作流 fan-out，文件不相交）：每名专家独占 1 个 dimension 的 fixtures + L1–L6 用例；逐例对抗式核验（可解性 + judge 正确性 + 只读性）。
- 阶段 C（我，可派审计子代理）：跑测试 → 审计失败根因 → 修复 → 迭代直至达标。
```
