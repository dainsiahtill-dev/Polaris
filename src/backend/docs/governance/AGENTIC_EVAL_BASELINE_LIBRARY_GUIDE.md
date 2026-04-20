# Polaris Agentic Eval 基线库说明（BFCL / ToolBench）

状态: Active  
归属 Cell: `llm.evaluation`

## 1. 文档目的

本文用于说明 Polaris `agentic-eval` 的基线库能力，以及 BFCL/ToolBench 分别属于什么类型的测试、覆盖哪些内容。

基线库在 Polaris 的作用：

1. 将外部行业基准资料固化到本地，形成可复现对照。
2. 给内部分数提供参照系，避免“只有分数没有标尺”。
3. 支持模型/Provider 切换后的回归审计与问题定位。

## 2. 快速使用

只下载基线资料，不跑评测：

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --baseline-pull all \
  --baseline-only
```

下载单一来源：

```bash
python -m polaris.delivery.cli agentic-eval --workspace "X:/BaselineTest" --baseline-pull bfcl --baseline-only
python -m polaris.delivery.cli agentic-eval --workspace "X:/BaselineTest" --baseline-pull toolbench --baseline-only
```

控制网络超时与重试：

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --baseline-pull bfcl \
  --baseline-only \
  --baseline-timeout 30 \
  --baseline-retries 3
```

仅做缓存检测（不触网下载）：

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --baseline-pull all \
  --baseline-only \
  --baseline-cache-check
```

强制刷新缓存（忽略已缓存内容，全部重拉）：

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --baseline-pull all \
  --baseline-only \
  --baseline-refresh
```

基于历史评测基线做对比：

```bash
# baseline 传 run_id
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --suite tool_calling_matrix \
  --role all \
  --compare-baseline <run_id>

# baseline 传审计包路径
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --suite agentic_benchmark \
  --compare-baseline .polaris/runtime/llm_evaluations/<run_id>/AGENTIC_EVAL_AUDIT.json
```

针对 Tool Calling 矩阵单独排查 stream / non-stream：

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --suite tool_calling_matrix \
  --matrix-transport stream

python -m polaris.delivery.cli agentic-eval \
  --workspace "X:/BaselineTest" \
  --suite tool_calling_matrix \
  --matrix-transport non_stream
```

默认输出目录：

`./.polaris/runtime/llm_evaluations/baselines/pull-<UTC时间戳>/`

缓存目录：

`./.polaris/runtime/llm_evaluations/baselines/cache/<source>/...`

每次拉取都会生成：

1. 每个来源一个 `SOURCE_MANIFEST.json`
2. 全局一个 `BASELINE_LIBRARY_PULL.json`

## 3. BFCL 是什么类型的测试

BFCL（Berkeley Function Calling Leaderboard）本质是一个**可执行的函数/工具调用评测基准**，重点不是自然语言写作质量，而是工具调用能力本身。

核心评测关注：

1. 工具选择是否正确（tool routing）。
2. 参数提取是否正确（argument extraction）。
3. 单轮与多轮调用是否稳定可执行（single-turn / multi-turn executability）。
4. 在不同提示格式、不同场景下是否一致（format and scenario robustness）。

### 3.1 BFCL 的测试分组（官方分类）

根据官方 `TEST_CATEGORIES.md`，常见组别包括：

1. `single_turn` / `multi_turn`
2. `live` / `non_live`
3. `python` / `non_python`
4. `memory` / `web_search`
5. `all_scoring`（会计入总分的类别集合）

### 3.2 BFCL 的代表性子类（官方类别）

基础与并行/组合调用：

1. `simple_python` / `simple_java` / `simple_javascript`
2. `parallel`
3. `multiple`
4. `parallel_multiple`
5. `irrelevance`

Live（用户贡献）类别：

1. `live_simple`
2. `live_multiple`
3. `live_parallel`
4. `live_parallel_multiple`
5. `live_irrelevance`
6. `live_relevance`

多轮类别：

1. `multi_turn_base`
2. `multi_turn_miss_func`
3. `multi_turn_miss_param`
4. `multi_turn_long_context`

Agentic 扩展类别：

1. `memory_kv`
2. `memory_vector`
3. `memory_rec_sum`
4. `web_search_base`
5. `web_search_no_snippet`
6. `format_sensitivity`（官方说明里属于非计分类诊断项）

## 4. ToolBench 是什么类型的测试

ToolBench 不是单一“打分榜单”，而是一个**数据集 + 训练 + 评测生态**，聚焦真实 API 工具使用。

官方 README 体现的关键规模与特征：

1. 约 3451 个工具、16464 个 API。
2. 约 126K 指令、约 469K API 调用。
3. 含推理轨迹、工具执行过程和执行结果标注。
4. 包含工具检索（retrieval）能力相关数据。

### 4.1 ToolBench 任务类型

ToolBench 数据构建覆盖：

1. 单工具任务（single-tool）。
2. 多工具任务（multi-tool）。
3. 同类别内多工具（intra-category multi-tool）。
4. 同集合内多工具（intra-collection multi-tool）。
5. 检索增强工具调用（open-domain retrieval-assisted tool use）。

其轨迹生成强调 DFSDT（depth-first search decision tree）式多步决策路径。

### 4.2 ToolEval 评测指标

ToolBench 的 ToolEval 主要包含两类指标：

1. `Pass Rate`：在受限调用预算内是否成功完成指令。
2. `Preference`（`Win Rate`）：对两个候选动作序列做偏好比较。

### 4.3 ToolEval 官方测试子集

README 的评测流程中明确要求六个测试子集预测结果：

1. `G1_instruction`
2. `G1_category`
3. `G1_tool`
4. `G2_category`
5. `G2_instruction`
6. `G3_instruction`

工程解读：

1. `G1` 更偏基础/单工具。
2. `G2`、`G3` 更偏复杂多工具和规划能力。

## 5. 与 Polaris 矩阵的对齐关系

Polaris `tool_calling_matrix` 的 7 个级别，可与 BFCL/ToolBench 形成如下对齐：

1. L1/L2 对应 BFCL 的基础路由与参数/类型正确性。
2. L3 对应 BFCL 的 `parallel/multiple` 与 ToolBench 多工具编排。
3. L4 对应 `irrelevance` 场景下的不过度调用能力。
4. L5 对应 ToolBench 风格多步轨迹（DAG/过程质量）。
5. L6 对应工业安全边界与拒绝策略。
6. L7 对应失败后的恢复/自纠正能力。

## 6. 当前边界说明

当前 Polaris `baseline-pull` 的定位是“可复现参考资产拉取”，不是“完整外部基准复刻运行器”。

1. BFCL 完整跑分仍依赖 BFCL 官方环境（如 `bfcl-eval`）。
2. ToolBench 完整数据与 ToolEval 仍需其官方数据包与依赖环境。
3. Polaris 侧先保证：基准资料可审计、可落盘、可对照。

## 7. 调试输出增强（Agent 排障友好）

`agentic-eval` 人类可读输出默认包含：

1. 实时进度条（按用例 + phase 标题）。
2. Top failures + failure diagnostics（含工具调用、事件类型、输出摘要）。
3. `stream` / `non_stream` 分模式观测（工具数、耗时、错误信息）。
4. 聚合修复计划（按 P0/P1/P2 优先级）。

## 8. 参考来源

1. BFCL README: `https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard`
2. BFCL 分类: `https://github.com/ShishirPatil/gorilla/blob/main/berkeley-function-call-leaderboard/TEST_CATEGORIES.md`
3. ToolBench README: `https://github.com/OpenBMB/ToolBench`
4. ToolEval 路径: `https://github.com/OpenBMB/ToolBench/tree/master/toolbench/tooleval`
