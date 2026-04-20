# TOP 6 生死级任务 — 执行蓝图

**日期**: 2026-04-01
**目标**: 止血 + 让系统不会死 + 让系统可 debug

---

## 执行计划（1周）

| Day | 任务 | 产出 |
|------|------|------|
| Day 1 | TurnEngine max_turns 硬限制 | 代码 + 测试 |
| Day 1 | 写工具禁 retry | 代码 + 测试 |
| Day 2 | 全局异常 logging | 代码 |
| Day 3 | Provider TTL + fallback | 代码 |
| Day 5 | 审计链 HMAC | 代码 |
| Day 6 | Tool 定义统一 | 代码 |

---

## Fix 1: TurnEngine 加 max_turns 硬限制

### 问题定位
`polaris/cells/roles/kernel/internal/turn_engine/engine.py`:
- `while True` 循环无 max_turns 检查
- `round_index` 递增但无上限
- `BudgetState` 的 `max_turns=0`，policy.evaluate() 的 check 不触发

### 修复方案
1. `_request_to_state()` 中从 `self.config` 读取 max_turns/max_tool_calls/max_wall_time 设置到 BudgetState
2. `run()` 循环末尾添加 `round_index >= self.config.max_turns` 检查

### 改动点
- `engine.py` `_request_to_state()`: 设置 budgets.max_turns
- `engine.py` `run()`: 循环内添加硬限制检查

---

## Fix 2: 写工具禁 retry

### 问题定位
`polaris/kernelone/tools/executor.py`:
- `execute_with_retry()` 对所有工具按 `on_error="retry"` 重试
- 写工具（write_file/edit_file/precision_edit/append_to_file）可被重复执行

### 修复方案
- 写工具跳过自动 retry（只执行一次）
- 读工具保留 retry 逻辑

### 改动点
- `executor.py` `execute_with_retry()`: 写工具时 max_attempts=1

---

## Fix 3: 全局异常 logging

### 问题定位
60+ 处 `except Exception: pass` 无日志

### 修复方案
统一为：
```python
except Exception as e:
    logger.exception("context message: %s", e)
```

### 改动点
- `polaris/infrastructure/storage/adapter.py`
- `polaris/domain/services/transcript_service.py`
- `polaris/domain/state_machine/phase_executor.py`
- `polaris/bootstrap/backend_bootstrap.py`
- `polaris/kernelone/events/message_bus.py`
- `polaris/kernelone/context/context_os/runtime.py`
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

---

## Fix 4: Provider TTL + fallback

### 问题定位
- `registry.py`: Provider 实例永久缓存
- `runtime.py`: fallback_model 被 `_ = fallback_model` 丢弃

### 修复方案
1. ProviderManager 添加 TTL(5min) 缓存 + 失败驱逐
2. invoke_role_runtime_provider() 实现 fallback 逻辑

### 改动点
- `polaris/kernelone/llm/providers/registry.py`
- `polaris/kernelone/llm/runtime.py`

---

## Fix 5: 审计链 HMAC

### 问题定位
`polaris/kernelone/audit/runtime.py`:
- `signature=""` 硬编码，从未填充

### 修复方案
- 实现 HMAC-SHA256 签名

### 改动点
- `polaris/kernelone/audit/runtime.py` emit_event()

---

## Fix 6: Tool 定义统一

### 问题定位
- `polaris/kernelone/tools/contracts.py` (_TOOL_SPECS)
- `polaris/kernelone/llm/toolkit/definitions.py` (STANDARD_TOOLS)
- 双真相，参数定义不一致

### 修复方案
- 废弃 `definitions.py` STANDARD_TOOLS
- 统一用 `contracts.py` _TOOL_SPECS + ToolSpecRegistry

### 改动点
- `AgentAccelToolExecutor.execute()`: 只查 contracts.py
- 标记 definitions.py 为 deprecated

---

## 风险矩阵

| Fix | 风险 | 缓解 |
|-----|------|------|
| Fix 1 | 低 — 仅添加检查 | 测试覆盖 |
| Fix 2 | 中 — 写工具失败不重试 | 加 idempotency key |
| Fix 3 | 低 — 日志多但安全 | INFO 级别 |
| Fix 4 | 中 — 性能开销 | TTL=5min |
| Fix 5 | 中 — 签名算法选型 | SHA256 足够 |
| Fix 6 | 高 — 改变工具加载 | 向后兼容 |
