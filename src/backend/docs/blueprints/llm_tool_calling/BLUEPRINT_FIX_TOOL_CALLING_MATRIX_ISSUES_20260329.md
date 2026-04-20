# Blueprint: 修复 Tool Calling Matrix 问题

**日期**: 2026-03-29
**状态**: 已分配
**优先级**: P0/P1/P2
**执行团队**: Python 架构与代码治理实验室
**Workspace**: `C:/Temp/BenchmarkTest`

---

## 执行摘要

| 问题 | 严重程度 | 估计工时 | 状态 |
|------|----------|----------|------|
| P0: `repo_apply_diff` 参数验证循环 | 🔴 阻塞 | 4h | 待修复 |
| P1: `repo_rg` Cooldown 过于严格 | 🟠 严重 | 2h | 待修复 |
| P2: LLM Lifecycle 资源泄漏 | 🟡 警告 | 6h | 待修复 |

---

## P0: `repo_apply_diff` 参数验证循环

### 问题描述

工具规格定义 `diff` 为必需参数，但 LLM 调用时使用 `patch` 别名或未提供正确参数名。工具处理逻辑未处理别名映射。

### 浅层根因 (Code-Level)

1. `contracts.py` 中 `repo_apply_diff` 的参数定义为 `diff`
2. `tool_normalization.py` 中缺少 `diff` ↔ `patch` 的参数别名映射
3. PolicyLayer 拦截后返回错误，但未做递归修复

### 深层根因 (LLM-System Interaction)

**补充自 Python 架构与代码治理实验室深度评估 (2026-03-29)**

1. **错误反馈机制失效** → 导致无限死循环
   - PolicyLayer 拦截后返回的错误信息格式不明确
   - 若返回 `"Error: Missing required parameter 'diff'"` 而非 `"Received unexpected parameter 'patch'"`
   - LLM 无法理解 `patch` 就是 `diff` 的别名，只能盲目重试

2. **Tool Prompt 描述不清**
   - 工具规格（ToolSpec）的 `required_doc` 可能没有足够强调 `diff` 是唯一合法键名
   - LLM 产生幻觉，将 `diff` 和 `patch` 视为等价物

3. **错误隔离 (Error Masking)**
   - PolicyLayer 抛出的异常可能未被正确格式化为 LLM 可理解的 `system/tool_response` 消息
   - 被外层框架捕获并转换成通用错误
   - LLM "不知道自己错在哪"，只能重试

### 修复方案

**Step 1**: 在 `tool_normalization.py` 添加参数别名映射

```python
# polaris/kernelone/llm/toolkit/tool_normalization.py
# 在 repo_apply_diff 相关处理逻辑中添加
if tool_name == "repo_apply_diff":
    if "patch" in normalized and "diff" not in normalized:
        normalized["diff"] = normalized.pop("patch")
```

**Step 2**: 增强错误消息的可操作性

```python
# 返回给 LLM 的错误消息必须包含：
# 1. 收到的参数是什么
# 2. 期望的参数是什么
# 3. 如何修正的建议
f"Parameter validation failed: Received 'patch' but 'diff' is required. "
f"Hint: rename 'patch' to 'diff' in your next call."
```

**Step 3**: 验证 `ToolSpecRegistry` 返回的 `arg_aliases` 正确传递

### 验收标准

- [ ] Case 7 (`l3_file_edit_sequence`) 不再卡住
- [ ] `repo_apply_diff` 接受 `patch` 参数并正确映射到 `diff`
- [ ] 错误消息包含可操作的修正提示
- [ ] pytest 通过

---

## P1: `repo_rg` Cooldown 过于严格

### 问题描述

TurnEngine 的 PolicyLayer 对 `repo_rg` 设置了 8 次调用上限，但 L1/L2 测试用例需要多次调用搜索工具验证结果。

### 浅层根因 (Code-Level)

- `PolicyLayer` 的 budget 配置写死为 8
- Benchmark 测试场景需要 >8 次调用
- 配置与实际需求不匹配

### 深层根因 (LLM Behavior)

**补充自 Python 架构与代码治理实验室深度评估 (2026-03-29)**

1. **盲目搜索陷阱 (Blind Search Trap)**
   - 提高 Budget 能够让 Benchmark 通过，但这可能掩盖了 LLM 搜索策略低下的问题
   - 如果 LLM 因为无法准确定位文件而反复进行大范围、宽泛的正则搜索
   - 系统资源开销会剧增

2. **搜索上下文聚合缺失**
   - LLM 可能在前几次 `repo_rg` 中没有获得足够结构化的结果（例如返回结果被截断）
   - 导致它不得不多次缩小范围重试

3. **Budget 阈值调整风险**
   - 提高 Budget 到 20 时需警惕可能引入新的 P2 级性能问题
   - API Token 消耗过大或超时风险增加

### 修复方案

**Step 1**: 检查 `policy_layer.py` 中的 budget 配置

```python
# polaris/cells/roles/kernel/internal/policy_layer.py
TOOL_BUDGETS: dict[str, int] = {
    "repo_rg": 8,  # 当前值 - 需调整为 20
}
```

**Step 2**: 将硬编码改为从 `BudgetConfig` 读取

**Step 3**: 审计 LLM 的搜索策略

```python
# 添加搜索效率指标日志
logger.info(
    "[TurnEngine] repo_rg call #%d, pattern=%s, results=%d, truncated=%s",
    call_count, pattern, result_count, was_truncated
)
```

**Step 4**: 为 benchmark 场景提供独立的 budget 配置（不应用于生产）

### 验收标准

- [ ] Case 2 (`l1_grep_search`) 通过，score ≥ 80
- [ ] Cooldown 不再拦截合法的多次调用
- [ ] 记录搜索效率指标供后续优化
- [ ] pytest 通过

---

## P2: LLM Lifecycle 资源泄漏

### 问题描述

多个 LLM 调用未正确关闭，Executor 或 Provider 资源未释放。

### 浅层根因 (Code-Level)

- `LLM lifecycle appears unclosed (run_id=llm_director_xxx, age=821.53s)`
- Provider 或 Executor 的 `__aexit__` 未被调用
- 可能发生在异常路径

### 深层根因 (Asyncio Architecture)

**补充自 Python 架构与代码治理实验室深度评估 (2026-03-29)**

1. **asyncio.CancelledError 吞没**
   - 当客户端断开连接或外层设置 Timeout 时，底层协程会被注入 `CancelledError`
   - 如果 Executor 内部有 `except Exception:` 却没有单独处理 `asyncio.CancelledError`
   - 或在捕获后没有向上抛出，就会导致清理逻辑被跳过

2. **悬挂的后台任务 (Hanging Background Tasks)**
   - 在 LLM 流式输出（Streaming）过程中使用 `asyncio.create_task()` 创建了后台日志或监控任务
   - 主任务退出时没有显式地 `await` 或 `cancel` 这些子任务
   - 垃圾回收器（GC）无法回收会话资源（如 `aiohttp.ClientSession`）

3. **13+ 分钟的生命周期泄漏**
   - `age=821.53s` 表明会话持续了约 13.7 分钟
   - 这种级别的泄漏通常与任务取消链路中断有关

### 修复方案

**Step 1**: 审计 `llm_toolkit/executor.py` 中的上下文管理器实现

```python
# 检查是否有类似这样的问题代码
async def __aexit__(self, exc_type, exc_val, exc_tb):
    if exc_type is asyncio.CancelledError:
        # 必须重新抛出 CancelledError，不能吞没
        raise
    await self.cleanup()
    return False  # 不吞异常
```

**Step 2**: 确保所有 `async with` 路径覆盖 `CancelledError`

**Step 3**: 取消所有悬挂的后台任务

```python
# 在清理逻辑中
for task in self._background_tasks:
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
self._background_tasks.clear()
```

**Step 4**: 添加资源泄漏检测日志

```python
# polaris/kernelone/llm/providers/base.py
async def __aexit__(self, exc_type, exc_val, exc_tb):
    # 确保资源释放
    await self.cleanup()
    # 记录资源释放日志
    logger.info("[LLM Provider] Cleanup completed for run_id=%s", self.run_id)
    return False  # 不吞异常
```

### 验收标准

- [ ] 无 `LLM lifecycle unclosed` 警告
- [ ] 长时间运行无 OOM
- [ ] `asyncio.CancelledError` 被正确传播而非吞没
- [ ] pytest 通过

---

## 实施计划

| Phase | 任务 | 负责人 | 截止日期 |
|-------|------|--------|----------|
| 1 | 修复 P0 `repo_apply_diff` 别名映射 + 增强错误消息 | TBD | 2026-03-30 |
| 2 | 修复 P1 `repo_rg` cooldown 配置 + 搜索效率审计 | TBD | 2026-03-30 |
| 3 | 修复 P2 LLM 资源泄漏 (CancelledError + 后台任务) | TBD | 2026-03-31 |
| 4 | 重新运行 benchmark 验证 | TBD | 2026-03-31 |

---

## 相关文件

- `polaris/kernelone/llm/toolkit/tool_normalization.py`
- `polaris/kernelone/tools/contracts.py`
- `polaris/cells/roles/kernel/internal/policy_layer.py`
- `polaris/kernelone/llm/providers/base.py`
- `polaris/kernelone/llm/toolkit/executor.py`

---

## 附录

### 完整测试结果 (2026-03-29)

| # | Case ID | Level | 状态 | 分数 | 耗时(ms) |
|---|---------|-------|------|------|----------|
| 1 | `l1_directory_listing` | L1 | ✅ PASS | 100.0 | 6,328 |
| 2 | `l1_grep_search` | L1 | ❌ FAIL | 55.0 | 14,149 |
| 3 | `l1_read_tail` | L1 | ✅ PASS | 90.0 | 6,359 |
| 4 | `l1_single_tool_accuracy` | L1 | ✅ PASS | 100.0 | 9,081 |
| 5 | `l2_complex_types_enum` | L2 | ✅ PASS | 94.17 | 8,413 |
| 6 | `l2_multi_file_read` | L2 | ✅ PASS | 85.0 | 4,471 |
| 7 | `l3_file_edit_sequence` | L3 | 🔄 卡住 | - | >300,000 |

### Benchmark 运行信息

- **运行ID**: `de44da70`
- **测试套件**: `tool_calling_matrix`
- **角色**: `director`
- **运行时长**: >15 分钟
- **卡住原因**: 无限重试循环

### Python 架构与代码治理实验室 (10人) 深度评估摘要

| 问题 | 浅层根因 | 深层根因 |
|------|----------|----------|
| P0 | 参数别名映射缺失 | 错误反馈机制失效导致 LLM 盲目重试 |
| P1 | Budget 硬编码 8 | 盲目搜索陷阱 + 搜索上下文聚合缺失 |
| P2 | `__aexit__` 未调用 | `CancelledError` 吞没 + 悬挂后台任务 |

---

**分配**: Python 架构与代码治理实验室 (10人)
**完成目标**: 2026-03-31 前所有 P0/P1 问题修复并验证
