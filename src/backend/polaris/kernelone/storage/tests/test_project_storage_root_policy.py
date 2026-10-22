"""Project writers stay local; historical readers never initialize storage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterator

import pytest
from polaris.kernelone.storage import layout


@pytest.fixture(autouse=True)
def isolated_roots(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("RUNTIME_ROOT", "RUNTIME_CACHE_ROOT", "RAMDISK_ROOT"):
        monkeypatch.delenv(f"KERNELONE_{name}", raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setattr(layout, "_business_roots_resolver", None)
    layout.clear_storage_roots_cache()
    yield
    layout.clear_storage_roots_cache()


def tree(root: Path) -> tuple[str, ...]:
    return tuple(sorted(str(path.relative_to(root)) for path in root.rglob("*")))


@pytest.mark.parametrize("setting", ["RUNTIME_ROOT", "RUNTIME_CACHE_ROOT", "RAMDISK_ROOT"])
def test_external_writer_refused_before_filesystem_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv(f"KERNELONE_{setting}", str(tmp_path / "outside"))
    if setting == "RAMDISK_ROOT":
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
    before = tree(tmp_path)

    with pytest.raises(RuntimeError, match=r"canonical|project-local"):
        layout.resolve_storage_roots(str(workspace))

    assert tree(tmp_path) == before


@pytest.mark.parametrize("explicit", ["default", "canonical", "legacy"])
def test_cold_writer_progresses_without_resolver_recursion_or_probe_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: str
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    if explicit != "default":
        selected = workspace / ".polaris" / "runtime" if explicit == "canonical" else workspace / "runtime"
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(selected))

    roots = layout.resolve_storage_roots(str(workspace))

    assert roots.runtime_root == str(workspace / ".polaris" / "runtime")
    assert tree(workspace) == (".polaris", ".polaris/runtime")
    assert layout.resolve_storage_roots(str(workspace)) == roots


def test_provisioning_uses_guarded_kfs_not_raw_layout_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()

    def forbidden_raw_open(*args: object, **kwargs: object) -> None:
        pytest.fail("storage.layout bypassed KFS for its write probe")

    monkeypatch.setattr(layout, "open", forbidden_raw_open, raising=False)
    roots = layout.resolve_storage_roots(str(workspace))

    assert Path(roots.runtime_root).is_dir()
    assert tree(workspace) == (".polaris", ".polaris/runtime")


def test_cold_writer_can_prepare_a_workspace_that_does_not_exist(tmp_path: Path) -> None:
    workspace = tmp_path / "new-parent" / "project"

    roots = layout.resolve_storage_roots(str(workspace))

    assert roots.runtime_root == str(workspace / ".polaris" / "runtime")
    assert tree(workspace) == (".polaris", ".polaris/runtime")


def test_symlinked_metadata_refused_without_outside_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".polaris").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError):
        layout.resolve_storage_roots(str(workspace))

    assert tree(outside) == ()


def test_cached_external_writer_cannot_bypass_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    old = layout._build_generic_storage_roots(
        workspace_abs=str(workspace), runtime_base=str(tmp_path / "outside"), runtime_mode="system_cache"
    )
    layout._set_cached_storage_roots(str(workspace), None, old)
    before = tree(tmp_path)

    with pytest.raises(RuntimeError, match=r"canonical|project-local"):
        layout.resolve_storage_roots(str(workspace))

    assert tree(tmp_path) == before


@pytest.mark.parametrize(
    "wrong_field", ["runtime_base", "runtime_root", "runtime_project_root", "runtime_projects_root", "workspace_abs"]
)
def test_registered_business_roots_cannot_bypass_policy_or_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong_field: str
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    canonical = layout._build_generic_storage_roots(
        workspace_abs=str(workspace), runtime_base=str(workspace / ".polaris" / "runtime"), runtime_mode="project_local"
    )
    invalid = replace(canonical, **{wrong_field: str(tmp_path / "outside")})
    monkeypatch.setattr(layout, "_business_roots_resolver", lambda *_: invalid)
    before = tree(tmp_path)

    with pytest.raises(RuntimeError, match=r"canonical|project-local"):
        layout.resolve_storage_roots(str(workspace))

    assert tree(tmp_path) == before


def test_registered_business_failure_is_not_silently_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()

    def unavailable(*args: object) -> layout.StorageRoots:
        raise RuntimeError("business namespace unavailable")

    monkeypatch.setattr(layout, "_business_roots_resolver", unavailable)
    with pytest.raises(RuntimeError, match="business namespace unavailable"):
        layout.resolve_storage_roots(str(workspace))
    assert tree(workspace) == ()


@pytest.mark.parametrize("setting", ["RUNTIME_ROOT", "RUNTIME_CACHE_ROOT", "RAMDISK_ROOT"])
def test_cold_current_reader_ignores_external_writer_settings_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv(f"KERNELONE_{setting}", str(tmp_path / "outside"))
    if setting == "RAMDISK_ROOT":
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
    before = tree(tmp_path)
    cache_before = dict(layout._storage_roots_cache)

    roots = layout.resolve_storage_roots_read_only(str(workspace))
    existing = layout.resolve_existing_storage_roots_read_only(str(workspace))

    assert roots.runtime_root == str(workspace / ".polaris" / "runtime")
    assert existing is None
    assert tree(tmp_path) == before
    assert layout._storage_roots_cache == cache_before


def test_current_reader_does_not_adopt_cached_history(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    old = layout._build_generic_storage_roots(
        workspace_abs=str(workspace), runtime_base=str(tmp_path / "outside"), runtime_mode="system_cache"
    )
    layout._set_cached_storage_roots(str(workspace), None, old)
    cache_before = dict(layout._storage_roots_cache)

    current = layout.resolve_storage_roots_read_only(str(workspace))

    assert current.runtime_root == str(workspace / ".polaris" / "runtime")
    assert layout.resolve_existing_storage_roots_read_only(str(workspace)) is None
    assert layout._storage_roots_cache == cache_before
    assert tree(workspace) == ()


@pytest.mark.parametrize("history_kind", ["explicit", "cache", "ramdisk", "system", "bare"])
def test_historical_namespace_remains_readable_without_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, history_kind: str
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    external = tmp_path / "archive"
    if history_kind == "bare":
        historical = workspace / "runtime"
    else:
        # The audit's physical ancestor already contains `.polaris`; the
        # historical base format therefore does not insert it a second time.
        historical = external / "projects" / layout.workspace_key(str(workspace)) / "runtime"
        if history_kind == "system":
            monkeypatch.setattr(layout, "default_kernelone_cache_base", lambda: str(external))
        else:
            setting = {"explicit": "RUNTIME_ROOT", "cache": "RUNTIME_CACHE_ROOT", "ramdisk": "RAMDISK_ROOT"}[
                history_kind
            ]
            monkeypatch.setenv(f"KERNELONE_{setting}", str(external))
            if history_kind == "ramdisk":
                monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
    historical.mkdir(parents=True)
    (historical / "evidence.txt").write_text("历史证据", encoding="utf-8")
    before = tree(tmp_path)

    roots = layout.resolve_existing_storage_roots_read_only(str(workspace))

    assert roots is not None
    assert Path(roots.runtime_root) == historical
    assert (Path(roots.runtime_root) / "evidence.txt").read_text(encoding="utf-8") == "历史证据"
    assert tree(tmp_path) == before
    assert not (workspace / ".polaris").exists()
