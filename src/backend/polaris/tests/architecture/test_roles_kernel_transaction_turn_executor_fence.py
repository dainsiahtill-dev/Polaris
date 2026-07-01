"""Architecture fence for the roles.kernel transaction-turn boundary."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.kernel.internal.kernel.transaction_turn_executor import (
    TransactionTurnExecutor,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
ROLES_KERNEL_ROOT = POLARIS_ROOT / "cells" / "roles" / "kernel"


def _production_python_sources(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts and "tests" not in path.parts]


def test_retired_turn_execution_module_does_not_exist() -> None:
    retired_path = ROLES_KERNEL_ROOT / "internal" / "kernel" / "turn_execution.py"

    assert not retired_path.exists()


def test_roles_kernel_production_code_uses_transaction_turn_executor() -> None:
    forbidden_tokens = {
        "turn_execution",
        "execute_transaction_kernel_turn",
        "execute_transaction_kernel_stream",
    }

    offenders: list[str] = []
    for path in _production_python_sources(ROLES_KERNEL_ROOT):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in text:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}::{token}")

    assert offenders == []


def test_role_execution_kernel_stays_api_shell() -> None:
    core_path = ROLES_KERNEL_ROOT / "internal" / "kernel" / "core.py"
    source = core_path.read_text(encoding="utf-8")

    assert "create_transaction_kernel" not in source
    assert "TransactionKernel" not in source
    assert "execute_non_stream_role_turn" in source
    assert "execute_stream_role_turn" in source


def test_transaction_turn_executor_is_explicit_application_service() -> None:
    assert callable(TransactionTurnExecutor.execute_turn)
    assert callable(TransactionTurnExecutor.execute_stream)
