# Polaris 架构文档

本文档面向工程师与核心开发者，详细说明 Polaris 的内部工作流、状态机、事件模型与并发约束。

完整的 PM->Director 端到端生命周期说明见：`docs/agent/pm-director-flow.md`。

> ACGA 3 方向补充：若任务涉及“无人值守软件工厂控制面”“LLM 只管 proposal、不直接维护 truth”“同一个治理内核支持内嵌与守护进程两种模式”，请同步参考 `../../ACGA3_FACTORY_POSITIONING.md`、`../architecture/ACGA_3_AUTONOMOUS_FACTORY_SPEC.md` 与 `../../src/backend/docs/ACGA_3.0_RFC.md`。这些文档描述的是目标补充，不替代本文当前实现说明。

---

## 0. 设计根源（Design Roots）

Polaris 的**初衷**是**无人值守的自动化写代码工具**。架构从这一根源出发，遵循以下原则：

- **宁修不滚（Fix-Forward）**：回滚浪费已消耗的时间和 token（成本），且“写完了再撤销”等于白写。系统默认**禁止自动回滚**，宁愿保留当前写入、通过后续任务或缺陷迭代去修，也不采用“先写再回滚”的常规纠错路径。详见 `docs/agent/invariants.md` 不变量 #10。
- **写前阻断优先**：通过 preflight、scope gate、capability gate 在落盘前拦截非法或高风险写入，减少“写后才发现不该写”的情况。
- **事实流与可回放**：所有关键决策与事件 append-only 记录，支持事后复盘与 3 Hops 定位，与“不自动回滚”一致——修正通过新事件与后续任务体现。
- **拟人化协商内核**：通过角色化对话（PM/工部尚书/Director/QA）把故障转为协商与修复动作，避免单线路僵死；目标是降低人工介入频次，而不是增加“表演式对话”。

新功能与策略变更不得违背上述根源；若需引入自动回滚类能力，必须显式 opt-in 且文档标明与设计根源的例外关系。

---

## 1. 核心状态机 (Loop State Machines)

Polaris 分为两个独立的循环：**PM Loop** (规划与决策) 和 **Director Loop** (执行与验证)。

### 1.1 PM Loop (规划循环)

PM Loop 负责生成高阶任务并决策是否继续。

**流程：**

1.  **读取上下文**：加载 requirements / plan / gap report / QA history / memory snapshot。
2.  **生成任务 (PM_TASKS)**：产出结构化任务包 `PM_TASKS.json`，这是 PM 与 Director 的唯一"合同"。
3.  **Handoff**：写入对话事件 (handoff)，标志着控制权移交。
4.  **拉起 Director** (可选)：通过子进程调用 Director 执行任务。
5.  **评审与决策**：
    - 读取 Director 返回的 `DIRECTOR_RESULT.json`。
    - 进行追问或评价。
    - 决策下一步：继续 (CONTINUE) / 结束 (FINISH) / 失败 (FAIL)。
6.  **归档**：生成 Memo 并更新索引。

**关键文件：**

- `PM_TASKS.json`: 任务合同 (输入)
- `PM_STATE.json`: 内部状态 (连败计数、阻碍计数等)

### 1.2 Director Loop (执行循环)

Director Loop 负责将 PM 的任务转化为具体代码变更，并保证质量。

**流程：**

1.  **初始化**：读取 `PM_TASKS.json`。
2.  **Tool Planner (取证)**：
    - 分析任务，决定需要读取哪些文件或搜索哪些符号。
    - 调用 `repo_*` 工具获取代码切片。
3.  **Patch Planner (计划)**：
    - 基于任务和证据，生成具体的执行计划 (tool_commands)。
4.  **Execution (执行)**：
    - 执行文件修改、命令运行。
    - 实时流式输出日志。
5.  **Reviewer (评审 - 可选)**：
    - 对改动进行自我评审，必要时回修。
6.  **QA (复核)**：
    - 运行测试 (pytest/npm test) 或验证脚本。
    - 生成 `QA_RESPONSE.md`。
7.  **结果汇总**：生成 `DIRECTOR_RESULT.json`，包含执行状态、消耗、风险评分等。

**多角色模拟：**
Director 在单次提示词中模拟多角色视角 (Creative Director, Designer, Engineer)，而非多进程协作。角色配置见 `prompts/*.json`。

---

## 2. 事件流与数据模型 (Event Streams)

Polaris 采用"事实源 + 投影层"的设计模式。

### 2.0 统一设计原则：数据即真相，视图即表现

- **数据即真相**：系统状态以事实源与规范化配置为准（如 `events.jsonl`、`DIRECTOR_RESULT.json`、LLM config）。
- **视图即表现**：UI、对话叙事、报表与各种看板均属于投影层，不维护第二份业务真相。
- **工程收益**：单一数据源减少同步冲突；新增视图仅需新增投影/适配器，不改核心数据模型。

### 2.1 事实源 (Truth Sources)

- **`events.jsonl` (Action/Observation Stream)**
  - 记录机器可读的原子操作：工具调用、文件读写、QA 结果。
  - 用途：回放 (Replay)、评测 (Eval)、轨迹分析 (Trajectory)。
  - **Schema**:
    - `kind`: `action` | `observation`
    - `actor`: `Tooling` | `Director` | `QA` | `Reviewer` | `System`
    - `name`: 工具名 (e.g., `repo_rg`, `apply_patch`)
    - `refs`: `{ "run_id": "...", "task_id": "...", "phase": "..." }`
    - `observation`:
      - `ok`: boolean
      - `output`: 结果数据
      - `duration_ms`: 执行耗时 (ms)
      - `truncated`: 是否截断
      - `artifacts`: 产生的副产物 (如报告文件路径)

- **`DIRECTOR_RESULT.json`**
  - 单次运行的最终结果摘要。
  - **关键字段**:
    - `schema_version`: 版本号 (int)
    - `status`: `success` | `fail` | `blocked`
    - `failure_code`: 失败分类码 (e.g., `QA_FAIL`, `RISK_BLOCKED`, `TOOL_TIMEOUT`)
    - `patch_risk`:
      - `score`: 总分
      - `factors`: 风险因子 (files_changed_count, lines_added, touches_auth, etc.)

### 2.2 解释层 (Interpretation Layer)

- **`DIALOGUE.jsonl` (Narrative Stream)**
  - 记录拟人化的对话过程：PM 分配任务、Director 汇报结果、QA 报告。
  - 用途：Dashboard 展示、人类阅读。
  - **Fields**: `timestamp`, `speaker`, `type` (handoff/receipt/say/coordination/council/done/thought), `content`.

- **`RUNLOG.md`**
  - 详细的文本日志流。

### 2.3 轨迹 (Trajectory)

- **`trajectory.json`**
  - 将 Events、Artifacts 和 Context 串联起来的索引文件。
  - 指向 events.jsonl 中的 seq 范围。

### 2.4 内心独白流 (Inner Voice Stream)

> **设计哲学**：只从模型已输出的内容中抽取思考摘要，不追求完整推理链。可选信号：有就展示，没有就不显示。

#### 2.4.1 数据模型

内心独白作为 `DIALOGUE.jsonl` 的扩展消息类型 (`type: "thought"`)：

```json
{
  "timestamp": "2026-02-02T16:32:10+08:00",
  "speaker": "director",
  "type": "thought",
  "visibility": "collapsed_by_default",
  "refs": {
    "run_id": "pm-00001",
    "task_id": "t-001",
    "phase": "plan",
    "mode": "planner"
  },
  "content": "Need evidence: read src/types/file.ts and src/types/files.ts; verify imports before patch.",
  "meta": {
    "kind": "inner_voice",
    "confidence": "high",
    "source": "think_block"
  }
}
```

**字段说明：**

| 字段              | 类型                                                      | 说明               |
| ----------------- | --------------------------------------------------------- | ------------------ |
| `type`            | `"thought"`                                               | 标识为内心独白消息 |
| `visibility`      | `"collapsed_by_default"` \| `"visible"`                   | UI 默认折叠状态    |
| `meta.kind`       | `"inner_voice"` \| `"reflection"` \| `"flashback"`        | 独白子类型         |
| `meta.confidence` | `"high"` \| `"medium"` \| `"low"`                         | 抽取置信度         |
| `meta.source`     | `"reasoning_summary"` \| `"think_block"` \| `"heuristic"` | 抽取来源           |

#### 2.4.2 规范化类型 (NormalizedThinking)

所有来源的思考内容会被规范化为统一结构：

```typescript
type NormalizedThinking = {
  kind: "reasoning_summary" | "think_block" | "heuristic";
  content: string;
  confidence: "high" | "medium" | "low";
};
```

#### 2.4.3 抽取规则优先级

从最确定到最不确定：

| 优先级 | 来源           | 置信度 | 说明                                             |
| ------ | -------------- | ------ | ------------------------------------------------ |
| 1      | **结构化字段** | high   | 响应中有 `reasoning_summary` / `thinking` 字段   |
| 2      | **标签块抽取** | high   | `<think>...</think>`, `<analysis>...</analysis>` |
| 3      | **启发式匹配** | low    | 识别 `Thoughts:`, `Reasoning:`, `Plan:` 段落     |

> ⚠️ 启发式匹配必须保守，避免把正文误判成 thinking。

---

## 3. 上下文引擎 (Context Engine v2)

> **定位**：把“上下文选择/压缩/渲染”从“拼字符串”升级为**结构化、可审计、可回放**的 ContextPack。
> 这层逻辑是 Host 端的稳定模块，负责把人类意图与证据组织成可控提示词。
>
> 详细升级计划见：[`context_engine_v2_plan.md`](context_engine_v2_plan.md)。

### 3.1 核心对象：ContextRequest / ContextPack

**ContextRequest（一次调用的需求单）**

- `run_id`, `step`, `role`, `mode`, `task_id`
- `query`：本轮意图（plan_text / failure signature / target）
- `budget`：token/char 预算 + cost_class（LOCAL/FIXED/METERED）
- `sources_enabled`：允许的 provider 列表
- `policy`：角色策略与必选证据（如 Director 必须包含 evidence）

**ContextPack（最终上下文包）**

- `items[]`：每项包含 `kind` / `content_or_pointer` / `refs` / `size_est` / `priority` / `reason`
- `compression_log[]`：裁剪/降级/摘要/丢弃的过程记录
- `rendered_prompt` / `messages[]`：不同 LLM provider 的渲染结果
- `hash`：用于缓存、重放、对比

> **不变量约束**：Memory/Reflection 必须带 refs；缺 refs 的条目只能作为 note 展示，不进入关键决策上下文。

### 3.2 Context Providers（可组合的上下文来源）

最小可落地拆分（先内置类，不做复杂插件系统）：

- **DocsProvider**：requirements / constraints / quality
- **ContractProvider**：PM_TASKS / PLAN
- **RepoEvidenceProvider**：显式 repo 证据切片（file + line range）
- **EventsProvider**：events.jsonl 尾部事件片段（failure signature / 关键 event）
- **MemoryProvider**：必须带 refs 的 memory/reflection
- **MemosProvider**：run memo / gap report

Context Engine 只负责：**拉候选 → 排序/裁剪/压缩 → 产包**。

### 3.3 预算梯度（Budget Ladder）

当超预算时，按固定顺序缩小（可解释、可回放）：

1. **去重**：同一文件多段引用保留最高优先级
2. **裁剪**：slice 半径从 ±200 行降到 ±60
3. **降级为指针**：file + line range + hash + 一句话用途
4. **摘要替换**：仅对大块低频内容（摘要必须缓存）
5. **丢弃低优先级项**：最后才丢

每一步必须写入 `compression_log`，并写 `context.build` 事件，保证可回放与可解释。

### 3.4 把“长输出”当作证据文件

对话历史/长日志/工具输出落成 artifacts（文件），ContextPack 仅引用：

- 路径 + hash
- 短摘要
- 必要时只取 tail（错误常在末尾）

推荐路径：`.polaris/runtime/runs/<run_id>/evidence/*.txt`

### 3.5 缓存与重放

无状态调用不等于“每次从零”：

- **Repo 索引缓存**（symbols/import graph）
- **摘要缓存**（按 source hash）
- **检索缓存**（query + repo_hash + topk）
- **ContextPack 缓存**（同一 run/step 重试直接复用）

### 3.6 观测与事件

每次调用必须记录：

- `context.build`：items、hash、budget、compression_log
- `context.snapshot`：ContextPack 快照（artifact 路径 + hash）
- `llm.invoke`：provider/model/latency/usage

> 这些事件写入 `events.jsonl`，形成可回放与可评测的证据链。

### 3.6.1 Context Snapshot（回放快照）

为避免“压缩导致回放漂移”，关键 LLM 调用前将 ContextPack 快照写入 artifacts：

- 路径：`.polaris/runtime/runs/<run_id>/evidence/context_snapshot_<hash>.json`
- 事件：`context.snapshot`（包含 `request_hash` / `snapshot_path` / `snapshot_hash`）
- 回放原则：**优先读取快照而非重新计算**

### 3.7 角色策略（偏执、可控）

- **PM**：docs + memo/reflection（少量）+ 上轮摘要；不喂大段代码
- **Director / Planner**：合同 + 必要证据（files_to_edit/rg 命中/symbol）
- **Director / Executor**：当前切片 + 最近错误输出（更小）
- **QA**：AC + 失败签名附近 N 行 + diff 摘要
- **Docs**：变更清单 + 模板 + refs（禁止编造）

### 3.8 最短落地路线

1. **ContextPack 结构化落盘 + 写 events**（不做摘要）
2. **预算梯度**（裁剪/指针）
3. **长输出落 artifacts**（文件化证据）
4. **摘要缓存**（超预算才触发）
5. **强化 RAG**（symbols + LanceDB）

### 3.9 Invariant Sentinel（自动合规检查）

在每次 Loop 结束后运行 Invariant Sentinel，自动检查并记录：

- **合同不可变**：goal/AC 是否被修改
- **事件流 Append-Only**：events.jsonl 是否被截断或回滚
- **Memory refs**：新记忆是否缺少证据引用

检测结果写入事件流：

- `invariant.check`：整体 PASS/FAIL
- `invariant.violation`：逐项违规明细

---

## 4. Policy 策略与配置合并

Director 的行为由 Policy 严格控制，支持多层级合并。

**合并优先级 (高 -> 低):**

1.  **CLI 参数**: `--risk-block-threshold 0`
2.  **Task Overrides**: `PM_TASKS.json` 中的 `policy_overrides` 字段
3.  **Policy 文件**: `.polaris/runtime/director_policy.json`
4.  **环境变量**: `KERNELONE_...`
5.  **代码默认值**

**Policy 示例:**

```json
{
  "repair": { "max_attempts": 2 },
  "risk": { "block_threshold": 6 },
  "memory": {
    "enabled": true,
    "backend": "lancedb",
    "store_every": 1
  },
  "inner_voice": {
    "enabled": true,
    "max_length": 2048,
  }
}
```

**生效记录:**
`DIRECTOR_RESULT.json` 中包含 `policy_effective` (最终生效配置) 和 `policy_sources` (来源追踪)。

---

## 5. 并发与原子性约束

### 5.1 写入约束

- **Workspace 隔离**: 推荐将 `.polaris/` 目录指向 RAMDISK，避免污染项目源码。
- **原子写入**: 关键状态文件 (如 `PM_TASKS.json`, `last_state.json`) 应尽量采用原子替换 (写临时文件 -> rename) 方式，防止读取到截断内容。
- **Dashboard 只读**: Dashboard 仅读取 `.polaris/` 下的文件进行展示，**绝不** 直接修改任务或代码，确保"展示面"与"控制面"分离。

## 6. Smart 视图解析架构 (Dashboard)

Dashboard 采用 **流式解析器 (Streaming Parser)** 将非结构化日志转化为结构化事件树。

**解析原理:**

- **哨兵行 (Sentinels)**: 利用 `OpenAI Codex v...`, `user`/`exec`, `mcp:`, `--------` 等固定特征行作为状态切换锚点。
- **状态机**: 维护 `mode` (idle/user/thinking/exec) 和 `lifecycle` (open/closed)。
- **增量渲染**:
  - **Open**: 遇到块开始 (如 `{`), 创建 `status: open` 卡片，UI 显示 loading。
  - **Update**: 后续行追加到 buffer，实时更新 UI (如表格行增加)。
  - **Close**: 遇到块结束或新哨兵，标记 `status: closed`。

**处理细节:**

- **JSON**: 括号计数 (brace counting)，归零后 Parse。
- **表格**: 识别 PowerShell `Get-ChildItem` 表头，按列宽切分。
- **ANSI**: 默认 Strip ANSI 以保证结构化展示清晰度。
- **Thinking 块**：识别 `<think>...</think>` 标签，抽取为 `thought` 类型消息。

---

## 7. 设计哲学

### 7.1 核心原则

- **Reality-Driven**: 基于真实成本和资源设计，而非理想模型。
- **Tool-Augmented**: 用工具链 (Lint/Test/RAG) 补足弱模型 (Local LLM) 的短板。
- **Memory-over-IO**: 优先使用内存缓存 (Repo Index)，减少磁盘 IO。
- **Control/Execute Separation**: PM 定义合约，Director 执行合约，Dashboard 旁路观测。

### 7.2 Inner Voice 设计哲学

内心独白功能遵循"面向现实"的原则：

| 原则                 | 做法                                                | 不做                          |
| -------------------- | --------------------------------------------------- | ----------------------------- |
| **只抽取已输出内容** | 从模型响应中提取 `<think>` 块或 `reasoning_summary` | 追求完整逐步推理链            |
| **可选信号**         | 有 thinking 就展示，没有就不显示                    | 强制要求所有模型输出 thinking |
| **长度可控**         | 控制思考块最大长度，避免刷屏                     | 不加限制导致 UI 混乱          |
| **旁路投影**         | 作为 UI 增强，不影响系统事实流                      | 让 thinking 参与决策逻辑      |

---

## 8. 消息类型速查表

| Speaker    | Type          | 说明                                          | 可见性   |
| ---------- | ------------- | --------------------------------------------- | -------- |
| `pm`       | `handoff`     | PM 移交任务给 Director                        | 正常     |
| `director` | `receipt`     | Director 确认收到任务                         | 正常     |
| `director` | `say`         | Director 对外汇报（可读、可交付）             | 正常     |
| `director` | `thought`     | **内心独白**：低亮度/半透明/可折叠            | 默认折叠 |
| `qa`       | `say`         | QA 裁决与证据引用（更冷静）                   | 正常     |
| `system`   | `mode_change` | 状态机变化（mode enter/exit、budget、policy） | 正常     |
| `pm`       | `done`        | PM 结束本轮                                   | 正常     |

---

## New Subsystems (v3.2)

### Independent Auditor (门下省独立化)

The QA/Auditor is now a truly independent module (`auditor.py`) separate from
the Director's execution context. Key changes:

- `AuditVerdict` structured result with acceptance, findings, defect ticket
- Events logged with `actor="Auditor"` (not `actor="QA"`) for clear provenance
- Uses the `qa` LLM role which can be configured to use a **different model/provider**
  than the Director, ensuring genuine separation of powers

### Director Capability Gate (工部权限矩阵)

Role-based access control for Director actions:

- `director_skills.py` — Role/Capability enums, SkillRegistry, CapabilityChecker
- `director_capability_gate.py` — Integration layer with advisory/strict modes
- Validates write, delete, tool usage before execution
- Advisory mode (default): logs warnings without blocking
- Strict mode: blocks unauthorized actions

### MCP Policy Server (大理寺 MCP)

Model Context Protocol server exposing governance tools:

- **Location**: `src/backend/tools/policy_mcp_server.py`
- **Protocol**: JSON-RPC 2.0 over stdio
- **Tools**:
  - `policy_check` — evaluate policy gates
  - `finops_check` — evaluate budget gates
  - `invariant_check` — run invariant sentinel
  - `get_policy_config` — read policy configuration

### Code Search Engine (经籍搜索)

LanceDB-backed code indexing and search:

- **Location**: `src/backend/core/director_runtime/storage/code_search.py`
- `index_workspace()` — chunk and index all code files
- `search_code()` — text search over indexed chunks
- `refresh_index()` — incremental re-index for changed files
- Supports 20+ file extensions, configurable chunk size with overlap

### Architect Role (中书令)

Automated spec generation from task requirements:

- Generates `docs/product/spec_<task_id>.md` with structured sections
- Extracts goal, constraints, scope, acceptance criteria, dependencies
- Integrated into PM Loop's assignee routing

### ChiefEngineer Role (工部尚书)

Deterministic construction-blueprint service between PM and Director:

- **Location**: `src/backend/core/polaris_loop/chief_engineer.py`
- Maintains project blueprint data structures (`ProjectBlueprint`, `TaskBlueprint`, `BlueprintFile`)
- Produces Director-ready construction plans at module/file/method granularity
- Emits scope union (`scope_for_apply`) to reduce missing-file / unresolved-import loops
- Mode: `off | auto | on` via `--chief-engineer-mode`
- Runtime artifacts:
  - `runtime/contracts/chief_engineer.blueprint.json`
  - `runtime/state/chief_engineer.state.json`
- PM orchestration injects these into Director task contract fields:
  - `task.chief_engineer`
  - `task.construction_plan`
  - expanded `task.scope_paths` and constraint hints

### Assignee Auto-Routing (派官簿自动路由)

Keyword-based task routing when `assigned_to` is empty or "auto":

- Architecture/design → Architect
- Blueprint/dependency/scope planning → ChiefEngineer
- Policy/compliance → PolicyGate
- Budget/cost → FinOps
- Review/audit → Auditor
- Default → Director

### Vision Service (视觉服务)

Multi-backend image analysis:

- **PIL backend** (always available): dimensions, format, mode
- **Florence-2 backend** (optional GPU): object detection, captioning, OCR
- Graceful degradation when GPU is unavailable
- API: `analyze_image()`, `extract_text()`, `describe_image()`, `detect_objects()`
