from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

FINAL_SPEC_REQUIRED_CELLS = (
    "delivery.api_gateway",
    "policy.workspace_guard",
    "audit.evidence",
    "context.engine",
    "runtime.projection",
    "runtime.state_owner",
    "archive.run_archive",
    "archive.task_snapshot_archive",
    "archive.factory_archive",
)

FINAL_SPEC_EFFECT_MINIMUMS: dict[str, tuple[str, ...]] = {
    "runtime.state_owner": (
        "fs.read:runtime/*",
        "fs.write:runtime/tasks/*",
        "fs.write:runtime/contracts/*",
        "fs.write:runtime/state/*",
    ),
    "archive.run_archive": (
        "fs.read:runtime/runs/*",
        "fs.write:workspace/history/runs/*",
    ),
    "archive.task_snapshot_archive": (
        "fs.read:runtime/tasks/*",
        "fs.write:workspace/history/tasks/*",
    ),
    "archive.factory_archive": (
        "fs.read:workspace/factory/*",
        "fs.write:workspace/history/factory/*",
    ),
    "audit.evidence": (
        "fs.write:runtime/events/*",
        "fs.write:workspace/history/**/*.json",
    ),
}

FINAL_SPEC_ALLOW_MULTI_STATE_OWNER = {
    "runtime/events/*",
    "*.index.jsonl",
    "workspace/history/index/*.index.jsonl",
}

CAPABILITY_CLUSTER_BY_PREFIX = {
    "bootstrap.": "startup_and_assembly",
    "delivery.": "transport_layer",
    "application.workflows.": "workflow_orchestration",
    "runtime.": "runtime_state",
    "storage.": "storage_and_archive",
    "archive.": "storage_and_archive",
    "policy.": "policy_and_permission",
    "orchestration.": "workflow_orchestration",
    "director.": "director_execution",
    "roles.": "roles_runtime",
    "llm.": "llm_platform",
    "audit.": "audit_and_evidence",
    "resident.": "resident_capability",
    "factory.": "factory_management",
    "context.": "context_plane",
    "compatibility.": "compatibility_bridge",
}


@dataclass(frozen=True)
class WriteResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def add(self, other: WriteResult) -> WriteResult:
        return WriteResult(
            created=self.created + other.created,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
        )


def _resolve_repo_root(script_path: Path) -> Path:
    for candidate in [script_path.parent, *script_path.parents]:
        if (candidate / ".git").exists():
            return candidate
    return script_path.parents[3]


def _default_catalog_path(repo_root: Path) -> Path:
    return repo_root / "src" / "backend" / "docs" / "graph" / "catalog" / "cells.yaml"


def _default_cells_root(repo_root: Path) -> Path:
    return repo_root / "src" / "backend" / "cells"


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be object: {path}")
    return payload


def _safe_write(path: Path, content: str, *, overwrite: bool, dry_run: bool) -> WriteResult:
    exists = path.exists()
    if exists:
        current = path.read_text(encoding="utf-8")
        if current == content:
            return WriteResult(skipped=1)
        if not overwrite:
            return WriteResult(skipped=1)
        if dry_run:
            return WriteResult(updated=1)
        path.write_text(content, encoding="utf-8")
        return WriteResult(updated=1)
    if dry_run:
        return WriteResult(created=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return WriteResult(created=1)


def _cell_folder(cells_root: Path, cell_id: str) -> Path:
    tokens = [token.strip() for token in cell_id.split(".") if token.strip()]
    if len(tokens) < 2:
        raise ValueError(f"Invalid cell id: {cell_id}")
    group = tokens[0]
    name = "_".join(tokens[1:])
    return cells_root / group / name


def _require_fields(cell: dict[str, Any]) -> None:
    required = {
        "id",
        "kind",
        "owner",
        "owned_paths",
        "public_contracts",
        "depends_on",
        "state_owners",
        "effects_allowed",
    }
    missing = sorted(required - set(cell.keys()))
    if missing:
        raise ValueError(f"Cell {cell.get('id', '<unknown>')} missing required fields: {missing}")


def _render_contract_module(entries: list[str], module_name: str) -> str:
    header = [
        "from __future__ import annotations",
        "",
        f'"""Auto-generated contract module: {module_name}."""',
        "",
    ]
    names = [str(item).strip() for item in entries if str(item).strip()]
    if not names:
        header.extend(["__all__ = []", ""])
        return "\n".join(header)
    for name in names:
        header.append(f"# contract: {name}")
    header.append("")
    header.append("__all__ = [")
    for name in names:
        header.append(f'    "{name}",')
    header.append("]")
    header.append("")
    return "\n".join(header)


def _render_cell_yaml(cell: dict[str, Any]) -> str:
    cell_id = str(cell["id"])
    payload = {
        "cell_id": cell_id,
        "capability_cluster": _infer_capability_cluster(cell_id),
        "kind": cell["kind"],
        "owner": cell["owner"],
        "public": bool(cell.get("visibility", "public") == "public"),
        "owned_paths": list(cell.get("owned_paths", [])),
        "state_owners": list(cell.get("state_owners", [])),
        "depends_on": list(cell.get("depends_on", [])),
        "effects_allowed": list(cell.get("effects_allowed", [])),
        "inbound_ports": [],
        "outbound_ports": [],
        "verify_targets": list(cell.get("verification", {}).get("tests", [])),
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _infer_capability_cluster(cell_id: str) -> str:
    for prefix, cluster in CAPABILITY_CLUSTER_BY_PREFIX.items():
        if cell_id.startswith(prefix):
            return cluster
    return "unclassified"


def _render_readme(cell: dict[str, Any]) -> str:
    cell_id = str(cell["id"])
    contracts = cell.get("public_contracts", {})
    commands = contracts.get("commands", [])
    queries = contracts.get("queries", [])
    events = contracts.get("events", [])
    results = contracts.get("results", [])
    errors = contracts.get("errors", [])
    return (
        "\n".join(
            [
                f"# {cell['id']}",
                "",
                "## Purpose",
                "",
                str(cell.get("purpose", "")).strip() or "TBD",
                "",
                "## Capability Cluster",
                "",
                f"- {_infer_capability_cluster(cell_id)}",
                "",
                "## Public Contracts",
                "",
                f"- commands: {', '.join(commands) if commands else 'None'}",
                f"- queries: {', '.join(queries) if queries else 'None'}",
                f"- events: {', '.join(events) if events else 'None'}",
                f"- results: {', '.join(results) if results else 'None'}",
                f"- errors: {', '.join(errors) if errors else 'None'}",
                "",
                "## Dependencies",
                "",
                *(f"- {item}" for item in cell.get("depends_on", [])),
                "",
                "## Effects Allowed",
                "",
                *(f"- {item}" for item in cell.get("effects_allowed", [])),
                "",
            ]
        ).strip()
        + "\n"
    )


def _render_context_pack(cell: dict[str, Any]) -> str:
    contracts = cell.get("public_contracts", {})
    payload = {
        "id": cell["id"],
        "kind": cell["kind"],
        "owner": cell["owner"],
        "public": bool(cell.get("visibility", "public") == "public"),
        "owned_paths": cell.get("owned_paths", []),
        "contracts": {
            "commands": contracts.get("commands", []),
            "queries": contracts.get("queries", []),
            "events": contracts.get("events", []),
            "results": contracts.get("results", []),
            "errors": contracts.get("errors", []),
        },
        "depends_on": cell.get("depends_on", []),
        "state_owners": cell.get("state_owners", []),
        "effects_allowed": cell.get("effects_allowed", []),
        "verify_targets": cell.get("verification", {}).get("tests", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _generate_for_cell(cell: dict[str, Any], cells_root: Path, *, overwrite: bool, dry_run: bool) -> WriteResult:
    _require_fields(cell)
    target = _cell_folder(cells_root, str(cell["id"]))
    for rel in (
        "public/contracts",
        "internal/application",
        "internal/domain",
        "internal/ports",
        "internal/adapters",
        "tests",
        "generated",
    ):
        if not dry_run:
            (target / rel).mkdir(parents=True, exist_ok=True)

    result = WriteResult()
    contracts = cell.get("public_contracts", {})
    result = result.add(
        _safe_write(target / "cell.yaml", _render_cell_yaml(cell), overwrite=overwrite, dry_run=dry_run)
    )
    result = result.add(
        _safe_write(target / "README.agent.md", _render_readme(cell), overwrite=overwrite, dry_run=dry_run)
    )
    result = result.add(
        _safe_write(
            target / "public" / "api.py",
            f'"""Public API for {cell["id"]}."""\n\nCELL_ID = "{cell["id"]}"\n',
            overwrite=overwrite,
            dry_run=dry_run,
        )
    )
    result = result.add(
        _safe_write(
            target / "public" / "contracts" / "commands.py",
            _render_contract_module(list(contracts.get("commands", [])), "commands"),
            overwrite=overwrite,
            dry_run=dry_run,
        )
    )
    result = result.add(
        _safe_write(
            target / "public" / "contracts" / "runtime_queries.py",
            _render_contract_module(list(contracts.get("queries", [])), "queries"),
            overwrite=overwrite,
            dry_run=dry_run,
        )
    )
    result = result.add(
        _safe_write(
            target / "public" / "contracts" / "events.py",
            _render_contract_module(list(contracts.get("events", [])), "events"),
            overwrite=overwrite,
            dry_run=dry_run,
        )
    )
    result = result.add(
        _safe_write(
            target / "public" / "contracts" / "results.py",
            _render_contract_module(list(contracts.get("results", [])), "results"),
            overwrite=overwrite,
            dry_run=dry_run,
        )
    )
    result = result.add(
        _safe_write(
            target / "public" / "contracts" / "errors.py",
            _render_contract_module(list(contracts.get("errors", [])), "errors"),
            overwrite=overwrite,
            dry_run=dry_run,
        )
    )
    result = result.add(
        _safe_write(
            target / "generated" / "context.pack.json", _render_context_pack(cell), overwrite=overwrite, dry_run=dry_run
        )
    )
    return result


def _validate_final_spec_boundaries(cells: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    index: dict[str, dict[str, Any]] = {}
    for cell in cells:
        cell_id = str(cell.get("id", "")).strip()
        if cell_id:
            index[cell_id] = cell

    for required_cell in FINAL_SPEC_REQUIRED_CELLS:
        if required_cell not in index:
            violations.append(f"missing required cell: {required_cell}")

    owner_map: dict[str, list[str]] = {}
    for cell in cells:
        cell_id = str(cell.get("id", ""))
        for state in cell.get("state_owners", []) or []:
            owner_map.setdefault(str(state), []).append(cell_id)
    for state_key, owners in sorted(owner_map.items()):
        unique = sorted(set(owners))
        if len(unique) > 1 and state_key not in FINAL_SPEC_ALLOW_MULTI_STATE_OWNER:
            violations.append(f"state owner conflict on '{state_key}': {unique}")

    for cell_id, expected_effects in FINAL_SPEC_EFFECT_MINIMUMS.items():
        if cell_id not in index:
            continue
        actual = {str(item) for item in index[cell_id].get("effects_allowed", [])}
        missing = [effect for effect in expected_effects if effect not in actual]
        if missing:
            violations.append(f"{cell_id} missing required effects: {missing}")

    delivery = index.get("delivery.api_gateway")
    if delivery:
        for effect in delivery.get("effects_allowed", []) or []:
            token = str(effect)
            if token.startswith("fs.write") or token.startswith("db.write"):
                violations.append(f"delivery.api_gateway forbidden effect declared: {token}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Cell asset folders/files from docs/graph/catalog/cells.yaml."
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root path.")
    parser.add_argument("--catalog", type=Path, default=None, help="Cell catalog YAML path.")
    parser.add_argument("--cells-root", type=Path, default=None, help="Output root for generated cell assets.")
    parser.add_argument("--public-only", action="store_true", help="Generate assets only for public cells.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files when content changed.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned writes without modifying files.")
    parser.add_argument(
        "--enforce-final-spec", action="store_true", help="Validate FINAL_SPEC hard boundaries before generating."
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve() if args.repo_root else _resolve_repo_root(Path(__file__).resolve())
    catalog_path = args.catalog.resolve() if args.catalog else _default_catalog_path(repo_root)
    cells_root = args.cells_root.resolve() if args.cells_root else _default_cells_root(repo_root)

    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog file not found: {catalog_path}")

    catalog = _load_yaml(catalog_path)
    cells = list(catalog.get("cells", []))
    if args.enforce_final_spec:
        violations = _validate_final_spec_boundaries(cells)
        if violations:
            payload = {
                "status": "failed",
                "violations": violations,
                "catalog": str(catalog_path),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2

    if args.public_only:
        cells = [cell for cell in cells if str(cell.get("visibility", "")).strip().lower() == "public"]

    aggregate = WriteResult()
    for cell in cells:
        aggregate = aggregate.add(_generate_for_cell(cell, cells_root, overwrite=args.overwrite, dry_run=args.dry_run))

    summary = {
        "catalog": str(catalog_path),
        "cells_root": str(cells_root),
        "cells_processed": len(cells),
        "created": aggregate.created,
        "updated": aggregate.updated,
        "skipped": aggregate.skipped,
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
