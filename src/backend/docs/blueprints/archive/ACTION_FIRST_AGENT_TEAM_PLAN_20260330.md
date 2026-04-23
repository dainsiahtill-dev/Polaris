# Action-First Agent System Prompt — 10人团队执行计划

**版本**: v1.0
**日期**: 2026-03-30
**执行团队**: 10 名高级 Python 工程师
**蓝图文档**: `docs/blueprints/ACTION_FIRST_AGENT_SYSTEM_PROMPT_BLUEPRINT_20260330.md`

---

## 执行摘要

| 角色 | Phase | 职责 | 核心文件 | 预估规模 |
|------|-------|------|---------|---------|
| Python Architect A-B | Phase 1A | Prompt模板开发 | `action_first.j2`, `prompt_templates.py` | ~120行 |
| Backend Engineer C-D | Phase 1B | Output Parser开发 | `action_parser.py`, `thinking_extractor.py` | ~200行 |
| Backend Engineer E-F | Phase 2 | Error Recovery机制 | `retry_policy.py`, `context_injector.py` | ~300行 |
| QA Engineer G-H | Phase 3 | 单元测试编写 | `tests/action_parser/`, `tests/error_recovery/` | ~500行 |
| DevOps/Benchmark I-J | Phase 4 | 集成测试 + CI门禁 | `test_benchmark_integration.py`, `gate.py` | ~300行 |

---

## Phase 1A: Prompt模板开发

**Engineer 1-2 专属**

### 前提：复用现有 Persona 系统

**已有系统（禁止重复造轮子）**：
- `assets/personas.yaml` — 100种人格YAML
- `polaris/cells/roles/kernel/internal/prompt_templates.py`:
  - `Persona` dataclass
  - `get_persona(persona_id) -> Persona`
  - `get_persona_registry() -> dict[str, Persona]`
  - `build_persona_prompt(template_id, persona_id) -> str`
- `polaris/kernelone/storage/persona_store.py`:
  - `load_workspace_persona(workspace, persona_ids) -> str`

### 步骤 1A.1: 创建 Action-First 基座模板

**文件**: `polaris/kernelone/llm/templates/action_first.j2`

```jinja
<role_definition>
你是 {persona_name}，{persona_traits}。在 <thinking> 区域内，尽情展现你的人设魅力。
</role_definition>

<production_physics>
你运行在一个真实、复杂、容错成本极高的生产代码库中。以下工程铁律是"物理定律"，违背即导致任务失败：
1. 【行动优先 (Action-First)】：探索目录、列表必须调用 repo_tree/list_directory，禁止用自然语言口头描述或猜测
2. 【EAFP豁免】：绝对禁止调用 file_exists 做预检。直接读取或写入目标，利用报错动态调整策略
3. 【闭环交付】：涉及代码修改的任务，必须以写入操作作为终结（调用 edit_file/append_to_file/precision_edit）。未落盘=零产出
</production_physics>

<output_contract>
<thinking>
[意识隔离区：用 {persona_name} 的语气风格进行沉浸式思考]
1. 目标分析：[当前状态 vs 目标状态]
2. 动作决策：[基于物理定律决定工具及参数]
</thinking>

[Action]: {tool_name}
[Arguments]: {json_arguments}
[Status]: {In Progress | Completed}
[Marker]: {marker}
</output_contract>
```

### 步骤 1A.2: 扩展 prompt_templates.py

**文件**: `polaris/kernelone/llm/prompt_templates.py`（追加）

在 `ROLE_PROMPT_TEMPLATES` 中新增 `action_first` 模板：

```python
ACTION_FIRST_TEMPLATE = """
你是 {persona_name}，{persona_traits}。

【生产物理定律 — 不可违背】
1. 【行动优先】：探索目录、列表必须调用 repo_tree/list_directory，禁止用自然语言口头描述
2. 【EAFP豁免】：禁止调用 file_exists 预检，直接操作目标并通过报错动态调整
3. 【闭环交付】：修改任务必须以写入操作终结，未落盘=零产出

【输出契约】
<thinking>
[意识隔离区：用 {persona_name} 的语气风格思考]
1. 目标分析：[当前状态 vs 目标状态]
2. 动作决策：[基于物理定律决定工具及参数]
</thinking>

[Action]: {tool_name}
[Arguments]: {json_arguments}
[Status]: {In Progress | Completed}
[Marker]: {marker}
""".strip()
```

### 步骤 1A.3: 新增 build_action_first_prompt()

```python
def build_action_first_prompt(
    persona_id: str,
    tool_name: str = "{tool_name}",
    json_arguments: str = "{}",
    marker: str | None = None,
) -> str:
    """构建 Action-First 风格的 System Prompt。"""
    persona = get_persona(persona_id)
    return ACTION_FIRST_TEMPLATE.format(
        persona_name=persona.name,
        persona_traits=persona.traits,
        tool_name=tool_name,
        json_arguments=json_arguments,
        marker=marker or "None",
    )
```

### 步骤 1A.4: 验证 Phase 1A

```bash
python -c "
from polaris.cells.roles.kernel.internal.prompt_templates import build_action_first_prompt, get_persona_registry
from polaris.kernelone.storage.persona_store import load_workspace_persona

registry = get_persona_registry()
persona_id = load_workspace_persona('.', list(registry.keys()))
prompt = build_action_first_prompt(persona_id)
assert '<role_definition>' in prompt
assert '<thinking>' in prompt
assert '[Action]:' in prompt
assert '【行动优先】' in prompt
assert '【EAFP豁免】' in prompt
assert '【闭环交付】' in prompt
print(f'Phase 1A: Prompt template OK (persona={persona_id})')
"
```

---

## Phase 1B: Output Parser开发

**Engineer 3-4 专属**

### 步骤 1B.1: Action Parser

**文件**: `polaris/kernelone/llm/output/action_parser.py`

```python
"""从 LLM 输出中提取 Action/Arguments/Status/Marker 块。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionBlock:
    """解析后的 Action 块。"""
    tool_name: str | None
    arguments: dict[str, Any]
    status: str  # "In Progress" | "Completed"
    marker: str | None


ACTION_PATTERN = re.compile(
    r'\[Action\]:\s*(\w+)\s*\n'
    r'\[Arguments\]:\s*(\{[^}]+\})\s*\n'
    r'\[Status\]:\s*(In Progress|Completed)\s*\n'
    r'\[Marker\]:\s*(\S+|None)',
    re.MULTILINE | re.DOTALL,
)


def parse_action_block(text: str) -> ActionBlock | None:
    """从文本中提取 Action 块。"""
    match = ACTION_PATTERN.search(text)
    if not match:
        return None

    tool_name, args_json, status, marker = match.groups()

    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        args = {}

    return ActionBlock(
        tool_name=tool_name,
        arguments=args,
        status=status,
        marker=None if marker == "None" else marker,
    )


def extract_thinking_block(text: str) -> str | None:
    """从文本中提取 <thinking> 块内容。"""
    match = re.search(r'<thinking>\s*(.*?)\s*</thinking>', text, re.DOTALL)
    return match.group(1).strip() if match else None
```

### 步骤 1B.2: 单元测试

**文件**: `polaris/kernelone/llm/output/tests/test_action_parser.py`

```python
import pytest
from polaris.kernelone.llm.output.action_parser import parse_action_block, extract_thinking_block, ActionBlock


def test_basic_action_block():
    text = """
    <thinking>Some thoughts here</thinking>
    [Action]: repo_tree
    [Arguments]: {"path": "."}
    [Status]: In Progress
    [Marker]: None
    """
    block = parse_action_block(text)
    assert block is not None
    assert block.tool_name == "repo_tree"
    assert block.arguments == {"path": "."}
    assert block.status == "In Progress"


def test_missing_action_returns_none():
    text = "No action here"
    assert parse_action_block(text) is None


def test_thinking_extraction():
    text = "<thinking>My thoughts</thinking> [Action]: test"
    assert extract_thinking_block(text) == "My thoughts"
```

### 步骤 1B.3: 验证 Phase 1B

```bash
pytest polaris/kernelone/llm/output/tests/test_action_parser.py -v
```

---

## Phase 2: Error Recovery机制

**Engineer 5-6 专属**

### 步骤 2.1: Retry Policy

**文件**: `polaris/kernelone/llm/error_recovery/retry_policy.py`

```python
"""工具执行失败后的自动重试与纠偏策略。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RetryConfig:
    """重试配置。"""
    max_retries: int = 3
    base_delay: float = 0.5
    exponential_backoff: bool = True


@dataclass
class ToolError:
    """工具执行错误。"""
    tool_name: str
    error_message: str
    args: dict[str, Any]


class RetryPolicy:
    """错误重试策略。"""

    def __init__(self, config: RetryConfig | None = None):
        self._config = config or RetryConfig()

    def should_retry(self, error: ToolError, attempt: int) -> bool:
        """判断是否应该重试。"""
        if attempt >= self._config.max_retries:
            return False
        # 某些错误不应重试
        if "Permission denied" in error.error_message:
            return False
        if "File not found" in error.error_message:
            # EAFP 哲学：不重试，修正路径后继续
            return True
        return True

    def compute_delay(self, attempt: int) -> float:
        """计算重试延迟。"""
        if self._config.exponential_backoff:
            return self._config.base_delay * (2 ** attempt)
        return self._config.base_delay

    def build_error_context(self, error: ToolError) -> str:
        """为下一次 LLM 调用构建错误上下文。"""
        return f"""[Previous Action Failed]
Tool: {error.tool_name}
Error: {error.error_message}
Arguments: {error.args}

Think: How to recover from this error? Consider:
1. Is the path correct?
2. Should I try a different tool?
3. Is there a permission issue?
"""
```

### 步骤 2.2: Context Injector

**文件**: `polaris/kernelone/llm/error_recovery/context_injector.py`

```python
"""将错误上下文注入 LLM 对话历史。"""

from __future__ import annotations

from typing import Any


class ErrorContextInjector:
    """错误上下文注入器。"""

    @staticmethod
    def inject_error_context(
        history: list[dict[str, Any]],
        tool_name: str,
        error_message: str,
        args: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """向对话历史注入错误上下文。"""
        error_entry = {
            "role": "system",
            "content": f"[Tool Execution Failed]\nTool: {tool_name}\nError: {error_message}\n\n请基于以上错误信息，决定下一步行动。",
        }
        return history + [error_entry]

    @staticmethod
    def inject_recovery_hint(
        history: list[dict[str, Any]],
        hint: str,
    ) -> list[dict[str, Any]]:
        """注入恢复提示。"""
        hint_entry = {
            "role": "system",
            "content": f"[Recovery Hint]\n{hint}",
        }
        return history + [hint_entry]
```

### 步骤 2.3: 验证 Phase 2

```bash
python -c "
from polaris.kernelone.llm.error_recovery.retry_policy import RetryPolicy, ToolError, RetryConfig
from polaris.kernelone.llm.error_recovery.context_injector import ErrorContextInjector

policy = RetryPolicy()
error = ToolError('read_file', 'File not found: /tmp/test.py', {'path': '/tmp/test.py'})
assert policy.should_retry(error, 0) == True
assert policy.should_retry(error, 3) == False  # max_retries=3

ctx = policy.build_error_context(error)
assert 'File not found' in ctx
assert 'read_file' in ctx

history = []
new_history = ErrorContextInjector.inject_error_context(history, 'read_file', 'File not found', {})
assert len(new_history) == 1

print('Phase 2: Error Recovery OK')
"
```

---

## Phase 3: 单元测试编写

**Engineer 7-8 专属**

### 步骤 3.1: Action Parser Tests

**文件**: `polaris/kernelone/llm/output/tests/test_action_parser_comprehensive.py`

```python
"""Action Parser 综合测试。"""

import pytest
from polaris.kernelone.llm.output.action_parser import (
    parse_action_block,
    extract_thinking_block,
    ActionBlock,
)


class TestActionBlockParsing:
    """测试各种 Action 块格式。"""

    def test_standard_format(self):
        text = """[Action]: repo_tree
[Arguments]: {"path": ".", "depth": 2}
[Status]: In Progress
[Marker]: None"""
        block = parse_action_block(text)
        assert block.tool_name == "repo_tree"
        assert block.arguments == {"path": ".", "depth": 2}
        assert block.status == "In Progress"

    def test_with_marker(self):
        text = """[Action]: edit_file
[Arguments]: {"file": "test.py"}
[Status]: Completed
[Marker]: Added by agent"""
        block = parse_action_block(text)
        assert block.marker == "Added by agent"

    def test_complex_json_args(self):
        text = """[Action]: search_replace
[Arguments]: {"file": "a.py", "search": "old", "replace": "new", "flags": ["i", "g"]}
[Status]: Completed
[Marker]: None"""
        block = parse_action_block(text)
        assert block.arguments["flags"] == ["i", "g"]

    def test_invalid_json_fallback(self):
        text = """[Action]: test
[Arguments]: {invalid json}
[Status]: Completed
[Marker]: None"""
        block = parse_action_block(text)
        assert block.arguments == {}  # Falls back to empty dict


class TestThinkingExtraction:
    """测试 <thinking> 块提取。"""

    def test_simple_thinking(self):
        text = "<thinking>My thoughts</thinking> [Action]: test"
        assert extract_thinking_block(text) == "My thoughts"

    def test_multiline_thinking(self):
        text = """<thinking>
Line 1
Line 2
</thinking>"""
        assert "Line 1" in extract_thinking_block(text)
        assert "Line 2" in extract_thinking_block(text)

    def test_no_thinking_returns_none(self):
        text = "No thinking block"
        assert extract_thinking_block(text) is None
```

### 步骤 3.2: Error Recovery Tests

**文件**: `polaris/kernelone/llm/error_recovery/tests/test_retry_policy.py`

```python
"""Retry Policy 综合测试。"""

import pytest
from polaris.kernelone.llm.error_recovery.retry_policy import (
    RetryPolicy,
    RetryConfig,
    ToolError,
)


class TestRetryPolicy:
    """测试重试策略。"""

    def test_max_retries_exceeded(self):
        policy = RetryPolicy(RetryConfig(max_retries=3))
        error = ToolError("test", "any error", {})
        assert policy.should_retry(error, 0) is True
        assert policy.should_retry(error, 2) is True
        assert policy.should_retry(error, 3) is False

    def test_permission_error_no_retry(self):
        policy = RetryPolicy()
        error = ToolError("write", "Permission denied", {})
        assert policy.should_retry(error, 0) is False

    def test_exponential_backoff(self):
        policy = RetryPolicy(RetryConfig(exponential_backoff=True))
        assert policy.compute_delay(0) == 0.5
        assert policy.compute_delay(1) == 1.0
        assert policy.compute_delay(2) == 2.0

    def test_linear_backoff(self):
        policy = RetryPolicy(RetryConfig(exponential_backoff=False))
        assert policy.compute_delay(0) == 0.5
        assert policy.compute_delay(1) == 0.5
        assert policy.compute_delay(2) == 0.5
```

### 步骤 3.3: 验证 Phase 3

```bash
pytest polaris/kernelone/llm/output/tests/ polaris/kernelone/llm/error_recovery/tests/ -v --tb=short
```

---

## Phase 4: 集成测试 + CI门禁

**Engineer 9-10 专属**

### 步骤 4.1: Benchmark集成测试

**文件**: `polaris/kernelone/llm/tests/test_action_first_benchmark.py`

```python
"""Action-First 架构在 Benchmark 场景下的集成测试。"""

import pytest
from polaris.kernelone.llm.prompt_builder import PromptBuilder
from polaris.kernelone.llm.output.action_parser import parse_action_block, extract_thinking_block
from polaris.kernelone.llm.error_recovery.retry_policy import RetryPolicy, ToolError
from polaris.kernelone.llm.error_recovery.context_injector import ErrorContextInjector


class TestActionFirstBenchmark:
    """Benchmark 场景验证。"""

    def test_directory_listing_uses_repo_tree(self):
        """目录列表必须使用 repo_tree。"""
        prompt = PromptBuilder().build_action_first_prompt()
        # 在实际 Benchmark 中，LLM 会根据此 prompt 输出 Action
        # 这里验证 prompt 模板正确包含指令
        assert "repo_tree" in prompt
        assert "禁止" in prompt  # 禁令存在

    def test_file_read_no_precheck(self):
        """文件读取不应有 file_exists 预检。"""
        prompt = PromptBuilder().build_action_first_prompt()
        assert "file_exists" not in prompt or "禁止" in prompt
        assert "EAFP" in prompt or "直接" in prompt

    def test_edit_task_requires_write(self):
        """编辑任务必须以写入结束。"""
        prompt = PromptBuilder().build_action_first_prompt()
        assert "闭环" in prompt
        assert "写入" in prompt or "edit_file" in prompt or "precision_edit" in prompt

    def test_error_recovery_loop(self):
        """错误恢复循环正常工作。"""
        policy = RetryPolicy()
        history = []

        # Simulate tool error
        error = ToolError("read_file", "File not found: test.py", {"path": "test.py"})
        assert policy.should_retry(error, 0) is True

        # Inject error context
        new_history = ErrorContextInjector.inject_error_context(
            history, "read_file", "File not found: test.py", {"path": "test.py"}
        )
        assert len(new_history) == 1


class TestOutputContract:
    """输出契约验证。"""

    def test_parse_mock_llm_output(self):
        """解析模拟的 LLM 输出。"""
        mock_output = """<thinking>我需要先看看目录结构</thinking>
[Action]: repo_tree
[Arguments]: {"path": ".", "depth": 1}
[Status]: In Progress
[Marker]: None"""

        block = parse_action_block(mock_output)
        assert block is not None
        assert block.tool_name == "repo_tree"

        thinking = extract_thinking_block(mock_output)
        assert thinking is not None
        assert "目录结构" in thinking
```

### 步骤 4.2: CI门禁脚本

**文件**: `docs/governance/ci/scripts/run_action_first_gate.py`

```python
#!/usr/bin/env python3
"""Action-First Agent 架构门禁。

验证 Prompt 模板、Parser、Error Recovery 全部正常工作。
用法:
    python run_action_first_gate.py --workspace . --mode all
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".", help="仓库根目录")
    parser.add_argument("--mode", default="all", choices=["all", "prompt", "parser", "recovery"])
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.workspace) / "src" / "backend"))

    results = []

    if args.mode in ("all", "prompt"):
        try:
            from polaris.kernelone.llm.prompt_builder import PromptBuilder
            pb = PromptBuilder()
            prompt = pb.build_action_first_prompt()
            assert "<role_definition>" in prompt
            assert "<thinking>" in prompt
            assert "[Action]:" in prompt
            print("[PASS] Prompt template")
            results.append(True)
        except Exception as e:
            print(f"[FAIL] Prompt template: {e}")
            results.append(False)

    if args.mode in ("all", "parser"):
        try:
            from polaris.kernelone.llm.output.action_parser import parse_action_block
            test_text = "[Action]: test\n[Arguments]: {}\n[Status]: Completed\n[Marker]: None"
            block = parse_action_block(test_text)
            assert block is not None
            print("[PASS] Action parser")
            results.append(True)
        except Exception as e:
            print(f"[FAIL] Action parser: {e}")
            results.append(False)

    if args.mode in ("all", "recovery"):
        try:
            from polaris.kernelone.llm.error_recovery.retry_policy import RetryPolicy, ToolError
            policy = RetryPolicy()
            error = ToolError("test", "error", {})
            assert policy.should_retry(error, 0) is True
            print("[PASS] Error recovery")
            results.append(True)
        except Exception as e:
            print(f"[FAIL] Error recovery: {e}")
            results.append(False)

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
```

### 步骤 4.3: 注册 fitness rule

**文件**: `docs/governance/ci/fitness-rules.yaml`

新增规则：

```yaml
- id: action-first-agent-architecture
  description: "Action-First Agent System Prompt 架构完整性检查"
  severity: blocker
  phase: pre-commit
  command: python docs/governance/ci/scripts/run_action_first_gate.py --workspace . --mode all
```

### 步骤 4.4: 验证 Phase 4

```bash
python docs/governance/ci/scripts/run_action_first_gate.py --workspace . --mode all
# 期望输出: [PASS] × 3
```

---

## 团队协作约定

### PR 提交流程

| 顺序 | Phase | 工程师 | 依赖 |
|------|-------|--------|------|
| 1 | Phase 1A | Engineer 1-2 | 无 |
| 2 | Phase 1B | Engineer 3-4 | Phase 1A |
| 3 | Phase 2 | Engineer 5-6 | Phase 1A |
| 4 | Phase 3 | Engineer 7-8 | Phase 1B |
| 5 | Phase 4 | Engineer 9-10 | Phase 1B, Phase 2 |

### 代码风格

- 所有新增文件通过 `ruff check . --fix` 和 `ruff format .`
- 每个模块必须有对应测试（`pytest -v` 100% 通过）
- 禁止 `# type: ignore` 掩盖类型冲突

### 每日同步

每天 10:00 UTC+8 通过内部频道同步进度，blocker 立即升级。

---

## 预估工作量

| Phase | 工程师 | 规模 | 预估时间 |
|-------|--------|------|---------|
| Phase 1A | Engineer 1-2 | ~120行 | 1 人天 |
| Phase 1B | Engineer 3-4 | ~200行 | 1.5 人天 |
| Phase 2 | Engineer 5-6 | ~300行 | 2 人天 |
| Phase 3 | Engineer 7-8 | ~500行 + 测试 | 2 人天 |
| Phase 4 | Engineer 9-10 | ~300行 + CI配置 | 1.5 人天 |
| **合计** | | **~1420行** | **8 人天** |

---

## 10人专家团队技能矩阵

| 角色 | 核心技能 | 适合任务 |
|------|---------|---------|
| **Python Architect A** | Jinja2模板, Prompt工程 | Phase 1A Lead |
| **Python Architect B** | LLM输出解析, Regex | Phase 1A Support |
| **Backend Engineer C** | Parser开发, dataclass设计 | Phase 1B Lead |
| **Backend Engineer D** | 边缘case处理, 测试设计 | Phase 1B Support |
| **Backend Engineer E** | 错误处理, 重试策略 | Phase 2 Lead |
| **Backend Engineer F** | 上下文管理, 对话历史 | Phase 2 Support |
| **QA Engineer G** | pytest, 单元测试 | Phase 3 Lead |
| **QA Engineer H** | 集成测试, Mock | Phase 3 Support |
| **DevOps Engineer I** | CI/CD, 门禁设计 | Phase 4 Lead |
| **Benchmark Engineer J** | Benchmark, 性能测试 | Phase 4 Support |
