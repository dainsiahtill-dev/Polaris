"""Unit tests for polaris.kernelone.fs utilities: encoding, fsync_mode, tree.

Covers:
- enforce_utf8: stdout/stderr reconfiguration, environment variables
- build_utf8_env: base env + extra overrides
- resolve_fsync_mode: normalization, env fallback, disabled tokens
- is_fsync_enabled: strict vs disabled modes
- format_workspace_tree: normal formatting, limits, exclusions, errors
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from polaris.kernelone.fs.encoding import build_utf8_env, enforce_utf8
from polaris.kernelone.fs.fsync_mode import (
    _DISABLED_TOKENS,
    IO_FSYNC_ENV,
    is_fsync_enabled,
    resolve_fsync_mode,
)
from polaris.kernelone.fs.tree import format_workspace_tree

# -----------------------------------------------------------------------------
# encoding.py
# -----------------------------------------------------------------------------


def test_enforce_utf8_sets_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """enforce_utf8 sets PYTHONUTF8, PYTHONIOENCODING, LANG, LC_ALL."""
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)

    enforce_utf8()

    assert os.environ.get("PYTHONUTF8") == "1"
    assert os.environ.get("PYTHONIOENCODING") == "utf-8"
    assert os.environ.get("LANG") == "en_US.UTF-8"
    assert os.environ.get("LC_ALL") == "en_US.UTF-8"


def test_enforce_utf8_does_not_override_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """enforce_utf8 uses setdefault and preserves existing values."""
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")

    enforce_utf8()

    assert os.environ.get("LANG") == "ja_JP.UTF-8"
    assert os.environ.get("LC_ALL") == "ja_JP.UTF-8"


def test_enforce_utf8_handles_stdout_reconfigure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """RuntimeError/ValueError during stdout reconfigure is swallowed."""
    fake_stdout = type("FakeStdout", (), {})()
    fake_stdout.reconfigure = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[assignment]
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    # Should not raise
    enforce_utf8()


def test_build_utf8_env_returns_dict_with_defaults() -> None:
    """build_utf8_env returns a dict with UTF-8 defaults."""
    env = build_utf8_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_ALL"] == "en_US.UTF-8"


def test_build_utf8_env_merges_extra() -> None:
    """Extra keys are merged into the returned environment."""
    env = build_utf8_env(extra={"MY_VAR": "42"})
    assert env["MY_VAR"] == "42"
    assert env["PYTHONUTF8"] == "1"


def test_build_utf8_env_extra_overrides() -> None:
    """Extra keys can override the defaults."""
    env = build_utf8_env(extra={"LANG": "fr_FR.UTF-8"})
    assert env["LANG"] == "fr_FR.UTF-8"


# -----------------------------------------------------------------------------
# fsync_mode.py
# -----------------------------------------------------------------------------


def test_resolve_fsync_mode_explicit_value() -> None:
    """Explicit raw_value is normalized to lowercase."""
    assert resolve_fsync_mode("STRICT") == "strict"
    assert resolve_fsync_mode("Relaxed") == "relaxed"
    assert resolve_fsync_mode("  SKIP  ") == "skip"


def test_resolve_fsync_mode_defaults_to_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no env is set and no explicit value, defaults to strict."""
    monkeypatch.delenv(IO_FSYNC_ENV, raising=False)
    # Also clear the fallback env name if different
    monkeypatch.delenv("KERNELONE_IO_FSYNC_MODE", raising=False)
    assert resolve_fsync_mode() == "strict"


def test_resolve_fsync_mode_empty_string_defaults() -> None:
    """Empty string resolves to strict."""
    assert resolve_fsync_mode("") == "strict"


def test_is_fsync_enabled_for_strict() -> None:
    """strict mode enables fsync."""
    assert is_fsync_enabled("strict") is True
    assert is_fsync_enabled("STRICT") is True


def test_is_fsync_enabled_for_all_disabled_tokens() -> None:
    """Every disabled token disables fsync."""
    for token in _DISABLED_TOKENS:
        assert is_fsync_enabled(token) is False, f"token {token!r} should disable fsync"


def test_is_fsync_enabled_case_insensitive() -> None:
    """Disabled tokens are case-insensitive."""
    assert is_fsync_enabled("FALSE") is False
    assert is_fsync_enabled("NO") is False
    assert is_fsync_enabled("OFF") is False


# -----------------------------------------------------------------------------
# tree.py
# -----------------------------------------------------------------------------


def test_format_workspace_tree_empty_dir(tmp_path: Path) -> None:
    """Empty workspace returns just '.'."""
    result = format_workspace_tree(tmp_path)
    assert result == "."


def test_format_workspace_tree_with_files(tmp_path: Path) -> None:
    """Workspace with files formats correctly."""
    (tmp_path / "README.md").write_text("# Hello", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    result = format_workspace_tree(tmp_path)
    assert result.startswith(".")
    assert "README.md" in result
    assert "main.py" in result


def test_format_workspace_tree_with_dirs(tmp_path: Path) -> None:
    """Directories are shown with trailing slash and contents."""
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "app.py").write_text("pass", encoding="utf-8")
    result = format_workspace_tree(tmp_path)
    assert "src/" in result
    assert "app.py" in result


def test_format_workspace_tree_excludes_hidden_by_default(tmp_path: Path) -> None:
    """Hidden files are excluded by default."""
    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").write_text("y", encoding="utf-8")
    result = format_workspace_tree(tmp_path)
    assert "visible.txt" in result
    assert ".hidden" not in result


def test_format_workspace_tree_excludes_pycache(tmp_path: Path) -> None:
    """__pycache__ is excluded by default."""
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "foo.cpython-311.pyc").write_bytes(b"x")
    result = format_workspace_tree(tmp_path)
    assert "__pycache__" not in result


def test_format_workspace_tree_max_files_limit(tmp_path: Path) -> None:
    """max_files limits root-level file display."""
    for i in range(5):
        (tmp_path / f"file{i}.txt").write_text("x", encoding="utf-8")
    result = format_workspace_tree(tmp_path, max_files=2)
    # Only 2 files shown
    lines = result.splitlines()
    file_lines = [ln for ln in lines if ln.strip().endswith(".txt")]
    assert len(file_lines) <= 2


def test_format_workspace_tree_max_dirs_limit(tmp_path: Path) -> None:
    """max_dirs limits root-level directory display."""
    for i in range(5):
        (tmp_path / f"dir{i}").mkdir()
    result = format_workspace_tree(tmp_path, max_dirs=2)
    lines = result.splitlines()
    dir_lines = [ln for ln in lines if ln.strip().endswith("/")]
    assert len(dir_lines) <= 2


def test_format_workspace_tree_max_sub_items(tmp_path: Path) -> None:
    """max_sub_items limits items shown inside each directory."""
    sub = tmp_path / "pkg"
    sub.mkdir()
    for i in range(5):
        (sub / f"mod{i}.py").write_text("pass", encoding="utf-8")
    result = format_workspace_tree(tmp_path, max_sub_items=2)
    assert "pkg/" in result
    # Should show ellipsis for remaining items
    assert any("more" in ln for ln in result.splitlines())


def test_format_workspace_tree_permission_error_returns_dot(tmp_path: Path) -> None:
    """PermissionError returns '.' gracefully."""
    with patch("pathlib.Path.iterdir", side_effect=PermissionError("denied")):
        result = format_workspace_tree(tmp_path)
        assert result == "."


def test_format_workspace_tree_oserror_returns_dot(tmp_path: Path) -> None:
    """OSError returns '.' gracefully."""
    with patch("pathlib.Path.iterdir", side_effect=OSError("fail")):
        result = format_workspace_tree(tmp_path)
        assert result == "."


def test_format_workspace_tree_runtime_error_returns_dot(tmp_path: Path) -> None:
    """RuntimeError returns '.' gracefully."""
    with patch("pathlib.Path.iterdir", side_effect=RuntimeError("boom")):
        result = format_workspace_tree(tmp_path)
        assert result == "."


def test_format_workspace_tree_subdir_permission_error(tmp_path: Path) -> None:
    """PermissionError inside a subdirectory shows [permission denied]."""
    sub = tmp_path / "locked"
    sub.mkdir()
    # Patch iterdir on the sub directory only
    original_iterdir = Path.iterdir

    def _patched_iterdir(self: Path) -> Any:
        if str(self) == str(sub):
            raise PermissionError("denied")
        return original_iterdir(self)

    with patch("pathlib.Path.iterdir", _patched_iterdir):
        result = format_workspace_tree(tmp_path)
        assert "locked/" in result
        assert "[permission denied]" in result


def test_format_workspace_tree_includes_hidden_when_configured(tmp_path: Path) -> None:
    """exclude_hidden=False includes hidden files."""
    (tmp_path / ".hidden").write_text("y", encoding="utf-8")
    result = format_workspace_tree(tmp_path, exclude_hidden=False)
    assert ".hidden" in result
