# Tool Sequence Planning & Recovery Protocol Blueprint

## 1. 问题陈述

L3 工具调用矩阵 9 Case 中 5 个失败，根因不是单个 bug，而是**架构层面缺少显式的工具序列规划与验证层**。

当前流程：
```
User Request → Task Contract (NEGATIVE规则) → LLM即兴生成 → Batch Executor运行时检查
```

问题：LLM 即兴生成工具序列，不知道正例模板、不会失败恢复、不会自我验证。

## 2. 目标架构

```
User Request → Task Contract (POSITIVE模板 + 恢复协议) → LLM约束生成 → Batch Executor
                    ↑
            任务类型识别 + 序列模板库
```

## 3. 修复策略（三处联动）

### 3.1 Task Contract Builder — 增加正例序列模板

**新增**：`build_tool_sequence_templates()`

根据 `required_tools` / `ordered_tool_groups` / `min_tool_calls` 识别任务类型，注入标准序列模板：

- **搜索-读取型** (glob → read_file, repo_rg → read_file)：
  ```
  TEMPLATE [Search-Then-Read]: Step 1: glob/repo_rg 定位文件 → Step 2: read_file 读取内容
  ```
- **编辑-验证型** (edit → read_verify)：
  ```
  TEMPLATE [Edit-Then-Verify]: Step 1: read_file 确认内容 → Step 2: precision_edit/edit_blocks 修改
  → Step 3: read_file 验证修改
  ```
- **多次读取型** (idempotent read × N)：
  ```
  TEMPLATE [Repeat-Read]: 用户要求读取 N 次。你必须真的调用 read_file 共 N 次。
  每次读取后报告结果，不要在一次读取后就认为任务完成。
  ```
- **搜索-替换型** (search → replace)：
  ```
  TEMPLATE [Search-Replace]: Step 1: repo_rg 搜索定位 → Step 2: read_file 读取目标文件
  → Step 3: search_replace/precision_edit 执行替换
  ```

**新增**：`build_failure_recovery_protocol()`

```
RECOVERY PROTOCOL:
1. 如果 edit_blocks/precision_edit/search_replace 因 "no match" 失败：
   → 立即 read_file 读取目标文件 → 复制精确内容 → 换 precision_edit 或 append_to_file 重试
2. 如果 glob/repo_rg 返回结果后未继续：
   → 检查 ordered_tool_groups，确保后续工具已调用
3. 任何工具失败后：
   → 不允许直接返回文本完成 → 必须尝试替代工具或读取验证
```

### 3.2 Failure Budget — 增强 edit 失败后的恢复引导

修改 `_escalate_suggestion()`：
- `no_match` 错误：增加**降级路径**（edit_blocks失败 → precision_edit → append_to_file）
- 第一次失败就给出强引导，不要等到 ESCALATE 阈值

### 3.3 Circuit Breaker — 豁免任务要求的重复读取

修改 `ProgressiveCircuitBreaker.evaluate()`：
- 接受 `expected_read_count` 参数
- 当 `expected_read_count > 1` 且当前 read-only streak ≤ expected_read_count 时，不触发 read-only stagnation
- 通过 Task Contract Builder 传递 `min_tool_calls` 作为 `expected_read_count`

## 4. 实施范围

### 修改文件
1. `polaris/cells/roles/kernel/internal/transaction/task_contract_builder.py`
2. `polaris/kernelone/tool_execution/failure_budget.py`
3. `polaris/cells/roles/kernel/internal/circuit_breaker.py`

### 新增文件
4. `polaris/cells/roles/kernel/internal/transaction/tool_sequence_templates.py`

## 5. 验证计划

1. 单元测试：task_contract_builder 生成正确模板
2. 集成测试：运行 L3 矩阵 9 Case
3. 目标：9/9 PASS

## 6. 风险

- Prompt 长度增加可能影响模型表现（需监控 token 使用）
- 正例模板可能与某些 edge case 冲突（需保留原有 NEGATIVE 规则）
