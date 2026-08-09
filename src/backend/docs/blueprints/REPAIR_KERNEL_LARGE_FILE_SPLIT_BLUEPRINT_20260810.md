# Repair Kernel 大文件拆分蓝图

**版本**: 2026-08-10  
**状态**: Active / Phase-0–2 modularization complete (2026-08-10): `typescript_syntax` package domain-split + path SSoT  
**负责人**: Principal Architect (Director Runtime)  
**范围**: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/`  
**原则**: Evidence First · Behavior-preserving · Small steps · No duplicate reimplementation  

---

## 0. 问题意识（Stage 0）

### 真正要解决的问题

1. **可维护性危机**：单文件承载过多 repair 规则，审查、回归、并行开发成本过高。
2. **重复实现风险**：跨语言 `*_syntax.py` / `*_runtime.py` 复制同类 helper，语义已漂移。
3. **lint 信号是体量信号，不是逻辑缺陷本身**：行数/复杂度高说明职责过载，不得把 C901 伪装成业务 bug。

### 当前证据

| 证据类型 | 内容 | 置信度 |
|---------|------|--------|
| 行数量具 | `typescript_syntax.py` 14483；`runtime_dispatch.py` 4699；`registry.py` 4281；`javascript_syntax.py` 4166 | 已确认 |
| Ruff | `typescript_syntax.py`：C901/PLR0912/PLR0915 共 ~62 热点；`javascript_syntax.py` 13；`registry` 4 | 已确认 |
| 符号碰撞 | `_normalize_repair_path` 出现在 **18** 个文件；**6** 种不同实现体 | 已确认 |
| 近相同克隆 | `_revalidation_failed` 双份；rust 三处 path helper 同体；diagnostics-for-runtime 模板三份 | 已确认 |
| 调用面 | 消费者：`runtime_dispatch`、`registry`、`typescript_runtime`、`__init__`、tests | 已确认 |
| 内部耦合 | `typescript_syntax` 内 `_normalize_repair_path` fan-in ~178；`_repair_plan_or_none` ~57 | 已确认 |
| 基线测试 | `test_repair_kernel_public_exports` + `test_typescript_m10_strict_compile_repairs`：**24 passed** | 已确认 |
| 硬路径 | `platform_modules/registry.py` 引用 `.../typescript_syntax.py` 文件路径 | 已确认 |

### 仍是猜测 / 未证明

- 统一 path helper 后是否改变任何真实 bench 产物路径接受集 → **需要 characterization + 分批切换验证**。
- `registry.py` / `runtime_dispatch.py` 是否应同期拆包 → **P1 之后再定**，避免并行大爆炸。
- 14k 行 TS 规则是否可按 source_tool 自动机械切片而无循环导入 → **高概率可行，但需每批 import 图验证**。

### 改错可能破坏什么

- Director deterministic repair 规划结果（漏修 / 误修）。
- `RunDirectorRepairCommandV1` 可执行 source_tool 绑定与 coverage catalog。
- Factory materialization / post-execution bridge 对 TS plan 的消费。
- 平台模块固化清单中的路径条目。

---

## 1. 当前理解

### 1.1 模块职责

```
repair_kernel/
  contracts.py          # RepairPlan / Operation / Diagnostic 契约
  registry.py           # 规则目录 + language slots + coverage（大）
  runtime_dispatch.py   # plan/run 绑定分派（大）
  *_syntax.py           # 语言规则：diagnostic → RepairPlan（纯规划）
  *_runtime.py          # 运行时 planning DTO + 与 composer 衔接
  executor / receipts / scheduler / policy_gate ...
```

`typescript_syntax.py` 是 **canonical TS/HTML plan builder 仓库**：  
`SOURCE_TOOL 常量 + regex 解析 + build_*_plan + build_typescript_runtime_plan_for_source_tool 分派`。

### 1.2 主调用链（已确认）

```
runtime_dispatch.plan/run
  → typescript_runtime / 直接 source_tool binding
    → typescript_syntax.build_* / build_typescript_runtime_plan_for_source_tool
      → RepairPlan(operations=...)
  → composer / policy_gate / executor
  → receipt + revalidation
```

跨 Cell 禁止 import `repair_kernel.internal`；public 面经 `director.runtime.public`。  
本重构 **只动 internal 包结构与 re-export**，不改 public 契约语义。

### 1.3 大文件优先级（lint 驱动）

| 优先级 | 文件 | 行数 | 主因 | 拆分策略 |
|--------|------|------|------|----------|
| P0 | `typescript_syntax.py` | 14483 | 规则堆积 | 包化 + 按域切片 + 共享 common |
| P0 | 跨文件 path helper 重复 | 18× | 语义漂移 | 先抽出唯一实现，再分批替换 |
| P1 | `javascript_syntax.py` | 4166 | 同类堆积 | 复用 path/common 后再包化 |
| P1 | `runtime_dispatch.py` | 4699 | 绑定表膨胀 | 按语言 binding 表拆分 |
| P2 | `registry.py` | 4281 | 规则元数据 | catalog 数据与匹配逻辑分离 |
| P2 | `rust_syntax.py` / `rust_runtime.py` | 2769/2550 | 已有 rust_ast 分流 | 继续按 rule family 拆 |

---

## 2. 缺陷 / 风险分析（审计视角，非急修）

### D1. Path 规范化语义分裂（Major）

- **表象**：同名 `_normalize_repair_path` 多实现。
- **触发**：路径含 `../`、`a/../b`、Windows 盘符、空串、`./` 前缀。
- **直接原因**：各语言文件本地复制后独立演化。
- **深层原因**：缺少 repair_kernel 级 **唯一 path SSoT**。
- **为何测试未抓**：多数测试用干净相对路径；遍历类输入少。
- **是否立即修业务语义**：否。先抽出模块 + characterization；**分批**替换，禁止 silent unify 改写安全边界。

实现体差异（证据摘要）：

| 变体 | 代表文件 | 对 `a/../b.ts` | 对 `/abs` | 对空串 |
|------|----------|----------------|-----------|--------|
| v1 majority | js/python/go_syntax 等 | 拒（`/../` 子串） | 拒 | 返回 `""` |
| v2 posix | cpp_syntax | 规范化后可能接受 `b.ts` | 拒 | 拒 |
| v3 permissive | typescript_runtime 等 | **接受** | **接受** | 返回 `""` |
| v5 rust | rust_* | 按 segment `..` 拒 | 拒 | 拒 |
| ts_syntax v4 | typescript_syntax | 拒 | 拒 | 拒（含 not normalized） |

**结论**：不得在 Phase-1 强行合并为一种语义并一次切换 18 文件。  
允许先提供 **显式 API**（`strict` / `permissive`），文档化语义，再让调用方声明。

### D2. 单文件职责过载（Major / 可维护性）

- 442 函数、562 行 preamble 常量/regex、公开 plan API 与私有 parse/ops 混居。
- Ruff 复杂度热点集中在 import rewrite、missing member、literal union 等。

### D3. 拆包时重复实现风险（Major if mishandled）

若多个专家并行从 monolith 复制 helper 到子模块而不抽 common，将 **放大** D1。  
强制：`path_files` / `plan_common` 为唯一 helper 源；子模块只 import，不重写。

### D4. 硬编码文件路径（Minor）

`platform_modules/registry.py` 写死 `typescript_syntax.py`；包化后必须改条目。

---

## 3. 目标架构（typescript_syntax 包）

```
repair_kernel/
  path_files.py                 # NEW: path/base_files SSoT（跨语言可渐进采用）
  plan_common.py                # NEW (Phase 1B): repair_plan_or_none, text ops, dedupe, json
  typescript_syntax/            # package replaces module
    __init__.py                 # 稳定 public re-export（兼容 from .typescript_syntax import X）
    constants.py                # SOURCE_TOOL + compiled regex
    dispatch.py                 # build_typescript_runtime_plan_for_source_tool
    object_literals.py          # comma / duplicate prop / missing props / shorthand
    nullability.py              # canvas/null guards / strict null relaxation
    imports_exports.py          # import/export/reexport/type-value conflict
    modules.py                  # missing relative module / augmentation / entrypoint
    members.py                  # missing member / alias / private ctor
    config_scaffold.py          # tsconfig lib/rootdir / scaffold / vitest / zod
    html_dom.py                 # html module script / container / dom shim
    text_repairs.py             # truncated eof / escaped newline / expect-error / hyphenated id
    type_shapes.py              # arg shape / branded literal / union facade / readonly
    _compat.py                  # optional: re-export private helpers for tests if needed
```

**外部行为不变条件**：

- `from polaris...repair_kernel.typescript_syntax import <name>` 继续可用。
- 所有 `TYPESCRIPT_*_SOURCE_TOOL` 字符串值不变。
- `build_*_plan` 返回的 operations / metadata 字段语义不变。
- `build_typescript_runtime_plan_for_source_tool` 分派表 key 集合不缩减。

**禁止**：

- 借拆分改 repair 启发式。
- 在 adapter / Factory / bench 新建第二套 TS repair。
- 子模块互相复制 `_normalize_repair_path`。
- 新增语言专用 public facade。

---

## 4. 分阶段执行计划

### Phase 0 — 审计与基线（完成）

- [x] 行数 lint 排序  
- [x] Ruff 复杂度热点  
- [x] 跨文件同名/同体扫描  
- [x] import 面与 public 符号清单  
- [x] 内部 fan-in  
- [x] 基线 pytest 24 passed  

### Phase 1A — 零语义包壳 + path SSoT 模块（本轮）

1. 新增 `path_files.py`：提供 `normalize_repair_path_strict` / `normalize_repair_path_permissive` / `normalize_base_files`。  
2. 新增 characterization 测试锁定两种语义。  
3. `typescript_syntax.py` → `typescript_syntax/` 包：  
   - 第一步允许 `_impl.py` 整文件迁入 + `__init__.py` 显式 re-export 公开符号；  
   - 或直接 `__init__.py` 承载后立刻抽 `constants`（优先后者若单 PR 可控）。  
4. 更新 `platform_modules/registry.py` 路径。  
5. 跑基线 + contract 子集。

**本阶段不修改** 其他 17 个文件中的本地 `_normalize_repair_path`（避免行为漂移）。

### Phase 1B — 抽出 plan_common + constants（下一小步）

- 将 `_repair_plan_or_none`、`_text_replace_operations_from_repair`、`_dedupe_preserve_order` 等迁入 `plan_common.py`。  
- `constants.py` 迁出 SOURCE_TOOL 与 regex。  
- TS 包内改为 import；**不改函数体**。

### Phase 2 — 按域切片 typescript_syntax（多 PR / 可并行）

并行专家边界（文件互斥）：

| 专家 | 域模块 | 迁出符号前缀 / 规则族 |
|------|--------|----------------------|
| E1 | object_literals | comma, duplicate prop, missing props, shorthand, enum sep |
| E2 | nullability | nullable canvas, strict null, non-null assert |
| E3 | imports_exports | import/export/reexport/type-value/unique binding |
| E4 | modules | relative module, augmentation, entrypoint, json-as-source |
| E5 | members | missing member, member alias, private ctor, unknown member |
| E6 | config_scaffold | tsconfig, scaffold, vitest, zod, sourcefile diagnostics |
| E7 | html_dom | html script, container, dom shim, typeorm, js annotation |
| E8 | type_shapes | readonly, arg shape, branded literal, union, number/string |
| E9 | Integration | dispatch 表、`__init__` re-export、循环依赖门禁 |
| E10 | QA | 每域迁完跑 m10 + contract 相关 + import smoke |

**合并规则**：每域一 PR；CI 绿；禁止同时改 `dispatch.py` 与多域（E9 独占 dispatch）。

### Phase 3 — 跨语言 path 采用 + javascript_syntax 包化

- 逐文件把本地 helper 换成 `path_files`（先 runtime 层 permissive 标注，syntax 层 strict）。  
- 再对 `javascript_syntax` 套用同一包化模式。

### Phase 4 — runtime_dispatch / registry 拆分

- 仅在 TS/JS 包稳定后进行。  
- binding 表按语言拆 `bindings_typescript.py` 等，dispatch 门面保持。

---

## 5. 测试计划

| 类型 | 内容 |
|------|------|
| Happy | 现有 m10 / public_exports / contract 中 TS plan 用例 |
| Edge | path：`""`, `./x`, `../x`, `a/../b`, `\\`, 盘符 |
| Exception | 非法 base_files key 被丢弃；未知 source_tool → `None` |
| Regression | 每个 SOURCE_TOOL 至少保留一条 plan 非空/operations 形状断言（可分批补） |
| Import smoke | `from ...typescript_syntax import build_typescript_runtime_plan_for_source_tool` |
| 架构 | boundary 测试：adapters 仍不得 import internal repair_kernel 实现细节 |

**基线命令**：

```bash
cd src/backend && PYTHONPATH=. pytest \
  polaris/cells/director/runtime/tests/test_repair_kernel_public_exports.py \
  polaris/cells/director/runtime/tests/test_typescript_m10_strict_compile_repairs.py \
  -q

# 扩展（Phase 1 后）
pytest polaris/cells/director/runtime/tests/test_repair_kernel_path_files.py -q
pytest polaris/cells/director/runtime/tests/test_repair_kernel_contract.py -q --tb=line
```

---

## 6. 回滚方案

- Phase 1A：删除新包/`path_files.py`，恢复单文件 `typescript_syntax.py`，还原 registry 路径。  
- 每域 Phase 2 PR 独立可 revert。  
- 不改 public DTO / source_tool 字符串 → 回滚不波及 Run Ledger 历史。

---

## 7. 明确不在范围内

- 新增 TS repair 规则或改变启发式。  
- 合并 6 种 path 语义为一种而不经 characterization。  
- 一次 PR 拆完 registry + dispatch + 全部语言。  
- 恢复 legacy `roles.adapters` repair_kernel。

---

## 8. 成功标准

1. `typescript_syntax` 无单文件 > ~1500 行（Phase 2 完成后）。  
2. 公开 import 路径兼容。  
3. 无第二套 `_normalize_repair_path` 在 **新代码** 中出现；旧副本只减不增。  
4. 目标测试集 100% 绿；contract 无新增失败。  
5. Ruff C901 热点随切片下降（非目标本身，是副作用）。

---

## 9. 决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 先 TS 还是先 path 统一全仓 | 先 path **模块** + TS 包壳，不全仓替换 | 避免 18 文件行为漂移 |
| 包化方式 | package + 稳定 `__init__` re-export | 保持 import 兼容 |
| 并行专家 | Phase 2 按域互斥文件 | 降冲突；E9 守 dispatch |
| lint 角色 | 发现体量/热点，不直接当 bug | 符合正确性优先 |

---

**下一动作**：执行 Phase 1A（`path_files.py` + characterization tests + `typescript_syntax` 包壳 + registry 路径）。
