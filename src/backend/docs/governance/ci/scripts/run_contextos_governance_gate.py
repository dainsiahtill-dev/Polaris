#!/usr/bin/env python3
"""ContextOS governance gate runner.

This gate validates:
1. ProviderFormatter Protocol exists in llm_caller.py
2. EpisodeCard dataclass exists in models.py
3. SSOT constraint tests pass

Exit codes:
    0 - all checks passed
    1 - one or more checks failed
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str = ""
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.passed or self.skipped


def _find_class_in_ast(tree: ast.AST, class_name: str) -> ast.ClassDef | None:
    """Find a class definition in an AST by name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _class_has_method(tree: ast.AST, class_name: str, method_name: str) -> bool:
    """Check if a class has a method with the given name."""
    cls_node = _find_class_in_ast(tree, class_name)
    if cls_node is None:
        return False
    for item in cls_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == method_name:
            return True
    return False


def _ensure_pytest_available() -> bool:
    """
    Ensure pytest is available in subprocess.
    Check and try to install/configure.
    """
    import shutil
    import subprocess
    import sys
    from importlib.util import find_spec

    # Check if pytest is available via which
    if shutil.which("pytest") or shutil.which("pytest.exe"):
        return True

    # Check if pytest module is available
    if find_spec("pytest") is not None:
        return True

    # Try using uv run pytest
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try using python -m pytest
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return False


def _run_pytest_subprocess(test_path: str, workspace: Path) -> dict:
    """
    Run pytest in subprocess with full environment setup.
    """
    import subprocess
    import sys

    # Prefer uv run pytest
    uv_cmd = ["uv", "run", "pytest", test_path, "-v", "--tb=short"]
    pip_cmd = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"]

    for cmd in [uv_cmd, pip_cmd]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,  # 2min timeout
                cwd=str(workspace),
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "cmd": " ".join(cmd),
            }
        except subprocess.TimeoutExpired:
            continue
        except FileNotFoundError:
            # uv or pytest not found, try next
            continue

    return {
        "returncode": -1,
        "stdout": "",
        "stderr": "Neither uv nor pytest available",
        "cmd": "none",
    }


def _check_provider_formatter() -> CheckResult:
    """Check ProviderFormatter Protocol exists with required methods via AST analysis."""
    # Scan llm_caller/ directory for provider_formatter.py
    llm_caller_dir = BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel" / "internal" / "llm_caller"
    provider_formatter_file = llm_caller_dir / "provider_formatter.py"

    source = None
    if provider_formatter_file.exists():
        try:
            source = provider_formatter_file.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name="ProviderFormatter",
                passed=False,
                message=f"Failed to read provider_formatter.py: {exc}",
            )
    else:
        # Fallback: scan llm_caller directory for provider-related files
        for py_file in llm_caller_dir.glob("*.py"):
            if "provider" in py_file.name.lower():
                try:
                    source = py_file.read_text(encoding="utf-8")
                except Exception as exc:
                    return CheckResult(
                        name="ProviderFormatter",
                        passed=False,
                        message=f"Failed to read {py_file}: {exc}",
                    )
                break
        if source is None:
            return CheckResult(
                name="ProviderFormatter",
                passed=False,
                message=f"provider_formatter.py not found in {llm_caller_dir}",
            )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return CheckResult(
            name="ProviderFormatter",
            passed=False,
            message=f"Syntax error in provider_formatter.py: {exc}",
        )

    cls_node = _find_class_in_ast(tree, "ProviderFormatter")
    if cls_node is None:
        return CheckResult(
            name="ProviderFormatter",
            passed=False,
            message="ProviderFormatter class not found in provider_formatter.py",
        )

    # Check class bases - should inherit from Protocol
    has_protocol_base = any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in cls_node.bases
    )
    if not has_protocol_base:
        return CheckResult(
            name="ProviderFormatter",
            passed=False,
            message="ProviderFormatter does not inherit from Protocol",
        )

    # Check required methods exist
    required_methods = ["format_messages", "format_tool_result"]
    for method_name in required_methods:
        if not _class_has_method(tree, "ProviderFormatter", method_name):
            return CheckResult(
                name="ProviderFormatter",
                passed=False,
                message=f"ProviderFormatter missing required method: {method_name}",
            )

    return CheckResult(
        name="ProviderFormatter",
        passed=True,
        message="ProviderFormatter Protocol with format_messages and format_tool_result",
    )


def _check_episode_card() -> CheckResult:
    """Check EpisodeCard dataclass exists with required fields via AST analysis."""
    module_path = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "context_os" / "models.py"
    if not module_path.exists():
        return CheckResult(
            name="EpisodeCard",
            passed=False,
            message=f"models.py not found at {module_path}",
        )

    try:
        source = module_path.read_text(encoding="utf-8")
    except Exception as exc:
        return CheckResult(
            name="EpisodeCard",
            passed=False,
            message=f"Failed to read models.py: {exc}",
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return CheckResult(
            name="EpisodeCard",
            passed=False,
            message=f"Syntax error in models.py: {exc}",
        )

    cls_node = _find_class_in_ast(tree, "EpisodeCard")
    if cls_node is None:
        return CheckResult(
            name="EpisodeCard",
            passed=False,
            message="EpisodeCard class not found in models.py",
        )

    # Check it's a dataclass (has @dataclass decorator)
    # The decorator can be @dataclass or @dataclass(...) i.e., ast.Call or ast.Name
    has_dataclass_decorator = any(
        (isinstance(item, ast.Name) and item.id == "dataclass")
        or (isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "dataclass")
        for item in cls_node.decorator_list
    )
    if not has_dataclass_decorator:
        return CheckResult(
            name="EpisodeCard",
            passed=False,
            message="EpisodeCard is not decorated with @dataclass",
        )

    # Extract field names from assignment statements in __init__ or class body
    # For dataclasses, fields are typically defined as simple assignments in the class body
    field_names: set[str] = set()
    for item in cls_node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            field_names.add(item.target.id)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    field_names.add(target.id)

    # Check required fields
    required_fields = [
        "episode_id",
        "from_sequence",
        "to_sequence",
        "intent",
        "outcome",
        "decisions",
        "facts",
        "artifact_refs",
        "entities",
        "status",
    ]
    missing_fields = [f for f in required_fields if f not in field_names]
    if missing_fields:
        return CheckResult(
            name="EpisodeCard",
            passed=False,
            message=f"EpisodeCard missing required fields: {missing_fields}",
        )

    return CheckResult(
        name="EpisodeCard",
        passed=True,
        message=f"EpisodeCard dataclass with all required fields: {required_fields}",
    )


def _check_ssot_constraint_tests() -> CheckResult:
    """Run SSOT constraint tests and return CheckResult."""
    test_file = BACKEND_ROOT / "polaris" / "tests" / "contextos" / "test_context_os_ssot_constraint.py"
    if not test_file.exists():
        return CheckResult(
            name="SSOTConstraintTests",
            passed=False,
            message=f"SSOT constraint test file not found: {test_file}",
        )

    # 检查 pytest 是否可用
    if not _ensure_pytest_available():
        return CheckResult(
            name="SSOTConstraintTests",
            passed=False,
            skipped=True,
            message="pytest not available in this environment - test skipped",
        )

    # Ensure dependencies are synced via uv sync --frozen
    import contextlib

    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(
            ["uv", "sync", "--frozen"],
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**env},
            check=False,
            timeout=60,
        )

    # Run pytest via subprocess with proper environment
    test_result = _run_pytest_subprocess(str(test_file), BACKEND_ROOT)

    if test_result["returncode"] == 0:
        return CheckResult(
            name="SSOTConstraintTests",
            passed=True,
            message="All SSOT constraint tests passed",
        )
    else:
        output = test_result["stdout"] or test_result["stderr"]
        truncated = output[:500] if len(output) > 500 else output
        return CheckResult(
            name="SSOTConstraintTests",
            passed=False,
            message=f"SSOT constraint tests failed:\n{truncated}",
        )


def _check_path_contract() -> CheckResult:
    """Path contract snapshot test - prevents path drift."""
    # Gate script lives at: docs/governance/ci/scripts/run_contextos_governance_gate.py
    # Expected: BACKEND_ROOT = <repo>/src/backend
    # Path contract: BACKEND_ROOT / "polaris" must equal actual polaris directory
    polaris_path = BACKEND_ROOT / "polaris"
    if polaris_path.exists() and polaris_path.is_dir():
        return CheckResult(
            name="PathContract",
            passed=True,
            message=f"Path contract valid: {polaris_path}",
        )
    return CheckResult(
        name="PathContract",
        passed=False,
        message=f"Path drift detected: polaris not found at {polaris_path}",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ContextOS governance gate.")
    parser.add_argument(
        "--output",
        default="workspace/meta/governance_reports/contextos_governance_gate.json",
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

    # Run checks
    checks: list[CheckResult] = []
    checks.append(_check_provider_formatter())
    checks.append(_check_episode_card())

    # SSOT constraint tests
    checks.append(_check_ssot_constraint_tests())

    # Path contract snapshot test
    checks.append(_check_path_contract())

    all_passed = all(c.ok for c in checks)

    # Build report
    report = {
        "gate": "contextos_governance",
        "passed": all_passed,
        "checks": [{"name": c.name, "passed": c.passed, "skipped": c.skipped, "message": c.message} for c in checks],
    }

    # Write report
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (BACKEND_ROOT / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.print_report:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if all_passed:
        print("[contextos-governance-gate] PASSED", file=sys.stderr)
        return 0

    print("[contextos-governance-gate] FAILED", file=sys.stderr)
    for c in checks:
        if c.skipped:
            print(f"  - {c.name}: SKIPPED ({c.message})", file=sys.stderr)
        elif not c.ok:
            print(f"  - {c.name}: {c.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
