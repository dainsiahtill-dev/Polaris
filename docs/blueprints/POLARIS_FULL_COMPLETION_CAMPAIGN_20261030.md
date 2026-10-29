# Polaris 全量完善战役蓝图（2026-10-30）

> 任务：全量完善 Polaris 元平台。用户裁定优先级：① 技术债清零 ② 文档规范对齐；允许大规模重构；门禁欠账随技术债首批清零，基线转绿后再做 god-module 大拆分。本蓝图是该战役的唯一方案载体（`src/backend/CLAUDE.md §4.1`）。

## 1. 背景与基线（2026-10-30 实测）

| 面 | 状态 | 数字 |
|---|---|---|
| ruff check（后端） | RED | 2355 errors |
| ruff format（后端） | RED | 195 文件未格式化 |
| mypy strict（后端） | RED | 2573 errors / 448 files / 4633 checked |
| `# type: ignore` | 掩盖 | 1994 处 / 590 文件（与验收标准「禁掩盖真实冲突」冲突） |
| 前端 typecheck / eslint | GREEN | 0 错（但 tsconfig 排除 `*.test.*`，测试文件不在保护面） |
| 前端 vitest | RED | 54 failed / 1702 passed（13 文件；根因含 `residentService.getAgiActionCatalog` 契约漂移） |
| 文档引用 | RED→已修 | `src/backend/server.py`、`pytest src/backend/tests`、`tests/electron` 等断链（批次 0 已修，59d652058） |
| 旧链退役 | 半成品 | `execute_role_session` 已 fail-closed，但 ~15 处生产调用点仍构造 `RoleRuntimeService`；TaskBoard 双模式、TaskMarket 活跃消费者、双 RunLedger 并存（batch 8 未完成） |
| god-module | 残余 | dispatch_pipeline 1701 / _portfolio 4468 / factory_workspace_quality_impl 5424 / _mixin_02 4031 / quality_gate/_prompt_and_targets 4040 / 前端 ResidentWorkspace.tsx 7130 等 12 文件 >1500 行 |
| 双真相源 | OPEN | 第二 ProviderManager（`infrastructure/llm/providers/provider_registry.py`）；`core_roles.yaml` 双份（ADR-0055/0065 收敛未完成） |
| 休眠面 | 决策债 | `CTEngineMiddleware` 零生产调用；`KERNELONE_COGNITIVE_ENABLED=0` 门控 74 文件 ~15.5k 行 |

## 2. 文本架构图（债务拓扑 → 目标态）

```
[门禁红基线]                      [结构债]                        [文档漂移]
 ruff 2355 ──┐                    RoleRuntimeService ~15 调用点     CLAUDE/指南断链(已修)
 mypy 2573 ──┼─ 挡住安全重构 ──►  TaskBoard 双模式                  system-map/快照过期
 ignore 1994 ┘                    TaskMarket 活跃消费者             graph/governance 陈旧
                                   双 RunLedger / 双 ProviderManager
                                          │
        ┌─────────────────────────────────┼──────────────────────────┐
        ▼                                 ▼                          ▼
 [P1 门禁清零]                    [P3 退役收尾]                [P4 双真相源收敛]
  ruff --fix+format                旧入口只读化                 ProviderManager 合一
  mypy 按错误码分批                TaskBoard/Market 收口        core_roles.yaml 合一
  type:ignore 解除                护栏测试防回潮                parity 测试
        │                                 │                          │
        └────────────► 绿基线 ◄───────────┴──────────┬───────────────┘
                                                     ▼
                                          [P5 god-module 拆分]
                                           characterization 护航
                                           monkeypatch 三陷阱审计
                                                     │
                                          [P6 文档对齐 II + TODO + 休眠面决议]
```

## 3. 模块职责（每批触碰面）

| 批次 | 目标 | 触碰模块 | 不许触碰 |
|---|---|---|---|
| P0 ✅ | 文档断链修复 | `CLAUDE.md`、`docs/agent-guides/development-reference.md`、`pyproject.toml` testpaths | — |
| P1 | 门禁清零 | `src/backend/**`（机械格式 + 按错误码修型）；`type:ignore` 逐项解除；门禁脚本/CI 接线 | 行为语义；冻结旧引擎运行逻辑 |
| P2 | 前端门禁绿 | `src/frontend/src/**` 测试与契约修复、`vitest.config.ts`、config 镜像、tsconfig 测试覆盖 | 后端 |
| P3 | 旧链退役收尾（batch 8） | `cells/roles/runtime/public/service.py` 调用点收口、`cells/runtime/task_runtime`、`cells/director/task_consumer`、`cells/qa/audit_verdict`、`bootstrap/project_completion_convergence_runtime.py` | 历史解码/只读查询；冻结 owner 旧记录 |
| P4 | 双真相源收敛 | `infrastructure/llm/providers/**`、role config 加载 | 运行中 provider 行为语义 |
| P5 | god-module 拆分 | dispatch_pipeline、_mixin_02、factory_workspace_quality_impl、quality_gate/_prompt_and_targets、_portfolio、前端 12 大件 | 公开契约签名；Cell 边界 |
| P6 | 文档对齐 II + TODO + 休眠面 | docs/**、TODO 26 处、CTEngine/cognitive 决议 | — |
| P7 | 测试覆盖 + flaky | 缺失单测（P1–P5 改动面优先） | — |

## 4. 核心数据流（每批验证闭环）

```
改动 → ruff check --fix / ruff format（改动面）
     → mypy <改动包>（strict，Success 才过；禁 type:ignore 掩盖）
     → pytest <改动面测试> -v（100% 绿；批级再跑全量对比基线）
     → TS 改动另跑 tsc --noEmit + vitest（并刷 .js 镜像坑位）
     → rtk git status（无运行产物）
     → 量化报告（门禁前后数字 / 改动文件数 / 回归测试 / 风险 / 未完项）
     → commit main（Co-Authored-By 规范）
```

fail-closed：任一门禁红不得标记批次完成；同题连败 5 次升级人工。bench 运行期间禁改 src。

## 5. 技术理由

1. **门禁先行**：mypy 2573 错的红基线上做 package 拆分无法检测拆包回归（`file-to-package-refactor-monkeypatch-trap`：私有 top-level 名丢失、monkeypatch 被 submodule 本地绑定绕过、exec-namespace 类方法绕过都不报错）。绿基线 + characterization 测试才是拆包安全网。
2. **机械批先行**：ruff `--fix`+format 是零语义批，先落可以立刻消灭 2355/195 的统计噪音，让后续每批的 ruff 信号只有真实回归。
3. **type:ignore 逐项解除**：1994 处掩盖使 strict 名存实亡；按「解除→mypy 报真错→修真错」顺序还原信号，禁止批量删注释放任红错。
4. **退役收尾而非删除**：旧链只做写准入收口与调用点清理，历史解码/只读查询保留（CLAUDE.md 主线 §7–8）；`RoleExecutionKernel` 协调入口保留，只退役写准入。删除纪律：逐项证据清单、reachability 实证后才删（静态死码历史误报率 ~74%）。
5. **文档对齐后置深度、前置断链**：断链即修（批次 0）；system-map/AGENTS 快照与 graph/governance 的深度对齐放在结构批次后，避免文档追着结构跑两遍。`src/backend` 三镜像快照事实由 `agent_instruction_snapshot_consistent` 门禁保护，改 §15–17 时必须同步。

## 6. 风险与边界

- **并发提交者（openhands 自动提交）**：以文件内容 grep 验证落地，不信 `git diff HEAD`；禁 `git stash/pop`。
- **冻结旧引擎域**（factory bench / L1-L12）：只做内部结构拆分不改行为；bench 期间不改 src。
- **删除误伤**：逐项确认 + reachability 实证；TurnEngine 等核心不删（偿债靠重构/修复）。
- **CI 工作流漂移**：门禁接线前先读现有 6 workflows，避免重复/覆盖。
- **超大测试件**（8808 行 boundary-fence helpers 等）：拆分放 P5 末尾，配套 characterization。

## 7. 未完成门禁记录（滚动）

- [ ] P1 ruff check → 0 errors（基线 2355）
- [ ] P1 ruff format → 0 files（基线 195）
- [ ] P1 mypy → Success（基线 2573）
- [ ] P1 type:ignore → 剩余全部合法（基线 1994）
- [ ] P2 vitest → 0 failed（基线 54）
- [ ] P2 vitest 不再收集 e2e spec；config 镜像合一
- [ ] P3 旧链调用点 → 0 处新 run 写路径（基线 ~15）
- [ ] P4 双真相源 → 单一来源 + parity 测试
- [ ] P5 god-module 全部拆毕 + 三陷阱审计记录
- [ ] P6 TODO 生产码 → 0（基线 26）；休眠面决议落档
- [ ] P7 测试覆盖缺口补齐清单
