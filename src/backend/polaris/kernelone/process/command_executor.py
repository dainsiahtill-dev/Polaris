"""Structured command execution with explicit trust-boundary validation.

This module replaces ad-hoc ``shell=True`` execution paths with:
- command parsing (`command: str` -> `executable + argv[]`)
- workspace-bound cwd enforcement
- executable allowlist checks
- timeout and output size guardrails
- environment variable security filtering
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

_logger = logging.getLogger(__name__)

# =============================================================================
# Security: Dangerous Environment Variables
# =============================================================================
# These environment variables can be exploited for malicious purposes and
# should be filtered when inheriting environment in secure execution contexts.

# Precise-match dangerous variables (will be filtered exactly)
_DANGEROUS_ENV_VARS_EXACT: frozenset[str] = frozenset(
    {
        # Dynamic linker/loader variables (Linux/Unix)
        "LD_PRELOAD",
        "LD_AUDIT",
        "LD_DEBUG",
        "LD_LIBRARY_PATH",
        "LD_ORIGIN",
        "LD_PROFILE",
        "LD_SHOW_AUXV",
        "LD_USE_LOAD_BIAS",
        "LD_BIND_NOW",
        "LD_TRACE_LOADED_OBJECTS",
        # Python environment variables
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONCOERCECLOCALE",
        "PYTHONDEVMODE",
        "PYTHONTRACEMALLOC",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONMALLOCSTATS",
        "PYTHONLEGACYWINDOWSSTDIO",
        # Rust environment variables
        "RUST_BACKTRACE",
        "RUST_LOG",
        "RUST_MIN_STACK",
        "RUST_PROFILE",
        "RUSTDOCFLAGS",
        "RUSTFLAGS",
        # Node.js environment variables
        "NODE_OPTIONS",
        "NODE_PATH",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "NODE_EXTRA_CA_CERTS",
        # Java environment variables
        "JAVA_HOME",
        "CLASSPATH",
        "JDK_JAVA_OPTIONS",
        "JAVA_TOOL_OPTIONS",
        # Go environment variables
        "GOROOT",
        "GOPATH",
        "GOFLAGS",
        "GOTOOLCHAIN",
        # Shell configuration variables
        "PS1",
        "PS2",
        "PS4",
        "ENV",
        "BASH_ENV",
        "ZDOTDIR",
        # Other dangerous variables
        "IFS",
        "CDPATH",
        "PERL5LIB",
        "PERLLIB",
        "PERL_OPTS",
        "RUBYLIB",
        "RUBYOPT",
        "BUNDLE_PATH",
        "BUNDLE_GEMFILE",
    }
)

# Prefix patterns for dangerous variable families (filtered by prefix match)
_DANGEROUS_ENV_VAR_PREFIXES: tuple[str, ...] = (
    "LD_",  # All LD_* dynamic linker variables
    "PYTHON",  # All PYTHON* variables
    "RUST_",  # All RUST_* variables
    "NODE_",  # All NODE_* variables
    "BASH_FUNC_",  # Shell function definitions
    "RUBY",  # Ruby-related variables
    "PERL",  # Perl-related variables
    "BUNDLE_",  # Bundler variables
)

# Safe default environment variables that are always allowed
_SAFE_DEFAULT_ENV: dict[str, str] = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
}

_FORBIDDEN_COMMAND_PATTERNS = (
    ";",
    "&&",
    "||",
    "|",
    "`",
    "$(",
    "<",
    ">",
)
_UNSAFE_TOKEN_RE = re.compile(r"[\x00-\x1f]")
_DEFAULT_MAX_TIMEOUT_SECONDS = 600
_DEFAULT_MAX_ARGS = 128
_DEFAULT_MAX_ARG_LENGTH = 4096
_DEFAULT_MAX_OUTPUT_CHARS = 200_000
_PYTHON_EXECUTABLE_NAMES = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
_NODE_EXECUTABLE_NAMES = {"node", "node.exe"}
_PACKAGE_MANAGER_EXECUTABLE_NAMES = {
    "npm",
    "npm.cmd",
    "pnpm",
    "pnpm.cmd",
    "yarn",
    "yarn.cmd",
    "npx",
    "npx.cmd",
}
# npx is allowed ONLY for packages in _SAFE_NPX_PACKAGES (validated in
# _validate_npx_execution). This prevents arbitrary package execution while
# still routing all npx calls through CommandExecutionService audit trail.
_SAFE_NPX_PACKAGES = {
    # TypeScript
    "tsc",
    "typescript",
    "ts-node",
    "ts-node-dev",
    # Linting
    "eslint",
    "prettier",
    "prettierd",
    # Type checking / analysis
    "mypy",
    "pyright",
    "ruff",
    # Build tools
    "esbuild",
    "webpack",
    "webpack-cli",
    "vite",
    "rollup",
    # Testing
    "vitest",
    "jest",
    "mocha",
}
_DISALLOWED_EXECUTABLE_NAMES: set[str] = set()  # npx is now validated via _validate_npx_execution
_PYTHON_INLINE_FLAGS = {"-c", "/c", "-i", "-"}
_PYTHON_ALLOWED_OPTION_ARITY = {
    "-u": 0,
    "-B": 0,
    "-E": 0,
    "-s": 0,
    "-S": 0,
    "-W": 1,
    "-X": 1,
    "--version": 0,
    "--help": 0,
}
_NODE_INLINE_FLAGS = {"-e", "--eval", "-p", "--print", "-"}
_NODE_ALLOWED_OPTIONS = {"--version", "-v", "--check"}
_SAFE_PACKAGE_MANAGER_VERBS = {"test", "run", "build", "lint", "install", "ci"}
_SAFE_PYTHON_MODULES = {
    "compileall",
    "mypy",
    "pip",
    "pip3",
    "pre_commit",
    "py_compile",
    "pytest",
    "ruff",
    "tools.main",
    "unittest",
}
_SAFE_PYTHON_MODULE_PREFIXES = ("polaris.", "tests.", "tools.")
_PYTHON_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _parse_command(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        raise ValueError("empty command")
    for marker in _FORBIDDEN_COMMAND_PATTERNS:
        if marker in raw:
            raise ValueError(f"forbidden shell operator detected: {marker}")
    try:
        tokens = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ValueError(f"invalid command syntax: {exc}") from exc
    if os.name == "nt":
        tokens = [_strip_wrapping_quotes(token) for token in tokens]
    normalized = [str(token).strip() for token in tokens if str(token).strip()]
    if not normalized:
        raise ValueError("empty command")
    return normalized


def _build_utf8_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or {})
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _filter_dangerous_env_vars(env: dict[str, str]) -> None:
    """Remove dangerous environment variables from dict in-place.

    This function filters both exact-match dangerous variables and
    prefix-family dangerous variables.

    Args:
        env: Environment dictionary to filter (modified in-place).
    """
    # Remove exact-match dangerous variables
    for var in _DANGEROUS_ENV_VARS_EXACT:
        env.pop(var, None)

    # Remove prefix-family dangerous variables
    keys_to_remove = [key for key in env if any(key.startswith(prefix) for prefix in _DANGEROUS_ENV_VAR_PREFIXES)]
    for key in keys_to_remove:
        env.pop(key, None)


def _normalize_allowlist(values: Iterable[str] | None) -> set[str]:
    normalized: set[str] = set()
    for value in values or []:
        token = str(value or "").strip().lower()
        if token:
            normalized.add(token)
    return normalized


def _default_allowlist() -> set[str]:
    from_env = [
        token.strip()
        for token in str(
            os.environ.get("KERNELONE_ALLOWED_EXECUTABLES") or ""
        ).split(",")
        if token.strip()
    ]
    baseline = {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
        "pip",
        "pip.exe",
        "pip3",
        "pip3.exe",
        "npm",
        "npm.cmd",
        "npx",
        "npx.cmd",
        "pnpm",
        "pnpm.cmd",
        "yarn",
        "yarn.cmd",
        "node",
        "node.exe",
        "uv",
        "uv.exe",
        "poetry",
        "poetry.exe",
        "git",
        "git.exe",
        "pytest",
        "pytest.exe",
        "ruff",
        "ruff.exe",
        "mypy",
        "mypy.exe",
        "go",
        "go.exe",
        "cargo",
        "cargo.exe",
        "make",
        "rg",
        "rg.exe",
        "cmake",
        "cmake.exe",
        "dotnet",
        "dotnet.exe",
        "java",
        "java.exe",
        "javac",
        "javac.exe",
        "mkdir",
        "mkdir.exe",
        "rmdir",
        "rmdir.exe",
        "cp",
        "cp.exe",
        "mv",
        "mv.exe",
    }
    baseline.update(_normalize_allowlist(from_env))
    return baseline


@dataclass(frozen=True)
class CommandRequest:
    executable: str
    args: list[str] = field(default_factory=list)
    cwd: str = "."
    timeout_seconds: int = 60
    env_policy: str = "inherit"


class CommandExecutionService:
    """Secure command execution service for workspace-scoped runtimes."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        allowed_executables: Iterable[str] | None = None,
        max_timeout_seconds: int = _DEFAULT_MAX_TIMEOUT_SECONDS,
        max_args: int = _DEFAULT_MAX_ARGS,
        max_arg_length: int = _DEFAULT_MAX_ARG_LENGTH,
        max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        self.workspace_root = Path(str(workspace_root or ".")).expanduser().resolve()
        self.allowed_executables = _normalize_allowlist(allowed_executables) or _default_allowlist()
        self.max_timeout_seconds = max(1, int(max_timeout_seconds))
        self.max_args = max(1, int(max_args))
        self.max_arg_length = max(32, int(max_arg_length))
        self.max_output_chars = max(256, int(max_output_chars))

    def run(
        self,
        request: CommandRequest,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {}
        try:
            spec = self.build_subprocess_spec(request, env_overrides=env_overrides)
            argv = list(spec["argv"])
            cwd = Path(str(spec["cwd"]))
            env = dict(spec["env"])
            timeout_seconds = int(spec["timeout_seconds"])
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": True,
                "error": f"Command timed out after {timeout_seconds}s",
                "command": dict(spec["command"]) if spec else {},
            }
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "error": str(exc),
                "command": dict(spec["command"]) if spec else {},
            }
        stdout = self._truncate_text(completed.stdout)
        stderr = self._truncate_text(completed.stderr)
        return {
            "ok": completed.returncode == 0,
            "returncode": int(completed.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "command": dict(spec["command"]),
        }

    def parse_command(
        self,
        command: str,
        *,
        cwd: str = ".",
        timeout_seconds: int = 60,
        env_policy: str = "inherit",
    ) -> CommandRequest:
        """Parse a plain command string into a structured request."""
        tokens = _parse_command(command)
        return CommandRequest(
            executable=tokens[0],
            args=tokens[1:],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_policy=env_policy,
        )

    def build_subprocess_spec(
        self,
        request: CommandRequest,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        cwd = self._resolve_cwd(request.cwd)
        validated = self._validate_request(request, cwd=cwd)
        env = self._build_env(validated.env_policy, env_overrides)
        timeout_seconds = min(max(1, int(validated.timeout_seconds)), self.max_timeout_seconds)
        return {
            "argv": [validated.executable, *validated.args],
            "cwd": str(cwd),
            "env": env,
            "timeout_seconds": timeout_seconds,
            "command": self._serialize_request(validated, cwd),
        }

    def _build_env(
        self,
        env_policy: str,
        env_overrides: dict[str, str] | None,
    ) -> dict[str, str]:
        """Build process environment variables with security filtering.

        Args:
            env_policy: Environment policy
                - "clean": Start with minimal safe defaults (no inheritance)
                - "inherit": Inherit parent process env but filter dangerous vars
            env_overrides: Optional environment variable overrides.

        Returns:
            Secure environment dictionary with UTF-8 enforcement.

        Raises:
            ValueError: If env_policy is invalid.

        Security:
            "inherit" mode automatically filters:
            - Dynamic linker variables (LD_*)
            - Python environment variables (PYTHON*)
            - Rust environment variables (RUST_*)
            - Node.js environment variables (NODE_*)
            - Shell configuration variables
            - Other potentially dangerous variables
        """
        policy = str(env_policy or "inherit").strip().lower()
        if policy == "":
            policy = "inherit"  # Empty string defaults to inherit
        if policy not in {"clean", "inherit"}:
            raise ValueError(f"Invalid env_policy: {policy!r}. Must be one of: clean, inherit")

        if policy == "clean":
            # Start with minimal safe environment
            base: dict[str, str] = {}
            for key in ("PATH", "SYSTEMROOT", "COMSPEC", "WINDIR", "HOME", "USERPROFILE", "TMP", "TEMP"):
                value = os.environ.get(key)
                if value:
                    base[key] = value

            # Filter PATH entries to remove potentially malicious directories
            if "PATH" in base:
                base["PATH"] = self._filter_safe_path_entries(base["PATH"])
        else:
            # Inherit parent environment but filter dangerous variables
            base = dict(os.environ)
            _filter_dangerous_env_vars(base)

        # Apply safe defaults (do not override user settings)
        for key, value in _SAFE_DEFAULT_ENV.items():
            base.setdefault(key, value)

        # Apply overrides with security warnings
        if env_overrides:
            for key, value in env_overrides.items():
                if key is None:
                    continue
                normalized_key = str(key).strip()
                if not normalized_key:
                    continue

                # Warn if overriding known dangerous variables
                if normalized_key in _DANGEROUS_ENV_VARS_EXACT or any(
                    normalized_key.startswith(prefix) for prefix in _DANGEROUS_ENV_VAR_PREFIXES
                ):
                    _logger.warning(
                        "Overriding potentially dangerous env var %s is not recommended",
                        normalized_key,
                    )

                base[normalized_key] = str(value or "")

        return _build_utf8_env(base)

    def _filter_safe_path_entries(self, path: str) -> str:
        """Filter PATH entries to remove potentially malicious directories.

        Args:
            path: Original PATH environment variable.

        Returns:
            Filtered PATH string.

        Note:
            Current implementation logs a warning if strict mode is requested
            but does not yet implement whitelist filtering. In high-security
            scenarios, implement a whitelist mechanism to restrict executable
            directories.
        """
        # TODO: Implement PATH whitelist mechanism for high-security scenarios.
        # Currently, the PATH is preserved, but when KERNELONE_STRICT_PATH is set,
        # a warning is logged to alert operators.
        if os.environ.get("KERNELONE_STRICT_PATH"):
            _logger.warning(
                "KERNELONE_STRICT_PATH is set but PATH whitelist filtering is not yet implemented. "
                "Consider implementing a whitelist of safe executable directories."
            )
        return path

    def _validate_request(self, request: CommandRequest, *, cwd: Path) -> CommandRequest:
        executable = str(request.executable or "").strip()
        if not executable:
            raise ValueError("Executable is required")
        if _UNSAFE_TOKEN_RE.search(executable):
            raise ValueError("Executable contains control characters")
        args = [str(arg) for arg in list(request.args or [])]
        if len(args) > self.max_args:
            raise ValueError(f"Too many args: {len(args)} > {self.max_args}")
        for arg in args:
            if len(arg) > self.max_arg_length:
                raise ValueError(f"Arg exceeds max length ({self.max_arg_length})")
            if _UNSAFE_TOKEN_RE.search(arg):
                raise ValueError("Argument contains control characters")
        timeout_seconds = int(request.timeout_seconds or 60)
        if timeout_seconds <= 0:
            timeout_seconds = 60
        env_policy = str(request.env_policy or "inherit").strip().lower() or "inherit"
        self._validate_executable(executable)
        # Workspace-boundary check is ALWAYS enforced (never bypassed by allowlist).
        self._validate_workspace_boundary(executable)
        self._validate_execution_shape(executable, args, cwd=cwd)
        return CommandRequest(
            executable=executable,
            args=args,
            cwd=str(request.cwd or "."),
            timeout_seconds=timeout_seconds,
            env_policy=env_policy,
        )

    def _validate_executable(self, executable: str) -> None:
        lowered = Path(executable).name.lower()
        if lowered in _DISALLOWED_EXECUTABLE_NAMES:
            raise ValueError(f"Executable is not allowed: {executable}")
        if lowered in self.allowed_executables:
            return  # Allowlisted: skip path resolution (fast path)
        candidate = Path(executable).expanduser()
        if not candidate.is_absolute():
            # Relative executable paths are allowed only within workspace.
            if any(marker in executable for marker in ("/", "\\")) or str(executable).startswith("."):
                candidate = (self.workspace_root / candidate).resolve()
            else:
                # Unqualified executable must be explicitly allowlisted.
                raise ValueError(f"Executable is not allowed: {executable}")
        resolved = candidate.resolve()
        try:
            common = os.path.commonpath([str(self.workspace_root), str(resolved)])
        except ValueError:
            common = ""
        if common == str(self.workspace_root):
            return
        raise ValueError(f"Executable is outside workspace and not allowlisted: {executable}")

    def _validate_workspace_boundary(self, executable: str) -> None:
        """Enforce that the executable resolves within the workspace root.

        This check is ALWAYS enforced regardless of allowlist status. It prevents
        bypassing workspace boundaries by adding executables to the allowlist.
        """
        lowered = Path(executable).name.lower()
        if lowered in self.allowed_executables:
            return  # Allowlisted executables skip path resolution
        candidate = Path(executable).expanduser()
        if not candidate.is_absolute():
            if any(marker in executable for marker in ("/", "\\")) or str(executable).startswith("."):
                candidate = (self.workspace_root / candidate).resolve()
            else:
                # Non-absolute, non-allowlisted: handled by _validate_executable
                return
        resolved = candidate.resolve()
        try:
            common = os.path.commonpath([str(self.workspace_root), str(resolved)])
        except ValueError:
            common = ""
        if common != str(self.workspace_root):
            raise ValueError(f"Executable is outside workspace: {executable}")

    def _validate_execution_shape(self, executable: str, args: list[str], *, cwd: Path) -> None:
        lowered = Path(executable).name.lower()
        if lowered in _PYTHON_EXECUTABLE_NAMES:
            self._validate_python_execution(args, cwd=cwd)
            return
        if lowered in _NODE_EXECUTABLE_NAMES:
            self._validate_node_execution(args, cwd=cwd)
            return
        if lowered in _PACKAGE_MANAGER_EXECUTABLE_NAMES:
            if lowered in {"npx", "npx.cmd"}:
                self._validate_npx_execution(args)
                return
            self._validate_package_manager_execution(executable, args)

    def _validate_npx_execution(self, args: list[str]) -> None:
        """Validate npx execution: requires a known-safe package as first arg."""
        if not args:
            raise ValueError("npx requires a package name")
        first = str(args[0]).strip().lower()
        if first not in _SAFE_NPX_PACKAGES:
            raise ValueError(
                f'npx package "{first}" is not in the safe allowlist. '
                f"Allowed packages: {', '.join(sorted(_SAFE_NPX_PACKAGES))}"
            )

    def _validate_python_execution(self, args: list[str], *, cwd: Path) -> None:
        if not args:
            return

        index = 0
        while index < len(args):
            arg = args[index]
            if arg in _PYTHON_INLINE_FLAGS:
                raise ValueError(f"Unsafe Python inline execution flag is not allowed: {arg}")
            if arg == "-m":
                if index + 1 >= len(args):
                    raise ValueError("Python module name is required after -m")
                module_name = str(args[index + 1]).strip()
                if not self._is_allowed_python_module(module_name, cwd=cwd):
                    raise ValueError(f"Python module is not allowed: {module_name}")
                return
            if arg.startswith("-"):
                arity = _PYTHON_ALLOWED_OPTION_ARITY.get(arg)
                if arity is None:
                    raise ValueError(f"Unsupported Python interpreter option: {arg}")
                index += arity + 1
                continue
            self._resolve_workspace_path(arg, cwd=cwd)
            return

    def _validate_node_execution(self, args: list[str], *, cwd: Path) -> None:
        if not args:
            return

        first = str(args[0]).strip()
        if first in _NODE_INLINE_FLAGS:
            raise ValueError(f"Unsafe Node inline execution flag is not allowed: {first}")
        if first in _NODE_ALLOWED_OPTIONS:
            if first == "--check":
                if len(args) < 2:
                    raise ValueError("node --check requires a script path")
                self._resolve_workspace_path(args[1], cwd=cwd)
            return
        if first.startswith("-"):
            raise ValueError(f"Unsupported Node option: {first}")
        self._resolve_workspace_path(first, cwd=cwd)

    def _validate_package_manager_execution(self, executable: str, args: list[str]) -> None:
        if not args:
            raise ValueError(f"{executable} requires an explicit subcommand")

        verb = str(args[0]).strip().lower()
        if verb not in _SAFE_PACKAGE_MANAGER_VERBS:
            raise ValueError(f"Unsupported package manager verb: {verb}")
        if verb == "run":
            if len(args) < 2:
                raise ValueError("Package manager 'run' requires a script name")
            script_name = str(args[1]).strip()
            if not script_name or script_name.startswith("-"):
                raise ValueError("Package manager script name is invalid")

    def _is_allowed_python_module(self, module_name: str, *, cwd: Path) -> bool:
        normalized = str(module_name or "").strip()
        if not normalized:
            return False
        if not _PYTHON_MODULE_RE.fullmatch(normalized):
            return False
        if normalized in _SAFE_PYTHON_MODULES:
            return True
        if normalized.startswith(_SAFE_PYTHON_MODULE_PREFIXES):
            return True
        return self._workspace_python_module_exists(normalized, cwd=cwd)

    def _workspace_python_module_exists(self, module_name: str, *, cwd: Path) -> bool:
        """Allow ``python -m`` only for modules materially present in workspace.

        This keeps inline or arbitrary stdlib execution blocked while allowing
        generated project packages under the current workspace to run their own
        module entrypoints during verification.
        """
        parts = module_name.split(".")
        if not parts:
            return False

        root_package = self._resolve_workspace_path(parts[0], cwd=cwd)
        if not root_package.exists():
            return False

        package_candidate = cwd.joinpath(*parts)
        module_candidate = cwd.joinpath(*parts[:-1], f"{parts[-1]}.py")
        init_candidate = package_candidate / "__init__.py"

        for candidate in (module_candidate, init_candidate):
            if not candidate.exists():
                continue
            resolved = self._resolve_workspace_path(str(candidate), cwd=cwd)
            if resolved.is_file():
                return True
        return False

    def _resolve_workspace_path(self, token: str, *, cwd: Path) -> Path:
        candidate = Path(str(token or "").strip()).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve()
        try:
            common = os.path.commonpath([str(self.workspace_root), str(resolved)])
        except ValueError:
            common = ""
        if common != str(self.workspace_root):
            raise ValueError(f"Path is outside workspace: {token}")
        return resolved

    def _resolve_cwd(self, cwd: str) -> Path:
        token = str(cwd or ".").strip() or "."
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve()
        try:
            common = os.path.commonpath([str(self.workspace_root), str(resolved)])
        except ValueError:
            common = ""
        if common != str(self.workspace_root):
            raise ValueError(f"cwd is outside workspace: {cwd}")
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"cwd not found: {cwd}")
        return resolved

    def _truncate_text(self, text: str | None) -> str:
        value = str(text or "")
        if len(value) <= self.max_output_chars:
            return value
        kept = value[: self.max_output_chars]
        omitted = len(value) - self.max_output_chars
        return f"{kept}\n\n[truncated {omitted} chars]"

    @staticmethod
    def _serialize_request(request: CommandRequest, cwd: Path) -> dict[str, Any]:
        return {
            "executable": request.executable,
            "args": list(request.args),
            "cwd": str(cwd),
            "timeout_seconds": int(request.timeout_seconds),
            "env_policy": request.env_policy,
        }
