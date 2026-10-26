"""Focused structural tests for the FactStream governance surface gate."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from docs.governance.ci.scripts.run_catalog_governance_gate import (
    _RULE_FACT_STREAM_SURFACE_DRIFT,
    GovernanceIssue,
    _check_fact_stream_surface_drift,
)

_CONTRACTS: dict[str, list[str]] = {
    "commands": [
        "AppendFactEventCommandV1",
        "AppendIfGuardedSnapshotCommandV1",
        "AppendPayloadFactEventCommandV1",
        "AppendSegmentedFactEventCommandV1",
        "EnsureSegmentedFactLedgerCommandV1",
        "StoreFactPayloadCommandV1",
    ],
    "queries": [
        "QueryFactEventsV1",
        "QueryPayloadFactByIdempotencyKeyV1",
        "QueryPayloadFactEventsV1",
        "QuerySegmentedFactEventsV1",
        "QuerySegmentedFactLedgerHeadV1",
        "ReadGuardedFactSnapshotCommandV1",
    ],
    "events": ["FactEventAppendedV1", "GuardedFactEventV1", "SegmentedFactEventAppendedV1"],
    "results": [
        "FactPayloadReferenceV1",
        "FactStreamHeadV1",
        "SegmentedFactLedgerHeadV1",
        "SegmentedFactLedgerReadyV1",
        "SegmentedFactQueryResultV1",
    ],
    "errors": ["FactStreamError"],
}
_CONTRACT_EXPORTS = [item for values in _CONTRACTS.values() for item in values]
_PUBLIC_EXPORTS = [
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
    "append_fact_event",
    "append_if_guarded_snapshot",
    "append_payload_fact_event",
    "append_segmented_fact_event",
    "bootstrap_fact_stream_workspace",
    "configure_debug_tracing",
    "emit_debug_event",
    "enroll_fact_stream_streams",
    "ensure_segmented_fact_ledger",
    "fact_stream_bootstrap_streams",
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
]
_PUBLIC_MODULES = [
    "polaris.cells.events.fact_stream.public",
    "polaris.cells.events.fact_stream.public.catalog",
    "polaris.cells.events.fact_stream.public.contracts",
    "polaris.cells.events.fact_stream.public.service",
    "polaris.cells.events.fact_stream.public.workspace_bootstrap",
]
_EFFECTS = ["fs.read:runtime/events/*", "fs.write:runtime/events/*"]
_OWNED_PATHS = ["polaris/cells/events/fact_stream/**"]


def _python_list(values: list[str]) -> str:
    return "[\n" + "".join(f'    "{value}",\n' for value in values) + "]\n"


def _write_surface_fixture(repo_root: Path) -> None:
    """Write a complete synthetic surface without importing the Cell package."""
    cell_root = repo_root / "polaris" / "cells" / "events" / "fact_stream"
    public_root = cell_root / "public"
    context_root = cell_root / "generated"
    public_root.mkdir(parents=True)
    context_root.mkdir()

    contract_bindings = [name for name in _PUBLIC_EXPORTS if name[0].isupper()]
    service_bindings = [
        name
        for name in _PUBLIC_EXPORTS
        if name[0].islower() and name not in {"fact_stream_bootstrap_streams", "bootstrap_fact_stream_workspace"}
    ]
    origins = {
        "contracts": contract_bindings,
        "service": service_bindings,
        "catalog": ["fact_stream_bootstrap_streams"],
        "workspace_bootstrap": ["bootstrap_fact_stream_workspace"],
    }
    public_imports = "".join(f"from .{module} import {', '.join(names)}\n" for module, names in origins.items())
    (public_root / "__init__.py").write_text(
        public_imports + "\n__all__ = " + _python_list(_PUBLIC_EXPORTS), encoding="utf-8"
    )
    definitions = "".join(
        f"class {name}({'RuntimeError' if name == 'FactStreamError' else 'object'}):\n    pass\n\n"
        for name in contract_bindings
    )
    (public_root / "contracts.py").write_text(
        definitions + "__all__ = " + _python_list(_CONTRACT_EXPORTS),
        encoding="utf-8",
    )
    for module in ("service", "catalog", "workspace_bootstrap"):
        (public_root / f"{module}.py").write_text(
            "".join(f"def {name}():\n    pass\n\n" for name in origins[module]),
            encoding="utf-8",
        )
    guarded_names = (
        "AppendIfGuardedSnapshotCommandV1",
        "GuardedFactAppendedV1",
        "GuardedFactEventV1",
        "GuardedFactSnapshotProofV1",
        "GuardedFactSnapshotV1",
        "ReadGuardedFactSnapshotCommandV1",
    )
    sourcing = repo_root / "polaris/kernelone/events/sourcing"
    sourcing.mkdir(parents=True)
    (sourcing / "guarded.py").write_text(
        "".join(f"class {name}:\n    pass\n\n" for name in guarded_names), encoding="utf-8"
    )
    (sourcing / "payload_store.py").write_text("class PayloadReferenceV1:\n    pass\n", encoding="utf-8")
    contracts_path = public_root / "contracts.py"
    content = contracts_path.read_text(encoding="utf-8")
    for name in guarded_names:
        content = content.replace(
            f"class {name}(object):\n    pass\n\n",
            f"from polaris.kernelone.events.sourcing.guarded import {name}\n",
        )
    content = content.replace(
        "class FactPayloadReferenceV1(object):\n    pass\n\n",
        "from polaris.kernelone.events.sourcing.payload_store import PayloadReferenceV1 as FactPayloadReferenceV1\n",
    )
    contracts_path.write_text(content, encoding="utf-8")
    debug_names = (
        "configure_debug_tracing",
        "emit_debug_event",
        "install_global_debug_hooks",
        "is_debug_tracing_enabled",
        "log_stream_token",
        "sanitize_headers",
        "set_debug_tracing_enabled",
    )
    internal = cell_root / "internal"
    internal.mkdir()
    (internal / "debug_trace.py").write_text(
        "".join(f"def {name}():\n    pass\n\n" for name in debug_names if name != "sanitize_headers")
        + "def _sanitize_headers():\n    pass\n\nsanitize_headers = _sanitize_headers\n",
        encoding="utf-8",
    )
    service_path = public_root / "service.py"
    content = service_path.read_text(encoding="utf-8")
    for name in debug_names:
        content = content.replace(
            f"def {name}():\n    pass\n\n",
            f"from polaris.cells.events.fact_stream.internal.debug_trace import {name}\n",
        )
    service_path.write_text(content, encoding="utf-8")
    imports = ", ".join(_PUBLIC_EXPORTS)
    (cell_root / "__init__.py").write_text(
        f"from .public import {imports}\n\n__all__ = " + _python_list(_PUBLIC_EXPORTS),
        encoding="utf-8",
    )

    common: dict[str, Any] = {
        "id": "events.fact_stream",
        "current_modules": _PUBLIC_MODULES,
        "owned_paths": _OWNED_PATHS,
        "depends_on": [],
        "effects_allowed": _EFFECTS,
        "public_surface": {"exports": _PUBLIC_EXPORTS},
        "public_contracts": _CONTRACTS,
    }
    (cell_root / "cell.yaml").write_text(
        yaml.safe_dump(common, sort_keys=False),
        encoding="utf-8",
    )
    catalog = {"cells": [dict(common)]}
    (repo_root / "docs" / "graph" / "catalog").mkdir(parents=True)
    (repo_root / "docs" / "graph" / "catalog" / "cells.yaml").write_text(
        yaml.safe_dump(catalog, sort_keys=False),
        encoding="utf-8",
    )
    context = {
        "id": "events.fact_stream",
        "owned_paths": _OWNED_PATHS,
        "neighbors": [],
        "effects_allowed": _EFFECTS,
        "public_surface": {"exports": _PUBLIC_EXPORTS},
        "public_contracts": _CONTRACTS,
    }
    (context_root / "context.pack.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# Events Fact Stream", "", "## Public Surface", ""]
    lines.extend(f"- `{name}`" for name in _PUBLIC_EXPORTS)
    lines.extend(["", "## Public Contracts", ""])
    for kind, names in _CONTRACTS.items():
        lines.append(f"- {kind}:")
        lines.extend(f"  - `{name}`" for name in names)
    lines.extend(["", "FactStreamErrorV1 is historical prose, not a declared contract.", ""])
    (cell_root / "README.agent.md").write_text("\n".join(lines), encoding="utf-8")


def _surface_issues(repo_root: Path) -> list[GovernanceIssue]:
    catalog_path = repo_root / "docs" / "graph" / "catalog" / "cells.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    issues: list[GovernanceIssue] = []
    _check_fact_stream_surface_drift(
        repo_root=repo_root,
        catalog_payload=catalog,
        issues=issues,
    )
    return issues


def test_complete_fact_stream_surface_passes_without_keyword_false_positive() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        _write_surface_fixture(repo_root)

        assert _surface_issues(repo_root) == []


def test_missing_root_export_is_a_structured_surface_failure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        _write_surface_fixture(repo_root)
        root_path = repo_root / "polaris" / "cells" / "events" / "fact_stream" / "__init__.py"
        root_path.write_text(
            root_path.read_text(encoding="utf-8").replace(
                '    "AppendIfGuardedSnapshotCommandV1",\n',
                "",
            ),
            encoding="utf-8",
        )

        issues = _surface_issues(repo_root)

        assert any(issue.rule_id == _RULE_FACT_STREAM_SURFACE_DRIFT for issue in issues)
        assert any("root facade drift" in issue.message for issue in issues)


@pytest.mark.parametrize(
    "removed_export",
    (
        "AppendFactEventCommandV1",
        "FactStreamLockIdentityV1",
        "append_fact_event",
    ),
)
def test_paired_public_facade_removal_fails_for_contract_support_and_service(
    removed_export: str,
) -> None:
    """The approved inventory catches matching root/public removals by symbol kind."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        _write_surface_fixture(repo_root)
        for relative_path in (
            "polaris/cells/events/fact_stream/public/__init__.py",
            "polaris/cells/events/fact_stream/__init__.py",
        ):
            path = repo_root / relative_path
            path.write_text(
                path.read_text(encoding="utf-8").replace(f'    "{removed_export}",\n', ""),
                encoding="utf-8",
            )

        issues = _surface_issues(repo_root)

        assert any("must expose exactly 59 names" in issue.message for issue in issues)


@pytest.mark.parametrize("artifact", ("manifest", "catalog", "context pack", "README"))
def test_public_surface_projection_rejects_removal_addition_and_order_drift(artifact: str) -> None:
    """All public-surface artifacts are exact ordered projections, not keyword scans."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        _write_surface_fixture(repo_root)
        if artifact == "README":
            path = repo_root / "polaris/cells/events/fact_stream/README.agent.md"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace("- `AppendFactEventCommandV1`\n", "", 1),
                encoding="utf-8",
            )
        else:
            if artifact == "manifest":
                path = repo_root / "polaris/cells/events/fact_stream/cell.yaml"
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                exports = payload["public_surface"]["exports"]
                exports[0], exports[1] = exports[1], exports[0]
                path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            elif artifact == "catalog":
                path = repo_root / "docs/graph/catalog/cells.yaml"
                catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
                exports = catalog["cells"][0]["public_surface"]["exports"]
                exports[0], exports[1] = exports[1], exports[0]
                path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")
            else:
                path = repo_root / "polaris/cells/events/fact_stream/generated/context.pack.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                exports = payload["public_surface"]["exports"]
                exports[0], exports[1] = exports[1], exports[0]
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        issues = _surface_issues(repo_root)

        assert any("public_surface.exports drift" in issue.message for issue in issues)


def test_manifest_catalog_error_mismatch_and_unknown_contract_are_reported() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        _write_surface_fixture(repo_root)
        manifest_path = repo_root / "polaris" / "cells" / "events" / "fact_stream" / "cell.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["public_contracts"]["errors"] = ["FactStreamErrorV1"]
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        catalog_path = repo_root / "docs" / "graph" / "catalog" / "cells.yaml"
        catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        catalog["cells"][0]["public_contracts"]["commands"].append("UnknownFactCommandV1")
        catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")

        issues = _surface_issues(repo_root)
        messages = [issue.message for issue in issues]

        assert any("manifest declares nonexistent errors: FactStreamErrorV1" in message for message in messages)
        assert any("catalog declares nonexistent commands: UnknownFactCommandV1" in message for message in messages)


def test_missing_required_effect_is_reported_from_structured_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        _write_surface_fixture(repo_root)
        context_path = repo_root / "polaris" / "cells" / "events" / "fact_stream" / "generated" / "context.pack.json"
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context["effects_allowed"] = ["fs.read:runtime/events/*"]
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

        issues = _surface_issues(repo_root)

        assert any(
            "context pack effects_allowed drift: missing=fs.write:runtime/events/*" in issue.message for issue in issues
        )


@pytest.mark.parametrize("attack", ("missing_import", "wrong_alias", "missing_implementation"))
def test_public_exports_require_their_actual_approved_binding(attack: str, tmp_path: Path) -> None:
    """A matching __all__ count cannot stand in for a callable implementation."""
    _write_surface_fixture(tmp_path)
    public_root = tmp_path / "polaris/cells/events/fact_stream/public"
    path = public_root / ("service.py" if attack == "missing_implementation" else "__init__.py")
    content = path.read_text(encoding="utf-8")
    if attack == "missing_import":
        content = content.replace("query_payload_fact_events, ", "", 1)
    elif attack == "wrong_alias":
        content = content.replace(
            "query_payload_fact_events, ",
            "query_payload_fact_by_idempotency_key as query_payload_fact_events, ",
            1,
        )
    else:
        content = content.replace("def query_payload_fact_events():\n    pass\n\n", "", 1)
    path.write_text(content, encoding="utf-8")

    messages = [issue.message for issue in _surface_issues(tmp_path)]

    assert any("public binding" in message and "query_payload_fact_events" in message for message in messages)


@pytest.mark.parametrize("replacement", ("phantom_payload_query", "query_payload_fact_events"))
def test_same_count_export_substitution_cannot_change_approved_inventory(replacement: str, tmp_path: Path) -> None:
    _write_surface_fixture(tmp_path)
    for relative_path in (
        "polaris/cells/events/fact_stream/public/__init__.py",
        "polaris/cells/events/fact_stream/__init__.py",
    ):
        path = tmp_path / relative_path
        path.write_text(
            path.read_text(encoding="utf-8").replace('"append_fact_event"', f'"{replacement}"'),
            encoding="utf-8",
        )

    messages = [issue.message for issue in _surface_issues(tmp_path)]

    assert any("approved public inventory" in message for message in messages)


def test_payload_contract_discovery_cannot_omit_existing_dto(tmp_path: Path) -> None:
    _write_surface_fixture(tmp_path)
    path = tmp_path / "polaris/cells/events/fact_stream/public/contracts.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace('    "StoreFactPayloadCommandV1",\n', ""),
        encoding="utf-8",
    )

    messages = [issue.message for issue in _surface_issues(tmp_path)]

    assert any("nonexistent commands: StoreFactPayloadCommandV1" in message for message in messages)


@pytest.mark.parametrize("attack", ("missing_external", "wrong_existing_alias", "cycle", "missing_local"))
def test_import_alias_must_resolve_to_approved_existing_origin(attack: str, tmp_path: Path) -> None:
    """An import's local name alone cannot prove a public capability exists."""
    _write_surface_fixture(tmp_path)
    public_root = tmp_path / "polaris/cells/events/fact_stream/public"
    path = public_root / "service.py"
    replacements = {
        "missing_external": "from nonexistent_fact_surface_module import absent_query as query_payload_fact_events\n",
        "wrong_existing_alias": "from .service import query_fact_events as query_payload_fact_events\n",
        "cycle": "from .origin_cycle import query_payload_fact_events\n",
        "missing_local": "from .origin_missing import query_payload_fact_events\n",
    }
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "def query_payload_fact_events():\n    pass\n\n", replacements[attack], 1
        ),
        encoding="utf-8",
    )
    if attack == "cycle":
        (public_root / "origin_cycle.py").write_text(
            "from .service import query_payload_fact_events\n", encoding="utf-8"
        )

    messages = [issue.message for issue in _surface_issues(tmp_path)]

    assert any("public binding" in message and "query_payload_fact_events" in message for message in messages)


def test_real_repo_alias_origins_remain_accepted_without_executing_audited_code() -> None:
    repo_root = Path(__file__).resolve().parents[5]
    assert _surface_issues(repo_root) == []
