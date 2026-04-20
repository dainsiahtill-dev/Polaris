from __future__ import annotations

from pathlib import Path

from polaris.cells.director.execution.internal.patch_apply_engine import (
    apply_all_operations,
    apply_operations_strict,
    parse_all_operations,
)


def test_parse_and_apply_search_replace(tmp_path: Path) -> None:
    target = tmp_path / "src" / "role_agent_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def value() -> int:\n    return 1\n", encoding="utf-8")

    payload = (
        "PATCH_FILE: src/role_agent_service.py\n"
        "<<<<<<< SEARCH\n"
        "return 1\n"
        "=======\n"
        "return 2\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )
    parsed = parse_all_operations(payload)
    assert len(parsed) == 1

    result = apply_all_operations(payload, str(tmp_path))
    assert result.success is True
    assert "src/role_agent_service.py" in result.changed_files
    assert "return 2" in target.read_text(encoding="utf-8")


def test_apply_full_file_and_delete(tmp_path: Path) -> None:
    stale = tmp_path / "src" / "stale.py"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("x = 1\n", encoding="utf-8")

    payload = (
        "FILE: src/new_module.py\n"
        "def ping() -> str:\n"
        "    return \"ok\"\n"
        "END FILE\n"
        "DELETE_FILE: src/stale.py\n"
    )
    result = apply_all_operations(payload, str(tmp_path))
    assert result.success is True
    assert (tmp_path / "src" / "new_module.py").is_file()
    assert not stale.exists()


def test_search_miss_fails_in_strict_mode(tmp_path: Path) -> None:
    target = tmp_path / "src" / "fastapi_entrypoint.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 'old'\n", encoding="utf-8")

    payload = (
        "PATCH_FILE: src/fastapi_entrypoint.py\n"
        "<<<<<<< SEARCH\n"
        "return 'missing'\n"
        "=======\n"
        "def run():\n"
        "    return 'new'\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )
    result = apply_all_operations(payload, str(tmp_path))
    assert result.success is False
    assert "src/fastapi_entrypoint.py" not in result.changed_files
    assert "old" in target.read_text(encoding="utf-8")


def test_patch_file_direct_content_falls_back_to_full_file(tmp_path: Path) -> None:
    payload = (
        "PATCH_FILE: src/app.py\n"
        "def app() -> str:\n"
        "    return 'ok'\n"
        "END PATCH_FILE\n"
    )
    result = apply_all_operations(payload, str(tmp_path))
    assert result.success is True
    target = tmp_path / "src" / "app.py"
    assert target.is_file()
    assert "return 'ok'" in target.read_text(encoding="utf-8")


def test_search_replace_supports_fuzzy_blank_line_match(tmp_path: Path) -> None:
    target = tmp_path / "tui_runtime.md"
    target.write_text(
        "---\n<section>\n  body   \n",
        encoding="utf-8",
    )
    payload = (
        "PATCH_FILE: tui_runtime.md\n"
        "<<<<<<< SEARCH\n"
        "---\n"
        "<section>\n"
        "body\n"
        "=======\n"
        "---\n"
        "<section>\n"
        "body\n"
        "<!-- touched -->\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )
    report = apply_operations_strict(
        payload,
        str(tmp_path),
        allow_fuzzy_match=True,
    )
    assert report.success is True
    assert "<!-- touched -->" in target.read_text(encoding="utf-8")
