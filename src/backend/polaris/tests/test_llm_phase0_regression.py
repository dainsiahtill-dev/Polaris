"""Phase 0 Regression Tests for LLM Configuration Unification

Tests for:
1. Default config unique provider keys (minimax fix)
2. LLMStatus contains last_updated field
3. LLMConfig atomic write and UTF-8 roundtrip
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from polaris.kernelone.storage import resolve_runtime_path

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture(autouse=True)
def isolate_polaris_root(tmp_path, monkeypatch):
    app_root = tmp_path / "polaris_root"
    app_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_ROOT", str(app_root))
    # llm_config uses KERNELONE_HOME via storage_layout.resolve_global_path.
    # Isolate it to avoid touching the real user config during tests.
    monkeypatch.setenv("KERNELONE_HOME", str(app_root))
    return app_root


class TestLLMDefaultConfigUniqueProviderKeys:
    """Test that default LLM config has unique provider keys."""

    def test_default_config_no_duplicate_provider_keys(self):
        """Ensure no duplicate provider IDs exist in default config."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        provider_ids = list(providers.keys())
        duplicate_ids = [pid for pid in provider_ids if provider_ids.count(pid) > 1]

        assert len(duplicate_ids) == 0, f"Found duplicate provider IDs in default config: {duplicate_ids}"

    def test_minimax_provider_appears_only_once(self):
        """Ensure minimax provider is defined exactly once with correct type."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        minimax_count = (
            providers.count("minimax") if hasattr(providers, "count") else sum(1 for k in providers if k == "minimax")
        )

        assert minimax_count == 1, f"Expected exactly one 'minimax' provider, found {minimax_count}"

        if "minimax" in providers:
            minimax_config = providers["minimax"]
            assert minimax_config.get("type") == "minimax", (
                f"Expected minimax type 'minimax', got '{minimax_config.get('type')}'"
            )

    def test_default_config_includes_chief_engineer_role(self):
        """Default LLM config should cover all desktop role workspaces."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        roles = config.get("roles", {})

        assert "pm" in roles
        assert "chief_engineer" in roles
        assert "director" in roles
        assert roles["chief_engineer"]["profile"] == "chief-engineer-blueprint"
        assert roles["chief_engineer"]["provider_id"] in config["providers"]


class TestLLMStatusLastUpdated:
    """Test that LLMStatus response includes last_updated field."""

    def test_status_response_has_last_updated_field(self):
        """Verify /v2/llm/status payload includes last_updated."""
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.pm_backend = "openai"
        mock_settings.pm_model = "gpt-4"
        mock_settings.director_model = None
        mock_settings.docs_model = None
        mock_settings.qa_model = None
        mock_settings.qa_enabled = True

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value={"schema_version": 1, "providers": {}, "roles": {}},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value={"providers": {}, "roles": {}},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

            assert "last_updated" in response, "Response missing 'last_updated' field"
            assert response["last_updated"] is None or isinstance(response["last_updated"], str), (
                f"last_updated should be None or ISO string, got {type(response['last_updated'])}"
            )

            if response["last_updated"] is not None:
                try:
                    datetime.fromisoformat(response["last_updated"])
                except (TypeError, ValueError) as e:
                    pytest.fail(f"last_updated is not valid ISO format: {e}")

    def test_status_last_updated_uses_latest_readiness_index_timestamp(self, tmp_path):
        """A fresh passed role test must advance the top-level status clock."""
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        config_path = tmp_path / "llm.json"
        config_path.write_text("{}", encoding="utf-8")
        old_epoch = 1_700_000_000
        os.utime(config_path, (old_epoch, old_epoch))

        mock_settings = MagicMock()
        mock_settings.workspace = str(tmp_path)
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        index_timestamp = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3-max",
                    "timestamp": index_timestamp,
                },
            },
            "providers": {},
            "last_update": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": None},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value=str(tmp_path / "cache"),
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.llm_config_path",
                return_value=str(config_path),
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["state"] == "READY"
        assert response["blocked_roles"] == []
        assert response["last_updated"] == index_timestamp

    def test_status_last_updated_uses_latest_interview_timestamp(self, tmp_path):
        """A fresh interview report must be visible to stale-response guards."""
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        config_path = tmp_path / "llm.json"
        config_path.write_text("{}", encoding="utf-8")
        old_epoch = 1_700_000_000
        os.utime(config_path, (old_epoch, old_epoch))

        mock_settings = MagicMock()
        mock_settings.workspace = str(tmp_path)
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        index_timestamp = datetime.now(timezone.utc).isoformat()
        interview_timestamp = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3-max",
                    "timestamp": index_timestamp,
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": interview_timestamp},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value=str(tmp_path / "cache"),
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.llm_config_path",
                return_value=str(config_path),
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["state"] == "READY"
        assert response["blocked_roles"] == []
        assert response["last_updated"] == interview_timestamp

    def test_status_exposes_role_binding_context_windows(self):
        """ContextOS must not fall back to one fixed 128k window for every bound model."""
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 2,
            "providers": {
                "kimi": {
                    "type": "anthropic_compat",
                    "name": "Kimi Coding",
                    "model": "kimi-for-coding",
                    "max_context_tokens": 262_144,
                    "max_output_tokens": 16_384,
                },
                "qwen-a": {
                    "type": "openai_compat",
                    "name": "Qwen A",
                    "model": "qwen3.6-27b-gpu0",
                    "max_context_tokens": 32_768,
                    "max_output_tokens": 8_192,
                },
                "qwen-b": {
                    "type": "openai_compat",
                    "name": "Qwen B",
                    "model": "qwen3.6-27b-gpu1",
                    "max_context_tokens": 65_536,
                    "max_output_tokens": 8_190,
                },
            },
            "roles": {
                "pm": {"provider_id": "kimi", "model": "kimi-for-coding"},
                "director": {
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu0",
                    "bindings": [
                        {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0"},
                        {"provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1"},
                    ],
                },
            },
            "policies": {
                "required_ready_roles": ["pm", "director"],
            },
        }

        ready_index = {
            "roles": {
                "pm": {
                    "ready": True,
                    "provider_id": "kimi",
                    "model": "kimi-for-coding",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "director": {
                    "ready": True,
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu0",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=ready_index,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index_candidates",
                return_value=[],
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": None},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        pm_status = response["roles"]["pm"]
        director_status = response["roles"]["director"]

        assert pm_status["provider_name"] == "Kimi Coding"
        assert pm_status["provider_type"] == "anthropic_compat"
        assert pm_status["max_context_tokens"] == 262_144
        assert pm_status["max_output_tokens"] == 16_384
        assert pm_status["bindings"][0]["max_context_tokens"] == 262_144

        assert director_status["provider_name"] == "Qwen A"
        assert director_status["max_context_tokens"] == 32_768
        assert [item["max_context_tokens"] for item in director_status["bindings"]] == [32_768, 65_536]
        assert response["providers"]["qwen-b"]["max_context_tokens"] == 65_536

    def test_llm_status_degrades_multi_bound_director_when_one_binding_is_unready(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        now = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "schema_version": 2,
            "providers": {
                "qwen-a": {"type": "openai_compat", "model": "qwen3.6-27b-gpu0"},
                "qwen-b": {"type": "openai_compat", "model": "qwen3.6-27b-gpu1"},
            },
            "roles": {
                "architect": {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0"},
                "pm": {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0"},
                "director": {
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu0",
                    "bindings": [
                        {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0"},
                        {"provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1"},
                    ],
                },
                "qa": {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0"},
            },
            "policies": {"required_ready_roles": ["architect", "pm", "director", "qa"]},
        }
        index_payload = {
            "roles": {
                role: {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu0",
                    "timestamp": now,
                }
                for role in ("architect", "pm", "director", "qa")
            },
            "providers": {
                "qwen-b": {
                    "ready": False,
                    "grade": "FAIL",
                    "role": "connectivity",
                    "model": "qwen3.6-27b-gpu1",
                    "timestamp": now,
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index_candidates",
                return_value=[],
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": None},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        director_status = response["roles"]["director"]
        assert director_status["ready"] is True
        assert director_status["degraded"] is True
        assert response["state"] == "DEGRADED"
        assert response["factory_state"] == "DEGRADED"
        assert response["blocked_roles"] == []
        assert response["factory_blocked_roles"] == []
        assert response["degraded_roles"] == ["director"]
        assert response["factory_degraded_roles"] == ["director"]
        assert director_status["bindings"][0]["ready"] is True
        assert director_status["bindings"][1]["ready"] is False
        assert director_status["bindings"][1]["skip_allowed"] is True
        assert director_status["bindings"][1]["skip_reason"] == "provider_readiness_failed"
        assert director_status["bindings"][1]["readiness_issue"] == "readiness_failed"
        assert director_status["skipped_bindings"] == [
            {
                "provider_id": "qwen-b",
                "model": "qwen3.6-27b-gpu1",
                "binding_id": "",
                "reason": "provider_readiness_failed",
                "readiness_source": "provider_index",
            }
        ]
        assert director_status["readiness_issue"] == "degraded: skipped unavailable Director binding(s)"

    def test_llm_status_blocks_multi_bound_director_when_all_bindings_are_unready(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = False

        now = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "schema_version": 2,
            "providers": {
                "qwen-a": {"type": "openai_compat", "model": "qwen3.6-27b-gpu0"},
                "qwen-b": {"type": "openai_compat", "model": "qwen3.6-27b-gpu1"},
            },
            "roles": {
                "director": {
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu0",
                    "bindings": [
                        {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0"},
                        {"provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1"},
                    ],
                },
            },
            "policies": {"required_ready_roles": ["director"]},
        }
        index_payload = {
            "roles": {},
            "providers": {
                provider_id: {
                    "ready": False,
                    "grade": "FAIL",
                    "role": "connectivity",
                    "model": model,
                    "timestamp": now,
                    "suites": {"connectivity": {"ok": False}},
                }
                for provider_id, model in {
                    "qwen-a": "qwen3.6-27b-gpu0",
                    "qwen-b": "qwen3.6-27b-gpu1",
                }.items()
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index_candidates",
                return_value=[],
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": None},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        director_status = response["roles"]["director"]
        assert director_status["ready"] is False
        assert director_status["degraded"] is False
        assert response["state"] == "BLOCKED"
        assert response["blocked_roles"] == ["director"]
        assert len(director_status["skipped_bindings"]) == 2

    def test_llm_status_overlays_new_runtime_dispatch_skips_over_stale_provider_success(self, tmp_path):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = str(tmp_path)
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = False

        stale_timestamp = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        config_payload = {
            "schema_version": 2,
            "providers": {
                "kimi": {"type": "anthropic_compat", "model": "kimi-for-coding"},
                "qwen-a": {"type": "openai_compat", "model": "qwen3.6-27b-q6-code-gpu0"},
                "qwen-b": {"type": "openai_compat", "model": "qwen3.6-27b-q6-code-gpu1"},
            },
            "roles": {
                "architect": {"provider_id": "kimi", "model": "kimi-for-coding"},
                "pm": {"provider_id": "kimi", "model": "kimi-for-coding"},
                "director": {
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-q6-code-gpu0",
                    "bindings": [
                        {"provider_id": "qwen-a", "model": "qwen3.6-27b-q6-code-gpu0"},
                        {"provider_id": "qwen-b", "model": "qwen3.6-27b-q6-code-gpu1"},
                    ],
                },
            },
            "policies": {"required_ready_roles": ["director"]},
        }
        index_payload = {
            "roles": {
                role: {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "kimi",
                    "model": "kimi-for-coding",
                    "timestamp": stale_timestamp,
                }
                for role in ("architect", "pm")
            },
            "providers": {
                provider_id: {
                    "ready": True,
                    "grade": "PASS",
                    "role": "connectivity",
                    "model": model,
                    "timestamp": stale_timestamp,
                }
                for provider_id, model in {
                    "qwen-a": "qwen3.6-27b-q6-code-gpu0",
                    "qwen-b": "qwen3.6-27b-q6-code-gpu1",
                }.items()
            },
        }
        dispatch_path = Path(resolve_runtime_path(str(tmp_path), "runtime/dispatch/log.json"))
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "metadata": {
                        "active_binding_count": 0,
                        "readiness_skipped_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "qwen-a",
                                "model": "qwen3.6-27b-q6-code-gpu0",
                                "binding_id": "director:0:qwen-a:qwen3.6-27b-q6-code-gpu0",
                                "status": "skipped",
                                "skipped": True,
                                "skip_reason": "provider_unreachable",
                            },
                            {
                                "provider_id": "qwen-b",
                                "model": "qwen3.6-27b-q6-code-gpu1",
                                "binding_id": "director:1:qwen-b:qwen3.6-27b-q6-code-gpu1",
                                "status": "skipped",
                                "skipped": True,
                                "skip_reason": "provider_unreachable",
                            },
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index_candidates",
                return_value=[],
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": None},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value=str(tmp_path / "cache"),
            ),
        ):
            response = build_llm_status(mock_settings)

        director_status = response["roles"]["director"]
        assert director_status["ready"] is False
        assert director_status["degraded"] is False
        assert director_status["readiness_source"] == "runtime_dispatch"
        assert director_status["readiness_issue"] == (
            "all Director bindings unavailable after runtime dispatch readiness filtering"
        )
        assert response["state"] == "BLOCKED"
        assert response["factory_state"] == "BLOCKED"
        assert response["blocked_roles"] == ["director"]
        assert response["factory_blocked_roles"] == ["director"]
        assert [binding["readiness_source"] for binding in director_status["bindings"]] == [
            "runtime_dispatch",
            "runtime_dispatch",
        ]
        assert [binding["binding_id"] for binding in director_status["bindings"]] == [
            "director:0:qwen-a:qwen3.6-27b-q6-code-gpu0",
            "director:1:qwen-b:qwen3.6-27b-q6-code-gpu1",
        ]
        assert {binding["skip_reason"] for binding in director_status["bindings"]} == {"provider_unreachable"}
        assert director_status["skipped_bindings"] == [
            {
                "provider_id": "qwen-a",
                "model": "qwen3.6-27b-q6-code-gpu0",
                "binding_id": "director:0:qwen-a:qwen3.6-27b-q6-code-gpu0",
                "reason": "provider_unreachable",
                "readiness_source": "runtime_dispatch",
            },
            {
                "provider_id": "qwen-b",
                "model": "qwen3.6-27b-q6-code-gpu1",
                "binding_id": "director:1:qwen-b:qwen3.6-27b-q6-code-gpu1",
                "reason": "provider_unreachable",
                "readiness_source": "runtime_dispatch",
            },
        ]
        assert response["providers"]["qwen-a"]["ready"] is False
        assert response["providers"]["qwen-a"]["readiness_source"] == "runtime_dispatch"
        assert response["providers"]["qwen-a"]["skip_reason"] == "provider_unreachable"

    def test_llm_status_ignores_runtime_dispatch_skip_older_than_provider_success(self, tmp_path):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = str(tmp_path)
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = False

        now = datetime.now(timezone.utc)
        index_timestamp = now.isoformat()
        old_dispatch_time = now - timedelta(minutes=5)
        config_payload = {
            "schema_version": 2,
            "providers": {
                "qwen-a": {"type": "openai_compat", "model": "qwen3.6-27b-q6-code-gpu0"},
            },
            "roles": {
                "director": {"provider_id": "qwen-a", "model": "qwen3.6-27b-q6-code-gpu0"},
            },
            "policies": {"required_ready_roles": ["director"]},
        }
        index_payload = {
            "roles": {},
            "providers": {
                "qwen-a": {
                    "ready": True,
                    "grade": "PASS",
                    "role": "connectivity",
                    "model": "qwen3.6-27b-q6-code-gpu0",
                    "timestamp": index_timestamp,
                },
            },
        }
        dispatch_path = Path(resolve_runtime_path(str(tmp_path), "runtime/dispatch/log.json"))
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "metadata": {
                        "active_binding_count": 0,
                        "per_binding": [
                            {
                                "provider_id": "qwen-a",
                                "model": "qwen3.6-27b-q6-code-gpu0",
                                "status": "skipped",
                                "skipped": True,
                                "skip_reason": "provider_unreachable",
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        os.utime(dispatch_path, (old_dispatch_time.timestamp(), old_dispatch_time.timestamp()))

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index_candidates",
                return_value=[],
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={"lastUpdated": None},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value=str(tmp_path / "cache"),
            ),
        ):
            response = build_llm_status(mock_settings)

        director_status = response["roles"]["director"]
        assert director_status["ready"] is True
        assert director_status["readiness_source"] == "provider_index"
        assert director_status["skipped_bindings"] == []
        assert response["providers"]["qwen-a"]["ready"] is True
        assert response["providers"]["qwen-a"]["readiness_source"] is None


class TestRoleRuntimeSupportConsistency:
    """Keep llm/status and director runtime gate aligned on provider support."""

    def test_llm_status_marks_director_codex_as_supported(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli", "codex_exec": {"sandbox": "workspace-write"}},
            },
            "roles": {
                "director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            },
            "policies": {
                "required_ready_roles": ["director"],
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value={"providers": {}, "roles": {}},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["director"]["runtime_supported"] is True
        assert response["roles"]["director"]["runtime_issue"] == ""
        assert "director" not in response["unsupported_roles"]

    def test_llm_status_marks_director_codex_read_only_sandbox_as_unsupported(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        now = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli", "codex_exec": {"sandbox": "read-only"}},
            },
            "roles": {
                "director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            },
            "policies": {
                "required_ready_roles": ["director"],
            },
        }
        index_payload = {
            "roles": {
                "director": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "codex_cli",
                    "model": "gpt-5.3-codex",
                    "timestamp": now,
                }
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["director"]["ready"] is True
        assert response["roles"]["director"]["runtime_supported"] is False
        assert response["roles"]["director"]["runtime_issue"] == "director_codex_read_only_sandbox"
        assert response["unsupported_roles"] == ["director"]

    def test_llm_status_blocks_stale_role_model_readiness(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "minimax-1": {"type": "minimax"},
            },
            "roles": {
                "pm": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        stale_index = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "minimax-1",
                    "model": "MiniMax-M2.5",
                },
            },
            "providers": {
                "minimax-1": {
                    "ready": True,
                    "grade": "PASS",
                    "model": "MiniMax-M2.5",
                    "role": "pm",
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=stale_index,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["pm"]["ready"] is False
        assert response["roles"]["pm"]["readiness_issue"] == "model_mismatch"
        assert response["roles"]["pm"]["tested_model"] == "MiniMax-M2.5"
        assert response["blocked_roles"] == ["pm"]
        assert response["state"] == "BLOCKED"

    def test_llm_status_allows_old_successful_readiness_timestamp(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "qwen-main": {"type": "openai_compat", "name": "Qwen Production"},
            },
            "roles": {
                "pm": {"provider_id": "qwen-main", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "qwen-main",
                    "model": "Qwen3-Max",
                    "timestamp": "2000-01-01T00:00:00+00:00",
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["pm"]["ready"] is True
        assert response["roles"]["pm"]["readiness_issue"] == ""
        assert response["roles"]["pm"]["tested_provider_id"] == "qwen-main"
        assert response["roles"]["pm"]["tested_model"] == "Qwen3-Max"
        assert response["roles"]["pm"]["tested_timestamp"] == "2000-01-01T00:00:00+00:00"
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_prefers_current_binding_candidate_over_workspace_mismatch(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli", "name": "Codex CLI"},
                "deepseek": {"type": "anthropic_compat", "name": "DeepSeek"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        workspace_index = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "deepseek",
                    "model": "deepseek-v4-pro",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            },
            "providers": {},
        }
        global_index = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "codex_cli",
                    "model": "gpt-5.3-codex",
                    "timestamp": "2000-01-01T00:00:00+00:00",
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=workspace_index,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index_candidates",
                return_value=[workspace_index, global_index],
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        pm = response["roles"]["pm"]
        assert pm["ready"] is True
        assert pm["readiness_issue"] == ""
        assert pm["tested_provider_id"] == "codex_cli"
        assert pm["tested_model"] == "gpt-5.3-codex"
        assert pm["tested_timestamp"] == "2000-01-01T00:00:00+00:00"
        assert response["blocked_roles"] == []

    def test_llm_status_prefers_old_role_specific_success_over_provider_role_mismatch(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "deepseek-main": {"type": "anthropic_compat", "name": "DeepSeek Main"},
            },
            "roles": {
                "architect": {"provider_id": "deepseek-main", "model": "deepseek-v4-pro"},
            },
            "policies": {
                "required_ready_roles": ["architect"],
            },
        }
        index_payload = {
            "roles": {
                "architect": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "deepseek-main",
                    "model": "deepseek-v4-pro",
                    "timestamp": "2000-01-01T00:00:00+00:00",
                },
            },
            "providers": {
                "deepseek-main": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "deepseek-main",
                    "model": "deepseek-v4-pro",
                    "role": "qa",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        architect = response["roles"]["architect"]
        assert architect["ready"] is True
        assert architect["readiness_issue"] == ""
        assert architect["readiness_source"] == "role_index"
        assert architect["tested_provider_id"] == "deepseek-main"
        assert architect["tested_model"] == "deepseek-v4-pro"
        assert architect["tested_timestamp"] == "2000-01-01T00:00:00+00:00"
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_allows_connectivity_provider_readiness_for_role(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "deepseek-main": {"type": "anthropic_compat", "name": "DeepSeek Main"},
            },
            "roles": {
                "chief_engineer": {"provider_id": "deepseek-main", "model": "deepseek-v4-pro"},
            },
            "policies": {
                "required_ready_roles": ["chief_engineer"],
            },
        }
        index_payload = {
            "roles": {},
            "providers": {
                "deepseek-main": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "deepseek-main",
                    "model": "deepseek-v4-pro",
                    "role": "connectivity",
                    "timestamp": "2000-01-01T00:00:00+00:00",
                    "suites": {"connectivity": {"ok": True}},
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        chief_engineer = response["roles"]["chief_engineer"]
        assert chief_engineer["ready"] is True
        assert chief_engineer["readiness_issue"] == ""
        assert chief_engineer["readiness_source"] == "provider_index"
        assert chief_engineer["tested_provider_id"] == "deepseek-main"
        assert chief_engineer["tested_model"] == "deepseek-v4-pro"
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_reports_failed_role_readiness_with_provider_model_and_timestamp(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        tested_at = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli", "name": "Codex CLI"},
            },
            "roles": {
                "director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            },
            "policies": {
                "required_ready_roles": ["director"],
            },
        }
        index_payload = {
            "roles": {
                "director": {
                    "ready": False,
                    "grade": "FAIL",
                    "provider_id": "codex_cli",
                    "model": "gpt-5.3-codex",
                    "timestamp": tested_at,
                    "suites": {
                        "connectivity": {"ok": True},
                        "response": {"ok": False},
                    },
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        director = response["roles"]["director"]
        assert director["ready"] is False
        assert director["readiness_issue"] == "readiness_failed"
        assert director["readiness_source"] == "role_index"
        assert director["tested_provider_id"] == "codex_cli"
        assert director["tested_model"] == "gpt-5.3-codex"
        assert director["tested_timestamp"] == tested_at
        assert response["blocked_roles"] == ["director"]
        assert response["state"] == "BLOCKED"

    def test_llm_status_allows_case_only_model_readiness_match(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3-max",
                },
            },
            "providers": {
                "openai_compat-1": {
                    "ready": True,
                    "grade": "PASS",
                    "model": "qwen3-max",
                    "role": "pm",
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["pm"]["ready"] is True
        assert response["roles"]["pm"]["readiness_issue"] == ""
        assert response["roles"]["pm"]["tested_model"] == "qwen3-max"
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_allows_qwen_separator_variant_model_readiness_match(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3 max",
                },
            },
            "providers": {
                "openai_compat-1": {
                    "ready": True,
                    "grade": "PASS",
                    "model": "qwen3_max",
                    "role": "pm",
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["pm"]["ready"] is True
        assert response["roles"]["pm"]["readiness_issue"] == ""
        assert response["roles"]["pm"]["tested_model"] == "qwen3 max"
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_canonicalizes_required_roles_before_blocking(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = False

        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": [" PM ", "pm", "docs", "qa"],
            },
        }
        index_payload = {
            "roles": {
                "PM": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3 max",
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["required_ready_roles"] == ["pm"]
        assert response["roles"]["pm"]["ready"] is True
        assert response["blocked_roles"] == []
        assert response["unsupported_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_blocks_factory_when_director_runtime_cannot_enforce_tool_choice(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "deepseek-anthropic": {
                    "type": "anthropic_compat",
                    "base_url": "https://api.deepseek.com/anthropic",
                    "model": "deepseek-v4-pro",
                },
            },
            "roles": {
                "architect": {"provider_id": "deepseek-anthropic", "model": "deepseek-v4-pro"},
                "pm": {"provider_id": "deepseek-anthropic", "model": "deepseek-v4-pro"},
                "director": {"provider_id": "deepseek-anthropic", "model": "deepseek-v4-pro"},
                "qa": {"provider_id": "deepseek-anthropic", "model": "deepseek-v4-pro"},
            },
            "policies": {
                "required_ready_roles": ["pm", "director", "qa"],
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        index_payload = {
            "roles": {
                role: {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "deepseek-anthropic",
                    "model": "deepseek-v4-pro",
                    "timestamp": now,
                }
                for role in ("architect", "pm", "director", "qa")
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["director"]["ready"] is True
        assert response["roles"]["director"]["runtime_supported"] is False
        assert response["unsupported_roles"] == ["director"]
        assert response["factory_unsupported_roles"] == ["director"]
        assert response["factory_state"] == "BLOCKED"
        assert response["state"] == "BLOCKED"

    def test_llm_status_blocks_unverified_minimax_director_runtime(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "minimax-1": {
                    "type": "minimax",
                    "base_url": "https://api.minimaxi.com/v1",
                    "model": "MiniMax-M2.7-highspeed",
                },
            },
            "roles": {
                "architect": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"},
                "pm": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"},
                "director": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"},
                "qa": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"},
            },
            "policies": {
                "required_ready_roles": ["pm", "director", "qa"],
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        index_payload = {
            "roles": {
                role: {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "minimax-1",
                    "model": "MiniMax-M2.7-highspeed",
                    "timestamp": now,
                }
                for role in ("architect", "pm", "director", "qa")
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["director"]["ready"] is True
        assert response["roles"]["director"]["runtime_supported"] is False
        assert response["unsupported_roles"] == ["director"]
        assert response["factory_unsupported_roles"] == ["director"]
        assert response["factory_state"] == "BLOCKED"
        assert response["state"] == "BLOCKED"

    def test_llm_status_defaults_qa_enabled_for_minimal_settings_objects(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        settings = SimpleNamespace(workspace="/tmp/test_workspace", ramdisk_root=None)
        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
                "qa": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm", "qa"],
            },
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3-max",
                },
                "qa": {
                    "ready": True,
                    "grade": "PASS",
                    "provider_id": "openai_compat-1",
                    "model": "qwen3-max",
                },
            },
            "providers": {},
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(settings)

        assert response["required_ready_roles"] == ["pm", "qa"]
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_llm_status_uses_provider_readiness_when_pm_role_index_is_missing(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True

        config_payload = {
            "schema_version": 1,
            "providers": {
                "openai_compat-1": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            },
            "policies": {
                "required_ready_roles": ["pm"],
            },
        }
        index_payload = {
            "roles": {},
            "providers": {
                "openai_compat-1": {
                    "ready": True,
                    "grade": "PASS",
                    "model": "qwen/qwen3-max",
                    "role": "PM",
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/test_cache",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["roles"]["pm"]["ready"] is True
        assert response["roles"]["pm"]["readiness_issue"] == ""
        assert response["roles"]["pm"]["readiness_source"] == "provider_index"
        assert response["roles"]["pm"]["tested_model"] == "qwen/qwen3-max"
        assert response["blocked_roles"] == []
        assert response["state"] == "READY"

    def test_director_gate_allows_codex_and_generic_provider(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        base_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            }
        }

        codex_cfg = {
            "providers": {"codex_cli": {"type": "codex_cli", "codex_exec": {"sandbox": "workspace-write"}}},
            "roles": {"director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"}},
        }
        generic_cfg = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"director": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=base_index),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=codex_cfg),
        ):
            _ensure_llm_ready(mock_state, "director")

        generic_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
            }
        }
        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=generic_cfg),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=generic_index),
        ):
            _ensure_llm_ready(mock_state, "director")

    def test_director_gate_blocks_codex_read_only_sandbox(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {
                "director": {"ready": True, "provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            }
        }
        config_payload = {
            "providers": {"codex_cli": {"type": "codex_cli", "codex_exec": {"sandbox": "read-only"}}},
            "roles": {"director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            pytest.raises(HTTPException) as exc,
        ):
            _ensure_llm_ready(mock_state, "director")

        assert exc.value.status_code == 409
        assert "director_codex_read_only_sandbox" in str(exc.value.detail)

    def test_pm_gate_allows_ready_role_without_provider_type_restriction(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        base_index = {
            "roles": {
                "pm": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
            }
        }

        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=base_index),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
        ):
            _ensure_llm_ready(mock_state, "pm")

    def test_pm_gate_allows_case_only_model_readiness_match(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {
                "pm": {"ready": True, "provider_id": "openai_compat", "model": "qwen3-max"},
            }
        }
        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "Qwen3-Max"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
        ):
            _ensure_llm_ready(mock_state, "pm")

    def test_pm_gate_allows_qwen_separator_variant_model_readiness_match(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {
                "pm": {"ready": True, "provider_id": "openai_compat", "model": "qwen3 max"},
            }
        }
        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "Qwen3-Max"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
        ):
            _ensure_llm_ready(mock_state, "pm")

    def test_pm_gate_uses_provider_readiness_when_role_index_is_missing(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {},
            "providers": {
                "openai_compat": {
                    "ready": True,
                    "provider_id": "openai_compat",
                    "model": "qwen/qwen3-max",
                    "role": "PM",
                },
            },
        }
        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "Qwen3-Max"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
        ):
            _ensure_llm_ready(mock_state, "pm")

    def test_pm_gate_allows_connectivity_provider_readiness_when_role_index_is_missing(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {},
            "providers": {
                "deepseek-main": {
                    "ready": True,
                    "provider_id": "deepseek-main",
                    "model": "deepseek-v4-pro",
                    "role": "connectivity",
                },
            },
        }
        config_payload = {
            "providers": {"deepseek-main": {"type": "anthropic_compat"}},
            "roles": {"chief_engineer": {"provider_id": "deepseek-main", "model": "deepseek-v4-pro"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
        ):
            _ensure_llm_ready(mock_state, "chief_engineer")

    def test_pm_gate_allows_old_successful_readiness_timestamp(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "provider_id": "openai_compat",
                    "model": "Qwen3-Max",
                    "timestamp": "2000-01-01T00:00:00+00:00",
                },
            }
        }
        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "Qwen3-Max"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
        ):
            _ensure_llm_ready(mock_state, "pm")

    def test_pm_gate_prefers_current_binding_candidate_over_workspace_mismatch(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        workspace_index = {
            "roles": {
                "pm": {
                    "ready": True,
                    "provider_id": "deepseek",
                    "model": "deepseek-v4-pro",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        }
        global_index = {
            "roles": {
                "pm": {
                    "ready": True,
                    "provider_id": "codex_cli",
                    "model": "gpt-5.3-codex",
                    "timestamp": "2000-01-01T00:00:00+00:00",
                },
            }
        }
        config_payload = {
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "deepseek": {"type": "anthropic_compat"},
            },
            "roles": {"pm": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=workspace_index),
            patch(
                "polaris.delivery.http.routers._shared.load_llm_test_index_candidates",
                return_value=[workspace_index, global_index],
            ),
        ):
            _ensure_llm_ready(mock_state, "pm")

    def test_role_gate_reports_failed_readiness_for_current_provider_model(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        tested_at = datetime.now(timezone.utc).isoformat()
        index_payload = {
            "roles": {
                "director": {
                    "ready": False,
                    "provider_id": "codex_cli",
                    "model": "gpt-5.3-codex",
                    "timestamp": tested_at,
                },
            }
        }
        config_payload = {
            "providers": {"codex_cli": {"type": "codex_cli"}},
            "roles": {"director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            pytest.raises(HTTPException) as exc,
        ):
            _ensure_llm_ready(mock_state, "director")

        assert exc.value.status_code == 409
        detail = str(exc.value.detail)
        assert "readiness failed" in detail
        assert "codex_cli" in detail
        assert "gpt-5.3-codex" in detail
        assert tested_at in detail

    def test_role_gate_prefers_active_workspace_path_for_llm_config(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/stale_repo"
        mock_settings.workspace_path = "/tmp/active_project"
        mock_settings.ramdisk_root = "/tmp/ram"
        mock_state = AppState(settings=mock_settings)

        index_payload = {
            "roles": {
                "pm": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
            }
        }
        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/cache") as cache_root,
            patch(
                "polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload
            ) as load_index,
            patch(
                "polaris.delivery.http.routers._shared.llm_config.load_llm_config",
                return_value=config_payload,
            ) as load_config,
        ):
            _ensure_llm_ready(mock_state, "pm")

        cache_root.assert_called_once_with("/tmp/ram", "/tmp/active_project")
        load_config.assert_called_once_with("/tmp/active_project", "/tmp/cache", settings=mock_settings)
        load_index.assert_called_once_with("/tmp/active_project")

    def test_required_roles_live_check_blocks_current_provider_failure(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True
        mock_state = AppState(settings=mock_settings)

        now = datetime.now(timezone.utc).isoformat()
        index_payload = {
            "roles": {
                "qa": {
                    "ready": True,
                    "provider_id": "anthropic_compat-1",
                    "model": "kimi-for-coding",
                    "timestamp": now,
                },
            }
        }
        config_payload = {
            "providers": {"anthropic_compat-1": {"type": "anthropic_compat"}},
            "roles": {"qa": {"provider_id": "anthropic_compat-1", "model": "kimi-for-coding"}},
            "policies": {"required_ready_roles": ["qa"]},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch(
                "polaris.delivery.http.routers._shared.run_connectivity_suite_sync",
                return_value={"ok": False, "error": "circuit_open:45s_remaining"},
            ) as live_check,
            pytest.raises(HTTPException) as exc,
        ):
            ensure_required_roles_ready(mock_state, default_roles=["qa"], force_roles=["qa"], live_check=True)

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "RUNTIME_ROLES_NOT_READY"
        assert exc.value.detail["details"]["missing_roles"] == ["qa"]
        assert "live LLM connectivity failed" in exc.value.detail["details"]["role_issues"]["qa"]
        assert "circuit_open" in exc.value.detail["details"]["role_issues"]["qa"]
        live_check.assert_called_once()

    def test_llm_status_prefers_active_workspace_path_for_readiness(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/stale_repo"
        mock_settings.workspace_path = "/tmp/active_project"
        mock_settings.ramdisk_root = "/tmp/ram"
        mock_settings.qa_enabled = False

        config_payload = {
            "schema_version": 1,
            "providers": {"openai_compat-1": {"type": "openai_compat"}},
            "roles": {
                "pm": {"provider_id": "openai_compat-1", "model": "qwen3-max"},
            },
            "policies": {"required_ready_roles": ["pm"]},
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "provider_id": "openai_compat-1",
                    "model": "qwen3-max",
                    "grade": "PASS",
                },
            },
            "providers": {
                "openai_compat-1": {
                    "ready": True,
                    "grade": "PASS",
                    "model": "qwen3-max",
                    "role": "pm",
                },
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
                return_value="/tmp/cache",
            ) as cache_root,
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ) as load_config,
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ) as load_index,
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.llm_config_path",
                return_value="/tmp/cache/llm_config.json",
            ) as config_path,
        ):
            response = build_llm_status(mock_settings)

        cache_root.assert_called_once_with("/tmp/ram", "/tmp/active_project")
        load_config.assert_called_once_with("/tmp/active_project", "/tmp/cache", settings=mock_settings)
        load_index.assert_called_once_with("/tmp/active_project")
        config_path.assert_called_once_with("/tmp/active_project", "/tmp/cache")
        assert response["state"] == "READY"
        assert response["blocked_roles"] == []
        assert response["roles"]["pm"]["ready"] is True

    def test_llm_status_exposes_full_factory_readiness_separately_from_policy_readiness(self):
        from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/factory_project"
        mock_settings.ramdisk_root = "/tmp/ram"
        mock_settings.qa_enabled = True

        now = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "schema_version": 1,
            "providers": {
                "deepseek": {"type": "anthropic_compat"},
                "kimi": {"type": "anthropic_compat"},
            },
            "roles": {
                "architect": {"provider_id": "kimi", "model": "kimi-for-coding"},
                "pm": {"provider_id": "deepseek", "model": "deepseek-v4-pro"},
                "director": {"provider_id": "deepseek", "model": "deepseek-v4-pro"},
                "qa": {"provider_id": "kimi", "model": "kimi-for-coding"},
            },
            "policies": {"required_ready_roles": ["pm", "director", "qa"]},
        }
        index_payload = {
            "roles": {
                "pm": {
                    "ready": True,
                    "provider_id": "deepseek",
                    "model": "deepseek-v4-pro",
                    "timestamp": now,
                },
                "director": {
                    "ready": True,
                    "provider_id": "deepseek",
                    "model": "deepseek-v4-pro",
                    "timestamp": now,
                },
                "qa": {
                    "ready": True,
                    "provider_id": "kimi",
                    "model": "kimi-for-coding",
                    "timestamp": now,
                },
            },
            "providers": {},
        }

        with (
            patch("polaris.cells.runtime.projection.internal.llm_status.build_cache_root", return_value="/tmp/cache"),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
                return_value=config_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_llm_test_index",
                return_value=index_payload,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.load_interview_history_summary",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.llm_status.llm_config.llm_config_path",
                return_value="/tmp/cache/llm_config.json",
            ),
        ):
            response = build_llm_status(mock_settings)

        assert response["state"] == "READY"
        assert response["blocked_roles"] == []
        assert response["factory_required_roles"] == ["architect", "pm", "director", "qa"]
        assert response["factory_state"] == "BLOCKED"
        assert response["factory_blocked_roles"] == ["architect"]
        assert response["roles"]["architect"]["readiness_issue"] == "role_readiness_missing"

    def test_required_ready_roles_prefers_active_workspace_path_for_policy(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import required_ready_roles

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/stale_repo"
        mock_settings.workspace_path = "/tmp/active_project"
        mock_settings.ramdisk_root = "/tmp/ram"
        mock_settings.qa_enabled = True
        mock_state = AppState(settings=mock_settings)

        config_payload = {
            "policies": {"required_ready_roles": ["pm", "director"]},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/cache") as cache_root,
            patch(
                "polaris.delivery.http.routers._shared.llm_config.load_llm_config",
                return_value=config_payload,
            ) as load_config,
        ):
            roles = required_ready_roles(mock_state, default_roles=["qa"])

        assert roles == ["pm", "director"]
        cache_root.assert_called_once_with("/tmp/ram", "/tmp/active_project")
        load_config.assert_called_once_with("/tmp/active_project", "/tmp/cache", settings=mock_settings)

    def test_director_start_requires_all_required_roles(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True
        mock_state = AppState(settings=mock_settings)

        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {
                "pm": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "director": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "qa": {"provider_id": "openai_compat", "model": "gpt-4.1"},
            },
            "policies": {"required_ready_roles": ["pm", "director", "qa"]},
        }
        index_payload = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
                "qa": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
            }
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            pytest.raises(HTTPException) as exc,
        ):
            ensure_required_roles_ready(mock_state, default_roles=["director", "qa"], force_first="director")

        assert exc.value.status_code == 409
        assert "pm" in exc.value.detail["details"]["missing_roles"]

    def test_director_start_skips_unavailable_multi_binding_when_another_binding_is_ready(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = False
        mock_state = AppState(settings=mock_settings)

        now = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "providers": {
                "qwen-a": {"type": "openai_compat"},
                "qwen-b": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu1",
                    "bindings": [
                        {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu1"},
                        {"provider_id": "qwen-b", "model": "qwen3.6-27b-gpu0"},
                    ],
                },
            },
            "policies": {"required_ready_roles": ["director"]},
        }
        index_payload = {
            "roles": {
                "director": {
                    "ready": True,
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu1",
                    "timestamp": now,
                },
            },
            "providers": {
                "qwen-b": {
                    "ready": False,
                    "grade": "FAIL",
                    "role": "connectivity",
                    "model": "qwen3.6-27b-gpu0",
                    "timestamp": now,
                    "suites": {"connectivity": {"ok": False}},
                }
            },
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            ensure_required_roles_ready(mock_state, default_roles=["director"], force_roles=["director"])

    def test_director_start_blocks_when_all_multi_bindings_are_unavailable(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = False
        mock_state = AppState(settings=mock_settings)

        now = datetime.now(timezone.utc).isoformat()
        config_payload = {
            "providers": {
                "qwen-a": {"type": "openai_compat"},
                "qwen-b": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "qwen-a",
                    "model": "qwen3.6-27b-gpu1",
                    "bindings": [
                        {"provider_id": "qwen-a", "model": "qwen3.6-27b-gpu1"},
                        {"provider_id": "qwen-b", "model": "qwen3.6-27b-gpu0"},
                    ],
                },
            },
            "policies": {"required_ready_roles": ["director"]},
        }
        index_payload = {
            "roles": {},
            "providers": {
                provider_id: {
                    "ready": False,
                    "grade": "FAIL",
                    "role": "connectivity",
                    "model": model,
                    "timestamp": now,
                    "suites": {"connectivity": {"ok": False}},
                }
                for provider_id, model in {
                    "qwen-a": "qwen3.6-27b-gpu1",
                    "qwen-b": "qwen3.6-27b-gpu0",
                }.items()
            },
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
            pytest.raises(HTTPException) as exc,
        ):
            ensure_required_roles_ready(mock_state, default_roles=["director"], force_roles=["director"])

        assert exc.value.status_code == 409
        assert "all bindings unavailable" in exc.value.detail["details"]["role_issues"]["director"]

    def test_pm_start_requires_all_required_roles(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import ensure_required_roles_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_settings.qa_enabled = True
        mock_state = AppState(settings=mock_settings)

        config_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {
                "pm": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "director": {"provider_id": "openai_compat", "model": "gpt-4.1"},
                "qa": {"provider_id": "openai_compat", "model": "gpt-4.1"},
            },
            "policies": {"required_ready_roles": ["pm", "director", "qa"]},
        }
        index_payload = {
            "roles": {
                "pm": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
                "director": {"ready": True, "provider_id": "openai_compat", "model": "gpt-4.1"},
            }
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=index_payload),
            pytest.raises(HTTPException) as exc,
        ):
            ensure_required_roles_ready(mock_state, default_roles=["pm", "director", "qa"])

        assert exc.value.status_code == 409
        assert "qa" in exc.value.detail["details"]["missing_roles"]
        assert "qa" in exc.value.detail["details"]["role_issues"]

    def test_pm_gate_blocks_stale_ready_model(self):
        from polaris.cells.runtime.state_owner.internal.state import AppState
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        mock_state = AppState(settings=mock_settings)

        config_payload = {
            "providers": {"minimax-1": {"type": "minimax"}},
            "roles": {"pm": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"}},
        }
        stale_index = {
            "roles": {
                "pm": {"ready": True, "provider_id": "minimax-1", "model": "MiniMax-M2.5"},
            },
            "providers": {
                "minimax-1": {"ready": True, "model": "MiniMax-M2.5"},
            },
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=stale_index),
            pytest.raises(HTTPException) as exc,
        ):
            _ensure_llm_ready(mock_state, "pm")

        assert exc.value.status_code == 409
        assert "MiniMax-M2.5" in str(exc.value.detail)
        assert "MiniMax-M2.7-highspeed" in str(exc.value.detail)


class TestLLMConfigAtomicWrite:
    """Test LLMConfig atomic write and UTF-8 roundtrip."""

    def test_config_save_and_load_utf8_roundtrip(self, mock_workspace):
        """Verify config can be saved and loaded with UTF-8 characters preserved."""
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        test_config = {
            "schema_version": 1,
            "providers": {
                "test_provider": {
                    "type": "openai_compat",
                    "name": "测试提供商",
                    "base_url": "https://api.test.com",
                    "api_key": "test_key_123",
                    "model": "test-model",
                    "description": "包含中文描述的配置",
                },
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "test_provider", "model": "test-model", "profile": "测试角色配置"},
                "director": {"provider_id": "ollama", "model": "test-model"},
                "qa": {"provider_id": "ollama", "model": "test-model"},
                "docs": {"provider_id": "openai_compat", "model": "test-model"},
            },
        }

        save_llm_config(mock_workspace, mock_workspace, test_config)

        loaded_config = load_llm_config(mock_workspace, mock_workspace)

        assert loaded_config.get("providers", {}).get("test_provider", {}).get("name") == "测试提供商", (
            "UTF-8 Chinese characters not preserved in provider name"
        )
        assert loaded_config.get("roles", {}).get("pm", {}).get("profile") == "测试角色配置", (
            "UTF-8 Chinese characters not preserved in role profile"
        )

    def test_config_atomic_write_pattern(self, mock_workspace):
        """Verify config uses atomic write pattern (tmp -> fsync -> rename)."""
        from polaris.kernelone.llm.config_store import llm_config_path, save_llm_config

        test_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "test-model"},
                "director": {"provider_id": "ollama", "model": "test-model"},
                "qa": {"provider_id": "ollama", "model": "test-model"},
                "docs": {"provider_id": "openai_compat", "model": "test-model"},
            },
        }

        config_path = llm_config_path(mock_workspace, mock_workspace)

        save_llm_config(mock_workspace, mock_workspace, test_config)

        assert os.path.isfile(config_path), "Config file was not created"

        assert not os.path.exists(config_path + ".tmp"), f"Found temporary file after write: {config_path}.tmp"

        with open(config_path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["schema_version"] == 2


class TestLLMVisualLayoutPersistence:
    """Test visual layout fields are preserved in config normalization and persistence."""

    def test_save_and_load_preserves_visual_fields(self, mock_workspace):
        """visual_layout / visual_node_states / visual_viewport should survive save+load."""
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        payload = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "gpt-4.1"},
                "director": {"provider_id": "ollama", "model": "glm-4.7-flash:latest"},
                "qa": {"provider_id": "ollama", "model": "glm-4.7-flash:latest"},
                "docs": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
            },
            "visual_layout": {
                "role:pm": {"x": 410.5, "y": 122.25},
                "provider:codex_cli": {"x": 84, "y": 48},
            },
            "visual_node_states": {
                "role:pm": {
                    "position": {"x": 410.5, "y": 122.25},
                    "selected": True,
                    "hidden": False,
                }
            },
            "visual_viewport": {"x": -20, "y": 16, "zoom": 1.15},
        }

        save_llm_config(mock_workspace, mock_workspace, payload)
        loaded = load_llm_config(mock_workspace, mock_workspace)

        assert loaded.get("visual_layout", {}).get("role:pm", {}).get("x") == 410.5
        assert loaded.get("visual_layout", {}).get("role:pm", {}).get("y") == 122.25
        assert loaded.get("visual_node_states", {}).get("role:pm", {}).get("position", {}).get("x") == 410.5
        assert loaded.get("visual_viewport", {}).get("zoom") == 1.15


class TestLLMConfigProviderDeletionPersistence:
    """Regression tests for user-deleted provider persistence."""

    def test_save_with_provider_removed_does_not_restore_old_provider(self, mock_workspace):
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        original_payload = {
            "schema_version": 2,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
                "openai_compat": {
                    "type": "openai_compat",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "old-secret",
                },
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"},
                "chief_engineer": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
                "director": {"provider_id": "ollama", "model": "llama3.2"},
                "qa": {"provider_id": "ollama", "model": "llama3.2"},
                "architect": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
            },
        }
        save_llm_config(mock_workspace, mock_workspace, original_payload)

        updated_payload = {
            "schema_version": 2,
            "providers": {
                "openai_compat": {
                    "type": "openai_compat",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "********",
                },
            },
            "roles": {
                "pm": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
                "chief_engineer": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
                "director": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
                "qa": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
                "architect": {"provider_id": "openai_compat", "model": "gpt-4.1-mini"},
            },
        }

        save_llm_config(mock_workspace, mock_workspace, updated_payload)
        loaded = load_llm_config(mock_workspace, mock_workspace)

        providers = loaded.get("providers", {})
        assert set(providers) == {"openai_compat"}
        assert "codex_cli" not in providers
        assert "ollama" not in providers
        assert providers["openai_compat"]["api_key"] == "old-secret"


class TestLLMConfigLoadPreservesUserFields:
    """Ensure loading config never rewrites or drops user-managed fields."""

    def test_load_llm_config_preserves_role_assignments_and_file_content(self, mock_workspace):
        from polaris.kernelone.llm.config_store import llm_config_path, load_llm_config

        path = llm_config_path(mock_workspace, mock_workspace)
        payload = {
            "schema_version": 1,
            "providers": {
                "minimax": {"type": "minimax"},
            },
            "roleAssignments": [
                {"roleId": "pm", "providerId": "minimax", "model": "MiniMax-M2.5"},
            ],
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        with open(path, encoding="utf-8") as handle:
            before = handle.read()

        loaded = load_llm_config(mock_workspace, mock_workspace)

        with open(path, encoding="utf-8") as handle:
            after = handle.read()

        assert after == before, "load_llm_config must not rewrite user config file"
        assert isinstance(loaded.get("roleAssignments"), list)
        assert loaded["roleAssignments"][0]["providerId"] == "minimax"

    def test_load_llm_config_accepts_utf8_bom_file_without_default_fallback(self, mock_workspace):
        from polaris.kernelone.llm.config_store import llm_config_path, load_llm_config

        path = llm_config_path(mock_workspace, mock_workspace)
        payload = {
            "schema_version": 2,
            "providers": {
                "codex_cli": {"type": "codex_cli", "codex_exec": {"sandbox": "workspace-write"}},
            },
            "roles": {
                "director": {"provider_id": "codex_cli", "model": "gpt-5.3-codex"},
            },
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        loaded = load_llm_config(mock_workspace, mock_workspace)

        assert loaded["roles"]["director"]["provider_id"] == "codex_cli"
        assert loaded["roles"]["director"]["model"] == "gpt-5.3-codex"
        assert loaded["providers"]["codex_cli"]["codex_exec"]["sandbox"] == "workspace-write"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestLLMConfigValidation:
    """Test validate_llm_config function for Phase 1 SSOT."""

    def test_valid_config_returns_no_errors(self):
        """Valid config should pass validation."""
        from polaris.kernelone.llm.config_store import build_default_config, validate_llm_config

        config = build_default_config()
        is_valid, errors, _warnings = validate_llm_config(config)

        assert is_valid, f"Valid config failed validation with errors: {errors}"
        assert len(errors) == 0, f"Expected no errors, got: {errors}"

    def test_missing_provider_type_returns_error(self):
        """Config with missing provider type should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {"schema_version": 1, "providers": {"bad_provider": {"name": "Bad Provider"}}, "roles": {}}

        is_valid, errors, _warnings = validate_llm_config(config)

        assert not is_valid, "Config with missing provider type should fail validation"
        assert any("Field required" in str(e) or "missing 'type'" in str(e) for e in errors), (
            f"Expected error about missing type field, got: {errors}"
        )

    def test_role_references_nonexistent_provider_returns_error(self):
        """Role referencing non-existent provider should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 1,
            "providers": {"existing_provider": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "nonexistent_provider", "model": "test-model"}},
        }

        is_valid, errors, _warnings = validate_llm_config(config)

        assert not is_valid, "Config with invalid provider reference should fail"
        assert any("non-existent provider" in e for e in errors), (
            f"Expected error about non-existent provider, got: {errors}"
        )

    def test_required_role_not_defined_returns_error(self):
        """Required role not in roles should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        config = {
            "schema_version": 1,
            "providers": {"test_provider": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "test_provider", "model": "test"}},
            "policies": {"required_ready_roles": ["director", "qa"]},
        }

        is_valid, errors, _warnings = validate_llm_config(config)

        assert not is_valid, "Config with missing required roles should fail"
        assert any("not defined in roles" in e for e in errors), (
            f"Expected error about missing required role, got: {errors}"
        )

    def test_non_dict_config_returns_error(self):
        """Non-dict config should fail validation."""
        from polaris.kernelone.llm.config_store import validate_llm_config

        is_valid, errors, _warnings = validate_llm_config("not a dict")

        assert not is_valid, "Non-dict config should fail validation"
        assert any("must be a dictionary" in e for e in errors), f"Expected error about dict type, got: {errors}"

    def test_provider_id_matches_type_field(self):
        """Provider ID should match the provider's type field."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()

        mismatched_providers = []
        for provider_id, provider_cfg in config.get("providers", {}).items():
            provider_type = provider_cfg.get("type")
            if provider_type and provider_type != provider_id:
                mismatched_providers.append((provider_id, provider_type))

        assert len(mismatched_providers) == 0, (
            f"Found providers where ID doesn't match type field: {mismatched_providers}"
        )

    def test_all_roles_have_valid_provider_reference(self):
        """All roles should reference valid providers."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()

        roles = config.get("roles", {})
        providers = config.get("providers", {})

        invalid_role_refs = []
        for role_id, role_cfg in roles.items():
            if isinstance(role_cfg, dict):
                provider_id = role_cfg.get("provider_id")
                if provider_id and provider_id not in providers:
                    invalid_role_refs.append((role_id, provider_id))

        assert len(invalid_role_refs) == 0, f"Roles with invalid provider references: {invalid_role_refs}"


class TestLLMConfigStandardProviders:
    """Test that standard providers are properly configured."""

    def test_all_required_providers_present(self):
        """Ensure all required providers are defined."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        required_providers = ["ollama", "openai_compat"]
        for required in required_providers:
            assert required in providers, f"Required provider '{required}' not found"

    def test_standard_openai_compat_config(self):
        """Verify openai_compat provider has correct base structure."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        provider = config.get("providers", {}).get("openai_compat")

        assert provider is not None, "openai_compat provider not found"
        assert provider.get("type") == "openai_compat", f"Expected type 'openai_compat', got '{provider.get('type')}'"
        assert "api_path" in provider, "openai_compat missing api_path"
        # models_path is deprecated and removed from default config

    def test_no_duplicate_minimax_entries(self):
        """Ensure no duplicate minimax-related entries exist."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        providers = config.get("providers", {})

        minimax_entries = [k for k in providers if "minimax" in k.lower()]
        minimax_types = [v.get("type") for v in providers.values() if "minimax" in str(v.get("type", "")).lower()]

        assert len(minimax_entries) <= 1, f"Found multiple minimax entries: {minimax_entries}"
        assert len(minimax_types) <= 1, f"Found multiple minimax types: {minimax_types}"


class TestLLMSaveConfigValidation:
    """Test that save_llm_config validates config before saving."""

    def test_save_invalid_config_raises_error(self, mock_workspace):
        """Invalid config should raise ValueError during save."""
        from polaris.kernelone.llm.config_store import save_llm_config

        invalid_config = {"schema_version": 1, "providers": {"bad_provider": {"name": "No type"}}, "roles": {}}

        with pytest.raises(ValueError) as exc_info:
            save_llm_config(mock_workspace, mock_workspace, invalid_config)

        assert "Invalid LLM configuration" in str(exc_info.value)
        assert "missing 'type' field" in str(exc_info.value) or "Field required" in str(exc_info.value)

    def test_save_config_with_invalid_role_reference_raises_error(self, mock_workspace):
        """Config with invalid provider reference should raise ValueError."""
        from polaris.kernelone.llm.config_store import save_llm_config

        invalid_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {"pm": {"provider_id": "nonexistent", "model": "test"}},
        }

        with pytest.raises(ValueError) as exc_info:
            save_llm_config(mock_workspace, mock_workspace, invalid_config)

        assert "non-existent provider" in str(exc_info.value)

    def test_save_valid_config_succeeds(self, mock_workspace):
        """Valid config should be saved without errors."""
        from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

        valid_config = {
            "schema_version": 1,
            "providers": {
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "codex_cli", "model": "test"},
                "director": {"provider_id": "ollama", "model": "test"},
                "qa": {"provider_id": "ollama", "model": "test"},
                "docs": {"provider_id": "openai_compat", "model": "test"},
            },
        }

        result = save_llm_config(mock_workspace, mock_workspace, valid_config)

        assert result is not None
        loaded = load_llm_config(mock_workspace, mock_workspace)
        assert loaded.get("schema_version") == 2


class TestSettingsPersistence:
    """Regression tests for global settings persistence."""

    @staticmethod
    def _write_json(path: str, payload: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _seed_legacy_llm_payload() -> dict:
        return {
            "schema_version": 1,
            "providers": {
                "custom": {"type": "openai_compat", "name": "Custom"},
                "codex_cli": {"type": "codex_cli"},
                "ollama": {"type": "ollama"},
                "openai_compat": {"type": "openai_compat"},
            },
            "roles": {
                "pm": {"provider_id": "custom", "model": "custom-model"},
                "director": {"provider_id": "ollama", "model": "director-model"},
                "qa": {"provider_id": "ollama", "model": "qa-model"},
                "docs": {"provider_id": "openai_compat", "model": "docs-model"},
            },
        }

    def test_save_settings_into_global_config(self, tmp_path, monkeypatch):
        from polaris.bootstrap.config import Settings
        from polaris.cells.storage.layout.internal.settings_utils import (
            get_migration_settings_path,
            get_settings_path,
            save_persisted_settings,
        )

        appdata = tmp_path / "appdata"
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        settings = Settings(
            workspace=str(workspace),
            pm_backend="ollama",
            pm_model="test-pm",
            director_model="test-director",
            model="test-model",
        )

        save_persisted_settings(settings)

        global_settings_path = get_settings_path(str(workspace))
        assert os.path.isfile(global_settings_path)
        with open(global_settings_path, encoding="utf-8") as handle:
            global_payload = json.load(handle)
        assert global_payload.get("workspace") == os.path.abspath(str(workspace))
        assert global_payload.get("pm_backend") == "ollama"

        assert not os.path.isfile(get_migration_settings_path())

    def test_load_settings_migrates_legacy_to_global(self, tmp_path, monkeypatch):
        from polaris.cells.storage.layout.internal.settings_utils import (
            get_migration_settings_path,
            get_settings_path,
            load_persisted_settings,
        )

        appdata = tmp_path / "appdata"
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        migration_payload = {
            "workspace": os.path.abspath(str(workspace)),
            "pm_backend": "ollama",
            "pm_model": "legacy-model",
            "auto_refresh": False,
        }
        self._write_json(get_migration_settings_path(), migration_payload)

        loaded = load_persisted_settings()
        assert loaded.get("workspace") == os.path.abspath(str(workspace))
        assert loaded.get("pm_backend") == "ollama"

        global_settings_path = get_settings_path(str(workspace))
        assert os.path.isfile(global_settings_path)
        with open(global_settings_path, encoding="utf-8") as handle:
            migrated_payload = json.load(handle)
        assert migrated_payload.get("workspace") == os.path.abspath(str(workspace))
        assert migrated_payload.get("pm_model") == "legacy-model"

    def test_load_settings_migrates_workspace_scoped_settings_to_global(self, tmp_path):
        from polaris.cells.storage.layout.internal.settings_utils import (
            get_settings_path,
            get_workspace_settings_path,
            load_persisted_settings,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        legacy_workspace_payload = {
            "workspace": os.path.abspath(str(workspace)),
            "pm_backend": "ollama",
            "pm_model": "workspace-legacy-model",
        }
        self._write_json(get_workspace_settings_path(str(workspace)), legacy_workspace_payload)

        loaded = load_persisted_settings(str(workspace))
        assert loaded.get("pm_model") == "workspace-legacy-model"

        global_settings_path = get_settings_path()
        with open(global_settings_path, encoding="utf-8") as handle:
            global_payload = json.load(handle)
        assert global_payload.get("pm_model") == "workspace-legacy-model"

    def test_load_llm_config_ignores_runtime_legacy_config(self, tmp_path):
        from polaris.kernelone.llm.config_store import llm_config_path, load_llm_config

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        legacy_path = Path(resolve_runtime_path(str(workspace), "runtime/config/llm_config.json"))
        self._write_json(str(legacy_path), self._seed_legacy_llm_payload())

        loaded = load_llm_config(str(workspace), "")
        target_path = llm_config_path(str(workspace), "")
        assert os.path.isfile(target_path)

        with open(target_path, encoding="utf-8") as handle:
            persisted_payload = json.load(handle)
        assert "custom" not in persisted_payload.get("providers", {})
        assert loaded.get("roles", {}).get("pm", {}).get("provider_id") != "custom"


class TestPmBackendRuntimeResolution:
    def test_sync_settings_sets_pm_backend_auto_for_generic_provider(self):
        from polaris.cells.llm.provider_config.internal.settings_sync import apply_llm_config_updates_to_settings

        settings = MagicMock()
        settings.pm_backend = "codex"
        settings.pm_model = ""

        payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        apply_llm_config_updates_to_settings(settings, payload)

        assert settings.pm_backend == "auto"
        assert settings.pm_model == "gpt-4.1"

    def test_check_backend_available_ignores_stale_codex_when_runtime_is_generic(self):
        from polaris.application.health import check_backend_available

        settings = MagicMock()
        settings.pm_backend = "codex"
        settings.workspace = "/tmp/workspace"
        settings.ramdisk_root = ""

        llm_payload = {
            "providers": {"openai_compat": {"type": "openai_compat"}},
            "roles": {"pm": {"provider_id": "openai_compat", "model": "gpt-4.1"}},
        }

        with (
            patch("polaris.kernelone.storage.io_paths.build_cache_root", return_value=""),
            patch("polaris.kernelone.llm.config_store.load_llm_config", return_value=llm_payload),
            patch("shutil.which", return_value=None),
        ):
            error = check_backend_available(settings)

        assert error is None

    def test_check_backend_available_requires_pm_role_mapping(self, tmp_path, monkeypatch):
        from polaris.application.health import check_backend_available

        settings = MagicMock()
        settings.pm_backend = "auto"
        settings.workspace = "/tmp/workspace"
        settings.ramdisk_root = ""

        monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "polaris-home"))
        with patch("polaris.kernelone.storage.io_paths.build_cache_root", return_value=""):
            error = check_backend_available(settings)

        assert isinstance(error, str)
        assert "PM role mapping is missing or incomplete" in error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
