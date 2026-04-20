# 工具失败自愈与预算架构蓝图

**日期**: 2026-04-10
**状态**: 实现中
**优先级**: P0
**根因**: Director LLM 调用 `precision_edit` 85次失败但无自愈

---

## 1. 问题背景

### 1.1 现象

JSONL 日志显示 Director LLM 调用 `precision_edit` 工具 **85次连续失败**，每次失败后 LLM 继续重试相同搜索字符串，未触发任何自愈机制。

### 1.2 根本原因

当前架构存在三个关键缺陷：

| 缺陷 | 描述 | 影响 |
|------|------|------|
| **stall_count 太严格** | 只追踪 exact signature match | `precision_edit` 每次搜索字符串略有不同就被认为是不同调用 |
| **tool_errors_count = 0** | 失败计数从未被更新 | 无法检测"同一工具反复失败"模式 |
| **SelfHealingExecutor 断开** | 存在但未连接到工具执行路径 | 异常时无自愈尝试 |

### 1.3 现有防护措施

- ✅ `max_turns` 硬限制（64次迭代后强制停止）
- ✅ SuggestionBuilder 错误建议系统（FuzzyMatchBuilder, ExplorationBuilder）
- ❌ 无错误模式聚类检测
- ❌ 无失败预算控制
- ❌ 无 SelfHealingExecutor 集成

---

## 2. 架构设计

### 2.1 设计原则

```
工具执行路径：
ToolRequest → ErrorClassifier → FailureTracker → RetryBudget → SelfHealingExecutor → LLM Context Injection
```

**核心洞察**：不要依赖 exact signature 检测循环，要依赖 **error pattern 聚类**。

### 2.2 组件架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Turn Engine                               │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  Turn Budget │  │  Stall Guard │  │  Failure Budget Guard  │  │
│  │  (max_turns) │  │(exact sig)   │  │ (error pattern聚类)    │  │
│  └──────┬───────┘  └──────────────┘  └───────────┬────────────┘  │
│         │                                       │               │
│         │                        ┌──────────────┴────────────┐  │
│         │                        │   Error Pattern Tracker   │  │
│         │                        │   - tool_errors_count    │  │
│         │                        │   - error_type classification│
│         │                        │   - pattern聚类           │  │
│         │                        └──────────────┬────────────┘  │
│         │                                       │               │
│         │                        ┌──────────────┴────────────┐  │
│         │                        │   SelfHealingExecutor     │  │
│         │                        │   - strategy selection    │  │
│         │                        │   - fix injection        │  │
│         │                        └──────────────┬────────────┘  │
│         │                                       │               │
│         └─────────────────┬─────────────────────┘               │
│                           │                                     │
│                    ┌──────▼───────┐                             │
│                    │ Tool Executor │◄─── Error Pattern Guard    │
│                    └──────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件实现

### 3.1 错误模式分类器

**文件**: `polaris/kernelone/tool_execution/error_classifier.py` (新建)

```python
"""Error Pattern Classifier - 错误模式识别模块。

将具体错误归类为错误模式，用于识别同类错误反复出现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class ToolErrorPattern:
    """错误模式 - 用于识别同类错误"""
    tool_name: str
    error_type: str          # "not_found", "permission", "timeout", "syntax", "invalid_arg"
    error_signature: str     # 泛化的错误签名
    frequency: int = 0


class ToolErrorClassifier:
    """将具体错误分类为错误模式"""
    
    # 错误类型关键词映射
    _ERROR_TYPE_KEYWORDS: ClassVar[dict[str, tuple[str, ...]]] = {
        "not_found": ("not found", "does not exist", "no such file", "not exist"),
        "permission": ("permission", "denied", "unauthorized", "forbidden"),
        "timeout": ("timeout", "timed out", "deadline"),
        "syntax": ("syntax", "parse error", "invalid syntax", "malformed"),
        "invalid_arg": ("invalid", "illegal", "illegal argument", "wrong type"),
        "no_match": ("no matches found", "no match", "search not found"),
        "encoding": ("encoding", "decode error", "utf-8"),
    }
    
    # 泛化模式：移除具体数值
    _GENERALIZE_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        (re.compile(r'line \d+', re.IGNORECASE), "line N"),
        (re.compile(r'col \d+', re.IGNORECASE), "col N"),
        (re.compile(r'0x[0-9a-f]+', re.IGNORECASE), "HEXADDR"),
        (re.compile(r'\d{3,}'), "N"),
    ]
    
    def classify(self, tool_name: str, error: str | Exception, **context) -> ToolErrorPattern:
        """将错误分类为错误模式。
        
        Args:
            tool_name: 工具名称
            error: 错误字符串或异常对象
            **context: 额外上下文
            
        Returns:
            ToolErrorPattern 实例
        """
        error_msg = str(error).lower() if error else ""
        
        # 泛化错误消息
        generalized = error_msg
        for pattern, replacement in self._GENERALIZE_PATTERNS:
            generalized = pattern.sub(replacement, generalized)
        
        # 确定错误类型
        error_type = self._determine_error_type(error_msg)
        
        # 生成错误签名
        signature = f"{tool_name}:{error_type}:{generalized[:80]}"
        
        return ToolErrorPattern(
            tool_name=tool_name,
            error_type=error_type,
            error_signature=signature,
            frequency=0,
        )
    
    def _determine_error_type(self, error_msg: str) -> str:
        """根据错误消息确定错误类型"""
        for error_type, keywords in self._ERROR_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in error_msg:
                    return error_type
        return "unknown"
```

### 3.2 失败预算控制器

**文件**: `polaris/kernelone/tool_execution/failure_budget.py` (新建)

```python
"""Failure Budget Controller - 失败预算控制器。

为每个工具和错误模式设置失败预算，防止无限循环调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import ClassVar

from polaris.kernelone.tool_execution.error_classifier import ToolErrorPattern, ToolErrorClassifier

logger = logging.getLogger(__name__)


class FailureDecision:
    """失败决策结果"""
    
    ALLOW = "ALLOW"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


@dataclass
class FailureBudget:
    """每个工具的错误预算"""
    
    max_failures_per_tool: ClassVar[int] = 3
    max_same_pattern: ClassVar[int] = 2
    max_total_per_turn: ClassVar[int] = 10
    
    _tool_failures: dict[str, int] = field(default_factory=lambda: {})
    _pattern_failures: dict[str, int] = field(default_factory=lambda: {})
    _total_failures: int = 0
    _classifier: ToolErrorClassifier = field(default_factory=ToolErrorClassifier)
    
    def record_failure(self, pattern: ToolErrorPattern) -> tuple[str, str | None]:
        """记录失败并返回决策。
        
        Returns:
            (decision, suggestion) 元组
        """
        tool_key = pattern.tool_name
        pattern_key = pattern.error_signature
        
        self._tool_failures[tool_key] = self._tool_failures.get(tool_key, 0) + 1
        self._pattern_failures[pattern_key] = self._pattern_failures.get(pattern_key, 0) + 1
        self._total_failures += 1
        
        tool_count = self._tool_failures[tool_key]
        pattern_count = self._pattern_failures[pattern_key]
        
        # BLOCK: 工具超过预算
        if tool_count > self.max_failures_per_tool:
            logger.warning(
                "[FailureBudget] BLOCK tool=%s (failures=%d, pattern=%s)",
                tool_key, tool_count, pattern_key
            )
            return FailureDecision.BLOCK, self._block_suggestion(pattern)
        
        # ESCALATE: 同类错误模式重复
        if pattern_count > self.max_same_pattern:
            logger.warning(
                "[FailureBudget] ESCALATE pattern=%s (count=%d)",
                pattern_key, pattern_count
            )
            return FailureDecision.ESCALATE, self._escalate_suggestion(pattern)
        
        # BLOCK: 总失败超过预算
        if self._total_failures > self.max_total_per_turn:
            logger.warning("[FailureBudget] BLOCK - total failures exhausted")
            return FailureDecision.BLOCK, "Total failure budget exhausted. Stop and report to user."
        
        return FailureDecision.ALLOW, None
    
    def _escalate_suggestion(self, pattern: ToolErrorPattern) -> str:
        """同类错误反复出现的升级建议"""
        suggestions = {
            "no_match": (
                f"Tool '{pattern.tool_name}' failing repeatedly with 'no match' errors. "
                "Consider: (1) Verify target exists with read_file, "
                "(2) Check search string format, (3) Try alternative tool, "
                "(4) Ask user for clarification"
            ),
            "not_found": (
                f"Tool '{pattern.tool_name}' failing with 'not found'. "
                "Try: glob() or repo_tree() to explore workspace structure first."
            ),
            "invalid_arg": (
                f"Tool '{pattern.tool_name}' has invalid arguments. "
                "Check tool signature and parameter types before retrying."
            ),
        }
        return suggestions.get(pattern.error_type, 
            f"Tool '{pattern.tool_name}' failing repeatedly. Consider alternative approach.")
    
    def _block_suggestion(self, pattern: ToolErrorPattern) -> str:
        """阻止工具的建议"""
        return (
            f"Tool '{pattern.tool_name}' blocked after {self.max_failures_per_tool} failures. "
            f"Stop attempting this tool and inform user of persistent failure."
        )
    
    def get_tool_failure_count(self, tool_name: str) -> int:
        """获取工具失败次数"""
        return self._tool_failures.get(tool_name, 0)
    
    def get_pattern_failure_count(self, pattern_key: str) -> int:
        """获取错误模式失败次数"""
        return self._pattern_failures.get(pattern_key, 0)
    
    def reset(self) -> None:
        """重置所有计数器"""
        self._tool_failures.clear()
        self._pattern_failures.clear()
        self._total_failures = 0
```

### 3.3 工具执行器集成

**修改文件**: `polaris/kernelone/llm/toolkit/executor/core.py`

在 `AgentAccelToolExecutor.execute()` 中集成错误模式追踪和失败预算：

```python
# 在 __init__ 中添加
self._error_classifier = ToolErrorClassifier()
self._failure_budget = FailureBudget()

# 在 execute() 方法中添加错误追踪
try:
    result = handler(self, **normalized_arguments)
    # ... 现有成功处理逻辑
except Exception as e:
    # 错误分类
    error_pattern = self._error_classifier.classify(
        canonical_tool_name, e
    )
    
    # 检查失败预算
    decision, suggestion = self._failure_budget.record_failure(error_pattern)
    
    if decision == FailureDecision.BLOCK:
        return {
            "ok": False,
            "error": suggestion or f"Tool {canonical_tool_name} blocked due to repeated failures",
            "tool": canonical_tool_name,
            "blocked": True,
            "failure_count": self._failure_budget.get_tool_failure_count(canonical_tool_name),
        }
    
    # 即使不阻止，也附加升级建议
    error_message = str(e)
    
    # 尝试自愈
    healing_result = self._try_self_healing(
        tool_name=canonical_tool_name,
        error=e,
        attempt=self._failure_budget.get_tool_failure_count(canonical_tool_name)
    )
    
    if healing_result:
        return healing_result
    
    return {
        "ok": False,
        "error": error_message,
        "tool": canonical_tool_name,
        "suggestion": suggestion or self._generate_fallback_suggestion(error_pattern),
    }
```

### 3.4 自愈执行器集成

**修改文件**: `polaris/kernelone/tool_execution/self_healing_integration.py` (新建)

```python
"""Self-Healing Integration - 自愈执行器与工具执行路径的集成。

将 SelfHealingExecutor 连接到工具执行流程。
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.resilience.self_healing import (
    AlternativeStrategy,
    HealingResult,
    RetryStrategy,
    SelfHealingExecutor,
)

logger = logging.getLogger(__name__)


class ToolSelfHealer:
    """工具执行自愈器"""
    
    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._retry_strategy = RetryStrategy(
            max_attempts=2,  # 限制重试次数
            base_delay=0.5,
            exponential_base=2.0,
            max_delay=5.0,
        )
    
    async def try_recover(
        self,
        tool_name: str,
        error: Exception,
        attempt: int,
        handler: Any,
        **normalized_arguments: Any,
    ) -> dict[str, Any] | None:
        """尝试自愈恢复。
        
        Args:
            tool_name: 工具名称
            error: 发生的错误
            attempt: 当前尝试次数
            handler: 工具处理器
            **normalized_arguments: 工具参数
            
        Returns:
            恢复后的结果，或 None 表示无法恢复
        """
        if attempt >= 2:
            # 超过自愈尝试次数
            return None
        
        # 根据错误类型尝试不同的修复策略
       修复策略 = self._select_repair_strategy(tool_name, error)
        
        if not 修复策略:
            return None
        
        try:
            result = await self._execute_with_strategy(
                handler=handler,
                strategy=修复策略,
                **normalized_arguments,
            )
            if result and result.get("ok"):
                logger.info(
                    "[ToolSelfHealer] Recovered %s using strategy %s",
                    tool_name, 修复策略.get("name")
                )
                result["healed"] = True
                result["healing_strategy"] = 修复策略.get("name")
                return result
        except Exception as e:
            logger.debug("[ToolSelfHealer] Repair strategy %s failed: %s",
                        修复策略.get("name"), e)
        
        return None
    
    def _select_repair_strategy(self, tool_name: str, error: Exception) -> dict[str, Any] | None:
        """根据错误类型选择修复策略"""
        error_msg = str(error).lower()
        
        # 编码错误 - 尝试不同编码
        if "encoding" in error_msg or "decode" in error_msg:
            return {
                "name": "encoding_fallback",
                "action": "retry_with_encoding",
                "encoding": "utf-8",
                "errors": "replace",
            }
        
        # 路径错误 - 尝试标准化路径
        if "not found" in error_msg or "no such file" in error_msg:
            return {
                "name": "path_normalization",
                "action": "normalize_and_retry",
            }
        
        # 参数错误 - 提供更清晰的错误
        if "invalid" in error_msg or "missing" in error_msg:
            return {
                "name": "argument_validation",
                "action": "validate_and_suggest",
            }
        
        return None
    
    async def _execute_with_strategy(
        self,
        handler: Any,
        strategy: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """使用指定策略执行"""
        action = strategy.get("action")
        
        if action == "retry_with_encoding":
            # 重新执行（调用者会处理编码）
            return await handler(**kwargs)
        
        return None
```

---

## 4. 与现有 SuggestionBuilder 的集成

### 4.1 集成架构

```
┌─────────────────────────────────────────────────────────────┐
│                    ToolExecutor.execute()                    │
│                          │                                  │
│            ┌──────────────▼──────────────┐                   │
│            │   Error Pattern Classifier │                   │
│            └──────────────┬──────────────┘                   │
│                          │                                  │
│            ┌──────────────▼──────────────┐                   │
│            │     Failure Budget Guard    │                   │
│            │  ALLOW / ESCALATE / BLOCK  │                   │
│            └──────────────┬──────────────┘                   │
│                          │                                  │
│         ┌────────────────┼────────────────┐               │
│         │                │                │                  │
│         ▼                ▼                ▼                  │
│   ┌───────────┐  ┌─────────────┐  ┌─────────────┐          │
│   │  Execute  │  │ Self-Healer │  │  Suggestion │          │
│   │  (ALLOW)  │  │ (ESCALATE)  │  │  Builder    │          │
│   └───────────┘  └─────────────┘  │  (BLOCK)    │          │
│                                   └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 SuggestionBuilder 优先级调整

现有 `FuzzyMatchBuilder` (priority=10) 和 `ExplorationBuilder` (priority=20) 保持不变。
新增的 `FailureEscalationBuilder` (priority=5) 在它们之前处理。

---

## 5. 实施计划

### Phase 1: 错误模式分类器 (立即)
- [ ] 创建 `error_classifier.py`
- [ ] 实现 `ToolErrorClassifier.classify()`
- [ ] 单元测试

### Phase 2: 失败预算控制器 (立即)
- [ ] 创建 `failure_budget.py`
- [ ] 实现 `FailureBudget.record_failure()`
- [ ] 实现决策逻辑 (ALLOW/ESCALATE/BLOCK)
- [ ] 单元测试

### Phase 3: 工具执行器集成 (短期)
- [ ] 在 `AgentAccelToolExecutor.__init__` 中初始化 `_error_classifier` 和 `_failure_budget`
- [ ] 在 `execute()` 方法中添加错误追踪和预算检查
- [ ] 集成测试

### Phase 4: 自愈执行器集成 (中期)
- [ ] 创建 `self_healing_integration.py`
- [ ] 实现 `ToolSelfHealer.try_recover()`
- [ ] 连接 `SelfHealingExecutor`
- [ ] 集成测试

### Phase 5: 验证与调优 (后期)
- [ ] 使用 JSONL 日志回放验证修复
- [ ] 调整预算参数
- [ ] 性能测试

---

## 6. 关键文件清单

| 文件 | 操作 |
|------|------|
| `polaris/kernelone/tool_execution/error_classifier.py` | 新建 |
| `polaris/kernelone/tool_execution/failure_budget.py` | 新建 |
| `polaris/kernelone/tool_execution/self_healing_integration.py` | 新建 |
| `polaris/kernelone/llm/toolkit/executor/core.py` | 修改 - 集成错误追踪 |
| `polaris/kernelone/tool_execution/suggestions/failure_escalation.py` | 新建 - FailureEscalationBuilder |

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 预算阈值过严 | 正常工具调用被阻止 | 可配置参数，默认保守值 |
| 错误分类不准确 | 自愈策略选错 | 保持 ALLOW 默认，仅对明确模式升级 |
| 性能开销 | 每次错误增加分类开销 | 缓存错误模式，复用已分类结果 |

---

## 8. 验收标准

1. **85次失败场景被阻止**: `precision_edit` 连续失败3次后被 BLOCK
2. **Suggestion 仍然有效**: FuzzyMatchBuilder 和 ExplorationBuilder 继续工作
3. **无回归**: 现有工具调用不受影响（budget 默认值足够宽松）
4. **可观测性**: 日志清晰显示决策过程和失败计数

---

## 9. 统一错误处理架构 v2 (2026-04-10)

### 9.1 问题背景

之前设计的 Phase 1-6 只解决了 **Turn 层面的失败阻止**，但存在两个问题：

1. **两套独立错误处理**：Turn 用 `FailureBudget`，Workflow 用 `RetryPolicy`，语义不一致
2. **信息丢失**：Workflow 不知道 Turn 里面发生了什么错误类型

### 9.2 核心理解

```
Workflow Engine
    │
    ├── Task 1 (Director 执行一个子任务)
    │       │
    │       └── 1 Turn = CognitiveOrchestrator.process(task_input)
    │               │
    │               └── 工具调用循环
    │                       │
    │                       └── FailureBudget (追踪失败)
    │                               │
    │                               ├── 3次同类失败 → BLOCK
    │                               └── 返回: {error_type, retryable, suggestion}
    │
    └── Workflow 根据 error_type 决定下一步
```

**关键洞察**：
- **每个 Task = 1 个 Turn**
- **Chat 模式只是 Workflow 的特例**（只有 1 个 Task）
- **Turn 是 Workflow 中 Role 执行 Task 的机制**

### 9.3 统一错误分类

所有层共享同一个错误分类语义：

```python
class ErrorCategory(Enum):
    """统一错误分类"""
    
    # 可恢复错误 - Workflow 可以重试
    TRANSIENT = "transient"        # 网络抖动、临时不可用
    RESOURCE = "resource"          # 资源暂时耗尽
    
    # 不可恢复错误 - 需要修正输入
    VALIDATION = "validation"      # 参数错误、格式问题
    NOT_FOUND = "not_found"        # 目标不存在
    
    # 不可恢复错误 - 需要改变策略
    PERMISSION = "permission"      # 权限问题
    LOGIC = "logic"               # 业务逻辑错误
    
    # 特殊
    UNKNOWN = "unknown"
```

### 9.4 Turn 结果结构

```python
@dataclass
class TurnResult:
    """Turn 执行结果 - 携带完整错误上下文供 Workflow 使用"""
    
    ok: bool
    error: str | None = None
    
    # 关键：统一错误分类
    error_type: ErrorCategory | None = None
    
    # 关键：是否可重试（Workflow 据此决策）
    retryable: bool = False
    
    # 被阻止的工具列表（Turn 层面阻止的工具）
    blocked_tools: list[str] = field(default_factory=list)
    
    # 修复建议
    suggestion: str | None = None
    
    # Workflow 应该采取的动作
    workflow_action: str | None = None  # "RETRY" / "CHANGE_STRATEGY" / "FAIL_FAST"
```

### 9.5 Workflow 决策逻辑

```python
class WorkflowEngine:
    async def _execute_task(self, task: TaskSpec) -> TaskResult:
        for attempt in range(task.retry_policy.max_attempts + 1):
            turn_result = await role.execute_task(task)
            
            if turn_result.ok:
                return TaskResult.ok(turn_result)
            
            # 根据 error_type 决定
            if not turn_result.retryable:
                # VALIDATION / NOT_FOUND / PERMISSION / LOGIC
                return TaskResult.fail(
                    turn_result.error,
                    error_type=turn_result.error_type,
                    action="DO_NOT_RETRY",
                )
            
            if turn_result.blocked_tools:
                # 工具被阻止，换策略
                return TaskResult.fail(
                    turn_result.error,
                    error_type=turn_result.error_type,
                    action="CHANGE_STRATEGY",
                )
            
            # TRANSIENT / RESOURCE - 可以重试
            await asyncio.sleep(calculate_backoff(attempt))
        
        return TaskResult.fail("max attempts exceeded")
```

### 9.6 实施计划

| 阶段 | 任务 | 文件 |
|------|------|------|
| **Phase 7** | 更新 `FailureBudget` 返回 `error_type` 和 `retryable` | `failure_budget.py` |
| **Phase 8** | 定义 `TurnResult` dataclass | `orchestrator.py` |
| **Phase 9** | 更新 `process()` 返回 TurnResult | `orchestrator.py` |
| **Phase 10** | 更新 Workflow Engine 决策 | `workflow/engine.py` |

### 9.7 关键文件改动

| 文件 | 改动 |
|------|------|
| `polaris/kernelone/tool_execution/failure_budget.py` | 返回结果包含 `error_type` 和 `retryable` |
| `polaris/kernelone/cognitive/orchestrator.py` | `process()` 返回 `TurnResult` 结构 |
| `polaris/kernelone/workflow/engine.py` | 使用 `error_type` 而非纯次数做决策 |

### 9.8 验收标准

1. **Turn 结果携带 error_type**：Workflow 能理解发生了什么错误
2. **Workflow 决策基于 error_type**：不再盲目重试
3. **两套机制语义统一**：Turn 和 Workflow 使用相同的错误分类
4. **无回归**：现有功能不受影响

