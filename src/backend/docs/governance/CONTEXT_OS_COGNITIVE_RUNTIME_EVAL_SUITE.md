# Context OS + Cognitive Runtime Eval Suite

状态: Active  
适用范围: `polaris/kernelone/context/**` + `polaris/cells/factory/cognitive_runtime/**`

## 1. 目标

这套评测用于做两件事：

1. 客观度量 Context OS 与 Cognitive Runtime 质量
2. 作为 `shadow -> mainline` 的自动化门禁

门禁原则：指标驱动，不靠主观信心。

## 2. 评测资产

1. Case Suite Schema:  
`docs/governance/schemas/context-os-runtime-eval-suite.schema.yaml`
2. Eval Report Schema:  
`docs/governance/schemas/context-os-runtime-eval-report.schema.yaml`
3. Gate Config:  
`docs/governance/ci/context-os-runtime-eval-gate.yaml`
4. Gate Runner:  
`docs/governance/ci/scripts/run_context_os_runtime_eval_gate.py`
5. Template (suite/report):  
`docs/governance/templates/context-os-eval/context-os-runtime-eval-suite.template.json`  
`docs/governance/templates/context-os-eval/context-os-runtime-eval-report.template.json`

## 3. 三层测试面

1. Core Context OS
- fact recovery / decision preservation / open-loop continuity
- artifact restore / temporal correctness / abstention / compaction regret

2. Attention Runtime
- intent carryover / latest-turn retention / focus regression
- false clear / pending follow-up resolution / seal guard / continuity alignment

3. Cognitive Runtime
- receipt coverage
- handoff roundtrip success
- state restore accuracy
- transaction envelope coverage
- SQLite write health (`p95`, failure rate)

## 4. 最小执行流程

1. 产出评测报告（遵循 report schema）
2. 运行门禁脚本
3. 若全部通过，才允许模式晋升

示例：

```bash
python docs/governance/ci/scripts/run_context_os_runtime_eval_gate.py \
  --report docs/governance/templates/context-os-eval/context-os-runtime-eval-report.template.json \
  --run-required-tests \
  --print-report
```

## 5. 结果判定

门禁输出为 JSON，核心字段：

- `passed`: 是否通过
- `recommended_mode`: `mainline` 或 `shadow`
- `failures`: 失败指标列表（包含 section / metric / threshold / actual）
- `suite_runs`: required pytest suite 执行证据

任一阈值失败，或 required suite 失败，都必须停留在 `shadow`。

## 6. CI 接线建议

1. 在 shadow 阶段每次 PR 跑 gate（含 required suites）
2. 将 gate report 落盘到 `workspace/meta/governance_reports/`
3. 仅当 `passed=true` 连续多次稳定，才切 `mainline`

## 7. 注意事项

1. Schema 是契约，不是建议；报告必须符合 schema。
2. Context OS 与 Cognitive Runtime 指标必须分开采集，最后统一 gate。
3. 任何手工覆盖 gate 结果都必须出 Verification Card + ADR。
