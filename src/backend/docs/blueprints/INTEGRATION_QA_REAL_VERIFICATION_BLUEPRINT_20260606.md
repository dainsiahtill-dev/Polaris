# Integration QA 真实验证蓝图 (Real Verification)

状态: Active
日期: 2026-06-06
适用范围: `src/backend/polaris/cells/orchestration/pm_planning/internal/shared_quality.py`（integration QA verify runner）
关联治理资产:
- `docs/governance/templates/verification-cards/vc-20260604-cognitive-runtime-contextos-production-activation.yaml`
- `docs/governance/templates/verification-cards/vc-20260603-pm-game-contract-scope-gate.yaml`

## 1. 背景 / 审计事实 (Evidence)

桌面端全链路（PM → Chief Engineer → Director(gemma-4-26b) → QA）在 `~/Temp/polaris-card-game-e2e`
报告 **22/22 成功 + build/test 绿灯**，但这是 **false green**：

1. **空壳验收 (hollow gates)**:
   - `build.mjs` 仅统计文件数/行数（≥18 模块、≥1200 行），不做任何 `tsc` 编译。
   - `test.mjs` 仅静态 grep 测试文件是否 `import ../../src/` + 含 `run*Checks/failures`，**从不执行任何测试**。
2. **integration QA 同样空壳**: `detect_integration_verify_command` 对 node 项目返回
   `npm run test -- --watch=false` → 执行空壳 `test.mjs` → exit 0 → `PASSED: True`（已实测复现）。
3. **真实 `tsc --noEmit` 立即暴露真相**: 当前“绿灯”代码存在 **6 个 TS1128 语法错误**
   （全部位于被注水的 30KB `src/animation/card-animations.ts`），即 **代码根本无法通过 TypeScript 解析**。
4. **行数门禁反向激励注水**: `≥1200 行` 直接诱导弱模型用通用 `*Registry/*Summary` CRUD 模板灌水。

结论：认知运行时（Context OS / 邪术层）当前 *已接入并产出 receipt*（`cognitive_runtime_receipt.ok`、
`context_os_audit.ok`），但 **验收闭环不保证真实性**，导致“无人开发”在弱模型下产出不可编译代码却判定成功。

分类: `structural`（多模块共享同一“结构性即通过”的错误验收假设）。

## 2. 目标 (Goal)

让 integration QA 对 TypeScript 项目执行 **真实编译验证**，使弱模型产出的语法/类型错误必然被拦截，
且不违反目标项目“禁止新增外部构建/测试依赖”的硬约束（编译器走 Polaris 仓库 toolchain）。

## 3. 架构 (Text Diagram)

```
desktop full-chain (V2 HTTP)
  └─ pm_orchestrator.run_dispatch
       ├─ director_orchestrator → 22 task adapters → 写文件 (write_tool_evidence)
       ├─ director_result_artifacts.build_director_result_from_runtime  → status=success (仅看任务完成)   [Fix A: fail-closed]
       └─ dispatch_pipeline.run_post_dispatch_integration_qa
            └─ shared_quality.run_integration_verify_runner(workspace)                                   [Fix B: 真实验证]
                 ├─ (新) _run_typescript_typecheck → 仓库 tsc --noEmit --skipLibCheck   ← 本蓝图核心
                 └─ detect_integration_verify_command → 执行项目 test/build 脚本
```

## 4. 模块职责 (Responsibilities)

- `shared_quality._resolve_repo_tsc()`（新）: 解析可用 `tsc`（env `KERNELONE_TSC_PATH` 优先，否则自
  `__file__` 向上找 `node_modules/.bin/tsc`）。
- `shared_quality._is_typescript_project()`（新）: `tsconfig.json` 存在且含 `.ts/.tsx` 源文件。
- `shared_quality._run_typescript_typecheck()`（新）: 用仓库 tsc 跑 `--noEmit --skipLibCheck`，解析
  `error TS####`，**过滤“声明依赖未安装”噪声**（TS2307/TS2688/TS7016 且模块名属于 package.json 声明依赖），
  其余错误（语法 TS1xxx、类型错误）一律判定失败。
- `shared_quality.run_integration_verify_runner()`（改）: node 项目先跑 TS typecheck（命中即 fail-fast），
  通过后再跑既有 detected command。**函数签名不变**（无跨 cell 契约变更）。

## 5. 核心数据流 (Data Flow)

`run_integration_verify_runner(ws)`：
1. node 项目 & `KERNELONE_INTEGRATION_QA_TS_TYPECHECK!=0` & TS 项目 & tsc 可解析
   → 运行 `tsc --noEmit --skipLibCheck`（cwd=ws, timeout 可配）。
2. 过滤声明依赖缺失噪声后若仍有 `error TS####` → `return (False, summary, errors[:20])`。
3. 否则继续既有命令检测/执行，返回其结果。
4. tsc 不可解析时保持旧行为（不回归非 TS / 无 toolchain 环境），但记录 evidence note。

## 6. 技术理由 (Rationale)

- **真实信号**: `tsc` 是 TS 栈的权威编译器；语法/类型错误是“代码是否真实可用”的最低保证。
- **不破坏目标约束**: 编译器来自 Polaris 仓库 toolchain，不向目标项目新增依赖；`--skipLibCheck` +
  依赖缺失噪声过滤，避免“未安装 three”造成假阴性。
- **fail-closed 对齐**: 与 CLAUDE.md「验证失败不得标记任务完成」一致。
- **契约稳定**: 仅改 `internal/` 实现，签名与返回结构不变，回归面可控。

## 7. 验证计划 (Verification Plan)

1. 新增单测 `shared_quality` typecheck：构造含语法错误的临时 TS 工程 → 断言 `run_integration_verify_runner` 返回 False 且 errors 含 `TS1128`；构造干净工程 → True；声明依赖缺失 → 不误判。
2. `ruff check` / `ruff format` / `mypy` on `shared_quality.py` + 新测试。
3. `pytest` 针对 `pm_planning` 相关既有测试，确认无回归。
4. 真值复现：对 `~/Temp/polaris-card-game-e2e` 运行 `run_integration_verify_runner` → 期望 **False（6×TS1128）**。
5. 后续：修复 Polaris 源头后重跑桌面全链路，证明从 false-green → 真实 fail/收敛。

## 8. 风险与回滚 (Risks & Rollback)

- 风险：tsc 误把“声明依赖未安装”判为错误 → 已用依赖名白名单过滤；可 `KERNELONE_INTEGRATION_QA_TS_TYPECHECK=0` 紧急关闭。
- 风险：tsc 不存在的 CI 环境 → 解析失败时保持旧行为，不阻断。
- 回滚：删除新增函数 + 还原 `run_integration_verify_runner` 即可（实现内聚于单文件）。

## 9. 后续 (Follow-ups, 非本蓝图实现范围)

- Fix A：`director_result_artifacts` / pm_orchestrator 终判须对 `integration_qa` `not-run/failed` fail-closed。
- Fix C：PM/Director 验收契约移除“行数门禁”，改为真实行为/编译信号。
