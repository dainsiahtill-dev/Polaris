"""Repository governance gate for ACGA catalog and graph assets.

This script turns key governance rules from policy text into executable checks.
It supports three modes:
  - audit-only: never fails (report only)
  - fail-on-new: fails only on issues not present in baseline
  - hard-fail: fails on any blocker/high issue
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

_MODE_AUDIT_ONLY = "audit-only"
_MODE_FAIL_ON_NEW = "fail-on-new"
_MODE_HARD_FAIL = "hard-fail"
_SUPPORTED_MODES = (_MODE_AUDIT_ONLY, _MODE_FAIL_ON_NEW, _MODE_HARD_FAIL)

_SEVERITY_BLOCKER = "blocker"
_SEVERITY_HIGH = "high"
_SEVERITY_MEDIUM = "medium"


@dataclass(frozen=True)
class GovernanceIssue:
    """One governance violation."""

    rule_id: str
    severity: str
    message: str
    path: str = ""
    line: int = 0

    def fingerprint(self) -> str:
        key = f"{self.rule_id}|{self.severity}|{self.path}|{self.line}|{self.message}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class GovernanceReport:
    """Structured governance report."""

    workspace: str
    mode: str
    exit_code: int
    issue_count: int
    blocker_count: int
    high_count: int
    new_issue_count: int
    issues: tuple[GovernanceIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "mode": self.mode,
            "exit_code": self.exit_code,
            "issue_count": self.issue_count,
            "blocker_count": self.blocker_count,
            "high_count": self.high_count,
            "new_issue_count": self.new_issue_count,
            "issues": [item.to_dict() for item in self.issues],
            "issue_fingerprints": [item.fingerprint() for item in self.issues],
        }


_RULE_MANIFEST_CATALOG_CONSISTENCY = "manifest_catalog_consistency"


@dataclass(frozen=True)
class CatalogCell:
    """Normalized cell record for rule checks."""

    cell_id: str
    owned_paths: tuple[str, ...]
    depends_on: tuple[str, ...]
    state_owners: tuple[str, ...]
    effects_allowed: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ManifestRecord:
    """Normalized cell manifest record for reconciliation checks."""

    cell_id: str
    owned_paths: tuple[str, ...]
    depends_on: tuple[str, ...]
    state_owners: tuple[str, ...]
    effects_allowed: tuple[str, ...]
    has_current_modules: bool


@dataclass(frozen=True, kw_only=True)
class ManifestCatalogMismatch:
    """One manifest<->catalog field mismatch for a single cell."""

    cell_id: str
    field: str
    mismatch_type: (
        str  # "manifest_extra" | "catalog_missing_module" | "catalog_not_superset" | "owned_path_not_contained"
    )
    manifest_value: str
    catalog_value: str = ""

    def fingerprint(self) -> str:
        key = f"mc|{self.cell_id}|{self.field}|{self.mismatch_type}|{self.manifest_value}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "field": self.field,
            "mismatch_type": self.mismatch_type,
            "manifest_value": self.manifest_value,
            "catalog_value": self.catalog_value,
            "fingerprint": self.fingerprint(),
        }


def _normalize_rel(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _iter_rule_targets(repo_root: Path, pattern: str) -> list[Path]:
    matches = [item for item in repo_root.glob(pattern) if item.is_file()]
    return sorted(matches)


def _build_cell_index(catalog_payload: dict[str, Any]) -> list[CatalogCell]:
    cells_payload = catalog_payload.get("cells")
    if not isinstance(cells_payload, list):
        return []

    cells: list[CatalogCell] = []
    for item in cells_payload:
        if not isinstance(item, dict):
            continue
        cell_id = str(item.get("id") or "").strip()
        if not cell_id:
            continue
        owned_paths = tuple(_normalize_rel(entry) for entry in (item.get("owned_paths") or []) if str(entry).strip())
        depends_on = tuple(str(entry).strip() for entry in (item.get("depends_on") or []) if str(entry).strip())
        state_owners = tuple(str(entry).strip() for entry in (item.get("state_owners") or []) if str(entry).strip())
        effects_allowed = tuple(
            str(entry).strip() for entry in (item.get("effects_allowed") or []) if str(entry).strip()
        )
        cells.append(
            CatalogCell(
                cell_id=cell_id,
                owned_paths=owned_paths,
                depends_on=depends_on,
                state_owners=state_owners,
                effects_allowed=effects_allowed,
            )
        )
    return cells


def _expand_owned_files(repo_root: Path, cells: Iterable[CatalogCell]) -> dict[str, set[str]]:
    file_owners: dict[str, set[str]] = {}
    for cell in cells:
        for pattern in cell.owned_paths:
            matches = list(repo_root.glob(pattern))
            for match in matches:
                if match.is_file():
                    rel = _normalize_rel(match.relative_to(repo_root).as_posix())
                    file_owners.setdefault(rel, set()).add(cell.cell_id)
                    continue
                if match.is_dir():
                    for child in match.rglob("*"):
                        if not child.is_file():
                            continue
                        rel = _normalize_rel(child.relative_to(repo_root).as_posix())
                        file_owners.setdefault(rel, set()).add(cell.cell_id)
    return file_owners


def _build_owner_effects(cells: Iterable[CatalogCell]) -> dict[str, tuple[str, ...]]:
    return {cell.cell_id: cell.effects_allowed for cell in cells}


def _load_manifest(manifest_path: Path) -> ManifestRecord | None:
    """Load and normalize a single cell.yaml manifest, or None if unreadable."""
    if not manifest_path.is_file():
        return None
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    cell_id = str(payload.get("id") or "").strip()
    if not cell_id:
        return None
    raw_paths = payload.get("owned_paths")
    owned_paths = tuple(
        _normalize_rel(str(entry)) for entry in (raw_paths if isinstance(raw_paths, list) else []) if str(entry).strip()
    )
    raw_deps = payload.get("depends_on")
    depends_on = tuple(
        str(entry).strip() for entry in (raw_deps if isinstance(raw_deps, list) else []) if str(entry).strip()
    )
    raw_state = payload.get("state_owners")
    state_owners = tuple(
        str(entry).strip() for entry in (raw_state if isinstance(raw_state, list) else []) if str(entry).strip()
    )
    raw_effects = payload.get("effects_allowed")
    effects_allowed = tuple(
        str(entry).strip() for entry in (raw_effects if isinstance(raw_effects, list) else []) if str(entry).strip()
    )
    has_current_modules = isinstance(payload.get("current_modules"), list) and bool(payload.get("current_modules"))
    return ManifestRecord(
        cell_id=cell_id,
        owned_paths=owned_paths,
        depends_on=depends_on,
        state_owners=state_owners,
        effects_allowed=effects_allowed,
        has_current_modules=has_current_modules,
    )


def _manifest_owned_path_contained(
    manifest_path: str,
    catalog_paths: tuple[str, ...],
) -> bool:
    """Check whether a manifest owned path is covered by at least one catalog glob path.

    Uses prefix matching: if catalog_path ends with /**, matches any path starting
    with the prefix (minus the /**). Otherwise uses exact match.
    Identical glob patterns (both containing **) are treated as equivalent.
    """
    for cat_path in catalog_paths:
        if "**" in cat_path:
            # Identical glob patterns are equivalent
            if manifest_path == cat_path:
                return True
            prefix = cat_path.replace("**", "").rstrip("/")
            if manifest_path.startswith(prefix + "/") or manifest_path.startswith(prefix + "\\"):
                return True
        elif manifest_path == cat_path:
            return True
    return False


def _check_manifest_catalog_consistency(
    *,
    repo_root: Path,
    catalog_cells: Iterable[CatalogCell],
) -> list[ManifestCatalogMismatch]:
    """Reconcile each manifest cell.yaml against its catalog cells.yaml entry.

    Rules enforced:
      1. catalog depends_on ⊇ manifest depends_on  (catalog must be superset)
      2. catalog owned_paths must contain each manifest owned_path (via glob match)
      3. catalog state_owners ⊇ manifest state_owners
      4. catalog effects_allowed ⊇ manifest effects_allowed
      5. if manifest has current_modules, catalog must also have current_modules

    MC findings are returned as ManifestCatalogMismatch objects ONLY -- they are NOT
    added to the shared GovernanceIssue list. This keeps the governance issue baseline
    and mismatch baseline independent.

    Returns the list of mismatches found (not yet filtered by baseline).
    """
    mismatches: list[ManifestCatalogMismatch] = []
    cells_root = repo_root / "polaris" / "cells"
    catalog_index = {cell.cell_id: cell for cell in catalog_cells}

    # Iterate every cell.yaml on disk
    for manifest_path in sorted(cells_root.glob("**/cell.yaml")):
        if "__pycache__" in manifest_path.parts:
            continue
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            # A manifest that cannot be loaded is a drift finding tracked as a mismatch.
            # The path-derived cell_id is approximate since we couldn't parse the manifest.
            # Use the parent path as a stable identifier.
            rel = manifest_path.relative_to(cells_root).parent
            approx_cell_id = ".".join(rel.parts)
            mismatches.append(
                ManifestCatalogMismatch(
                    cell_id=approx_cell_id,
                    field="manifest_load_failure",
                    mismatch_type="manifest_unreadable",
                    manifest_value=str(manifest_path),
                    catalog_value="(manifest could not be parsed)",
                )
            )
            continue

        catalog_cell = catalog_index.get(manifest.cell_id)
        if catalog_cell is None:
            # Cell is in manifest but not in catalog -- handled by separate test
            continue

        # Rule 1: depends_on superset
        catalog_deps = set(catalog_cell.depends_on)
        for dep in manifest.depends_on:
            if dep not in catalog_deps:
                mismatches.append(
                    ManifestCatalogMismatch(
                        cell_id=manifest.cell_id,
                        field="depends_on",
                        mismatch_type="catalog_not_superset",
                        manifest_value=dep,
                        catalog_value=", ".join(sorted(catalog_deps)),
                    )
                )

        # Rule 2: owned_paths containment
        for mpath in manifest.owned_paths:
            if not _manifest_owned_path_contained(mpath, catalog_cell.owned_paths):
                mismatches.append(
                    ManifestCatalogMismatch(
                        cell_id=manifest.cell_id,
                        field="owned_paths",
                        mismatch_type="owned_path_not_contained",
                        manifest_value=mpath,
                        catalog_value=", ".join(sorted(catalog_cell.owned_paths)),
                    )
                )

        # Rule 3: state_owners superset
        catalog_state = set(catalog_cell.state_owners)
        for so in manifest.state_owners:
            if so not in catalog_state:
                mismatches.append(
                    ManifestCatalogMismatch(
                        cell_id=manifest.cell_id,
                        field="state_owners",
                        mismatch_type="catalog_not_superset",
                        manifest_value=so,
                        catalog_value=", ".join(sorted(catalog_state)),
                    )
                )

        # Rule 4: effects_allowed superset
        catalog_effects = set(catalog_cell.effects_allowed)
        for eff in manifest.effects_allowed:
            if eff not in catalog_effects:
                mismatches.append(
                    ManifestCatalogMismatch(
                        cell_id=manifest.cell_id,
                        field="effects_allowed",
                        mismatch_type="catalog_not_superset",
                        manifest_value=eff,
                        catalog_value=", ".join(sorted(catalog_effects)),
                    )
                )

        # Rule 5: current_modules presence -- if manifest has current_modules but catalog
        # has no owned_paths at all, it is a clear drift signal.
        # (Module paths are also covered by owned_paths containment in Rule 2.)
        if manifest.has_current_modules and not catalog_cell.owned_paths:
            mismatches.append(
                ManifestCatalogMismatch(
                    cell_id=manifest.cell_id,
                    field="current_modules",
                    mismatch_type="catalog_missing_module",
                    manifest_value="(manifest declares current_modules)",
                    catalog_value="(catalog has no owned_paths)",
                )
            )

    return mismatches


def _load_baseline_fingerprints(baseline_path: Path | None) -> set[str]:
    """Load frozen mismatch fingerprints from a JSON Lines baseline file.

    Returns an empty set if the file does not exist or cannot be opened.
    Malformed lines are skipped individually; valid lines are always processed.
    """
    if baseline_path is None or not baseline_path.is_file():
        return set()
    fingerprints: set[str] = set()
    try:
        text = baseline_path.read_text(encoding="utf-8")
    except Exception:
        return set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            fp = record.get("fingerprint") if isinstance(record, dict) else None
            if fp and isinstance(fp, str):
                fingerprints.add(fp)
        except Exception:
            # Skip malformed lines individually; continue processing.
            pass
    return fingerprints


def _count_new_mismatches(
    mismatches: list[ManifestCatalogMismatch],
    baseline_path: Path | None,
) -> int:
    """Count mismatches not present in the JSON Lines baseline."""
    frozen = _load_baseline_fingerprints(baseline_path)
    if not frozen:
        return len(mismatches)
    return sum(1 for mm in mismatches if mm.fingerprint() not in frozen)


def _write_mismatch_baseline(baseline_path: Path, mismatches: list[ManifestCatalogMismatch]) -> None:
    """Write current mismatches to a JSON Lines baseline file."""
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(mm.to_dict(), ensure_ascii=False) for mm in mismatches]
    baseline_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_schema_targets(
    *,
    repo_root: Path,
    schema_path: Path,
    target_patterns: tuple[str, ...],
    rule_id: str,
    issues: list[GovernanceIssue],
) -> None:
    if not schema_path.is_file():
        issues.append(
            GovernanceIssue(
                rule_id=rule_id,
                severity=_SEVERITY_BLOCKER,
                message=f"Schema file missing: {schema_path.relative_to(repo_root).as_posix()}",
                path=str(schema_path),
            )
        )
        return

    registry = Registry()
    schema_dir = schema_path.parent
    for candidate in sorted(schema_dir.glob("*.yaml")):
        try:
            schema_payload = _read_yaml(candidate)
        except Exception:
            continue
        resource = DRAFT202012.create_resource(schema_payload)
        registry = registry.with_resource(candidate.resolve().as_uri(), resource)
        registry = registry.with_resource(candidate.name, resource)
        registry = registry.with_resource(f"./{candidate.name}", resource)

    schema_payload = _read_yaml(schema_path)
    validator = Draft202012Validator(schema_payload, registry=registry)

    for pattern in target_patterns:
        targets = _iter_rule_targets(repo_root, pattern)
        if not targets:
            issues.append(
                GovernanceIssue(
                    rule_id=rule_id,
                    severity=_SEVERITY_BLOCKER,
                    message=f"No files matched required target pattern: {pattern}",
                )
            )
            continue
        for target in targets:
            try:
                payload = _read_yaml(target)
            except Exception as exc:
                issues.append(
                    GovernanceIssue(
                        rule_id=rule_id,
                        severity=_SEVERITY_BLOCKER,
                        message=f"Failed to parse YAML: {exc}",
                        path=str(target),
                    )
                )
                continue
            for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path)):
                path_token = ".".join(str(item) for item in error.path)
                message = f"{error.message}"
                if path_token:
                    message = f"{message} (path: {path_token})"
                issues.append(
                    GovernanceIssue(
                        rule_id=rule_id,
                        severity=_SEVERITY_BLOCKER,
                        message=message,
                        path=str(target),
                    )
                )


def _check_owned_path_overlaps(
    *,
    file_owners: dict[str, set[str]],
    issues: list[GovernanceIssue],
) -> None:
    for rel_path in sorted(file_owners):
        owners = sorted(file_owners[rel_path])
        if len(owners) <= 1:
            continue
        issues.append(
            GovernanceIssue(
                rule_id="owned_paths_do_not_overlap",
                severity=_SEVERITY_BLOCKER,
                message=f"Path has multiple owners: {owners}",
                path=rel_path,
            )
        )


def _check_single_state_owner(
    *,
    cells: Iterable[CatalogCell],
    issues: list[GovernanceIssue],
) -> None:
    state_index: dict[str, list[str]] = {}
    for cell in cells:
        for state_path in cell.state_owners:
            state_index.setdefault(state_path, []).append(cell.cell_id)
    for state_path, owners in sorted(state_index.items()):
        if len(owners) <= 1:
            continue
        issues.append(
            GovernanceIssue(
                rule_id="single_state_owner",
                severity=_SEVERITY_BLOCKER,
                message=f"State owner conflict: {sorted(owners)}",
                path=state_path,
            )
        )


def _owner_for_path(file_owners: dict[str, set[str]], rel_path: str) -> str | None:
    owners = file_owners.get(_normalize_rel(rel_path), set())
    if len(owners) != 1:
        return None
    return next(iter(owners))


def _path_to_cell_id(*, rel_path: str, known_cells: set[str]) -> str | None:
    normalized = _normalize_rel(rel_path)
    parts = normalized.split("/")
    if len(parts) < 4:
        return None
    if parts[0] != "polaris" or parts[1] != "cells":
        return None
    candidate = f"{parts[2]}.{parts[3]}"
    return candidate if candidate in known_cells else None


def _module_to_cell_id(module: str, *, known_cells: set[str]) -> str | None:
    parts = str(module or "").strip().split(".")
    if len(parts) < 4:
        return None
    if parts[0] != "polaris" or parts[1] != "cells":
        return None
    candidate = f"{parts[2]}.{parts[3]}"
    return candidate if candidate in known_cells else None


def _check_cross_cell_internal_imports(
    *,
    repo_root: Path,
    file_owners: dict[str, set[str]],
    issues: list[GovernanceIssue],
) -> None:
    for source_file in sorted((repo_root / "polaris").rglob("*.py")):
        if "__pycache__" in source_file.parts:
            continue
        rel_path = source_file.relative_to(repo_root).as_posix()
        source_owner = _owner_for_path(file_owners, rel_path)
        if not source_owner:
            continue

        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        except Exception as exc:
            issues.append(
                GovernanceIssue(
                    rule_id="no_cross_cell_internal_import",
                    severity=_SEVERITY_HIGH,
                    message=f"Failed to parse python source: {exc}",
                    path=rel_path,
                )
            )
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = str(node.module or "").strip()
            if not module.startswith("polaris.cells.") or ".internal." not in module:
                continue
            module_parts = module.split(".")
            if len(module_parts) < 4:
                continue
            target_cell_id = f"{module_parts[2]}.{module_parts[3]}"
            if target_cell_id == source_owner:
                continue
            issues.append(
                GovernanceIssue(
                    rule_id="no_cross_cell_internal_import",
                    severity=_SEVERITY_BLOCKER,
                    message=f"{source_owner} imports {target_cell_id} internal module",
                    path=rel_path,
                    line=int(getattr(node, "lineno", 0) or 0),
                )
            )


def _check_declared_cell_dependencies(
    *,
    repo_root: Path,
    cells: Iterable[CatalogCell],
    issues: list[GovernanceIssue],
) -> None:
    catalog_cells = tuple(cells)
    known_cells = {cell.cell_id for cell in catalog_cells}
    declared_depends_on = {cell.cell_id: set(cell.depends_on) for cell in catalog_cells}
    discovered_edges: dict[tuple[str, str], str] = {}

    for source_file in sorted((repo_root / "polaris" / "cells").rglob("*.py")):
        if "__pycache__" in source_file.parts:
            continue
        rel_path = source_file.relative_to(repo_root).as_posix()
        source_cell_id = _path_to_cell_id(rel_path=rel_path, known_cells=known_cells)
        if not source_cell_id:
            continue

        try:
            tree = ast.parse(
                source_file.read_text(encoding="utf-8"),
                filename=str(source_file),
            )
        except Exception as exc:
            issues.append(
                GovernanceIssue(
                    rule_id="declared_cell_dependencies_match_imports",
                    severity=_SEVERITY_HIGH,
                    message=f"Failed to parse python source: {exc}",
                    path=rel_path,
                )
            )
            continue

        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_modules.add(node.module)

        for module_name in sorted(imported_modules):
            target_cell_id = _module_to_cell_id(module_name, known_cells=known_cells)
            if not target_cell_id or target_cell_id == source_cell_id:
                continue
            edge = (source_cell_id, target_cell_id)
            discovered_edges.setdefault(edge, rel_path)

    for (source_cell_id, target_cell_id), rel_path in sorted(discovered_edges.items()):
        if target_cell_id in declared_depends_on.get(source_cell_id, set()):
            continue
        issues.append(
            GovernanceIssue(
                rule_id="declared_cell_dependencies_match_imports",
                severity=_SEVERITY_HIGH,
                message=(f"{source_cell_id} imports {target_cell_id} but does not declare it in depends_on"),
                path=rel_path,
            )
        )


def _check_critical_subgraphs(
    *,
    repo_root: Path,
    issues: list[GovernanceIssue],
) -> None:
    required = (
        "director_pipeline.yaml",
        "pm_pipeline.yaml",
        "context_plane.yaml",
    )
    for filename in required:
        path = repo_root / "docs" / "graph" / "subgraphs" / filename
        if not path.is_file():
            issues.append(
                GovernanceIssue(
                    rule_id="critical_subgraph_has_verify_targets",
                    severity=_SEVERITY_BLOCKER,
                    message=f"Missing critical subgraph file: {filename}",
                    path=str(path),
                )
            )
            continue
        payload = _read_yaml(path)
        verify_targets = payload.get("verify_targets") if isinstance(payload, dict) else None
        if not isinstance(verify_targets, dict):
            issues.append(
                GovernanceIssue(
                    rule_id="critical_subgraph_has_verify_targets",
                    severity=_SEVERITY_BLOCKER,
                    message="verify_targets must be an object",
                    path=str(path),
                )
            )
            continue
        tests = verify_targets.get("tests")
        if not isinstance(tests, list) or not tests:
            issues.append(
                GovernanceIssue(
                    rule_id="critical_subgraph_has_verify_targets",
                    severity=_SEVERITY_BLOCKER,
                    message="verify_targets.tests must be non-empty",
                    path=str(path),
                )
            )
            continue
        for entry in tests:
            rel = str(entry or "").strip()
            if not rel:
                continue
            test_path = repo_root / rel
            if not test_path.is_file():
                issues.append(
                    GovernanceIssue(
                        rule_id="critical_subgraph_has_verify_targets",
                        severity=_SEVERITY_BLOCKER,
                        message=f"verify target does not exist: {rel}",
                        path=str(path),
                    )
                )


def _effect_token_exists(effects: tuple[str, ...], prefix: str) -> bool:
    return any(str(item).startswith(prefix) for item in effects)


def _check_undeclared_effects(
    *,
    repo_root: Path,
    file_owners: dict[str, set[str]],
    owner_effects: dict[str, tuple[str, ...]],
    issues: list[GovernanceIssue],
) -> None:
    for source_file in sorted((repo_root / "polaris").rglob("*.py")):
        if "__pycache__" in source_file.parts:
            continue
        rel_path = source_file.relative_to(repo_root).as_posix()
        source_owner = _owner_for_path(file_owners, rel_path)
        if not source_owner:
            continue
        effects = owner_effects.get(source_owner, ())
        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        except Exception:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if isinstance(node.func, ast.Attribute):
                target = f"{getattr(node.func.value, 'id', '')}.{node.func.attr}"
                if target in {"subprocess.run", "subprocess.Popen", "subprocess.call"}:
                    if not _effect_token_exists(effects, "process.spawn:"):
                        issues.append(
                            GovernanceIssue(
                                rule_id="undeclared_effects_forbidden",
                                severity=_SEVERITY_HIGH,
                                message=f"process spawn call without declared effect ({source_owner})",
                                path=rel_path,
                                line=int(getattr(node, "lineno", 0) or 0),
                            )
                        )
                    continue
                if target in {
                    "requests.get",
                    "requests.post",
                    "requests.put",
                    "requests.delete",
                    "httpx.get",
                    "httpx.post",
                }:
                    if not _effect_token_exists(effects, "network.") and not _effect_token_exists(effects, "http."):
                        issues.append(
                            GovernanceIssue(
                                rule_id="undeclared_effects_forbidden",
                                severity=_SEVERITY_HIGH,
                                message=f"network call without declared effect ({source_owner})",
                                path=rel_path,
                                line=int(getattr(node, "lineno", 0) or 0),
                            )
                        )
                    continue

            if isinstance(node.func, ast.Name) and node.func.id == "open":
                mode = "r"
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value or "r")
                for keyword in node.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        mode = str(keyword.value.value or "r")
                if any(token in mode for token in ("w", "a", "x")) and "b" not in mode:
                    if not _effect_token_exists(effects, "fs.write:"):
                        issues.append(
                            GovernanceIssue(
                                rule_id="undeclared_effects_forbidden",
                                severity=_SEVERITY_HIGH,
                                message=f"text write open() without declared fs.write effect ({source_owner})",
                                path=rel_path,
                                line=int(getattr(node, "lineno", 0) or 0),
                            )
                        )


def _count_new_issues(
    issues: tuple[GovernanceIssue, ...],
    *,
    mode: str,
    baseline_path: Path | None,
) -> int:
    if mode != _MODE_FAIL_ON_NEW:
        return 0
    if baseline_path is None or not baseline_path.is_file():
        return len(issues)
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception:
        return len(issues)
    baseline = payload.get("issue_fingerprints")
    if not isinstance(baseline, list):
        return len(issues)
    baseline_set = {str(item).strip() for item in baseline if str(item).strip()}
    current_set = {item.fingerprint() for item in issues}
    return len(current_set - baseline_set)


def _resolve_exit_code(
    *,
    mode: str,
    blocker_count: int,
    high_count: int,
    new_issue_count: int,
) -> int:
    if mode == _MODE_AUDIT_ONLY:
        return 0
    if mode == _MODE_FAIL_ON_NEW:
        return 1 if new_issue_count > 0 else 0
    return 1 if blocker_count > 0 or high_count > 0 else 0


def _write_baseline(path: Path, report: GovernanceReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "workspace": report.workspace,
        "issue_fingerprints": [item.fingerprint() for item in report.issues],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_governance_gate(
    *,
    workspace: str,
    mode: str,
    baseline_path: Path | None,
    mismatch_baseline_path: Path | None,
) -> tuple[GovernanceReport, dict[str, Any]]:
    repo_root = Path(workspace).resolve()
    catalog_path = repo_root / "docs" / "graph" / "catalog" / "cells.yaml"
    if not catalog_path.is_file():
        issue = GovernanceIssue(
            rule_id="manifest_schema_valid",
            severity=_SEVERITY_BLOCKER,
            message="Missing catalog file docs/graph/catalog/cells.yaml",
            path=str(catalog_path),
        )
        issues = (issue,)
        new_issue_count = _count_new_issues(issues, mode=mode, baseline_path=baseline_path)
        exit_code = _resolve_exit_code(
            mode=mode,
            blocker_count=1,
            high_count=0,
            new_issue_count=new_issue_count,
        )
        return GovernanceReport(
            workspace=str(repo_root),
            mode=mode,
            exit_code=exit_code,
            issue_count=1,
            blocker_count=1,
            high_count=0,
            new_issue_count=new_issue_count,
            issues=issues,
        ), {"mismatch_count": 0, "new_mismatch_count": 0, "mismatches": [], "mc_blocker_count": 0}

    issues: list[GovernanceIssue] = []
    catalog_payload = _read_yaml(catalog_path)
    if not isinstance(catalog_payload, dict):
        issues.append(
            GovernanceIssue(
                rule_id="manifest_schema_valid",
                severity=_SEVERITY_BLOCKER,
                message="Catalog payload must be a YAML object",
                path=str(catalog_path),
            )
        )
        cells = []
        file_owners = {}
        owner_effects = {}
    else:
        cells = _build_cell_index(catalog_payload)
        file_owners = _expand_owned_files(repo_root, cells)
        owner_effects = _build_owner_effects(cells)

    _validate_schema_targets(
        repo_root=repo_root,
        schema_path=repo_root / "docs" / "governance" / "schemas" / "cell-catalog.schema.yaml",
        target_patterns=("docs/graph/catalog/cells.yaml",),
        rule_id="manifest_schema_valid",
        issues=issues,
    )
    _validate_schema_targets(
        repo_root=repo_root,
        schema_path=repo_root / "docs" / "governance" / "schemas" / "subgraph.schema.yaml",
        target_patterns=("docs/graph/subgraphs/*.yaml",),
        rule_id="manifest_schema_valid",
        issues=issues,
    )
    _validate_schema_targets(
        repo_root=repo_root,
        schema_path=repo_root / "docs" / "governance" / "schemas" / "cell.schema.yaml",
        target_patterns=("polaris/cells/*/*/cell.yaml",),
        rule_id="manifest_schema_valid",
        issues=issues,
    )

    _check_owned_path_overlaps(file_owners=file_owners, issues=issues)
    _check_single_state_owner(cells=cells, issues=issues)
    _check_cross_cell_internal_imports(repo_root=repo_root, file_owners=file_owners, issues=issues)
    _check_declared_cell_dependencies(repo_root=repo_root, cells=cells, issues=issues)
    _check_critical_subgraphs(repo_root=repo_root, issues=issues)
    _check_undeclared_effects(
        repo_root=repo_root,
        file_owners=file_owners,
        owner_effects=owner_effects,
        issues=issues,
    )

    # Manifest-catalog reconciliation (G-2: dual-source drift)
    # MC findings go ONLY into the mismatch baseline -- NOT into the shared issues list.
    catalog_cells_tuple = tuple(cells)
    mismatches = _check_manifest_catalog_consistency(
        repo_root=repo_root,
        catalog_cells=catalog_cells_tuple,
    )
    # Build mc_issues from mismatches for mismatch_info reporting.
    # These are NOT added to the shared issues list.
    mc_issues: tuple[GovernanceIssue, ...] = tuple(
        GovernanceIssue(
            rule_id=_RULE_MANIFEST_CATALOG_CONSISTENCY,
            severity=_SEVERITY_BLOCKER,
            message=(
                f"[manifest-catalog drift] cell={mm.cell_id} field={mm.field} "
                f"type={mm.mismatch_type} manifest={mm.manifest_value}"
            ),
            path=f"polaris/cells/{mm.cell_id.replace('.', '/')}/cell.yaml",
        )
        for mm in mismatches
    )
    new_mismatch_count = _count_new_mismatches(mismatches, mismatch_baseline_path)

    issues_tuple = tuple(issues)
    blocker_count = sum(1 for item in issues_tuple if item.severity == _SEVERITY_BLOCKER)
    high_count = sum(1 for item in issues_tuple if item.severity == _SEVERITY_HIGH)
    new_issue_count = _count_new_issues(issues_tuple, mode=mode, baseline_path=baseline_path)

    # In fail-on-new mode, manifest-catalog new mismatches contribute to exit code
    mc_new_count = new_mismatch_count
    if mode == _MODE_FAIL_ON_NEW:
        total_new = new_issue_count + mc_new_count
        exit_code = 1 if total_new > 0 else 0
    else:
        exit_code = _resolve_exit_code(
            mode=mode,
            blocker_count=blocker_count,
            high_count=high_count,
            new_issue_count=new_issue_count,
        )

    report = GovernanceReport(
        workspace=str(repo_root),
        mode=mode,
        exit_code=exit_code,
        issue_count=len(issues_tuple),
        blocker_count=blocker_count,
        high_count=high_count,
        new_issue_count=new_issue_count,
        issues=issues_tuple,
    )

    # Attach mismatch metadata to report via a side-channel dict
    mismatch_info: dict[str, Any] = {
        "mismatch_count": len(mismatches),
        "new_mismatch_count": mc_new_count,
        "mismatches": [mm.to_dict() for mm in mismatches],
        "mc_blocker_count": sum(1 for iss in mc_issues if iss.severity == _SEVERITY_BLOCKER),
    }
    return report, mismatch_info


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ACGA catalog governance gate checks.")
    parser.add_argument("--workspace", default=".", help="Repository root (defaults to current directory)")
    parser.add_argument(
        "--mode",
        default=_MODE_FAIL_ON_NEW,
        choices=_SUPPORTED_MODES,
        help="Gate mode",
    )
    parser.add_argument("--baseline", help="Baseline JSON for fail-on-new mode")
    parser.add_argument("--report", help="Output report JSON path")
    parser.add_argument("--write-baseline", help="Write current fingerprints to this JSON path")
    parser.add_argument(
        "--mismatch-baseline",
        dest="mismatch_baseline",
        help="JSON Lines baseline for manifest-catalog reconciliation (fail-on-new mode)",
    )
    parser.add_argument(
        "--write-mismatch-baseline",
        dest="write_mismatch_baseline",
        help="Write current manifest-catalog mismatches to this JSON Lines file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    baseline_path = Path(str(args.baseline)).resolve() if args.baseline else None
    mismatch_baseline_path = Path(str(args.mismatch_baseline)).resolve() if args.mismatch_baseline else None
    report, mismatch_info = run_governance_gate(
        workspace=str(args.workspace),
        mode=str(args.mode),
        baseline_path=baseline_path,
        mismatch_baseline_path=mismatch_baseline_path,
    )
    payload = report.to_dict()
    payload["manifest_catalog"] = mismatch_info
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    print(serialized)

    if args.report:
        report_path = Path(str(args.report)).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(serialized, encoding="utf-8")

    if args.write_baseline:
        _write_baseline(Path(str(args.write_baseline)).resolve(), report)

    if args.write_mismatch_baseline:
        mismatches_list: list[dict[str, Any]] = mismatch_info.get("mismatches", [])
        mm_objects = [
            ManifestCatalogMismatch(
                cell_id=m["cell_id"],
                field=m["field"],
                mismatch_type=m["mismatch_type"],
                manifest_value=m["manifest_value"],
                catalog_value=m["catalog_value"],
            )
            for m in mismatches_list
        ]
        _write_mismatch_baseline(Path(str(args.write_mismatch_baseline)).resolve(), mm_objects)

    # Adjust exit code for new mismatches in fail-on-new mode
    final_exit = report.exit_code
    if str(args.mode) == _MODE_FAIL_ON_NEW and mismatch_info.get("new_mismatch_count", 0) > 0:
        final_exit = 1

    return int(final_exit)


if __name__ == "__main__":
    raise SystemExit(main())
