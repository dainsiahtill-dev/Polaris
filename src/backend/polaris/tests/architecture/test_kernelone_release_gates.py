from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
KERNELONE_ROOT = BACKEND_ROOT / "polaris" / "kernelone"
EXCEPTION_BUDGET_PATH = (
    BACKEND_ROOT / "polaris" / "tests" / "architecture" / "allowlists" / "kernelone_exception_budget.json"
)
AGENTS_PATH = BACKEND_ROOT / "AGENTS.md"
CLAUDE_PATH = BACKEND_ROOT / "CLAUDE.md"
GEMINI_PATH = BACKEND_ROOT / "GEMINI.md"

FORBIDDEN_LAYER_SEGMENTS = {
    "application",
    "cells",
    "delivery",
    "domain",
    "infrastructure",
}
RELATIVE_FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*from\s+\.+(?:polaris\.)?(application|cells|delivery|domain|infrastructure)\b"
)

# ---------------------------------------------------------------------------
# Deprecated-shim allowlist (ACGA 2.0 migration bridges).
# KernelOne files that intentionally re-export from polaris.cells or
# polaris.domain as backward-compatibility bridges during the migration.
# These are temporary: once all callers are updated, these shims must be removed.
# ---------------------------------------------------------------------------
DEPRECATION_SHIM_PATHS: frozenset[str] = frozenset(
    {
        # Agent-34: director toolchain migration (kernelone -> cells)
        # These files are DEPRECATION SHIMS: they live in KernelOne but re-export
        # from polaris.cells to provide backward compatibility during the migration.
        # They must be removed once all callers are updated to import from cells.
        "polaris/kernelone/tools/director/__init__.py",
        "polaris/kernelone/tools/director/chain.py",
        "polaris/kernelone/tools/director/cli_builder.py",
        "polaris/kernelone/tools/director/constants.py",
        "polaris/kernelone/tools/director/executor.py",
        "polaris/kernelone/tools/director/executor_core.py",
        "polaris/kernelone/tools/director/models.py",
        "polaris/kernelone/tools/director/output.py",
        "polaris/kernelone/tools/director/plan_parser.py",
        "polaris/kernelone/tools/director/security.py",
        "polaris/kernelone/tools/director/tooling_constants.py",
        "polaris/kernelone/tools/director/utils.py",
        # Agent-32: role alias normalization moved to cells layer
        "polaris/kernelone/prompts/meta_prompting.py",
        # Agent-35: TaskBoard re-export from task_runtime cell
        "polaris/kernelone/task_graph/task_board.py",
        # Historical migration bridges pending retirement.
        # Agent-34 continued: runtime_executor bridges director tools via cells
        "polaris/kernelone/tools/runtime_executor.py",
        # ACGA 2.0 migration: KernelOne runtime defaults re-export domain constants
        "polaris/kernelone/runtime/defaults.py",
        # ACGA 2.0 migration: KernelOne LLM provider registry bridges infrastructure providers
        "polaris/kernelone/llm/providers/registry.py",
        # ACGA 2.0 migration: KernelOne FS registry bridges infrastructure storage adapters
        "polaris/kernelone/fs/registry.py",
        # ACGA 2.0 migration: KernelOne event publisher bridges cells storage layout
        "polaris/kernelone/events/uep_publisher.py",
        # ACGA 2.0 migration: KernelOne task trace events resolve message bus via DI container
        "polaris/kernelone/events/task_trace_events.py",
        # ACGA 2.0 migration: KernelOne context models import metrics for budget tracking
        "polaris/kernelone/context/context_os/models.py",
        # ACGA 2.0 migration: KernelOne chunk assembler optionally integrates cells metrics
        "polaris/kernelone/context/chunks/assembler.py",
        # ACGA 2.0 migration: KernelOne cognitive orchestrator bridges cells alignment service
        "polaris/kernelone/cognitive/orchestrator.py",
        # ACGA 2.0 migration: KernelOne multi-agent bus port bridges cells runtime bus
        "polaris/kernelone/multi_agent/bus_port.py",
        # ACGA 2.0 migration: KernelOne NATS broker bridges cells runtime bus port
        "polaris/kernelone/multi_agent/neural_syndicate/nats_broker.py",
        # Expert 3 (Wave 1): KernelOne SLM summarizer bridges cells transaction gateway
        # ADR-0067-extension: ContextOS 2.0 SLM semantic compression layer
        "polaris/kernelone/context/context_os/summarizers/slm.py",
        # Expert 3 (Wave 1): KernelOne alignment port abstracts cells alignment service
        # ACGA 2.0 Section 6.3: KernelOne defines interface contracts, Cells provide implementations
        "polaris/kernelone/ports/alignment.py",
    }
)

EXCEPT_EXCEPTION_RE = re.compile(r"\bexcept\s+Exception\b")
BARE_PASS_RE = re.compile(r"^\s*pass\s*(?:#.*)?$")


@dataclass(frozen=True)
class ExceptionBudget:
    max_total_except_exception: int
    max_total_bare_pass: int
    hotspot_trigger_except_exception: int
    hotspot_trigger_bare_pass: int
    hotspot_max_except_exception: dict[str, int]
    hotspot_max_bare_pass: dict[str, int]


@dataclass(frozen=True)
class ExceptionMetrics:
    total_except_exception: int
    total_bare_pass: int
    except_by_file: dict[str, int]
    pass_by_file: dict[str, int]


@dataclass(frozen=True)
class SnapshotFacts:
    date: str
    migration_status: str
    declared_cells: int
    descriptor_covered: int
    descriptor_total: int
    collect_count: int
    collect_errors: int
    descriptor_generator_command_present: bool


def _iter_kernelone_sources() -> Iterable[Path]:
    for path in sorted(KERNELONE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_forbidden_polaris_import(module_name: str, *, source_path: Path | None = None) -> bool:
    token = str(module_name or "").strip()
    if not token.startswith("polaris."):
        return False
    parts = token.split(".")
    if len(parts) < 2:
        return False
    if parts[1] not in FORBIDDEN_LAYER_SEGMENTS:
        return False
    # Allow documented deprecation shims that re-export from cells/domain.
    if source_path is not None:
        rel = source_path.relative_to(BACKEND_ROOT).as_posix()
        if rel in DEPRECATION_SHIM_PATHS:
            return False
        # Test files legitimately import cross-layer for integration testing.
        if "/tests/" in rel:
            return False
        # Benchmark files are cross-layer by design (measure upper layers).
        if "/benchmark/" in rel:
            return False
    return True


def _load_exception_budget() -> ExceptionBudget:
    payload = json.loads(EXCEPTION_BUDGET_PATH.read_text(encoding="utf-8"))
    return ExceptionBudget(
        max_total_except_exception=int(payload["max_total_except_exception"]),
        max_total_bare_pass=int(payload["max_total_bare_pass"]),
        hotspot_trigger_except_exception=int(payload["hotspot_trigger_except_exception"]),
        hotspot_trigger_bare_pass=int(payload["hotspot_trigger_bare_pass"]),
        hotspot_max_except_exception={
            str(path): int(count) for path, count in dict(payload["hotspot_max_except_exception"]).items()
        },
        hotspot_max_bare_pass={str(path): int(count) for path, count in dict(payload["hotspot_max_bare_pass"]).items()},
    )


def _collect_exception_metrics() -> ExceptionMetrics:
    except_by_file: dict[str, int] = {}
    pass_by_file: dict[str, int] = {}

    total_except_exception = 0
    total_bare_pass = 0

    for path in _iter_kernelone_sources():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()

        except_count = sum(1 for line in lines if EXCEPT_EXCEPTION_RE.search(line))
        pass_count = sum(1 for line in lines if BARE_PASS_RE.search(line))

        if except_count:
            except_by_file[rel] = except_count
            total_except_exception += except_count
        if pass_count:
            pass_by_file[rel] = pass_count
            total_bare_pass += pass_count

    return ExceptionMetrics(
        total_except_exception=total_except_exception,
        total_bare_pass=total_bare_pass,
        except_by_file=except_by_file,
        pass_by_file=pass_by_file,
    )


def _extract_snapshot_facts(path: Path) -> SnapshotFacts:
    text = path.read_text(encoding="utf-8")

    date_match = re.search(r"当前架构现实快照（(\d{4}-\d{2}-\d{2})）", text)
    status_match = re.search(r"migration_status:\s*`?([a-z0-9_]+)`?", text, re.IGNORECASE)
    cells_match = re.search(r"Cell[：:]\s*\*\*(\d+)", text)
    descriptor_match = re.search(r"descriptor.*覆盖[：:]\s*\*\*(\d+)\s*/\s*(\d+)\*\*", text, re.IGNORECASE)
    collect_match = re.search(r"(\d+)\s+collected\s*/\s*(\d+)\s+errors", text, re.IGNORECASE)

    assert date_match is not None, f"missing snapshot date in {path}"
    assert status_match is not None, f"missing migration_status in {path}"
    assert cells_match is not None, f"missing declared cell count in {path}"
    assert descriptor_match is not None, f"missing descriptor coverage in {path}"
    assert collect_match is not None, f"missing collect-only snapshot in {path}"

    return SnapshotFacts(
        date=str(date_match.group(1)),
        migration_status=str(status_match.group(1)),
        declared_cells=int(cells_match.group(1)),
        descriptor_covered=int(descriptor_match.group(1)),
        descriptor_total=int(descriptor_match.group(2)),
        collect_count=int(collect_match.group(1)),
        collect_errors=int(collect_match.group(2)),
        descriptor_generator_command_present=(
            "python -m polaris.cells.context.catalog.internal.descriptor_pack_generator" in text
        ),
    )


def _kernelone_release_suite_paths() -> list[str]:
    candidates: list[Path] = []
    candidates.extend(sorted((BACKEND_ROOT / "polaris" / "tests").glob("test_kernelone_*.py")))
    candidates.extend(sorted((BACKEND_ROOT / "polaris" / "tests" / "architecture").glob("test_kernelone_*.py")))
    candidates.append(BACKEND_ROOT / "polaris" / "tests" / "architecture" / "test_polaris_kernel_fs_guard.py")

    files: list[str] = []
    for path in candidates:
        if path.exists():
            files.append(path.relative_to(BACKEND_ROOT).as_posix())
    return files


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def test_kernelone_import_fence_blocks_reverse_layer_imports() -> None:
    violations: list[str] = []

    for path in _iter_kernelone_sources():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        rel = path.relative_to(BACKEND_ROOT).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden_polaris_import(alias.name, source_path=path):
                        violations.append(f"{rel}:{node.lineno} import {alias.name}")
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module and _is_forbidden_polaris_import(node.module, source_path=path):
                violations.append(f"{rel}:{node.lineno} from {node.module} import ...")

        for line_number, raw_line in enumerate(source.splitlines(), start=1):
            if RELATIVE_FORBIDDEN_IMPORT_RE.search(raw_line):
                violations.append(f"{rel}:{line_number} {raw_line.strip()}")

    if violations:
        formatted = "\n".join(f"  - {entry}" for entry in sorted(set(violations)))
        pytest.fail(f"KernelOne import fence violated: reverse-layer imports detected.\n{formatted}")


def test_kernelone_exception_budget_is_non_regressive() -> None:
    assert EXCEPTION_BUDGET_PATH.is_file(), f"missing exception budget file: {EXCEPTION_BUDGET_PATH}"

    budget = _load_exception_budget()
    metrics = _collect_exception_metrics()

    assert metrics.total_except_exception <= budget.max_total_except_exception, (
        "except Exception regression detected: "
        f"observed={metrics.total_except_exception} "
        f"budget={budget.max_total_except_exception}"
    )
    assert metrics.total_bare_pass <= budget.max_total_bare_pass, (
        f"bare pass regression detected: observed={metrics.total_bare_pass} budget={budget.max_total_bare_pass}"
    )

    for path, allowed in budget.hotspot_max_except_exception.items():
        observed = metrics.except_by_file.get(path, 0)
        assert observed <= allowed, f"except Exception hotspot regression: {path} observed={observed} budget={allowed}"
    for path, allowed in budget.hotspot_max_bare_pass.items():
        observed = metrics.pass_by_file.get(path, 0)
        assert observed <= allowed, f"bare pass hotspot regression: {path} observed={observed} budget={allowed}"

    emergent_except_hotspots = sorted(
        (
            f"{path}={count}"
            for path, count in metrics.except_by_file.items()
            if count >= budget.hotspot_trigger_except_exception and path not in budget.hotspot_max_except_exception
        )
    )
    emergent_pass_hotspots = sorted(
        (
            f"{path}={count}"
            for path, count in metrics.pass_by_file.items()
            if count >= budget.hotspot_trigger_bare_pass and path not in budget.hotspot_max_bare_pass
        )
    )
    if emergent_except_hotspots or emergent_pass_hotspots:
        details = []
        if emergent_except_hotspots:
            details.append(
                "emergent except hotspots:\n" + "\n".join(f"  - {item}" for item in emergent_except_hotspots)
            )
        if emergent_pass_hotspots:
            details.append(
                "emergent bare-pass hotspots:\n" + "\n".join(f"  - {item}" for item in emergent_pass_hotspots)
            )
        pytest.fail("KernelOne exception budget has new hotspots.\n" + "\n".join(details))


def test_kernelone_command_execution_contract_is_hardened() -> None:
    executor_path = BACKEND_ROOT / "polaris" / "kernelone" / "process" / "command_executor.py"
    security_path = BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "security.py"

    executor_source = executor_path.read_text(encoding="utf-8")
    security_source = security_path.read_text(encoding="utf-8")
    executor_tree = ast.parse(executor_source, filename=str(executor_path))

    shell_true_calls: list[int] = []
    shell_false_calls: list[int] = []
    for node in ast.walk(executor_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            continue
        if node.func.attr != "run":
            continue
        for keyword in node.keywords:
            if keyword.arg != "shell":
                continue
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                shell_true_calls.append(node.lineno)
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
                shell_false_calls.append(node.lineno)

    assert not shell_true_calls, f"subprocess.run uses shell=True at lines: {shell_true_calls}"
    assert shell_false_calls, "subprocess.run must explicitly set shell=False"
    assert 'encoding="utf-8"' in executor_source
    assert "_PYTHON_INLINE_FLAGS" in executor_source and '"-c"' in executor_source
    assert "_NODE_INLINE_FLAGS" in executor_source and '"--eval"' in executor_source

    shell_meta_guard_hits = security_source.count("if _contains_shell_metacharacters(command):")
    assert shell_meta_guard_hits >= 2

    # --- npx safe-package allowlist ---
    assert "_SAFE_NPX_PACKAGES" in executor_source, (
        "command_executor.py must define _SAFE_NPX_PACKAGES for npx security"
    )
    for pkg in ("tsc", "typescript", "eslint", "mypy", "ruff"):
        assert pkg in executor_source, f"_SAFE_NPX_PACKAGES must include '{pkg}'"
    # npx itself must be in the package-manager names set
    assert '"npx"' in executor_source or "'npx'" in executor_source, "npx must be in _PACKAGE_MANAGER_EXECUTABLE_NAMES"

    # --- _validate_npx_execution method exists ---
    assert "_validate_npx_execution" in executor_source, "command_executor.py must have _validate_npx_execution method"

    # --- _validate_workspace_boundary exists (prevents allowlist bypass) ---
    assert "_validate_workspace_boundary" in executor_source, (
        "command_executor.py must have _validate_workspace_boundary to prevent "
        "allowlist-based workspace boundary bypass"
    )

    # --- run() must catch ValueError from build_subprocess_spec() ---
    # Check that the try block in run() wraps build_subprocess_spec
    assert "_validate_request" in executor_source, "validation must be called in run()"
    assert "except (OSError, RuntimeError, TypeError, ValueError" in executor_source, (
        "run() must catch ValueError so validation errors return error dict, not raise"
    )


def test_agent_instruction_snapshot_is_consistent() -> None:
    agents = _extract_snapshot_facts(AGENTS_PATH)
    claude = _extract_snapshot_facts(CLAUDE_PATH)
    gemini = _extract_snapshot_facts(GEMINI_PATH)

    assert claude == agents
    assert gemini == agents
    assert agents.descriptor_generator_command_present is True


def test_kernelone_release_suite_collect_only_has_no_errors() -> None:
    suite_paths = _kernelone_release_suite_paths()
    assert suite_paths, "KernelOne release suite is empty"

    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        *suite_paths,
    ]

    completed = subprocess.run(
        command,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_build_utf8_env(),
        timeout=150,
        check=False,
    )
    assert completed.returncode == 0, (
        "KernelOne release collect-only gate failed.\n"
        f"command: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
