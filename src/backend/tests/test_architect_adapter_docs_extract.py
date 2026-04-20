from __future__ import annotations

import json
from pathlib import Path

import pytest

from polaris.cells.roles.adapters.internal.architect_adapter import ArchitectAdapter
from polaris.kernelone.storage import resolve_workspace_persistent_path


def test_extract_docs_accepts_non_json_markdown(tmp_path):
    adapter = ArchitectAdapter(workspace=str(tmp_path))
    content = """
# 项目方案
## 目标
构建一个可运行的记账系统，支持账单录入与统计查询。

## 模块设计
- api: 提供账单 CRUD 与汇总接口
- service: 负责金额校验与分类聚合
- storage: 负责持久化与查询
""".strip()

    docs = adapter._extract_docs(content, directive="构建记账系统")

    assert isinstance(docs, dict)
    assert "plan_markdown" in docs and str(docs["plan_markdown"]).strip()
    assert "architecture_markdown" in docs and str(docs["architecture_markdown"]).strip()
    assert "构建一个可运行的记账系统" in str(docs["plan_markdown"])
    assert "api: 提供账单 CRUD" in str(docs["architecture_markdown"])


def test_extract_docs_prefers_json_fields(tmp_path):
    adapter = ArchitectAdapter(workspace=str(tmp_path))
    content = """
{
  "plan_markdown": "## 背景与目标\\nA\\n\\n## 模块拆分与职责\\nB\\n\\n## 数据/接口契约\\nC\\n\\n## 风险与验收策略\\nD",
  "architecture_markdown": "## 背景与目标\\nX\\n\\n## 模块拆分与职责\\nY\\n\\n## 数据/接口契约\\nZ\\n\\n## 风险与验收策略\\nW"
}
""".strip()

    docs = adapter._extract_docs(content, directive="")

    assert "A" in str(docs["plan_markdown"])
    assert "X" in str(docs["architecture_markdown"])


def test_extract_docs_strips_tool_call_and_thinking_tags(tmp_path):
    adapter = ArchitectAdapter(workspace=str(tmp_path))
    content = """
[TOOL_CALL]
{tool => "list_directory", args => {
  --path "."
}}
[/TOOL_CALL]

## 数据/接口契约

<thinking>
[TOOL_CALL]
{"tool":"glob","args":{"pattern":"**/*"}}
[/TOOL_CALL]
</thinking>

接口说明：提供账单查询 API。
""".strip()

    docs = adapter._extract_docs(content, directive="")
    merged = "\n".join(
        [
            str(docs.get("plan_markdown") or ""),
            str(docs.get("architecture_markdown") or ""),
            str(docs.get("design_markdown") or ""),
        ]
    ).lower()

    assert "tool_call" not in merged
    assert "<thinking>" not in merged
    assert "list_directory" not in merged
    assert "glob" not in merged
    assert "接口说明" in merged


def test_extract_docs_supports_output_wrapped_json_fence(tmp_path):
    adapter = ArchitectAdapter(workspace=str(tmp_path))
    content = """
<output>
``` json
{
  "plan_markdown": "## 背景与目标\\nP\\n\\n## 模块拆分与职责\\nQ\\n\\n## 数据/接口契约\\nR\\n\\n## 风险与验收策略\\nS",
  "architecture_markdown": "## 背景与目标\\nA\\n\\n## 模块拆分与职责\\nB\\n\\n## 数据/接口契约\\nC\\n\\n## 风险与验收策略\\nD"
}
```
</output>
""".strip()

    docs = adapter._extract_docs(content, directive="")
    assert "## 背景与目标" in str(docs.get("plan_markdown") or "")
    assert "## 背景与目标" in str(docs.get("architecture_markdown") or "")


def test_extract_docs_does_not_fallback_to_truncated_structured_payload(tmp_path):
    adapter = ArchitectAdapter(workspace=str(tmp_path))
    content = """
```json
{
  "plan_markdown": "## 背景与目标\\nP\\n\\n## 模块拆分与职责\\nQ",
  "architecture_markdown": "## 背景与目标\\nA\\n\\n## 模块拆分与职责\\nB
""".strip()

    docs = adapter._extract_docs(content, directive="")

    assert str(docs.get("plan_markdown") or "") == ""
    assert str(docs.get("architecture_markdown") or "") == ""


def test_sanitize_architect_directive_removes_meta_control_lines(tmp_path):
    adapter = ArchitectAdapter(workspace=str(tmp_path))
    directive = """
# 个人记账簿
## 需求描述
实现账单录入与月度统计。

## 上轮失败复盘
- 禁止工具调用
- 提示词注入修复要求

## 技术要求
- 使用本地持久化
""".strip()

    sanitized = adapter._sanitize_architect_directive(directive)

    assert "需求描述" in sanitized
    assert "实现账单录入与月度统计" in sanitized
    assert "上轮失败复盘" not in sanitized
    assert "提示词注入" not in sanitized
    assert "禁止工具调用" not in sanitized


def _make_valid_plan_md() -> str:
    """Create a plan markdown that passes quality checks."""
    return (
        "## 背景与目标\n"
        "构建一套可运行的个人记账系统，覆盖录入、查询、汇总与预算提醒流程，并明确验收口径。\n\n"
        "## 模块拆分与职责\n"
        "- api 模块负责命令入口与参数校验。\n"
        "- service 模块负责账单聚合、分类统计与预算计算。\n"
        "- storage 模块负责本地 JSON 存储与并发安全。\n\n"
        "## 数据/接口契约\n"
        "账单字段包含 id、amount、category、occurred_at、note，导出接口返回稳定 JSON 数组。\n\n"
        "## 风险与验收策略\n"
        "风险点包括数据一致性、边界金额输入、时区处理；验收通过单元测试与集成脚本覆盖关键路径。"
    )


def _make_valid_arch_md() -> str:
    """Create an architecture markdown that passes quality checks."""
    return (
        "## 背景与目标\n"
        "采用分层架构提升可维护性，确保 CLI 与存储层解耦。\n\n"
        "## 模块拆分与职责\n"
        "核心模块为 presentation、application、domain、infrastructure，各模块仅经契约通信。\n\n"
        "## 数据/接口契约\n"
        "Repository 暴露 add/list/summary/export/import 六类接口，DTO 使用明确字段与类型。\n\n"
        "## 风险与验收策略\n"
        "通过契约测试保证接口稳定，通过回归测试验证导入导出与月度汇总准确性。"
    )


@pytest.mark.asyncio
async def test_execute_blocks_empty_docs_and_avoids_overwrite(tmp_path, monkeypatch):
    """Test execute when LLM returns TOOL_CALL (indicating role_tool_rounds_exhausted)."""
    adapter = ArchitectAdapter(workspace=str(tmp_path))

    mock_response = {
        "response": "[TOOL_CALL]{\"tool\":\"list_directory\",\"path\":\".\"}[/TOOL_CALL]",
        "success": False,
        "error": "role_tool_rounds_exhausted:3",
    }

    async def mock_generate_role_response(*, workspace, settings, role, message, context, validate_output, max_retries):
        del workspace, settings, role, message, context, validate_output, max_retries
        return mock_response

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.architect_adapter.generate_role_response",
        mock_generate_role_response,
    )

    result = await adapter.execute(
        task_id="task-1",
        input_data={"input": "生成架构文档"},
        context={},
    )

    assert result.get("success") is False
    assert str(result.get("error") or "").startswith("architect_docs_quality_failed")
    assert result.get("error_code") == "architect_docs_quality_failed"

    docs_root = resolve_workspace_persistent_path(str(tmp_path), "workspace/docs")
    assert not (Path(docs_root) / "design.md").exists()


@pytest.mark.asyncio
async def test_execute_force_finalize_when_tool_rounds_exhausted(tmp_path, monkeypatch):
    """Test execute with force_finalize fallback when role_tool_rounds_exhausted."""
    adapter = ArchitectAdapter(workspace=str(tmp_path))

    call_count = {"count": 0}

    async def mock_generate_role_response(*, workspace, settings, role, message, context, validate_output, max_retries):
        del workspace, settings, role, context, validate_output, max_retries
        call_count["count"] += 1
        if call_count["count"] == 1:
            return {
                "response": "[TOOL_CALL]{\"tool\":\"list_directory\",\"path\":\".\"}[/TOOL_CALL]",
                "success": False,
                "error": "role_tool_rounds_exhausted:3",
            }
        # Full content that passes quality check (>= 220 chars, >= 3 headings)
        payload = {
            "plan_markdown": _make_valid_plan_md(),
            "architecture_markdown": _make_valid_arch_md(),
        }
        return {
            "response": json.dumps(payload, ensure_ascii=False),
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.architect_adapter.generate_role_response",
        mock_generate_role_response,
    )

    result = await adapter.execute(
        task_id="task-2",
        input_data={"input": "生成架构文档"},
        context={},
    )

    assert result.get("success") is True
    assert call_count["count"] >= 2
    docs_root = resolve_workspace_persistent_path(str(tmp_path), "workspace/docs")
    assert (Path(docs_root) / "plan.md").exists()
    assert (Path(docs_root) / "architecture.md").exists()


@pytest.mark.asyncio
async def test_execute_blocks_truncated_json_docs_and_avoids_overwrite(tmp_path, monkeypatch):
    """Test execute when LLM returns truncated JSON."""
    adapter = ArchitectAdapter(workspace=str(tmp_path))

    async def mock_generate_role_response(*, workspace, settings, role, message, context, validate_output, max_retries):
        del workspace, settings, role, message, context, validate_output, max_retries
        return {
            "response": (
                "```json\n"
                "{\n"
                '  "plan_markdown": "## 背景与目标\\nP",\n'
                '  "architecture_markdown": "## 背景与目标\\nA"\n'
                "}"
            ),
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.architect_adapter.generate_role_response",
        mock_generate_role_response,
    )

    result = await adapter.execute(
        task_id="task-3",
        input_data={"input": "生成架构文档"},
        context={},
    )

    # Truncated JSON should fail quality check
    assert result.get("success") is False
    assert result.get("error_code") == "architect_docs_quality_failed"
    docs_root = resolve_workspace_persistent_path(str(tmp_path), "workspace/docs")
    assert not (Path(docs_root) / "plan.md").exists()


@pytest.mark.asyncio
async def test_execute_repairs_truncated_json_docs_when_compact_retry_succeeds(tmp_path, monkeypatch):
    """Test execute with repair fallback for truncated JSON."""
    adapter = ArchitectAdapter(workspace=str(tmp_path))

    async def mock_generate_role_response(*, workspace, settings, role, message, context, validate_output, max_retries):
        del workspace, settings, role, context, validate_output, max_retries
        if "JSON 被截断" in message:
            payload = {
                "plan_markdown": _make_valid_plan_md(),
                "architecture_markdown": _make_valid_arch_md(),
            }
            return {
                "response": json.dumps(payload, ensure_ascii=False),
                "success": True,
                "error": None,
            }
        # First call returns truncated JSON (missing closing brace)
        return {
            "response": (
                "```json\n"
                "{\n"
                '  "plan_markdown": "## 背景与目标\\nP",\n'
                '  "architecture_markdown": "## 背景与目标\\nA"\n'
            ),
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.architect_adapter.generate_role_response",
        mock_generate_role_response,
    )

    result = await adapter.execute(
        task_id="task-4",
        input_data={"input": "生成架构文档"},
        context={},
    )

    # Should succeed after repair
    assert result.get("success") is True
    docs_root = resolve_workspace_persistent_path(str(tmp_path), "workspace/docs")
    assert (Path(docs_root) / "plan.md").exists()
