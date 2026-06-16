# Repair-Mode Cross-File Symbol Coherence — Blueprint (2026-06-16)

## 0) 状态
- 类型：弱模型工厂硬化 / 通用根因闭合（#54 reframed）。
- 触发证据：L3-14（React/Vite/TS 博客 SPA）在 int4 上**稳定 FAIL**（非 flaky）：
  `chain=fail files=7 director={total:3,successes:0,failures:1}`。
- 本蓝图只描述方案；实现须 gated + 单测含负例 + **过 L2-floor 回归**后才保留（F21/F22/F25 前车）。

## 1) 根因（已被代码 + L3-14 实证锁定）

L3-14 工作区落了 7 文件（package.json / tsconfig / vite.config / index.html / tsconfig.node / `src/main.tsx` / `src/app.tsx`），
但 `src/main.tsx` `import { router } from './router'` + `import './styles/global.css'`，`src/app.tsx`
`import { router } from './router'` —— **`src/router/index.tsx` 与 `src/styles/global.css` 从未落盘**。
质量门**正确**检出 unresolved import → 进入 `MATERIALIZATION QUALITY REPAIR MODE`，Director 甚至**正确列出**缺失文件
（RAW_RESPONSE: "missed 3 critical target files... src/app.tsx, src/router/index.tsx, src/styles/global.css"），
**但 0/3 successes —— 知道却写不出来**。

机制分两层（都成立）：

1. **覆盖缺口（coverage）**：被 import 引用但**不在任何 task 的 `target_files`** 的本地文件，没有可靠创建路径。
   - 确定性修复 `execute_method.py:_apply_deterministic_missing_declared_target_repair`（:4465-4467）有硬门
     `if missing_rel not in task_candidates: continue` —— **只修 task 声明目标**。`./router`、`./styles/global.css`
     是 import 引用、非声明目标 → 被 skip。
   - 即便进，`_synthesize_declared_target_file_content`（:4485）只对**可模板化**类型产内容；router
     需 `export const router = createBrowserRouter([... 引用页面组件 ...])`，**不可泛化合成** → 返回空 → continue。
2. **符号一致性 + 弱修复收敛（symbol coherence + convergence）**：落空覆盖后掉到 LLM 修复
   `repair_service.run_repair`（:120）—— 只给一段**文本 brief**（`_build_repair_brief`:296，"请修复上述问题"）
   列出 `repair_scope`，**不对每个缺失文件强制写**。弱 Director 在 repair 模式读/叙述、跨轮只部分收敛
   （L3-14：跨轮写出了 app.tsx，但 router/global.css 始终没写），`max_build_rounds=4` 耗尽 → FAIL。
   - 这正是 [[weak-Director-write-tool-wall]] 在 **repair 路径**的版本；F24 进展感知强制写只 gate
     **from-scratch bootstrap** 读循环，**未 gate repair 模式 bootstrap**。

> 关键澄清：**质量门是对的、规划不是主因**。主因 = "import 引用的非声明目标文件" 无可靠创建路径
> + repair 模式无 per-file 强制写收敛。F25（抬 repair 预算 4→8）已证**治错杠杆**被回退：问题不是预算，是 Director repair 模式读不写。

## 2) 现状代码地图

```
QA / artifact_quality_scan
  └─ _collect_materialization_quality_errors (execute_method.py:2518)
       └─ unresolved-import / missing-target errors
            ├─(A) _apply_deterministic_materialization_quality_repairs (:2638)   ← 确定性，无弱模型依赖
            │      └─ _apply_deterministic_missing_declared_target_repair (:4438)
            │           gate: missing_rel ∈ task_candidates  AND  content 可合成
            │           else → 跳过（L3-14 的 router/global.css 在此漏网）
            └─(B) repair_service.run_repair_loop (:207) → _repair_executor(brief, scope)  ← LLM,自由读写
                   _build_repair_brief (:296): 纯文本,无 per-file forced write
                   弱 Director 不收敛 → 0 successes
```

## 3) 设计（三个可独立落地的 gated 增量）

### W1 — 覆盖：把 "import 引用但盘上缺失的本地文件" 纳入缺失目标集
- **在哪**：缺失目标解析处（`_parse_missing_declared_target_files` / `_collect_materialization_quality_errors` 旁）。
- **做什么**：扫描已落盘源文件的**本地相对 import**（`from './x'` / `import './x.css'` / `require('./x')`），
  解析到盘上不存在的文件 → 加入"待创建缺失集"，**不再要求它必须是 task 声明目标**。
- **§8 合规**：纯静态依赖分析（语言无关的相对路径解析），非项目专用硬编码。
- **floor-safe**：只在已有 unresolved-import 错误时扩集；import 全解析的项目（L2 全部、L3 多数）此扫描产出空 → inert。

### W2 — 收敛：对缺失集做 per-file 强制写（复用既有 forced-function tool_choice 阶梯）
- **在哪**：repair 驱动处（`repair_service.run_repair` 或其 `_repair_executor` 接线），复用
  `retry_orchestrator.py:2092` 的 `{"type":"function","function":{"name":"write_file"}}` bootstrap-followup 强制写机制。
- **做什么**：对缺失集中**每个文件**发一个 **forced-write-only** 契约（一次一个文件），brief 里**引用导入行**
  （"`main.tsx` 有 `import { router } from './router'`；创建 `src/router/index.tsx`，必须 `export const router`"），
  循环直到缺失集清空或该文件连续强制写仍空（Wall 2）则降级报错。
- **依据**：实测 int4 vLLM **执行** forced-function tool_choice（直连坐实）；from-scratch bootstrap-followup 已用此机制成功。
- **floor-safe**：只在 repair 模式 + 缺失集非空时触发；正常 turn（首批真写）永不进入。**进展感知**：
  每轮强制写后重算物化指纹，**有新文件落盘才续**（借 F24 的 `_read_bootstrap_makes_no_progress` 信号，避免 F21 计数误判）。

### W3 — 符号一致性 brief 强化
- **在哪**：`_build_repair_brief`（:296）+ W2 的 per-file brief。
- **做什么**：brief 里对每个缺失文件**列出其被引用的符号**（从 importer 的 `import { A, B }` 抽取），
  命令"该文件必须 `export` 这些符号"。把"文件存在"升级为"文件导出 importer 需要的符号"。
- **floor-safe**：纯 prompt 增强，只在 repair brief 内；不改正常路径。

> 优先级：**W1+W2 是主干**（覆盖 + 强制写收敛），W3 是质量增强。最小可行 = W1+W2。

## 4) floor-safe 总论证
- 三个增量**全部 gated 在 "已有 unresolved-import/missing-target 错误 + repair 模式"**：L2 全集 import 自洽（无 unresolved）→
  三者产出空集 → **字节级 inert**（F26 安全类）。
- W2 借 F24 的进展感知信号（非 F21 计数），避免"正常多读收敛被误判"的回归。
- 不改 raw 工具名（§6.6）；角色绑定不变；fix 非删除。

## 5) 验收
- **单测**（`polaris/cells/.../tests/`，确定性无 LLM）：
  - W1：给一个已落盘 main.tsx import `./router`、盘上无 router → 断言 router 进入缺失集；import 全解析 → 缺失集空。
  - W2：stub 一个缺失集 + 一个会"乖乖写"的假 executor → 断言对每个文件发 forced-write 契约、缺失集清空；
    假 executor 连续空写 → 降级报错不死循环。
  - W3：断言 brief 含 importer 引用的符号名。
- **集成/floor**：
  - `bash /home/dains/Temp/factory-bench/run_batch_L2int4.sh` → **RUNNABLE 必须 ≥ 本轮 6/6，无新 dead-letter**（核心铁律）。
  - L3-14 定向重跑（绑 int4）→ 期望 `src/router/index.tsx` + `src/styles/global.css` 落盘、unresolved import=0、chain=clean/qa_passed。
- **三门**：ruff/ruff format/mypy("Success")/pytest 全绿。

## 6) 风险 / gotcha / 依赖
- **Wall 2（forced write 空 content）**：W2 强制写可能返回空 content（实测 int4 会）；需 P3 的空内容检测 + 降级。**W2 依赖/合并 P3**。
- **大小写族**：缺失集解析须用 F19/F20/RC-B/F26 同款大小写不敏感匹配（别把 `./Router` vs `router.tsx` 误判缺失）。
- **import 解析边界**：只认**本地相对 import**（`./`/`../`），忽略 npm 包（`react-router-dom`）；扩展名补全（`./router` → `.tsx/.ts/.jsx/.js/index.*`）。
- **§8 旁证（单独审计项，非本蓝图范围）**：`_apply_deterministic_materialization_quality_repairs` 现挂了多个**疑似项目专用**确定性修复
  （`typeorm`/`zod`/`audit_service_contract`/`task_service_contract`/`tenant_model_contract` + 硬编码 `TaskStatus` enum），疑似 §8 违规，建议另开审计。
- **依赖**：W2 与 P3（Wall 2 空内容）协同；建议 P3 先行或并行。

## 7) 关联
[[weak-Director-write-tool-wall]]、[[cross-file-coherence-fix]]（F8 ownership ledger + syntax gate）、
[[l2-int4-floor-6of6]]（F24 进展感知 / F21-F25 回归教训）、[[l4-multifile-megabatch-wall]]（L4 同族跨文件）、task #54。
