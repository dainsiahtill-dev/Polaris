from __future__ import annotations

from pathlib import Path

from .role_tool_habits import (
    ToolInteraction,
    TurnRecord,
    allocate_fresh_workspace,
    analyze_tool_interaction,
    build_default_prompts,
    build_summary,
    load_prompt_corpus,
    seed_demo_workspace,
)


def test_load_prompt_corpus_from_json_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts.json"
    prompt_file.write_text(
        '[ "inspect the workspace", "search for TODO markers" ]\n',
        encoding="utf-8",
    )

    prompts = load_prompt_corpus(prompt_file)

    assert prompts == ["inspect the workspace", "search for TODO markers"]


def test_seed_demo_workspace_creates_utf8_files(tmp_path: Path) -> None:
    written = seed_demo_workspace(tmp_path)

    assert "README.md" in written
    assert "src/service.py" in written
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# Expense Tracker Demo")
    assert "summarize_expenses" in (tmp_path / "src" / "service.py").read_text(encoding="utf-8")


def test_analyze_tool_interaction_detects_common_failed_habits() -> None:
    search_findings = analyze_tool_interaction(
        tool="search_code",
        args={"key": "expense_total", "path": "/workspace/src/", "recursive": "true\n**"},
        raw_result={"ok": False, "error": "Parameter validation failed: Missing required parameter: query"},
        prompt_index=1,
    )
    execute_findings = analyze_tool_interaction(
        tool="execute_command",
        args={"command": "ls -la /workspace/src", "timeout": "5\n**"},
        raw_result={"ok": False, "error": "Executable is not allowed: ls"},
        prompt_index=1,
    )

    search_categories = {item["category"] for item in search_findings}
    execute_categories = {item["category"] for item in execute_findings}
    read_file_categories = {
        item["category"]
        for item in analyze_tool_interaction(
            tool="read_file",
            args={"path": "/workspace/README.md"},
            raw_result={"success": False, "error": "路径 '/workspace/README.md' 包含穿越序列"},
            prompt_index=1,
        )
    }

    assert "search_query_alias" in search_categories
    assert "search_query_validation_failure" in search_categories
    assert "markdown_scalar_noise" in search_categories
    assert "readonly_shell_alias" in execute_categories
    assert "disallowed_executable" in execute_categories
    assert "workspace_alias_path_rejected" in read_file_categories


def test_build_default_prompts_returns_role_specific_tail() -> None:
    prompts = build_default_prompts("qa")

    assert len(prompts) == 3
    assert "QA review" in prompts[-1]


def test_allocate_fresh_workspace_rejects_polluted_existing_workspace(tmp_path: Path) -> None:
    polluted = tmp_path / "polluted"
    polluted.mkdir(parents=True, exist_ok=True)
    (polluted / "old.txt").write_text("stale\n", encoding="utf-8")

    try:
        allocate_fresh_workspace(polluted)
    except ValueError as exc:
        assert "not fresh" in str(exc)
    else:
        raise AssertionError("Expected polluted workspace to be rejected")


def test_build_summary_separates_failed_and_tolerated_habits() -> None:
    interaction = ToolInteraction(
        tool="execute_command",
        success=False,
        error="Executable is not allowed: ls",
        findings=[
            {"category": "readonly_shell_alias", "status": "tolerated"},
            {"category": "disallowed_executable", "status": "failed"},
            {"category": "markdown_scalar_noise", "status": "observed"},
        ],
    )
    summary = build_summary([TurnRecord(prompt_index=1, prompt="p", tool_interactions=[interaction])])

    assert summary["failed_categories"] == {"disallowed_executable": 1}
    assert summary["tolerated_habits"] == {"readonly_shell_alias": 1}
    assert summary["observed_habits"] == {"markdown_scalar_noise": 1}
