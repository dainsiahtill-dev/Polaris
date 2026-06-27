# Polaris Task Execution Audit Specification

状态: Active draft  
适用范围: Polaris 后端 `src/backend`、PM -> Chief Engineer -> Director -> QA 执行链路、Resident AGI 监督链路  
目标: 把现有 PM 合同、CE 蓝图、任务执行画像、工具授权、最终 LLM 请求审计、Run Ledger、ReceiptStore 和 QA 裁决收敛成同一套长期契约。

本文件不是新的旁路架构。它是对 Polaris 现有治理对象的收敛规范。实现时必须优先复用现有 Cell、KernelOne、ContextOS、Run Ledger、Job Token、ReceiptStore、Verifier/Gate Policy 和公开 public contract。

## 0. 当前事实与目标态

### 0.1 当前事实

Polaris 已经存在以下事实对象或能力:

- PM 任务合同和 legacy task 同步。
- Chief Engineer blueprint、quality gate、risk register、`evaluate_handoff_decision_for_blueprint`。
- `task.execution_profile.v1` 和 Director execution strategy。
- 最终 provider request audit、ContextOS snapshot、Run Ledger、ReceiptStore、tool/effect receipt。
- Job Token 作为 capability token 的雏形。
- Resident AGI evidence interface，可读取 task execution profile、Run Ledger、audit verdict、ContextOS、Director repair strategy catalog 等证据。

### 0.2 目标态

目标不是继续堆提示词，而是形成以下执行链:

```text
Validated PM Contract Snapshot
  -> Immutable CE Blueprint Snapshot
  -> CE Handoff Decision
  -> Immutable Execution Envelope
  -> Capability-Enforced Director Tools
  -> Final Provider Request Receipt
  -> QA / Verifier Verdict
  -> Run Provenance Bundle
```

### 0.3 禁止误读

- `handoff_ready` 只能是 UI 或摘要字段，不能作为授权字段。
- CE LLM overlay 只能是 advisory，不能扩大 PM authoritative scope。
- Director prompt 只指导模型，不授予权限。
- Job Token / capability token 是从控制面事实源派生的授权证据，不能成为第二事实源。
- AGI 可以读取证据、提出建议、请求受控执行，但不能绕过 PM -> CE -> Director -> QA。

## 1. 核心安全目标

1. 每次 Director 执行都能证明: 谁授权、基于哪个 PM contract、哪个 CE blueprint、哪个 execution profile、允许写哪些路径、允许执行哪些命令。
2. 每次 LLM 调用都能证明: 最终 provider request 里真实包含了哪些 messages、tools、response_format、temperature、max_tokens、context refs 和 coverage flags。
3. 每次文件写入和命令执行都能证明: 使用了哪个 envelope/capability、写了哪些文件、before/after hash 是什么、QA 如何验证。
4. 任一入口不得复制本地 handoff 规则。所有 Director dispatch 必须走同一类 handoff decision 服务。
5. 任一失败不得被吞成成功。QA failed、tool failed、audit missing、context missing 都必须在 Run Ledger 中保留可追踪状态。

## 2. 权威对象

### 2.1 Validated PM Contract Snapshot

PM LLM 原始输出不是权限来源。只有经过 schema validation、policy normalization 和 project policy check 的 PM Contract Snapshot 才是 authority。

必须至少包含:

- `task_id`
- `goal`
- `scope_paths`
- `target_files`
- `acceptance_criteria`
- `deterministic_checks`
- `delivery_plan_document`
- `delivery_depth_contract`
- `contract_hash`
- `policy_version`

### 2.2 Immutable CE Blueprint Snapshot

CE blueprint 是 PM contract 的技术施工蓝图。它可以补充模块边界、接口定名、验证命令、风险和交接证据，但不能扩大 PM authority。

必须绑定:

- `task_id`
- `pm_contract_hash`
- `blueprint_hash`
- `execution_profile_hash`
- `policy_version`

### 2.3 CE Handoff Decision

Director dispatch 的唯一授权来源是 `ce_handoff_decision.v1.allowed == true`。

该 decision 必须绑定当前:

- `task_id`
- `pm_contract_hash`
- `blueprint_hash`
- `execution_profile_hash`
- `policy_version`

缺少任一绑定时必须 fail-closed。

### 2.4 Execution Envelope

`execution_envelope.v1` 是 Director 执行前由系统生成的不可变执行信封。它聚合 contract、blueprint、handoff decision、execution profile、授权范围、模型策略、预算策略和审计策略。

职责边界:

- Prompt 负责指导模型。
- Envelope 负责定义权限和预算。
- Tool kernel 负责强制执行权限。
- Run Ledger / ReceiptStore 负责记录证据。

### 2.5 Capability Token

Capability token 是 run-scoped、task-scoped、envelope-scoped 的能力凭证。

工具层不得只检查 `role == director`，必须检查:

- token 未过期。
- `run_id` 匹配。
- `task_id` 匹配。
- `envelope_hash` 匹配。
- 路径在 `allowed_write_paths` 内。
- 命令在 `allowed_commands` 内。
- `policy_version` 匹配。

## 3. 任务状态机

合法状态转换:

```text
CREATED
  -> PM_CONTRACT_DRAFTED
  -> PM_CONTRACT_VALIDATED
  -> PM_CONTRACTED
  -> CE_BLUEPRINT_DRAFTED
  -> CE_BLUEPRINT_VALIDATED
  -> CE_HANDOFF_DENIED
  -> PM_REPLAN_REQUIRED | CE_REPLAN_REQUIRED

CE_BLUEPRINT_VALIDATED
  -> CE_HANDOFF_ALLOWED
  -> EXECUTION_ENVELOPE_CREATED
  -> DIRECTOR_DISPATCHED
  -> DIRECTOR_EXECUTING
  -> QA_PENDING
  -> QA_PASSED
  -> CLOSED

QA_PENDING
  -> QA_FAILED
  -> DIRECTOR_REPAIR | CE_REPLAN_REQUIRED | PM_CLARIFICATION_REQUIRED | INFRA_RETRY | HARD_STOP
```

### 3.1 状态进入规则

| 状态 | 必须输入 | 必须输出 | 禁止事项 |
| --- | --- | --- | --- |
| PM_CONTRACT_VALIDATED | PM raw intent | validated contract snapshot | 直接把 PM LLM raw output 当 authority |
| CE_HANDOFF_ALLOWED | validated PM contract, CE blueprint, execution profile | handoff decision | 只凭 `handoff_ready` 放行 |
| EXECUTION_ENVELOPE_CREATED | handoff decision allowed | immutable envelope | 执行时重新读取可变 blueprint |
| DIRECTOR_DISPATCHED | envelope, capability token | Director role turn | PM 直接调 Director |
| DIRECTOR_EXECUTING | final provider request, tool schema | tool/effect receipts | prompt 自行扩大权限 |
| QA_PENDING | execution result | QA verdict | 失败被标成功 |

## 4. Authority Matrix

| 字段 | 来源 | 是否权威 | 是否可覆盖 | 下游用途 |
| --- | --- | --- | --- | --- |
| `task_id` | Validated PM Contract | 是 | 否 | 所有对象绑定 |
| `target_files` | Validated PM Contract | 是 | CE 不可覆盖 | path guard / write guard |
| `scope_paths` | Validated PM Contract | 是 | CE 不可覆盖 | read/write scope |
| `acceptance_criteria` | Validated PM Contract | 是 | CE 不可覆盖 | QA / verifier |
| `delivery_depth_contract` | Validated PM Contract | 是 | CE 不可覆盖 | anti-hollow delivery |
| `construction_plan` | CE blueprint / overlay | 建议 | 不适用 | Director prompt |
| `ce_suggested_files` | CE overlay | 否 | 不适用 | 提示候选 touch points |
| `risk_assessment.blocking_risks` | CE gate / risk register | 是 | 只能通过治理动作解除 | handoff decision |
| `risk_assessment.non_blocking_warnings` | CE gate / risk register | 建议 | 可更新 | Director prompt / QA checklist |
| `temperature` | execution profile | 是 | runtime 不可擅改 | provider request |
| `tool_choice` | execution profile + envelope | 是 | runtime 不可擅改 | provider request |
| `allowed_write_paths` | execution envelope | 是 | Director 不可覆盖 | tool guard |
| `allowed_commands` | execution envelope | 是 | Director 不可覆盖 | command guard |

命名要求: 避免使用 `scope_for_apply` 表示 advisory scope。推荐使用 `ce_suggested_files`、`recommended_touch_points` 或 `candidate_implementation_scope`。

## 5. Execution Envelope Contract

机器可读 schema: `docs/governance/schemas/execution-envelope.schema.yaml`。

关键字段:

```yaml
schema_version: polaris.execution_envelope.v1
envelope_id: ...
run_id: ...
task_id: ...
workspace: ...
pm_contract:
  ref: ...
  hash: ...
ce_blueprint:
  ref: ...
  hash: ...
handoff_decision:
  ref: ...
  hash: ...
  allowed: true
execution_profile:
  ref: ...
  hash: ...
authorization:
  allowed_read_paths: []
  allowed_write_paths: []
  allowed_commands: []
  target_files: []
  scope_paths: []
model_policy:
  model: ...
  temperature: 0.25
  top_p: ...
  max_tokens: ...
  response_format: ...
  tool_choice: ...
  tool_schema_hash: ...
budget_policy:
  input_budget_tokens: ...
  output_budget_tokens: ...
  tool_call_budget: ...
  command_timeout_seconds: ...
  repair_attempt_budget: ...
audit_policy:
  required_evidence: []
  final_provider_request_required: true
validity:
  created_at: ...
  expires_at: ...
  policy_version: ...
envelope_hash: ...
```

不变量:

- `handoff_decision.allowed` 必须为 true。
- `pm_contract.hash`、`ce_blueprint.hash`、`execution_profile.hash` 必须与 handoff decision 绑定值一致。
- `tool_receipt.execution_envelope_hash` 必须等于当前 `envelope_hash`。
- 执行期间不得按 `blueprint_id` 重新读取可变蓝图作为授权依据。

## 6. Provider Request Audit

最终 provider request 是 LLM 上下文的唯一事实源。UI、prompt 预览、messages projection、RoleProfile whitelist、日志摘要都不能替代它。

必须记录:

- provider、model、model version 或 fingerprint。
- messages hash、完整 messages ref。
- tools 是否存在、tools hash、tool_choice。
- response_format。
- temperature、top_p、max_tokens、seed、stop。
- cache_key、cache_hit、fallback_chain。
- context_snapshot_ref、context_snapshot_hash。
- execution_profile_hash、envelope_hash。
- request_hash、response_id、response_hash。

Cache key 必须包含所有会影响输出的 provider request 参数，至少包括:

- model
- temperature
- top_p
- max_tokens
- tool schema hash
- tool_choice
- response_format
- execution_profile_hash
- context_snapshot_hash
- envelope_hash

## 7. Evidence Coverage Audit

不要把 `min_context_utilization` 设计成“prompt 越长越好”。Polaris 应奖励必要证据覆盖，而不是奖励上下文填充。

机器可读 schema: `docs/governance/schemas/final-request-evidence-coverage.schema.yaml`。

推荐 coverage 对象:

```yaml
context_coverage:
  required_refs:
    - pm_contract
    - ce_blueprint
    - handoff_decision
    - execution_profile
    - target_files
    - acceptance_criteria
    - delivery_depth_contract
    - language_best_practices
    - failed_gate_evidence
  included_refs:
    - pm_contract
    - ce_blueprint
  missing_required_refs:
    - target_files
  coverage_ratio: 0.22
  pass: false
```

Director / CE / PM 的上下文健康判断必须同时看:

- 最终 provider request token。
- required evidence coverage。
- target files/source summaries coverage。
- CE blueprint coverage。
- acceptance criteria coverage。
- language/task prompt profile coverage。
- tool schema availability。

### 7.1 结构化覆盖要求

每个 coverage flag 必须尽量携带:

- `present`
- `source`
- `ref`
- `hash`
- `confidence`
- `freshness`

字符串命中只能标记为 `confidence=text_heuristic`。只有绑定 PM contract、CE blueprint、Run Ledger、ContextOS snapshot、receipt 或 execution envelope 的 ref/hash 时，才能作为高置信度审计证据。

### 7.2 工具覆盖要求

最终 provider request audit 必须能回答:

- 当前角色/任务要求哪些工具。
- 最终 `provider_request.tools` 实际提供了哪些工具。
- 哪些 required tools 缺失。
- 是否出现工具 schema 裁剪。
- 裁剪原因是什么。
- ToolSpecRegistry aliases / arg_aliases 是否进入 schema 或归一化证据。

### 7.3 角色身份一致性

最终 provider request 的首条 system message、role metadata、run_id、trace_id 和 expected role 必须一致。PM、Chief Engineer、Director、QA 的 system prompt 串线必须视为 P0。

## 8. QA 失败分类

QA failed 不能默认回到 Director repair。必须分类:

| 类别 | 去向 |
| --- | --- |
| IMPLEMENTATION_DEFECT | Director repair |
| SCOPE_MISMATCH | CE replan |
| CONTRACT_AMBIGUOUS | PM clarification / PM revision |
| TEST_ENVIRONMENT_FAILURE | infra retry / quarantine |
| ACCEPTANCE_INVALID | PM/QA correction |
| SECURITY_POLICY_VIOLATION | hard stop |

每条 QA failure 必须包含:

- `class`
- `severity`
- `repairable_by_director`
- `requires_ce_replan`
- `requires_pm_revision`
- `evidence_refs`

## 9. AGI 集成边界

Resident AGI 是平台角色，不是旁路执行器。

AGI 可以:

- 读取 audit、Run Ledger、ContextOS、Verifier、Director repair catalog、execution profile。
- 对架构方案、依赖选型、质量门禁响应、是否请求 CE/Director/QA 继续动作做智能判断。
- 生成 advisory suggested rules，例如 repair pattern 建议。
- 请求受控执行。

AGI 不可以:

- 直接生成或覆盖 authoritative PM contract。
- 直接改写 CE handoff decision。
- 直接标记失败 gate 为通过。
- 直接扩大 Director allowed_write_paths 或 allowed_commands。
- 绕过 PM -> CE -> Director -> QA。

AGI 输出中涉及 repair 的字段必须默认为 advisory:

```yaml
repair_advisor_note:
  authoritative: false
  suggested_rules: []
  confidence: 0.0
  evidence: []
```

## 10. Path Safety Invariants

写入和命令执行 guard 必须处理:

- canonical path 后再校验。
- 禁止 `..` 路径逃逸。
- 禁止 symlink 逃逸。
- 禁止绝对路径绕过。
- 禁止大小写不敏感文件系统绕过。
- 禁止 Unicode normalization 绕过。
- 禁止 glob 过宽。
- 禁止临时文件 rename 到 scope 外。
- 禁止 generated file 间接覆盖 scope 外文件。

必须保留 negative tests:

| 反例 | 期望 |
| --- | --- |
| `../../outside.py` | 写入拒绝 |
| `src/allowed/../outside.py` | canonicalize 后拒绝 |
| `symlink_to_outside/file.py` | 写入拒绝 |
| `SRC/File.py` vs `src/file.py` | case policy 明确 |
| NFC/NFD Unicode 文件名 | normalization policy 明确 |
| `allowed.py.tmp -> outside.py` rename | 写入拒绝 |

## 11. Threat Model

| 风险 | Polaris 对应缺陷 | 控制措施 |
| --- | --- | --- |
| Prompt injection | 用户输入、repo 文件、issue 文本诱导越权 | system priority、tool guard、scope guard |
| CE overlay escalation | CE 建议被误当权限 | authority matrix、schema validation |
| Stale blueprint replay | handoff 时合法，执行时蓝图已变 | immutable snapshot、hash binding |
| Tool schema leak | PM probe 暴露 Director tools | final provider request assertion |
| Cache key drift | 温度/工具变了但命中旧缓存 | cache key 包含输出相关参数 |
| TOCTOU | 检查与执行读不同对象 | envelope hash 和 snapshot ref |
| Excessive agency | Director 自行扩权 | capability token |
| Sensitive information disclosure | ContextOS/ledger 泄露 secret | redaction、evidence classification |
| Unbounded consumption | repair loop/tool loop/token 失控 | budget policy、attempt cap、timeout |

## 12. 反例驱动测试矩阵

| 不变量 | 反例输入 | 期望 |
| --- | --- | --- |
| 禁止 PM -> Director | generic orchestration 直接 `role=director` | 409，无 Director dispatch |
| 禁止 stale blueprint | `blueprint_hash` 不匹配 | handoff denied |
| 禁止 CE overlay 扩权 | CE 建议 scope 外文件 | authority 不变，写入被拒 |
| PM route probe 无工具 | provider request 包含 `repo_tree` schema | 测试失败 |
| provider request 为准 | audit temperature 与 invoker request 不一致 | 测试失败 |
| path guard 生效 | `../outside.py` | 写入拒绝 |
| symlink guard 生效 | target 内 symlink 指向外部 | 写入拒绝 |
| QA 不吞失败 | verifier failed | run 不得 CLOSED/PASSED |
| cache key 完整 | messages 相同但 temperature 不同 | cache key 不同 |
| role-session 不绕过 | export-to-director 无 handoff | 409 |

## 13. Final Request Evidence Coverage

机器可读 schema: `docs/governance/schemas/final-request-evidence-coverage.schema.yaml`。

`final_request_evidence_coverage` 是 `llm.final_request_context_audit.v1` 下的结构化证据矩阵。它不替代原有 token 统计，也不把“窗口利用率高”误判为“上下文充分”；它只回答最终 provider request 是否实际包含当前角色所需的权威证据和工具 schema。

必须记录:

```yaml
schema_version: polaris.final_request_evidence_coverage.v1
request_hash: ...
role_id: director
expected_role_id: director
role_identity_ok: true
required_refs:
  - pm_contract
  - ce_blueprint
  - target_files
  - execution_profile
  - execution_strategy
  - execution_envelope
included_refs: []
missing_required_refs: []
required_tools: []
available_tools: []
missing_required_tools: []
workflow_chain:
  pm_contract_hash: ...
  ce_blueprint_hash: ...
  handoff_decision_hash: ...
  execution_profile_hash: ...
  execution_envelope_hash: ...
ledger_evidence:
  run_ledger_ref: ...
  receipt_refs:
    - chief_engineer_blueprint
coverage_ratio: 1.0
pass: true
```

审计规则:

- `coverage` 旧字段只保留为 UI/兼容层的文本启发式信号；`final_request_evidence_coverage` 是后续 hard gate 的候选事实源。
- 如果 prompt 或 metadata 声明 `required_tools`，最终 provider request 的 `tools` schema 中必须能逐项找到对应工具名，否则记录 `missing_required_final_request_tools`。
- 如果 execution strategy 或 execution envelope 声明 `required_evidence`，最终请求必须包含映射后的证据引用；缺失时记录 `missing_required_final_request_evidence`。
- Director 请求必须能把 PM contract、CE blueprint、handoff decision、execution profile 与 execution envelope hash 串成同一条 workflow chain。
- ReceiptStore 卸载的大块上下文必须在最终请求审计中留下 `receipt_refs`。`receipt_refs` 只作为证据定位与复盘入口，不能作为路径、命令或写入授权来源。
- 该对象当前是审计信号；提升为阻断门禁时必须先完成所有 Director/CE/PM 入口的兼容迁移。

## 14. Run Provenance Bundle

机器可读 schema: `docs/governance/schemas/run-provenance-bundle.schema.yaml`。

每次 run 结束后应生成:

```yaml
schema_version: polaris.run_provenance_bundle.v1
bundle_id: ...
run_id: ...
task_id: ...
commit: ...
pm_contract_hash: ...
ce_blueprint_hash: ...
handoff_decision_hash: ...
execution_envelope_hash: ...
final_provider_request_hashes: []
tool_receipt_hashes: []
file_diff_hash: ...
command_receipt_hashes: []
qa_result_hash: ...
verifier_logs_ref: ...
final_status: ...
created_at: ...
```

用途:

- 事故复盘。
- QA 和 AGI 读取。
- 跨版本 regression 对比。
- 证明“代码 diff 是按哪个合同、蓝图、信封、请求和工具执行产生的”。

## 15. Developer Audit Checklist

开发人员审计任一 Director 执行入口时必须确认:

- 是否调用共享 handoff decision 服务。
- 是否拒绝 PM -> Director 直连。
- 是否基于 validated PM contract，而不是 PM LLM raw output。
- 是否使用 immutable blueprint snapshot 和 hash。
- 是否创建 execution envelope。
- tool/write/command guard 是否验证 envelope/capability。
- final provider request audit 是否能逐字段比对实际 invoker payload。
- CE overlay 是否无法覆盖 PM authoritative fields。
- QA failed 是否不会生成 success ledger。
- ContextOS coverage 是否报告 required evidence 缺失。
- 每个关键对象是否包含 `schema_version`、hash、policy_version。

## 15. Definition of Done

本架构完成的最低标准:

1. 所有 Director dispatch 入口调用同一个 handoff decision service。
2. 所有 Director execution 绑定 immutable execution envelope。
3. 所有 tool/write/command guard 验证 envelope/capability，而不是只验证 role。
4. CE overlay 不能覆盖 PM authoritative fields。
5. Provider request audit 与实际 invoker payload 可逐字段比较。
6. PM route probe 的最终 provider request 不包含任何 tool schema。
7. QA failed 不会生成 success ledger。
8. 所有关键对象都有 `schema_version`、hash、policy_version。
9. 所有 bypass 类问题都有 negative test。
10. 每次 run 都能生成 provenance bundle。

## 16. 分阶段落地路线

### Phase 1: Contract Lock

- 新增并维护 `execution-envelope.schema.yaml`。
- 新增并维护 `ce-handoff-decision.schema.yaml`。
- 新增并维护 `run-provenance-bundle.schema.yaml`。
- 文档明确 `handoff_ready` 不是 authority。

### Phase 2: Service Convergence

- 将当前 `evaluate_handoff_decision_for_blueprint` 收敛为所有入口共享的 handoff decision 服务。
- 禁止 route、CLI、consumer、role-session export 各自复制本地判断。
- 为 stale blueprint、missing hash、CE overlay path escalation 添加 negative tests。

### Phase 3: Envelope Enforcement

- Director dispatch 前创建 immutable execution envelope。
- Tool gateway / write guard / command guard 消费 envelope-derived capability。
- effect receipt 写入 `execution_envelope_hash`、capability evidence、before/after hash。

### Phase 4: Evidence Coverage

- final provider request audit 增加 required refs coverage。
- Director/CE/PM 上下文健康从 token utilization 升级为 evidence coverage。
- AGI 可读取 coverage 并请求证据补齐，但不能覆盖 gate。

### Phase 5: Provenance and AGI

- 每次 run 输出 provenance bundle。
- Resident AGI 使用 provenance、Run Ledger、audit verdict 做无人值守监督。
- AGI suggested rules 进入 advisory overlay，不直接执行。
