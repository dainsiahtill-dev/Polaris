"""Tests for polaris.infrastructure.llm.providers.codex_cli_args."""

from __future__ import annotations

from polaris.infrastructure.llm.providers.codex_cli_args import (
    _build_codex_exec_args,
    _pick_reasoning_effort_fallback,
    _set_codex_config_override,
    _supports_reasoning_effort,
)


class TestSupportsReasoningEffort:
    def test_gpt_models(self) -> None:
        assert _supports_reasoning_effort("gpt-4") is True
        assert _supports_reasoning_effort("gpt-4o") is True

    def test_o_series(self) -> None:
        assert _supports_reasoning_effort("o1-preview") is True
        assert _supports_reasoning_effort("o3-mini") is True

    def test_codex_in_name(self) -> None:
        assert _supports_reasoning_effort("codex-1") is True

    def test_unsupported(self) -> None:
        assert _supports_reasoning_effort("claude-3") is False
        assert _supports_reasoning_effort("") is False


class TestBuildCodexExecArgs:
    def test_default_args(self) -> None:
        args = _build_codex_exec_args("gpt-4", {})
        assert "exec" in args
        assert "--skip-git-repo-check" in args
        assert "--model" in args
        assert "--sandbox" in args
        assert "read-only" in args
        assert "--json" in args

    def test_custom_sandbox(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"sandbox": "workspace-write"}})
        assert "workspace-write" in args

    def test_invalid_sandbox_fallback(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"sandbox": "invalid"}})
        assert "read-only" in args

    def test_yolo_mode(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"yolo": True}})
        assert "--yolo" in args

    def test_full_auto_mode(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"full_auto": True}})
        assert "--full-auto" in args

    def test_oss_flag(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"oss": True}})
        assert "--oss" in args

    def test_add_dirs(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"add_dirs": ["/tmp"]}})
        assert "--add-dir" in args
        assert "/tmp" in args

    def test_images(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"images": ["img.png"]}})
        assert "--image" in args
        assert "img.png" in args

    def test_profile(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"profile": "default"}})
        assert "--profile" in args
        assert "default" in args

    def test_config_overrides(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"config": ["key=value"]}})
        assert "--config" in args
        assert "key=value" in args

    def test_json_mode_disabled(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"json": False}})
        assert "--json" not in args

    def test_experimental_json(self) -> None:
        args = _build_codex_exec_args("gpt-4", {"codex_exec": {"json": "experimental"}})
        assert "--experimental-json" in args


class TestPickReasoningEffortFallback:
    def test_no_error_text(self) -> None:
        assert _pick_reasoning_effort_fallback("") is None

    def test_no_reasoning_mention(self) -> None:
        assert _pick_reasoning_effort_fallback("some other error") is None

    def test_supported_values(self) -> None:
        text = "reasoning.effort error. Supported values are: 'low', 'medium', 'high'"
        assert _pick_reasoning_effort_fallback(text) == "high"

    def test_mentions_fallback(self) -> None:
        assert _pick_reasoning_effort_fallback("reasoning.effort 'high'") == "medium"
        assert _pick_reasoning_effort_fallback("reasoning.effort 'medium'") == "low"
        assert _pick_reasoning_effort_fallback("reasoning.effort 'xhigh'") == "high"


class TestSetCodexConfigOverride:
    def test_replaces_existing(self) -> None:
        args = ["exec", "--config", "key=old", "{prompt}"]
        result = _set_codex_config_override(args, "key", "new")
        assert "key=new" in result
        assert "key=old" not in result

    def test_inserts_before_prompt(self) -> None:
        args = ["exec", "{prompt}"]
        result = _set_codex_config_override(args, "key", "val")
        idx_prompt = result.index("{prompt}")
        idx_config = result.index("--config")
        assert idx_config < idx_prompt

    def test_appends_when_no_prompt(self) -> None:
        args = ["exec"]
        result = _set_codex_config_override(args, "key", "val")
        assert result[-2] == "--config"
        assert result[-1] == "key=val"
