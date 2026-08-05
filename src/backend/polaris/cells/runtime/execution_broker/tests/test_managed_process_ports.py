"""Contract and binding proof for GR3B managed-process ports."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import managed_process_ports as registry
from polaris.cells.runtime.execution_broker.public import __all__ as public_exports
from polaris.cells.runtime.execution_broker.public.bootstrap import bind_managed_process_ports
from polaris.cells.runtime.execution_broker.public.contracts import (
    AppendManagedProcessReceiptCommandV1,
    ExecutionBrokerError,
    GetManagedProcessReceiptQueryV1,
    ManagedProcessPortsV1,
    ManagedProcessReceiptAppendResultV1,
    ManagedProcessReceiptRecordV1,
    ManagedProcessReceiptStorePortV1,
)
from polaris.cells.runtime.execution_broker.public.service import (
    __all__ as service_exports,
    get_managed_process_ports,
)


class _ReceiptStore:
    def append_managed_process_receipt(
        self,
        command: AppendManagedProcessReceiptCommandV1,
        /,
    ) -> ManagedProcessReceiptAppendResultV1:
        return ManagedProcessReceiptAppendResultV1(
            receipt_ref=f"audit.evidence:{command.receipt_hash}",
            receipt_hash=command.receipt_hash,
            already_present=False,
        )

    def get_managed_process_receipt(
        self,
        query: GetManagedProcessReceiptQueryV1,
        /,
    ) -> ManagedProcessReceiptRecordV1 | None:
        return ManagedProcessReceiptRecordV1(
            receipt_ref=f"audit.evidence:{query.receipt_hash}",
            receipt_hash=query.receipt_hash,
            receipt={"schema_version": "test/1"},
        )


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_managed_process_ports", None)


def _ports() -> ManagedProcessPortsV1:
    return ManagedProcessPortsV1(
        receipt_store=_ReceiptStore(),
    )


def test_managed_process_port_contracts_are_structural() -> None:
    ports = _ports()

    assert isinstance(ports.receipt_store, ManagedProcessReceiptStorePortV1)
    with pytest.raises(TypeError, match="receipt_store"):
        ManagedProcessPortsV1(
            receipt_store=object(),  # type: ignore[arg-type]
        )


def test_managed_process_receipt_contracts_bind_identity_without_persisting() -> None:
    receipt_hash = "a" * 64
    command = AppendManagedProcessReceiptCommandV1(
        workspace=".",
        receipt_hash=receipt_hash,
        receipt={"schema_version": "managed_process_receipt/1"},
    )
    query = GetManagedProcessReceiptQueryV1(workspace=".", receipt_hash=receipt_hash)
    store = _ReceiptStore()

    appended = store.append_managed_process_receipt(command)
    record = store.get_managed_process_receipt(query)

    assert appended.receipt_hash == receipt_hash
    assert appended.already_present is False
    assert record is not None and record.receipt_hash == receipt_hash
    with pytest.raises(ValueError, match="SHA-256"):
        GetManagedProcessReceiptQueryV1(workspace=".", receipt_hash="bad")


def test_managed_process_ports_binding_is_same_object_idempotent() -> None:
    ports = _ports()

    bind_managed_process_ports(ports)
    bind_managed_process_ports(ports)

    assert get_managed_process_ports() is ports


def test_managed_process_ports_conflicting_rebind_fails_closed() -> None:
    original = _ports()
    bind_managed_process_ports(original)

    with pytest.raises(ExecutionBrokerError) as exc_info:
        bind_managed_process_ports(_ports())

    assert exc_info.value.code == "execution_broker.managed_process_ports_conflicting_rebind"
    assert get_managed_process_ports() is original


def test_managed_process_ports_unbound_lookup_fails_closed() -> None:
    with pytest.raises(ExecutionBrokerError) as exc_info:
        get_managed_process_ports()

    assert exc_info.value.code == "execution_broker.managed_process_ports_unbound"


def test_bind_is_exposed_only_from_bootstrap_surface() -> None:
    assert "bind_managed_process_ports" not in public_exports
    assert "bind_managed_process_ports" not in service_exports
    assert not hasattr(registry, "reset_managed_process_ports")


def test_managed_process_binding_has_no_forbidden_owner_imports() -> None:
    cell_root = Path(__file__).resolve().parents[1]
    files = (
        cell_root / "public" / "contracts.py",
        cell_root / "public" / "bootstrap.py",
        cell_root / "public" / "service.py",
        cell_root / "internal" / "managed_process_ports.py",
    )
    forbidden = (
        "polaris.cells.factory",
        "polaris.cells.runtime.task_runtime",
        "polaris.cells.runtime.task_boundary",
        "polaris.cells.control_plane.run_ledger",
        "polaris.cells.roles.kernel",
        "polaris.cells.roles.adapters",
        "polaris.cells.director",
    )
    imported: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)

    for module in imported:
        assert not module.startswith(forbidden)
