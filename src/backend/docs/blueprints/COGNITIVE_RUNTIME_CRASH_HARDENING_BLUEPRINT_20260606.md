# Blueprint — Cognitive Runtime crash hardening (preflight must never crash the host turn)

- 日期: 2026-06-06
- 作者: claude-opus-automation-test-director
- 分类: Bug 根因修复（KernelOne 认知运行时 / RoleRuntime 预检）
- 触发: SWE-bench Arch-B 代表性样本（随机 20 题，django/sympy/matplotlib/sphinx）盲修时，约半数实例的 ChiefEngineer 调用直接崩溃。

## 1. 现象 (Symptom)

`generate_role_response(role="chief_engineer", ...)` 在多个 django 实例上崩溃（solve rc=1），两类报错：

1. `OSError: [Errno 36] File name too long: '<workspace>/django__django-11133/t handle memoryview objects\nDescription...'`
2. `RuntimeError: cognitive_runtime_blocked:Blockers: ('Low probability - insufficient confidence',)`

均发生在 `RoleRuntimeService._prepare_session_request → _apply_cognitive_runtime_preflight` 链路（MAINLINE 模式）。

## 2. 根因 (Root Cause, 经 codegraph 定位)

- 预检在 MAINLINE 模式调用 `CognitiveMiddleware.process(message=<problem_statement>, ...)` →
  `CognitiveOrchestrator → pipeline_coordinator → execution/pipeline → acting_handler.execute_action → RollbackManager.prepare_rollback`。
- **OSError 根因**：`rollback_manager.py:77` 的 `if path.exists() and path.is_file():` 未被保护。acting handler 把**问题陈述的片段**当作"目标文件路径"传入 `target_paths`；该字符串作为单段文件名远超 `NAME_MAX`，`path.exists()` 抛 `OSError [Errno 36]`。注意：原代码的 `try/except (OSError, ValueError)` 只包住了后面的**读文件**，没包住 `.exists()/.is_file()` 本身。
- **传播根因**：`CognitiveMiddleware.process` 的兜底 `except (RuntimeError, ValueError)` **不含 OSError**，故该 OSError 穿透中间件、穿透预检，直接崩掉宿主角色调用。
- **MAINLINE 阻断**：MAINLINE 设计为 fail-closed——低置信度即 `cognitive_runtime_blocked` 抛错。对"无写工具的单轮 CE 定位 + 困难仓库"，这会系统性地误阻断合法自动化调用。

## 3. 修复方案 (Fix)

### Fix A — `RollbackManager.prepare_rollback` 路径存在性检查防御（直接根因）
把 `.exists()/.is_file()` 用 `try/except OSError` 包住；非法/超长路径不可 stat → 记为 `unreadable` 并 `continue`，绝不让坏路径崩掉 rollback 准备（进而崩掉整个认知 turn 与角色调用）。

### Fix B — `CognitiveMiddleware.process` 边界容错（纵深防御）
兜底 `except` 增加 `OSError`：认知预处理是**增强项**，任何基础设施错误（坏路径/IO）都必须降级为 no-op 上下文，绝不崩掉宿主角色 turn。

### Fix C — Headless 基准用 SHADOW 模式（正确配置，非引擎语义弱化）
SWE-bench 盲修属 headless 自动化：解题器 `polaris_solve_one.py` 默认 `KERNELONE_COGNITIVE_RUNTIME_MODE=shadow`（认知运行时观测/记录但不 gate）。这是 SHADOW 模式的设计用途（"benchmarking and validation"），保留认知运行时在场，同时不让 MAINLINE 的低置信阻断误杀合法定位。**未改动 MAINLINE 的治理语义**。

## 4. 验证 (Verification)

- 复现：django-11133 在 MAINLINE 下从 `OSError` 崩溃 → 修复后正常产出 gemma 定位（1221 字符，命中 `django/http/response.py`）；SHADOW 下同样正常。
- ruff / mypy：clean（rollback_manager.py、middleware.py）。
- 回归：认知套件 `execution/tests` + rollback + middleware + bugfixes + benchmark **87 passed**；新增 `TestRollbackManagerMalformedPath`（超长/含换行路径→不再 OSError，按 unreadable 域错误处理）后 rollback 套件 **14 passed**。
- 端到端：SHADOW 重跑 20 题盲修，`solve rc=1` 崩溃数 **4/8 → 0**。

## 5. 风险与边界

- 仅 KernelOne cell 内实现 + 一处脚本默认环境变量；无公开契约 / 状态拥有 / effect 变化。
- 未弱化 MAINLINE 治理：低置信仍会 block；只是基础设施错误不再崩溃，且基准跑用 SHADOW。
- 深层项：acting handler 把任意消息解析为带文件目标的"动作"（旧认知骨架的脆弱解析）属更上游议题；本次以 rollback/middleware 边界做 fail-safe 加固，未改 acting 解析语义。
