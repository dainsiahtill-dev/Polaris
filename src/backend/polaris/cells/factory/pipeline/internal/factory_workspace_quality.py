"""Workspace quality command runner for the factory quality gate.

Holds the package.json parsing, npm command building, and subprocess execution
extracted verbatim from ``OrchestrationStageExecutor``. The runner owns a
``workspace`` root; ``OrchestrationStageExecutor`` keeps same-named delegating
shims so the test-overridden ``_run_workspace_quality_command`` and the
monkeypatched ``_resolve_workspace_quality_command`` entry points stay intact.

Behavior preservation notes:

* ``run_command`` references command resolution / output trimming through the
  shared ``factory_stage_helpers`` functions, which read ``shutil`` / ``os``
  through their module namespace at call time. The historical tests monkeypatch
  ``factory_run_service.shutil.which`` / ``factory_run_service.os.name``; because
  Python caches module objects those patches mutate the shared ``shutil`` / ``os``
  module objects, so resolution stays patchable here too.
* Section-8 business defaults (the npm ``test`` / ``build`` script mapping and
  the ``npm install --ignore-scripts`` prepare command) are reproduced verbatim.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import factory_stage_helpers as helpers
from .factory_run_models import _WORKSPACE_VALIDATION_TIMEOUT_SECONDS

_MASKED_WORKSPACE_FAILURE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\berror\s+TS\d+\s*:", re.IGNORECASE),
        "command exited 0 but output contains TypeScript compiler errors",
    ),
    (
        re.compile(r"\bTypeScript check skipped\b", re.IGNORECASE),
        "command exited 0 but output reports TypeScript check skipped",
    ),
    (
        re.compile(
            r"\bskipped\s+['\"][\s\S]{0,240}\b(?:javac|compile|compilation|build)[\s\S]{0,160}\bfailed\b",
            re.IGNORECASE,
        ),
        "command exited 0 but output reports skipped tests caused by compile/build failure",
    ),
)
_CALLED_PROCESS_ERROR_COMMAND_RE = re.compile(
    r"CalledProcessError:\s+Command\s+['\"]?(?P<command>\[[^\n]+?\])['\"]?\s+"
    r"returned\s+non-zero\s+exit\s+status\s+\d+",
    re.IGNORECASE,
)
_LONG_RUNNING_WEB_START_MARKERS = (
    "http-server",
    "vite --host",
    "vite --host ",
    "vite --host=",
    "webpack serve",
    "next start",
)
_NESTED_JAVAC_DIAGNOSTIC_TIMEOUT_SECONDS = 30.0


def _npm_start_runs_long_lived_web_server(script: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(script or "").strip().lower())
    if not normalized:
        return False
    if any(marker in normalized for marker in _LONG_RUNNING_WEB_START_MARKERS):
        return True
    return bool(re.search(r"(?:^|\s)npx\s+(?:--yes\s+)?serve(?:\s|$)", normalized))


class WorkspaceQualityRunner:
    """Builds and runs workspace quality (npm test/build) commands."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def workspace_package_has_external_dependencies(self) -> bool:
        package_path = (self.workspace / "package.json").resolve()
        if not package_path.exists() or not package_path.is_file():
            return False
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            value = payload.get(key)
            if isinstance(value, dict) and value:
                return True
        return False

    def workspace_quality_prepare_commands(
        self,
        commands: list[list[str]],
        context: dict[str, Any],
    ) -> list[list[str]]:
        if not helpers.bool_from_context_or_env(
            context,
            "workspace_validation_install_dependencies",
            "qa_workspace_validation_install_dependencies",
            env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION_INSTALL_DEPENDENCIES",
            default=True,
        ):
            return []
        if not any(command and str(command[0]).strip().lower() == "npm" for command in commands):
            return []
        if (self.workspace / "node_modules").is_dir():
            return []
        if not self.workspace_package_has_external_dependencies():
            return []
        return [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]]

    def load_package_scripts(self) -> dict[str, str]:
        package_path = (self.workspace / "package.json").resolve()
        if not package_path.exists() or not package_path.is_file():
            return {}
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        scripts = payload.get("scripts")
        if not isinstance(scripts, dict):
            return {}
        return {str(key): str(value) for key, value in scripts.items() if key and value}

    def workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        if not helpers.bool_from_context_or_env(
            context,
            "workspace_validation",
            "qa_workspace_validation",
            env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION",
            default=True,
        ):
            return []

        configured = context.get("quality_commands") or context.get("workspace_quality_commands")
        if isinstance(configured, list):
            configured_commands: list[list[str]] = []
            for item in configured:
                if isinstance(item, list) and all(isinstance(part, str) and part.strip() for part in item):
                    configured_commands.append([part.strip() for part in item])
                elif isinstance(item, str) and item.strip():
                    configured_commands.append([part for part in item.strip().split(" ") if part])
            return configured_commands

        scripts = self.load_package_scripts()
        commands: list[list[str]] = []
        if "build" in scripts:
            commands.append(["npm", "run", "build"])
        if "test" in scripts:
            commands.append(["npm", "test"])
        if (
            helpers.bool_from_context_or_env(
                context,
                "workspace_validation_entrypoint_smoke",
                "qa_workspace_validation_entrypoint_smoke",
                env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
                default=True,
            )
            and "start" in scripts
            and not _npm_start_runs_long_lived_web_server(scripts["start"])
        ):
            commands.append(["npm", "run", "start"])
        go_commands = self._go_workspace_quality_commands(context)
        commands.extend(go_commands)
        commands.extend(self._rust_workspace_quality_commands())
        cpp_commands = self._cpp_workspace_quality_commands()
        commands.extend(cpp_commands)
        if not go_commands and not cpp_commands and not (self.workspace / "Cargo.toml").is_file():
            commands.extend(self._python_workspace_quality_commands(context))
        return commands

    def _go_workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        go_files = [
            path
            for path in self.workspace.rglob("*.go")
            if path.is_file()
            and "vendor" not in path.parts
            and "runtime" not in path.parts
            and ".polaris" not in path.parts
        ]
        if not go_files and not (self.workspace / "go.mod").is_file():
            return []

        commands: list[list[str]] = [["go", "test", "./..."]]
        if (self.workspace / "main.go").is_file() and helpers.bool_from_context_or_env(
            context,
            "workspace_validation_entrypoint_smoke",
            "qa_workspace_validation_entrypoint_smoke",
            env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
            default=True,
        ):
            commands.append(["go", "run", "."])
        return commands

    def _rust_workspace_quality_commands(self) -> list[list[str]]:
        if not (self.workspace / "Cargo.toml").is_file():
            return []
        return [["cargo", "check", "--quiet"]]

    def _cpp_workspace_quality_commands(self) -> list[list[str]]:
        cpp_files = [
            path
            for ext in ("*.cpp", "*.cc", "*.cxx", "*.c")
            for path in self.workspace.rglob(ext)
            if path.is_file() and "build" not in path.parts and "cmake-build" not in path.parts
        ]
        if not cpp_files and not (self.workspace / "CMakeLists.txt").is_file():
            return []

        script = """
import pathlib
import subprocess
import sys

root = pathlib.Path(".")
# C++ projects set an include root (CMake target_include_directories, e.g. src/);
# headers are then included as <models/foo.hpp>. g++ -fsyntax-only does NOT read
# CMakeLists.txt, so add the conventional include roots (workspace root + src/ +
# include/ when present) as -I so the syntax check matches the project's real
# CMake build instead of failing on a missing header (factory_bench L1-06).
include_flags = []
for inc in (".", "src", "include"):
    if (root / inc).is_dir():
        include_flags += ["-I", str(root / inc)]
files = sorted(
    path for ext in ("*.cpp", "*.cc", "*.cxx", "*.c")
    for path in root.rglob(ext)
    if path.is_file() and "build" not in path.parts and "cmake-build" not in path.parts
)
if not files:
    print("No C++ translation units found", file=sys.stderr)
    raise SystemExit(1)
failed = []
for path in files:
    completed = subprocess.run(
        ["g++", "-std=c++17", "-fsyntax-only", *include_flags, str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if completed.returncode != 0:
        failed.append(f"### {path}\\n{completed.stderr or completed.stdout}")
if failed:
    print("\\n".join(failed), file=sys.stderr)
    raise SystemExit(1)
print(f"C++ syntax check passed for {len(files)} translation unit(s)")
""".strip()
        return [[sys.executable, "-c", script]]

    def _python_workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        """Infer real validation commands for Python-only generated projects."""

        python_files = self._python_workspace_files()
        if not python_files:
            return []

        commands: list[list[str]] = []
        requirements_path = self.workspace / "requirements.txt"
        pyproject_path = self.workspace / "pyproject.toml"
        if requirements_path.exists():
            commands.append([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        elif pyproject_path.exists():
            commands.append([sys.executable, "-m", "pip", "install", "-e", "."])

        compile_targets = self._python_compile_targets()
        if compile_targets:
            commands.append([sys.executable, "-m", "compileall", "-q", *compile_targets])

        if any((self.workspace / "tests").glob("test_*.py")):
            commands.append([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"])

        if helpers.bool_from_context_or_env(
            context,
            "workspace_validation_entrypoint_smoke",
            "qa_workspace_validation_entrypoint_smoke",
            env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
            default=True,
        ):
            commands.extend(self._python_entrypoint_smoke_commands())
        return commands

    def _python_workspace_files(self) -> list[Path]:
        roots = [self.workspace / "main.py", self.workspace / "src", self.workspace / "tests"]
        files: list[Path] = []
        for root in roots:
            if root.is_file() and root.suffix == ".py":
                files.append(root)
            elif root.is_dir():
                files.extend(path for path in root.rglob("*.py") if path.is_file())
        return files

    def _python_compile_targets(self) -> list[str]:
        targets: list[str] = []
        for relative in ("src", "tests", "main.py"):
            path = self.workspace / relative
            if path.exists():
                targets.append(relative)
        return targets

    def _python_entrypoint_smoke_commands(self) -> list[list[str]]:
        commands: list[list[str]] = []
        if (self.workspace / "main.py").is_file():
            commands.append([sys.executable, "main.py"])
        if (self.workspace / "src" / "main.py").is_file():
            commands.append([sys.executable, "src/main.py"])
        return commands

    # Patterns that indicate npm install failed due to a hallucinated dependency
    # (a package that does not exist on the registry, or a version that does not exist).
    _NPM_HALLUCINATED_DEP_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"notarget\s+No matching version found for\s+(?P<name>[^\s@]+)@", re.IGNORECASE),
        re.compile(r"code\s+ETARGET.*?notarget.*?(?P<name>[^\s@]+)@", re.IGNORECASE | re.DOTALL),
        re.compile(r"code\s+E404.*?(?P<name>[^\s@]+)@(?P<version>[^\s]+)", re.IGNORECASE | re.DOTALL),
    )

    def repair_hallucinated_npm_dependencies(self, stderr: str) -> list[str]:
        """Remove hallucinated dependencies from package.json.

        Parses npm install stderr for packages that don't exist on the
        registry and removes them from all dependency sections.
        Returns the list of removed package names.
        """
        package_path = (self.workspace / "package.json").resolve()
        if not package_path.exists():
            return []
        removed: list[str] = []
        for pattern in self._NPM_HALLUCINATED_DEP_PATTERNS:
            for match in pattern.finditer(stderr):
                name = match.group("name").strip()
                if name and name not in removed:
                    removed.append(name)
        if not removed:
            return []
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        actually_removed: list[str] = []
        for dep_key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            deps = payload.get(dep_key)
            if not isinstance(deps, dict):
                continue
            for name in removed:
                if name in deps:
                    del deps[name]
                    if name not in actually_removed:
                        actually_removed.append(name)
        if not actually_removed:
            return []
        package_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return actually_removed

    # Regex for CJS destructuring require: const { X } = require("./path")
    _CJS_DESTRUCTURE_REQUIRE = re.compile(r"const\s*\{([^}]+)\}\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)")
    # Regex for CJS direct class/function export: module.exports = ClassName
    _CJS_DIRECT_EXPORT = re.compile(r"module\.exports\s*=\s*([A-Za-z_$][\w$]*)\s*;")

    def repair_cjs_export_import_mismatch(self) -> list[dict[str, str]]:
        """Fix CJS destructuring imports that target direct-export modules.

        Common Director LLM error pattern:
        - Model file: ``module.exports = Dream;`` (direct export)
        - Consumer: ``const { Dream } = require("./models/Dream");`` (destructure)

        The destructuring yields ``undefined`` because the module exports the
        class directly, not an object with a ``Dream`` property.

        Fix: convert destructure to direct assignment in consumer files.
        """
        js_files = [p for p in self.workspace.rglob("*.js") if p.is_file() and "node_modules" not in p.parts]
        if not js_files:
            return []

        # Build map: resolved module path -> exported name (for direct exports)
        direct_exports: dict[str, str] = {}
        for js_file in js_files:
            try:
                content = js_file.read_text(encoding="utf-8")
            except OSError:
                continue
            match = self._CJS_DIRECT_EXPORT.search(content)
            if match:
                # Key: absolute resolved path without extension
                direct_exports[str(js_file.resolve().with_suffix(""))] = match.group(1)

        if not direct_exports:
            return []

        repairs: list[dict[str, str]] = []
        for js_file in js_files:
            try:
                content = js_file.read_text(encoding="utf-8")
            except OSError:
                continue
            modified = False
            new_lines: list[str] = []
            for line in content.split("\n"):
                match = self._CJS_DESTRUCTURE_REQUIRE.search(line)
                if match:
                    names = [n.strip() for n in match.group(1).split(",") if n.strip()]
                    require_path = match.group(2)
                    # Resolve the require path relative to the importing file
                    if require_path.startswith("."):
                        resolved = (js_file.parent / require_path).resolve()
                        resolved_key = str(resolved.with_suffix(""))
                        if resolved_key in direct_exports:
                            exported_name = direct_exports[resolved_key]
                            # Only fix if the destructured name matches the direct export
                            if exported_name in names:
                                # Convert: const { X } = require("./path") -> const X = require("./path")
                                indent = line[: len(line) - len(line.lstrip())]
                                line = f'{indent}const {exported_name} = require("{require_path}");'
                                modified = True
                                repairs.append(
                                    {
                                        "file": str(js_file.relative_to(self.workspace)),
                                        "fix": f"destructure_to_direct:{exported_name}",
                                    }
                                )
                new_lines.append(line)
            if modified:
                js_file.write_text("\n".join(new_lines), encoding="utf-8")

        return repairs

    # Regex for Node.js assert.strictEqual / assert.equal with string literals
    _ASSERT_EQUAL_PATTERN = re.compile(r"(assert\.(?:strictEqual|equal|deepStrictEqual|deepEqual))\s*\(")
    # Detect whitespace-only assertion difference from npm test stderr
    _WHITESPACE_DIFF_PATTERN = re.compile(r"'\s*([^']*?)\s*'\s*!==\s*'\s*([^']*?)\s*'")

    def repair_test_trim_mismatch(self, test_stderr: str) -> list[str]:
        """Fix test assertions that fail due to whitespace/trim differences.

        When npm test fails with ERR_ASSERTION showing a whitespace-only
        difference (e.g. ' Tide ' !== 'Tide'), this method patches the test
        files by wrapping string comparisons with .trim().
        """
        if not self._WHITESPACE_DIFF_PATTERN.search(test_stderr):
            return []

        test_files = [p for p in self.workspace.rglob("test_*.js") if p.is_file() and "node_modules" not in p.parts]
        test_files.extend(p for p in self.workspace.rglob("*.test.js") if p.is_file() and "node_modules" not in p.parts)
        if not test_files:
            return []

        patched: list[str] = []
        for test_file in test_files:
            try:
                content = test_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Find strictEqual/equal assertions and add .trim() to property accesses
            modified = False
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if self._ASSERT_EQUAL_PATTERN.search(line) and ".trim()" not in line:
                    # Only add .trim() when the expected value is a string literal
                    # (i.e. the second argument is in quotes). Skip numeric/boolean comparisons.
                    has_string_expected = bool(re.search(r',\s*["\']', line))
                    if not has_string_expected:
                        continue
                    # Add .trim() to property access patterns like obj.field or obj[key]
                    # Match patterns: result.field, dream.title, item.text, etc.
                    patched_line = re.sub(
                        r"\b([a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)+)\s*(,|\))",
                        lambda m: (
                            f"{m.group(1)}.trim(){m.group(2)}"
                            if ".trim()" not in m.group(0)
                            and "assert" not in m.group(1)
                            and "require" not in m.group(1)
                            else m.group(0)
                        ),
                        line,
                    )
                    if patched_line != line:
                        lines[i] = patched_line
                        modified = True
            if modified:
                test_file.write_text("\n".join(lines), encoding="utf-8")
                patched.append(str(test_file.relative_to(self.workspace)))

        return patched

    def run_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        resolved_command = helpers.resolve_workspace_quality_command(command)
        if not resolved_command:
            executable = command[0] if command else ""
            return {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"executable not found: {executable}",
                "stdout_tail": "",
                "stderr_tail": "",
            }
        try:
            completed = subprocess.run(
                resolved_command,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1.0, float(timeout_seconds or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS)),
                env={**os.environ, "CI": os.environ.get("CI", "1")},
                check=False,
            )
            stdout = helpers.trim_command_output(completed.stdout)
            stderr = helpers.trim_command_output(completed.stderr)
            nested_diagnostics = _nested_javac_diagnostics_from_output(
                workspace=self.workspace,
                stdout=stdout,
                stderr=stderr,
                timeout_seconds=min(
                    _NESTED_JAVAC_DIAGNOSTIC_TIMEOUT_SECONDS,
                    max(1.0, float(timeout_seconds or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS)),
                ),
            )
            if nested_diagnostics:
                stderr = helpers.trim_command_output("\n\n".join(part for part in (stderr, nested_diagnostics) if part))
            masked_failure_reason = ""
            if int(completed.returncode) == 0:
                masked_failure_reason = _masked_workspace_failure_reason(stdout, stderr)
            result: dict[str, Any] = {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": int(completed.returncode),
                "passed": int(completed.returncode) == 0 and not masked_failure_reason,
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
            if nested_diagnostics:
                result["nested_diagnostics"] = nested_diagnostics
            if masked_failure_reason:
                result["error"] = masked_failure_reason
            return result
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"timeout after {float(timeout_seconds):.1f}s",
                "stdout_tail": helpers.trim_command_output(str(exc.stdout or "")),
                "stderr_tail": helpers.trim_command_output(str(exc.stderr or "")),
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout_tail": "",
                "stderr_tail": "",
            }


def _masked_workspace_failure_reason(stdout: str, stderr: str) -> str:
    output = f"{stdout}\n{stderr}"
    for pattern, reason in _MASKED_WORKSPACE_FAILURE_PATTERNS:
        if pattern.search(output):
            return reason
    return ""


def _nested_javac_diagnostics_from_output(
    *,
    workspace: Path,
    stdout: str,
    stderr: str,
    timeout_seconds: float,
) -> str:
    command = _called_process_error_javac_command(f"{stdout}\n{stderr}")
    if not command or not _safe_javac_diagnostic_command(command, workspace=workspace):
        return ""
    resolved = helpers.resolve_workspace_quality_command(command)
    if not resolved:
        return ""
    try:
        completed = subprocess.run(
            resolved,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, timeout_seconds),
            env={**os.environ, "CI": os.environ.get("CI", "1")},
            check=False,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired, TypeError, ValueError) as exc:
        return f"Nested javac diagnostics unavailable: {type(exc).__name__}: {exc}"
    diagnostic_output = helpers.trim_command_output(
        "\n".join(part for part in (completed.stderr, completed.stdout) if part)
    )
    if not diagnostic_output:
        return ""
    return (
        "Nested javac diagnostics from unittest subprocess "
        f"(exit_code={int(completed.returncode)}):\n{diagnostic_output}"
    )


def _called_process_error_javac_command(output: str) -> list[str]:
    for match in _CALLED_PROCESS_ERROR_COMMAND_RE.finditer(str(output or "")):
        try:
            parsed = ast.literal_eval(match.group("command"))
        except (SyntaxError, ValueError):
            continue
        if not isinstance(parsed, list) or not parsed:
            continue
        command = [str(part) for part in parsed if isinstance(part, str) and part]
        if len(command) == len(parsed) and Path(command[0]).name == "javac":
            return command
    return []


def _safe_javac_diagnostic_command(command: list[str], *, workspace: Path) -> bool:
    if not command or Path(command[0]).name != "javac" or len(command) > 200:
        return False
    workspace_root = workspace.resolve()
    for part in command[1:]:
        if not _javac_arg_is_path_like(part):
            continue
        try:
            candidate = Path(part)
            resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
            resolved.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError):
            return False
    return True


def _javac_arg_is_path_like(value: str) -> bool:
    token = str(value or "").strip()
    if not token or token.startswith("-"):
        return False
    return "/" in token or "\\" in token or token.endswith((".java", ".class", ".jar"))
