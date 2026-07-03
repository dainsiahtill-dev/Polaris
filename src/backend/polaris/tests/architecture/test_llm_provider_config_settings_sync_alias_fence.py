"""Architecture fence for retired provider-config settings sync helper."""

from __future__ import annotations

from pathlib import Path

import polaris.cells.llm.provider_config as provider_config
import polaris.cells.llm.provider_config.internal.settings_sync as settings_sync
import polaris.cells.llm.provider_config.public as provider_config_public
import polaris.cells.llm.provider_config.public.service as provider_config_service

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROVIDER_CONFIG_ROOT = BACKEND_ROOT / "polaris" / "cells" / "llm" / "provider_config"
LLM_ROUTER_SOURCE = BACKEND_ROOT / "polaris" / "delivery" / "http" / "routers" / "llm.py"
RETIRED_HELPER = "_".join(("sync", "settings", "from", "llm"))


def test_provider_config_sync_helper_alias_is_retired() -> None:
    """Provider config exposes the explicit apply helper, not the retired wrapper."""
    assert hasattr(settings_sync, "apply_llm_config_updates_to_settings")
    assert not hasattr(settings_sync, RETIRED_HELPER)
    assert not hasattr(provider_config_service, RETIRED_HELPER)
    assert not hasattr(provider_config_public, RETIRED_HELPER)
    assert not hasattr(provider_config, RETIRED_HELPER)
    assert RETIRED_HELPER not in settings_sync.__all__
    assert RETIRED_HELPER not in provider_config_service.__all__
    assert RETIRED_HELPER not in provider_config_public.__all__
    assert RETIRED_HELPER not in provider_config.__all__


def test_provider_config_sources_do_not_reintroduce_sync_helper_alias() -> None:
    """Block the retired mutating helper name from provider-config production paths."""
    offenders: list[str] = []
    for source_file in [*sorted(PROVIDER_CONFIG_ROOT.rglob("*.py")), LLM_ROUTER_SOURCE]:
        source = source_file.read_text(encoding="utf-8")
        if RETIRED_HELPER in source:
            offenders.append(source_file.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
