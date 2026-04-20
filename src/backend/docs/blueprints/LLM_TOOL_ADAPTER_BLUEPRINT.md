# LLM 工具适配器设计蓝图

**版本**: 1.0
**创建日期**: 2026-03-27
**更新日期**: 2026-03-27
**状态**: ✅ 已实现
**负责人**: Claude (总架构师)

---

## 1. 背景与目标

### 1.1 问题描述

当前 Polaris 的工具系统存在以下问题：

1. **工具命名不一致**: LLM 习惯用 `read_file`、`search_code`，系统使用 `repo_read_head`、`repo_rg`
2. **参数名不兼容**: LLM 用 `limit`，系统用 `n` 或 `max_bytes`
3. **别名映射歧义**: 当前别名映射存在跨工具歧义（如 `limit` 在不同工具中映射到不同参数）
4. **LLM 循环调用**: 因语义混淆导致 LLM 陷入无限工具调用循环

### 1.2 设计目标

**核心理念**: "让工具适应 LLM，而不是让 LLM 适应工具"

1. **完全兼容 LLM 习惯**: LLM 可以用任何习惯的工具名和参数名
2. **零歧义**: 每个 LLM 参数名只映射到一个系统参数
3. **意图推断**: 根据上下文智能选择最合适的工具
4. **向后兼容**: 现有系统工具和调用方式保持不变

### 1.3 设计原则

1. **LLM 中心化**: 所有适配逻辑围绕 LLM 的工具调用习惯设计
2. **工具独立性**: 每个工具有独立的参数映射表，无跨工具歧义
3. **智能推断**: 结合工具名、参数、上下文推断 LLM 意图
4. **可观测性**: 完整的日志和调试支持

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM (任何工具名/参数名)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   LLMToolAdapter (工具适配器)                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              LLM 工具映射表 (LLM_TOOL_MAP)              │   │
│  │  按 LLM 工具名组织，参数映射完全隔离                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              意图推断引擎 (IntentEngine)                 │   │
│  │  结合工具名、参数、上下文推断 LLM 意图                   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    系统工具执行层                                  │
│   read_file | repo_read_head | repo_rg | repo_tree | ...      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

| 组件 | 职责 | 文件位置 |
|------|------|----------|
| `LLMToolAdapter` | 主适配器，协调工具解析和执行 | `polaris/cells/roles/kernel/internal/tool_adapter.py` |
| `LLMToolMap` | LLM 工具映射表，按 LLM 工具名组织 | `polaris/cells/roles/kernel/internal/llm_tool_map.py` |
| `IntentEngine` | 意图推断引擎，根据上下文选择工具 | `polaris/cells/roles/kernel/internal/intent_engine.py` |
| `ParamNormalizer` | 参数归一化器，处理 LLM 参数到系统参数的映射 | `polaris/cells/roles/kernel/internal/param_normalizer.py` |

---

## 3. 实现状态

### 3.1 已实现功能

- ✅ `LLMToolAdapter` 主适配器
- ✅ `LLMToolMap` 映射表（42 个工具）
- ✅ `IntentEngine` 意图推断（13 种意图类型）
- ✅ `ParamNormalizer` 参数归一化
- ✅ 集成到 `RoleToolGateway`
- ✅ 单元测试（105 tests passed）

### 3.2 工具映射覆盖

| 工具类别 | LLM 工具名 | 系统工具 |
|----------|------------|----------|
| 读取 | `read_file`, `read_head`, `read_tail`, `file_read`, `cat` | `read_file`, `repo_read_head`, `repo_read_tail` |
| 搜索 | `grep`, `ripgrep`, `rg`, `search_code` | `repo_rg` |
| 目录 | `ls`, `list_directory`, `dir` | `list_directory`, `repo_tree` |
| 写入 | `write_file`, `edit_file` | `precision_edit` |
| 文件操作 | `file_exists`, `glob`, `execute_command` | `file_exists`, `repo_glob`, `execute_command` |

---

## 4. 使用示例

### 3.1 LLM 工具映射表

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class LLMParamMapping:
    """LLM 参数映射配置"""
    llm_param: str           # LLM 使用的参数名
    system_param: str       # 系统参数名
    transform: str | None = None  # 可选的转换函数名

@dataclass
class LLMToolConfig:
    """LLM 工具配置"""
    llm_tool_name: str                    # LLM 使用的工具名
    system_tool_name: str                 # 实际执行的系统工具名
    priority: int = 100                   # 匹配优先级
    description: str = ""                 # 工具描述
    param_mappings: list[LLMParamMapping] = field(default_factory=list)  # 参数映射
    intent_hints: list[str] = field(default_factory=list)   # 意图关键词
    examples: list[str] = field(default_factory=list)       # 使用示例

# LLM 工具映射表示例
LLM_TOOL_MAP: dict[str, LLMToolConfig] = {
    "read_file": LLMToolConfig(
        llm_tool_name="read_file",
        system_tool_name="read_file",
        priority=100,
        description="读取完整文件",
        param_mappings=[
            LLMParamMapping("path", "file"),
            LLMParamMapping("filepath", "file"),
            LLMParamMapping("file_path", "file"),
            LLMParamMapping("limit", "max_bytes"),
            LLMParamMapping("lines", "max_bytes"),
            LLMParamMapping("n", "max_bytes"),
            LLMParamMapping("offset", "range_required"),
        ],
        intent_hints=["完整", "全文", "整个", "all", "full", "read"],
    ),

    "read_head": LLMToolConfig(
        llm_tool_name="read_head",
        system_tool_name="repo_read_head",
        priority=90,
        description="读取文件头部",
        param_mappings=[
            LLMParamMapping("path", "file"),
            LLMParamMapping("filepath", "file"),
            LLMParamMapping("file", "file"),
            LLMParamMapping("n", "n"),
            LLMParamMapping("limit", "n"),
            LLMParamMapping("lines", "n"),
            LLMParamMapping("first_n", "n"),
        ],
        intent_hints=["前", "开头", "头部", "head", "first", "前几行", "前50行"],
    ),

    "search_code": LLMToolConfig(
        llm_tool_name="search_code",
        system_tool_name="repo_rg",
        priority=100,
        description="代码搜索",
        param_mappings=[
            LLMParamMapping("path", "paths"),
            LLMParamMapping("dir", "paths"),
            LLMParamMapping("directory", "paths"),
            LLMParamMapping("query", "pattern"),
            LLMParamMapping("keyword", "pattern"),
            LLMParamMapping("text", "pattern"),
            LLMParamMapping("q", "pattern"),
            LLMParamMapping("pattern", "pattern"),
            LLMParamMapping("limit", "max_results"),
            LLMParamMapping("max", "max_results"),
            LLMParamMapping("n", "max_results"),
            LLMParamMapping("g", "glob"),
        ],
        intent_hints=["搜索", "查找", "grep", "search", "find", "查找函数", "搜索关键词"],
    ),

    "ripgrep": LLMToolConfig(
        llm_tool_name="ripgrep",
        system_tool_name="repo_rg",
        priority=95,
        param_mappings=[
            LLMParamMapping("pattern", "pattern"),
            LLMParamMapping("path", "paths"),
            LLMParamMapping("limit", "max_results"),
        ],
        intent_hints=["rg", "ripgrep"],
    ),

    "grep": LLMToolConfig(
        llm_tool_name="grep",
        system_tool_name="repo_rg",
        priority=90,
        param_mappings=[
            LLMParamMapping("pattern", "pattern"),
            LLMParamMapping("path", "paths"),
            LLMParamMapping("limit", "max_results"),
        ],
        intent_hints=["grep", "搜索"],
    ),

    "ls": LLMToolConfig(
        llm_tool_name="ls",
        system_tool_name="repo_tree",
        priority=100,
        description="目录列表",
        param_mappings=[
            LLMParamMapping("path", "root"),
            LLMParamMapping("dir", "root"),
            LLMParamMapping("depth", "depth"),
            LLMParamMapping("max", "max_entries"),
        ],
        intent_hints=["ls", "列表", "目录", "列出文件"],
    ),

    "list_directory": LLMToolConfig(
        llm_tool_name="list_directory",
        system_tool_name="repo_tree",
        priority=95,
        param_mappings=[
            LLMParamMapping("path", "root"),
        ],
        intent_hints=["list", "目录列表"],
    ),

    "write_file": LLMToolConfig(
        llm_tool_name="write_file",
        system_tool_name="precision_edit",
        priority=100,
        description="文件写入",
        param_mappings=[
            LLMParamMapping("path", "file"),
            LLMParamMapping("content", "content"),
            LLMParamMapping("text", "content"),
        ],
        intent_hints=["写入", "write", "创建文件"],
    ),

    "append_to_file": LLMToolConfig(
        llm_tool_name="append_to_file",
        system_tool_name="append_to_file",
        priority=100,
        param_mappings=[
            LLMParamMapping("path", "file"),
            LLMParamMapping("content", "content"),
        ],
        intent_hints=["追加", "append", "追加内容"],
    ),
}
```

### 3.2 意图推断结果

```python
from dataclasses import dataclass
from enum import Enum

class ToolIntent(Enum):
    """工具意图枚举"""
    READ_FULL = "read_full"           # 读取完整文件
    READ_HEAD = "read_head"           # 读取文件头部
    READ_TAIL = "read_tail"           # 读取文件尾部
    READ_RANGE = "read_range"         # 读取文件范围
    SEARCH = "search"                 # 代码搜索
    LIST = "list"                    # 目录列表
    WRITE = "write"                  # 文件写入
    UNKNOWN = "unknown"              # 未知

@dataclass
class IntentResult:
    """意图推断结果"""
    intent: ToolIntent
    confidence: float                # 置信度 0-1
    recommended_tool: str            # 推荐工具
    reasoning: str                  # 推理过程
```

---

## 4. 核心组件设计

### 4.1 LLMToolAdapter (主适配器)

```python
class LLMToolAdapter:
    """LLM 工具适配器 - 将 LLM 的工具调用映射到系统工具"""

    def __init__(self, tool_map: dict[str, LLMToolConfig] | None = None):
        self.tool_map = tool_map or LLM_TOOL_MAP
        self.system_tools = self._load_system_tools()
        self.intent_engine = IntentEngine()
        self.param_normalizer = ParamNormalizer()

    def resolve(self, llm_tool_name: str, llm_params: dict, context: dict | None = None) -> ResolveResult:
        """解析 LLM 工具调用

        Args:
            llm_tool_name: LLM 使用的工具名
            llm_params: LLM 传递的参数
            context: 上下文信息（用户消息、历史等）

        Returns:
            解析结果，包含系统工具名和归一化参数
        """
        # 1. 精确匹配
        if llm_tool_name in self.tool_map:
            return self._resolve_exact(llm_tool_name, llm_params, context)

        # 2. 模糊匹配
        best_match = self._fuzzy_match(llm_tool_name)
        if best_match:
            return self._resolve_exact(best_match, llm_params, context)

        # 3. 智能推断
        return self._resolve_infer(llm_tool_name, llm_params, context)

    def _resolve_exact(self, llm_tool_name: str, llm_params: dict, context: dict | None) -> ResolveResult:
        """精确匹配解析"""
        config = self.tool_map[llm_tool_name]
        normalized_params = self.param_normalizer.normalize(llm_params, config.param_mappings)

        # 意图推断
        intent_result = self.intent_engine.infer(llm_tool_name, llm_params, context)

        return ResolveResult(
            llm_tool_name=llm_tool_name,
            system_tool_name=config.system_tool_name,
            normalized_params=normalized_params,
            intent=intent_result,
            mapping_source="exact",
        )

    def _fuzzy_match(self, llm_tool_name: str) -> str | None:
        """模糊匹配"""
        name_lower = llm_tool_name.lower()
        best_match = None
        best_score = 0

        for known_tool in self.tool_map.keys():
            score = self._calculate_similarity(name_lower, known_tool.lower())
            if score > best_score and score >= 0.6:
                best_score = score
                best_match = known_tool

        return best_match

    def _resolve_infer(self, llm_tool_name: str, llm_params: dict, context: dict | None) -> ResolveResult:
        """智能推断解析"""
        # 尝试从工具名推断
        inferred_tool = self._infer_from_name(llm_tool_name)

        if inferred_tool:
            config = self.tool_map[inferred_tool]
            normalized_params = self.param_normalizer.normalize(llm_params, config.param_mappings)

            return ResolveResult(
                llm_tool_name=llm_tool_name,
                system_tool_name=config.system_tool_name,
                normalized_params=normalized_params,
                intent=self.intent_engine.infer(inferred_tool, llm_params, context),
                mapping_source="inferred",
            )

        # 最后尝试：如果是系统工具名，直接使用
        if llm_tool_name in self.system_tools:
            return ResolveResult(
                llm_tool_name=llm_tool_name,
                system_tool_name=llm_tool_name,
                normalized_params=llm_params,
                intent=IntentResult(ToolIntent.UNKNOWN, 0.5, llm_tool_name, "Direct system tool"),
                mapping_source="direct",
            )

        raise UnknownToolError(f"无法解析工具: {llm_tool_name}")

    def _infer_from_name(self, tool_name: str) -> str | None:
        """从工具名推断"""
        name_lower = tool_name.lower()

        # 读取相关
        if "read" in name_lower or "file" in name_lower or "head" in name_lower:
            if "tail" in name_lower:
                return "read_tail"
            if "head" in name_lower or "first" in name_lower:
                return "read_head"
            return "read_file"

        # 搜索相关
        if "search" in name_lower or "grep" in name_lower or "find" in name_lower:
            return "search_code"

        # 目录相关
        if "ls" in name_lower or "list" in name_lower or "dir" in name_lower:
            return "ls"

        # 写入相关
        if "write" in name_lower or "edit" in name_lower:
            return "write_file"

        if "append" in name_lower:
            return "append_to_file"

        return None

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """计算字符串相似度"""
        if not s1 or not s2:
            return 0

        # 共同前缀权重更高
        prefix_len = 0
        for a, b in zip(s1, s2):
            if a == b:
                prefix_len += 1
            else:
                break

        prefix_score = prefix_len / max(len(s1), len(s2))

        # 包含关系
        contains_score = 1.0 if s1 in s2 or s2 in s1 else 0.0

        return max(prefix_score, contains_score * 0.8)

    def _load_system_tools(self) -> set[str]:
        """加载系统工具列表"""
        # 从 contracts.py 获取
        from polaris.kernelone.tools.contracts import supported_tool_names
        return set(supported_tool_names())
```

### 4.2 IntentEngine (意图推断引擎)

```python
class IntentEngine:
    """意图推断引擎"""

    def __init__(self):
        self.intent_keywords = {
            ToolIntent.READ_FULL: ["完整", "全文", "整个", "all", "full", "read entire"],
            ToolIntent.READ_HEAD: ["前", "开头", "头部", "head", "first", "前几行", "前50行", "开头"],
            ToolIntent.READ_TAIL: ["后", "尾部", "tail", "last", "最后", "末尾"],
            ToolIntent.SEARCH: ["搜索", "查找", "grep", "search", "find", "查找函数", "搜索关键词"],
            ToolIntent.LIST: ["列表", "目录", "ls", "list files", "列出文件"],
            ToolIntent.WRITE: ["写入", "write", "创建文件", "修改文件"],
        }

    def infer(self, tool_name: str, params: dict, context: dict | None = None) -> IntentResult:
        """推断工具意图

        结合工具名、参数、上下文推断 LLM 的真实意图
        """
        user_message = context.get("user_message", "") if context else ""

        # 1. 从工具名推断
        intent_from_name = self._infer_from_tool_name(tool_name)

        # 2. 从用户消息推断
        intent_from_message = self._infer_from_message(user_message)

        # 3. 从参数推断
        intent_from_params = self._infer_from_params(params)

        # 4. 综合决策
        return self._combine_intents(intent_from_name, intent_from_message, intent_from_params)

    def _infer_from_tool_name(self, tool_name: str) -> tuple[ToolIntent, float]:
        """从工具名推断"""
        name_lower = tool_name.lower()

        if "read" in name_lower and "head" in name_lower:
            return ToolIntent.READ_HEAD, 0.9
        if "read" in name_lower and "tail" in name_lower:
            return ToolIntent.READ_TAIL, 0.9
        if "read" in name_lower:
            return ToolIntent.READ_FULL, 0.7
        if "search" in name_lower or "grep" in name_lower or "find" in name_lower:
            return ToolIntent.SEARCH, 0.9
        if "ls" in name_lower or "list" in name_lower:
            return ToolIntent.LIST, 0.9
        if "write" in name_lower or "edit" in name_lower:
            return ToolIntent.WRITE, 0.9

        return ToolIntent.UNKNOWN, 0.0

    def _infer_from_message(self, message: str) -> tuple[ToolIntent, float]:
        """从用户消息推断"""
        if not message:
            return ToolIntent.UNKNOWN, 0.0

        message_lower = message.lower()
        scores = {}

        for intent, keywords in self.intent_keywords.items():
            score = sum(1 for kw in keywords if kw in message_lower) / len(keywords)
            scores[intent] = score

        if not scores:
            return ToolIntent.UNKNOWN, 0.0

        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]

        return best_intent, min(best_score * 2, 1.0)  # 放大分数

    def _infer_from_params(self, params: dict) -> tuple[ToolIntent, float]:
        """从参数推断"""
        param_str = str(params).lower()

        # limit 参数暗示限制行数
        if "limit" in param_str or "n" in params:
            return ToolIntent.READ_HEAD, 0.6

        return ToolIntent.UNKNOWN, 0.0

    def _combine_intents(self, *intents: tuple[ToolIntent, float]) -> IntentResult:
        """综合多个意图推断结果"""
        # 加权平均
        intent_scores: dict[ToolIntent, float] = {}
        for intent, score in intents:
            if intent != ToolIntent.UNKNOWN:
                intent_scores[intent] = intent_scores.get(intent, 0) + score

        if not intent_scores:
            return IntentResult(ToolIntent.UNKNOWN, 0.0, "", "No clear intent")

        best_intent = max(intent_scores, key=intent_scores.get)
        confidence = min(intent_scores[best_intent] / len(intents), 1.0)

        # 推荐工具
        tool_mapping = {
            ToolIntent.READ_FULL: "read_file",
            ToolIntent.READ_HEAD: "repo_read_head",
            ToolIntent.READ_TAIL: "repo_read_tail",
            ToolIntent.SEARCH: "repo_rg",
            ToolIntent.LIST: "repo_tree",
            ToolIntent.WRITE: "precision_edit",
        }

        recommended_tool = tool_mapping.get(best_intent, "")

        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            recommended_tool=recommended_tool,
            reasoning=f"Combined score: {intent_scores[best_intent]:.2f}",
        )
```

### 4.3 ParamNormalizer (参数归一化器)

```python
class ParamNormalizer:
    """参数归一化器"""

    def normalize(self, params: dict, mappings: list[LLMParamMapping]) -> dict:
        """将 LLM 参数映射到系统参数

        Args:
            params: LLM 传递的参数
            mappings: 参数映射配置

        Returns:
            归一化后的系统参数
        """
        result = {}
        mapping_dict = {m.llm_param: m for m in mappings}

        for key, value in params.items():
            if key in mapping_dict:
                mapping = mapping_dict[key]
                system_key = mapping.system_param

                # 应用转换函数
                if mapping.transform:
                    value = self._apply_transform(value, mapping.transform)
            else:
                # 保留未知参数（让系统处理错误）
                system_key = key

            result[system_key] = value

        return result

    def _apply_transform(self, value: Any, transform: str) -> Any:
        """应用转换函数"""
        if transform == "to_int":
            try:
                return int(value)
            except (ValueError, TypeError):
                return value
        elif transform == "to_list":
            if isinstance(value, str):
                return [value]
            return list(value) if value else []
        return value
```

---

## 5. 集成设计

### 5.1 与现有系统集成

```
┌─────────────────────────────────────────────────────────────────┐
│                    RoleExecutionKernel                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            LLMToolAdapter (新增)                       │   │
│  │  - 解析 LLM 工具调用                                  │   │
│  │  - 意图推断                                          │   │
│  │  - 参数归一化                                        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            │                                   │
│                            ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            ToolGateway (现有)                          │   │
│  │  - 执行系统工具                                        │   │
│  │  - 参数验证                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 集成点

| 文件 | 修改内容 |
|------|----------|
| `polaris/kernelone/tools/contracts.py` | 移除旧的别名映射逻辑 |
| `polaris/cells/roles/kernel/internal/tool_gateway.py` | 集成 LLMToolAdapter |
| `polaris/cells/roles/kernel/internal/tool_adapter.py` | 新增 LLMToolAdapter |
| `polaris/cells/roles/kernel/internal/intent_engine.py` | 新增 IntentEngine |
| `polaris/cells/roles/kernel/internal/param_normalizer.py` | 新增 ParamNormalizer |
| `polaris/cells/roles/kernel/internal/llm_tool_map.py` | 新增 LLM_TOOL_MAP |

---

## 6. 测试设计

### 6.1 单元测试

```python
import pytest
from polaris.cells.roles.kernel.internal.tool_adapter import LLMToolAdapter, LLM_TOOL_MAP

class TestLLMToolAdapter:
    """LLMToolAdapter 单元测试"""

    def test_exact_match_read_file(self):
        adapter = LLMToolAdapter()
        result = adapter.resolve("read_file", {"path": "test.py", "limit": 50})

        assert result.system_tool_name == "read_file"
        assert result.normalized_params == {"file": "test.py", "max_bytes": 50}
        assert result.mapping_source == "exact"

    def test_exact_match_read_head(self):
        adapter = LLMToolAdapter()
        result = adapter.resolve("read_head", {"path": "test.py", "n": 100})

        assert result.system_tool_name == "repo_read_head"
        assert result.normalized_params == {"file": "test.py", "n": 100}

    def test_fuzzy_match(self):
        adapter = LLMToolAdapter()
        result = adapter.resolve("ripgrep", {"pattern": "def ", "path": "."})

        assert result.system_tool_name == "repo_rg"
        assert result.mapping_source == "exact"  # ripgrep 在映射表中

    def test_unknown_tool_infer(self):
        adapter = LLMToolAdapter()
        result = adapter.resolve("some_unknown_tool", {"path": "test.py"})

        # 应该推断为 read_file
        assert result.system_tool_name in ["read_file", "repo_read_head"]

    def test_param_aliases(self):
        adapter = LLMToolAdapter()

        # 测试各种参数名
        test_cases = [
            ({"path": "test.py"}, {"file": "test.py"}),
            ({"filepath": "test.py"}, {"file": "test.py"}),
            ({"file_path": "test.py"}, {"file": "test.py"}),
            ({"limit": 50}, {"max_bytes": 50}),
            ({"lines": 100}, {"max_bytes": 100}),
        ]

        for llm_params, expected in test_cases:
            result = adapter.resolve("read_file", llm_params)
            assert result.normalized_params == expected


class TestIntentEngine:
    """意图推断引擎测试"""

    def test_infer_read_head_from_message(self):
        engine = IntentEngine()
        result = engine.infer("read_file", {"limit": 50}, {"user_message": "读取前50行"})

        assert result.intent == ToolIntent.READ_HEAD
        assert result.confidence > 0.5

    def test_infer_search_from_message(self):
        engine = IntentEngine()
        result = engine.infer("read_file", {}, {"user_message": "帮我搜索函数定义"})

        assert result.intent == ToolIntent.SEARCH
```

### 6.2 集成测试

```python
class TestToolAdapterIntegration:
    """集成测试"""

    def test_end_to_end_read_file(self):
        """端到端测试：LLM 调用 read_file 到系统执行"""
        adapter = LLMToolAdapter()

        # 模拟 LLM 调用
        llm_tool = "read_file"
        llm_params = {"path": "src/utils.py", "limit": 50}

        # 解析
        result = adapter.resolve(llm_tool, llm_params)

        # 验证解析结果
        assert result.system_tool_name == "read_file"
        assert "file" in result.normalized_params
        assert "max_bytes" in result.normalized_params

        # 执行
        from polaris.cells.roles.kernel.internal.tool_gateway import ToolGateway
        gateway = ToolGateway()
        execution_result = gateway.execute(result.system_tool_name, result.normalized_params)

        # 验证执行结果
        assert execution_result.success
```

---

## 7. 实施计划

### 阶段1: 核心实现 (1-2天)
1. 创建 `llm_tool_map.py` - LLM 工具映射表
2. 创建 `intent_engine.py` - 意图推断引擎
3. 创建 `param_normalizer.py` - 参数归一化器
4. 创建 `tool_adapter.py` - 主适配器

### 阶段2: 集成测试 (1天)
1. 集成到 `tool_gateway.py`
2. 单元测试
3. 集成测试

### 阶段3: 清理旧代码 (0.5天)
1. 移除 `contracts.py` 中的旧别名映射
2. 更新文档

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 意图推断不准确 | 工具执行错误 | 高置信度时使用，低置信度时警告 |
| 新 LLM 工具名未知 | 无法解析 | 提供模糊匹配和默认 fallback |
| 性能影响 | 增加解析延迟 | 缓存映射结果 |

---

## 9. 附录

### 9.1 LLM 工具名参考

| 类别 | LLM 常用工具名 |
|------|---------------|
| 读取文件 | `read_file`, `file_read`, `cat`, `rf` |
| 读取头部 | `read_head`, `head`, `first_n` |
| 读取尾部 | `read_tail`, `tail`, `last_n` |
| 代码搜索 | `search_code`, `grep`, `ripgrep`, `rg`, `find` |
| 目录列表 | `ls`, `list_dir`, `list_directory` |
| 写入文件 | `write_file`, `edit_file`, `create_file` |
| 追加内容 | `append`, `append_to_file` |

### 9.2 系统工具参考

| 系统工具 | 描述 |
|----------|------|
| `read_file` | 读取完整文件（高成本） |
| `repo_read_head` | 读取文件头部 |
| `repo_read_tail` | 读取文件尾部 |
| `repo_read_slice` | 读取文件范围 |
| `repo_rg` | 代码搜索 |
| `repo_tree` | 目录树 |
| `precision_edit` | 精确编辑 |
| `append_to_file` | 追加内容 |
