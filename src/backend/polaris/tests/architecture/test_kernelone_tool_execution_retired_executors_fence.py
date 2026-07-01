from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TOOL_EXECUTION_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "tool_execution"


def test_deprecated_tool_execution_executors_are_removed() -> None:
    assert not (TOOL_EXECUTION_ROOT / "executor.py").exists()
    assert not (TOOL_EXECUTION_ROOT / "executor_core.py").exists()


def test_tool_execution_package_root_does_not_advertise_retired_executors() -> None:
    source = (TOOL_EXECUTION_ROOT / "__init__.py").read_text(encoding="utf-8")

    assert "executor  :" not in source
    assert "executor_core" not in source
    assert "DEPRECATED" not in source
    assert "AgentAccelToolExecutor" in source


def test_retired_tool_execution_imports_do_not_reappear() -> None:
    forbidden_imports = (
        "polaris.kernelone.tool_execution.executor",
        "polaris.kernelone.tool_execution.executor_core",
        "from polaris.kernelone.tool_execution import ToolExecutor",
        "from polaris.kernelone.tool_execution import ToolChainExecutor",
        "from polaris.kernelone.tool_execution import run_tool_plan",
        "from polaris.kernelone.tool_execution import run_tool_chain",
    )

    search_roots = (
        BACKEND_ROOT / "polaris",
        BACKEND_ROOT / "scripts",
        BACKEND_ROOT / "tests",
    )
    offenders: list[str] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in forbidden_imports:
                if forbidden in source:
                    offenders.append(f"{path.relative_to(BACKEND_ROOT)} contains {forbidden!r}")

    assert offenders == []
