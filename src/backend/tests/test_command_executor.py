"""Unit tests for CommandExecutionService environment variable security filtering."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from polaris.kernelone.process.command_executor import (
    CommandExecutionService,
    _filter_dangerous_env_vars,
    _DANGEROUS_ENV_VARS_EXACT,
    _DANGEROUS_ENV_VAR_PREFIXES,
    _SAFE_DEFAULT_ENV,
)


@pytest.fixture
def service(tmp_path: Path) -> CommandExecutionService:
    """Create a CommandExecutionService instance for testing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return CommandExecutionService(workspace_root=str(workspace))


class TestFilterDangerousEnvVars:
    """Tests for the _filter_dangerous_env_vars function."""

    def test_filters_ld_preload(self) -> None:
        """LD_PRELOAD should be removed."""
        env = {"LD_PRELOAD": "/malicious.so", "PATH": "/usr/bin"}
        _filter_dangerous_env_vars(env)
        assert "LD_PRELOAD" not in env
        assert "PATH" in env

    def test_filters_ld_library_path(self) -> None:
        """LD_LIBRARY_PATH should be removed."""
        env = {"LD_LIBRARY_PATH": "/malicious/lib", "HOME": "/home/user"}
        _filter_dangerous_env_vars(env)
        assert "LD_LIBRARY_PATH" not in env
        assert "HOME" in env

    def test_filters_pythonpath(self) -> None:
        """PYTHONPATH should be removed."""
        env = {"PYTHONPATH": "/malicious", "PATH": "/usr/bin"}
        _filter_dangerous_env_vars(env)
        assert "PYTHONPATH" not in env
        assert "PATH" in env

    def test_filters_node_options(self) -> None:
        """NODE_OPTIONS should be removed."""
        env = {"NODE_OPTIONS": "--inspect", "PATH": "/usr/bin"}
        _filter_dangerous_env_vars(env)
        assert "NODE_OPTIONS" not in env
        assert "PATH" in env

    def test_filters_rust_backtrace(self) -> None:
        """RUST_BACKTRACE should be removed."""
        env = {"RUST_BACKTRACE": "1", "PATH": "/usr/bin"}
        _filter_dangerous_env_vars(env)
        assert "RUST_BACKTRACE" not in env
        assert "PATH" in env

    def test_filters_bash_func_prefix(self) -> None:
        """BASH_FUNC_* variables should be removed."""
        env = {
            "BASH_FUNC_echo%%": "() { echo pwned; }",
            "BASH_FUNC_cat%%": "() { cat /etc/passwd; }",
            "HOME": "/home/user",
        }
        _filter_dangerous_env_vars(env)
        assert "BASH_FUNC_echo%%" not in env
        assert "BASH_FUNC_cat%%" not in env
        assert "HOME" in env

    def test_filters_ld_wildcard_prefix(self) -> None:
        """All LD_* variables should be removed."""
        env = {
            "LD_PRELOAD": "/malicious.so",
            "LD_AUDIT": "/evil.so",
            "LD_DEBUG": "all",
            "LD_ORIGIN": "/origin",
            "HOME": "/home/user",
        }
        _filter_dangerous_env_vars(env)
        assert "LD_PRELOAD" not in env
        assert "LD_AUDIT" not in env
        assert "LD_DEBUG" not in env
        assert "LD_ORIGIN" not in env
        assert "HOME" in env

    def test_filters_python_wildcard_prefix(self) -> None:
        """All PYTHON* variables should be removed."""
        env = {
            "PYTHONPATH": "/malicious",
            "PYTHONHOME": "/evil",
            "PYTHONSTARTUP": "/evil/startup.py",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": "/home/user",
        }
        _filter_dangerous_env_vars(env)
        assert "PYTHONPATH" not in env
        assert "PYTHONHOME" not in env
        assert "PYTHONSTARTUP" not in env
        assert "PYTHONDONTWRITEBYTECODE" not in env
        assert "HOME" in env

    def test_filters_shell_config_vars(self) -> None:
        """Shell configuration variables should be removed."""
        env = {
            "PS1": "$(whoami)>",
            "PS2": "> ",
            "PS4": "+ ",
            "ENV": "/etc/bashrc",
            "BASH_ENV": "/etc/bashrc",
            "HOME": "/home/user",
        }
        _filter_dangerous_env_vars(env)
        assert "PS1" not in env
        assert "PS2" not in env
        assert "PS4" not in env
        assert "ENV" not in env
        assert "BASH_ENV" not in env
        assert "HOME" in env

    def test_filters_other_dangerous_vars(self) -> None:
        """Other dangerous variables should be removed."""
        env = {
            "IFS": " \n\t",
            "CDPATH": "/etc",
            "JAVA_HOME": "/opt/java",
            "GOROOT": "/usr/local/go",
            "HOME": "/home/user",
        }
        _filter_dangerous_env_vars(env)
        assert "IFS" not in env
        assert "CDPATH" not in env
        assert "JAVA_HOME" not in env
        assert "GOROOT" not in env
        assert "HOME" in env

    def test_preserves_safe_vars(self) -> None:
        """Safe environment variables should be preserved."""
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/user",
            "USER": "testuser",
            "SHELL": "/bin/bash",
            "TERM": "xterm-256color",
            "TMP": "/tmp",
            "TEMP": "/tmp",
        }
        original = dict(env)
        _filter_dangerous_env_vars(env)
        assert env == original

    def test_handles_empty_dict(self) -> None:
        """Empty dictionary should be handled gracefully."""
        env: dict[str, str] = {}
        _filter_dangerous_env_vars(env)
        assert env == {}

    def test_preserves_non_dangerous_with_prefix(self) -> None:
        """Variables that start with prefix but are not dangerous should be preserved."""
        # HOME starts with nothing dangerous
        env = {"HOME": "/home/user", "HOSTNAME": "testhost"}
        _filter_dangerous_env_vars(env)
        assert "HOME" in env
        assert "HOSTNAME" in env


class TestBuildEnv:
    """Tests for the _build_env method."""

    def test_inherit_filters_dangerous_vars(self, service: CommandExecutionService) -> None:
        """verify inherit mode filters dangerous variables."""
        dangerous_vars = {
            "SAFE_VAR": "safe_value",
            "LD_PRELOAD": "/malicious.so",
            "LD_LIBRARY_PATH": "/malicious/lib",
            "PYTHONPATH": "/malicious",
            "NODE_OPTIONS": "--inspect",
            "RUST_BACKTRACE": "1",
            "JAVA_HOME": "/evil/java",
        }
        with patch.dict("os.environ", dangerous_vars, clear=False):
            env = service._build_env("inherit", None)

        # Safe variables should be preserved
        assert env["SAFE_VAR"] == "safe_value"
        # Dangerous variables should be filtered
        assert "LD_PRELOAD" not in env
        assert "LD_LIBRARY_PATH" not in env
        assert "PYTHONPATH" not in env
        assert "NODE_OPTIONS" not in env
        assert "RUST_BACKTRACE" not in env
        assert "JAVA_HOME" not in env
        # Safe defaults should be present
        assert env["PYTHONUTF8"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_clean_mode_no_inherit(self, service: CommandExecutionService) -> None:
        """verify clean mode does not inherit any variables."""
        with patch.dict("os.environ", {"EXISTING_VAR": "value", "PATH": "/usr/bin"}, clear=False):
            env = service._build_env("clean", None)

        assert "EXISTING_VAR" not in env
        # Some essential system vars are still allowed
        assert env.get("PATH") is not None
        # Safe defaults should be present
        assert env["PYTHONUTF8"] == "1"

    def test_inherit_preserves_path(self, service: CommandExecutionService) -> None:
        """verify PATH is preserved in inherit mode."""
        with patch.dict("os.environ", {"PATH": "/usr/bin:/bin", "HOME": "/home/user"}, clear=False):
            env = service._build_env("inherit", None)

        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/user"

    def test_env_overrides_applied(self, service: CommandExecutionService) -> None:
        """verify environment overrides are applied."""
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=False):
            env = service._build_env("clean", {"CUSTOM_VAR": "custom_value"})

        assert env["CUSTOM_VAR"] == "custom_value"
        assert env["PYTHONUTF8"] == "1"

    def test_dangerous_var_override_warning(self, service: CommandExecutionService) -> None:
        """verify overriding dangerous variables produces a warning."""
        with patch("polaris.kernelone.process.command_executor._logger.warning") as mock_warning:
            env = service._build_env("clean", {"PYTHONPATH": "/test"})

        mock_warning.assert_called_once()
        # The second argument (index 1) is the actual variable name
        call_args = mock_warning.call_args[0]
        assert len(call_args) >= 2
        assert call_args[1] == "PYTHONPATH"

    def test_safe_default_override_with_inherit(self, service: CommandExecutionService) -> None:
        """verify safe defaults are set but can be overridden."""
        with patch.dict("os.environ", {"HOME": "/home/user"}, clear=False):
            env = service._build_env("inherit", {"LANG": "zh_CN.UTF-8"})

        # Safe defaults should be set, override should be preserved
        assert env["LANG"] == "zh_CN.UTF-8"

    def test_invalid_policy_raises(self, service: CommandExecutionService) -> None:
        """verify invalid env_policy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            service._build_env("invalid_policy", None)

        assert "invalid_policy" in str(exc_info.value).lower()

    def test_none_policy_defaults_to_inherit(self, service: CommandExecutionService) -> None:
        """verify None policy defaults to inherit."""
        with patch.dict("os.environ", {"SAFE_VAR": "value", "LD_PRELOAD": "/evil"}, clear=False):
            env = service._build_env(None, None)

        assert env["SAFE_VAR"] == "value"
        assert "LD_PRELOAD" not in env

    def test_empty_string_policy_defaults_to_inherit(self, service: CommandExecutionService) -> None:
        """verify empty string policy defaults to inherit."""
        with patch.dict("os.environ", {"SAFE_VAR": "value", "LD_PRELOAD": "/evil"}, clear=False):
            env = service._build_env("  ", None)

        assert env["SAFE_VAR"] == "value"
        assert "LD_PRELOAD" not in env

    def test_case_insensitive_policy(self, service: CommandExecutionService) -> None:
        """verify policy matching is case insensitive."""
        with patch.dict("os.environ", {"HOME": "/home/user"}, clear=False):
            # Should not raise
            env_lower = service._build_env("clean", None)
            env_upper = service._build_env("CLEAN", None)
            env_mixed = service._build_env("Clean", None)

        # All should work the same way
        assert env_lower.get("HOME") is not None
        assert env_upper.get("HOME") is not None
        assert env_mixed.get("HOME") is not None


class TestBuildSubprocessSpec:
    """Tests for build_subprocess_spec integration."""

    def test_spec_contains_filtered_env(self, service: CommandExecutionService) -> None:
        """verify subprocess spec contains filtered environment."""
        # Use python executable which is in the allowlist
        request = CommandExecutionService.parse_command(
            service, "python --version", env_policy="inherit"
        )

        with patch.dict("os.environ", {"PATH": "/usr/bin", "LD_PRELOAD": "/evil"}, clear=False):
            spec = service.build_subprocess_spec(request)

        assert "env" in spec
        assert "LD_PRELOAD" not in spec["env"]
        assert spec["env"]["PYTHONUTF8"] == "1"


class TestConstants:
    """Tests for security constant definitions."""

    def test_dangerous_vars_exact_is_frozenset(self) -> None:
        """verify _DANGEROUS_ENV_VARS_EXACT is a frozenset."""
        assert isinstance(_DANGEROUS_ENV_VARS_EXACT, frozenset)

    def test_dangerous_vars_prefixes_is_tuple(self) -> None:
        """verify _DANGEROUS_ENV_VAR_PREFIXES is a tuple."""
        assert isinstance(_DANGEROUS_ENV_VAR_PREFIXES, tuple)

    def test_safe_default_env_is_dict(self) -> None:
        """verify _SAFE_DEFAULT_ENV is a dict."""
        assert isinstance(_SAFE_DEFAULT_ENV, dict)

    def test_safe_default_env_has_utf8_vars(self) -> None:
        """verify safe defaults include UTF-8 enforcement."""
        assert "PYTHONUTF8" in _SAFE_DEFAULT_ENV
        assert "PYTHONIOENCODING" in _SAFE_DEFAULT_ENV
        assert _SAFE_DEFAULT_ENV["PYTHONUTF8"] == "1"
        assert _SAFE_DEFAULT_ENV["PYTHONIOENCODING"] == "utf-8"

    def test_dangerous_vars_contains_common_exploits(self) -> None:
        """verify constants include common exploitation vectors."""
        assert "LD_PRELOAD" in _DANGEROUS_ENV_VARS_EXACT
        assert "PYTHONPATH" in _DANGEROUS_ENV_VARS_EXACT
        assert "NODE_OPTIONS" in _DANGEROUS_ENV_VARS_EXACT
        assert "BASH_FUNC_" in _DANGEROUS_ENV_VAR_PREFIXES
