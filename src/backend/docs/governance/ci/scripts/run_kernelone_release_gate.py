"""KernelOne release gate runner.

This script provides a stable CI entrypoint for KernelOne release gating:
1. collect-only sanity for the KernelOne-focused test suite
2. optional execution of the same suite
3. structured JSON report output for audit trails
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[4]
_ROLE_RUNTIME_PUBLIC_IMPORT_BOUNDARY_STAGE = "roles_runtime_public_import_boundary"
_ROLE_RUNTIME_CELL_IMPORT_BOUNDARY_STAGE = "roles_runtime_cell_import_boundary"
_ROLE_RUNTIME_CAPABILITY_RESULT_SANDBOX_STAGE = "roles_runtime_capability_result_sandbox"
_ROLE_EXECUTION_KERNEL_ENTRYPOINT_STAGE = "role_execution_kernel_entrypoint_boundary"
_LEGACY_ROLE_DIALOGUE_ENTRYPOINT_STAGE = "legacy_role_dialogue_entrypoint_boundary"
_ROLES_KERNEL_ADAPTER_DEPENDENCY_STAGE = "roles_kernel_adapter_dependency_boundary"
_ROLES_KERNEL_DIALOGUE_DEPENDENCY_STAGE = "roles_kernel_dialogue_dependency_boundary"
_ROLES_KERNEL_RUNTIME_DEPENDENCY_STAGE = "roles_kernel_runtime_dependency_boundary"
_KERNELONE_ROLES_BUSINESS_BOUNDARY_STAGE = "kernelone_roles_business_boundary"
_ROLE_RUNTIME_PUBLIC_ROOT = Path("polaris/cells/roles/runtime/public")
_ROLE_RUNTIME_CELL_YAML = Path("polaris/cells/roles/runtime/cell.yaml")
_ROLES_KERNEL_ROOT = Path("polaris/cells/roles/kernel")
_KERNELONE_ROLES_ROOT = Path("polaris/kernelone/roles")
_ROLE_RUNTIME_OWN_INTERNAL_PREFIX = "polaris.cells.roles.runtime.internal"
_ROLE_EXECUTION_KERNEL_ALLOWED_PATHS = frozenset(
    {
        "polaris/cells/roles/runtime/public/service.py",
    }
)
_ROLE_EXECUTION_KERNEL_ALLOWED_PREFIXES = ("polaris/cells/roles/kernel/",)
_ROLE_EXECUTION_KERNEL_IMPORT_MODULES = frozenset(
    {
        "polaris.cells.roles.kernel",
        "polaris.cells.roles.kernel.public",
        "polaris.cells.roles.kernel.public.service",
        "polaris.cells.roles.kernel.internal.kernel",
        "polaris.cells.roles.kernel.internal.kernel.core",
    }
)
_LEGACY_ROLE_DIALOGUE_ALLOWED_PREFIXES = ("polaris/cells/llm/dialogue/",)
_LEGACY_ROLE_DIALOGUE_FUNCTIONS = frozenset(
    {
        "generate_role_response",
        "generate_role_response_streaming",
    }
)
_LEGACY_ROLE_DIALOGUE_IMPORT_MODULES = frozenset(
    {
        "polaris.cells.llm.dialogue",
        "polaris.cells.llm.dialogue.internal",
        "polaris.cells.llm.dialogue.internal.role_dialogue",
        "polaris.cells.llm.dialogue.public",
        "polaris.cells.llm.dialogue.public.service",
    }
)
_PRODUCTION_SOURCE_ROOTS = (
    Path("polaris/application"),
    Path("polaris/cells"),
    Path("polaris/delivery"),
)
_ROLES_ADAPTERS_MODULE_PREFIX = "polaris.cells.roles.adapters"
_LLM_DIALOGUE_MODULE_PREFIX = "polaris.cells.llm.dialogue"
_ROLES_RUNTIME_MODULE_PREFIX = "polaris.cells.roles.runtime"
_BUSINESS_ROLE_TOKENS = frozenset(
    {
        "architect",
        "ce",
        "chief_engineer",
        "director",
        "pm",
        "project_manager",
        "qa",
        "quality_assurance",
    }
)


@dataclass(frozen=True)
class GateRunResult:
    stage: str
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _discover_suite_paths() -> list[str]:
    candidates: list[Path] = []
    candidates.extend(sorted((BACKEND_ROOT / "polaris" / "tests").glob("test_kernelone_*.py")))
    candidates.extend(sorted((BACKEND_ROOT / "polaris" / "tests" / "architecture").glob("test_kernelone_*.py")))
    candidates.append(BACKEND_ROOT / "polaris" / "tests" / "architecture" / "test_polaris_kernel_fs_guard.py")

    suite_paths: list[str] = []
    for path in candidates:
        if path.exists():
            suite_paths.append(path.relative_to(BACKEND_ROOT).as_posix())
    if not suite_paths:
        raise RuntimeError("KernelOne release suite is empty; no test files discovered.")
    return suite_paths


def _run_pytest(stage: str, pytest_args: Iterable[str]) -> GateRunResult:
    command = [sys.executable, "-m", "pytest", *pytest_args]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_build_utf8_env(),
        check=False,
    )
    duration_seconds = time.monotonic() - started
    return GateRunResult(
        stage=stage,
        command=command,
        returncode=int(completed.returncode),
        duration_seconds=float(round(duration_seconds, 3)),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _iter_import_modules(tree: ast.AST) -> Iterable[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module


def _is_cross_cell_internal_import(module: str) -> bool:
    if not module.startswith("polaris.cells."):
        return False
    if ".internal" not in module:
        return False
    return not module.startswith(_ROLE_RUNTIME_OWN_INTERNAL_PREFIX)


def _is_production_python_file(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".py":
        return False
    if "__pycache__" in path.parts:
        return False
    if "tests" in path.parts:
        return False
    return not path.name.startswith("test_")


def _iter_production_python_files() -> Iterable[Path]:
    yielded: set[Path] = set()
    for root in _PRODUCTION_SOURCE_ROOTS:
        absolute_root = BACKEND_ROOT / root
        if not absolute_root.exists():
            continue
        for path in sorted(absolute_root.rglob("*.py")):
            if path in yielded or not _is_production_python_file(path):
                continue
            yielded.add(path)
            yield path


def _is_role_execution_kernel_allowed_path(rel_path: str) -> bool:
    if rel_path in _ROLE_EXECUTION_KERNEL_ALLOWED_PATHS:
        return True
    return any(rel_path.startswith(prefix) for prefix in _ROLE_EXECUTION_KERNEL_ALLOWED_PREFIXES)


def _is_role_execution_kernel_import(module: str, names: Iterable[str] = ()) -> bool:
    module_token = str(module or "").strip()
    if module_token not in _ROLE_EXECUTION_KERNEL_IMPORT_MODULES:
        return False
    imported_names = tuple(str(name or "").strip() for name in names)
    if not imported_names:
        return True
    return "RoleExecutionKernel" in imported_names


def _find_role_execution_kernel_entrypoint_violations(tree: ast.AST, rel_path: str) -> tuple[str, ...]:
    if _is_role_execution_kernel_allowed_path(rel_path):
        return ()

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_role_execution_kernel_import(alias.name):
                    violations.append(
                        f"{rel_path}:{node.lineno}: "
                        "production code must enter RoleRuntimeService instead of importing RoleExecutionKernel"
                    )
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names = tuple(alias.name for alias in node.names)
            if _is_role_execution_kernel_import(node.module, imported_names):
                violations.append(
                    f"{rel_path}:{node.lineno}: "
                    "production code must enter RoleRuntimeService instead of importing RoleExecutionKernel"
                )
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "RoleExecutionKernel") or (
                isinstance(func, ast.Attribute) and func.attr == "RoleExecutionKernel"
            ):
                violations.append(
                    f"{rel_path}:{node.lineno}: "
                    "production code must enter RoleRuntimeService instead of constructing RoleExecutionKernel"
                )
    return tuple(violations)


def _is_legacy_role_dialogue_allowed_path(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in _LEGACY_ROLE_DIALOGUE_ALLOWED_PREFIXES)


def _find_legacy_role_dialogue_entrypoint_violations(tree: ast.AST, rel_path: str) -> tuple[str, ...]:
    if _is_legacy_role_dialogue_allowed_path(rel_path):
        return ()

    violations: list[str] = []
    imported_legacy_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _LEGACY_ROLE_DIALOGUE_IMPORT_MODULES:
                    imported_name = alias.asname or alias.name.rsplit(".", 1)[-1]
                    imported_legacy_names.add(imported_name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module in _LEGACY_ROLE_DIALOGUE_IMPORT_MODULES:
            blocked_names = tuple(alias for alias in node.names if alias.name in _LEGACY_ROLE_DIALOGUE_FUNCTIONS)
            if blocked_names:
                imported_legacy_names.update(alias.asname or alias.name for alias in blocked_names)
                violations.append(
                    f"{rel_path}:{node.lineno}: "
                    "production code must enter RoleRuntimeService instead of importing legacy role dialogue"
                )
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and (func.id in imported_legacy_names or func.id in _LEGACY_ROLE_DIALOGUE_FUNCTIONS)
            ) or (isinstance(func, ast.Attribute) and func.attr in _LEGACY_ROLE_DIALOGUE_FUNCTIONS):
                violations.append(
                    f"{rel_path}:{node.lineno}: "
                    "production code must enter RoleRuntimeService instead of calling legacy role dialogue"
                )
    return tuple(violations)


def _is_roles_kernel_path(rel_path: str) -> bool:
    return rel_path.startswith(_ROLES_KERNEL_ROOT.as_posix() + "/")


def _is_roles_adapters_import(module: str) -> bool:
    module_token = str(module or "").strip()
    return module_token == _ROLES_ADAPTERS_MODULE_PREFIX or module_token.startswith(_ROLES_ADAPTERS_MODULE_PREFIX + ".")


def _is_llm_dialogue_import(module: str) -> bool:
    module_token = str(module or "").strip()
    return module_token == _LLM_DIALOGUE_MODULE_PREFIX or module_token.startswith(_LLM_DIALOGUE_MODULE_PREFIX + ".")


def _is_roles_runtime_import(module: str) -> bool:
    module_token = str(module or "").strip()
    return module_token == _ROLES_RUNTIME_MODULE_PREFIX or module_token.startswith(_ROLES_RUNTIME_MODULE_PREFIX + ".")


def _find_roles_kernel_adapter_dependency_violations(tree: ast.AST, rel_path: str) -> tuple[str, ...]:
    if not _is_roles_kernel_path(rel_path):
        return ()

    violations: list[str] = []
    for lineno, module in _iter_import_modules(tree):
        if _is_roles_adapters_import(module):
            violations.append(
                f"{rel_path}:{lineno}: "
                "roles.kernel production code must not import roles.adapters; "
                "runtime/profile contracts must provide role-specific schema decisions"
            )
    return tuple(violations)


def _find_roles_kernel_dialogue_dependency_violations(tree: ast.AST, rel_path: str) -> tuple[str, ...]:
    if not _is_roles_kernel_path(rel_path):
        return ()

    violations: list[str] = []
    for lineno, module in _iter_import_modules(tree):
        if _is_llm_dialogue_import(module):
            violations.append(
                f"{rel_path}:{lineno}: "
                "roles.kernel production code must not import llm.dialogue; "
                "role turns must enter provider/control-plane paths through roles.runtime and roles.kernel contracts"
            )
    return tuple(violations)


def _find_roles_kernel_runtime_dependency_violations(tree: ast.AST, rel_path: str) -> tuple[str, ...]:
    if not _is_roles_kernel_path(rel_path):
        return ()

    violations: list[str] = []
    for lineno, module in _iter_import_modules(tree):
        if _is_roles_runtime_import(module):
            violations.append(
                f"{rel_path}:{lineno}: "
                "roles.kernel production code must not import roles.runtime; "
                "roles.runtime composes the kernel through public contracts, not the reverse"
            )
    return tuple(violations)


def _is_role_capability_invocation_result_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "RoleCapabilityInvocationResultV1"
    if isinstance(func, ast.Attribute):
        return func.attr == "RoleCapabilityInvocationResultV1"
    return False


def _literal_bool_keyword(node: ast.Call, keyword_name: str) -> bool | None:
    for keyword in node.keywords:
        if keyword.arg != keyword_name:
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, bool):
            return value.value
        return None
    return None


def _split_identifier_tokens(value: str) -> set[str]:
    normalized_chars: list[str] = []
    previous = ""
    for char in value:
        if char.isupper() and previous and (previous.islower() or previous.isdigit()):
            normalized_chars.append("_")
        if char.isalnum():
            normalized_chars.append(char.lower())
        else:
            normalized_chars.append("_")
        previous = char
    normalized = "".join(normalized_chars)
    tokens = {token for token in normalized.split("_") if token}
    if "chief" in tokens and "engineer" in tokens:
        tokens.add("chief_engineer")
    if "project" in tokens and "manager" in tokens:
        tokens.add("project_manager")
    if "quality" in tokens and "assurance" in tokens:
        tokens.add("quality_assurance")
    return tokens


def _contains_business_role_token(value: str) -> bool:
    return bool(_split_identifier_tokens(value) & _BUSINESS_ROLE_TOKENS)


def _load_roles_runtime_owned_path_patterns() -> tuple[list[str], list[str]]:
    cell_yaml = BACKEND_ROOT / _ROLE_RUNTIME_CELL_YAML
    errors: list[str] = []
    try:
        payload = yaml.safe_load(cell_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], [f"{_ROLE_RUNTIME_CELL_YAML.as_posix()}: parse failed: {exc}"]

    if not isinstance(payload, dict):
        return [], [f"{_ROLE_RUNTIME_CELL_YAML.as_posix()}: expected mapping payload"]
    if payload.get("id") != "roles.runtime":
        errors.append(f"{_ROLE_RUNTIME_CELL_YAML.as_posix()}: expected id roles.runtime")

    raw_owned_paths = payload.get("owned_paths")
    if not isinstance(raw_owned_paths, list):
        return [], [*errors, f"{_ROLE_RUNTIME_CELL_YAML.as_posix()}: owned_paths must be a list"]

    patterns: list[str] = []
    for index, value in enumerate(raw_owned_paths):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{_ROLE_RUNTIME_CELL_YAML.as_posix()}: owned_paths[{index}] must be a non-empty string")
            continue
        patterns.append(value.strip())
    return patterns, errors


def _iter_python_files_for_owned_path(pattern: str) -> Iterable[Path]:
    if pattern.endswith("/**"):
        root = BACKEND_ROOT / pattern.removesuffix("/**")
        if root.is_dir():
            yield from sorted(root.rglob("*.py"))
        return

    if "*" in pattern:
        for path in sorted(BACKEND_ROOT.glob(pattern)):
            if path.is_dir():
                yield from sorted(path.rglob("*.py"))
            elif path.suffix == ".py":
                yield path
        return

    path = BACKEND_ROOT / pattern
    if path.is_dir():
        yield from sorted(path.rglob("*.py"))
    elif path.suffix == ".py":
        yield path


def _iter_roles_runtime_owned_python_files(patterns: Iterable[str]) -> Iterable[Path]:
    yielded: set[Path] = set()
    for pattern in patterns:
        for path in _iter_python_files_for_owned_path(pattern):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path in yielded:
                continue
            yielded.add(path)
            yield path


def _is_pytest_raises_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "raises"
    if isinstance(func, ast.Attribute):
        return func.attr == "raises"
    return False


class _RoleCapabilityResultSandboxVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.violations: list[str] = []
        self._pytest_raises_depth = 0

    def visit_With(self, node: ast.With) -> None:
        guarded_by_pytest_raises = any(_is_pytest_raises_call(item.context_expr) for item in node.items)
        if guarded_by_pytest_raises:
            self._pytest_raises_depth += 1
        self.generic_visit(node)
        if guarded_by_pytest_raises:
            self._pytest_raises_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        if self._pytest_raises_depth:
            self.generic_visit(node)
            return
        if _is_role_capability_invocation_result_call(node):
            ok_value = _literal_bool_keyword(node, "ok")
            allowed_value = _literal_bool_keyword(node, "allowed")
            if ok_value is False and allowed_value is True:
                self.violations.append(
                    f"{self.rel_path}:{node.lineno}: "
                    "RoleCapabilityInvocationResultV1(ok=False, allowed=True) is forbidden; "
                    "use allowed=False and metadata.capability_available for discoverability"
                )
        self.generic_visit(node)


def _check_role_runtime_public_import_boundaries() -> GateRunResult:
    started = time.monotonic()
    public_root = BACKEND_ROOT / _ROLE_RUNTIME_PUBLIC_ROOT
    command = ["static", _ROLE_RUNTIME_PUBLIC_IMPORT_BOUNDARY_STAGE, public_root.as_posix()]
    violations: list[str] = []

    for path in sorted(public_root.rglob("*.py")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            violations.append(f"{rel_path}: parse failed: {exc}")
            continue
        for lineno, module in _iter_import_modules(tree):
            if _is_cross_cell_internal_import(module):
                violations.append(f"{rel_path}:{lineno}: cross-cell internal import: {module}")

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLE_RUNTIME_PUBLIC_IMPORT_BOUNDARY_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_roles_runtime_cell_import_boundaries() -> GateRunResult:
    started = time.monotonic()
    owned_patterns, violations = _load_roles_runtime_owned_path_patterns()
    command = ["static", _ROLE_RUNTIME_CELL_IMPORT_BOUNDARY_STAGE, _ROLE_RUNTIME_CELL_YAML.as_posix(), *owned_patterns]

    for path in _iter_roles_runtime_owned_python_files(owned_patterns):
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            violations.append(f"{rel_path}: parse failed: {exc}")
            continue
        for lineno, module in _iter_import_modules(tree):
            if _is_cross_cell_internal_import(module):
                violations.append(f"{rel_path}:{lineno}: cross-cell internal import: {module}")

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLE_RUNTIME_CELL_IMPORT_BOUNDARY_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_role_runtime_capability_result_sandbox() -> GateRunResult:
    started = time.monotonic()
    public_root = BACKEND_ROOT / _ROLE_RUNTIME_PUBLIC_ROOT
    command = ["static", _ROLE_RUNTIME_CAPABILITY_RESULT_SANDBOX_STAGE, public_root.as_posix()]
    violations: list[str] = []

    for path in sorted(public_root.rglob("*.py")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            violations.append(f"{rel_path}: parse failed: {exc}")
            continue
        visitor = _RoleCapabilityResultSandboxVisitor(rel_path)
        visitor.visit(tree)
        violations.extend(visitor.violations)

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLE_RUNTIME_CAPABILITY_RESULT_SANDBOX_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_role_execution_kernel_entrypoint_boundary() -> GateRunResult:
    started = time.monotonic()
    command = [
        "static",
        _ROLE_EXECUTION_KERNEL_ENTRYPOINT_STAGE,
        *[root.as_posix() for root in _PRODUCTION_SOURCE_ROOTS],
    ]
    violations: list[str] = []

    for path in _iter_production_python_files():
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            violations.append(f"{rel_path}: parse failed: {exc}")
            continue
        violations.extend(_find_role_execution_kernel_entrypoint_violations(tree, rel_path))

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLE_EXECUTION_KERNEL_ENTRYPOINT_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_legacy_role_dialogue_entrypoint_boundary() -> GateRunResult:
    started = time.monotonic()
    command = [
        "static",
        _LEGACY_ROLE_DIALOGUE_ENTRYPOINT_STAGE,
        *[root.as_posix() for root in _PRODUCTION_SOURCE_ROOTS],
    ]
    violations: list[str] = []

    for path in _iter_production_python_files():
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            violations.append(f"{rel_path}: parse failed: {exc}")
            continue
        violations.extend(_find_legacy_role_dialogue_entrypoint_violations(tree, rel_path))

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_LEGACY_ROLE_DIALOGUE_ENTRYPOINT_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_roles_kernel_adapter_dependency_boundary() -> GateRunResult:
    started = time.monotonic()
    command = ["static", _ROLES_KERNEL_ADAPTER_DEPENDENCY_STAGE, _ROLES_KERNEL_ROOT.as_posix()]
    violations: list[str] = []

    kernel_root = BACKEND_ROOT / _ROLES_KERNEL_ROOT
    if kernel_root.exists():
        for path in sorted(kernel_root.rglob("*.py")):
            if not _is_production_python_file(path):
                continue
            rel_path = path.relative_to(BACKEND_ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                violations.append(f"{rel_path}: parse failed: {exc}")
                continue
            violations.extend(_find_roles_kernel_adapter_dependency_violations(tree, rel_path))

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLES_KERNEL_ADAPTER_DEPENDENCY_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_roles_kernel_dialogue_dependency_boundary() -> GateRunResult:
    started = time.monotonic()
    command = ["static", _ROLES_KERNEL_DIALOGUE_DEPENDENCY_STAGE, _ROLES_KERNEL_ROOT.as_posix()]
    violations: list[str] = []

    kernel_root = BACKEND_ROOT / _ROLES_KERNEL_ROOT
    if kernel_root.exists():
        for path in sorted(kernel_root.rglob("*.py")):
            if not _is_production_python_file(path):
                continue
            rel_path = path.relative_to(BACKEND_ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                violations.append(f"{rel_path}: parse failed: {exc}")
                continue
            violations.extend(_find_roles_kernel_dialogue_dependency_violations(tree, rel_path))

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLES_KERNEL_DIALOGUE_DEPENDENCY_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_roles_kernel_runtime_dependency_boundary() -> GateRunResult:
    started = time.monotonic()
    command = ["static", _ROLES_KERNEL_RUNTIME_DEPENDENCY_STAGE, _ROLES_KERNEL_ROOT.as_posix()]
    violations: list[str] = []

    kernel_root = BACKEND_ROOT / _ROLES_KERNEL_ROOT
    if kernel_root.exists():
        for path in sorted(kernel_root.rglob("*.py")):
            if not _is_production_python_file(path):
                continue
            rel_path = path.relative_to(BACKEND_ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                violations.append(f"{rel_path}: parse failed: {exc}")
                continue
            violations.extend(_find_roles_kernel_runtime_dependency_violations(tree, rel_path))

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_ROLES_KERNEL_RUNTIME_DEPENDENCY_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _check_kernelone_roles_business_boundary() -> GateRunResult:
    started = time.monotonic()
    roles_root = BACKEND_ROOT / _KERNELONE_ROLES_ROOT
    command = ["static", _KERNELONE_ROLES_BUSINESS_BOUNDARY_STAGE, roles_root.as_posix()]
    violations: list[str] = []

    if roles_root.exists():
        for path in sorted(roles_root.rglob("*.py")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel_path = path.relative_to(BACKEND_ROOT).as_posix()
            if _contains_business_role_token(path.stem):
                violations.append(
                    f"{rel_path}: business role filename is forbidden in polaris/kernelone/roles; "
                    "move PM/CE/Architect/QA/Director objects to their owner Cell"
                )
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                violations.append(f"{rel_path}: parse failed: {exc}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    _contains_business_role_token(node.name)
                ):
                    violations.append(
                        f"{rel_path}:{node.lineno}: business role definition {node.name!r} "
                        "is forbidden in polaris/kernelone/roles; use roles.runtime composition "
                        "and the role owner Cell public contract"
                    )

    duration_seconds = time.monotonic() - started
    stderr = "\n".join(violations)
    return GateRunResult(
        stage=_KERNELONE_ROLES_BUSINESS_BOUNDARY_STAGE,
        command=command,
        returncode=1 if violations else 0,
        duration_seconds=float(round(duration_seconds, 3)),
        stdout="",
        stderr=stderr,
    )


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KernelOne release CI gate suite.")
    parser.add_argument(
        "--mode",
        choices=("collect", "tests", "all"),
        default="all",
        help="collect: collect-only; tests: execute suite; all: collect then execute.",
    )
    parser.add_argument(
        "--report",
        default="workspace/meta/governance_reports/kernelone_release_gate_report.json",
        help="JSON report output path (relative to backend root).",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Print JSON report to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    suite_paths = _discover_suite_paths()

    stage_results: list[GateRunResult] = []
    stage_results.append(_check_role_runtime_public_import_boundaries())
    stage_results.append(_check_roles_runtime_cell_import_boundaries())
    stage_results.append(_check_role_runtime_capability_result_sandbox())
    stage_results.append(_check_role_execution_kernel_entrypoint_boundary())
    stage_results.append(_check_legacy_role_dialogue_entrypoint_boundary())
    stage_results.append(_check_roles_kernel_adapter_dependency_boundary())
    stage_results.append(_check_roles_kernel_dialogue_dependency_boundary())
    stage_results.append(_check_roles_kernel_runtime_dependency_boundary())
    stage_results.append(_check_kernelone_roles_business_boundary())

    if args.mode in {"collect", "all"}:
        stage_results.append(_run_pytest("collect", ["--collect-only", "-q", *suite_paths]))
    if args.mode in {"tests", "all"}:
        stage_results.append(_run_pytest("tests", ["-q", *suite_paths]))

    ok = all(result.ok for result in stage_results)
    payload = {
        "ok": ok,
        "mode": args.mode,
        "suite_size": len(suite_paths),
        "suite_paths": suite_paths,
        "results": [
            {
                **asdict(result),
                "ok": result.ok,
            }
            for result in stage_results
        ],
    }

    report_path = Path(str(args.report)).expanduser()
    if not report_path.is_absolute():
        report_path = (BACKEND_ROOT / report_path).resolve()
    _write_report(report_path, payload)

    if args.print_report:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if ok:
        return 0

    for result in stage_results:
        if result.ok:
            continue
        print(
            f"[kernelone-release-gate] stage={result.stage} failed rc={result.returncode}",
            file=sys.stderr,
        )
        if result.stdout.strip():
            print(result.stdout, file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
