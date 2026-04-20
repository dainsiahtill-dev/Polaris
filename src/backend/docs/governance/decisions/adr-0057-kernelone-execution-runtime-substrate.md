# ADR-0057: KernelOne 统一 Execution Runtime 基座

- 状态: Accepted
- 日期: 2026-03-26
- 相关 VC: `vc-20260326-kernelone-execution-runtime-substrate`

## 背景

当前仓库已经具备若干可复用的 KernelOne 技术子系统：

1. `kernelone.process` 提供异步 subprocess contract 与 streaming handle。
2. `kernelone.locks` 提供文件锁实现。
3. `kernelone.scheduler` 提供 in-process scheduler。
4. `kernelone.trace` 提供 `create_task_with_context()` 等上下文传播能力。

但执行语义仍然分散在多个业务调用点：

1. `workflow_runtime` 与 `pm_planning` 各自直接使用 `SubprocessPopenRunner`。
2. `roles.runtime` 仍保留直接 `subprocess.Popen` + watcher thread。
3. `background_manager` 维持 queue thread + monitor thread + Popen 的独立实现。
4. 部分 LLM/provider 代码仍通过局部线程池补丁绕开阻塞。

结果是：

1. timeout / cancel / backpressure 语义不一致。
2. process / task 生命周期缺少统一状态模型。
3. 观测、审计与 orphan 回收无法在单一点收口。
4. “执行 lane” 只在工具运行时局部存在，没有上升为 KernelOne 统一基座。

## 决策

本 ADR 规定在 `polaris/kernelone/runtime/` 引入统一 `ExecutionRuntime` 基座，作为：

1. `async_task`
2. `blocking_io`
3. `subprocess`

三类执行入口的共同 runtime substrate。

本轮约束如下：

1. 线程仅用于阻塞 I/O offload，不再作为长生命周期任务编排的默认方案。
2. 可中止的长任务优先走 `subprocess` lane。
3. 所有 lane 必须具备统一的：
   - execution id
   - queued/running/terminal 状态
   - timeout
   - cancel / terminate
   - backpressure / concurrency limit
   - trace-friendly task creation
4. `ExecutionRuntime` 只承载技术执行语义，不承载 Polaris 业务状态与业务策略。
5. 本轮只接一条 canonical 调用链到新 runtime，避免大面积同时迁移导致回归面失控。
6. 对外必须提供高层迁移门面（ExecutionFacade），使并行迁移无需重复编排底层状态机。
7. 在 Cell 层新增 `runtime.execution_broker`，作为业务 Cell 唯一推荐的执行中转门面。

## 后果

### 正面

1. KernelOne 首次具备跨 Cell 可复用的统一 execution substrate。
2. 业务调用点不再各自拼接 queue / thread / subprocess 生命周期。
3. subprocess timeout 能在 runtime 层统一回收，降低 orphan 风险。
4. 业务 Cell 有统一接入面：`runtime.execution_broker -> kernelone.execution_facade`。

### 负面

1. `blocking_io` lane 的超时无法像 subprocess 一样强杀线程，只能保证事件循环不被阻塞。
2. graph catalog 目前尚未声明 `kernelone.execution_runtime` 技术 cell，这是治理 gap。
3. 仍有历史调用点未迁移，短期内会处于“新基座 + 旧路径并存”的过渡期。

## 实施边界

1. 本轮新增：
   - `polaris/kernelone/runtime/execution_runtime.py`
   - `polaris/kernelone/runtime/execution_facade.py`
   - `polaris/cells/runtime/execution_broker/**`
   - `polaris/kernelone/tests/test_execution_runtime.py`
   - `polaris/kernelone/tests/test_execution_facade.py`
   - `polaris/cells/runtime/execution_broker/tests/test_service.py`
2. 本轮接线：
   - `polaris/cells/orchestration/workflow_runtime/internal/process_launcher.py`
   - `polaris/cells/orchestration/pm_planning/service.py`
   - `polaris/cells/roles/runtime/internal/process_service.py`
3. 本轮仅更新 KernelOne runtime 导出，不修改用户当前已改脏的其它 KernelOne 热点文件。
4. 本轮不新增兼容 shim，不恢复旧路径。

## 治理 Gap

1. `docs/graph/catalog/cells.yaml` 当前存在对 `kernelone.process` 的依赖引用，但缺少正式 `kernelone.*` 技术 cell 定义。
2. 在技术 cell catalog 补齐前，本 ADR 作为当前事实与迁移方向记录，不把该 gap 伪装成“已治理完成”。

## 验证

最小验证门禁：

1. `python -m pytest -q polaris/kernelone/tests/test_execution_runtime.py`
2. `python -m pytest -q polaris/tests/orchestration/test_workflow_engine.py -k process_launcher`
