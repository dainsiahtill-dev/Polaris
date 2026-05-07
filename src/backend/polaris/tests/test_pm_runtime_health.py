from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from polaris.bootstrap.runtime_health import check_backend_available


def test_check_backend_available_allows_ollama_runtime_without_cli() -> None:
    settings = SimpleNamespace(pm_backend="auto", workspace="/tmp/workspace", ramdisk_root="")
    llm_payload = {
        "providers": {"ollama": {"type": "ollama", "base_url": "http://127.0.0.1:11434"}},
        "roles": {"pm": {"provider_id": "ollama", "model": "qwen"}},
    }

    with (
        patch("polaris.kernelone.storage.io_paths.build_cache_root", return_value=""),
        patch("polaris.kernelone.llm.config_store.load_llm_config", return_value=llm_payload),
        patch("shutil.which", return_value=None),
    ):
        error = check_backend_available(settings)

    assert error is None


def test_check_backend_available_still_requires_codex_cli_for_codex_provider() -> None:
    settings = SimpleNamespace(pm_backend="auto", workspace="/tmp/workspace", ramdisk_root="")
    llm_payload = {
        "providers": {"codex_cli": {"type": "codex_cli"}},
        "roles": {"pm": {"provider_id": "codex_cli", "model": "gpt-5"}},
    }

    with (
        patch("polaris.kernelone.storage.io_paths.build_cache_root", return_value=""),
        patch("polaris.kernelone.llm.config_store.load_llm_config", return_value=llm_payload),
        patch("shutil.which", return_value=None),
    ):
        error = check_backend_available(settings)

    assert error == "codex command not found in PATH. PM role mapping points to codex provider."
