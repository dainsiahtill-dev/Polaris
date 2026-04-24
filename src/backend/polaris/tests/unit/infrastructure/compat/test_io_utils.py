"""Tests for polaris.infrastructure.compat.io_utils (deprecated module)."""

from __future__ import annotations

import warnings


class TestIoUtilsDeprecation:
    """Test that io_utils emits deprecation warning."""

    def test_deprecation_warning(self) -> None:
        """Importing io_utils should emit a DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import polaris.infrastructure.compat.io_utils  # noqa: F401

            assert len(w) >= 1
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) >= 1
            assert "deprecated" in str(deprecation_warnings[0].message).lower()


class TestIoUtilsReExports:
    """Test that key re-exports are available (smoke tests)."""

    def test_ensure_process_utf8_exists(self) -> None:
        """ensure_process_utf8 should be importable and callable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import ensure_process_utf8

        assert callable(ensure_process_utf8)

    def test_utc_iso_now_exists(self) -> None:
        """utc_iso_now should be importable and return a string."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import utc_iso_now

        result = utc_iso_now()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_scan_last_seq_exists(self) -> None:
        """scan_last_seq should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import scan_last_seq

        assert callable(scan_last_seq)

    def test_build_cache_root_exists(self) -> None:
        """build_cache_root should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import build_cache_root

        assert callable(build_cache_root)

    def test_get_event_seq_exists(self) -> None:
        """get_event_seq should be importable and return an integer."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import get_event_seq

        result = get_event_seq()
        assert isinstance(result, int)

    def test_read_file_safe_exists(self) -> None:
        """read_file_safe should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import read_file_safe

        assert callable(read_file_safe)

    def test_read_file_safe_empty_path(self) -> None:
        """read_file_safe with empty path should return empty string."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import read_file_safe

        result = read_file_safe("")
        assert result == ""

    def test_read_memory_snapshot_empty_path(self) -> None:
        """read_memory_snapshot with empty path should return None."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import read_memory_snapshot

        result = read_memory_snapshot("")
        assert result is None

    def test_write_memory_snapshot_empty_path(self) -> None:
        """write_memory_snapshot with empty path should not raise."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import write_memory_snapshot

        # Should not raise even with empty path
        write_memory_snapshot("", {"key": "value"})

    def test_write_loop_warning_empty_path(self) -> None:
        """write_loop_warning with empty path should not raise."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import write_loop_warning

        # Should not raise even with empty path - just logs warning
        write_loop_warning("", "test message")

    def test_append_jsonl_empty_path(self) -> None:
        """append_jsonl with empty path should not raise."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import append_jsonl

        # Empty path should be handled gracefully (no-op)
        append_jsonl("", {"key": "value"})

    def test_append_jsonl_atomic_empty_path(self) -> None:
        """append_jsonl_atomic with empty path should not raise."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import append_jsonl_atomic

        # Empty path should be handled gracefully (no-op)
        append_jsonl_atomic("", {"key": "value"})

    def test_flush_jsonl_buffers_exists(self) -> None:
        """flush_jsonl_buffers should be importable and callable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import flush_jsonl_buffers

        assert callable(flush_jsonl_buffers)
        flush_jsonl_buffers()  # Should not raise

    def test_configure_jsonl_buffer_exists(self) -> None:
        """configure_jsonl_buffer should be importable and callable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import configure_jsonl_buffer

        assert callable(configure_jsonl_buffer)
        configure_jsonl_buffer()  # Should not raise with defaults

    def test_state_to_ramdisk_enabled_exists(self) -> None:
        """state_to_ramdisk_enabled should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import state_to_ramdisk_enabled

        assert callable(state_to_ramdisk_enabled)

    def test_normalize_ramdisk_root_exists(self) -> None:
        """normalize_ramdisk_root should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import normalize_ramdisk_root

        assert callable(normalize_ramdisk_root)

    def test_resolve_ramdisk_root_exists(self) -> None:
        """resolve_ramdisk_root should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import resolve_ramdisk_root

        assert callable(resolve_ramdisk_root)

    def test_default_ramdisk_root_exists(self) -> None:
        """default_ramdisk_root should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import default_ramdisk_root

        assert callable(default_ramdisk_root)

    def test_find_workspace_root_exists(self) -> None:
        """find_workspace_root should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import find_workspace_root

        assert callable(find_workspace_root)

    def test_resolve_artifact_path_exists(self) -> None:
        """resolve_artifact_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import resolve_artifact_path

        assert callable(resolve_artifact_path)

    def test_resolve_workspace_path_exists(self) -> None:
        """resolve_workspace_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import resolve_workspace_path

        assert callable(resolve_workspace_path)

    def test_is_hot_artifact_path_exists(self) -> None:
        """is_hot_artifact_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import is_hot_artifact_path

        assert callable(is_hot_artifact_path)

    def test_normalize_artifact_rel_path_exists(self) -> None:
        """normalize_artifact_rel_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import normalize_artifact_rel_path

        assert callable(normalize_artifact_rel_path)

    def test_update_latest_pointer_exists(self) -> None:
        """update_latest_pointer should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import update_latest_pointer

        assert callable(update_latest_pointer)

    def test_workspace_has_docs_exists(self) -> None:
        """workspace_has_docs should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import workspace_has_docs

        assert callable(workspace_has_docs)

    def test_resolve_run_dir_exists(self) -> None:
        """resolve_run_dir should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import resolve_run_dir

        assert callable(resolve_run_dir)

    def test_resolve_codex_path_exists(self) -> None:
        """resolve_codex_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import resolve_codex_path

        assert callable(resolve_codex_path)

    def test_resolve_ollama_path_exists(self) -> None:
        """resolve_ollama_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import resolve_ollama_path

        assert callable(resolve_ollama_path)

    def test_ensure_codex_available_exists(self) -> None:
        """ensure_codex_available should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import ensure_codex_available

        assert callable(ensure_codex_available)

    def test_ensure_ollama_available_exists(self) -> None:
        """ensure_ollama_available should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import ensure_ollama_available

        assert callable(ensure_ollama_available)

    def test_ensure_tools_available_exists(self) -> None:
        """ensure_tools_available should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import ensure_tools_available

        assert callable(ensure_tools_available)

    def test_stop_requested_exists(self) -> None:
        """stop_requested should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import stop_requested

        assert callable(stop_requested)

    def test_pause_requested_exists(self) -> None:
        """pause_requested should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import pause_requested

        assert callable(pause_requested)

    def test_clear_stop_flag_exists(self) -> None:
        """clear_stop_flag should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import clear_stop_flag

        assert callable(clear_stop_flag)

    def test_clear_director_stop_flag_exists(self) -> None:
        """clear_director_stop_flag should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import clear_director_stop_flag

        assert callable(clear_director_stop_flag)

    def test_stop_flag_path_exists(self) -> None:
        """stop_flag_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import stop_flag_path

        assert callable(stop_flag_path)

    def test_director_stop_flag_path_exists(self) -> None:
        """director_stop_flag_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import director_stop_flag_path

        assert callable(director_stop_flag_path)

    def test_pause_flag_path_exists(self) -> None:
        """pause_flag_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import pause_flag_path

        assert callable(pause_flag_path)

    def test_interrupt_notice_path_exists(self) -> None:
        """interrupt_notice_path should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import interrupt_notice_path

        assert callable(interrupt_notice_path)

    def test_emit_dialogue_exists(self) -> None:
        """emit_dialogue should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import emit_dialogue

        assert callable(emit_dialogue)

    def test_emit_event_exists(self) -> None:
        """emit_event should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import emit_event

        assert callable(emit_event)

    def test_emit_llm_event_exists(self) -> None:
        """emit_llm_event should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import emit_llm_event

        assert callable(emit_llm_event)

    def test_set_dialogue_seq_exists(self) -> None:
        """set_dialogue_seq should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import set_dialogue_seq

        assert callable(set_dialogue_seq)

    def test_set_event_seq_exists(self) -> None:
        """set_event_seq should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import set_event_seq

        assert callable(set_event_seq)

    def test_get_memory_summary_exists(self) -> None:
        """get_memory_summary should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import get_memory_summary

        assert callable(get_memory_summary)

    def test_ensure_memory_dir_exists(self) -> None:
        """ensure_memory_dir should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import ensure_memory_dir

        assert callable(ensure_memory_dir)

    def test_extract_field_exists(self) -> None:
        """extract_field should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import extract_field

        assert callable(extract_field)

    def test_ensure_parent_dir_exists(self) -> None:
        """ensure_parent_dir should be importable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import ensure_parent_dir

        assert callable(ensure_parent_dir)

    def test_build_utf8_env_exists(self) -> None:
        """build_utf8_env should be importable and return a dict."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import build_utf8_env

        result = build_utf8_env()
        assert isinstance(result, dict)

    def test_enforce_utf8_exists(self) -> None:
        """enforce_utf8 should be importable and callable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import enforce_utf8

        assert callable(enforce_utf8)

    def test_write_json_atomic_empty_path(self) -> None:
        """write_json_atomic with empty path should not raise."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import write_json_atomic

        # Empty path should be handled gracefully (no-op)
        write_json_atomic("", {"key": "value"})

    def test_write_text_atomic_empty_path(self) -> None:
        """write_text_atomic with empty path should not raise."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from polaris.infrastructure.compat.io_utils import write_text_atomic

        # Empty path should be handled gracefully (no-op)
        write_text_atomic("", "test content")
