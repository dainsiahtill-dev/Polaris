"""KernelOne top-level admission fence (foundation hardening stage A).

Freezes the ``polaris/kernelone/`` top-level directory *set* against
``docs/graph/catalog/kernelone_capabilities.yaml``.

This gate does not forbid file evolution inside already-registered
directories. A new top-level subtree requires an ADR plus a catalog row.

Scan scope is a single ``iterdir()`` of the KernelOne root. Hidden
directories and ``__pycache__`` are ignored. Top-level ``.py`` files
are not part of the frozen set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
KERNELONE_ROOT = BACKEND_ROOT / "polaris" / "kernelone"
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "kernelone_capabilities.yaml"

_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset({"__pycache__"})
_ALLOWED_KINDS: Final[frozenset[str]] = frozenset({"capability", "support", "test_root", "legacy_or_overlap"})
_REQUIRED_ENTRY_KEYS: Final[tuple[str, ...]] = (
    "id",
    "path",
    "kind",
    "public_entry",
    "allowed_new_files",
    "notes",
)
_ADMISSION_HOW_TO: Final[str] = (
    "To add a new polaris/kernelone top-level subtree you must:\n"
    "  1. Write an ADR under src/backend/docs/adr/ proving KERNELONE_ARCHITECTURE_SPEC.md "
    "§2 / §3.5 admission (no Polaris business semantics, independently testable, "
    "stable technical contract).\n"
    "  2. Add a matching entry to src/backend/docs/graph/catalog/kernelone_capabilities.yaml "
    "(id, path, kind, public_entry, allowed_new_files, notes).\n"
    "This gate freezes the top-level directory SET only; files inside existing "
    "directories may evolve without a new catalog row."
)


def _require_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a YAML mapping, got {type(value).__name__}")
    return value


def _require_str(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise AssertionError(f"{label} must be a string, got {type(value).__name__}")
    return value


def _require_sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{label} must be a YAML list, got {type(value).__name__}")
    return value


def load_catalog_document() -> Mapping[str, object]:
    """Load and parse the KernelOne capability catalog as UTF-8 YAML."""
    assert CATALOG_PATH.is_file(), f"KernelOne capability catalog missing: {CATALOG_PATH}"
    raw = CATALOG_PATH.read_text(encoding="utf-8")
    loaded: object = yaml.safe_load(raw)
    return _require_mapping(loaded, label=str(CATALOG_PATH))


def catalog_entry_paths(catalog: Mapping[str, object]) -> set[str]:
    entries = _require_sequence(catalog.get("entries"), label="catalog.entries")
    paths: set[str] = set()
    for index, item in enumerate(entries):
        entry = _require_mapping(item, label=f"catalog.entries[{index}]")
        path = _require_str(entry.get("path"), label=f"catalog.entries[{index}].path")
        if not path or "/" in path or "\\" in path:
            raise AssertionError(
                f"catalog.entries[{index}].path must be a single top-level directory name (no slashes), got {path!r}"
            )
        if path in paths:
            raise AssertionError(f"duplicate catalog path: {path}")
        paths.add(path)
    return paths


def list_kernelone_toplevel_dirs(root: Path) -> set[str]:
    """Return top-level directory names under KernelOne. One level only."""
    if not root.is_dir():
        raise AssertionError(f"KernelOne root missing: {root}")
    names: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name in _SKIP_DIR_NAMES or name.startswith("."):
            continue
        names.add(name)
    return names


def _format_names(names: set[str]) -> str:
    return "\n".join(f"  - {name}" for name in sorted(names))


class TestKernelOneToplevelAdmission:
    """Fail-closed fence: catalog ↔ disk top-level directory set."""

    def test_catalog_exists_and_yaml_parses(self) -> None:
        catalog = load_catalog_document()
        assert catalog.get("kernelone_root") == "polaris/kernelone"
        assert catalog.get("generated_at") == "2026-08-10"
        assert _require_str(catalog.get("owner"), label="catalog.owner")
        assert catalog.get("version") is not None
        entries = _require_sequence(catalog.get("entries"), label="catalog.entries")
        assert entries, "catalog.entries must not be empty"

    def test_catalog_policy_forbids_unadmitted_toplevel(self) -> None:
        catalog = load_catalog_document()
        policy = _require_mapping(catalog.get("policy"), label="catalog.policy")
        assert policy.get("no_new_toplevel_subtree_without_adr_and_catalog") is True
        summary = _require_str(policy.get("summary"), label="catalog.policy.summary")
        admission = _require_str(policy.get("admission"), label="catalog.policy.admission")
        combined = f"{summary}\n{admission}"
        assert "ADR" in combined
        assert "catalog" in combined

    def test_overlap_warning_documents_cells_kernelone_dual_entry(self) -> None:
        catalog = load_catalog_document()
        warning = _require_mapping(catalog.get("overlap_warning"), label="catalog.overlap_warning")
        assert warning.get("id") == "cells_kernelone_dual_entry"
        assert warning.get("consumer_surface") == "polaris.cells.kernelone"
        assert warning.get("implementation_surface") == "polaris.kernelone"
        cells_paths = _require_sequence(warning.get("cells_paths"), label="catalog.overlap_warning.cells_paths")
        assert "polaris/cells/kernelone/core" in cells_paths
        assert "polaris/cells/kernelone/traceability" in cells_paths

    def test_catalog_entries_have_required_fields(self) -> None:
        catalog = load_catalog_document()
        entries = _require_sequence(catalog.get("entries"), label="catalog.entries")
        seen_ids: set[str] = set()
        for index, item in enumerate(entries):
            entry = _require_mapping(item, label=f"catalog.entries[{index}]")
            missing = [key for key in _REQUIRED_ENTRY_KEYS if key not in entry]
            assert not missing, f"catalog.entries[{index}] missing keys: {missing}"
            entry_id = _require_str(entry.get("id"), label=f"catalog.entries[{index}].id")
            assert entry_id, f"catalog.entries[{index}].id must be non-empty"
            assert entry_id not in seen_ids, f"duplicate catalog id: {entry_id}"
            seen_ids.add(entry_id)
            kind = _require_str(entry.get("kind"), label=f"catalog.entries[{index}].kind")
            assert kind in _ALLOWED_KINDS, f"catalog.entries[{index}].kind={kind!r} not in {sorted(_ALLOWED_KINDS)}"
            public_entry = _require_str(entry.get("public_entry"), label=f"catalog.entries[{index}].public_entry")
            if public_entry == "":
                notes = _require_str(entry.get("notes"), label=f"catalog.entries[{index}].notes")
                if kind != "test_root":
                    assert "needs_owner" in notes, (
                        f"catalog.entries[{index}] empty public_entry requires notes containing needs_owner"
                    )
            allowed = entry.get("allowed_new_files")
            assert isinstance(allowed, bool), f"catalog.entries[{index}].allowed_new_files must be a bool"
            notes = _require_str(entry.get("notes"), label=f"catalog.entries[{index}].notes")
            assert notes, f"catalog.entries[{index}].notes must be a non-empty line"
            assert "\n" not in notes, f"catalog.entries[{index}].notes must be a single line"

    def test_unregistered_disk_dir_is_admission_failure(self) -> None:
        catalog = load_catalog_document()
        catalog_paths = catalog_entry_paths(catalog)
        disk_dirs = list_kernelone_toplevel_dirs(KERNELONE_ROOT)
        extra = disk_dirs - catalog_paths
        if extra:
            pytest.fail(
                "Unregistered KernelOne top-level "
                f"{'directories' if len(extra) != 1 else 'directory'} detected "
                "(admission blocked):\n"
                f"{_format_names(extra)}\n\n"
                f"{_ADMISSION_HOW_TO}"
            )

    def test_catalog_entry_missing_on_disk_is_ghost_failure(self) -> None:
        catalog = load_catalog_document()
        catalog_paths = catalog_entry_paths(catalog)
        disk_dirs = list_kernelone_toplevel_dirs(KERNELONE_ROOT)
        missing = catalog_paths - disk_dirs
        if missing:
            pytest.fail(
                "Catalog entries have no matching KernelOne top-level directory "
                "(ghost / empty-head paths):\n"
                f"{_format_names(missing)}\n\n"
                "Remove the catalog row or restore the directory. "
                "The catalog must not list paths that do not exist on disk."
            )

    def test_disk_toplevel_directory_set_equals_catalog_paths(self) -> None:
        catalog = load_catalog_document()
        catalog_paths = catalog_entry_paths(catalog)
        disk_dirs = list_kernelone_toplevel_dirs(KERNELONE_ROOT)
        assert disk_dirs == catalog_paths, (
            "KernelOne top-level directory set must equal catalog entries.path.\n"
            f"only_on_disk:\n{_format_names(disk_dirs - catalog_paths)}\n"
            f"only_in_catalog:\n{_format_names(catalog_paths - disk_dirs)}"
        )

    def test_scan_is_single_level_and_ignores_files_and_pycache(self) -> None:
        """Admission compares directory names only; top-level modules are out of scope."""
        disk_dirs = list_kernelone_toplevel_dirs(KERNELONE_ROOT)
        assert "__pycache__" not in disk_dirs
        assert "__init__.py" not in disk_dirs
        for name in disk_dirs:
            assert (KERNELONE_ROOT / name).is_dir()
            assert "/" not in name
        top_level_files = {
            child.name for child in KERNELONE_ROOT.iterdir() if child.is_file() and child.suffix == ".py"
        }
        assert top_level_files, "expected KernelOne top-level .py modules (not catalogued)"
        catalog_paths = catalog_entry_paths(load_catalog_document())
        assert top_level_files.isdisjoint(catalog_paths)
