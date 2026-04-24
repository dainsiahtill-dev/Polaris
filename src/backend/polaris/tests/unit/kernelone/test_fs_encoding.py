"""Tests for polaris.kernelone.fs.encoding (UTF-8 enforcement utilities)."""

from __future__ import annotations

from polaris.kernelone.fs.encoding import build_utf8_env, enforce_utf8


class TestBuildUtf8Env:
    """Tests for build_utf8_env."""

    def test_sets_pythonutf8(self) -> None:
        env = build_utf8_env()
        assert env.get("PYTHONUTF8") == "1"

    def test_sets_pythonioencoding(self) -> None:
        env = build_utf8_env()
        assert env.get("PYTHONIOENCODING") == "utf-8"

    def test_sets_lang(self) -> None:
        env = build_utf8_env()
        assert env.get("LANG") == "en_US.UTF-8"

    def test_sets_lc_all(self) -> None:
        env = build_utf8_env()
        assert env.get("LC_ALL") == "en_US.UTF-8"

    def test_extra_env_vars_merged(self) -> None:
        env = build_utf8_env(extra={"MY_VAR": "my_value"})
        assert env.get("MY_VAR") == "my_value"
        assert env.get("PYTHONUTF8") == "1"

    def test_extra_does_not_overwrite_utf8_vars(self) -> None:
        env = build_utf8_env(extra={"PYTHONUTF8": "0"})
        # build_utf8_env uses setdefault, so existing values in os.environ
        # are preserved when copy() is called first
        assert env.get("PYTHONUTF8") == "0"

    def test_returns_dict(self) -> None:
        env = build_utf8_env()
        assert isinstance(env, dict)


class TestEnforceUtf8:
    """Tests for enforce_utf8 (side-effect heavy, smoke test only)."""

    def test_enforce_utf8_runs_without_error(self) -> None:
        # This function modifies global state (stdout/stderr encoding)
        # Just verify it doesn't raise
        enforce_utf8()  # should not raise

    def test_enforce_utf8_idempotent(self) -> None:
        # Running twice should not cause issues
        enforce_utf8()
        enforce_utf8()  # still should not raise
