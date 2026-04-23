---
status: accepted
date: 2026-04-23
---

# ADR-0083: SUPER Mode Director Continuation Integrity

## 背景

CLI `--super` 已能把高层工程意图分流到 `architect -> director` 或 `pm -> director`。
但在实际执行中，任务市场与任务文件虽然已经正确产出，Director 仍可能在末段失去持续修改能力。

实测表明问题不在任务发布，而在三层状态不一致：

1. `delivery.cli` 只对 `code_delivery` 打开 Director 自动续执行。
2. `roles.kernel` 与 `roles.runtime` 把任意成功 write 都当成 authoritative write。
3. `roles.runtime` 的 read-only auto-end 会在 SUPER materialize Director 会话中把状态提前收口为 `done`。

## 决策

### 1. Director auto-loop 覆盖所有 SUPER 代码交付链

只要 SUPER 路由最终进入 Director 执行代码落地，就必须使用同一套 `_run_director_execution_loop()`。
因此 `architect_code_delivery` 与 `code_delivery` 一律纳入 loop 覆盖。

### 2. 建立 authoritative write 语义

系统不再把“任意写工具成功”当成 mutation satisfied。

authoritative write 的最小规则为：

1. 排除 `SESSION_PATCH.md`
2. 排除 `.polaris/**` 控制面/运行时写入
3. 其余可识别目标文件写入视为 authoritative
4. 无法解析路径时保守放行，避免误伤真实 patch 工具

此规则必须同时驱动：

1. `MutationObligationState.authoritative_write_count`
2. `PhaseManager` 的 `implementing` 推进
3. `roles.runtime` 的 materialize completion 判定

### 3. SUPER materialize Director 禁止 read-only auto-end

在 `role=director` 且会话 goal/context 含 `SUPER_MODE_HANDOFF` 或 `SUPER_MODE_DIRECTOR_CONTINUE` 时：

1. `read_only_termination_exemption` 不得把 AUTO_CONTINUE 改写成 END_SESSION
2. 会话终止必须来自显式完成信号、显式终态文本或 loop safety cap

## 影响

### 直接影响

1. 高层工程请求 `architect -> director` 不再只执行一轮 Director。
2. `SESSION_PATCH.md`、`.polaris/projects/.../runtime/*` 之类辅助写入不再污染 mutation 满足度。
3. `done -> implementing` 的 phase 回退不再由错误的 auto-end 链路触发。

### 边界说明

1. 该 ADR 不改变 SUPER 的 delivery-layer orchestration 定位。
2. 该 ADR 不把 `super` 注册为真实业务角色。
3. 该 ADR 只收紧 authoritative write 判定，不改变普通 write tool 的安全/授权模型。

## 验证

1. `polaris/delivery/cli/tests/test_terminal_console.py`
2. `polaris/delivery/cli/tests/test_super_mode.py`
3. `polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py`
4. `polaris/cells/roles/kernel/tests/test_phase_timeout_loop_fix.py`
5. `polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py`
6. `polaris/cells/roles/kernel/tests/test_modification_contract.py`

## 关联资产

1. `docs/blueprints/CLI_SUPER_MODE_DIRECTOR_CONTINUATION_INTEGRITY_BLUEPRINT_20260423.md`
2. `docs/governance/templates/verification-cards/vc-20260423-super-mode-director-continuation-integrity.yaml`
