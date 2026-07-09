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
    get_role_binding_candidates,
    get_role_binding_slots,
    get_role_concurrency,
    get_role_model,
    get_role_provider_pool,
    mark_role_binding_unhealthy,
    reset_runtime_config_manager,
    resolve_role_worker_plan,
    set_role_binding_override,
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


def _manager_with_config(tmp_path: Path, config: dict[str, object]) -> RuntimeConfigManager:
    path = tmp_path / "llm_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return RuntimeConfigManager(config_path_resolver=lambda: str(path))


class TestResolveRoleWorkerPlan:
    """Per-role worker plan: len == configured concurrency, decoupled from provider count."""

    def test_plan_count_equals_concurrency_over_pool(self, tmp_path: Path) -> None:
        set_runtime_config_manager(
            _manager(
                tmp_path,
                {"provider_id": "prov-local", "model": "qwen", "provider_pool": ["prov-lan"], "concurrency": 4},
            )
        )
        try:
            plan = resolve_role_worker_plan("director")
            assert len(plan) == 4  # 4 workers (concurrency), NOT 2 (provider count)
            assert [str(s) for s in plan] == ["prov-local", "prov-lan", "prov-local", "prov-lan"]
        finally:
            reset_runtime_config_manager()

    def test_three_workers_on_one_provider(self, tmp_path: Path) -> None:
        # The user's case: concurrency=3 on ONE provider (e.g. CE=3 on a single MiniMax).
        set_runtime_config_manager(_manager(tmp_path, {"provider_id": "prov-local", "model": "qwen", "concurrency": 3}))
        try:
            plan = resolve_role_worker_plan("director")
            assert len(plan) == 3
            assert {str(s) for s in plan} == {"prov-local"}
        finally:
            reset_runtime_config_manager()

    def test_concurrency_one_is_single_worker(self, tmp_path: Path) -> None:
        set_runtime_config_manager(_manager(tmp_path, {"provider_id": "prov-local", "model": "qwen", "concurrency": 1}))
        try:
            assert len(resolve_role_worker_plan("director")) == 1
        finally:
            reset_runtime_config_manager()


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

    def test_local_provider_defaults_to_one_slot_even_when_role_allows_more(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {"local": {"type": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen"}},
                "roles": {
                    "director": {
                        "max_concurrency": 6,
                        "bindings": [{"provider_id": "local", "model": "qwen"}],
                    }
                },
            },
        )

        slots = mgr.get_role_binding_slots("director")

        assert len(slots) == 1
        assert slots[0].provider_id == "local"
        assert slots[0].model == "qwen"
        assert slots[0].max_concurrency == 1

    def test_local_provider_explicit_capacity_can_allocate_multiple_slots(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "local": {
                        "type": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen",
                        "max_concurrency": 3,
                    }
                },
                "roles": {
                    "director": {
                        "max_concurrency": 5,
                        "bindings": [{"provider_id": "local", "model": "qwen"}],
                    }
                },
            },
        )

        slots = mgr.get_role_binding_slots("director")

        assert len(slots) == 3
        assert all(slot.provider_id == "local" and slot.model == "qwen" for slot in slots)

    def test_cloud_provider_can_allocate_multiple_same_provider_slots(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {"kimi": {"type": "kimi", "max_concurrency": 20, "model": "kimi-k2"}},
                "roles": {
                    "director": {
                        "max_concurrency": 5,
                        "bindings": [{"provider_id": "kimi", "model": "kimi-k2", "max_concurrency": 4}],
                    }
                },
            },
        )

        slots = mgr.get_role_binding_slots("director")

        assert len(slots) == 4
        assert {(slot.role_id, slot.provider_id, slot.model) for slot in slots} == {("director", "kimi", "kimi-k2")}
        assert [slot.slot_index for slot in slots] == [0, 1, 2, 3]

    def test_role_cap_limits_total_slots_across_bindings(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "kimi": {"type": "kimi", "max_concurrency": 10},
                    "minimax": {"type": "minimax", "max_concurrency": 10},
                },
                "roles": {
                    "director": {
                        "max_concurrency": 3,
                        "bindings": [
                            {"provider_id": "kimi", "model": "kimi-k2", "max_concurrency": 2},
                            {"provider_id": "minimax", "model": "MiniMax-M2", "max_concurrency": 2},
                        ],
                    }
                },
            },
        )

        slots = mgr.get_role_binding_slots("director")

        assert len(slots) == 3
        assert [slot.provider_id for slot in slots] == ["kimi", "kimi", "minimax"]

    def test_failover_candidates_include_all_bindings_when_worker_concurrency_is_one(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "minimax": {"type": "anthropic_compat", "model": "MiniMax-M3"},
                    "kimi": {"type": "anthropic_compat", "model": "kimi-for-coding"},
                },
                "roles": {
                    "director": {
                        "provider_id": "minimax",
                        "model": "MiniMax-M3",
                        "provider_pool": ["minimax"],
                        "concurrency": 1,
                        "bindings": [
                            {"provider_id": "minimax", "model": "MiniMax-M3"},
                            {"provider_id": "kimi", "model": "kimi-for-coding"},
                        ],
                    }
                },
            },
        )

        worker_slots = mgr.get_role_binding_slots("director")
        failover_candidates = mgr.get_role_binding_candidates("director")

        assert [slot.provider_id for slot in worker_slots] == ["minimax"]
        assert [slot.provider_id for slot in failover_candidates] == ["minimax", "kimi"]

        set_runtime_config_manager(mgr)
        try:
            assert [slot.provider_id for slot in get_role_binding_slots("director")] == ["minimax"]
            assert [slot.provider_id for slot in get_role_binding_candidates("director")] == ["minimax", "kimi"]
        finally:
            reset_runtime_config_manager()

    def test_failover_candidates_append_explicit_backup_role_without_worker_slot(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "minimax": {"type": "anthropic_compat", "model": "MiniMax-M3"},
                    "kimi": {"type": "anthropic_compat", "model": "kimi-for-coding"},
                    "gemma": {"type": "openai_compat", "model": "gemma-4-12B-it-Q8_0"},
                },
                "roles": {
                    "director": {
                        "provider_id": "minimax",
                        "model": "MiniMax-M3",
                        "provider_pool": ["minimax"],
                        "concurrency": 1,
                        "bindings": [
                            {"provider_id": "minimax", "model": "MiniMax-M3"},
                            {"provider_id": "kimi", "model": "kimi-for-coding"},
                        ],
                    },
                    "_director_backup": {
                        "provider_id": "gemma",
                        "model": "gemma-4-12B-it-Q8_0",
                        "bindings": [{"provider_id": "gemma", "model": "gemma-4-12B-it-Q8_0"}],
                    },
                },
            },
        )

        worker_slots = mgr.get_role_binding_slots("director")
        failover_candidates = mgr.get_role_binding_candidates("director")

        assert [slot.provider_id for slot in worker_slots] == ["minimax"]
        assert [slot.provider_id for slot in failover_candidates] == ["minimax", "kimi", "gemma"]
        assert {slot.role_id for slot in failover_candidates} == {"director"}
        assert str(failover_candidates[-1].binding_id).startswith("director:backup:")


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

    def test_binding_override_distinguishes_same_provider_different_models(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {"kimi": {"type": "kimi", "max_concurrency": 4}},
                "roles": {
                    "director": {
                        "max_concurrency": 2,
                        "bindings": [
                            {"provider_id": "kimi", "model": "kimi-k2"},
                            {"provider_id": "kimi", "model": "kimi-k1"},
                        ],
                    }
                },
            },
        )
        set_runtime_config_manager(mgr)

        set_role_binding_override("director", provider_id="kimi", model="kimi-k1")

        assert get_role_model("director") == ("kimi", "kimi-k1")

    def test_binding_override_honors_explicit_backup_role_candidate(self, tmp_path: Path) -> None:
        from polaris.kernelone.llm.engine._executor_base import resolve_provider_model

        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "minimax": {"type": "anthropic_compat", "model": "MiniMax-M3"},
                    "kimi": {"type": "anthropic_compat", "model": "kimi-for-coding"},
                    "gemma": {"type": "openai_compat", "model": "gemma-4-12B-it-Q8_0"},
                },
                "roles": {
                    "director": {
                        "provider_id": "minimax",
                        "model": "MiniMax-M3",
                        "provider_pool": ["minimax"],
                        "concurrency": 1,
                        "bindings": [
                            {"provider_id": "minimax", "model": "MiniMax-M3"},
                            {"provider_id": "kimi", "model": "kimi-for-coding"},
                        ],
                    },
                    "_director_backup": {
                        "provider_id": "gemma",
                        "model": "gemma-4-12B-it-Q8_0",
                        "bindings": [{"provider_id": "gemma", "model": "gemma-4-12B-it-Q8_0"}],
                    },
                },
            },
        )
        set_runtime_config_manager(mgr)

        set_role_binding_override("director", provider_id="gemma", model="gemma-4-12B-it-Q8_0")

        assert get_role_model("director") == ("gemma", "gemma-4-12B-it-Q8_0")
        assert resolve_provider_model(provider_id="minimax", model="MiniMax-M3", role="director") == (
            "gemma",
            "gemma-4-12B-it-Q8_0",
        )


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
        assert len(get_role_binding_slots("director")) == 2

    def test_cooling_one_binding_preserves_worker_count_on_healthy_binding(self, tmp_path: Path) -> None:
        mgr = _manager_with_config(
            tmp_path,
            {
                "schema_version": 2,
                "providers": {
                    "prov-local": {
                        "type": "openai_compat",
                        "base_url": "http://localhost:8189",
                        "model": "qwen",
                        "max_concurrency": 4,
                    },
                    "prov-lan": {
                        "type": "openai_compat",
                        "base_url": "http://192.168.1.50:8189",
                        "model": "qwen",
                        "max_concurrency": 4,
                    },
                },
                "roles": {
                    "director": {
                        "max_concurrency": 4,
                        "bindings": [
                            {"provider_id": "prov-local", "model": "qwen"},
                            {"provider_id": "prov-lan", "model": "qwen"},
                        ],
                    }
                },
            },
        )
        set_runtime_config_manager(mgr)

        mark_role_binding_unhealthy(
            "director",
            provider_id="prov-local",
            model="qwen",
            cooldown_seconds=60,
        )

        plan = resolve_role_worker_plan("director")
        assert len(plan) == 4
        assert {slot.provider_id for slot in plan} == {"prov-lan"}
        assert get_role_model("director") == ("prov-lan", "qwen")

    def test_helpers_safe_when_unbound(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, {"model": "qwen"})  # no provider_id -> unbound
        set_runtime_config_manager(mgr)
        assert get_role_provider_pool("director") == ()
        assert get_role_concurrency("director") == 1
        assert get_role_binding_slots("director") == ()


class TestResolveProviderModelHonorsOverride:
    """The provider/model resolver must let a context-scoped role override win over a
    provider_id baked into a cached role profile — otherwise pooled Director workers
    all collapse onto the default backend and the extra endpoints stay idle.
    """

    def teardown_method(self) -> None:
        clear_role_provider_override("director")
        reset_runtime_config_manager()

    @staticmethod
    def _heterogeneous_manager(tmp_path: Path) -> RuntimeConfigManager:
        """A pool whose endpoints serve DIFFERENTLY-NAMED models (like int4 vs gpu0)."""
        config = {
            "schema_version": 2,
            "providers": {
                "prov-local": {"type": "openai_compat", "base_url": "http://localhost:8189", "model": "m-int4"},
                "prov-gpu0": {"type": "openai_compat", "base_url": "http://10.0.0.1:18110", "model": "m-gpu0"},
            },
            "roles": {
                "director": {
                    "provider_id": "prov-local",
                    "model": "m-int4",
                    "provider_pool": ["prov-gpu0"],
                    "concurrency": 2,
                }
            },
        }
        path = tmp_path / "llm_config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return RuntimeConfigManager(config_path_resolver=lambda: str(path))

    def test_override_wins_over_explicit_baked_provider(self, tmp_path: Path) -> None:
        from polaris.kernelone.llm.engine._executor_base import resolve_provider_model

        set_runtime_config_manager(self._heterogeneous_manager(tmp_path))

        # No override: the explicit (profile-baked) pair is returned verbatim.
        assert resolve_provider_model(provider_id="prov-local", model="m-int4", role="director") == (
            "prov-local",
            "m-int4",
        )

        # A worker pins itself to prov-gpu0; even though the request still carries the
        # profile-baked prov-local/m-int4, resolution must route to prov-gpu0 AND switch
        # the model name to that endpoint's own model (a mismatch is a hard 404).
        set_role_provider_override("director", "prov-gpu0")
        assert resolve_provider_model(provider_id="prov-local", model="m-int4", role="director") == (
            "prov-gpu0",
            "m-gpu0",
        )

        # Cleared override falls back to the explicit pair.
        clear_role_provider_override("director")
        assert resolve_provider_model(provider_id="prov-local", model="m-int4", role="director") == (
            "prov-local",
            "m-int4",
        )

    def test_no_override_no_role_returns_explicit(self, tmp_path: Path) -> None:
        from polaris.kernelone.llm.engine._executor_base import resolve_provider_model

        set_runtime_config_manager(_manager(tmp_path, {"provider_id": "prov-local", "model": "qwen"}))
        # No role at all → explicit pair is returned unchanged.
        assert resolve_provider_model(provider_id="prov-local", model="qwen", role=None) == ("prov-local", "qwen")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
