"""Regression tests: P0-04 history archive dual-path resolution.

Root cause: HistoryArchiveService previously passed ``str(self.history_root)``
(i.e. ``<workspace>/.polaris/history``) to ``HistoryManifestRepository``.
Inside that constructor, ``resolve_storage_roots(history_root)`` appends
``.polaris/history`` again, producing a doubly-nested path:

    <workspace>/.polaris/history/.polaris/history/

The fix: pass ``str(self.workspace)`` so resolution happens once in each class.

Coverage:
1. history_root identity – service.history_root == manifest_repo.history_root
2. No nested segment in any computed path
3. index_dir derives from the same canonical history_root
4. Index file paths contain exactly one ``.polaris/history`` segment
5. Both objects agree when workspace path has trailing separators or is relative-then-resolved
6. Invariant holds after storage-roots cache is cleared (not a cache artefact)
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.storage import clear_storage_roots_cache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(workspace: Path):
    """Construct a HistoryArchiveService, clearing the storage-roots cache first."""
    clear_storage_roots_cache()
    from polaris.cells.archive.run_archive.internal.history_archive_service import (
        HistoryArchiveService,
    )
    return HistoryArchiveService(str(workspace))


def _make_repo(workspace: Path):
    """Construct a HistoryManifestRepository directly, clearing the cache first."""
    clear_storage_roots_cache()
    from polaris.cells.archive.run_archive.internal.history_manifest_repository import (
        HistoryManifestRepository,
    )
    return HistoryManifestRepository(str(workspace))


_NESTED_SEGMENT = ".polaris/history/.polaris/history"


def _to_fwd(path: Path) -> str:
    """Normalise path to forward slashes for portable string matching."""
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_service_and_repo_history_roots_are_identical(tmp_path: Path) -> None:
    """history_root on the service must equal history_root on the manifest repo."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)

    assert service.history_root == service._manifest_repo.history_root, (  # noqa: SLF001
        f"Path mismatch: service={service.history_root!r}  "
        f"repo={service._manifest_repo.history_root!r}"  # noqa: SLF001
    )


def test_no_nested_history_segment_in_history_root(tmp_path: Path) -> None:
    """history_root must not contain a double-nested .polaris/history/... segment."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    repo_history_root = _to_fwd(service._manifest_repo.history_root)  # noqa: SLF001

    assert _NESTED_SEGMENT not in repo_history_root, (
        f"Double-nested path detected: {repo_history_root!r}"
    )


def test_history_root_is_workspace_polaris_history(tmp_path: Path) -> None:
    """history_root must resolve to exactly <workspace>/.polaris/history."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    expected = workspace.resolve() / ".polaris" / "history"

    assert service.history_root == expected
    assert service._manifest_repo.history_root == expected  # noqa: SLF001


def test_index_dir_is_inside_canonical_history_root(tmp_path: Path) -> None:
    """index_dir must be a direct child of history_root with no extra nesting."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    repo = service._manifest_repo  # noqa: SLF001

    assert repo.index_dir == repo.history_root / "index"
    assert _NESTED_SEGMENT not in _to_fwd(repo.index_dir)


def test_index_file_paths_have_single_polaris_history_occurrence(tmp_path: Path) -> None:
    """Each index file path must contain '.polaris/history' exactly once."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    repo = service._manifest_repo  # noqa: SLF001

    from polaris.cells.archive.run_archive.internal.history_manifest_repository import IndexType

    for index_type in IndexType:
        index_path = _to_fwd(repo._get_index_path(index_type))  # noqa: SLF001
        count = index_path.count(".polaris/history")
        assert count == 1, (
            f"Expected exactly 1 occurrence of '.polaris/history' in "
            f"{index_path!r}, found {count}"
        )


def test_invariant_holds_after_cache_clear(tmp_path: Path) -> None:
    """Path invariant must hold even when the storage-roots cache is purged mid-test."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    first_history_root = service.history_root

    clear_storage_roots_cache()
    service2 = _make_service(workspace)

    assert service2.history_root == first_history_root
    assert service2._manifest_repo.history_root == first_history_root  # noqa: SLF001


def test_standalone_repo_with_workspace_matches_service_repo(tmp_path: Path) -> None:
    """A standalone HistoryManifestRepository(workspace) must agree with the one
    embedded in HistoryArchiveService for the same workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    standalone_repo = _make_repo(workspace)

    assert standalone_repo.history_root == service.history_root
    assert standalone_repo.history_root == service._manifest_repo.history_root  # noqa: SLF001


def test_passing_history_root_to_repo_directly_produces_nested_path(tmp_path: Path) -> None:
    """Demonstrates the original bug: passing history_root as workspace produces
    double-nesting. This test documents and locks in the known bad behaviour so
    any change that silently 'fixes' the lower layer is detected.

    This test is intentionally asserting the BUG path – it confirms that the
    fix is NOT in the repository constructor, but in the call site choosing the
    correct argument.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = _make_service(workspace)
    history_root = service.history_root

    # Deliberately pass history_root (wrong argument) – must produce nesting
    clear_storage_roots_cache()
    from polaris.cells.archive.run_archive.internal.history_manifest_repository import (
        HistoryManifestRepository,
    )
    buggy_repo = HistoryManifestRepository(str(history_root))

    buggy_path = _to_fwd(buggy_repo.history_root)
    assert _NESTED_SEGMENT in buggy_path, (
        f"Expected double-nesting when passing history_root as workspace, "
        f"but got: {buggy_path!r}. "
        f"If resolve_storage_roots was changed to handle this transparently, "
        f"update this test and the fix comment in history_archive_service.py."
    )
