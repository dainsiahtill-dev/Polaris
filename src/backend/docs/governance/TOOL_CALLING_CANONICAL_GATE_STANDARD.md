# Tool Calling Canonical Gate Standard

Status: Active  
Gate Script: `docs/governance/ci/scripts/run_tool_calling_canonical_gate.py`

## 1. Purpose

该门禁用于防止“工具名映射掩盖真实错误”。
它只做一件事：验证 `tool_calling_matrix` 的 raw 工具调用身份与观测结果一致。

## 2. Required Input

输入为 `TOOL_CALLING_MATRIX_REPORT.json`：

1. 默认从 `.polaris/runtime/llm_evaluations/*` 自动选最新报告
2. 或通过 `--input-report` 显式指定

## 3. Core Checks

1. `missing_required_raw_tool`:
   case 的 `judge.stream.required_tools` 必须在 `raw_events` 中真实出现
2. `raw_observed_count_mismatch`:
   `raw tool_call` 数量必须与 `stream_observed.tool_calls` 对齐
3. `alias_tool_name_used`:
   raw 工具名若非 canonical 且可被映射到 canonical，直接记为违规
4. `raw_observed_name_drift`:
   同索引位 raw tool 与 observed tool 不一致，直接记为违规

## 4. Policy

允许：

1. 参数别名兼容（参数层）

不允许：

1. 不同功能工具名映射
2. 在评测门禁里把 raw 工具名映射后再判通过
3. 用映射掩盖白名单注入缺失问题

## 5. CLI

Hard fail（推荐）：

```bash
python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py --workspace . --role director --mode hard-fail --report workspace/meta/governance_reports/tool_calling_canonical_gate.json
```

Audit only：

```bash
python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py --workspace . --role all --mode audit-only
```

指定输入报告：

```bash
python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py --workspace . --input-report .polaris/runtime/llm_evaluations/<run_id>/TOOL_CALLING_MATRIX_REPORT.json --mode hard-fail
```

## 6. Governance Wiring

1. Fitness Rule:
   `tool_calling_canonical_identity_non_regressive`
2. Pipeline Stage:
   `tool_calling_canonical_gate`
3. Regression Test:
   `tests/architecture/test_tool_calling_canonical_gate.py`

## 7. Expected Output

脚本输出 JSON，包含：

1. `issue_count`
2. `issues[]`（含 `case_id`, `category`, `evidence`）
3. `target_case_count`
4. `input_report`

`mode=hard-fail` 且 `issue_count > 0` 时返回非 0 退出码。
