# Resident Autonomy 点火蓝图 (Ignition Blueprint)

- **日期**: 2026-06-09
- **范围**: `resident.autonomy` 自治循环驱动 + 决策燃料补全 + 前端按需触发
- **来源**: 《审计报告：Resident Engineer 可用性》（"已接线但从未点火"）
- **关联**: `docs/rfc/0001-resident-engineer.md`（Status: Draft）、`src/backend/polaris/cells/resident/autonomy/cell.yaml`

## 1. 问题陈述

审计结论：Resident 子系统所有零件单独可用、已注册、已挂载、有测试，但：

- **G1（无点火）**：驱动整个智能层的唯一引擎 `ResidentService.tick()`
  （`resident_runtime_service.py:411`）在正常运行中从不被调用——无调度器、
  `start()` 只翻转 `active` 标志、前端有 `useResident.tick()` 但无按钮。
  `get_status()`（UI 每 15s 轮询入口）纯读不计算。净效果：元认知/技能/反事实/
  自改/自动目标在真实使用中从不产出。
- **G3（无燃料）**：`record_resident_decision` 仅在 PM-编排 workflow 路径触发；
  标准 `director --iterations N` 控制台走 `DirectorOrchestrator.execute_task`
  （`director_orchestrator.py:191`）→ 直连 `roles.adapters` director adapter，
  **不记录任何决策**。决策追踪为空 → 即使点火，`tick()` 也捞不到燃料。

经验佐证：本工作区 `.polaris/meta/resident/` 只有 `identity.json` + `agenda.json`，
零 decisions/insights/skills/runtime_state；而 e2e 产物 `resident.state.json`
显示 `tick_count: 1` —— 机器被驱动时可工作，只是正常运行中无人踩油门。

## 2. 目标与非目标

**目标**
1. 为自治循环提供**点火开关**：可选的后台周期 `tick()` 驱动（服务端，按工作区）。
2. 提供**按需点火**：前端 ResidentWorkspace 头部「立即运行一轮反思 (Tick)」按钮。
3. 补全**决策燃料 (G3)**：`DirectorOrchestrator.execute_task` 记录 Resident 决策。

**非目标**（本轮不做，留待后续 wave）
- 补全 skills/experiments/improvements 的 UI 渲染面（审计 G2）。
- 接通 inert 的 CQRS 公开契约（审计 G4）、合并 `workflow_activity` 死副本（G5）。
- 改变 RFC 的阶段模型或引入 `assist`/`bounded_auto` 自动执行能力。

## 3. 关键技术事实（已核实）

| 事实 | 证据 | 设计含义 |
|---|---|---|
| `tick()` 的所有 lab 都是纯计算（无 LLM） | `cells/resident/autonomy/internal/` 无任何 llm/provider 引用 | 周期 tick 廉价无 token 成本，可安全周期化 |
| `tick(force=False)` 在未 `active` 时早退为 `get_status` | `resident_runtime_service.py:411-414` | 后台循环天然受「是否已启动」二次门控 |
| workflow 路径与 `DirectorOrchestrator` 是**互斥**执行器 | workflow 走 `execute_task_phase` activity → `create_role_adapter`（`director_activities.py:387`），不经 Orchestrator | G3 写入不会与 workflow 双重记录 |
| workflow 子进程上下文带 `KERNELONE_WORKFLOW_ID` | `kernelone/trace/context.py:112` | 作为 G3 的二次防双写护栏 |
| lifespan 已算出服务端主工作区 | `app_factory.py:81` `app.state.settings.workspace` | 后台循环的作用域 = 服务端主工作区 |
| `tick()` 受 `threading.Lock` 保护、为同步函数 | `resident_runtime_service.py` `self._lock` | 后台用 `asyncio.to_thread` 调用，避免阻塞事件循环 |

## 4. 文本架构图

```
                          ┌─────────────────────────────────────────────┐
   [ops env flag]         │  delivery/http/app_factory.py  (lifespan)     │
 KERNELONE_RESIDENT_      │   startup: maybe_start_resident_autotick(ws)  │
   AUTOTICK=1     ───────▶│   shutdown: task.cancel() + await             │
                          └───────────────┬───────────────────────────────┘
                                          │ asyncio.create_task
                                          ▼
                ┌──────────────────────────────────────────────────────────┐
   (新增)        │  delivery/http/resident_autotick.py                       │
                │   _loop(ws, interval):  while True: sleep; _run_once(ws)  │
                │   _run_once(ws):  asyncio.to_thread(tick force=False)     │  ← 纯调度，零领域逻辑
                │                   try/except → 永不击穿循环                 │
                └───────────────┬──────────────────────────────────────────┘
                                │ 公开契约 (delivery → cell.public)
                                ▼
   [前端按需]    ┌──────────────────────────────────────────────────────────┐
 ResidentWork-  │  cells/resident/autonomy/public/service.py                │
 space「反思」── │   get_resident_service(ws).tick(force=…)                  │
 按钮 (force=T) │     → meta_cognition / skill_foundry / capability_graph    │
   │            │       / counterfactual_lab / self_improvement_lab          │
   │ useResident│       / goal_governor.generate  → 落盘派生状态              │
   │  .tick()   └──────────────────────────────────────────────────────────┘
   ▼                                          ▲ 读取 decisions (燃料)
 POST /v2/resident/tick                       │
                                              │ record_resident_decision (公开契约)
   ┌──────────────────────────────────────────┴───────────────────────────┐
   │  application/orchestration/director_orchestrator.py  (G3, 新增)        │
   │   execute_task(): adapter.execute(...) → 计算 status/changed_files     │
   │     → _record_director_decision_safe(ws, payload)                     │
   │         guard: KERNELONE_WORKFLOW_ID 存在则跳过（workflow 自记）         │
   │         best-effort: 异常仅 debug 日志，绝不影响任务执行                  │
   └──────────────────────────────────────────────────────────────────────┘
```

## 5. 模块职责

- **`delivery/http/resident_autotick.py`（新增）**：纯 delivery 调度器。拥有
  asyncio 节奏与生命周期；不含任何领域逻辑；只经公开契约 `get_resident_service`
  调用 `tick`。可独立单测（env 解析、区间钳制、错误吞咽）。
- **`delivery/http/app_factory.py`（改）**：在 `lifespan` startup 段（主工作区解析后）
  调用 `maybe_start_resident_autotick`；在 shutdown 段 `cancel()` 并 `await` 任务。
- **`application/orchestration/director_orchestrator.py`（改）**：在 `execute_task`
  返回前，best-effort 记录一条 Resident 决策（带防双写护栏）。新增模块级私有
  `_record_director_decision_safe`，镜像 workflow 既有 `_record_resident_decision_safe`。
- **`components/resident/ResidentWorkspace.tsx`（改）**：头部新增「立即运行一轮反思」
  按钮，接已存在的 `useResident.tick()`，`isActing('tick')` 期间禁用并显示加载态。

## 6. 核心数据流

1. **按需（前端）**：点击按钮 → `useResident.tick()` → `POST /v2/resident/tick?force=true`
   → `ResidentService.tick(force=True)` 跑全量 lab → 落盘 insights/skills/.../goals
   → `runAction` 自动 `refresh()` → Overview 卡片即时刷新。
2. **无人值守（后台）**：`KERNELONE_RESIDENT_AUTOTICK=1` 时，lifespan 起循环 →
   每 `INTERVAL` 秒对主工作区 `tick(force=False)`。仅当该工作区 resident 已
   `start()`（active=True）时才实际计算，否则无害早退。
3. **燃料（G3）**：任意经 `DirectorOrchestrator.execute_task` 的任务执行（CLI 控制台、
   PM director-interface compat 路径）→ 写入一条 `actor=director, stage=task_execution`
   的决策 → 成为后续 `tick()` 的元认知/技能/反事实输入。

## 7. 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `KERNELONE_RESIDENT_AUTOTICK` | `0`（关闭） | 置 `1/true/yes/on` 启用后台周期 tick |
| `KERNELONE_RESIDENT_AUTOTICK_INTERVAL_SECONDS` | `600` | 周期秒数；下限钳制 `30s` |

**默认关闭的理由**：后台循环会触发 `goal_governor.generate` 自动生成目标提案，属
无人值守自治行为，应是 ops 的显式选择（契合 RFC `Draft` 与 cell「governed autonomy」
价值）。**按需 Tick 按钮始终可用**，因此功能不再「未接线」；后台仅是「无人值守」升级。
> 若希望开箱即默认运行，将 `_DEFAULT_ENABLED` 改为 `True` 即可（一行）。

## 8. 错误隔离与防双写

- **循环永不崩**：`_run_once` 全量 `try/except` + `logger.exception`；`_loop` 仅对
  `CancelledError` 透传以支持优雅停机。单次 tick 失败不影响后续周期。
- **不阻塞事件循环**：同步 `tick()` 经 `asyncio.to_thread` 执行。
- **防双写（G3）**：`KERNELONE_WORKFLOW_ID` 存在即跳过（workflow 层自记）；叠加
  「workflow 与 Orchestrator 互斥」的结构性事实，双重保险。
- **best-effort 语义**：决策记录是可观测性而非任务依赖；失败仅 `debug` 日志，
  `execute_task` 结果不受影响（与 workflow 既有 `_record_resident_decision_safe` 一致）。

## 9. 验证计划

- 新增 `tests/test_resident_autotick.py`：启用/禁用判定、区间解析+钳制、`_run_once`
  吞咽异常、enabled 时 `maybe_start_*` 返回 Task 且可取消。
- 新增/扩展 director orchestrator 决策记录测试：`_record_director_decision_safe`
  在 `KERNELONE_WORKFLOW_ID` 置位时不记录、未置位时调用公开契约（monkeypatch）。
- 前端：`ResidentWorkspace.test.tsx` 既有渲染测试保持绿；新增按钮触发 `tick` 的断言。
- 质量门禁（fail-closed）：后端 `ruff check --fix` + `ruff format` + `mypy` + `pytest`；
  前端 `npm run typecheck` + `lint` + `test`。

## 10. 风险与边界

- **依赖边界**：`application/orchestration/director_orchestrator.py` 新增对
  `resident.autonomy.public` 的依赖。这是经**公开契约**的合法跨界（workflow_runtime
  已有同样依赖先例），不直连 `internal/`。
- **作用域**：后台循环只驱动服务端**主工作区**；多工作区 API 仍按请求各自驱动。
  桌面端单工作区场景天然吻合。
- **不改 effect 面**：`tick()` 的 fs 读写已在 cell.yaml `effects_allowed`
  （`fs.write:runtime/state/resident/*`）内；G3 经公开契约写入，不新增 cell effect。
- **可逆**：两个新增点都是加法且默认关闭/best-effort；移除 env flag 或回退提交即复原。
