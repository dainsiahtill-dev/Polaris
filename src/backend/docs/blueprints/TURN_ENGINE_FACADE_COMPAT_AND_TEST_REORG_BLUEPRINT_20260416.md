# TurnEngine Facade Compat And Test Reorg Blueprint (2026-04-16)

## Goal

在不恢复旧 `TurnEngine` 循环实现的前提下，完成一次受控兼容修复：

1. 冻结 `TurnEngine` 为 deprecated facade，只补兼容缺口，不新增主逻辑。
2. 补回 facade 仍应维护的结果契约：
   - `run_stream()` finalization 内容覆盖决策期可见文本
   - `run_stream()` `complete` 事件恢复 `result`
   - `run()` / `run_stream()` 结果补回 `turn_id` / `turn_envelope`
3. 重组测试：
   - 删除过时的 run/stream 等价性断言
   - 保留当前 `TransactionKernel` 单轮事务语义下仍成立的兼容断言
   - 把状态机/stream/tool-loop 测试改成断言“当前事实”，而不是旧实现行为

## Textual Architecture Diagram

```text
RoleExecutionKernel / TurnEngine facade
  -> TransactionKernel.execute / execute_stream
      -> decision stream
      -> tool batch runtime
      -> finalization
      -> completion

Compat layer responsibilities
  -> map TransactionKernel result/event
  -> preserve minimal legacy metadata contract
  -> emit deprecation signal

Tests
  -> keep facade compatibility assertions
  -> remove obsolete parity assumptions
  -> align stream/tool-loop expectations with single-turn transaction semantics
```

## Scope

- Code:
  - `polaris/cells/roles/kernel/internal/turn_engine/engine.py`
  - shared compatibility helpers under `polaris/cells/roles/kernel/internal/**` if needed
- Tests:
  - `polaris/cells/roles/kernel/tests/test_run_stream_parity.py`
  - `polaris/cells/roles/kernel/tests/test_stream_parity.py`
  - `polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`

## Non-Goals

1. 不恢复旧 `while True` tool loop 或多轮 stall 状态机。
2. 不把 `TurnEngine` 重新变成主执行引擎。
3. 不为了旧 parity 测试去扩大 `TransactionKernel` 的当前事务语义。

## Root Cause

1. `TurnEngine` facade 和 `RoleExecutionKernel` stream shim 已经出现行为漂移，导致 finalization 内容处理不一致。
2. `complete` 事件在 facade cutover 后丢失 `result`，旧兼容调用点断裂。
3. 测试仍把“旧 run/stream 双轨等价”当成架构事实，但当前事实已经是 `TransactionKernel` 单轮事务 + handoff/finalization 语义。
4. 部分测试替身仍沿用旧 `_execute_single_tool(..., tool_args=...)` 形状，和当前 `args=` 调用方式不匹配。

## Implementation Plan

### Commit 1: Facade Freeze + Compat Patch

1. 在 `TurnEngine` 上增加显式 deprecation warning 与 freeze 注释。
2. 在 facade 层统一构造 `turn_id` / `turn_envelope` metadata。
3. 在 stream complete 事件补回 `RoleTurnResult`。
4. 修复 finalization chunk 覆盖逻辑，确保 `complete.content` 只反映最终用户可见输出。

### Commit 2: Test Reorg

1. 删除/改写过时 parity 断言：
   - 不再要求旧 multi-turn transcript parity
   - 不再要求旧 stall loop parity
   - 不再要求 `controller._history` 承担当前 `TransactionKernel` 不再维护的历史事实
2. 将 stream/tool-loop 测试改成：
   - 断言 finalization 后的上下文形状
   - 断言 tool events/tool execution 证据
   - 断言 handoff/too-many-tools 等当前语义
3. 修正测试替身签名，统一接收 `args`

## Verification

1. `python -m pytest polaris/cells/roles/kernel/tests/test_run_stream_parity.py polaris/cells/roles/kernel/tests/test_stream_parity.py polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py -q`
2. `ruff check polaris/cells/roles/kernel/internal/turn_engine/engine.py polaris/cells/roles/kernel/tests/test_run_stream_parity.py polaris/cells/roles/kernel/tests/test_stream_parity.py polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py --fix`
3. `ruff format polaris/cells/roles/kernel/internal/turn_engine/engine.py polaris/cells/roles/kernel/tests/test_run_stream_parity.py polaris/cells/roles/kernel/tests/test_stream_parity.py polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`
4. `mypy polaris/cells/roles/kernel/internal/turn_engine/engine.py polaris/cells/roles/kernel/tests/test_run_stream_parity.py polaris/cells/roles/kernel/tests/test_stream_parity.py polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`

## Risks

1. `complete.result` 的兼容恢复如果依赖过深事件细节，可能再次把 facade 绑回旧内部结构。
2. 直接删除 parity 测试可能误删仍有价值的契约覆盖，因此必须保留当前事实下的兼容断言。
3. `turn_envelope` metadata 恢复需要保持 additive，不得影响现有 handoff pack 结构。
