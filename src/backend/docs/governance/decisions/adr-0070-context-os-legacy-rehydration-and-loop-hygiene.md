---
status: accepted
date: 2026-04-15
---

# ADR-0070: ContextOS 旧快照兼容恢复与 Tool Loop 噪音隔离

## 背景

Director 在真实代码执行链路中出现了三个联动问题：

1. 旧会话把 `state_first_context_os` 持久化为 `transcript_log_index`，新代码虽然恢复了 `transcript_log` 持久化，但没有兼容恢复旧格式。
2. `tool_result`、`[SYSTEM WARNING]` 一类控制面文本被 Session Continuity 和 ContextOS state hints 当成高信号，污染了 `stable_facts`、`open_loops` 与 run card。
3. 为阻止只读死循环而把 `HARD` 全部改成抛错，会破坏原本 same-tool 渐进式恢复设计，属于过度修复。

## 决策

1. 引入 ContextOS 旧快照再水合层：
   - 读取 `state_first_context_os` 时，若缺少 `transcript_log` 但存在 `transcript_log_index`，使用 `session_turn_events` 恢复完整 transcript。
   - 同时清理 persisted snapshot 中已被污染的 control-plane 状态项。

2. 明确控制面噪音边界：
   - `tool_result`、`[SYSTEM WARNING]`、`[SYSTEM REMINDER]`、`[CIRCUIT BREAKER]` 等文本视为 control-plane noise。
   - Session Continuity summary / stable_facts / open_loops 与 ContextOS state hints 不再从这类文本提取业务状态。

3. 收紧 read-only stagnation 的终止条件：
   - 保留 same-tool / no-gain 的 `HARD = warning-only` 渐进式语义。
   - 仅在代码执行场景、且命中 read-only stagnation 阈值时，将 `HARD` 提升为真正终止。

4. 保留流式 telemetry 修复：
   - `llm_call_end` 必须携带 `prompt_tokens`，保证审计链路闭合。

## 后果

### 正向

- 旧 session 不需要重建即可被当前代码消费。
- 历史污染的 control-plane 文本不会继续回灌 prompt。
- 只读死循环被阻断，但不会误伤同类渐进式探索恢复路径。
- streaming token telemetry 恢复完整。

### 代价

- 读取旧会话时会多一道兼容恢复与清洗逻辑。
- 需要新增回归测试覆盖旧格式与 read-only stagnation 的差异化语义。

## 验证

1. `python -m pytest polaris/kernelone/context/tests/test_continuity.py -q`
2. `python -m pytest polaris/kernelone/context/tests/test_context_os_domain_adapters.py -q`
3. `python -m pytest polaris/cells/roles/session/tests/test_role_session_service.py -q`
4. `python -m pytest polaris/cells/roles/kernel/internal/tests/test_circuit_breaker.py -q`
5. `python -m pytest polaris/cells/roles/kernel/tests/test_llm_caller.py -q`

## 关联资产

- `docs/governance/templates/verification-cards/vc-20260415-context-os-loop-hardening.yaml`
- `docs/TURN_ENGINE_TRANSACTIONAL_TOOL_FLOW_BLUEPRINT_2026-03-26.md`
