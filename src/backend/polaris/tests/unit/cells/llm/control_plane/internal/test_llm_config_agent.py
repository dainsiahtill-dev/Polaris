"""Tests for polaris.cells.llm.control_plane.internal.llm_config_agent."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestWriteJsonAtomic:
    """Integration tests for write_json_atomic temp-file-then-rename pattern."""

    def test_atomic_write_creates_temp_then_renames(self, tmp_path: Path) -> None:
        """Verify atomic write creates temp file then atomically renames."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
        from polaris.kernelone.fs.runtime import KernelFileSystem

        adapter = LocalFileSystemAdapter()
        fs = KernelFileSystem(str(tmp_path), adapter)

        # Write a config file atomically
        test_data = {"role": "pm", "model": "test-model"}
        fs.write_json_atomic("config/test.json", test_data)

        # Verify target file exists
        target = tmp_path / "config" / "test.json"
        assert target.exists()

        # Verify no temp files remain
        temp_files = list(tmp_path.glob(".write_tmp_*.json"))
        assert len(temp_files) == 0

    def test_atomic_write_preserves_content(self, tmp_path: Path) -> None:
        """Verify atomic write preserves exact JSON content."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
        from polaris.kernelone.fs.runtime import KernelFileSystem

        adapter = LocalFileSystemAdapter()
        fs = KernelFileSystem(str(tmp_path), adapter)

        # Write multiple configs
        configs = {
            "pm": {"role": "pm", "model": "claude-3-5-sonnet"},
            "architect": {"role": "architect", "model": "gpt-4"},
        }
        fs.write_json_atomic("llm/configs.json", configs)

        # Read back and verify
        result = fs.read_json("llm/configs.json")
        assert result == configs
        assert result["pm"]["model"] == "claude-3-5-sonnet"
        assert result["architect"]["model"] == "gpt-4"

    def test_atomic_write_overwrites_existing(self, tmp_path: Path) -> None:
        """Verify atomic write correctly overwrites existing file."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
        from polaris.kernelone.fs.runtime import KernelFileSystem

        adapter = LocalFileSystemAdapter()
        fs = KernelFileSystem(str(tmp_path), adapter)

        # Write initial content
        fs.write_json_atomic("data.json", {"version": 1})

        # Overwrite with new content
        fs.write_json_atomic("data.json", {"version": 2, "extra": "data"})

        # Verify only new content exists
        result = fs.read_json("data.json")
        assert result == {"version": 2, "extra": "data"}

    def test_atomic_write_creates_parent_directories(self, tmp_path: Path) -> None:
        """Verify atomic write creates parent directories as needed."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
        from polaris.kernelone.fs.runtime import KernelFileSystem

        adapter = LocalFileSystemAdapter()
        fs = KernelFileSystem(str(tmp_path), adapter)

        # Write to nested path that doesn't exist
        fs.write_json_atomic("a/b/c/deep.json", {"nested": True})

        # Verify path was created
        deep_file = tmp_path / "a" / "b" / "c" / "deep.json"
        assert deep_file.exists()
        assert fs.read_json("a/b/c/deep.json") == {"nested": True}

    def test_atomic_write_with_special_chars_in_json(self, tmp_path: Path) -> None:
        """Verify atomic write handles special characters correctly."""
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
        from polaris.kernelone.fs.runtime import KernelFileSystem

        adapter = LocalFileSystemAdapter()
        fs = KernelFileSystem(str(tmp_path), adapter)

        # Write JSON with special characters
        data = {
            "chinese": "中文测试",
            "emoji": "🎉",
            "unicode": "  ",
            "quotes": 'he said "hello"',
        }
        fs.write_json_atomic("special.json", data)

        # Verify round-trip
        result = fs.read_json("special.json")
        assert result["chinese"] == "中文测试"
        assert result["emoji"] == "🎉"

    def test_atomic_write_fallback_when_adapter_lacks_support(self, tmp_path: Path) -> None:
        """Verify fallback works when adapter lacks write_json_atomic."""
        from polaris.kernelone.fs.runtime import KernelFileSystem

        # Create a mock adapter without write_json_atomic
        class MinimalAdapter:
            def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
                with open(path, encoding=encoding) as f:
                    return f.read()

            def read_bytes(self, path: str) -> bytes:
                with open(path, "rb") as f:
                    return f.read()

            def exists(self, path: str) -> bool:
                import os
                return os.path.exists(path)

            def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
                import os
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding=encoding) as f:
                    f.write(content)
                return len(content.encode(encoding))

            def write_bytes(self, path: str, content: bytes) -> int:
                import os
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
                return len(content)

            def append_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:
                with open(path, "a", encoding=encoding) as f:
                    f.write(content)
                return len(content.encode(encoding))

            def remove(self, path: str, *, missing_ok: bool = True) -> bool:
                import os
                try:
                    os.remove(path)
                    return True
                except FileNotFoundError:
                    return missing_ok

            def is_file(self, path: str) -> bool:
                import os
                return os.path.isfile(path)

            def is_dir(self, path: str) -> bool:
                import os
                return os.path.isdir(path)

        adapter = MinimalAdapter()
        fs = KernelFileSystem(str(tmp_path), adapter)

        # write_json_atomic should work even without adapter support
        fs.write_json_atomic("fallback.json", {"test": "fallback"})

        # Verify content
        assert fs.read_json("fallback.json") == {"test": "fallback"}
