"""Scope and containment contract for KernelOne skill installation.

These tests pin the mechanism that closed EXECUTOR-IMPLICIT-SKILLS-IO:

* constructing a tool executor must not create any skill file;
* an explicit installation is the only writer, and it writes exactly one
  canonical workspace tree;
* a skills root that escapes the workspace is refused *before* any effect;
* the builtin skill catalogue stays usable on a fresh workspace, so a
  read-only loader never needs a disk write to be useful.

Every text read and write in this file is explicit UTF-8.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs.contracts import NamespaceOperationUnavailableError
from polaris.kernelone.fs.guarded_namespace_ops import NamespaceBoundaryError
from polaris.kernelone.fs.types import NamespaceBindingV1, NamespaceCreateReceiptV1
from polaris.kernelone.single_agent.skill_system import (
    DEFAULT_SKILLS,
    SkillLoader,
    SkillRootSecurityError,
    SkillWorkspaceMissingError,
    install_default_skills,
)
from polaris.kernelone.storage import clear_storage_roots_cache


def _relative_files(root: Path) -> list[str]:
    """Return every file below ``root`` as a sorted relative path list."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def _skill_files(root: Path) -> list[str]:
    """Return every installed skill file below ``root``."""
    return sorted(path for path in _relative_files(root) if path.endswith(".md"))


def _tree(root: Path) -> list[str]:
    """Return every entry below ``root`` as a sorted relative path list."""
    if not root.exists():
        return []
    return sorted(str(item.relative_to(root)) for item in root.rglob("*"))


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated workspace with its own canonical runtime root."""
    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    clear_storage_roots_cache()
    return workspace


def _register_adapter(adapter: Any) -> None:
    """Publish ``adapter`` as the default KernelOne filesystem adapter.

    The registry is the real production registry: the adapter is published
    with ``set_default_adapter`` exactly as a host would publish it, so the
    installer resolves it through the ordinary lookup path.
    """
    from polaris.kernelone.fs import set_default_adapter

    set_default_adapter(adapter)


@pytest.fixture
def registered_adapter():
    """Publish a registered adapter and restore the previous one afterwards."""
    from polaris.kernelone.fs import get_default_adapter, set_default_adapter

    previous = get_default_adapter()
    try:
        yield lambda adapter: set_default_adapter(adapter)
    finally:
        set_default_adapter(previous)


class _DenyingAdapter(LocalFileSystemAdapter):
    """A registered adapter whose policy denies every managed write."""

    def __init__(self) -> None:
        self.legacy_write_calls: list[str] = []

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        self.legacy_write_calls.append(path)
        raise PermissionError("synthetic registered adapter write policy denial")

    def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> int:
        self.legacy_write_calls.append(path)
        raise PermissionError("synthetic registered adapter write policy denial")


class _PermittingAdapter(LocalFileSystemAdapter):
    """A registered adapter that keeps the production create capability."""


class _CommitThenFailAdapter(LocalFileSystemAdapter):
    """An adapter whose create lands and then reports a failure."""

    def __init__(self) -> None:
        self.create_calls = 0

    def create_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        max_bytes: int,
    ) -> NamespaceCreateReceiptV1:
        self.create_calls += 1
        LocalFileSystemAdapter.create_managed_namespace_bytes(self, binding, content, max_bytes=max_bytes)
        raise OSError("synthetic failure after the creation landed")


class TestExecutorConstructionHasNoSkillEffect:
    """Constructing the executor must not materialise any skill file."""

    def test_empty_tool_executor_creates_zero_skill_files(self, tmp_path: Path) -> None:
        """An executor with an empty allowlist creates no skill file at all."""
        from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

        workspace = tmp_path / "ws"
        workspace.mkdir()

        AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset())

        assert _skill_files(workspace) == []
        assert _relative_files(workspace) == []

    def test_executor_construction_leaves_no_skills_tree(self, tmp_path: Path) -> None:
        """The executor does not create the workspace skills tree either."""
        from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

        workspace = tmp_path / "ws"
        workspace.mkdir()

        AgentAccelToolExecutor(str(workspace))

        assert not (workspace / ".polaris" / "skills").exists()
        assert not (workspace / ".polaris" / "runtime" / "skills").exists()


class TestExplicitInstallationIsWorkspaceContained:
    """An explicit installation writes one canonical tree inside the workspace."""

    def test_explicit_install_writes_only_the_canonical_root(self, tmp_path: Path) -> None:
        """The installer writes the canonical skills root and nothing else."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        installed = install_default_skills(str(workspace), explicit=True)

        assert sorted(installed) == sorted(DEFAULT_SKILLS)
        assert _skill_files(workspace) == sorted(f".polaris/skills/{name}.md" for name in DEFAULT_SKILLS)
        # The retired duplicate runtime tree is never written again.
        assert not (workspace / ".polaris" / "runtime" / "skills").exists()

    def test_cold_loader_reads_the_installed_skills(self, tmp_path: Path) -> None:
        """A cold loader discovers the freshly installed skills."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        install_default_skills(str(workspace), explicit=True)
        loader = SkillLoader(str(workspace))

        assert sorted(item["name"] for item in loader.list_skills()) == sorted(DEFAULT_SKILLS)
        assert "Security" in loader.load_skill_content("security-review")

    def test_install_is_idempotent_and_preserves_user_content(self, tmp_path: Path) -> None:
        """A second installation reports nothing and keeps user-authored content."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        install_default_skills(str(workspace), explicit=True)
        user_skill = workspace / ".polaris" / "skills" / "security-review.md"
        user_skill.write_text(
            "---\ndescription: USER AUTHORED\n---\n\n# mine\n",
            encoding="utf-8",
        )

        second = install_default_skills(str(workspace), explicit=True)

        assert second == []
        assert "USER AUTHORED" in user_skill.read_text(encoding="utf-8")
        assert len(_skill_files(workspace)) == len(DEFAULT_SKILLS)

    def test_install_into_a_missing_workspace_is_reported_honestly(self, tmp_path: Path) -> None:
        """A missing workspace root is refused, never created out of band.

        Contract migration: the previous candidate materialised the
        workspace root with a raw ``guarded_create_directory`` anchored at
        ``/``.  That bypassed the registered adapter, so it is retired.  The
        managed create capability provisions the *intermediate* directories of
        a leaf but retains the namespace root, so the unsupported edge is
        now reported instead of bypassed.
        """
        workspace = tmp_path / "not-yet"

        with pytest.raises(SkillWorkspaceMissingError):
            install_default_skills(str(workspace), explicit=True)

        # Nothing was created, neither inside nor next to the workspace.
        assert not workspace.exists()
        assert sorted(path.name for path in tmp_path.iterdir()) == []

    def test_install_without_explicit_flag_is_refused(self, tmp_path: Path) -> None:
        """The explicit flag remains mandatory."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        with pytest.raises(RuntimeError, match="explicit=True"):
            install_default_skills(str(workspace))

        assert _relative_files(workspace) == []


class TestEscapingRootsAreRefused:
    """A skills root that leaves the workspace is refused before any effect."""

    @pytest.mark.parametrize(
        "root",
        [
            "../escape",
            ".polaris/../../escape",
            "/tmp/absolute-escape",
            "~/escape",
            "C:/drive-escape",
        ],
    )
    def test_escaping_root_is_refused_without_writing(self, tmp_path: Path, root: str) -> None:
        """The refusal happens before any file is created."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        with pytest.raises(SkillRootSecurityError):
            install_default_skills(str(workspace), skills_root=root, explicit=True)

        assert _relative_files(workspace) == []
        assert _relative_files(tmp_path) == []

    def test_symlinked_skills_root_is_refused(self, tmp_path: Path) -> None:
        """A symlinked skills root cannot redirect writes outside the workspace.

        Contract migration: the refusal is now raised by the managed
        namespace capability, which reports a linked parent as
        ``NamespaceBoundaryError``.  The refusal itself is unchanged: the
        linked directory is never followed and nothing is written outside the
        workspace.
        """
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (workspace / ".polaris").mkdir()
        os.symlink(outside, workspace / ".polaris" / "skills")

        with pytest.raises(NamespaceBoundaryError):
            install_default_skills(str(workspace), explicit=True)

        assert _relative_files(outside) == []


class TestBuiltinSkillsStayUsable:
    """A read-only loader exposes the builtin catalogue without any write."""

    def test_fresh_workspace_exposes_builtin_skills_without_writing(self, tmp_path: Path) -> None:
        """Builtin skills are usable on a fresh workspace with zero disk effect."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        loader = SkillLoader(str(workspace))

        assert sorted(item["name"] for item in loader.list_skills()) == sorted(DEFAULT_SKILLS)
        assert "Security" in loader.load_skill_content("security-review")
        assert "Skills available" in loader.get_system_prompt_section()
        assert _relative_files(workspace) == []

    def test_unknown_skill_still_reports_an_error(self, tmp_path: Path) -> None:
        """Unknown skill names keep their established error contract."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        loader = SkillLoader(str(workspace))

        content = loader.load_skill_content("nonexistent")

        assert "Error" in content
        assert "Unknown skill" in content


class TestLegacyRuntimeTreeIsDiscoveryOnly:
    """The retired duplicate runtime tree is read, never written, never copied."""

    def test_legacy_runtime_skill_is_discovered_without_being_copied(self, tmp_path: Path) -> None:
        """A legacy runtime skill is readable but never copied into the canonical root."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        legacy = workspace / ".polaris" / "runtime" / "skills"
        legacy.mkdir(parents=True)
        (legacy / "legacy-only.md").write_text(
            "---\ndescription: LEGACY\n---\n\n# legacy\n",
            encoding="utf-8",
        )

        loader = SkillLoader(str(workspace))

        assert "legacy-only" in {item["name"] for item in loader.list_skills()}
        # Discovery is read-only: the canonical root was not created and the
        # legacy file was not copied anywhere.
        assert not (workspace / ".polaris" / "skills").exists()
        assert sorted(path.name for path in legacy.iterdir()) == ["legacy-only.md"]

    def test_explicit_install_does_not_write_the_legacy_tree(self, tmp_path: Path) -> None:
        """An explicit installation never writes the retired runtime tree."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        install_default_skills(str(workspace), explicit=True)

        assert not (workspace / ".polaris" / "runtime" / "skills").exists()


class TestConfiguredRootOutOfWorkspace:
    """A configured root outside the workspace is refused or read-only."""

    def test_a_configured_escaping_root_is_refused_by_the_installer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The environment cannot redirect an installation out of the workspace."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        monkeypatch.setenv("KERNELONE_SKILLS_ROOT", "../escape")

        with pytest.raises(SkillRootSecurityError):
            install_default_skills(str(workspace), explicit=True)

        assert _relative_files(tmp_path) == []

    def test_a_configured_escaping_root_stays_read_only_for_the_loader(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The loader keeps serving the builtin catalogue from memory."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        monkeypatch.setenv("KERNELONE_SKILLS_ROOT", "../escape")

        loader = SkillLoader(str(workspace))

        assert sorted(item["name"] for item in loader.list_skills()) == sorted(DEFAULT_SKILLS)
        assert _relative_files(tmp_path) == []

    def test_an_explicit_escaping_root_is_refused_by_the_loader(
        self,
        tmp_path: Path,
    ) -> None:
        """An explicitly requested escaping root is refused loudly."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        with pytest.raises(SkillRootSecurityError):
            SkillLoader(str(workspace), skills_root="../escape")

        assert _relative_files(tmp_path) == []


class TestInstallationFailuresAreNotSwallowed:
    """A refused or failed installation surfaces its real cause."""

    def test_a_create_failure_propagates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown create failure is raised, never converted into a success."""
        import polaris.kernelone.single_agent.skill_system as skill_system

        class _UnknownWriteError(RuntimeError):
            """A failure whose effect state is unknown."""

        def _fail(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise _UnknownWriteError("write effect unknown")

        monkeypatch.setattr(skill_system, "_create_skill_file", _fail)
        workspace = tmp_path / "ws"
        workspace.mkdir()

        with pytest.raises(_UnknownWriteError, match="write effect unknown"):
            install_default_skills(str(workspace), explicit=True)


class TestRegisteredDenyingAdapterIsHonoured:
    """The installer must honour the *registered* adapter's refusal.

    This is the regression for the rejected candidate: the installer used a
    guarded primitive directly, so an ordinary KFS write denied by the
    registered adapter was bypassed and eight skill files still appeared.
    The effect must now be owned by the adapter.
    """

    def test_a_registered_denying_adapter_refuses_the_install_without_any_effect(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """A registered denial creates no parent directory and no file."""
        workspace = _workspace(tmp_path, monkeypatch)
        adapter = _DenyingAdapter()
        registered_adapter(adapter)

        with pytest.raises(NamespaceOperationUnavailableError):
            install_default_skills(str(workspace), explicit=True)

        assert adapter.legacy_write_calls == [], "the legacy writer must not run as an authorization probe"
        assert _tree(workspace) == [".polaris", ".polaris/runtime"], "the denial must leave no physical trace"

    def test_the_denying_adapter_denial_matches_the_ordinary_kfs_write_denial(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """The installer and an ordinary write agree on the adapter's refusal."""
        from polaris.kernelone.fs import KernelFileSystem

        workspace = _workspace(tmp_path, monkeypatch)
        adapter = _DenyingAdapter()
        registered_adapter(adapter)

        fs = KernelFileSystem(str(workspace), adapter)
        with pytest.raises(PermissionError):
            fs.workspace_write_text("control.txt", "control")

        with pytest.raises(PermissionError):
            install_default_skills(str(workspace), explicit=True)

        assert not (workspace / "control.txt").exists()
        assert _skill_files(workspace) == []

    def test_the_executor_constructor_is_zero_write_under_a_denying_adapter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """Constructing the executor writes nothing even when writes are denied."""
        from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

        workspace = _workspace(tmp_path, monkeypatch)
        adapter = _DenyingAdapter()
        registered_adapter(adapter)

        AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset())

        assert _tree(workspace) == [".polaris", ".polaris/runtime"]

    def test_the_read_tools_are_zero_write_under_a_denying_adapter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """``load_skill`` and ``skill_manifest`` never install behind the adapter."""
        from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

        workspace = _workspace(tmp_path, monkeypatch)
        adapter = _DenyingAdapter()
        registered_adapter(adapter)

        executor = AgentAccelToolExecutor(
            str(workspace),
            allowed_tools=frozenset({"load_skill", "skill_manifest"}),
        )

        loaded = executor.execute("load_skill", {"name": "security-review"})
        manifest = executor.execute("skill_manifest", {})

        assert loaded["ok"] is True
        assert manifest["ok"] is True
        assert _tree(workspace) == [".polaris", ".polaris/runtime"], "the read tools must not write"

    def test_lawful_install_succeeds_and_cold_loader_sees_the_skills(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """The legal progress is preserved through the adapter-owned create."""
        workspace = _workspace(tmp_path, monkeypatch)
        registered_adapter(_PermittingAdapter())

        installed = install_default_skills(str(workspace), explicit=True)

        assert sorted(installed) == sorted(DEFAULT_SKILLS)
        assert _skill_files(workspace) == sorted(f".polaris/skills/{name}.md" for name in DEFAULT_SKILLS)

        loader = SkillLoader(str(workspace))
        assert sorted(item["name"] for item in loader.list_skills()) == sorted(DEFAULT_SKILLS)
        assert "Security" in loader.load_skill_content("security-review")

    def test_an_existing_user_file_is_preserved_through_the_adapter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """User-authored content survives the installation byte-for-byte."""
        workspace = _workspace(tmp_path, monkeypatch)
        registered_adapter(_PermittingAdapter())
        target = workspace / ".polaris" / "skills" / "security-review.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("USER-AUTHORED\n", encoding="utf-8")

        installed = install_default_skills(str(workspace), explicit=True)

        assert "security-review" not in installed
        assert target.read_text(encoding="utf-8") == "USER-AUTHORED\n"

    def test_two_concurrent_installers_create_each_skill_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """Concurrent installers never both claim the same leaf."""
        from concurrent.futures import ThreadPoolExecutor

        workspace = _workspace(tmp_path, monkeypatch)
        registered_adapter(_PermittingAdapter())

        def install() -> list[str]:
            return install_default_skills(str(workspace), explicit=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: install(), (0, 1)))

        claimed = [name for outcome in outcomes for name in outcome]
        assert sorted(claimed) == sorted(DEFAULT_SKILLS), "each skill is created exactly once"
        assert len(_skill_files(workspace)) == len(DEFAULT_SKILLS)

    def test_the_skill_handlers_never_install_behind_the_adapter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """The read handlers are zero-write under a registered denial."""
        from polaris.kernelone.llm.toolkit.executor.handlers import skills as skill_handlers

        workspace = _workspace(tmp_path, monkeypatch)
        adapter = _DenyingAdapter()
        registered_adapter(adapter)
        skill_handlers._skill_loaders.clear()
        skill_handlers._skill_interfaces.clear()

        class _Executor:
            """A minimal executor that only exposes the workspace."""

            def __init__(self, workspace_path: str) -> None:
                self.workspace = workspace_path

        try:
            executor = _Executor(str(workspace))
            loaded = skill_handlers._handle_load_skill(executor, name="security-review")
            manifest = skill_handlers._handle_skill_manifest(executor)
        finally:
            skill_handlers._skill_loaders.clear()
            skill_handlers._skill_interfaces.clear()

        assert loaded["ok"] is True
        assert manifest["ok"] is True
        assert manifest["count"] == len(DEFAULT_SKILLS)
        assert _tree(workspace) == [".polaris", ".polaris/runtime"], "the handlers must not write"

    def test_an_uncertain_create_effect_is_not_replayed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        registered_adapter: Any,
    ) -> None:
        """A post-effect failure stays unknown and is never replayed."""
        workspace = _workspace(tmp_path, monkeypatch)
        adapter = _CommitThenFailAdapter()
        registered_adapter(adapter)

        with pytest.raises(OSError, match="synthetic failure after the creation landed"):
            install_default_skills(str(workspace), explicit=True)

        assert adapter.create_calls == 1, "an uncertain create must never be replayed"
