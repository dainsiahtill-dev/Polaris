"""Director multi-backend concurrency: provider pool + per-thread override.

A role may be spread across a pool of provider endpoints (each qwen instance is
one provider) with a concurrency count; a worker thread binds its own endpoint
via a thread-local override so its LLM calls route to the assigned backend.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from polaris.kernelone.llm.runtime_config import (
    RoleModelConfig,
    RuntimeConfigManager,
    clear_role_provider_override,
    get_role_concurrency,
    get_role_model,
    get_role_provider_pool,
    reset_runtime_config_manager,
    set_role_provider_override,
    set_runtime_config_manager,
)


def _manager(tmp_path: Path, director: dict[str, object]) -> RuntimeConfigManager:
    config = {
        "schema_version": 2,
        "providers": {
            "prov-local": {"type": "openai_compat", "base_url": "http://localhost:8189", "model": "qwen"},
            "prov-lan": {"type": "openai_compat", "base_url": "http://192.168.1.50:8189", "model": "qwen"},
        },
        "roles": {"director": director},
    }
    path = tmp_path / "llm_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return RuntimeConfigManager(config_path_resolver=lambda: str(path))


class TestRoleModelConfigPool:
    def test_resolved_pool_falls_back_to_primary(self) -> None:
        cfg = RoleModelConfig(role_id="director", provider_id="prov-local", model="qwen")
        assert cfg.resolved_pool() == ("prov-local",)
        assert cfg.concurrency == 1

    def test_explicit_pool_used(self) -> None:
        cfg = RoleModelConfig(
            role_id="director",
            provider_id="prov-local",
            model="qwen",
            provider_pool=("prov-local", "prov-lan"),
            concurrency=2,
        )
        assert cfg.resolved_pool() == ("prov-local", "prov-lan")


class TestConfigParsing:
    def test_pool_and_concurrency_parsed(self, tmp_path: Path) -> None:
        mgr = _manager(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 3},
        )
        cfg = mgr.get_role_config("director")
        assert cfg is not None
        # primary is always first, then pool extras, deduped
        assert cfg.resolved_pool() == ("prov-local", "prov-lan")
        assert cfg.concurrency == 3

    def test_missing_pool_defaults_to_primary(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, {"provider_id": "prov-local", "model": "qwen"})
        cfg = mgr.get_role_config("director")
        assert cfg is not None
        assert cfg.resolved_pool() == ("prov-local",)
        assert cfg.concurrency == 1

    def test_invalid_concurrency_floors_to_one(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, {"provider_id": "prov-local", "model": "qwen", "concurrency": 0})
        cfg = mgr.get_role_config("director")
        assert cfg is not None and cfg.concurrency == 1


class TestThreadOverride:
    def teardown_method(self) -> None:
        clear_role_provider_override()
        reset_runtime_config_manager()

    def test_override_routes_when_in_pool(self, tmp_path: Path) -> None:
        mgr = _manager(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"]},
        )
        set_runtime_config_manager(mgr)
        assert get_role_model("director") == ("prov-local", "qwen")
        set_role_provider_override("director", "prov-lan")
        assert get_role_model("director") == ("prov-lan", "qwen")
        clear_role_provider_override("director")
        assert get_role_model("director") == ("prov-local", "qwen")

    def test_override_ignored_when_not_in_pool(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, {"provider_id": "prov-local", "model": "qwen"})
        set_runtime_config_manager(mgr)
        set_role_provider_override("director", "prov-lan")  # not in pool
        # falls back to primary — a stale override never routes off-pool
        assert get_role_model("director") == ("prov-local", "qwen")

    def test_override_is_thread_local(self, tmp_path: Path) -> None:
        mgr = _manager(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"]},
        )
        set_runtime_config_manager(mgr)
        set_role_provider_override("director", "prov-lan")
        other: dict[str, tuple[str, str]] = {}

        def worker() -> None:
            # no override set in this thread -> sees the primary
            other["model"] = get_role_model("director")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert other["model"] == ("prov-local", "qwen")
        # main thread still sees its override
        assert get_role_model("director") == ("prov-lan", "qwen")

    def test_override_propagates_through_asyncio_run_and_to_thread(self, tmp_path: Path) -> None:
        # The Director adapter runs its LLM call via asyncio.run + asyncio.to_thread;
        # the override (a ContextVar) must reach the actual HTTP-invoke thread.
        import asyncio

        mgr = _manager(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"]},
        )
        set_runtime_config_manager(mgr)
        set_role_provider_override("director", "prov-lan")

        async def _coro() -> tuple[str, str]:
            # resolution that happens off-loop (like the provider HTTP call)
            return await asyncio.to_thread(get_role_model, "director")

        # asyncio.run in this thread inherits the current context (override set)
        assert asyncio.run(_coro()) == ("prov-lan", "qwen")


class TestModuleHelpers:
    def teardown_method(self) -> None:
        reset_runtime_config_manager()

    def test_pool_and_concurrency_helpers(self, tmp_path: Path) -> None:
        mgr = _manager(
            tmp_path,
            {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 2},
        )
        set_runtime_config_manager(mgr)
        assert get_role_provider_pool("director") == ("prov-local", "prov-lan")
        assert get_role_concurrency("director") == 2

    def test_helpers_safe_when_unbound(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, {"model": "qwen"})  # no provider_id -> unbound
        set_runtime_config_manager(mgr)
        assert get_role_provider_pool("director") == ()
        assert get_role_concurrency("director") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
