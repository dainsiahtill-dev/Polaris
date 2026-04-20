# Team Theta: kernel.py 重构蓝图

## 目标文件
`polaris/cells/roles/kernel/internal/kernel.py` (1761行)

## 架构分析

### 当前问题
1. **RoleExecutionKernel 承担过多职责**: run/run_stream/工具执行/错误处理
2. **错误建议生成内嵌**: `_get_suggestions_for_error` 占用大量空间
3. **与TurnEngine耦合**: 需要进一步解耦

### 拆分方案

```
polaris/cells/roles/kernel/internal/
├── kernel.py                    # Facade (50行)
├── kernel/
│   ├── __init__.py
│   ├── core.py                  # RoleExecutionKernel核心 (400行)
│   ├── error_handler.py         # 错误处理 (300行)
│   ├── suggestions.py           # 错误建议生成 (200行)
│   └── helpers.py               # 辅助函数 (200行)
```

### 核心契约

```python
# core.py
class RoleExecutionKernel:
    """角色执行内核 - 精简版。"""

    __slots__ = (
        '_workspace',
        '_llm_caller',
        '_output_parser',
        '_tool_executor',
        '_turn_engine',
        '_error_handler',
    )

    async def run(
        self,
        request: RoleTurnRequest,
    ) -> RoleTurnResult:
        """执行角色回合 - 委托给TurnEngine。"""
        return await self._turn_engine.run(request)

# error_handler.py
class KernelErrorHandler:
    """内核错误处理器。"""

    def handle(
        self,
        error: Exception,
        context: dict[str, Any],
    ) -> ErrorResolution:
        """处理错误并返回解决方案。"""
        ...

# suggestions.py
class ErrorSuggestionProvider:
    """错误建议提供者。"""

    __slots__ = ('_suggestion_map',)

    def get_suggestions(
        self,
        error_category: str,
    ) -> list[str]:
        """获取错误建议。"""
        ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31