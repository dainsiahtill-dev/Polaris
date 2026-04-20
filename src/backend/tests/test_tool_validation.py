from __future__ import annotations

import subprocess

import pytest
from polaris.kernelone.llm.toolkit import executor as executor_module


@pytest.fixture(autouse=True)
def _populate_tool_registry():
    """Repopulate ToolSpecRegistry after reset_singletons clears it."""
    from polaris.kernelone.llm.toolkit.tool_normalization import schema_driven_normalizer
    from polaris.kernelone.tool_execution.tool_spec_registry import migrate_from_contracts_specs

    migrate_from_contracts_specs()
    schema_driven_normalizer._normalizer_instance = None


def test_validation_before_dependency_check_unknown_tool(monkeypatch) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(".")

    result = executor.execute("unknown_tool", {})

    assert result["ok"] is False
    assert "Unknown tool" in str(result.get("error") or "")


def test_removed_semantic_tools_are_unknown(monkeypatch) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(".")

    # 历史语义工具名已下线；不得再作为可调用工具。
    for tool_name in {"get_semantic_context", "analyze_code_changes", "verify_imports"}:
        result = executor.execute(tool_name, {"query": "UserService"})
        assert result["ok"] is False
        assert "Unknown tool" in str(result.get("error") or "")


def test_validation_before_dependency_check_for_missing_parameters(monkeypatch) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(".")

    result = executor.execute("search_code", {})

    assert result["ok"] is False
    assert "Parameter validation failed" in str(result.get("error") or "")
    assert "missing required argument: pattern" in str(result.get("error") or "")


def test_search_code_accepts_path_alias(monkeypatch) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(".")

    result = executor.execute(
        "search_code",
        {
            "query": "auth service",
            "path": "src/backend",
            "max": 5,
        },
    )

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("query") == "auth service"
    assert payload.get("backend") in {"rg", "rg_unavailable"}


def test_search_code_accepts_llm_type_file_pattern_and_max_lines_aliases(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "expense_book.py").write_text("def expense_total():\n    return 1\n", encoding="utf-8")

    result = executor.execute(
        "search_code",
        {
            "type": "grep",
            "query": "expense_total",
            "file_pattern": ["src/*.py"],
            "max_lines": 2,
            "max": 10,
        },
    )

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("query") == "expense_total"
    assert payload.get("backend") in {"rg", "rg_unavailable"}


def test_read_file_ignores_unknown_llm_parameters(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = executor.execute(
        "read_file",
        {
            "path": "/workspace/src/app.py",
            "recursive": "true\n**",
            "max_lines": 30,
        },
    )

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert "print('ok')" in str(payload.get("content") or "")


def test_execute_command_returns_error_without_shell_fallback_on_permission_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    call_shell_modes: list[bool] = []

    def _fake_run(args, **kwargs):  # noqa: ANN001, ANN003
        call_shell_modes.append(bool(kwargs.get("shell")))
        raise PermissionError("Access is denied")

    monkeypatch.setattr(executor_module.subprocess, "run", _fake_run)

    result = executor.execute("execute_command", {"command": "python --version", "timeout": 10})

    assert result["ok"] is False
    assert "Access is denied" in str(result.get("error") or "")
    assert call_shell_modes == [False]


def test_execute_command_strips_markdown_from_python_flag_but_keeps_security_block(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))

    result = executor.execute(
        "execute_command",
        {"command": 'python **-c** "print(123)"', "timeout": 10},
    )

    assert result["ok"] is False
    assert "Unsafe Python inline execution flag is not allowed: -c" in str(result.get("error") or "")


def test_read_file_accepts_file_path_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = executor.execute("read_file", {"file_path": "sample.txt"})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert str(payload.get("file") or "").endswith("sample.txt")
    assert str(payload.get("content") or "").strip() == "hello"


def test_write_file_normalizes_empty_search_patch_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))

    result = executor.execute(
        "write_file",
        {
            "file": "src/app.py",
            "content": '<<<<<<< SEARCH\n\n=======\nprint("ok")\n>>>>>>> REPLACE',
        },
    )

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("normalized_patch_like_write") is True
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == 'print("ok")\n'


def test_write_file_normalizes_search_replace_patch_payload_against_existing_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('old')\n", encoding="utf-8")

    result = executor.execute(
        "write_file",
        {
            "file": "src/app.py",
            "content": "<<<<<<< SEARCH\nprint('old')\n=======\nprint('new')\n>>>>>>> REPLACE",
        },
    )

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("normalized_patch_like_write") is True
    assert target.read_text(encoding="utf-8") == 'print("new")\n'


def test_write_file_rejects_unmatched_patch_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('old')\n", encoding="utf-8")

    result = executor.execute(
        "write_file",
        {
            "file": "src/app.py",
            "content": "<<<<<<< SEARCH\nprint('missing')\n=======\nprint('new')\n>>>>>>> REPLACE",
        },
    )

    assert result["ok"] is False
    assert "SEARCH block was not found" in str(result.get("error") or "")
    assert target.read_text(encoding="utf-8") == "print('old')\n"


def test_glob_accepts_query_alias_without_pattern(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")

    result = executor.execute(
        "glob",
        {
            "query": "src/**/*.py",
            "max": 10,
        },
    )

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("pattern") == "src/**/*.py"
    assert payload.get("total_results", 0) >= 2


def test_file_exists_accepts_file_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    target = tmp_path / "config.yaml"
    target.write_text("name: demo\n", encoding="utf-8")

    result = executor.execute("file_exists", {"file": "config.yaml"})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("exists") is True
    assert payload.get("is_file") is True


def test_list_directory_accepts_workspace_alias_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = executor.execute("list_directory", {"path": "/workspace", "recursive": False})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("path") == "."
    assert payload.get("total_entries", 0) >= 1


def test_file_exists_accepts_workspace_prefixed_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    target = tmp_path / "package.json"
    target.write_text('{"name":"demo"}\n', encoding="utf-8")

    result = executor.execute("file_exists", {"path": "/workspace/package.json"})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("exists") is True
    assert payload.get("is_file") is True


def test_list_directory_accepts_short_r_alias(monkeypatch, tmp_path) -> None:
    """测试 list_directory 接受短参数 r 作为 recursive"""
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").write_text("content")

    # r=True 应该被转换为 recursive=True，能递归列出子目录内容
    result = executor.execute("list_directory", {"path": ".", "r": True})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    # 验证 r=True 生效：能递归列出子目录中的文件
    assert payload.get("total_entries", 0) >= 2  # sub 目录和 file.txt


def test_glob_accepts_short_r_alias(monkeypatch, tmp_path) -> None:
    """测试 glob 接受短参数 r 作为 recursive"""
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "fastapi_entrypoint.py").write_text("print('ok')\n", encoding="utf-8")

    # r=True 应该被转换为 recursive=True，使 glob 递归搜索
    result = executor.execute("glob", {"path": ".", "pattern": "src/**/*.py", "r": True})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    # 验证 r=True 生效：能找到文件
    assert payload.get("total_results", 0) >= 1


def test_list_directory_accepts_projects_alias(monkeypatch, tmp_path) -> None:
    """测试 list_directory 接受 /projects 作为 workspace 别名"""
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = executor.execute("list_directory", {"path": "/projects", "recursive": False})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("path") == "."


def test_file_exists_accepts_projects_prefixed_path(monkeypatch, tmp_path) -> None:
    """测试 file_exists 接受 /projects/xxx 形式的路径"""
    monkeypatch.setattr(executor_module, "CODE_INTELLIGENCE_AVAILABLE", False)
    executor = executor_module.AgentAccelToolExecutor(str(tmp_path))
    target = tmp_path / "config.json"
    target.write_text('{}', encoding="utf-8")

    result = executor.execute("file_exists", {"path": "/projects/config.json"})

    assert result["ok"] is True
    payload = result.get("result")
    assert isinstance(payload, dict)
    assert payload.get("exists") is True

