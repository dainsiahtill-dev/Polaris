from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.llm.embedding import get_default_embedding_port
from polaris.kernelone.storage import resolve_workspace_persistent_path
from polaris.kernelone.telemetry.metrics import MetricsRecorder, Timer
from polaris.kernelone.utils.time_utils import utc_now_str

from .public.contracts import CellDescriptorV1, SearchCellsQueryV1, SearchCellsResultV1

logger = logging.getLogger(__name__)

_ROOT_REQUIRED_FIELDS = (
    "version",
    "generated_at",
    "workspace",
    "embedding_runtime_fingerprint",
    "descriptors",
)

_DESCRIPTOR_REQUIRED_FIELDS = (
    "cell_id",
    "title",
    "primary_category",
    "domain",
    "kind",
    "visibility",
    "stateful",
    "owner",
    "capability_summary",
    "purpose",
    "schema_version",
    "descriptor_version",
    "generated_at",
    "graph_fingerprint",
    "descriptor_hash",
    "embedding_runtime_fingerprint",
    "derived_from",
    "classification",
    "subgraphs",
    "when_to_use",
    "when_not_to_use",
    "responsibilities",
    "non_goals",
    "invariants",
    "key_invariants",
    "testability",
    "public_contracts",
    "dependencies",
    "state_owners",
    "effects_allowed",
    "source_hash",
    "descriptor_text",
    "embedding_vector",
    "embedding_provider",
    "embedding_model_name",
    "embedding_device",
)

_DERIVED_FROM_REQUIRED_FIELDS = (
    "cell_manifest",
    "readme",
    "context_pack",
    "code_fingerprint",
)

_CLASSIFICATION_REQUIRED_FIELDS = (
    "plane",
    "kind",
    "domain",
    "role",
    "state_profile",
    "effect_profile",
    "criticality",
)

_EMBEDDING_RUNTIME_FINGERPRINT = "graph-catalog-seed:none"
_EMBEDDING_PROVIDER = "graph_catalog_seed"
_EMBEDDING_MODEL_NAME = "none"
_EMBEDDING_DEVICE = "cpu"


# Backward compatibility alias
_utc_now_iso = utc_now_str


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_context_catalog_cache_path(workspace: str | Path) -> Path:
    return Path(
        resolve_workspace_persistent_path(
            str(Path(workspace).resolve()),
            "workspace/meta/context_catalog/descriptors.json",
        )
    )


def resolve_context_catalog_index_state_path(workspace: str | Path) -> Path:
    return Path(
        resolve_workspace_persistent_path(
            str(Path(workspace).resolve()),
            "workspace/meta/context_catalog/index_state.json",
        )
    )


def validate_descriptor_cache_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["descriptor cache root must be an object"]

    for field in _ROOT_REQUIRED_FIELDS:
        if field not in payload:
            errors.append(f"missing root field: {field}")

    descriptors = payload.get("descriptors")
    if not isinstance(descriptors, list):
        errors.append("root field 'descriptors' must be a list")
        return errors

    for index, descriptor in enumerate(descriptors):
        prefix = f"descriptors[{index}]"
        if not isinstance(descriptor, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in _DESCRIPTOR_REQUIRED_FIELDS:
            if field not in descriptor:
                errors.append(f"{prefix} missing field: {field}")
        derived_from = descriptor.get("derived_from")
        if isinstance(derived_from, dict):
            for field in _DERIVED_FROM_REQUIRED_FIELDS:
                if field not in derived_from:
                    errors.append(f"{prefix}.derived_from missing field: {field}")
        else:
            errors.append(f"{prefix}.derived_from must be an object")
        classification = descriptor.get("classification")
        if isinstance(classification, dict):
            for field in _CLASSIFICATION_REQUIRED_FIELDS:
                if field not in classification:
                    errors.append(f"{prefix}.classification missing field: {field}")
        else:
            errors.append(f"{prefix}.classification must be an object")
        embedding_vector = descriptor.get("embedding_vector")
        if not isinstance(embedding_vector, list) or not embedding_vector:
            errors.append(f"{prefix}.embedding_vector must be a non-empty list")
        elif not all(isinstance(item, (int, float)) for item in embedding_vector):
            errors.append(f"{prefix}.embedding_vector must contain only numbers")
    return errors


class ContextCatalogService:
    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).resolve()
        self.catalog_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        self.subgraphs_root = self.workspace / "docs" / "graph" / "subgraphs"
        self.cell_root = self.workspace / "polaris" / "cells"
        self.fs = KernelFileSystem(str(self.workspace), get_default_adapter())

        # Initialize metrics recorder
        self.metrics = MetricsRecorder()
        self.metrics.define_histogram(
            "catalog_search_duration_seconds",
            description="Catalog search duration in seconds",
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        )
        self.metrics.define_counter(
            "catalog_search_total", description="Total number of catalog searches", labels=["status"]
        )
        self.metrics.define_histogram(
            "catalog_sync_duration_seconds",
            description="Catalog sync duration in seconds",
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )
        self.metrics.define_counter("catalog_sync_total", description="Total number of catalog syncs")

    @property
    def cache_path(self) -> Path:
        return resolve_context_catalog_cache_path(self.workspace)

    @property
    def index_state_path(self) -> Path:
        return resolve_context_catalog_index_state_path(self.workspace)

    def sync(self) -> dict[str, Any]:
        with Timer("catalog_sync_timer") as timer:
            result = self._sync_impl()
            # Record metrics
            duration_seconds = timer.elapsed_seconds
            self.metrics.get_histogram("catalog_sync_duration_seconds").observe(duration_seconds)
            self.metrics.get_counter("catalog_sync_total").inc()
            return result

    def _sync_impl(self) -> dict[str, Any]:
        graph_payload = self._load_graph_catalog()
        generated_at = _utc_now_iso()
        graph_fingerprint = self._compute_graph_fingerprint()

        # Get dynamic fingerprint from port
        embedding_fingerprint = get_default_embedding_port().get_fingerprint()

        descriptors = [
            self._build_descriptor(
                cell_entry=cell_entry,
                generated_at=generated_at,
                graph_fingerprint=graph_fingerprint,
            )
            for cell_entry in graph_payload.get("cells", [])
            if isinstance(cell_entry, dict)
        ]
        payload = {
            "version": 1,
            "generated_at": generated_at,
            "workspace": str(self.workspace),
            "embedding_runtime_fingerprint": embedding_fingerprint,
            "descriptors": descriptors,
        }
        errors = validate_descriptor_cache_payload(payload)
        if errors:
            raise ValueError("descriptor cache payload invalid: " + "; ".join(errors))
        self.fs.write_json(
            "workspace/meta/context_catalog/descriptors.json",
            payload,
            indent=2,
            ensure_ascii=False,
        )
        index_state = {
            "version": 1,
            "generated_at": generated_at,
            "workspace": str(self.workspace),
            "graph_fingerprint": graph_fingerprint,
            "descriptor_count": len(descriptors),
            "embedding_runtime_fingerprint": embedding_fingerprint,
            "descriptor_cache_path": str(self.cache_path),
        }
        self.fs.write_json(
            "workspace/meta/context_catalog/index_state.json",
            index_state,
            indent=2,
            ensure_ascii=False,
        )
        return {
            "cache_path": str(self.cache_path),
            "index_state_path": str(self.index_state_path),
            "descriptor_count": len(descriptors),
            "graph_fingerprint": graph_fingerprint,
        }

    def is_index_stale(self) -> bool:
        if not self.cache_path.exists() or not self.index_state_path.exists():
            return True

        embedding_fingerprint = get_default_embedding_port().get_fingerprint()

        try:
            payload = json.loads(self.fs.read_text("workspace/meta/context_catalog/descriptors.json"))
            index_state = json.loads(self.fs.read_text("workspace/meta/context_catalog/index_state.json"))
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to load context catalog cache: %s", exc)
            return True
        if validate_descriptor_cache_payload(payload):
            return True
        if not isinstance(index_state, dict):
            return True
        if index_state.get("graph_fingerprint") != self._compute_graph_fingerprint():
            return True
        descriptors = payload.get("descriptors")
        if not isinstance(descriptors, list):
            return True
        if index_state.get("descriptor_count") != len(descriptors):
            return True
        if payload.get("embedding_runtime_fingerprint") != embedding_fingerprint:
            return True
        return index_state.get("embedding_runtime_fingerprint") != embedding_fingerprint

    def search(self, query: SearchCellsQueryV1) -> SearchCellsResultV1:
        with Timer("catalog_search_timer") as timer:
            result = self._search_impl(query)
            # Record metrics
            duration_seconds = timer.elapsed_seconds
            self.metrics.get_histogram("catalog_search_duration_seconds").observe(duration_seconds)
            status = "success" if result and result.total > 0 else "empty"
            self.metrics.get_counter("catalog_search_total", {"status": status}).inc()
            return result

    def _search_impl(self, query: SearchCellsQueryV1) -> SearchCellsResultV1:
        payload = self._load_cache_payload()
        raw_descriptors = payload.get("descriptors", [])
        if not isinstance(raw_descriptors, list):
            return SearchCellsResultV1(descriptors=(), total=0)

        tokens = self._tokenize(query.query)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for descriptor in raw_descriptors:
            if not isinstance(descriptor, dict):
                continue
            searchable_text = " ".join(
                [
                    str(descriptor.get("cell_id", "")),
                    str(descriptor.get("title", "")),
                    str(descriptor.get("purpose", "")),
                    str(descriptor.get("capability_summary", "")),
                    str(descriptor.get("descriptor_text", "")),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in searchable_text)
            if score > 0:
                ranked.append((score, descriptor))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("cell_id", ""))))
        selected = ranked[: max(query.limit, 0)]
        descriptors = tuple(
            CellDescriptorV1(
                cell_id=str(item["cell_id"]),
                title=str(item["title"]),
                purpose=str(item["purpose"]),
                domain=str(item["domain"]),
                kind=str(item["kind"]),
                visibility=str(item["visibility"]),
                stateful=bool(item["stateful"]),
                owner=str(item["owner"]),
                capability_summary=str(item["capability_summary"]),
            )
            for _, item in selected
        )
        return SearchCellsResultV1(descriptors=descriptors, total=len(ranked))

    def list_cells(self) -> list[dict[str, Any]]:
        """List all cells from the graph catalog.

        Returns:
            List of cell dictionaries with basic info.
        """
        payload = self._load_graph_catalog()
        cells = payload.get("cells", [])
        if not isinstance(cells, list):
            return []
        return [
            {
                "cell_id": str(cell.get("id", "")),
                "name": str(cell.get("name", "")),
                "migration_status": str(cell.get("migration_status", "")),
            }
            for cell in cells
            if isinstance(cell, dict)
        ]

    def get_cell(self, cell_id: str) -> dict[str, Any] | None:
        """Get a specific cell by ID.

        Args:
            cell_id: The cell ID to look up.

        Returns:
            Cell dictionary or None if not found.
        """
        cells = self.list_cells()
        for cell in cells:
            if cell.get("cell_id") == cell_id:
                return cell
        return None

    def _load_graph_catalog(self) -> dict[str, Any]:
        payload = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"graph catalog must be an object: {self.catalog_path}")
        return payload

    def _load_cache_payload(self) -> dict[str, Any]:
        payload = json.loads(self.fs.read_text("workspace/meta/context_catalog/descriptors.json"))
        if not isinstance(payload, dict):
            raise ValueError(f"descriptor cache must be an object: {self.cache_path}")
        return payload

    def _compute_graph_fingerprint(self) -> str:
        fragments = [self.catalog_path.read_text(encoding="utf-8")]
        if self.subgraphs_root.exists():
            for path in sorted(self.subgraphs_root.glob("*.yaml")):
                fragments.append(path.read_text(encoding="utf-8"))
        return _sha256_text("\n".join(fragments))

    def _build_descriptor(
        self,
        *,
        cell_entry: dict[str, Any],
        generated_at: str,
        graph_fingerprint: str,
    ) -> dict[str, Any]:
        cell_id = str(cell_entry.get("id") or "")
        title = str(cell_entry.get("title") or cell_id)
        purpose = str(cell_entry.get("purpose") or "")
        kind = str(cell_entry.get("kind") or "capability")
        domain = self._infer_domain(cell_entry, cell_id)
        visibility = str(cell_entry.get("visibility") or "internal")
        stateful = bool(cell_entry.get("stateful"))
        owner = str(cell_entry.get("owner") or "unknown")
        subgraphs = self._string_list(cell_entry.get("subgraphs"))
        dependencies = self._string_list(cell_entry.get("depends_on"))
        state_owners = self._string_list(cell_entry.get("state_owners"))
        effects_allowed = self._string_list(cell_entry.get("effects_allowed"))
        owned_paths = self._string_list(cell_entry.get("owned_paths"))
        source_hash = _sha256_text(_json_dumps(cell_entry))
        cell_rel_root = self._cell_relative_root(cell_id)
        descriptor_text = " ".join(
            part
            for part in [
                cell_id,
                title,
                purpose,
                " ".join(subgraphs),
                " ".join(dependencies),
                " ".join(state_owners),
                " ".join(effects_allowed),
            ]
            if part
        )
        public_contracts = cell_entry.get("public_contracts")
        if not isinstance(public_contracts, dict):
            public_contracts = {
                "commands": [],
                "queries": [],
                "events": [],
                "results": [],
                "errors": [],
            }
        verification = cell_entry.get("verification")
        verify_tests = []
        if isinstance(verification, dict):
            verify_tests = self._string_list(verification.get("tests"))
        responsibilities = [purpose] if purpose else [f"Owns {cell_id} capability boundary."]
        when_to_use = [f"Use when work targets {cell_id} or its declared contracts."]
        when_not_to_use = ["Do not use this descriptor as a replacement for graph truth."]
        invariants = [
            "Graph assets remain the source of truth.",
            "Descriptor cache is derived, UTF-8 encoded, and rebuildable.",
        ]
        effect_profile = sorted({effect.split(":", 1)[0] for effect in effects_allowed})

        # Use real embedding port
        embedding_port = get_default_embedding_port()
        embedding_vector = embedding_port.get_embedding(descriptor_text)
        embedding_fingerprint = embedding_port.get_fingerprint()

        descriptor = {
            "cell_id": cell_id,
            "title": title,
            "primary_category": domain,
            "domain": domain,
            "kind": kind,
            "visibility": visibility,
            "stateful": stateful,
            "owner": owner,
            "capability_summary": purpose or f"{cell_id} capability",
            "purpose": purpose,
            "schema_version": 1,
            "descriptor_version": 1,
            "generated_at": generated_at,
            "graph_fingerprint": graph_fingerprint,
            "embedding_runtime_fingerprint": embedding_fingerprint,
            "derived_from": {
                "cell_manifest": f"{cell_rel_root}/cell.yaml",
                "readme": f"{cell_rel_root}/README.agent.md",
                "context_pack": f"{cell_rel_root}/generated/context.pack.json",
                "code_fingerprint": source_hash,
            },
            "classification": {
                "plane": self._infer_plane(domain, kind),
                "kind": kind,
                "domain": domain,
                "role": "system" if visibility == "public" else "internal",
                "state_profile": "owner" if state_owners else "stateless",
                "effect_profile": effect_profile,
                "criticality": "high" if stateful or bool(effect_profile) else "normal",
            },
            "subgraphs": subgraphs,
            "when_to_use": when_to_use,
            "when_not_to_use": when_not_to_use,
            "responsibilities": responsibilities,
            "non_goals": ["Does not override graph truth."],
            "invariants": invariants,
            "key_invariants": invariants,
            "testability": {
                "standalone_runnable": True,
                "standalone_testable": True,
                "stateful": stateful,
                "verify_targets": verify_tests,
                "requires_injected_ports": dependencies,
            },
            "classification_tags": [
                domain,
                kind,
                visibility,
                "stateful" if stateful else "stateless",
            ],
            "public_contracts": public_contracts,
            "dependencies": dependencies,
            "state_owners": state_owners,
            "effects_allowed": effects_allowed,
            "owned_paths": owned_paths,
            "source_files": [str(self.catalog_path.relative_to(self.workspace)), *owned_paths],
            "source_hash": source_hash,
            "descriptor_text": descriptor_text,
            "embedding_vector": embedding_vector,
            "embedding_provider": embedding_fingerprint.split("/")[0],
            "embedding_model_name": embedding_fingerprint.split("/")[1].split(":")[0],
            "embedding_device": embedding_fingerprint.split(":")[-1] if ":" in embedding_fingerprint else "cpu",
            "metadata": {
                "asset_root": cell_rel_root,
                "seed_source": "docs/graph/catalog/cells.yaml",
            },
        }
        descriptor["descriptor_hash"] = _sha256_text(_json_dumps(descriptor))
        return descriptor

    def _cell_relative_root(self, cell_id: str) -> str:
        return "polaris/cells/" + cell_id.replace(".", "/")

    def _infer_domain(self, cell_entry: dict[str, Any], cell_id: str) -> str:
        raw = cell_entry.get("domain")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if "." in cell_id:
            return cell_id.split(".", 1)[0]
        return "system"

    def _infer_plane(self, domain: str, kind: str) -> str:
        if domain == "context":
            return "context"
        if kind == "governance":
            return "governance"
        if kind == "policy":
            return "policy"
        return "capability"

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _tokenize(self, query: str) -> list[str]:
        return [token for token in query.lower().split() if token]


def _result_to_jsonable(result: SearchCellsResultV1) -> dict[str, Any]:
    return {
        "total": result.total,
        "descriptors": [asdict(descriptor) for descriptor in result.descriptors],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Context catalog migration seed service.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Build descriptor cache from graph catalog.")
    sync_parser.add_argument("--workspace", default=".", help="Backend workspace root.")
    sync_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    search_parser = subparsers.add_parser("search", help="Search descriptor cache lexically.")
    search_parser.add_argument("--workspace", default=".", help="Backend workspace root.")
    search_parser.add_argument("--query", required=True, help="Search query.")
    search_parser.add_argument("--limit", type=int, default=10, help="Result limit.")

    args = parser.parse_args(argv)
    service = ContextCatalogService(args.workspace)
    if args.command == "sync":
        sync_result = service.sync()
        if args.json:
            logger.info("%s", json.dumps(sync_result, ensure_ascii=False, indent=2))
        else:
            logger.info("%s", sync_result["cache_path"])
        return 0
    if args.command == "search":
        search_result = service.search(SearchCellsQueryV1(query=args.query, limit=args.limit))
        logger.info("%s", json.dumps(_result_to_jsonable(search_result), ensure_ascii=False, indent=2))
        return 0
    return 1
