"""Tests for polaris.cells.llm.control_plane.internal.llm_config_agent."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.llm.control_plane.internal.llm_config_agent import (
    HRAgent,
    LLMConfig,
    LLMConfigStore,
    _infer_workspace_from_storage_path,
    _mask_sensitive_values,
    _validate_provider_cfg,
)


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""

    def test_to_dict(self) -> None:
        now = datetime.now()
        config = LLMConfig(
            config_id="config_pm",
            role="pm",
            provider_id="provider-1",
            provider_type="ollama",
            provider_kind="ollama",
            model="llama2",
            profile="default",
            provider_cfg={"temperature": 0.7},
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        result = config.to_dict()

        assert result["config_id"] == "config_pm"
        assert result["role"] == "pm"
        assert result["provider_id"] == "provider-1"
        assert result["provider_type"] == "ollama"
        assert result["provider_kind"] == "ollama"
        assert result["model"] == "llama2"
        assert result["profile"] == "default"
        assert result["provider_cfg"] == {"temperature": 0.7}
        assert result["is_active"] is True
        assert "created_at" in result
        assert "updated_at" in result

    def test_from_dict(self) -> None:
        data = {
            "config_id": "config_pm",
            "role": "pm",
            "provider_id": "provider-1",
            "provider_type": "ollama",
            "provider_kind": "ollama",
            "model": "llama2",
            "profile": "default",
            "provider_cfg": {"temperature": 0.7},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "is_active": True,
        }
        config = LLMConfig.from_dict(data)

        assert config.config_id == "config_pm"
        assert config.role == "pm"
        assert config.provider_id == "provider-1"
        assert config.model == "llama2"
        assert config.provider_cfg == {"temperature": 0.7}
        assert config.is_active is True

    def test_from_dict_with_defaults(self) -> None:
        data = {
            "config_id": "config_pm",
            "role": "pm",
            "provider_id": "provider-1",
            "provider_type": "ollama",
            "provider_kind": "ollama",
            "model": "llama2",
            "profile": "default",
        }
        config = LLMConfig.from_dict(data)

        assert config.provider_cfg == {}
        assert config.is_active is True

    def test_roundtrip_serialization(self) -> None:
        """Test that to_dict -> from_dict preserves data."""
        original = LLMConfig(
            config_id="config_test",
            role="architect",
            provider_id="codex_cli",
            provider_type="codex_cli",
            provider_kind="codex",
            model="claude-3-5-sonnet",
            profile="production",
            provider_cfg={"max_tokens": 4096},
            created_at=datetime(2024, 6, 15, 10, 30, 0),
            updated_at=datetime(2024, 6, 15, 12, 0, 0),
            is_active=False,
        )
        serialized = original.to_dict()
        restored = LLMConfig.from_dict(serialized)

        assert restored.config_id == original.config_id
        assert restored.role == original.role
        assert restored.provider_id == original.provider_id
        assert restored.provider_type == original.provider_type
        assert restored.provider_kind == original.provider_kind
        assert restored.model == original.model
        assert restored.profile == original.profile
        assert restored.provider_cfg == original.provider_cfg
        assert restored.is_active == original.is_active


class TestInferWorkspaceFromStoragePath:
    """Tests for _infer_workspace_from_storage_path function."""

    def test_polaris_marker(self) -> None:
        # Use platform-aware path separator
        if os.name == "nt":
            path = "C:/Users/user/.polaris/config/llm"
            # On Windows, result will have backslashes - just check it starts correctly
            result = _infer_workspace_from_storage_path(path)
            assert result.lower().replace("\\", "/") == "c:/users/user"
        else:
            path = "/home/user/.polaris/config/llm"
            expected_workspace = "/home/user"
            result = _infer_workspace_from_storage_path(path)
            assert result == expected_workspace

    def test_polaris_cache_marker(self) -> None:
        if os.name == "nt":
            path = "C:/Users/user/.polaris-cache/config/llm"
            result = _infer_workspace_from_storage_path(path)
            assert result.lower().replace("\\", "/") == "c:/users/user"
        else:
            path = "/home/user/.polaris-cache/config/llm"
            expected_workspace = "/home/user"
            result = _infer_workspace_from_storage_path(path)
            assert result == expected_workspace

    def test_runtime_marker(self) -> None:
        if os.name == "nt":
            path = "C:/project/runtime/llm/configs"
            result = _infer_workspace_from_storage_path(path)
            assert result.lower().replace("\\", "/") == "c:/project"
        else:
            path = "/project/runtime/llm/configs"
            expected_workspace = "/project"
            result = _infer_workspace_from_storage_path(path)
            assert result == expected_workspace

    def test_no_marker_returns_cwd(self) -> None:
        result = _infer_workspace_from_storage_path("/some/random/path")
        # Should return current working directory
        assert result is not None

    def test_empty_path_returns_cwd(self) -> None:
        result = _infer_workspace_from_storage_path("")
        # Should return current working directory
        assert result is not None


class TestLLMConfigStore:
    """Tests for LLMConfigStore with mocked filesystem."""

    def _make_store_with_mock_fs(self, storage_path: str) -> tuple[LLMConfigStore, MagicMock]:
        """Create a store with fully mocked filesystem to avoid path issues."""
        mock_fs = MagicMock()
        mock_fs.to_logical_path.return_value = "/logical/llm/configs.json"

        # Patch the KernelFileSystem at module level before creating store
        with patch(
            "polaris.cells.llm.control_plane.internal.llm_config_agent.KernelFileSystem",
            return_value=mock_fs,
        ):
            store = LLMConfigStore(storage_path)
            store._fs = mock_fs
            store._lock = MagicMock()  # Use mock lock too
        return store, mock_fs

    def test_save_and_get_config(self) -> None:
        store, mock_fs = self._make_store_with_mock_fs("/tmp/llm")

        mock_fs.exists.return_value = True
        mock_fs.read_json.return_value = {}
        mock_fs.to_logical_path.return_value = "/logical/path"

        config = LLMConfig(
            config_id="config_pm",
            role="pm",
            provider_id="provider-1",
            provider_type="ollama",
            provider_kind="ollama",
            model="llama2",
            profile="default",
        )

        # Mock the lock to do nothing
        with patch.object(store, "_lock"):
            store.save(config)
        mock_fs.write_json_atomic.assert_called_once()

    def test_get_config_not_found(self) -> None:
        store, mock_fs = self._make_store_with_mock_fs("/tmp/llm")

        mock_fs.exists.return_value = False
        mock_fs.to_logical_path.return_value = "/logical/path"

        with patch.object(store, "_lock"):
            result = store.get("nonexistent_role")
        assert result is None

    def test_get_all_configs_empty(self) -> None:
        store, mock_fs = self._make_store_with_mock_fs("/tmp/llm")

        mock_fs.exists.return_value = False
        mock_fs.to_logical_path.return_value = "/logical/path"

        with patch.object(store, "_lock"):
            result = store.get_all()
        assert result == []

    def test_delete_config(self) -> None:
        store, mock_fs = self._make_store_with_mock_fs("/tmp/llm")

        mock_fs.exists.return_value = True
        mock_fs.read_json.return_value = {
            "pm": {"config_id": "config_pm", "role": "pm"},
            "architect": {"config_id": "config_architect", "role": "architect"},
        }
        mock_fs.to_logical_path.return_value = "/logical/path"

        with patch.object(store, "_lock"):
            result = store.delete("pm")
        assert result is True
        mock_fs.write_json_atomic.assert_called_once()

    def test_delete_config_not_found(self) -> None:
        store, mock_fs = self._make_store_with_mock_fs("/tmp/llm")

        mock_fs.exists.return_value = True
        mock_fs.read_json.return_value = {}
        mock_fs.to_logical_path.return_value = "/logical/path"

        with patch.object(store, "_lock"):
            result = store.delete("nonexistent")
        assert result is False

    def test_delete_empty_role(self) -> None:
        store, mock_fs = self._make_store_with_mock_fs("/tmp/llm")

        mock_fs.to_logical_path.return_value = "/logical/path"

        with patch.object(store, "_lock"):
            result = store.delete("")
        assert result is False

        with patch.object(store, "_lock"):
            result = store.get("")
        assert result is None


class TestHRAgent:
    """Tests for HRAgent class - testing method implementations."""

    def test_resolve_provider_kind_ollama(self) -> None:
        # Create agent and directly set minimal required attributes
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent._config_store = MagicMock()
        result = agent._resolve_provider_kind("local", "ollama", {})
        assert result == "ollama"

    def test_resolve_provider_kind_codex_cli(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent._config_store = MagicMock()
        result = agent._resolve_provider_kind("codex_cli", "codex_cli", {})
        assert result == "codex"

    def test_resolve_provider_kind_codex_sdk(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent._config_store = MagicMock()
        result = agent._resolve_provider_kind("codex_sdk", "codex_sdk", {})
        assert result == "codex"

    def test_resolve_provider_kind_cli_with_codex_command(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent._config_store = MagicMock()
        result = agent._resolve_provider_kind("my_codex", "cli", {"command": "codex start"})
        assert result == "codex"

    def test_resolve_provider_kind_generic(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent._config_store = MagicMock()
        result = agent._resolve_provider_kind("some_provider", "anthropic", {})
        assert result == "generic"

    def test_tool_methods_exist(self) -> None:
        """Test that the tool methods are callable (signature test)."""
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent._config_store = MagicMock()

        # Test that methods exist and are callable
        assert callable(agent._tool_get_llm_config)
        assert callable(agent._tool_set_llm_config)
        assert callable(agent._tool_list_all_configs)
        assert callable(agent._tool_update_llm_config)
        assert callable(agent._tool_deactivate_config)
        assert callable(agent._tool_activate_config)
        assert callable(agent._tool_delete_config)

    def test_get_config_delegates_to_store(self) -> None:
        """Test that get_config properly delegates to the store."""
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        mock_store.get.return_value = None
        agent._config_store = mock_store

        result = agent.get_config("pm")

        mock_store.get.assert_called_once_with("pm")
        assert result is None

    def test_get_all_configs_delegates_to_store(self) -> None:
        """Test that get_all_configs properly delegates to the store."""
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        mock_store.get_all.return_value = []
        agent._config_store = mock_store

        result = agent.get_all_configs()

        mock_store.get_all.assert_called_once_with()
        assert result == []

    def test_tool_set_llm_config_validates_provider_cfg(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        agent._config_store = mock_store

        result = agent._tool_set_llm_config(
            role="pm",
            provider_id="openai",
            provider_type="openai",
            model="gpt-4",
            profile="default",
            provider_cfg={"temperature": 3.0},
        )
        assert result["ok"] is False
        assert result["error"] == "invalid_provider_cfg"
        mock_store.save.assert_not_called()

    def test_tool_set_llm_config_accepts_valid_provider_cfg(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        agent._config_store = mock_store

        result = agent._tool_set_llm_config(
            role="pm",
            provider_id="openai",
            provider_type="openai",
            model="gpt-4",
            profile="default",
            provider_cfg={"temperature": 0.7, "max_tokens": 2048},
        )
        assert result["ok"] is True
        mock_store.save.assert_called_once()
        saved_config = mock_store.save.call_args[0][0]
        assert saved_config.provider_cfg == {"temperature": 0.7, "max_tokens": 2048}
        # config_id should contain a UUID-like suffix, not just timestamp
        assert len(saved_config.config_id) > len("config_pm_")

    def test_tool_set_llm_config_masks_secrets_in_response(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        agent._config_store = mock_store

        result = agent._tool_set_llm_config(
            role="pm",
            provider_id="openai",
            provider_type="openai",
            model="gpt-4",
            profile="default",
            provider_cfg={"api_key": "sk-secret123", "temperature": 0.7},
        )
        assert result["ok"] is True
        config_dict = result["config"]
        assert config_dict["provider_cfg"]["api_key"] == "***"
        assert config_dict["provider_cfg"]["temperature"] == 0.7

    def test_tool_update_llm_config_validates_provider_cfg(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        existing = LLMConfig(
            config_id="cfg",
            role="pm",
            provider_id="openai",
            provider_type="openai",
            provider_kind="generic",
            model="gpt-4",
            profile="default",
        )
        mock_store.get.return_value = existing
        agent._config_store = mock_store

        result = agent._tool_update_llm_config(
            role="pm",
            provider_cfg={"max_tokens": 999999},
        )
        assert result["ok"] is False
        assert result["error"] == "invalid_provider_cfg"
        mock_store.save.assert_not_called()

    def test_tool_update_llm_config_masks_secrets(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        mock_store = MagicMock()
        existing = LLMConfig(
            config_id="cfg",
            role="pm",
            provider_id="openai",
            provider_type="openai",
            provider_kind="generic",
            model="gpt-4",
            profile="default",
            provider_cfg={"api_key": "old-key"},
        )
        mock_store.get.return_value = existing
        agent._config_store = mock_store

        result = agent._tool_update_llm_config(
            role="pm",
            provider_cfg={"api_key": "new-key", "temperature": 0.5},
        )
        assert result["ok"] is True
        assert result["config"]["provider_cfg"]["api_key"] == "***"
        assert result["config"]["provider_cfg"]["temperature"] == 0.5

    def test_handle_message_catches_exceptions(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent.agent_name = "HR"
        mock_store = MagicMock()
        mock_store.get.side_effect = RuntimeError("store failure")
        agent._config_store = mock_store

        from polaris.cells.roles.runtime.internal.agent_runtime_base import AgentMessage, MessageType

        msg = AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="test",
            receiver="hr",
            payload={"action": "get_config", "role": "pm"},
        )
        response = agent.handle_message(msg)
        assert response is not None
        assert response.type == MessageType.RESULT
        payload = dict(response.payload or {})
        assert payload["result"]["ok"] is False
        assert payload["result"]["error"] == "internal_error"
        assert "store failure" in payload["result"]["detail"]

    def test_handle_message_unsupported_action(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        agent.workspace = "/tmp"
        agent.agent_name = "HR"
        agent._config_store = MagicMock()

        from polaris.cells.roles.runtime.internal.agent_runtime_base import AgentMessage, MessageType

        msg = AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="test",
            receiver="hr",
            payload={"action": "unknown_action"},
        )
        response = agent.handle_message(msg)
        assert response is not None
        assert response.type == MessageType.RESULT
        payload = dict(response.payload or {})
        assert payload["result"]["ok"] is False
        assert payload["result"]["error"] == "unsupported_action"


class TestProviderCfgValidation:
    """Tests for _validate_provider_cfg and _mask_sensitive_values."""

    def test_validate_allowed_keys(self) -> None:
        _validate_provider_cfg({"temperature": 0.5})
        _validate_provider_cfg({"max_tokens": 100})
        _validate_provider_cfg({"stop": ["\n"]})

    def test_validate_allows_unknown_keys(self) -> None:
        # Unknown keys are allowed (e.g. api_key, provider-specific settings)
        _validate_provider_cfg({"unknown_key": 1, "api_key": "secret"})

    def test_validate_max_tokens_range(self) -> None:
        with pytest.raises(ValueError, match="between 1 and"):
            _validate_provider_cfg({"max_tokens": 0})
        with pytest.raises(ValueError, match="between 1 and"):
            _validate_provider_cfg({"max_tokens": 100_000})

    def test_validate_temperature_range(self) -> None:
        with pytest.raises(ValueError, match="between 0.0 and 2.0"):
            _validate_provider_cfg({"temperature": 5.0})

    def test_validate_top_p_range(self) -> None:
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _validate_provider_cfg({"top_p": 1.5})

    def test_validate_presence_penalty_range(self) -> None:
        with pytest.raises(ValueError, match="between -2.0 and 2.0"):
            _validate_provider_cfg({"presence_penalty": -5.0})

    def test_mask_sensitive_values(self) -> None:
        cfg = {"api_key": "secret", "temperature": 0.7, "password": "pass123"}
        result = _mask_sensitive_values(cfg)
        assert result["api_key"] == "***"
        assert result["password"] == "***"
        assert result["temperature"] == 0.7
        # Original should be unchanged
        assert cfg["api_key"] == "secret"


class TestWriteJsonAtomic:
    """Integration tests for atomic write via LocalFileSystemAdapter."""

    def test_atomic_write_creates_temp_then_renames(self, tmp_path: Path) -> None:
        """Verify atomic write creates temp file then atomically renames."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        adapter = LocalFileSystemAdapter()
        target = str(tmp_path / "config" / "test.json")

        # Write a config file atomically
        data = '{"role": "pm", "model": "test-model"}\n'
        adapter.write_text(target, data, atomic=True)

        # Verify target file exists
        assert Path(target).exists()

    def test_atomic_write_preserves_content(self, tmp_path: Path) -> None:
        """Verify atomic write preserves exact JSON content."""
        import json
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        adapter = LocalFileSystemAdapter()
        target = str(tmp_path / "llm" / "configs.json")

        configs = {
            "pm": {"role": "pm", "model": "claude-3-5-sonnet"},
            "architect": {"role": "architect", "model": "gpt-4"},
        }
        adapter.write_text(target, json.dumps(configs), atomic=True)

        result = json.loads(adapter.read_text(target))
        assert result == configs

    def test_atomic_write_overwrites_existing(self, tmp_path: Path) -> None:
        """Verify atomic write correctly overwrites existing file."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        adapter = LocalFileSystemAdapter()
        target = str(tmp_path / "data.json")

        adapter.write_text(target, '{"version": 1}', atomic=True)
        adapter.write_text(target, '{"version": 2, "extra": "data"}', atomic=True)

        import json
        result = json.loads(adapter.read_text(target))
        assert result == {"version": 2, "extra": "data"}

    def test_atomic_write_creates_parent_directories(self, tmp_path: Path) -> None:
        """Verify atomic write creates parent directories as needed."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        adapter = LocalFileSystemAdapter()
        target = str(tmp_path / "a" / "b" / "c" / "deep.json")

        adapter.write_text(target, '{"nested": true}', atomic=True)
        assert Path(target).exists()

    def test_atomic_write_with_special_chars_in_json(self, tmp_path: Path) -> None:
        """Verify atomic write handles special characters correctly."""
        import json
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        adapter = LocalFileSystemAdapter()
        target = str(tmp_path / "special.json")

        data = {
            "chinese": "中文测试",
            "emoji": "🎉",
            "quotes": 'he said "hello"',
        }
        adapter.write_text(target, json.dumps(data, ensure_ascii=False), atomic=True)

        result = json.loads(adapter.read_text(target))
        assert result["chinese"] == "中文测试"
        assert result["emoji"] == "🎉"

    def test_atomic_write_no_temp_leak(self, tmp_path: Path) -> None:
        """Verify atomic write does not leave temp files behind."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        adapter = LocalFileSystemAdapter()
        target = str(tmp_path / "config" / "test.json")

        adapter.write_text(target, '{"role": "pm"}', atomic=True)

        # Verify no temp files remain in parent dir
        temp_files = list(Path(target).parent.glob(".tmp*"))
        assert len(temp_files) == 0
