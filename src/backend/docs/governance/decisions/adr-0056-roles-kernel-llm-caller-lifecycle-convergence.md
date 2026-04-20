# ADR-0056: roles.kernel LLMCaller 生命周期与请求收口

- 状态: Accepted
- 日期: 2026-03-26
- 相关 VC: `vc-20260326-roles-kernel-llm-caller-hardening`

## 背景

`polaris/cells/roles/kernel/internal/llm_caller.py` 在迁移期承载了过多职责：

1. request planning（contract/capability/context）
2. sync/structured/stream 三种执行
3. lifecycle 审计事件发射
4. 缓存策略
5. structured fallback 请求构建

在当前实现中，这些职责在不同入口有分叉，具体表现为：

1. stream early error 与 native-tools-unavailable 分支的审计收口不一致
2. structured fallback 存在手工 request 构建路径
3. 缓存缺少“仅 plain-text/no-tools 场景”显式边界

## 决策

本 ADR 要求 `LLMCaller` 本轮收口遵循：

1. 统一生命周期事件：`CALL_START -> CALL_END|CALL_ERROR`，禁止静默早退。
2. structured fallback 复用 `_prepare_llm_request()` 基线，不再手工构造偏离合同的请求。
3. 缓存仅用于 `plain_text + no native tools + no response model` 场景。
4. 对外 API 保持兼容，不引入跨 Cell 边界变更。

## 后果

### 正面

1. 观测链路完整，问题定位成本下降。
2. structured path 与 normal path 的 request 语义一致。
3. 避免未来工具调用回合误命中纯文本缓存。

### 负面

1. 文本缓存命中率可能下降（主动 trade-off，优先语义正确性）。
2. 需要补充 `test_llm_caller.py` 与 stream 回归断言。

## 实施边界

1. 本轮只改 `roles.kernel` owned paths：
   - `polaris/cells/roles/kernel/internal/llm_caller.py`
   - `polaris/cells/roles/kernel/tests/test_llm_caller.py`
2. 不改 graph truth，不新增兼容 shim。
3. 不把 `roles.kernel` 业务语义迁入 `kernelone`。

## 验证

最小验证门禁：

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`

