#!/usr/bin/env python3
"""
Cell Schema Normalizer
Fixes common schema inconsistencies in cell.yaml files.
Run with --dry-run first to see what would change.
"""

import argparse
from pathlib import Path
from typing import Any

import yaml

CELLS_DIR = Path(__file__).parent
BACKUP_DIR = CELLS_DIR / ".schema_backups"


def load_cell(cell_path: Path) -> dict[str, Any]:
    """Load a cell.yaml file."""
    with open(cell_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_cell(cell_path: Path, data: dict[str, Any]) -> None:
    """Save a cell.yaml file with proper formatting."""
    with open(cell_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def normalize_public_contracts(data: dict[str, Any]) -> list[str]:
    """Normalize public_contracts and return list of changes."""
    changes = []
    pc = data.get("public_contracts", {})

    # Ensure modules exists
    if "modules" not in pc:
        pc["modules"] = []
        changes.append("Added missing 'public_contracts.modules'")

    # Ensure all sub-fields exist
    for field in ["commands", "queries", "events", "results", "errors"]:
        if field not in pc:
            pc[field] = []
            changes.append(f"Added missing 'public_contracts.{field}'")

    data["public_contracts"] = pc
    return changes


def normalize_verification(data: dict[str, Any]) -> list[str]:
    """Normalize verification section and return list of changes."""
    changes = []
    v = data.get("verification", {})

    # Ensure verification exists
    if not v:
        data["verification"] = {}
        v = data["verification"]

    # Ensure smoke_commands exists (P0 fix)
    if "smoke_commands" not in v:
        v["smoke_commands"] = []
        changes.append("Added missing 'verification.smoke_commands'")

    # Ensure tests exists
    if "tests" not in v:
        v["tests"] = []
        changes.append("Added missing 'verification.tests'")

    # Ensure gaps exists
    if "gaps" not in v:
        v["gaps"] = []
        changes.append("Added missing 'verification.gaps'")

    data["verification"] = v
    return changes


def normalize_top_level_fields(data: dict[str, Any]) -> list[str]:
    """Normalize top-level fields and return list of changes."""
    changes = []

    # Ensure subgraphs exists
    if "subgraphs" not in data:
        data["subgraphs"] = []
        changes.append("Added missing 'subgraphs'")

    # Ensure state_owners exists
    if "state_owners" not in data:
        data["state_owners"] = []
        changes.append("Added missing 'state_owners'")

    # Ensure effects_allowed exists
    if "effects_allowed" not in data:
        data["effects_allowed"] = []
        changes.append("Added missing 'effects_allowed'")

    # Add current_modules if public_contracts.modules exists but current_modules doesn't
    has_contract_modules = (
        "public_contracts" in data
        and "modules" in data["public_contracts"]
        and len(data["public_contracts"]["modules"]) > 0
    )
    has_current_modules = "current_modules" in data

    if has_contract_modules and not has_current_modules:
        data["current_modules"] = data["public_contracts"]["modules"][:1]  # Copy first module as placeholder
        changes.append("Added 'current_modules' based on public_contracts.modules (verify correctness)")

    # Add generated_artifacts if missing (P1 fix)
    if "generated_artifacts" not in data:
        data["generated_artifacts"] = []
        changes.append("Added missing 'generated_artifacts'")

    # Add tags if missing (P2 fix)
    if "tags" not in data:
        data["tags"] = []
        changes.append("Added missing 'tags'")

    return changes


def normalize_cell(cell_path: Path, dry_run: bool = True) -> list[str]:
    """Normalize a single cell and return list of changes made."""
    data = load_cell(cell_path)
    all_changes = []

    # Normalize in order
    all_changes.extend(normalize_top_level_fields(data))
    all_changes.extend(normalize_public_contracts(data))
    all_changes.extend(normalize_verification(data))

    # Sort keys in a consistent order
    ordered_keys = [
        "id",
        "title",
        "kind",
        "visibility",
        "stateful",
        "owner",
        "purpose",
        "current_modules",
        "tags",
        "owned_paths",
        "public_contracts",
        "depends_on",
        "subgraphs",
        "state_owners",
        "effects_allowed",
        "verification",
        "generated_artifacts",
    ]

    ordered_data = {}
    for key in ordered_keys:
        if key in data:
            ordered_data[key] = data[key]
    # Add any keys not in our list
    for key in data:
        if key not in ordered_data:
            ordered_data[key] = data[key]

    if not dry_run:
        # Create backup
        BACKUP_DIR.mkdir(exist_ok=True)
        backup_path = BACKUP_DIR / f"{cell_path.name}.bak"
        with open(cell_path, encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())

        # Save normalized version
        save_cell(cell_path, ordered_data)

    return all_changes


def main():
    parser = argparse.ArgumentParser(description="Normalize cell.yaml schema")
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="Show changes without applying them (default: True)"
    )
    parser.add_argument("--apply", action="store_true", help="Actually apply changes (default: False)")
    parser.add_argument("--cell", type=str, help="Specific cell to normalize (e.g., 'context.catalog')")
    args = parser.parse_args()

    if args.apply:
        args.dry_run = False

    # Find cell.yaml files
    cell_files = list(CELLS_DIR.glob("**/cell.yaml"))
    cell_files = [f for f in cell_files if "fixtures" not in str(f) and f.name == "cell.yaml"]

    if args.cell:
        cell_files = [f for f in cell_files if args.cell in str(f)]

    print(f"Found {len(cell_files)} cell.yaml files")
    if args.dry_run:
        print("DRY RUN - no changes will be made")
    print()

    total_changes = 0
    for cell_path in sorted(cell_files):
        changes = normalize_cell(cell_path, dry_run=args.dry_run)
        if changes:
            cell_id = cell_path.parent.name
            print(f"\n{cell_id}:")
            for change in changes:
                print(f"  - {change}")
            total_changes += len(changes)

    print(f"\n{'=' * 60}")
    print(f"Total changes: {total_changes}")
    if args.dry_run:
        print("Run with --apply to apply these changes")
    else:
        print("Changes have been applied")
        print(f"Backups saved to: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
