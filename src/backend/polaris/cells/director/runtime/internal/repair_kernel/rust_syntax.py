"""Rust deterministic repair planners owned by Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

RUST_DEPENDENCY_SOURCE_TOOL = "deterministic_rust_dependency_repair"

_RUST_UNRESOLVED_IMPORT_RE = re.compile(
    r"unresolved import [`'\"](?P<import>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)
_KNOWN_RUST_DEPENDENCIES: dict[str, str] = {
    "serde": 'serde = { version = "1.0", features = ["derive"] }',
    "serde_json": 'serde_json = "1.0"',
}


def build_rust_dependency_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for known missing Rust dependencies."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    cargo_text = normalized_base.get("Cargo.toml")
    if cargo_text is None:
        return None

    packages = _rust_dependency_packages_to_add(normalized_base, diagnostics)
    missing = [package for package in packages if not _cargo_dependency_declared(cargo_text, package)]
    if not missing:
        return None

    repaired = cargo_text
    for package in missing:
        repaired = _insert_cargo_dependency(repaired, _KNOWN_RUST_DEPENDENCIES[package])
    if repaired == cargo_text:
        return None

    return RepairPlan(
        rule_id="rust.unlinked_crate_dependency",
        source_tool=RUST_DEPENDENCY_SOURCE_TOOL,
        operations=(
            RepairOperation(
                kind="write_file",
                path="Cargo.toml",
                content=repaired,
                before_hash=sha256_text(cargo_text),
                metadata={"repair_kind": "rust_dependency", "packages": tuple(missing)},
            ),
        ),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=0,
    )


def _rust_dependency_packages_to_add(
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        for match in _RUST_UNRESOLVED_IMPORT_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            root = match.group("import").split("::", 1)[0]
            if root in _KNOWN_RUST_DEPENDENCIES and root not in seen:
                seen.add(root)
                packages.append(root)

    source_text = "\n".join(
        content for path, content in sorted(base_files.items()) if path.endswith(".rs") and "/target/" not in path
    )
    if "serde_json::" in source_text and "serde_json" not in seen:
        seen.add("serde_json")
        packages.append("serde_json")
    return packages


def _cargo_dependency_declared(cargo_text: str, package: str) -> bool:
    return bool(re.search(rf"(?m)^\s*{re.escape(package)}\s*=", cargo_text))


def _insert_cargo_dependency(cargo_text: str, dependency_line: str) -> str:
    dependency_header = re.search(r"(?m)^\[dependencies\]\s*$", cargo_text)
    if not dependency_header:
        suffix = "" if cargo_text.endswith("\n") else "\n"
        return f"{cargo_text}{suffix}\n[dependencies]\n{dependency_line}\n"
    insert_at = dependency_header.end()
    return f"{cargo_text[:insert_at]}\n{dependency_line}{cargo_text[insert_at:]}"


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
