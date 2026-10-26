"""Actual config owner inference: physical path components, nested workspaces."""

import os
from pathlib import Path

import pytest
from polaris.cells.llm.control_plane.internal.llm_config_agent import (
    LLMConfig,
    LLMConfigStore,
    _infer_workspace_from_storage_path,
)


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (".polaris/audits/project/runtime/llm", ".polaris/audits/project"),
        (".polaris/audits/project/.polaris/config/llm", ".polaris/audits/project"),
        (".polaris/audits/project/.polaris/runtime/llm", ".polaris/audits/project"),
        (".polaris-cache/audits/project/runtime/llm", ".polaris-cache/audits/project"),
        (".polaris/runtime/llm", "."),
    ],
)
def test_nearest_complete_storage_boundary(tmp_path: Path, suffix: str, expected: str) -> None:
    target = tmp_path / suffix
    assert _infer_workspace_from_storage_path(str(target)) == str((tmp_path / expected).resolve())


@pytest.mark.parametrize("suffix", ["runtime-assets/config", ".polaris-assets/config"])
def test_substrings_do_not_claim_a_workspace(tmp_path: Path, suffix: str) -> None:
    assert _infer_workspace_from_storage_path(str(Path("/synthetic-unmarked-root") / suffix)) == str(
        Path.cwd().resolve()
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows path components are case insensitive")
def test_case_sensitive_physical_marker(tmp_path: Path) -> None:
    assert _infer_workspace_from_storage_path(str(Path("/synthetic-unmarked-root/.POLARIS/config"))) == str(
        Path.cwd().resolve()
    )


def test_real_store_cold_read_keeps_nested_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / ".polaris/audits/project"
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "private-home"))
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace))
    for key in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    store = LLMConfigStore(str(workspace / ".polaris/runtime/llm"))
    assert Path(store._workspace) == workspace.resolve()
    config = LLMConfig(
        config_id="synthetic-config",
        role="director",
        provider_id="synthetic",
        provider_type="openai",
        provider_kind="openai",
        model="synthetic",
        profile="synthetic",
        provider_cfg={"api_key": "synthetic-only"},
    )
    store.save(config)
    recovered = LLMConfigStore(str(workspace / ".polaris/runtime/llm"))
    assert recovered.get("director") == config
    assert Path(recovered._workspace) == workspace.resolve()
    assert recovered._fs.resolve_path(recovered._configs_file_logical).is_relative_to(workspace.resolve())


def test_legacy_runtime_path_is_not_reenabled_by_owner_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / ".polaris/audits/project"
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "private-home"))
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace))
    for key in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    assert _infer_workspace_from_storage_path(str(workspace / "runtime/llm")) == str(workspace.resolve())
    with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
        LLMConfigStore(str(workspace / "runtime/llm"))
    assert not (workspace / "runtime").exists()
