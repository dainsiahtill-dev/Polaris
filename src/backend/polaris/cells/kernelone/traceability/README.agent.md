# kernelone.traceability

**Status**: Declared in cells.yaml — 目录待实现
**Directory**: polaris/cells/kernelone/traceability/（待创建）

## 职责

平台级可追溯性服务。注册执行节点、链接构件、持久化不可变的可追溯性矩阵。不依赖任何应用级 Cell。

### 管理模块

- `polaris.kernelone.traceability.public.contracts` — 公开契约定义
- `polaris.kernelone.traceability.public.service` — 公开服务接口
- `polaris.kernelone.traceability.internal.service_impl` — 内部实现
- `polaris.kernelone.traceability.internal.safety` — 安全校验

## 公开契约

模块:
- `polaris.kernelone.traceability.public.contracts`
- `polaris.kernelone.traceability.public.service`

### Queries
- **`QueryTraceabilityMatrixV1`** — 查询可追溯性矩阵

### Results
- **`TraceabilityMatrixV1`** — 可追溯性矩阵
- **`TraceNodeV1`** — 追踪节点
- **`TraceLinkV1`** — 追踪链接

### Errors
- **`TraceabilityErrorV1`** — 追踪错误

## 依赖

无。

## 效果

- `fs.read:runtime/traceability/*`
- `fs.write:runtime/traceability/*`

## 状态拥有

- `runtime/traceability/*`

## 验证

- 测试:
  - `polaris/kernelone/traceability/tests/test_traceability.py`
  - `polaris/kernelone/traceability/tests/test_safety.py`
- Gaps:
  - Vector/graph query backend not yet implemented.

## 备注

本 Cell 无独立的 `polaris/cells/kernelone/traceability/` 目录，owned_paths 指向 `polaris/kernelone/traceability/` 下的现有代码。
