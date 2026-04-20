from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

FINAL_SPEC_REQUIRED_CELLS = (
    "delivery.api_gateway",
    "runtime.state_owner",
    "runtime.projection",
    "archive.run_archive",
    "archive.task_snapshot_archive",
    "archive.factory_archive",
    "audit.evidence",
    "policy.workspace_guard",
)

FINAL_SPEC_EFFECT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
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

ALLOW_MULTI_OWNER_STATE = {
    "runtime/events/*",
    "*.index.jsonl",
    "workspace/history/index/*.index.jsonl",
}


@dataclass(frozen=True)
class Replacement:
    source: str
    target: str


def _resolve_repo_root(script_path: Path) -> Path:
    for candidate in [script_path.parent, *script_path.parents]:
        if (candidate / ".git").exists():
            return candidate
    return script_path.parents[3]


def _default_catalog(repo_root: Path) -> Path:
    return repo_root / "src" / "backend" / "docs" / "graph" / "catalog" / "cells.yaml"


def _default_subgraphs_dir(repo_root: Path) -> Path:
    return repo_root / "src" / "backend" / "docs" / "graph" / "subgraphs"


def _parse_replacement(token: str) -> Replacement:
    if "=" not in token:
        raise ValueError(f"Invalid replacement '{token}', expected from=to")
    source, target = token.split("=", 1)
    source = source.strip()
    target = target.strip()
    if not source:
        raise ValueError("Replacement source cannot be empty")
    return Replacement(source=source, target=target)


def _load_replacements(tokens: list[str], reverse: bool) -> list[Replacement]:
    rules = [_parse_replacement(token) for token in tokens]
    if not rules:
        rules = [Replacement("", "polaris/")]
    if reverse:
        rules = [Replacement(source=rule.target, target=rule.source) for rule in rules]
    rules.sort(key=lambda item: len(item.source), reverse=True)
    return rules


def _rewrite_token(value: str, replacements: list[Replacement]) -> tuple[str, int]:
    updated = value
    changed = 0
    for rule in replacements:
        count = updated.count(rule.source)
        if count:
            updated = updated.replace(rule.source, rule.target)
            changed += count
    return updated, changed


def _rewrite_string_list(values: list[str], replacements: list[Replacement]) -> tuple[list[str], int]:
    output: list[str] = []
    total = 0
    for value in values:
        rewritten, changed = _rewrite_token(str(value), replacements)
        output.append(rewritten)
        total += changed
    return output, total


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be object: {path}")
    return payload


def _dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8")


def _rewrite_catalog(payload: dict[str, Any], replacements: list[Replacement]) -> tuple[dict[str, Any], int]:
    changed = 0
    cells = payload.get("cells", [])
    if not isinstance(cells, list):
        raise ValueError("catalog.cells must be list")
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        for key in ("current_modules", "owned_paths"):
            values = cell.get(key, [])
            if isinstance(values, list):
                updated, count = _rewrite_string_list(values, replacements)
                cell[key] = updated
                changed += count
        contracts = cell.get("public_contracts", {})
        if isinstance(contracts, dict):
            modules = contracts.get("modules", [])
            if isinstance(modules, list):
                updated, count = _rewrite_string_list(modules, replacements)
                contracts["modules"] = updated
                changed += count
        verification = cell.get("verification", {})
        if isinstance(verification, dict):
            tests = verification.get("tests", [])
            if isinstance(tests, list):
                updated, count = _rewrite_string_list(tests, replacements)
                verification["tests"] = updated
                changed += count
            smoke = verification.get("smoke_commands", [])
            if isinstance(smoke, list):
                updated, count = _rewrite_string_list(smoke, replacements)
                verification["smoke_commands"] = updated
                changed += count
    return payload, changed


def _rewrite_subgraph(payload: dict[str, Any], replacements: list[Replacement]) -> tuple[dict[str, Any], int]:
    changed = 0
    verify = payload.get("verify_targets", {})
    if isinstance(verify, dict):
        tests = verify.get("tests", [])
        if isinstance(tests, list):
            updated, count = _rewrite_string_list(tests, replacements)
            verify["tests"] = updated
            changed += count
    return payload, changed


def _collect_missing_paths(repo_root: Path, catalog: dict[str, Any], subgraphs: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for cell in catalog.get("cells", []):
        if not isinstance(cell, dict):
            continue
        for path_token in cell.get("owned_paths", []):
            token = str(path_token).strip()
            if "*" in token or "?" in token:
                continue
            if token.startswith("http://") or token.startswith("https://"):
                continue
            candidate = repo_root / token
            if not candidate.exists():
                missing.append(token)
    for subgraph in subgraphs:
        verify = subgraph.get("verify_targets", {})
        if not isinstance(verify, dict):
            continue
        for path_token in verify.get("tests", []):
            token = str(path_token).strip()
            if not token:
                continue
            candidate = repo_root / token
            if not candidate.exists():
                missing.append(token)
    dedup: dict[str, None] = {}
    for item in missing:
        dedup[item] = None
    return list(dedup.keys())


def _validate_state_owners(catalog: dict[str, Any]) -> list[str]:
    owner_map: dict[str, list[str]] = {}
    for cell in catalog.get("cells", []):
        if not isinstance(cell, dict):
            continue
        cell_id = str(cell.get("id", ""))
        for state_token in cell.get("state_owners", []) or []:
            owner_map.setdefault(str(state_token), []).append(cell_id)
    violations: list[str] = []
    for state_token, owners in sorted(owner_map.items()):
        unique = sorted(set(owners))
        if len(unique) > 1 and state_token not in ALLOW_MULTI_OWNER_STATE:
            violations.append(f"state owner conflict: {state_token} -> {unique}")
    return violations


def _validate_effects(catalog: dict[str, Any]) -> list[str]:
    cells_index: dict[str, dict[str, Any]] = {}
    for cell in catalog.get("cells", []):
        if isinstance(cell, dict):
            cell_id = str(cell.get("id", "")).strip()
            if cell_id:
                cells_index[cell_id] = cell

    violations: list[str] = []
    for required_cell in FINAL_SPEC_REQUIRED_CELLS:
        if required_cell not in cells_index:
            violations.append(f"missing required cell: {required_cell}")

    for cell_id, expected in FINAL_SPEC_EFFECT_REQUIREMENTS.items():
        if cell_id not in cells_index:
            continue
        actual = {str(item) for item in cells_index[cell_id].get("effects_allowed", [])}
        missing = [rule for rule in expected if rule not in actual]
        if missing:
            violations.append(f"{cell_id} missing effect declarations: {missing}")

    delivery = cells_index.get("delivery.api_gateway")
    if delivery:
        for effect in delivery.get("effects_allowed", []) or []:
            token = str(effect)
            if token.startswith("fs.write") or token.startswith("db.write"):
                violations.append(f"delivery.api_gateway forbidden effect: {token}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Rewrite graph catalog/subgraph path fields using replacement rules.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root path.")
    parser.add_argument("--catalog", type=Path, default=None, help="Path to cells catalog yaml.")
    parser.add_argument("--subgraphs-dir", type=Path, default=None, help="Path to subgraphs directory.")
    parser.add_argument("--replacement", action="append", default=[], help="Replacement rule from=to.")
    parser.add_argument("--reverse", action="store_true", help="Reverse replacement rules (to=from).")
    parser.add_argument("--execute", action="store_true", help="Apply file writes. Default is dry-run.")
    parser.add_argument("--check-missing", action="store_true", help="Check whether rewritten referenced paths exist.")
    parser.add_argument("--strict-missing", action="store_true", help="Return non-zero when missing paths detected.")
    parser.add_argument(
        "--enforce-boundaries", action="store_true", help="Validate FINAL_SPEC state/effect hard boundaries."
    )
    parser.add_argument(
        "--strict-boundaries", action="store_true", help="Return non-zero when boundary violations are detected."
    )
    parser.add_argument("--report", type=Path, default=None, help="Optional report JSON output path.")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve() if args.repo_root else _resolve_repo_root(Path(__file__).resolve())
    catalog_path = args.catalog.resolve() if args.catalog else _default_catalog(repo_root)
    subgraphs_dir = args.subgraphs_dir.resolve() if args.subgraphs_dir else _default_subgraphs_dir(repo_root)
    dry_run = not args.execute
    replacements = _load_replacements(tokens=list(args.replacement), reverse=bool(args.reverse))

    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog file not found: {catalog_path}")
    if not subgraphs_dir.exists():
        raise FileNotFoundError(f"Subgraphs directory not found: {subgraphs_dir}")

    catalog_payload = _load_yaml(catalog_path)
    catalog_payload, catalog_changes = _rewrite_catalog(catalog_payload, replacements)

    subgraph_changes = 0
    subgraph_payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(subgraphs_dir.glob("*.yaml")):
        payload = _load_yaml(path)
        payload, changed = _rewrite_subgraph(payload, replacements)
        subgraph_changes += changed
        subgraph_payloads.append((path, payload))

    if not dry_run:
        _dump_yaml(catalog_path, catalog_payload)
        for path, payload in subgraph_payloads:
            _dump_yaml(path, payload)

    missing_paths: list[str] = []
    if args.check_missing or args.strict_missing:
        missing_paths = _collect_missing_paths(
            repo_root=repo_root,
            catalog=catalog_payload,
            subgraphs=[payload for _, payload in subgraph_payloads],
        )

    boundary_violations: list[str] = []
    if args.enforce_boundaries or args.strict_boundaries:
        boundary_violations.extend(_validate_state_owners(catalog_payload))
        boundary_violations.extend(_validate_effects(catalog_payload))

    result = {
        "repo_root": str(repo_root),
        "catalog": str(catalog_path),
        "subgraphs_dir": str(subgraphs_dir),
        "dry_run": dry_run,
        "rules": [{"from": item.source, "to": item.target} for item in replacements],
        "catalog_replacements": catalog_changes,
        "subgraph_replacements": subgraph_changes,
        "missing_paths_count": len(missing_paths),
        "missing_paths": missing_paths,
        "boundary_violations_count": len(boundary_violations),
        "boundary_violations": boundary_violations,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.report:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.strict_missing and missing_paths:
        return 2
    if args.strict_boundaries and boundary_violations:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
