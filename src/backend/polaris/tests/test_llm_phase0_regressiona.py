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


