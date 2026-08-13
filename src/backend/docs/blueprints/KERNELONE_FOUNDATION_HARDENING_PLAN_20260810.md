# KernelOne / 底座代码审计与完善计划

**版本**: 2026-08-10  
**状态**: Phase A landed 2026-08-10 (catalog + debt register + path SSoT fence); later phases still open  
**范围**: `polaris/kernelone`、`polaris/cells` 公共契约面、控制面（Run Ledger / Factory / Director repair）、`delivery`/`bootstrap` 薄适配层  
**原则**: Evidence First · Cell Reuse First · KernelOne Foundation · No Dual Truth · 最小安全变更  

---

## 0. 底座是什么（当前事实）

底座不是某一个目录，而是三层强制叠合：

```
delivery / bootstrap          传输与进程装配（必须薄）
        │
cells/* public contracts      业务能力边界（PM/CE/Director/QA/Factory/Control Plane）
        │
kernelone/*                   Agent-OS 技术底座（LLM / Context / Effect / FS / Events / Quality）
```

权威约束（已确认）：

1. 新开发必须基于 KernelOne 契约与运行时，禁止绕过直连底层。
2. 跨 Cell 只能走 `public/` 契约；禁止 import 他 Cell `internal/`。
3. 每个事实源只能有一个写 owner（Run Ledger、completion contract、repair receipts 分属不同 owner）。
4. `kernelone/` 允许依赖自己，禁止依赖 `delivery` / `application` / `domain`。
5. `docs/graph/**` 是图谱唯一真相。

---

## 1. 审计证据清单

| 证据 | 观察 | 置信度 |
|------|------|--------|
| 规范 | `KERNELONE_ARCHITECTURE_SPEC.md` 明确：底座不得再膨胀成新的 `core/` 垃圾场 | 已确认 |
| 目录面 | `kernelone/` 顶层 **56** 个子树（磁盘为准；旧快照 57 已作废） | 已确认 |
| 体量 | `kernelone/**/*.py` 合计约 **37.5 万行** | 已确认 |
| 大文件 | `artifact_quality` 测试 2075；`locked_regular_file` 1925；`tool_spec_registry` 1861；ContextOS pipeline/evaluation 1.7k+ | 已确认 |
| 双轨 | 同时存在 `polaris/kernelone/` 与 `polaris/cells/kernelone/`（core/traceability） | 已确认 |
| Delivery | `delivery/` 中至少 **142** 个文件引用 `infrastructure` 或 `kernelone`（薄适配层偏厚） | 已确认 |
| Repair | `typescript_syntax` 已包化；`javascript_syntax` 仍 4166；`_normalize_repair_path` **仍 18 处副本** | 已确认 |
| Repair dispatch | `runtime_dispatch` 已部分包化，`__init__.py` 仍 ~1707 | 已确认 |
| 债务账 | `debt.register.yaml` 仅 3 条且均为 `retired` — **活跃结构债未集中登记** | 已确认 |
| 治理门禁 | CELL_KERNELONE_03/04/05/06 存在；04 要求 Cells 路径解析必须委托 KernelOne storage | 已确认 |
| 完成权威 | `completed_verified` 仅 `runtime.projection` 可签发；`factory.verification_guard` 只做物理验证 | 文档已确认，本轮未全链路重放 bench |

尚未验证：

- 57 个 kernelone 子树中哪些是死模块 / 重复子系统。
- `delivery` 142 处引用里有多少是合法薄适配、多少是用例编排泄漏。
- 18 处 path helper 的语义差异在真实 bench 上的接受集。

---

## 2. 问题分层（根因，不是症状）

### P0 — 权威与边界（正确性）

| ID | 问题 | 根因 | 影响 |
|----|------|------|------|
| F-01 | KernelOne 顶层过宽，像第二个 `core/` | 缺「下沉判定 + 准入清单」，能力按需堆目录 | 新 Agent 不知道该复用哪个面，重复实现 |
| F-02 | `kernelone` 包 vs `cells/kernelone` 双入口 | Cell 化中途未切断旧公共面 | 调用方选错层，契约漂移 |
| F-03 | Delivery 直接碰 KernelOne/Infra 过多 | 传输层承担编排/策略 | 破坏「delivery 必须薄」 |
| F-04 | 完成/验证/repair 三套证据链仍依赖约定而非编译期类型 | 控制面字段多、投影多 | 误签发 `completed_verified` / `model_ceiling` 风险 |

### P1 — 底座能力收敛（可维护）

| ID | 问题 | 根因 |
|----|------|------|
| F-05 | LLM：`tool_spec_registry` + toolkit executor + engine executor + stream executor 多层并行 | 历史兼容面未收口 |
| F-06 | ContextOS：models / pipeline / evaluation / projection 各自 1.5k+ | 认知运行时与投影未拆成稳定端口 |
| F-07 | FS：`locked_regular_file` + snapshot 接近 2k | 锁/原子写/路径策略混居 |
| F-08 | Quality：`artifact_quality` 测试已 2k+，生产扫描器仍是平台热点 | 诊断 taxonomy 与 repair coverage 未完全同源 |

### P2 — Repair / 语言内核（上一轮未做完）

| ID | 问题 | 现状 |
|----|------|------|
| F-09 | path helper 18 副本、6 语义 | TS 已走 `path_files`；其它语言未迁 |
| F-10 | `javascript_syntax.py` 4166 仍是单文件 | 应用 TS 同模式 |
| F-11 | `typescript_syntax/common` 已拆子包，但 `misc_ops`/`arg_shape`/`member_text` 仍 >1k | 二次切片 |
| F-12 | `registry` 规则表、`runtime_dispatch` 绑定表仍厚 | 按语言拆 catalog，门面保持 |

### P3 — 治理可执行性

| ID | 问题 |
|----|------|
| F-13 | `debt.register.yaml` 几乎空转；真实债散落在 10+ ledger md |
| F-14 | CELL_KERNELONE_04 等门禁存在，但未覆盖「新 kernelone 子树准入」 |
| F-15 | 图谱 `cells.yaml` 仍标注 phase1 gap（bootstrap 启动仍穿越 infrastructure） |

---

## 3. 完善计划（分阶段）

### 阶段 A — 底座地图与准入（1 个 PR，只读 + 门禁）

**目标**：先让「底座边界」可判定，再改代码。

1. 产出 `kernelone` 能力目录（每个顶层目录：owner、公共入口、是否允许新增文件、替代 Cell）。
2. 新增架构测试：禁止再增加第 58 个顶层子树，除非 ADR + catalog 条目。
3. 明确 `polaris.cells.kernelone.*` 与 `polaris.kernelone.*` 的唯一消费面（推荐：Cell public 对外，kernelone 仅技术实现）。
4. 把 F-01~F-15 登记进 `debt.register.yaml`（status=open），结束「账本空、文档散」。

**成功标准**：新 Agent 读一张表就能知道「路径解析去 storage、工具去 toolkit、完成去 projection」。

**禁止**：本阶段不搬 37 万行。

---

### 阶段 B — 权威链路硬化（控制面 + KernelOne 端口）

**目标**：完成/验证/repair 的 owner 用类型钉死，而不是靠 AGENTS 长文。

1. **Completion**：`runtime.projection` 签发 `completed_verified` 的输入必须是 typed obligation set + owner-sealed receipts；禁止 `dict[str, Any]` 旁路。
2. **VerificationGuard**：只消费 owner contract；caller-supplied evidence 编译期不可进入 success 路径。
3. **model_ceiling**：只能由最终请求快照 + physical attempt + ledger + repair residual 共同封存；补一条架构测试禁止「自报 attempt」触发终态。
4. **ContextOS snapshot**：`context_snapshot_ref` 必须是 24-hex；非法 ref 在 delivery 层 fail-closed（已有规则，补跨 workspace 404 契约测试）。
5. **Path SSoT**：CELL_KERNELONE_04 扩展到 repair_kernel 的 18 处 `_normalize_repair_path`：新代码禁止新增副本；旧副本分批改 `path_files`（strict / permissive 显式）。

**成功标准**：伪造 receipt / 缺 hash / 错 owner 无法走到 `completed_verified`（测试矩阵，不是文档）。

---

### 阶段 C — KernelOne 子系统收口（按子系统串行）

每个子系统一个 PR，模式固定：`ports + 一个实现 + 公共 facade + 杀重复入口`。

| 顺序 | 子系统 | 动作 | 不要做什么 |
|------|--------|------|------------|
| C1 | `kernelone.storage` / paths | 唯一路径解析；Cells 只委托 | 不改磁盘布局语义 |
| C2 | `kernelone.llm.toolkit` | ToolSpecRegistry 为唯一工具事实源；engine/stream 只消费 | 不新建 ToolIntegration |
| C3 | `kernelone.llm` providers | `ProviderManager` 唯一实例化；infrastructure adapter 只做出站 | delivery 禁止 new Provider |
| C4 | ContextOS | pipeline / evaluation / projection 拆端口；UI 只订 runtime.v2 | 不把 bench 事件当生产事实 |
| C5 | FS 原子写 | locked file + snapshot 合并策略对象 | 不改 UTF-8 / 锁语义 |
| C6 | Quality | artifact_quality 诊断码与 repair coverage/registry 同源 | bench_gates 禁止「顺手修」 |

**成功标准**：每个子系统对外 ≤2 个稳定 import 路径；旧 re-export 有 parity 测试（已有 StreamEventType 先例）。

---

### 阶段 D — Repair kernel 余量（Director 确定性修复）

在 **不改启发式 / SOURCE_TOOL 字符串** 前提下：

1. **path_files 全仓采用**：syntax 层 strict，runtime 层 permissive；删 18 副本（每语言一 PR）。
2. **javascript_syntax → 包**：复用 TS 拆包剧本（constants / common / domain / dispatch / facade）。
3. **TS 二次切片**：`common/misc_ops`、`arg_shape_ops`、`member_text_ops`、`type_shapes.py`、`imports_exports.py` 压到 <1500。
4. **registry / runtime_dispatch**：规则表、绑定表按语言文件拆；`default_repair_rule_registry()` / `runtime_repair_bindings()` 门面不变。
5. **Rust**：`rust_syntax` / `rust_runtime` / `rust_ast` 已有分流，按 rule family 继续拆，保持 20 个 Rust executable 不变量。

**成功标准**：无单文件 syntax/runtime >3000；path helper 新副本门禁红；targeted repair tests 全绿。

---

### 阶段 E — Delivery / Bootstrap 变薄

1. 审计 142 处 delivery 引用，分类：合法传输适配 vs 用例编排泄漏。
2. 编排泄漏下沉到 `application/` 或对应 Cell public service。
3. Bootstrap 启动穿越 infrastructure 的 gap（cells.yaml 已写）做成显式 port，而不是继续直 import。

**成功标准**：delivery 新增文件默认不能 import `infrastructure.*`（allowlist 例外 + 架构测试）。

---

## 4. 执行顺序与并行边界

```
A 地图/准入 ─────────────────────────────┐
         │                                │
         ▼                                │
B 权威链路（completion/path/context）      │  不可与 C/D 抢同一契约文件
         │                                │
    ┌────┴────┐                           │
    ▼         ▼                           │
   C1-C3    D1 path 采用                   │
    │         │                           │
   C4-C6    D2 JS 包 + TS 二次切片         │
              D3 registry/dispatch         │
         │                                │
         ▼                                │
         E delivery 变薄 ◄────────────────┘
```

并行规则：

- C 与 D 可并行，但 **禁止同时改** `kernelone/storage` 与 repair `path_files` 语义。
- D2 JS 与 D3 registry 不可并行（JS source_tool 常量被 registry import）。
- E 必须在 B/C 端口稳定之后。

---

## 5. 明确不在本计划内

- 为单个 bench 样例加语言分支或 legacy helper。
- 静默统一 6 种 path 语义而不经 characterization。
- 把 Factory Bench 提升为生产事实源。
- 在 KernelOne 再开第 58 个「方便目录」。
- 大面积重写 roles.kernel turn engine（债已 retired，只守门禁）。

---

## 6. 验证门禁（每阶段）

| 阶段 | 必跑 |
|------|------|
| A | 新架构测试：顶层目录冻结 + catalog 存在 |
| B | completion / model_ceiling / snapshot_ref / path 架构测试 |
| C | 子系统 parity + 原单元测试 |
| D | `test_repair_kernel_*` + `test_typescript_*` + JS 对应子集；path SSoT 结构测试 |
| E | delivery import allowlist 架构测试 |

全阶段：UTF-8、禁止跨 Cell internal import、Ruff/mypy 按改动面最小执行。

---

## 7. 建议的「下一个最小 PR」

**只做阶段 A + B5 的前半**：

1. KernelOne 顶层能力表 + 禁止新增顶层目录的架构测试。
2. `debt.register.yaml` 写入 F-01~F-15。
3. repair_kernel 架构测试：`typescript_syntax` 包内禁止再出现 `while normalized.startswith` 本地实现（已有包测可上提为 kernel 级）。

三天内可合；不碰启发式；为后续 C/D 提供地图。

---

## 8. 回滚

- 每阶段独立 PR。
- 不改 SOURCE_TOOL / completion hash 算法 / 磁盘 layout 语义则 Run Ledger 历史可保留。
- 包化类变更保持旧 import 面 re-export，回滚 = revert 单 PR。
