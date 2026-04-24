"""Tests for polaris.cells.llm.control_plane.internal.llm_config_agent."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from polaris.cells.llm.control_plane.internal.llm_config_agent import (
    HRAgent,
    LLMConfig,
    LLMConfigStore,
    _infer_workspace_from_storage_path,
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
        result = _infer_workspace_from_storage_path("/home/user/.polaris/config/llm")
        assert result == "/home/user"

    def test_polaris_cache_marker(self) -> None:
        result = _infer_workspace_from_storage_path("/home/user/.polaris-cache/config/llm")
        assert result == "/home/user"

    def test_runtime_marker(self) -> None:
        result = _infer_workspace_from_storage_path("/project/runtime/llm/configs")
        assert result == "/project"

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

    def _make_store(self, tmp_path: Path) -> tuple[LLMConfigStore, MagicMock]:
        """Create a store with mocked filesystem."""
        storage_path = str(tmp_path / "llm")

        mock_fs = MagicMock()
        store = LLMConfigStore(storage_path)
        store._fs = mock_fs
        return store, mock_fs

    def test_save_and_get_config(self, tmp_path: Path) -> None:
        store, mock_fs = self._make_store(tmp_path)

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

        store.save(config)
        mock_fs.write_json.assert_called_once()

    def test_get_config_not_found(self, tmp_path: Path) -> None:
        store, mock_fs = self._make_store(tmp_path)

        mock_fs.exists.return_value = False
        result = store.get("nonexistent_role")
        assert result is None

    def test_get_all_configs_empty(self, tmp_path: Path) -> None:
        store, mock_fs = self._make_store(tmp_path)

        mock_fs.exists.return_value = False
        result = store.get_all()
        assert result == []

    def test_delete_config(self, tmp_path: Path) -> None:
        store, mock_fs = self._make_store(tmp_path)

        mock_fs.exists.return_value = True
        mock_fs.read_json.return_value = {
            "pm": {"config_id": "config_pm", "role": "pm"},
            "architect": {"config_id": "config_architect", "role": "architect"},
        }
        mock_fs.to_logical_path.return_value = "/logical/path"

        result = store.delete("pm")
        assert result is True
        mock_fs.write_json.assert_called_once()

    def test_delete_config_not_found(self, tmp_path: Path) -> None:
        store, mock_fs = self._make_store(tmp_path)

        mock_fs.exists.return_value = True
        mock_fs.read_json.return_value = {}
        mock_fs.to_logical_path.return_value = "/logical/path"

        result = store.delete("nonexistent")
        assert result is False

    def test_delete_empty_role(self, tmp_path: Path) -> None:
        store, _ = self._make_store(tmp_path)

        result = store.delete("")
        assert result is False

        result = store.get("")
        assert result is None


class TestHRAgent:
    """Tests for HRAgent class."""

    def test_resolve_provider_kind_ollama(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        result = agent._resolve_provider_kind("local", "ollama", {})
        assert result == "ollama"

    def test_resolve_provider_kind_codex_cli(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        result = agent._resolve_provider_kind("codex_cli", "codex_cli", {})
        assert result == "codex"

    def test_resolve_provider_kind_codex_sdk(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        result = agent._resolve_provider_kind("codex_sdk", "codex_sdk", {})
        assert result == "codex"

    def test_resolve_provider_kind_cli_with_codex_command(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        result = agent._resolve_provider_kind("my_codex", "cli", {"command": "codex start"})
        assert result == "codex"

    def test_resolve_provider_kind_generic(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        result = agent._resolve_provider_kind("some_provider", "anthropic", {})
        assert result == "generic"

    def test_role_id(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        assert agent.role_id == "HR"

    def test_get_capabilities(self) -> None:
        agent = HRAgent.__new__(HRAgent)
        caps = agent.get_capabilities()
        assert "set_llm_config" in caps
        assert "get_llm_config" in caps
        assert "list_all_configs" in caps
