"""Architecture guard: LLM execution file I/O must go through KernelFileSystem."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
KERNELONE_ROOT = BACKEND_ROOT / "polaris"
BASELINE_PATH = BACKEND_ROOT / "tests" / "architecture" / "allowlists" / "kfs_direct_write_baseline.txt"
LLM_FILE_IO_SURFACES = (
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "executor.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "protocol_kernel.py",
)
KFS_DIRECT_IO_HARD_ALLOWLIST = {
    "polaris/infrastructure/storage/local_fs_adapter.py",
    "polaris/kernelone/fs/runtime.py",
    "polaris/kernelone/fs/text_ops.py",
    "polaris/kernelone/fs/memory_snapshot.py",
    "polaris/kernelone/fs/control_flags.py",
    "polaris/kernelone/fs/jsonl/ops.py",
    "polaris/kernelone/fs/jsonl/locking.py",
}

DISALLOWED_ATTRIBUTE_CALLS = {
    "open",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "unlink",
}


def _find_direct_file_io_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}:{node.lineno} builtins.open")
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in DISALLOWED_ATTRIBUTE_CALLS:
            violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}:{node.lineno} .{node.func.attr}(...)")
    return violations


def _is_test_file(path: Path) -> bool:
    rel = path.relative_to(BACKEND_ROOT).as_posix()
    return "/tests/" in rel or path.name.startswith("test_") or path.name.endswith("_test.py")


def _is_write_mode(call: ast.Call) -> bool:
    mode_value: str | None = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
        mode_value = call.args[1].value
    for keyword in call.keywords:
        if keyword.arg != "mode":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            mode_value = keyword.value.value
            break
    if mode_value is None:
        return False
    token = mode_value.lower()
    return any(ch in token for ch in ("w", "a", "x", "+"))


def _collect_direct_write_counts() -> Counter[str]:
    counts: Counter[str] = Counter()
    for py_file in KERNELONE_ROOT.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_ROOT).as_posix()
        if rel in KFS_DIRECT_IO_HARD_ALLOWLIST:
            continue
        if _is_test_file(py_file):
            continue
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "open" and _is_write_mode(node):
                counts[rel] += 1
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"write_text", "write_bytes"}:
                counts[rel] += 1
                continue
            if node.func.attr == "open" and _is_write_mode(node):
                counts[rel] += 1
                continue
            if node.func.attr == "fdopen" and _is_write_mode(node):
                counts[rel] += 1
                continue
    return counts


def _load_write_baseline() -> dict[str, int]:
    assert BASELINE_PATH.is_file(), f"Missing KFS write baseline file: {BASELINE_PATH}"
    baseline: dict[str, int] = {}
    for raw_line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(f"Invalid baseline line: {line}")
        path, count_text = line.split("=", 1)
        baseline[path.strip()] = int(count_text.strip())
    return baseline


def test_llm_file_io_surfaces_use_kernel_filesystem_only() -> None:
    violations: list[str] = []

    for module_path in LLM_FILE_IO_SURFACES:
        assert module_path.is_file(), f"Missing LLM file I/O surface: {module_path}"
        source = module_path.read_text(encoding="utf-8")
        assert "KernelFileSystem" in source, (
            "LLM file I/O surface must explicitly depend on KernelFileSystem: "
            f"{module_path.relative_to(BACKEND_ROOT).as_posix()}"
        )
        violations.extend(_find_direct_file_io_calls(module_path))

    if violations:
        pytest.fail(
            "LLM file I/O guard failed: direct file API detected outside KernelFileSystem.\n" + "\n".join(violations)
        )


def test_business_runtime_direct_write_is_baselined_and_non_regressive() -> None:
    """
    Governance rule:
    - Business/runtime write I/O should converge to KFS.
    - During migration, legacy direct-write callsites are strictly baselined.
    - New direct-write files/calls are rejected.
    """

    observed = _collect_direct_write_counts()
    baseline = _load_write_baseline()

    regressions: list[str] = []

    for path, count in sorted(observed.items()):
        allowed_count = baseline.get(path)
        if allowed_count is None:
            regressions.append(f"NEW_DIRECT_WRITE_FILE {path} observed={count} baseline=0")
            continue
        if count > allowed_count:
            regressions.append(f"DIRECT_WRITE_COUNT_INCREASE {path} observed={count} baseline={allowed_count}")

    if regressions:
        pytest.fail(
            "KFS direct-write guard failed. Business/runtime direct disk writes regressed.\n" + "\n".join(regressions)
        )
