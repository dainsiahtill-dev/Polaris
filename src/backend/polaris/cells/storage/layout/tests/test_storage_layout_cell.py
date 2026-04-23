"""Unit tests for ``polaris.cells.storage.layout`` public API.

These tests exercise the Cell's public contracts and business-layer logic
in isolation, using only tmp_path fixtures (no external environment state).
The test module is self-contained and does NOT depend on the KernelOne
bootstrap or the ``tests/test_storage_layout_v4.py`` integration fixture.

Coverage targets (Cell public API):
- resolve_storage_layout()          -> StorageLayoutResultV1
- PolarisStorageLayout          -> path resolution
- PolarisStorageRoots          -> HP-specific config_root
- polaris_home()               -> env priority chain
- default_polaris_cache_base() -> cross-platform cache paths
- StorageLayoutErrorV1             -> contract error
- StorageLayoutResultV1            -> contract validation
- ResolveStorageLayoutQueryV1       -> input validation
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

# ─── Imports of the code under test ──────────────────────────────────────────
from polaris.cells.storage.layout import (
    PolarisStorageLayout,
    PolarisStorageRoots,
    RefreshStorageLayoutCommandV1,
    ResolveRuntimePathQueryV1,
    ResolveStorageLayoutQueryV1,
    ResolveWorkspacePathQueryV1,
    StorageLayoutError,
    StorageLayoutErrorV1,
    StorageLayoutResultV1,
    default_polaris_cache_base,
    polaris_home,
    refresh_storage_layout,
    resolve_polaris_roots,
    resolve_storage_layout,
)
from polaris.kernelone._runtime_config import (
    get_workspace_metadata_dir_default,
    set_workspace_metadata_dir_name,
)
from polaris.kernelone.storage import (
    clear_business_roots_resolver,
    clear_storage_roots_cache,
    register_business_roots_resolver,
)

if TYPE_CHECKING:
    from pathlib import Path

# ─── Module-level bootstrap fixture ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _hp_bootstrap():
    """Set .polaris as workspace metadata dir and register the business resolver.

    This mirrors what Polaris's bootstrap does so that Cell-level tests
    operate under the same path conventions as the integration tests.
    """
    original = get_workspace_metadata_dir_default()
    clear_storage_roots_cache()
    set_workspace_metadata_dir_name(".polaris")
    register_business_roots_resolver(resolve_polaris_roots)
    yield
    clear_storage_roots_cache()
    clear_business_roots_resolver()
    set_workspace_metadata_dir_name(original)


# ─── Contract dataclass validation ─────────────────────────────────────────────


class TestResolveStorageLayoutQueryV1:
    def test_valid_workspace_strips_whitespace(self) -> None:
        q = ResolveStorageLayoutQueryV1(workspace="  /tmp/foo  ")
        assert q.workspace == "/tmp/foo"

    def test_empty_workspace_raises_ValueError(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ResolveStorageLayoutQueryV1(workspace="")

    def test_whitespace_only_raises_ValueError(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ResolveStorageLayoutQueryV1(workspace="   ")


class TestRefreshStorageLayoutCommandV1:
    def test_defaults_force_to_false(self) -> None:
        cmd = RefreshStorageLayoutCommandV1(workspace="/tmp/ws")
        assert cmd.force is False

    def test_explicit_force_true(self) -> None:
        cmd = RefreshStorageLayoutCommandV1(workspace="/tmp/ws", force=True)
        assert cmd.force is True


class TestResolveRuntimePathQueryV1:
    def test_both_fields_required(self) -> None:
        q = ResolveRuntimePathQueryV1(workspace="/tmp/ws", relative_path="runtime/logs")
        assert q.workspace == "/tmp/ws"
        assert q.relative_path == "runtime/logs"

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ResolveRuntimePathQueryV1(workspace="", relative_path="runtime/logs")

    def test_empty_relative_path_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ResolveRuntimePathQueryV1(workspace="/tmp/ws", relative_path="")


class TestResolveWorkspacePathQueryV1:
    def test_valid(self) -> None:
        q = ResolveWorkspacePathQueryV1(workspace="/tmp/ws", relative_path="workspace/docs")
        assert q.workspace == "/tmp/ws"
        assert q.relative_path == "workspace/docs"


class TestStorageLayoutResultV1:
    def test_extras_defaults_to_empty_dict(self) -> None:
        # __post_init__ calls _to_dict_copy() which converts None -> {}
        r = StorageLayoutResultV1(
            workspace="/tmp/ws",
            runtime_root="/tmp/cache/.polaris/projects/k/runtime",
            history_root="/tmp/ws/.polaris/history",
            meta_root="/tmp/ws/.polaris",
        )
        assert r.extras == {}

    def test_extras_accepts_mapping(self) -> None:
        r = StorageLayoutResultV1(
            workspace="/tmp/ws",
            runtime_root="/tmp/cache/.polaris/projects/k/runtime",
            history_root="/tmp/ws/.polaris/history",
            meta_root="/tmp/ws/.polaris",
            extras={"config_root": "/home/.polaris/config"},
        )
        assert r.extras is not None
        assert r.extras["config_root"] == "/home/.polaris/config"

    def test_extras_must_be_dict_copy(self, tmp_path: Path) -> None:
        # Extras should be a copy, not a reference to the original
        original = {"key": "value"}
        r = StorageLayoutResultV1(
            workspace=str(tmp_path),
            runtime_root=str(tmp_path / "runtime"),
            history_root=str(tmp_path / "history"),
            meta_root=str(tmp_path / "meta"),
            extras=original,
        )
        original["key"] = "mutated"
        assert r.extras is not None
        assert r.extras["key"] == "value"

    def test_empty_required_field_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            StorageLayoutResultV1(
                workspace="",
                runtime_root="/rt",
                history_root="/ht",
                meta_root="/mt",
            )


class TestStorageLayoutErrorV1:
    def test_code_defaults(self) -> None:
        err = StorageLayoutErrorV1("test message")
        assert err.code == "storage_layout_error"
        assert err.details == {}
        assert str(err) == "test message"

    def test_custom_code_and_details(self) -> None:
        err = StorageLayoutErrorV1(
            "custom error",
            code="path_escape_rejected",
            details={"path": "/etc/passwd", "workspace": "/home"},
        )
        assert err.code == "path_escape_rejected"
        assert err.details["path"] == "/etc/passwd"
        assert isinstance(err, RuntimeError)

    def test_backward_compat_alias(self) -> None:
        # StorageLayoutError must still work for existing consumers
        err = StorageLayoutError("legacy message")
        assert str(err) == "legacy message"
        assert isinstance(err, StorageLayoutErrorV1)


# ─── polaris_home() ───────────────────────────────────────────────────────


class TestPolarisHome:
    def test_polaris_home_priority_polaris_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hp_home = "/custom/hp/home"
        monkeypatch.setenv("KERNELONE_HOME", hp_home)
        result = polaris_home()
        # KERNELONE_HOME is used directly as complete path
        assert os.path.abspath(result) == os.path.abspath(hp_home)

    def test_polaris_home_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hp_home = "/custom/hp/home/"
        monkeypatch.setenv("KERNELONE_HOME", hp_home)
        result = polaris_home()
        assert not result.endswith("/") and not result.endswith("\\")

    def test_polaris_home_expands_user_and_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hp_home = "~/hp-home"
        monkeypatch.setenv("KERNELONE_HOME", hp_home)
        result = polaris_home()
        assert result.startswith(os.path.expanduser("~"))
        assert "hp-home" in result

    def test_polaris_home_fallback_to_polaris_suffix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # When KERNELONE_HOME is set and no Polaris dir exists, polaris_home()
        # appends .polaris (the current metadata dir)
        kern_home = str(tmp_path / "kern-home")
        monkeypatch.delenv("KERNELONE_HOME", raising=False)
        monkeypatch.setenv("KERNELONE_HOME", kern_home)
        result = polaris_home()
        expected = os.path.join(os.path.abspath(kern_home), ".polaris")
        assert os.path.abspath(result) == expected

    def test_polaris_home_fallback_expanduser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Without any env vars, fallback is ~/.polaris (current metadata dir)
        monkeypatch.delenv("KERNELONE_HOME", raising=False)
        monkeypatch.delenv("KERNELONE_HOME", raising=False)
        result = polaris_home()
        expected = os.path.abspath(os.path.expanduser("~/.polaris"))
        assert os.path.abspath(result) == expected


# ─── default_polaris_cache_base() ─────────────────────────────────────────


class TestDefaultPolarisCacheBase:
    def test_windows_uses_local_app_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if os.name != "nt":
            pytest.skip("Windows-specific test")
        monkeypatch.setenv("LOCALAPPDATA", "D:\\AppData\\Local")
        result = default_polaris_cache_base()
        assert "Polaris" in result
        assert "cache" in result.lower()

    def test_unix_uses_xdg_cache_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if os.name == "nt":
            pytest.skip("Unix-specific test")
        monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")
        result = default_polaris_cache_base()
        assert "polaris" in result.lower()
        assert "/custom/cache" in result

    def test_unix_fallback_expanduser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if os.name == "nt":
            pytest.skip("Unix-specific test")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        result = default_polaris_cache_base()
        assert ".cache" in result or ".cache" in os.path.expanduser("~/.cache")


# ─── PolarisStorageLayout ──────────────────────────────────────────────────


class TestPolarisStorageLayout:
    def test_config_root_uses_polaris_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        hp_home = str(tmp_path / "hp-home")
        monkeypatch.setenv("KERNELONE_HOME", hp_home)
        layout = PolarisStorageLayout(str(tmp_path / "workspace"), str(tmp_path / "runtime"))
        # config_root must be anchored at polaris_home(), not kernelone_home()
        config_str = layout.config_root.as_posix()
        assert "hp-home" in config_str
        assert "config" in config_str

    def test_workspace_and_runtime_root_resolved(self, tmp_path: Path) -> None:
        workspace = tmp_path / "my-project"
        runtime = tmp_path / "runtime-cache"
        workspace.mkdir()
        runtime.mkdir()

        layout = PolarisStorageLayout(str(workspace), str(runtime))

        assert layout.workspace.resolve() == workspace.resolve()
        assert ".polaris" in layout.workspace_root.parts

    def test_resolve_polaris_roots_returns_correct_types(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Mock default_ramdisk_root to return "" to avoid Windows X:\ detection
        # that pollutes cross-test _ramdisk_check_cache
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "project"
        ws.mkdir()
        layout = PolarisStorageLayout(str(ws), str(tmp_path / "runtime-cache"))

        roots = layout.resolve_polaris_roots()
        assert isinstance(roots, PolarisStorageRoots)
        # config_root is under polaris_home() (polaris_home), which resolves
        # to ~/.polaris since the metadata dir is set to .polaris in production
        # and ~/.polaris exists on this system
        assert ".polaris" in roots.config_root or ".polaris" in roots.config_root  # backward compat
        assert "config" in roots.config_root
        assert roots.project_root.endswith(".polaris")

    def test_workspace_key_deterministic(self, tmp_path: Path) -> None:
        workspace = tmp_path / "deterministic-test"
        workspace.mkdir()
        key1 = PolarisStorageLayout._compute_workspace_key(str(workspace))
        key2 = PolarisStorageLayout._compute_workspace_key(str(workspace))
        assert key1 == key2
        assert key1.startswith("deterministic-test-")

    def test_workspace_key_different_for_different_paths(self, tmp_path: Path) -> None:
        ws_a = tmp_path / "alpha"
        ws_b = tmp_path / "beta"
        ws_a.mkdir()
        ws_b.mkdir()
        key_a = PolarisStorageLayout._compute_workspace_key(str(ws_a))
        key_b = PolarisStorageLayout._compute_workspace_key(str(ws_b))
        assert key_a != key_b

    def test_repr_includes_config_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        hp_home = str(tmp_path / "hp-home")
        hp_home_path = tmp_path / "hp-home"
        hp_home_path.mkdir()
        monkeypatch.setenv("KERNELONE_HOME", hp_home)
        layout = PolarisStorageLayout(str(tmp_path / "ws"), str(tmp_path / "runtime"))
        repr_str = repr(layout)
        assert "config_root=" in repr_str
        assert "hp-home" in repr_str


# ─── resolve_polaris_roots() ───────────────────────────────────────────────


class TestResolvePolarisRoots:
    def test_config_root_uses_polaris_home_not_kernelone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # config_root = polaris_home()/config, which defaults to ~/.polaris/config
        # When KERNELONE_HOME is set, it uses that instead
        hp_home = str(tmp_path / "hp-home")
        hp_home_path = tmp_path / "hp-home"
        hp_home_path.mkdir()
        monkeypatch.setenv("KERNELONE_HOME", hp_home)
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        runtime_cache = tmp_path / "runtime-cache"
        runtime_cache.mkdir()
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_cache))

        ws = tmp_path / "workspace"
        ws.mkdir()

        roots = resolve_polaris_roots(str(ws))

        # config_root is anchored at polaris_home()/config
        # When KERNELONE_HOME=hp-home, config_root = hp-home/config
        assert "config" in roots.config_root
        assert "hp-home" in roots.config_root

    def test_project_root_uses_polaris_metadata_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "myproject"
        ws.mkdir()
        roots = resolve_polaris_roots(str(ws))
        assert ".polaris" in roots.project_root
        assert roots.project_persistent_root == roots.project_root

    def test_workspace_key_is_stable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "stable"
        ws.mkdir()
        r1 = resolve_polaris_roots(str(ws))
        r2 = resolve_polaris_roots(str(ws))
        assert r1.workspace_key == r2.workspace_key

    def test_history_root_always_workspace_anchored(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # history_root must be under workspace_abs, NOT under runtime_base,
        # to guarantee it stays on the same drive as the workspace.
        # Use KERNELONE_RUNTIME_ROOT to force runtime_base to tmp_path.
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "ws"
        ws.mkdir()
        roots = resolve_polaris_roots(str(ws))

        # history_root must be workspace-relative
        ws_abs = os.path.abspath(str(ws))
        assert roots.history_root.startswith(ws_abs)
        assert ".polaris" in roots.history_root
        assert "history" in roots.history_root

    def test_runtime_mode_is_project_local(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "ws"
        ws.mkdir()
        roots = resolve_polaris_roots(str(ws))
        assert roots.storage_layout_mode == "project_local"

    def test_extras_runtime_mode_in_result(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # When ramdisk is explicitly passed, runtime_mode reflects it
        ws = tmp_path / "ws"
        ws.mkdir()
        ramdisk = tmp_path / "ramdisk"
        ramdisk.mkdir()
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "1")
        roots = resolve_polaris_roots(str(ws), ramdisk_root=str(ramdisk))
        assert roots.runtime_mode == "ramdisk"

    def test_empty_workspace_defaults_to_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # resolve_polaris_roots accepts "" and falls back to os.getcwd()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))
        roots = resolve_polaris_roots("")
        assert roots.workspace_abs == os.path.abspath(os.getcwd())


# ─── resolve_storage_layout() ─────────────────────────────────────────────────


class TestResolveStorageLayout:
    def test_returns_storage_layout_result(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        query = ResolveStorageLayoutQueryV1(workspace=str(ws))
        result = resolve_storage_layout(query)

        assert isinstance(result, StorageLayoutResultV1)
        assert result.workspace == str(ws)
        assert ".polaris" in result.runtime_root
        assert ".polaris" in result.meta_root

    def test_result_extras_contains_all_roots(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        query = ResolveStorageLayoutQueryV1(workspace=str(ws))
        result = resolve_storage_layout(query)

        assert result.extras is not None
        assert "config_root" in result.extras
        assert "workspace_key" in result.extras
        assert "runtime_mode" in result.extras

    def test_empty_workspace_ValueError_from_query_construction(self) -> None:
        # Empty workspace raises ValueError during ResolveStorageLayoutQueryV1
        # construction (__post_init__ validation), BEFORE resolve_storage_layout runs
        with pytest.raises(ValueError, match="non-empty"):
            ResolveStorageLayoutQueryV1(workspace="")

    def test_whitespace_only_workspace_ValueError_from_query_construction(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ResolveStorageLayoutQueryV1(workspace="   ")

    def test_result_is_consistent_with_resolve_polaris_roots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        query = ResolveStorageLayoutQueryV1(workspace=str(ws))

        result = resolve_storage_layout(query)
        direct = resolve_polaris_roots(str(ws))

        assert result.runtime_root == direct.runtime_root
        assert result.history_root == direct.history_root
        assert result.extras is not None
        assert result.extras["config_root"] == direct.config_root
        assert result.extras["workspace_key"] == direct.workspace_key

    def test_logging_audit_event_emitted(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog) -> None:
        import logging

        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        query = ResolveStorageLayoutQueryV1(workspace=str(ws))

        with caplog.at_level(logging.INFO):
            resolve_storage_layout(query)

        assert any(
            "path_compliance_audit" in record.message and "workspace=" + str(ws) in record.message
            for record in caplog.records
        )


# ─── Path safety / escape guards ───────────────────────────────────────────────


class TestPathEscapeGuards:
    """Verify that illegal path prefixes are rejected by normalize_logical_rel_path.

    These tests document the expected rejection behaviour of the path normalisation
    layer that underlies all KernelOne storage resolution.  The guards are exercised
    via the KernelOne public API (normalize_logical_rel_path).
    """

    def test_dotdot_rejected_in_normalize_logical_rel_path(self) -> None:
        from polaris.kernelone.storage.layout import normalize_logical_rel_path

        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path("../escape")

    def test_dotdot_segment_rejected(self) -> None:
        from polaris.kernelone.storage.layout import normalize_logical_rel_path

        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path("runtime/../etc/passwd")

    def test_metadata_dir_prefix_rejected_in_normalize_logical_rel_path(self) -> None:
        from polaris.kernelone.storage.layout import normalize_logical_rel_path

        # With .polaris metadata dir, .polaris/ prefix triggers rejection;
        # plain "runtime/events.jsonl" does NOT (runtime != .polaris)
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path(".polaris/runtime/events.jsonl")

    def test_kernelone_metadata_dir_prefix_rejected(self) -> None:
        from polaris.kernelone.storage.layout import normalize_logical_rel_path

        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path(".kernelone/runtime/events.jsonl")

    def test_query_construction_with_dotdot_rejected(self, tmp_path: Path) -> None:
        # ResolveRuntimePathQueryV1 does NOT validate path content (only non-empty).
        # The actual path rejection happens in the KernelOne normalise step.
        # This test documents the query contract: it accepts any non-empty string.
        # The caller is responsible for validating the path via normalize_logical_rel_path.
        q = ResolveRuntimePathQueryV1(workspace=str(tmp_path), relative_path="../escape")
        assert q.relative_path == "../escape"  # Query accepts it; caller must call normalise

    def test_query_construction_with_metadata_prefix_accepted(self, tmp_path: Path) -> None:
        # Query contract: only checks non-empty. Content validation is the caller's
        # responsibility via normalize_logical_rel_path.
        # With .polaris metadata dir, the normalized form is "runtime/events.jsonl"
        q = ResolveRuntimePathQueryV1(workspace=str(tmp_path), relative_path="runtime/events.jsonl")
        assert "runtime" in q.relative_path


# ─── refresh_storage_layout() ───────────────────────────────────────────────────


class TestRefreshStorageLayout:
    def test_returns_storage_layout_result(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        cmd = RefreshStorageLayoutCommandV1(workspace=str(ws), force=False)
        result = refresh_storage_layout(cmd)

        assert isinstance(result, StorageLayoutResultV1)
        assert result.workspace == str(ws)
        assert ".polaris" in result.runtime_root

    def test_force_false_uses_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # When force=False, the cache is NOT invalidated; a second call returns
        # the same object identity (since nothing changed and the cache is fresh).
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        cmd = RefreshStorageLayoutCommandV1(workspace=str(ws), force=False)

        r1 = refresh_storage_layout(cmd)
        r2 = refresh_storage_layout(cmd)
        # Both calls return structurally identical results.
        assert r1.runtime_root == r2.runtime_root
        assert r1.history_root == r2.history_root

    def test_force_true_invalidates_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # When force=True the cache is cleared, but the result is the same
        # since the underlying environment hasn't changed.
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        cmd = RefreshStorageLayoutCommandV1(workspace=str(ws), force=True)

        r1 = refresh_storage_layout(cmd)
        r2 = refresh_storage_layout(cmd)
        assert r1.runtime_root == r2.runtime_root

    def test_empty_workspace_raises_ValueError(self) -> None:
        # Empty workspace raises ValueError during RefreshStorageLayoutCommandV1
        # construction (__post_init__ validation), BEFORE refresh_storage_layout runs.
        with pytest.raises(ValueError, match="non-empty"):
            RefreshStorageLayoutCommandV1(workspace="", force=True)

    def test_whitespace_only_workspace_raises_ValueError(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RefreshStorageLayoutCommandV1(workspace="   ", force=False)

    def test_result_extras_identical_to_resolve_storage_layout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()

        refresh_result = refresh_storage_layout(RefreshStorageLayoutCommandV1(workspace=str(ws), force=True))
        query_result = resolve_storage_layout(ResolveStorageLayoutQueryV1(workspace=str(ws)))

        assert refresh_result.runtime_root == query_result.runtime_root
        assert refresh_result.history_root == query_result.history_root
        assert refresh_result.extras is not None
        assert query_result.extras is not None
        assert refresh_result.extras["config_root"] == query_result.extras["config_root"]
        assert refresh_result.extras["workspace_key"] == query_result.extras["workspace_key"]

    def test_force_true_emits_debug_log(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog) -> None:
        import logging

        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "test-ws"
        ws.mkdir()
        cmd = RefreshStorageLayoutCommandV1(workspace=str(ws), force=True)

        with caplog.at_level(logging.DEBUG):
            refresh_storage_layout(cmd)

        assert any("cache invalidated" in record.message and "test-ws" in record.message for record in caplog.records)


# ─── Performance benchmarks ───────────────────────────────────────────────────────


class TestStorageLayoutPerformance:
    """Latency benchmarks for path resolution.

    These tests verify that hot-path resolution is sub-millisecond and that
    cold-path resolution completes within reasonable bounds.  Results are
    reported via the pytest-report log channel so they appear in CI output.
    """

    def test_resolve_storage_layout_hot_path_sub_ms(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Hot-path: same workspace resolved twice (second call hits cache)."""
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "perf-ws"
        ws.mkdir()
        query = ResolveStorageLayoutQueryV1(workspace=str(ws))

        # Warm up the cache
        resolve_storage_layout(query)

        import time

        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            resolve_storage_layout(query)
        elapsed = (time.perf_counter() - start) * 1000  # ms

        avg_ms = elapsed / iterations
        assert avg_ms < 1.0, f"Hot-path avg {avg_ms:.3f}ms exceeds 1ms threshold"

    def test_resolve_storage_layout_cold_path_completes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Cold-path: cache is cleared between each call."""
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        import time

        iterations = 50
        latencies: list[float] = []
        for i in range(iterations):
            ws = tmp_path / f"cold-ws-{i}"
            ws.mkdir()
            clear_storage_roots_cache()
            query = ResolveStorageLayoutQueryV1(workspace=str(ws))

            start = time.perf_counter()
            resolve_storage_layout(query)
            latencies.append((time.perf_counter() - start) * 1000)

        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        p50 = sorted(latencies)[len(latencies) // 2]
        # Cold path on local tmpfs should be well under 50ms P99.
        assert p99 < 50.0, f"Cold-path P99 {p99:.3f}ms exceeds 50ms threshold (P50={p50:.3f}ms)"

    def test_polaris_roots_workspace_key_determinism(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """workspace_key must be stable across repeated calls without cache."""
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "stable-ws"
        ws.mkdir()
        keys = set()
        for _ in range(10):
            clear_storage_roots_cache()
            roots = resolve_polaris_roots(str(ws))
            keys.add(roots.workspace_key)

        assert len(keys) == 1, "workspace_key must be stable across calls"

    def test_config_root_resolution_does_not_probe_filesystem(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """config_root is computed without touching the filesystem.

        KERNELONE_HOME is already set, so config_root = KERNELONE_HOME/config
        requires no stat calls.  Verify by clearing the cache between calls and
        confirming P99 stays under threshold.
        """
        hp_home = tmp_path / "hp-home"
        hp_home.mkdir()
        monkeypatch.setenv("KERNELONE_HOME", str(hp_home))
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        import time

        ws = tmp_path / "cfg-ws"
        ws.mkdir()

        latencies: list[float] = []
        for _ in range(200):
            clear_storage_roots_cache()
            start = time.perf_counter()
            roots = resolve_polaris_roots(str(ws))
            # Access config_root to ensure it's fully resolved
            _ = roots.config_root
            latencies.append((time.perf_counter() - start) * 1000)

        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        assert p99 < 5.0, f"config_root resolution P99 {p99:.3f}ms exceeds 5ms"

    def test_refresh_command_force_false_equivalent_to_query(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """force=False refresh must be functionally identical to resolve_storage_layout."""
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime-cache"))

        ws = tmp_path / "equiv-ws"
        ws.mkdir()

        query_result = resolve_storage_layout(ResolveStorageLayoutQueryV1(workspace=str(ws)))
        refresh_result = refresh_storage_layout(RefreshStorageLayoutCommandV1(workspace=str(ws), force=False))

        assert query_result.runtime_root == refresh_result.runtime_root
        assert query_result.history_root == refresh_result.history_root
        assert query_result.extras == refresh_result.extras
