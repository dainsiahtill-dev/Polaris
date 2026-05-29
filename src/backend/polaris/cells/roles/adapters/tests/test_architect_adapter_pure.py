"""Pure ArchitectAdapter behavior tests."""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.adapters.internal.architect_adapter import ArchitectAdapter


def _make_adapter(tmp_path: Any) -> ArchitectAdapter:
    return ArchitectAdapter(workspace=str(tmp_path))


class TestArchitectPrompt:
    def test_prompt_forbids_inspection_deferral(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        message = adapter._build_architect_message("Build FashionGenStudio")

        assert "没有读取/检查工具" in message
        assert "不要声明“先检查项目/目录/代码”" in message
        assert "plan_markdown" in message
        assert "architecture_markdown" in message


class TestNonFinalActionDetection:
    def test_detects_english_inspection_deferral(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        assert adapter._looks_like_non_final_action_response(
            "Let me first inspect the existing project to ground the architecture in reality."
        )

    def test_detects_chinese_inspection_deferral(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        assert adapter._looks_like_non_final_action_response("我先检查当前项目目录，然后再给出架构。")

    def test_allows_final_json_payload(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        assert not adapter._looks_like_non_final_action_response(
            '{"plan_markdown":"## 背景与目标\\n- 目标明确",'
            '"architecture_markdown":"## 架构与技术栈\\n- 使用 TypeScript 模块"}'
        )


class TestSkipLlmDocRepairs:
    def test_skips_repair_after_timeout_with_blocking_issues(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        assert adapter._should_skip_llm_doc_repairs(
            result={"error": "Request timeout (60.0s)"},
            issues=["plan_markdown_too_short"],
            content="",
        )

    def test_does_not_skip_when_docs_are_good(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        assert not adapter._should_skip_llm_doc_repairs(
            result={"error": ""},
            issues=[],
            content='{"plan_markdown":"ok","architecture_markdown":"ok"}',
        )
