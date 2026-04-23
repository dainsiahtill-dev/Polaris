# CLI SUPER Mode Director Continuation Integrity Blueprint

Date: 2026-04-23
Status: Proposed
Classification: Structural

## 1. Objective

彻底修复 CLI `--super` 在 `architect -> director` / `pm -> director` 代码交付链路中的续执行失真问题，保证：

1. 任务发布成功不等于 Director 已完成代码落地。
2. Director 的自动续执行不依赖单一 `code_delivery` 路由原因。
3. 控制面/运行时辅助写入不会被误判为“真实代码修改已完成”。
4. `roles.runtime` 的 read-only auto-end 不会把 SUPER materialize Director 会话提前收口到 `done`。

## 2. Observed Facts

1. `runtime/task_market/task_market.db` 与 `runtime/tasks/task_1.json` 至 `task_5.json` 已真实生成。
2. 因此故障不在任务发布，而在 Director 执行续回合与会话状态一致性。
3. 现有实现里：
   - `architect_code_delivery` 会进入 `architect -> director`
   - Director 自动 loop 只覆盖 `code_delivery`
   - 任意成功 write receipt 都会被 runtime 当成 mutation satisfied
   - `SESSION_PATCH.md` / `.polaris/**` 这类辅助写入会污染 mutation/phase 判定

## 3. Root Cause

### 3.1 Delivery layer gap

`terminal_console._run_super_turn()` 只在 `decision.reason == "code_delivery"` 时执行 `_run_director_execution_loop()`。
这使 `architect_code_delivery` 仅获得一次 Director materialize 机会。

### 3.2 Authoritative write gap

`roles.runtime` 与 `roles.kernel` 都把“任意写工具成功”视为 authoritative write。
结果：

1. `SESSION_PATCH.md` 追加写入会满足 mutation obligation
2. `.polaris/projects/.../runtime/*` 控制面写入会满足 mutation obligation
3. PhaseManager 会被错误推进到 `implementing`

### 3.3 Session close gap

`session_orchestrator._apply_read_only_termination_exemption()` 在 SUPER materialize Director 场景下仍允许 read-only turn 自动收口。
一旦历史上已有被误判的 write receipt，会把后续只读 turn 改写成 `END_SESSION -> done`。

### 3.4 Resume gap

用户再输入 `继续` 时，super 路由会 fallback 到 `director` 单跳复用旧 session。
如果该 session 已被错误写成 `done`，后续 kernel 再要求 `implementing` 会触发 invariant rollback。

## 4. Decision

### 4.1 Director auto-loop coverage

凡是 SUPER 路由最终进入 Director 的代码交付链路，都必须共享同一套 auto-loop 逻辑，至少覆盖：

1. `code_delivery`
2. `architect_code_delivery`

### 4.2 Authoritative write contract

引入统一 authoritative write 判定：

1. 代码/文档等用户目标文件写入，计为 authoritative write
2. `SESSION_PATCH.md` 不计入 authoritative write
3. `.polaris/**` 下的控制面/运行时写入不计入 authoritative write
4. 无法提取路径的写入保守视为 authoritative，避免误伤真实 patch 工具

该判定必须同时驱动：

1. `roles.kernel` mutation obligation
2. `PhaseManager` 阶段推进
3. `roles.runtime` materialize completion 判定

### 4.3 SUPER Director no-auto-end rule

对于 `role=director` 且 goal/context 含 `SUPER_MODE_HANDOFF` / `SUPER_MODE_DIRECTOR_CONTINUE` 的 materialize session：

1. 禁止 read-only termination exemption 自动改写为 `END_SESSION`
2. 收口只能来自显式完成信号、明确终态输出，或 loop safety cap

## 5. Text Architecture

```text
user request
  -> delivery.cli SuperModeRouter
     -> architect|pm readonly planning stage
        -> director materialize handoff
           -> roles.kernel mutation / phase evaluation
              -> roles.runtime session continuation + invariant check
```

Authoritative write side-channel:

```text
tool invocation / batch receipt
  -> authoritative-write classifier
     -> mutation obligation
     -> phase transition
     -> runtime completion eligibility
```

## 6. Implementation Scope

Primary files:

1. `polaris/delivery/cli/terminal_console.py`
2. `polaris/cells/roles/runtime/internal/session_orchestrator.py`
3. `polaris/cells/roles/kernel/internal/transaction/phase_manager.py`
4. `polaris/cells/roles/kernel/internal/transaction/write_authority.py`
5. `polaris/cells/roles/kernel/internal/transaction/receipt_utils.py`
6. `polaris/cells/roles/kernel/internal/transaction/contract_guards.py`
7. `polaris/cells/roles/kernel/public/transaction_contracts.py`

Expected test scope:

1. `polaris/delivery/cli/tests/test_terminal_console.py`
2. `polaris/delivery/cli/tests/test_super_mode.py`
3. `polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py`
4. `polaris/cells/roles/kernel/tests/test_phase_timeout_loop_fix.py`
5. `polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py`

## 7. Verification Plan

1. `python -m ruff check <changed_paths> --fix`
2. `python -m ruff format <changed_paths>`
3. `python -m mypy <changed_python_paths>`
4. `python -m pytest -q polaris/delivery/cli/tests/test_super_mode.py polaris/delivery/cli/tests/test_terminal_console.py polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py polaris/cells/roles/kernel/tests/test_phase_timeout_loop_fix.py polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py polaris/cells/roles/kernel/tests/test_modification_contract.py`

## 8. Risks

1. Authoritative write denylist过宽会误伤合法写入，因此初版只排除 `SESSION_PATCH.md` 与 `.polaris/**`。
2. Director auto-loop 过度积极可能导致额外 turn；因此仍保留现有 safety cap。
3. SUPER Director 禁止 auto-end 后，必须依赖显式完成信号或 loop cap，测试要覆盖这一行为。
