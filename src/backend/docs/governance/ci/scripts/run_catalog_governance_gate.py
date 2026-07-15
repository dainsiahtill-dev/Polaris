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
        key = f"{self.rule_id}|{self.severity}|{self.path}|{self.message}"
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
_RULE_FACT_STREAM_SURFACE_DRIFT = "fact_stream_surface_drift"
_RULE_DECLARED_CELL_DEPENDENCIES_MATCH_IMPORTS = "declared_cell_dependencies_match_imports"

_FACT_STREAM_CELL_ID = "events.fact_stream"
_FACT_STREAM_ROOT_REL = "polaris/cells/events/fact_stream/__init__.py"
_FACT_STREAM_PUBLIC_REL = "polaris/cells/events/fact_stream/public/__init__.py"
_FACT_STREAM_CONTRACTS_REL = "polaris/cells/events/fact_stream/public/contracts.py"
_FACT_STREAM_MANIFEST_REL = "polaris/cells/events/fact_stream/cell.yaml"
_FACT_STREAM_README_REL = "polaris/cells/events/fact_stream/README.agent.md"
_FACT_STREAM_CONTEXT_PACK_REL = "polaris/cells/events/fact_stream/generated/context.pack.json"
_FACT_STREAM_OWNED_PATHS = frozenset({"polaris/cells/events/fact_stream/**"})
_FACT_STREAM_PUBLIC_MODULES = frozenset(
    {
        "polaris.cells.events.fact_stream.public",
        "polaris.cells.events.fact_stream.public.catalog",
        "polaris.cells.events.fact_stream.public.contracts",
        "polaris.cells.events.fact_stream.public.service",
        "polaris.cells.events.fact_stream.public.workspace_bootstrap",
    }
)
_FACT_STREAM_REQUIRED_EFFECTS = frozenset(
    {
        "fs.read:runtime/events/*",
        "fs.write:runtime/events/*",
    }
)
_FACT_STREAM_CONTRACT_KINDS = ("commands", "queries", "events", "results", "errors")
_FACT_STREAM_PUBLIC_EXPORT_COUNT = 37

# Cross-cell acyclicity (GATE-01): a NEW policy enhancement.
# ACGA 2.0 has NO on-disk peer-cell acyclicity rule, so this gate does not enforce a
# pre-existing rule. Instead it freezes the set of dependency cycles currently declared
# in cells.yaml as an allowlist and fails only on cycles that are NEW relative to that
# allowlist, mirroring the fail-on-new baseline mechanism used elsewhere in this gate.
_RULE_NO_NEW_CROSS_CELL_CYCLE = "no_new_cross_cell_cycle"
_CYCLE_ALLOWLIST_REL = "docs/governance/ci/cell-cycle-allowlist.yaml"


@dataclass(frozen=True)
class CatalogCell:
    """Normalized cell record for rule checks."""

    cell_id: str
    owned_paths: tuple[str, ...]
    depends_on: tuple[str, ...]
    state_owners: tuple[str, ...]
    effects_allowed: tuple[str, ...]


@dataclass(frozen=True)
class CycleAllowlistBaseline:
    """One frozen SCC baseline, including every internal directed edge."""

    members: frozenset[str]
    internal_edges: frozenset[str]


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


def _repo_relative_path(repo_root: Path, candidate: Path | str) -> str:
    path = Path(candidate)
    try:
        return _normalize_rel(path.resolve().relative_to(repo_root.resolve()).as_posix())
    except (OSError, ValueError):
        return _normalize_rel(str(candidate))


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _literal_string_sequence(tree: ast.AST, name: str) -> tuple[str, ...] | None:
    """Return a literal list or tuple assigned to ``name`` without importing code."""
    for node in getattr(tree, "body", []):
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        ) or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name):
            value = node.value
        if value is None:
            continue
        try:
            raw = ast.literal_eval(value)
        except (TypeError, ValueError):
            return None
        if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
            return None
        return tuple(raw)
    return None


def _parse_python_module(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


def _fact_stream_issue(issues: list[GovernanceIssue], *, path: str, message: str) -> None:
    issues.append(
        GovernanceIssue(
            rule_id=_RULE_FACT_STREAM_SURFACE_DRIFT,
            severity=_SEVERITY_BLOCKER,
            message=message,
            path=path,
        )
    )


def _mapping_string_set(
    payload: dict[str, Any],
    *,
    field: str,
) -> frozenset[str] | None:
    raw = payload.get(field)
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        return None
    return frozenset(item.strip() for item in raw)


def _mapping_string_sequence(
    payload: dict[str, Any],
    *,
    field: str,
) -> tuple[str, ...] | None:
    """Return a non-empty ordered string list from structured metadata."""
    raw = payload.get(field)
    if not isinstance(raw, list) or not raw:
        return None
    if not all(isinstance(item, str) and item.strip() for item in raw):
        return None
    return tuple(item.strip() for item in raw)


def _fact_stream_contract_exports(
    contracts_path: Path,
) -> tuple[frozenset[str], dict[str, frozenset[str]]] | None:
    tree = _parse_python_module(contracts_path)
    if tree is None:
        return None
    exports = _literal_string_sequence(tree, "__all__")
    if exports is None:
        return None

    runtime_error_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "RuntimeError" for base in node.bases)
    }
    categorized: dict[str, set[str]] = {kind: set() for kind in _FACT_STREAM_CONTRACT_KINDS}
    for name in exports:
        if name in runtime_error_classes:
            categorized["errors"].add(name)
        elif name.startswith(("Query", "Read")) and name.endswith("V1"):
            categorized["queries"].add(name)
        elif name.endswith("CommandV1"):
            categorized["commands"].add(name)
        elif "Event" in name and name.endswith("V1"):
            categorized["events"].add(name)
        else:
            categorized["results"].add(name)
    return frozenset(exports), {kind: frozenset(values) for kind, values in categorized.items()}


def _readme_contract_projection(path: Path) -> dict[str, frozenset[str]] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    contracts: dict[str, set[str]] = {kind: set() for kind in _FACT_STREAM_CONTRACT_KINDS}
    active_kind: str | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("##"):
            active_kind = None
            continue
        if stripped.startswith("- ") and stripped.endswith(":"):
            candidate = stripped[2:-1].strip()
            active_kind = candidate if candidate in contracts else None
            continue
        if active_kind is None or not stripped.startswith("- "):
            continue
        value = stripped[2:].strip().strip("`")
        if value:
            contracts[active_kind].add(value)
    if not all(contracts.values()):
        return None
    return {kind: frozenset(values) for kind, values in contracts.items()}


def _readme_public_surface(path: Path) -> tuple[str, ...] | None:
    """Parse the exact public export list from the README's dedicated section."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    exports: list[str] = []
    in_surface = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            if in_surface:
                break
            in_surface = stripped == "## Public Surface"
            continue
        if not in_surface:
            continue
        if not stripped:
            continue
        if not stripped.startswith("- `") or not stripped.endswith("`"):
            return None
        export = stripped[3:-1].strip()
        if not export:
            return None
        exports.append(export)
    return tuple(exports) if exports else None


def _check_fact_stream_contract_projection(
    *,
    artifact: str,
    path: str,
    projection: dict[str, frozenset[str]] | None,
    contract_exports: frozenset[str],
    expected_contracts: dict[str, frozenset[str]],
    issues: list[GovernanceIssue],
) -> None:
    if projection is None:
        _fact_stream_issue(
            issues,
            path=path,
            message=f"FactStream {artifact} contract projection is missing or malformed.",
        )
        return
    for kind in _FACT_STREAM_CONTRACT_KINDS:
        declared = projection.get(kind, frozenset())
        nonexistent = declared - contract_exports
        if nonexistent:
            _fact_stream_issue(
                issues,
                path=path,
                message=(
                    f"FactStream {artifact} declares nonexistent {kind}: "
                    + ", ".join(sorted(nonexistent))
                ),
            )
        missing = expected_contracts[kind] - declared
        extra = declared - expected_contracts[kind]
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("missing=" + ", ".join(sorted(missing)))
            if extra:
                details.append("unexpected=" + ", ".join(sorted(extra)))
            _fact_stream_issue(
                issues,
                path=path,
                message=f"FactStream {artifact} {kind} drift: " + "; ".join(details),
            )


def _check_fact_stream_metadata_set(
    *,
    artifact: str,
    path: str,
    payload: dict[str, Any],
    field: str,
    expected: frozenset[str],
    issues: list[GovernanceIssue],
) -> None:
    actual = _mapping_string_set(payload, field=field)
    if actual is None:
        _fact_stream_issue(
            issues,
            path=path,
            message=f"FactStream {artifact} {field} must be a non-empty string list.",
        )
        return
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing=" + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected=" + ", ".join(sorted(extra)))
        _fact_stream_issue(
            issues,
            path=path,
            message=f"FactStream {artifact} {field} drift: " + "; ".join(details),
        )


def _check_fact_stream_public_surface_projection(
    *,
    artifact: str,
    path: str,
    projection: tuple[str, ...] | None,
    expected_exports: tuple[str, ...],
    issues: list[GovernanceIssue],
) -> None:
    """Require an ordered, exact projection of the public facade exports."""
    if projection is None:
        _fact_stream_issue(
            issues,
            path=path,
            message=f"FactStream {artifact} public_surface.exports is missing or malformed.",
        )
        return
    if projection != expected_exports:
        _fact_stream_issue(
            issues,
            path=path,
            message=f"FactStream {artifact} public_surface.exports drift from public.__all__.",
        )


def _check_fact_stream_surface_drift(
    *,
    repo_root: Path,
    catalog_payload: dict[str, Any],
    issues: list[GovernanceIssue],
) -> None:
    """Verify the Cell facade and derived FactStream governance projections.

    This check intentionally parses Python exports plus YAML/JSON/Markdown
    structures. It does not use repository-wide keyword matching, so prose or
    unrelated type names cannot create a false positive.
    """
    root_path = repo_root / _FACT_STREAM_ROOT_REL
    public_path = repo_root / _FACT_STREAM_PUBLIC_REL
    contracts_path = repo_root / _FACT_STREAM_CONTRACTS_REL
    manifest_path = repo_root / _FACT_STREAM_MANIFEST_REL
    readme_path = repo_root / _FACT_STREAM_README_REL
    context_path = repo_root / _FACT_STREAM_CONTEXT_PACK_REL

    public_tree = _parse_python_module(public_path)
    root_tree = _parse_python_module(root_path)
    public_exports = _literal_string_sequence(public_tree, "__all__") if public_tree else None
    root_exports = _literal_string_sequence(root_tree, "__all__") if root_tree else None
    if public_exports is None or root_exports is None:
        _fact_stream_issue(
            issues,
            path=_FACT_STREAM_ROOT_REL,
            message="FactStream root or public facade has no literal string __all__ surface.",
        )
    else:
        expected_export_set = frozenset(public_exports)
        actual_exports = frozenset(root_exports)
        missing = expected_export_set - actual_exports
        extra = actual_exports - expected_export_set
        if missing or extra or tuple(root_exports) != tuple(public_exports):
            details: list[str] = []
            if missing:
                details.append("missing=" + ", ".join(sorted(missing)))
            if extra:
                details.append("unexpected=" + ", ".join(sorted(extra)))
            if not missing and not extra:
                details.append("export order differs from public.__all__")
            _fact_stream_issue(
                issues,
                path=_FACT_STREAM_ROOT_REL,
                message="FactStream root facade drift: " + "; ".join(details),
            )
        imported_from_public: set[str] = set()
        unexpected_root_imports: list[str] = []
        if root_tree is not None:
            for node in root_tree.body:
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "public":
                    for alias in node.names:
                        if alias.name != "*":
                            imported_from_public.add(alias.asname or alias.name)
                    continue
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "__future__":
                    continue
                if isinstance(node, ast.Import):
                    unexpected_root_imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = "." * node.level + str(node.module or "")
                    unexpected_root_imports.append(module)
        if imported_from_public != expected_export_set:
            _fact_stream_issue(
                issues,
                path=_FACT_STREAM_ROOT_REL,
                message="FactStream root imports do not exactly re-export public.__all__.",
            )
        if unexpected_root_imports:
            _fact_stream_issue(
                issues,
                path=_FACT_STREAM_ROOT_REL,
                message=(
                    "FactStream root imports outside the public facade: "
                    + ", ".join(sorted(unexpected_root_imports))
                ),
            )

    if public_exports is None:
        expected_public_exports: tuple[str, ...] = ()
    else:
        expected_public_exports = tuple(public_exports)
        if len(expected_public_exports) != _FACT_STREAM_PUBLIC_EXPORT_COUNT:
            _fact_stream_issue(
                issues,
                path=_FACT_STREAM_PUBLIC_REL,
                message=(
                    "FactStream public facade must expose exactly "
                    f"{_FACT_STREAM_PUBLIC_EXPORT_COUNT} names, found {len(expected_public_exports)}."
                ),
            )

    contract_surface = _fact_stream_contract_exports(contracts_path)
    if contract_surface is None:
        _fact_stream_issue(
            issues,
            path=_FACT_STREAM_CONTRACTS_REL,
            message="FactStream contracts module has no literal public contract __all__ surface.",
        )
        return
    contract_exports, expected_contracts = contract_surface

    try:
        manifest_payload = _read_yaml(manifest_path)
    except (OSError, yaml.YAMLError):
        manifest_payload = None
    context_payload: Any
    try:
        context_payload = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        context_payload = None
    catalog_cell = next(
        (
            item
            for item in catalog_payload.get("cells", [])
            if isinstance(item, dict) and item.get("id") == _FACT_STREAM_CELL_ID
        ),
        None,
    )

    artifacts = (
        ("manifest", _FACT_STREAM_MANIFEST_REL, manifest_payload),
        ("catalog", "docs/graph/catalog/cells.yaml", catalog_cell),
        ("context pack", _FACT_STREAM_CONTEXT_PACK_REL, context_payload),
    )
    for artifact, path, payload in artifacts:
        if not isinstance(payload, dict):
            _fact_stream_issue(
                issues,
                path=path,
                message=f"FactStream {artifact} is missing or malformed.",
            )
            continue
        public_surface_payload = payload.get("public_surface")
        public_projection = None
        if isinstance(public_surface_payload, dict):
            public_projection = _mapping_string_sequence(public_surface_payload, field="exports")
        _check_fact_stream_public_surface_projection(
            artifact=artifact,
            path=path,
            projection=public_projection,
            expected_exports=expected_public_exports,
            issues=issues,
        )
        contracts_payload = payload.get("public_contracts")
        projection = None
        if isinstance(contracts_payload, dict):
            projection = {
                kind: _mapping_string_set(contracts_payload, field=kind) or frozenset()
                for kind in _FACT_STREAM_CONTRACT_KINDS
            }
        _check_fact_stream_contract_projection(
            artifact=artifact,
            path=path,
            projection=projection,
            contract_exports=contract_exports,
            expected_contracts=expected_contracts,
            issues=issues,
        )
        _check_fact_stream_metadata_set(
            artifact=artifact,
            path=path,
            payload=payload,
            field="owned_paths",
            expected=_FACT_STREAM_OWNED_PATHS,
            issues=issues,
        )
        _check_fact_stream_metadata_set(
            artifact=artifact,
            path=path,
            payload=payload,
            field="effects_allowed",
            expected=_FACT_STREAM_REQUIRED_EFFECTS,
            issues=issues,
        )

    if isinstance(manifest_payload, dict):
        _check_fact_stream_metadata_set(
            artifact="manifest",
            path=_FACT_STREAM_MANIFEST_REL,
            payload=manifest_payload,
            field="current_modules",
            expected=_FACT_STREAM_PUBLIC_MODULES,
            issues=issues,
        )
        _check_fact_stream_metadata_set(
            artifact="manifest",
            path=_FACT_STREAM_MANIFEST_REL,
            payload=manifest_payload,
            field="depends_on",
            expected=frozenset(),
            issues=issues,
        )
    if isinstance(catalog_cell, dict):
        _check_fact_stream_metadata_set(
            artifact="catalog",
            path="docs/graph/catalog/cells.yaml",
            payload=catalog_cell,
            field="current_modules",
            expected=_FACT_STREAM_PUBLIC_MODULES,
            issues=issues,
        )
        _check_fact_stream_metadata_set(
            artifact="catalog",
            path="docs/graph/catalog/cells.yaml",
            payload=catalog_cell,
            field="depends_on",
            expected=frozenset(),
            issues=issues,
        )
    if isinstance(context_payload, dict):
        _check_fact_stream_metadata_set(
            artifact="context pack",
            path=_FACT_STREAM_CONTEXT_PACK_REL,
            payload=context_payload,
            field="neighbors",
            expected=frozenset(),
            issues=issues,
        )

    _check_fact_stream_contract_projection(
        artifact="README",
        path=_FACT_STREAM_README_REL,
        projection=_readme_contract_projection(readme_path),
        contract_exports=contract_exports,
        expected_contracts=expected_contracts,
        issues=issues,
    )
    _check_fact_stream_public_surface_projection(
        artifact="README",
        path=_FACT_STREAM_README_REL,
        projection=_readme_public_surface(readme_path),
        expected_exports=expected_public_exports,
        issues=issues,
    )


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
    except (OSError, yaml.YAMLError):
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
            if (
                manifest_path == prefix
                or manifest_path.startswith(prefix + "/")
                or manifest_path.startswith(prefix + "\\")
            ):
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
    except OSError:
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
        except json.JSONDecodeError:
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
                path=_repo_relative_path(repo_root, schema_path),
            )
        )
        return

    registry = Registry()
    schema_dir = schema_path.parent
    for candidate in sorted(schema_dir.glob("*.yaml")):
        try:
            schema_payload = _read_yaml(candidate)
        except (OSError, yaml.YAMLError):
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
            except (OSError, yaml.YAMLError) as exc:
                issues.append(
                    GovernanceIssue(
                        rule_id=rule_id,
                        severity=_SEVERITY_BLOCKER,
                        message=f"Failed to parse YAML: {exc}",
                        path=_repo_relative_path(repo_root, target),
                    )
                )
                continue
            payload = _legacy_schema_compatible_payload(repo_root=repo_root, target=target, payload=payload)
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
                        path=_repo_relative_path(repo_root, target),
                    )
                )


def _legacy_schema_compatible_payload(*, repo_root: Path, target: Path, payload: Any) -> Any:
    """Hide the FactStream-only surface extension from pre-extension schemas.

    ``public_surface`` is validated more strictly by the dedicated FactStream
    governance rule below. The shared catalog schemas predate that additive
    metadata field, so this compatibility projection preserves their existing
    constraints without weakening validation for any other Cell or field.
    """
    target_rel = _repo_relative_path(repo_root, target)
    if target_rel == _FACT_STREAM_MANIFEST_REL and isinstance(payload, dict):
        projected = dict(payload)
        projected.pop("public_surface", None)
        return projected
    if target_rel != "docs/graph/catalog/cells.yaml" or not isinstance(payload, dict):
        return payload
    cells = payload.get("cells")
    if not isinstance(cells, list):
        return payload
    projected_cells: list[Any] = []
    for cell in cells:
        if isinstance(cell, dict) and cell.get("id") == _FACT_STREAM_CELL_ID:
            projected_cell = dict(cell)
            projected_cell.pop("public_surface", None)
            projected_cells.append(projected_cell)
        else:
            projected_cells.append(cell)
    projected_payload = dict(payload)
    projected_payload["cells"] = projected_cells
    return projected_payload


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
        except (OSError, SyntaxError, ValueError) as exc:
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


def _build_depends_on_graph(cells: Iterable[CatalogCell]) -> dict[str, tuple[str, ...]]:
    """Build the directed cell dependency graph from declared depends_on edges.

    Edges to unknown cells (not present in the catalog) are dropped so the graph
    only reflects relationships that the catalog itself can reason about.
    """
    catalog_cells = tuple(cells)
    known_cells = {cell.cell_id for cell in catalog_cells}
    graph: dict[str, tuple[str, ...]] = {}
    for cell in catalog_cells:
        targets = tuple(
            dependency for dependency in cell.depends_on if dependency in known_cells and dependency != cell.cell_id
        )
        graph[cell.cell_id] = targets
    return graph


def _strongly_connected_components(graph: dict[str, tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Return strongly connected components with more than one member (the cycles).

    Uses an iterative Tarjan traversal so deeply nested catalogs cannot overflow the
    recursion limit. Each returned component is a sorted tuple of cell ids; the order of
    the returned list is deterministic (sorted by the component members).
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    scc_stack: list[str] = []
    counter = 0
    components: list[tuple[str, ...]] = []

    for root in sorted(graph):
        if root in index_of:
            continue
        # Each work item is (node, index into its neighbour list).
        work_stack: list[tuple[str, int]] = [(root, 0)]
        while work_stack:
            node, next_child = work_stack[-1]
            if next_child == 0:
                index_of[node] = counter
                lowlink[node] = counter
                counter += 1
                scc_stack.append(node)
                on_stack.add(node)
            neighbours = graph.get(node, ())
            if next_child < len(neighbours):
                work_stack[-1] = (node, next_child + 1)
                child = neighbours[next_child]
                if child not in index_of:
                    work_stack.append((child, 0))
                elif child in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[child])
                continue
            # All neighbours processed: settle this node.
            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = scc_stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(tuple(sorted(component)))
            work_stack.pop()
            if work_stack:
                parent, _ = work_stack[-1]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

    return sorted(components)


def _cycle_fingerprint(component: tuple[str, ...]) -> str:
    """Stable fingerprint for one dependency cycle (a strongly connected component)."""
    key = "cycle|" + "|".join(sorted(component))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _internal_component_edges(
    graph: dict[str, tuple[str, ...]],
    members: Iterable[str],
) -> tuple[str, ...]:
    """Return sorted ``source -> target`` edges whose endpoints share one SCC."""
    member_set = frozenset(members)
    return tuple(
        sorted(
            f"{source} -> {target}"
            for source in member_set
            for target in graph.get(source, ())
            if target in member_set
        )
    )


def _load_cycle_allowlist(repo_root: Path) -> set[str]:
    """Load the frozen set of allowlisted cycle fingerprints.

    The allowlist file lists the cycles that already exist in cells.yaml at the time the
    rule was introduced. A missing or malformed file yields an empty allowlist, which is
    fail-closed: every detected cycle is then treated as new.
    """
    allowlist_path = repo_root / _CYCLE_ALLOWLIST_REL
    if not allowlist_path.is_file():
        return set()
    try:
        payload = yaml.safe_load(allowlist_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(payload, dict):
        return set()
    entries = payload.get("cycles")
    if not isinstance(entries, list):
        return set()
    fingerprints: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fingerprint = entry.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint.strip():
            fingerprints.add(fingerprint.strip())
    return fingerprints


def _load_cycle_allowlist_baselines(repo_root: Path) -> tuple[CycleAllowlistBaseline, ...]:
    """Load structurally valid SCC baselines with their frozen internal edges.

    Missing, malformed, or edge-incomplete entries are omitted deliberately.
    The cycle gate then treats the affected observed SCC as new, which keeps the
    allowlist fail-closed instead of silently accepting an underspecified baseline.
    """
    allowlist_path = repo_root / _CYCLE_ALLOWLIST_REL
    if not allowlist_path.is_file():
        return ()
    try:
        payload = yaml.safe_load(allowlist_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ()
    if not isinstance(payload, dict) or not isinstance(payload.get("cycles"), list):
        return ()

    baselines: list[CycleAllowlistBaseline] = []
    for entry in payload["cycles"]:
        if not isinstance(entry, dict):
            continue
        raw_members = entry.get("members")
        raw_edges = entry.get("edges")
        if not isinstance(raw_members, list) or not isinstance(raw_edges, list):
            continue
        members = frozenset(str(member).strip() for member in raw_members if str(member).strip())
        edges = tuple(str(edge).strip() for edge in raw_edges if isinstance(edge, str) and edge.strip())
        if len(members) <= 1 or not edges or len(edges) != len(set(edges)):
            continue
        valid_edges = True
        for edge in edges:
            source, separator, target = edge.partition(" -> ")
            if not separator or not source or not target or source not in members or target not in members:
                valid_edges = False
                break
        if valid_edges:
            baselines.append(CycleAllowlistBaseline(members=members, internal_edges=frozenset(edges)))
    return tuple(baselines)


def _check_no_new_cross_cell_cycle(
    *,
    repo_root: Path,
    cells: Iterable[CatalogCell],
    issues: list[GovernanceIssue],
) -> None:
    """Fail when an observed SCC exceeds its members-and-edges baseline."""
    graph = _build_depends_on_graph(cells)
    components = _strongly_connected_components(graph)
    baselines = _load_cycle_allowlist_baselines(repo_root)
    for component in components:
        component_members = frozenset(component)
        observed_edges = frozenset(_internal_component_edges(graph, component_members))
        matching_member_baselines = tuple(
            baseline for baseline in baselines if component_members <= baseline.members
        )
        if any(observed_edges <= baseline.internal_edges for baseline in matching_member_baselines):
            continue
        if matching_member_baselines:
            allowed_edges = frozenset().union(
                *(baseline.internal_edges for baseline in matching_member_baselines)
            )
            unexpected_edges = sorted(observed_edges - allowed_edges)
            reason = "new internal edge(s): " + ", ".join(unexpected_edges)
        else:
            reason = "members are not a subset of any baseline SCC"
        issues.append(
            GovernanceIssue(
                rule_id=_RULE_NO_NEW_CROSS_CELL_CYCLE,
                severity=_SEVERITY_HIGH,
                message=(
                    "Cross-cell dependency cycle exceeds the allowlist baseline ("
                    + reason
                    + "): "
                    + " -> ".join(component)
                    + ". Break the cycle or, if intentional, add it to "
                    + _CYCLE_ALLOWLIST_REL
                    + " via --write-cycle-allowlist."
                ),
                path=_CYCLE_ALLOWLIST_REL,
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
        except (OSError, SyntaxError, ValueError) as exc:
            issues.append(
                GovernanceIssue(
                    rule_id=_RULE_DECLARED_CELL_DEPENDENCIES_MATCH_IMPORTS,
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
                rule_id=_RULE_DECLARED_CELL_DEPENDENCIES_MATCH_IMPORTS,
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
        except (OSError, SyntaxError, ValueError):
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
                if (
                    any(token in mode for token in ("w", "a", "x"))
                    and "b" not in mode
                    and not _effect_token_exists(effects, "fs.write:")
                ):
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
    except (OSError, json.JSONDecodeError):
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


def _write_cycle_allowlist(repo_root: Path, cells: Iterable[CatalogCell]) -> None:
    """Regenerate the cross-cell cycle allowlist from the current catalog.

    Writes one entry per currently-declared dependency cycle so that re-running the gate
    treats them as known and passes. Use this only after a deliberate, reviewed decision
    to accept the listed cycles.
    """
    graph = _build_depends_on_graph(cells)
    components = _strongly_connected_components(graph)
    lines = [
        "# Allowlist of cross-cell dependency cycles known to cells.yaml.",
        "# Regenerate with: run_catalog_governance_gate.py --write-cycle-allowlist <path>",
        "# Each entry freezes one SCC's members and exact internal directed edges. The",
        "# no_new_cross_cell_cycle rule permits only member and edge subsets of this snapshot.",
        "cycles:",
    ]
    for component in components:
        lines.append(f"  - fingerprint: {_cycle_fingerprint(component)}")
        members = ", ".join(component)
        lines.append(f"    members: [{members}]")
        edges = ", ".join(_internal_component_edges(graph, component))
        lines.append(f"    edges: [{edges}]")
    allowlist_path = repo_root / _CYCLE_ALLOWLIST_REL
    allowlist_path.parent.mkdir(parents=True, exist_ok=True)
    allowlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
            path=_repo_relative_path(repo_root, catalog_path),
        )
        missing_catalog_issues = (issue,)
        new_issue_count = _count_new_issues(missing_catalog_issues, mode=mode, baseline_path=baseline_path)
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
            issues=missing_catalog_issues,
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
    _check_no_new_cross_cell_cycle(repo_root=repo_root, cells=cells, issues=issues)
    _check_critical_subgraphs(repo_root=repo_root, issues=issues)
    if isinstance(catalog_payload, dict):
        _check_fact_stream_surface_drift(
            repo_root=repo_root,
            catalog_payload=catalog_payload,
            issues=issues,
        )
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
    parser.add_argument(
        "--write-cycle-allowlist",
        dest="write_cycle_allowlist",
        action="store_true",
        help=("Regenerate the cross-cell cycle allowlist (" + _CYCLE_ALLOWLIST_REL + ") from the current catalog"),
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

    if args.write_cycle_allowlist:
        repo_root = Path(str(args.workspace)).resolve()
        catalog_path = repo_root / "docs" / "graph" / "catalog" / "cells.yaml"
        catalog_payload = _read_yaml(catalog_path) if catalog_path.is_file() else None
        cells = _build_cell_index(catalog_payload) if isinstance(catalog_payload, dict) else []
        _write_cycle_allowlist(repo_root, cells)

    # Adjust exit code for new mismatches in fail-on-new mode
    final_exit = report.exit_code
    if str(args.mode) == _MODE_FAIL_ON_NEW and mismatch_info.get("new_mismatch_count", 0) > 0:
        final_exit = 1

    return int(final_exit)


if __name__ == "__main__":
    raise SystemExit(main())
