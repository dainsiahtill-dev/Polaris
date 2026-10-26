"""Public bindings and their ordered governance projections stay truthful."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
import yaml
from polaris.cells.events import fact_stream
from polaris.cells.events.fact_stream import public
from polaris.cells.events.fact_stream.public import contracts
from polaris.kernelone.events.sourcing.payload_store import PayloadReferenceV1

_BINDING_GROUPS = {
    "contracts": (
        "AppendFactEventCommandV1",
        "AppendIfGuardedSnapshotCommandV1",
        "AppendPayloadFactEventCommandV1",
        "AppendSegmentedFactEventCommandV1",
        "BootstrapFactStreamWorkspaceCommandV1",
        "EnrollFactStreamStreamsCommandV1",
        "EnsureSegmentedFactLedgerCommandV1",
        "FactEventAppendedV1",
        "FactPayloadReferenceV1",
        "FactStreamError",
        "FactStreamHeadV1",
        "FactStreamLockIdentityV1",
        "FactStreamLockKeyEvidenceV1",
        "FactStreamMaintenanceProofV1",
        "FactStreamMaintenanceReceiptV1",
        "FactStreamProvenanceV1",
        "FactStreamQueryResultV1",
        "GuardedFactAppendedV1",
        "GuardedFactEventV1",
        "GuardedFactSnapshotProofV1",
        "GuardedFactSnapshotV1",
        "ProvisionFactStreamLockAuthorityCommandV1",
        "QueryFactEventsV1",
        "QueryFactStreamHeadV1",
        "QueryPayloadFactByIdempotencyKeyV1",
        "QueryPayloadFactEventsV1",
        "QuerySegmentedFactEventsV1",
        "QuerySegmentedFactLedgerHeadV1",
        "ReadGuardedFactSnapshotCommandV1",
        "SegmentedFactEventAppendedV1",
        "SegmentedFactLedgerHeadV1",
        "SegmentedFactLedgerReadyV1",
        "SegmentedFactQueryResultV1",
        "StoreFactPayloadCommandV1",
    ),
    "service": (
        "append_fact_event",
        "append_if_guarded_snapshot",
        "append_payload_fact_event",
        "append_segmented_fact_event",
        "configure_debug_tracing",
        "emit_debug_event",
        "enroll_fact_stream_streams",
        "ensure_segmented_fact_ledger",
        "install_global_debug_hooks",
        "is_debug_tracing_enabled",
        "log_stream_token",
        "provision_fact_stream_lock_authority",
        "query_fact_events",
        "query_fact_stream_head",
        "query_payload_fact_by_idempotency_key",
        "query_payload_fact_events",
        "query_payload_fact_ledger_presence",
        "query_segmented_fact_events",
        "query_segmented_fact_ledger_head",
        "read_guarded_fact_snapshot",
        "sanitize_headers",
        "set_debug_tracing_enabled",
        "store_fact_payload",
    ),
    "catalog": ("fact_stream_bootstrap_streams",),
    "workspace_bootstrap": ("bootstrap_fact_stream_workspace",),
}
_EXPECTED_EXPORTS = sorted(name for names in _BINDING_GROUPS.values() for name in names)
_CELL_ROOT = Path(public.__file__).resolve().parents[1]


def test_root_facade_preserves_every_existing_public_binding_identity() -> None:
    assert public.__all__ == _EXPECTED_EXPORTS
    assert fact_stream.__all__ == _EXPECTED_EXPORTS
    for module_name, names in _BINDING_GROUPS.items():
        origin = importlib.import_module(f"{public.__name__}.{module_name}")
        for name in names:
            binding = getattr(origin, name)
            assert getattr(public, name) is binding, name
            assert getattr(fact_stream, name) is binding, name


@pytest.mark.parametrize(
    "name",
    (
        "AppendPayloadFactEventCommandV1",
        "FactPayloadReferenceV1",
        "QueryPayloadFactByIdempotencyKeyV1",
        "QueryPayloadFactEventsV1",
        "StoreFactPayloadCommandV1",
    ),
)
def test_existing_payload_contract_is_available_to_contract_discovery(name: str) -> None:
    assert name in contracts.__all__
    assert getattr(contracts, name) is getattr(public, name)


def test_payload_reference_keeps_existing_kernelone_identity() -> None:
    assert public.FactPayloadReferenceV1 is PayloadReferenceV1


@pytest.mark.parametrize("artifact", ("cell.yaml", "generated/context.pack.json", "README.agent.md"))
def test_public_surface_projection_preserves_exact_binding_order(artifact: str) -> None:
    content = (_CELL_ROOT / artifact).read_text(encoding="utf-8")
    if artifact.endswith(".json"):
        exports = json.loads(content)["public_surface"]["exports"]
    elif artifact.endswith(".yaml"):
        exports = yaml.safe_load(content)["public_surface"]["exports"]
    else:
        section = content.split("## Public Surface\n", 1)[1].split("\n## ", 1)[0]
        exports = [line[3:-1] for line in section.splitlines() if line.startswith("- `")]
    assert exports == _EXPECTED_EXPORTS


def test_payload_stream_catalog_remains_same_cell_helper() -> None:
    catalog = importlib.import_module(f"{public.__name__}.catalog")
    assert catalog.fact_payload_streams() == ("roles.kernel.turn_outcomes.v3",)
    assert "fact_payload_streams" not in catalog.__all__
    assert not hasattr(public, "fact_payload_streams")
