# Blueprint: E2E Integration Tests for Critical Paths

## 1. 目标

为 `roles/kernel` / `roles/runtime` / `context/` 三个模块的关键路径添加端到端集成测试，覆盖率额外提升 5%。

## 2. 架构分析

### 2.1 关键模块职责

| 模块 | 关键类/函数 | 职责 |
|------|------------|------|
| `roles.kernel` | `TransactionKernel` / `TurnTransactionController` | Turn 事务执行内核，单次决策/单工具批次，ADR-0071 约束 |
| `roles.runtime` | `RoleSessionOrchestrator` | 多 Turn 会话编排器，ContinuationPolicy 仲裁，Checkpoint 持久化 |
| `context/` | `ContentStore` / `ReceiptStore` / `StateFirstContextOS` | ContextOS 内容去重、收据存储、投影引擎 |

### 2.2 执行路径

```
User Prompt
    |
    v
RoleSessionOrchestrator.execute_stream()
    |-- Turn 0: SessionState 初始化 / HARD-GATE 检查
    |       v
    |   TransactionKernel.execute_stream()
    |       |-- LLM Decision (tool_batch / final_answer / ask_user)
    |       |-- ToolBatchExecutor (write/read)
    |       |-- PhaseManager 状态推进
    |       |-- TurnLedger 记录
    |       v
    |   CompletionEvent / ErrorEvent
    |
    |-- Turn 1+: ContinuationPrompt 构建 (4-zone XML)
    |       |-- 检查 ContinuationPolicy (max_auto_turns / stagnation)
    |       |-- 检查 materialize_changes guard
    |       v
    |   SessionCompletedEvent / SessionWaitingHumanEvent
    |
    v
Checkpoint 持久化 (.polaris/checkpoints/)
```

### 2.3 测试策略

使用 **pytest fixtures 模拟外部依赖**：

1. `MockLLMProvider` — 模拟 LLM 调用，返回结构化响应
2. `MockToolRuntime` — 模拟工具执行，返回工具结果
3. `MockWorkspace` — 临时文件系统，测试 checkpoint 持久化
4. `MockContentStore` — 模拟内容存储，测试去重和驱逐

## 3. 测试用例设计

### 3.1 roles/kernel — TransactionKernel E2E

| 测试编号 | 场景 | 关键验证点 |
|---------|------|----------|
| TK-01 | 单 Turn 正常完成 | CompletionEvent 包含 turn_result，kind=final_answer |
| TK-02 | 单 Turn 执行写工具 | ToolBatchExecutor 调用，batch_receipt 包含 write_receipt |
| TK-03 | 单 Turn LLM 决策为 ask_user | TurnContinuationMode=WAITING_HUMAN |
| TK-04 | 空 context 降级 | TransactionKernel 处理空 context 不崩溃 |
| TK-05 | 工具执行异常捕获 | ErrorEvent，返回 failure_class=RUNTIME_FAILURE |
| TK-06 | 多工具批次顺序执行 | ToolBatches 顺序执行，receipt 正确聚合 |
| TK-07 | ContextOS 投影生成 | ContentStore intern/get/release 正确工作 |

### 3.2 roles/runtime — SessionOrchestrator E2E

| 测试编号 | 场景 | 关键验证点 |
|---------|------|----------|
| RS-01 | 首回合正常启动 | SessionStartedEvent → CompletionEvent → SessionCompletedEvent |
| RS-02 | 多 Turn 自动继续 | turn_count 递增，ContinuationPolicy 放行 |
| RS-03 | 多 Turn 达到 max_auto_turns | 第 N 回合后停止，reason=max_turns |
| RS-04 | HARD-GATE 危险操作拦截 | SessionWaitingHumanEvent，reason=DESTRUCTIVE_OPERATION |
| RS-05 | Checkpoint 持久化与恢复 | checkpoint 文件存在，state 正确恢复 |
| RS-06 | materialize_changes guard | 无 write_receipt 时不能 END_SESSION |
| RS-07 | SessionPatch 提取 | session_patch 正确注入 structured_findings |
| RS-08 | 空 prompt 降级 | 不崩溃，使用默认 goal |
| RS-09 | Stagnation 检测 | 连续 2 回合 artifact hash 相同，强制终止 |
| RS-10 | 探索熔断 | 连续 2 回合仅探索工具，追加 mandatory_instruction |

### 3.3 context/ — ContextOS Workflow E2E

| 测试编号 | 场景 | 关键验证点 |
|---------|------|----------|
| CX-01 | ContentStore intern 去重 | 相同内容只存储一次，ref_count 正确 |
| CX-02 | ContentStore 驱逐策略 | 超出 max_entries 时驱逐零引用项 |
| CX-03 | ReceiptStore 收据存储 | 大型工具输出正确 offload 到 ReceiptStore |
| CX-04 | ReceiptStore idempotency | batch_idempotency_key 防止重复执行 |
| CX-05 | ContentStore 线程安全 | 多线程并发 intern/release 不破坏 ref_count |
| CX-06 | ContextOS Projection | 投影结果包含 active_window 和 artifact_stubs |

## 4. 技术实现

### 4.1 目录结构

```
tests/integration/
    conftest.py                          # 共享 fixtures
    roles/
        kernel/
            test_transaction_kernel_e2e.py
        runtime/
            test_session_orchestrator_e2e.py
    context/
        test_contextos_workflow_e2e.py
```

### 4.2 Mock 策略

使用 `unittest.mock.AsyncMock` 和 `MagicMock` 模拟：

- `LLMProvider`: 返回 `{"choices": [{"message": {"content": "..."}}]}`
- `ToolRuntime`: 返回成功/失败的 `ToolExecutionResult`
- `ContentStore`: 使用真实实例，避免 mock 过厚

### 4.3 覆盖率目标

- `roles/kernel`: +3% (主要覆盖 `execute_stream` 主路径)
- `roles/runtime`: +1.5% (主要覆盖 `execute_stream` 和 checkpoint)
- `context/`: +0.5% (主要覆盖 `ContentStore` 和 `ReceiptStore` 集成路径)

## 5. 验证门禁

```bash
pytest tests/integration/ -v --tb=short --cov=polaris --cov-report=term-missing
ruff check tests/integration/ --fix
ruff format tests/integration/
mypy tests/integration/ --strict
```

## 6. 风险与边界

1. **Mock 过厚风险**: 避免过度 mock，使用真实 ContentStore/ReceiptStore 实例
2. **异步测试复杂性**: 使用 `@pytest.mark.asyncio` 确保事件循环正确管理
3. **Checkpoint 路径依赖**: 使用临时目录避免污染真实 workspace
4. **Import 循环**: 避免在 conftest 中直接导入核心模块，使用延迟导入

## 7. 实现计划

1. 创建 `tests/integration/conftest.py` — 共享 fixtures
2. 创建 `tests/integration/roles/kernel/test_transaction_kernel_e2e.py` — 7 个测试
3. 创建 `tests/integration/roles/runtime/test_session_orchestrator_e2e.py` — 10 个测试
4. 创建 `tests/integration/context/test_contextos_workflow_e2e.py` — 6 个测试
5. 运行验证门禁，确保所有测试通过
