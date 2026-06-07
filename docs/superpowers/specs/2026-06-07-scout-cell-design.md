# Scout（探子）Cell v1 设计

- **日期**: 2026-06-07
- **状态**: 已批准（设计阶段），待实现计划
- **职责范围（v1）**: 代码/符号侦察（read-only code/symbol reconnaissance）
- **作者**: 设计经 codegraph 全程对照真实代码

---

## 1. 背景与现状核查（grounding）

引入"探子"前，用 codegraph 核查了 Polaris 现状，结论是**八成底座已存在，本期是"接线"而非"新建"**。

### 1.1 已存在但未接线
- `scout` 角色已在三层定义，但**零调用方**：
  - `SCOUT_TEMPLATE` —— `src/backend/polaris/kernelone/roles/templates/preset_templates.py:174`
  - `ScoutToolIntegration` + `SCOUT_TOOL_PROMPT`，已注册进 `ROLE_TOOL_INTEGRATIONS` / `_SUPPORTED_ROLES` —— `src/backend/polaris/cells/llm/tool_runtime/internal/role_integrations.py:836`
  - scout builtin profile（`allow_code_write=False`、`max_tool_calls=50`、最高 context tokens；测试坐实）—— `src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py`
  - `codegraph_callers(ScoutToolIntegration)` = 无调用方；其 `process_llm_response` 已是 `_disabled_text_tool_protocol_result`（死路径）；scout 不在 5 角色 `ROLE_PROMPT_TEMPLATES`、也不在 `CORE_ROLES` —— **代码已天然把它当辅助/非核心角色**，与"纯辅助"定性一致。
- `SubagentSpawner`（`src/backend/polaris/kernelone/single_agent/subagent_runtime.py`）也零调用，是 SWE-bench single-agent 那套，**绕开角色治理栈**，本期不采用其 runtime。

### 1.2 可直接复用的真实底座
- **工具读/写/执行分类**：`ToolSpecRegistry.is_read_tool / is_write_tool / is_exec_tool` —— `src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py`。这是"只读靠构造"的现成门禁。
- **目标工作区的真实只读工具**（`infrastructure/tools/`）：
  - `repo_rg`（grep，返回结构化 `file:line:col`）、`repo_tree`、`repo_symbols_index`（真实**符号索引**）—— `infrastructure/tools/search.py`
  - `repo_glob`、`repo_read_slice/head/tail/around`、`file_exists` —— `infrastructure/tools/files.py`
- **分阶段检索策略**：`ExplorationPhase` + `_build_phase_tools` —— `src/backend/polaris/kernelone/context/exploration_policy.py`
- **证据/防篡改**：`EvidenceCollector` / `EvidencePackage.compute_hash()` —— `src/backend/polaris/domain/verification/evidence_collector.py`
- **受治理角色执行入口**：`RoleRuntimeService.execute_role_session(ExecuteRoleSessionCommandV1) -> RoleSessionResultV1` —— `src/backend/polaris/cells/roles/runtime/public/service.py`（**不开 TaskMarket 事务**，与 single-broker 相容）
- **只读 fs 门禁**：`SandboxPolicy.evaluate_fs_scope` —— `src/backend/polaris/cells/roles/kernel/internal/policy/sandbox_policy.py`

### 1.3 需要修正的现状认知
- `SCOUT_TEMPLATE` 工具名 `codebase_search / codebase_map / dependency_query` **代码中查无此物**，是虚构名。v1 用真实工具（`repo_symbols_index` / `repo_rg` / ...），并在 P2 修正模板/profile 的工具名。
- **codegraph 是开发期 MCP 工具（索引 Polaris 仓库本身），不是 Polaris 给目标工作区的运行时能力**。Scout 运行在用户目标项目上，只能用 `infrastructure/tools/` 这套 + `repo_symbols_index`，**不得**把 codegraph 当运行时依赖。

---

## 2. 目标与非目标

### 2.1 目标
- 提供一个**辅助型只读角色** Scout，被主角色（首发 Director，其次 PM）在**自身 Turn 内同步拉起、用完即走**。
- 输入模糊目标 → 输出精炼的"现状边界 / X 在哪"证据包，**为主角色挡掉环境噪声**、节省其 token 与 phase budget。
- 最大化复用现状底座，最小 blast radius。

### 2.2 非目标（v1 明确不做）
- 不进 TaskMarket、不领单、不开独立事务。
- 不写盘、无副作用（read-only / side-effect free）。
- 不做持久化（长效沉淀以后接 Akashic）。
- 不碰主角色私有控制面（只收只读 Target Descriptor，只回结构化证据）。
- 不做：瞬态向量库、多协议探针、起飞前干跑沙箱、自适应限流、错误指纹库、日志榨汁、干跑职责（均推迟）。

---

## 3. 落地路线（已选 C：确定性 + 可选升级）

确定性优先的探测，作为新辅助 cell；仅当目标过于模糊、确定性检索不足时，升级到受治理的 scout 角色会话兜底。相对其他两条路线（A 完整角色会话 / B 隔离子代理），C 最轻、最快、最可预测、blast radius 最小，且"代码/符号侦察"本质就是"在已有符号工具上检索+蒸馏"。

```
[Director / PM 的 LLM]
      │  scout_probe(target)                         ← 作为只读工具被调用
      ▼
ScoutProbeService.probe(ScoutProbeTargetV1)           cells/roles/scout/public/service.py
      │
      ├─ 1. Turn 内 TTL 缓存命中？→ 直接返回 ScoutReportV1
      ├─ 2. 解析目标 → exploration_policy 出检索计划
      ├─ 3. 只读检索：repo_symbols_index / repo_rg / repo_tree / repo_glob / repo_read_*
      │      （仅 ToolSpecRegistry.is_read_tool 的工具）
      ├─ 4. 候选排序 → 1 次便宜模型蒸馏出 summary
      ├─ 5.（可选）目标太模糊 → 升级到 execute_role_session(role="scout")
      └─ 6. 组装 ScoutReportV1（含 EvidencePackage.compute_hash 作 verify-pack）→ 回写缓存
      ▼
[主角色] 自行决定是否把 summary 并入自身 Context（是否纳入由调用方把关）
```

---

## 4. Cell 骨架（镜像 `cells/roles/runtime`）

新建 `src/backend/polaris/cells/roles/scout/`：

```
cells/roles/scout/
├── cell.yaml                 # ACGA manifest（声明只读资产、依赖、能力）
├── README.agent.md
├── context.pack.json
├── public/
│   ├── __init__.py
│   ├── contracts.py          # ScoutProbeTargetV1 / ScoutFinding / ScoutReportV1
│   ├── service.py            # ScoutProbeService（contract-first facade）
│   └── tests/                # 公共契约与服务测试（与代码同址）
└── internal/
    ├── target.py             # 解析/校验 Target Descriptor
    ├── planner.py            # 复用 exploration_policy 产出检索计划
    ├── retrieval.py          # 只读工具调用（仅 is_read_tool）
    ├── ranker.py             # 候选打分排序
    ├── distiller.py          # 便宜模型蒸馏（榨汁）
    ├── cache.py              # Turn 内 TTL 缓存
    ├── escalation.py         # 升级到 scout 角色会话的桥
    ├── evidence.py           # 发 EvidencePackage（compute_hash）
    └── tests/
```

每个 internal 模块单一职责、可独立测试；公共面只暴露 `ScoutProbeService` 与契约。

---

## 5. 契约（V1，`public/contracts.py`）

### `ScoutProbeTargetV1`（只读输入 / Target Descriptor）
| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | `str` | 模糊自然语言目标（如"支付网关错误处理在哪"） |
| `hints` | `dict` | 可选：`{paths?: list[str], symbols?: list[str], globs?: list[str]}` |
| `mode` | `str` | `"locate"`（X 在哪）\| `"boundary"`（现状边界） |
| `max_findings` | `int` | 返回发现上限 |
| `token_budget` | `int` | summary 的 token 上限 |
| `caller_role` | `str` | 仅审计联系（director/pm），**不建事务** |
| `run_id` / `task_id` | `str` | 仅审计联系 |
| `allow_escalation` | `bool` | 默认 `False`；允许时才可升级到角色会话 |

### `ScoutFinding`
`path: str`, `symbol: str | None`, `line: int | None`, `snippet: str`, `why_relevant: str`, `confidence: float`

### `ScoutReportV1`（结构化输出）
| 字段 | 类型 | 说明 |
|---|---|---|
| `findings` | `list[ScoutFinding]` | 命中项 |
| `summary` | `str` | 蒸馏后的 Scout Context Pack（受 `token_budget` 约束） |
| `coverage` | `dict` | `{searched_paths, tools_used, truncated: bool}` —— **暴露盲区，不静默截断** |
| `confidence` | `float` | 整体置信度 |
| `content_hash` | `str` | 套 `EvidencePackage.compute_hash`，作 verify-pack |
| `usage` | `dict` | `{model, tokens, duration_ms, context_saved}` |
| `cache_hit` | `bool` | 是否命中 TTL 缓存 |
| `escalated` | `bool` | 是否走了角色会话升级 |

公共接口禁用 `Any`；所有文本读写显式 UTF-8。

---

## 6. 探测流程（`ScoutProbeService.probe`）

1. **缓存**：`cache.py` 按 `hash(target)` 查 Turn 内 TTL 缓存，命中即返回（`cache_hit=True`）。
2. **解析**：`target.py` 校验并归一化 Target Descriptor。
3. **计划**：`planner.py` 复用 `exploration_policy`（`ExplorationPhase`）产出分阶段只读检索计划。
4. **检索**：`retrieval.py` 只调 `is_read_tool` 工具：`repo_symbols_index`（符号）、`repo_rg`（文本/正则）、`repo_tree`/`repo_glob`（结构）、`repo_read_*`（片段）。
5. **排序**：`ranker.py` 对候选打分（符号匹配 > 路径/名称匹配 > 文本匹配；带 hints 加权）。
6. **蒸馏**：`distiller.py` 用便宜模型做 1 次榨汁，产出受 `token_budget` 约束的 `summary`；确定性足够时可跳过 LLM（零成本）。
7. **升级（可选）**：`allow_escalation=True` 且确定性结果不足（覆盖率/置信度低于阈值）时，`escalation.py` 调 `execute_role_session(role="scout")` 兜底。
8. **证据 + 缓存**：`evidence.py` 生成 `EvidencePackage` 并算 `content_hash`；组装 `ScoutReportV1`，回写缓存。

---

## 7. 只读门禁（fail-closed）

- **构造层**：`retrieval.py` 只从 `ToolSpecRegistry` 取 `is_read_tool == True` 的工具；写/执行工具**根本不接**。
- **升级层**：`execute_role_session(role="scout")` 时，scout profile `allow_code_write=False` + `SandboxPolicy.evaluate_fs_scope` 强制只读；任何写/执行调用 → `BLOCKED` 并记入 evidence。这即"Fail-Closed 探测网关"，由现成 `SandboxPolicy` 实现，不另造。
- 失败闭合：检索/蒸馏失败时返回低置信度报告并标 `coverage.truncated`，绝不臆造发现。

---

## 8. 调用点

- **(a) 只读工具 `scout_probe`**：在 `ToolSpecRegistry.register` 注册（标记 read），加入 **Director、PM 的 `tool_policy.whitelist`**。主角色 LLM 不确定时自调；工具返回 = `summary` + top findings（紧凑），完整 report 按 id 取。
- **(b) 服务方法 `ScoutProbeService.probe()`**：供编排层"规划/改前"确定性预跑（PM 规划前摸边界、Director 改前定位）。
- **Turn 内 TTL 缓存**：`dict` 按 `hash(target)` 去重，TTL ≈ 调用方一个 turn，防同 turn 重复探测烧 token（不引 Redis）。
- 调用并入主角色 Turn 的子步骤，token/耗时记进主 Turn receipt，**不开独立事务**。

---

## 9. 模型与成本
- 蒸馏用**便宜模型**，经 `ProviderManager`（禁止绕过；模型选择走角色 provider/model 解析）。确定性检索能解决时**零 LLM**。
- `usage.context_saved`：借 `_estimate_pollution` 思路，报告"为主角色挡掉多少噪声 token"。

---

## 10. 验证与治理（fail-closed）
- 测试与代码同址：`cells/roles/scout/public/tests/` + 各 internal 单测，pytest 全绿。
- 过 CLAUDE.md 三道闸：`ruff check . --fix`、`ruff format .`、`mypy`（公共接口禁 `Any`）、`pytest -v`。
- 治理约束：
  - 新工具 `scout_probe` 需过 `run_tool_catalog_consistency_gate`（`src/backend/docs/governance/ci/scripts/`）。
  - **不触碰** TaskMarket single-broker（`check_task_market_single_broker`）。
  - 更新 `src/backend/docs/graph/catalog/cells.yaml` 及相关 subgraph，登记新 cell，避免再产生文档-代码偏差。
  - 如需改 `AGENTS.md/CLAUDE.md/GEMINI.md`，须三文件一致（`agent_instruction_snapshot_consistent`）；v1 尽量不改这三者。

---

## 11. 增量交付
- **P1**：契约 + `ScoutProbeService.probe()` 确定性检索（`repo_symbols_index`/`repo_rg`/...）+ 蒸馏 + 测试 + cell manifest。
- **P2**：`scout_probe` 工具注册 + Director/PM 白名单 + TTL 缓存；修正 `SCOUT_TEMPLATE`/scout profile 的虚构工具名为真实工具。
- **P3**：模糊目标升级到受治理 scout 角色会话（`escalation.py`；确保 scout profile 可被 `RoleRuntimeService` 解析、有合法 system prompt）。
- **P4（以后）**：把高价值发现沉淀进 Akashic 知识管道。

---

## 12. 已定决策
- Cell 位置：`src/backend/polaris/cells/roles/scout/`（与其他 role cell 同级，符合"辅助型角色"定性）。
- `scout_probe` 首发同时给 **Director + PM**（PM 规划前、Director 改前都受益）。
- 升级路径保持在 **P3**（P1 先把确定性主干跑通并验证）。
- 不采用 `SubagentSpawner` runtime；仅借鉴其 `context_saved` 污染评分思路。
