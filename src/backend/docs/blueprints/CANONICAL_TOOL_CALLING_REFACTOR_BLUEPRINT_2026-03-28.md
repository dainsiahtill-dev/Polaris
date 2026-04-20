# Canonical Tool Calling Refactor Blueprint (2026-03-28)

Status: Active  
Owner: `llm.evaluation` + `roles.runtime` + `kernelone.tools`

## 1. Problem Statement

Current tool-calling evaluation can mask defects when raw tool names are alias-mapped
before final assertions. This creates false pass results and hides whitelist/policy bugs
in real execution.

典型故障模式：

1. LLM 原始输出工具名不在白名单
2. 归一化层把名字映射成 canonical
3. 评测层基于 canonical 结果通过
4. 运行时白名单拒绝或执行漂移，最终线上故障

## 2. Refactor Goals

1. 全链路以 canonical tool name 为合同真相
2. 原始 `tool_call` 事件必须可审计且不可被映射掩盖
3. 白名单拦截前置到 LLM 请求/响应边界，不放在映射层做策略判定
4. 评测门禁必须对 raw tool identity 失败即失败（hard fail）

## 3. Principles

1. Tool identity strictness:
   不同功能工具禁止互为别名。
2. Parameter compatibility:
   参数别名允许在参数层兼容，不改变工具身份。
3. Policy layering:
   白名单、授权、越权拒绝在映射前判定。
4. Audit first:
   保留 raw events 与 observed events，强制做一致性检查。

## 4. Scope

In scope:

1. `tool_calling_matrix` 审计强化
2. Canonical identity gate 落地
3. Governance 规则与 pipeline 接线
4. 相关标准文档更新

Out of scope:

1. 修改业务工具实现逻辑
2. 在评测层引入新的跨功能别名机制

## 5. Deliverables

1. 门禁脚本：
   `docs/governance/ci/scripts/run_tool_calling_canonical_gate.py`
2. 规则接线：
   `docs/governance/ci/fitness-rules.yaml`
3. 流水线接线：
   `docs/governance/ci/pipeline.template.yaml`
4. 回归测试：
   `tests/architecture/test_tool_calling_canonical_gate.py`
5. 标准文档更新：
   `docs/governance/AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD.md`
6. 治理说明文档：
   `docs/governance/TOOL_CALLING_CANONICAL_GATE_STANDARD.md`

## 6. Execution Plan

Phase 1 (P0): Gate foundation

1. 读取 `TOOL_CALLING_MATRIX_REPORT.json`
2. 从 `raw_events` 提取原始 `tool_call`
3. 对比 `raw` 与 `stream_observed.tool_calls`
4. 发现 name drift / alias usage 直接报错

Phase 2 (P1): Governance wiring

1. 将 canonical gate 写入 fitness rules
2. 将 canonical gate 写入 pipeline stage
3. 补齐架构测试，防止规则与流水线回退

Phase 3 (P2): Runtime convergence (follow-up)

1. 收敛 director case fixtures 到纯 canonical 工具集
2. 将兼容组（`required_any_tools`）从跨功能工具名迁移为 canonical-only
3. 补齐全工具覆盖矩阵（每个 canonical tool 至少 1 个正向用例）

## 7. Acceptance Criteria

1. raw tool name 与 observed tool name 出现漂移时，gate 必须 hard fail
2. raw 使用非 canonical 名称时，gate 必须给出可定位证据
3. fitness rule 与 pipeline stage 均可在测试中被检测到
4. 文档明确声明“参数别名可兼容，工具身份不可映射”

## 8. Risk & Mitigation

Risk 1:
历史 fixture 含兼容工具组，短期可能触发更多失败。

Mitigation:
先在 director 关键用例运行 hard-fail，逐步扩到 all roles。

Risk 2:
部分 provider 事件 shape 不稳定导致 raw/observed 数量差异。

Mitigation:
gate 报告中保留计数与逐项 evidence，定位到具体 case/索引位。

## 9. Rollout Command

```bash
python -m polaris.delivery.cli agentic-eval --workspace . --suite tool_calling_matrix --role director --matrix-transport both --provider-id runtime_binding --model runtime_binding --format json
python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py --workspace . --role director --mode hard-fail --report workspace/meta/governance_reports/tool_calling_canonical_gate.json
```
