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

from polaris.kernelone.benchmark.factory_audit import check_workspace_delivery_depth_contract

from . import factory_stage_helpers as helpers
from .factory_run_models import _WORKSPACE_VALIDATION_TIMEOUT_SECONDS
from .factory_workspace_quality_evidence import (
    compact_compiler_error_blocks,
    compact_go_stack_overflow_diagnostic,
)
from .native_validation_sandbox import (
    NativeValidationContractError,
    NativeValidationSandboxError,
    cargo_native_test_count,
    is_cargo_test_command,
    sandboxed_cargo_test_command,
)

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


def workspace_quality_subprocess_env(*, workspace: Path) -> dict[str, str]:
    """Build factory-owned verifier env for workspace quality commands.

    Python CLI entrypoints such as ``python src/main.py`` put the script
    directory (``src/``) on ``sys.path[0]``. Package imports like
    ``from src.engine import ...`` then fail with
    ``ModuleNotFoundError: No module named 'src'`` unless the workspace root
    is on ``PYTHONPATH``. Bench gates already inject this; the official
    quality runner must match. Host ``PYTHONPATH`` is replaced so a parent
    ``src`` package cannot shadow the target workspace.
    """
    env = {**os.environ, "CI": os.environ.get("CI", "1")}
    env["PYTHONPATH"] = str(Path(workspace).resolve())
    return env


def _npm_start_runs_long_lived_web_server(script: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(script or "").strip().lower())
    if not normalized:
        return False
    if any(marker in normalized for marker in _LONG_RUNNING_WEB_START_MARKERS):
        return True
    return bool(re.search(r"(?:^|\s)npx\s+(?:--yes\s+)?serve(?:\s|$)", normalized))


class WorkspaceQualityRunner:
    """Build and run quality commands without mutating target artifacts.

    Factory owns measurement and failure evidence only. Any source, manifest,
    or test repair must execute through the Director runtime authority.
    """

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
            env_var="KERNELONE_FACTORY_WORKSPACE_VALIDATION_INSTALL_DEPENDENCIES",
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

    def delivery_depth_contract_result(self, context: dict[str, Any]) -> dict[str, Any] | None:
        if not helpers.bool_from_context_or_env(
            context,
            "workspace_validation_delivery_depth_contract",
            "qa_workspace_validation_delivery_depth_contract",
            env_var="KERNELONE_FACTORY_WORKSPACE_VALIDATION_DELIVERY_DEPTH_CONTRACT",
            default=True,
        ):
            return None
        level_contract = self._load_delivery_depth_level_contract()
        if level_contract is None:
            return None
        passed, detail = check_workspace_delivery_depth_contract(
            str(self.workspace),
            level_contract=level_contract,
        )
        trimmed_detail = helpers.trim_command_output(detail)
        return {
            "command": ["delivery_depth_contract"],
            "exit_code": 0 if passed else 1,
            "passed": passed,
            "stdout_tail": trimmed_detail if passed else "",
            "stderr_tail": "" if passed else trimmed_detail,
            "error": "" if passed else f"delivery_depth_contract_failed: {trimmed_detail}",
            "delivery_depth_contract": {
                "schema_version": "factory.workspace_quality.delivery_depth_contract.v1",
                "level": level_contract.get("level"),
                "minimums": level_contract.get("minimums"),
                "detail": detail,
            },
        }

    def _load_delivery_depth_level_contract(self) -> dict[str, Any] | None:
        catalog_path = self.workspace / ".polaris" / "catalog_contract.json"
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None

        for key in ("level_contract", "factory_bench_level_contract", "delivery_depth_contract"):
            candidate = payload.get(key)
            if not isinstance(candidate, dict):
                continue
            minimums = candidate.get("minimums")
            if not isinstance(minimums, dict) or not minimums:
                continue
            normalized = dict(candidate)
            if normalized.get("level") is None and payload.get("level") is not None:
                normalized["level"] = payload.get("level")
            return normalized
        return None

    def workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        if not helpers.bool_from_context_or_env(
            context,
            "workspace_validation",
            "qa_workspace_validation",
            env_var="KERNELONE_FACTORY_WORKSPACE_VALIDATION",
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
                env_var="KERNELONE_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
                default=True,
            )
            and "start" in scripts
            and not _npm_start_runs_long_lived_web_server(scripts["start"])
        ):
            commands.append(["npm", "run", "start"])
        go_commands = self._go_workspace_quality_commands(context)
        commands.extend(go_commands)
        rust_commands = self._rust_workspace_quality_commands()
        commands.extend(rust_commands)
        cpp_commands = self._cpp_workspace_quality_commands()
        commands.extend(cpp_commands)
        if not go_commands and not rust_commands and not cpp_commands:
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
            env_var="KERNELONE_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
            default=True,
        ):
            commands.append(["go", "run", "."])
        return commands

    def _rust_source_files(self) -> list[Path]:
        ignored = {"target", "runtime", ".polaris", "vendor"}
        return [
            path
            for path in self.workspace.rglob("*.rs")
            if path.is_file() and not any(part in ignored for part in path.parts)
        ]

    def _rust_manifest_candidates(self) -> list[Path]:
        if not self.workspace.is_dir():
            return []
        try:
            return [path for path in self.workspace.iterdir() if path.is_file() and path.name.lower() == "cargo.toml"]
        except OSError:
            return []

    def _rust_workspace_quality_commands(self) -> list[list[str]]:
        rust_files = self._rust_source_files()
        manifests = self._rust_manifest_candidates()
        if not rust_files and not manifests:
            return []
        # Cargo and the native sandbox both require the exact basename
        # ``Cargo.toml``. Live L2-14 wrote ``cargo.toml``; skipping rust when
        # that file (or any ``*.rs``) exists made quality_gate pass with only
        # delivery_depth and no compile/test receipt.
        # ``cargo test`` compiles every target and executes native unit and
        # integration tests. A prior Bench-shaped PM contract generated a
        # Python wrapper for Rust while this gate ran only ``cargo check``;
        # the declared test evidence was therefore never executed.
        return [["cargo", "test", "--quiet"]]

    def _cpp_manifest_candidates(self) -> list[Path]:
        if not self.workspace.is_dir():
            return []
        try:
            return [
                path for path in self.workspace.iterdir() if path.is_file() and path.name.lower() == "cmakelists.txt"
            ]
        except OSError:
            return []

    def _cpp_workspace_quality_commands(self) -> list[list[str]]:
        cpp_files = [
            path
            for ext in ("*.cpp", "*.cc", "*.cxx", "*.c")
            for path in self.workspace.rglob(ext)
            if path.is_file() and "build" not in path.parts and "cmake-build" not in path.parts
        ]
        manifests = self._cpp_manifest_candidates()
        if not cpp_files and not manifests:
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
failed_paths = []
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
        failed_paths.append(str(path))
        body = (completed.stderr or completed.stdout or "")[:1200]
        failed.append(f"### {path}\\n{body}")
if failed:
    # Live L2-15: first TU's unclosed-namespace dump (75+ stdexcept
    # errors) filled the validation excerpt and hid later ### TUs.
    # Index must come first so leftover can rotate after trim.
    print("### FAILING_TUS " + " ".join(failed_paths), file=sys.stderr)
    print("\\n".join(failed), file=sys.stderr)
    raise SystemExit(1)
print(f"C++ syntax check passed for {len(files)} translation unit(s)")
""".strip()
        commands: list[list[str]] = [[sys.executable, "-c", script]]
        # Live L2-15: syntax-only hid the official CMake binary + Python
        # behavior tests. cargo analog is cmake --build then unittest.
        # Lowercase ``cmakelists.txt`` is a typed diagnostic, not a write.
        if manifests:
            cmake_script = """
import pathlib
import subprocess
import sys

root = pathlib.Path(".")
canonical = root / "CMakeLists.txt"
found = sorted(
    path for path in root.iterdir()
    if path.is_file() and path.name.lower() == "cmakelists.txt"
)
if not canonical.is_file():
    if found:
        print(
            f"{found[0].as_posix()}:1:1: error: official CMakeLists.txt "
            f"basename required (found {found[0].name})",
            file=sys.stderr,
        )
    else:
        print("CMakeLists.txt:1:1: error: CMakeLists.txt is missing", file=sys.stderr)
    raise SystemExit(1)
for command in (["cmake", "-S", ".", "-B", "build"], ["cmake", "--build", "build"]):
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout or " ".join(command), file=sys.stderr)
        raise SystemExit(completed.returncode or 1)
print("CMake build passed")
""".strip()
            commands.append([sys.executable, "-c", cmake_script])
        test_dir = self.workspace / "tests"
        if test_dir.is_dir() and any(test_dir.glob("test_*.py")):
            commands.append([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"])
        return commands

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
            env_var="KERNELONE_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
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
        if is_cargo_test_command(resolved_command):
            try:
                with sandboxed_cargo_test_command(
                    workspace=self.workspace,
                    command=resolved_command,
                ) as sandbox:
                    return self._run_resolved_command(
                        command=command,
                        resolved_command=sandbox.command,
                        timeout_seconds=timeout_seconds,
                        started_at=started_at,
                        sandbox_backend=sandbox.backend,
                        cargo_test=True,
                    )
            except NativeValidationContractError as exc:
                return {
                    "command": command,
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "exit_code": None,
                    "passed": False,
                    "error": f"native_validation_contract_invalid: {exc}",
                    "stdout_tail": "",
                    "stderr_tail": "",
                    "sandboxed": False,
                    "native_test_count": 0,
                }
            except NativeValidationSandboxError as exc:
                return {
                    "command": command,
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "exit_code": None,
                    "passed": False,
                    "error": f"native_validation_sandbox_unavailable: {exc}",
                    "stdout_tail": "",
                    "stderr_tail": "",
                    "sandboxed": False,
                }
        return self._run_resolved_command(
            command=command,
            resolved_command=resolved_command,
            timeout_seconds=timeout_seconds,
            started_at=started_at,
            sandbox_backend="",
            cargo_test=False,
        )

    def _run_resolved_command(
        self,
        *,
        command: list[str],
        resolved_command: list[str],
        timeout_seconds: float,
        started_at: str,
        sandbox_backend: str,
        cargo_test: bool,
    ) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                resolved_command,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1.0, float(timeout_seconds or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS)),
                env=workspace_quality_subprocess_env(workspace=self.workspace),
                check=False,
            )
            stdout = helpers.trim_command_output(completed.stdout)
            stderr = helpers.trim_command_output(completed.stderr)
            nested_diagnostics = ""
            if not cargo_test:
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
            native_test_count = cargo_native_test_count(completed.stdout) if cargo_test else 0
            zero_native_tests = cargo_test and int(completed.returncode) == 0 and native_test_count < 1
            result: dict[str, Any] = {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": int(completed.returncode),
                "passed": (int(completed.returncode) == 0 and not masked_failure_reason and not zero_native_tests),
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
            if not bool(result["passed"]):
                raw_diagnostic = compact_go_stack_overflow_diagnostic(
                    "\n".join(part for part in (completed.stdout, completed.stderr) if part)
                )
                diagnostic_excerpt = compact_compiler_error_blocks(raw_diagnostic)
                if not diagnostic_excerpt.strip():
                    diagnostic_excerpt = helpers.trim_command_output(raw_diagnostic)
                if diagnostic_excerpt:
                    result["diagnostic_excerpt"] = diagnostic_excerpt
            if cargo_test:
                result.update(
                    {
                        "native_test_count": native_test_count,
                        "sandbox_backend": sandbox_backend,
                        "sandboxed": True,
                    }
                )
            if nested_diagnostics:
                result["nested_diagnostics"] = nested_diagnostics
            if zero_native_tests:
                result["error"] = "cargo_test_zero_tests"
            elif masked_failure_reason:
                result["error"] = masked_failure_reason
            return result
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"timeout after {float(timeout_seconds):.1f}s",
                "stdout_tail": helpers.trim_command_output(str(exc.stdout or "")),
                "stderr_tail": helpers.trim_command_output(str(exc.stderr or "")),
            }
            diagnostic_excerpt = compact_compiler_error_blocks(
                "\n".join(str(part or "") for part in (exc.stdout, exc.stderr) if part)
            )
            if not diagnostic_excerpt.strip():
                diagnostic_excerpt = helpers.trim_command_output(
                    "\n".join(str(part or "") for part in (exc.stdout, exc.stderr) if part)
                )
            if diagnostic_excerpt:
                result["diagnostic_excerpt"] = diagnostic_excerpt
            if cargo_test:
                result.update(
                    {
                        "native_test_count": cargo_native_test_count(exc.stdout),
                        "sandbox_backend": sandbox_backend,
                        "sandboxed": True,
                    }
                )
            return result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            result = {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout_tail": "",
                "stderr_tail": "",
            }
            if cargo_test:
                result.update(
                    {
                        "native_test_count": 0,
                        "sandbox_backend": sandbox_backend,
                        "sandboxed": True,
                    }
                )
            return result


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
            env=workspace_quality_subprocess_env(workspace=workspace),
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
