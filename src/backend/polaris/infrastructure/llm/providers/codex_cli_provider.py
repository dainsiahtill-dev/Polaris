from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
from typing import Any

from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ThinkingInfo,
    ValidationResult,
    WorkingDirConfig,
)
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds

from .codex_cli_args import (
    _build_codex_exec_args,
    _pick_reasoning_effort_fallback,
    _set_codex_config_override,
    _supports_reasoning_effort,
)

# --- Sub-module imports ---------------------------------------------------- #
from .codex_command_utils import (
    _render_args,
    _resolve_command,
    _resolve_output_path,
    _truncate,
)
from .codex_output_parser import (
    _extract_cli_error_message,
    _parse_codex_json_output,
)
from .codex_process import (
    _run_cli,
    _run_cli_pty,
)

logger = logging.getLogger(__name__)


class CodexCLIProvider(BaseProvider):
    """Enhanced provider for Codex CLI with JSON mode support"""

    def get_tui_instructions(self) -> dict[str, str]:
        """Get TUI mode instructions for Codex CLI

        Returns helpful instructions for users who need to interact with Codex CLI TUI
        """
        return {
            "model_discovery": "Run 'codex' then type '/models' to see all available models",
            "status_check": "Run 'codex' then type '/status' to see current session configuration",
            "permissions": "Run 'codex' then type '/permissions' to adjust approval settings",
            "help": "Run 'codex' then type '/help' to see all available commands",
            "exit": "Run 'codex' then type '/quit' or '/exit' to leave the TUI",
        }

    def get_session_status_hint(self) -> str:
        """Get hint about checking session status in TUI mode"""
        return "For detailed session status, run 'codex' and type '/status' in the TUI"

    def _build_codex_exec_args(self, model: str, config: dict[str, Any]) -> list[str]:
        """Build Codex CLI exec arguments based on actual CLI usage"""
        return _build_codex_exec_args(model, config)

    def _parse_codex_json_output(self, raw_output: str) -> str:
        """Parse JSON output from Codex CLI"""
        return _parse_codex_json_output(raw_output)

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Codex CLI Provider",
            type="codex_cli",
            description="Codex CLI with JSON mode and thinking extraction",
            version="2.0.0",
            author="Polaris Team",
            documentation_url="https://docs.codex.ai/cli",
            supported_features=[
                "thinking_extraction",
                "working_directory",
                "health_check",
                "json_mode",
                "real_time_streaming",
                "autonomous_file_operations",
                "sandbox_control",
            ],
            cost_class="FIXED",
            provider_category="AGENT",
            autonomous_file_access=True,
            requires_file_interfaces=False,
            model_listing_method="TUI",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "type": "codex_cli",
            "name": "Codex CLI",
            "command": "codex",
            "args": [],
            "cli_mode": "headless",
            "codex_exec": {
                "cd": "",
                "color": "never",
                "ask_for_approval": "on-request",
                "sandbox": "read-only",
                "skip_git_repo_check": True,
                "json": True,
                "yolo": False,
                "full_auto": False,
                "oss": False,
                "output_schema": "",
                "output_last_message": "",
                "profile": "",
                "add_dirs": [],
                "images": [],
                "config": [],
            },
            "manual_models": [],
            "list_args": [],
            "health_args": ["version"],
            "timeout": 60,
            "thinking_extraction": {
                "enabled": True,
                "patterns": [r"<thinking>(.*?)</thinking>", r"```thinking(.*?)```", r"Reasoning:(.*?)(?:\n\n|\n[A-Z])"],
                "confidence_threshold": 0.7,
            },
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        errors = []
        warnings = []
        normalized = config.copy()

        # Validate command
        command = str(config.get("command", "codex")).strip()
        if not command:
            command = "codex"

        resolved = _resolve_command(command)
        if not resolved:
            errors.append("Codex CLI command not found in PATH")
            warnings.append("Please install Codex CLI: https://docs.codex.ai/cli")
        else:
            normalized["command"] = resolved

        # Validate args
        args = config.get("args", [])
        if not isinstance(args, list):
            errors.append("Args must be a list")
            normalized["args"] = []

        # Validate timeout
        timeout = config.get("timeout", 60)
        if not isinstance(timeout, (int, float)):
            warnings.append("Invalid timeout, using default 60")
            normalized["timeout"] = 60
        else:
            timeout_num = int(timeout)
            if timeout_num < 0:
                warnings.append("Timeout cannot be negative, using default 60")
                normalized["timeout"] = 60
            else:
                normalized["timeout"] = timeout_num

        # Validate CLI mode
        cli_mode = str(config.get("cli_mode") or "").strip().lower()
        if cli_mode not in ("headless", "tui"):
            normalized["cli_mode"] = "headless"
            warnings.append("Invalid cli_mode, using headless")

        # Validate codex_exec config
        codex_exec = config.get("codex_exec", {})
        if not isinstance(codex_exec, dict):
            warnings.append("codex_exec should be a dictionary")
            normalized["codex_exec"] = cls.get_default_config()["codex_exec"]

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, normalized_config=normalized)

    def health(self, config: dict[str, Any]) -> HealthResult:
        command = str(config.get("command", "codex")).strip()
        resolved = _resolve_command(command)
        if not resolved:
            return HealthResult(ok=False, latency_ms=0, error="Codex CLI command not found")

        health_args = config.get("health_args", ["version"])
        try:
            code, stdout, stderr, latency_ms = _run_cli(
                resolved,
                list(map(str, health_args)),
                str(config.get("working_dir") or ""),
                config.get("env") or {},
                normalize_timeout_seconds(config.get("timeout"), default=15),
                None,
            )
            if code != 0:
                message = (stderr or stdout or "health check failed").strip()
                return HealthResult(ok=False, latency_ms=latency_ms, error=message)

            version_info = stdout.strip() if stdout else ""
            return HealthResult(
                ok=True,
                latency_ms=latency_ms,
                details={
                    "version": version_info,
                    "command": resolved,
                    "working_dir": str(config.get("working_dir") or ""),
                    "model_listing_method": "TUI",
                    "provider_category": "AGENT",
                },
            )
        except (RuntimeError, ValueError) as exc:
            return HealthResult(ok=False, latency_ms=0, error=str(exc))

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        """List available models for Codex CLI

        Note: Codex CLI requires TUI interaction to list models.
        This method provides a manual entry interface for users.
        """
        command = str(config.get("command", "codex")).strip()
        resolved = _resolve_command(command)
        if not resolved:
            return ModelListResult(ok=False, supported=False, models=[], error="Codex CLI command not found")

        manual_models = config.get("manual_models", [])
        if isinstance(manual_models, list) and manual_models:
            models = []
            for model_entry in manual_models:
                if isinstance(model_entry, str) and model_entry.strip():
                    models.append(ModelInfo(id=model_entry.strip(), label=model_entry.strip()))
            return ModelListResult(ok=True, supported=True, models=models, error="Models manually entered (TUI mode)")

        provider = CodexCLIProvider()
        tui_instructions = provider.get_tui_instructions()

        return ModelListResult(
            ok=True,
            supported=True,
            models=[
                ModelInfo(id="gpt-5.1-codex-max", label="GPT-5.1 Codex Max (Experimental)"),
                ModelInfo(id="gpt-4-codex", label="GPT-4 Codex (Common)"),
                ModelInfo(id="gpt-5.2-codex", label="GPT-5.2 Codex (Latest)"),
                ModelInfo(id="gpt-3.5-turbo", label="GPT-3.5 Turbo (Legacy)"),
            ],
            error=f"TUI_MODE: {tui_instructions['model_discovery']}. Enter models manually above or see TUI instructions.",
        )

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        command = str(config.get("command", "codex")).strip()
        resolved = _resolve_command(command)
        if not resolved:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error="Codex CLI command not found")

        args = _build_codex_exec_args(model, config)

        output_path = _resolve_output_path(config)
        if output_path:
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
            except (RuntimeError, ValueError):
                logger.debug("DEBUG: codex_cli_provider.py:{286} {exc} (swallowed)")

        rendered_args, send_prompt = _render_args(args, prompt, model, output_path)
        debug_raw = None
        debug_steps: list[str] = []
        stream_output_lines: list[str] = []

        if config.get("debug_emit_args"):
            try:
                debug_args = [str(item) for item in rendered_args]
            except (RuntimeError, ValueError):
                debug_args = []

            debug_steps.append(f"1. RESOLVED COMMAND: {resolved}")
            debug_steps.append(f"2. MODEL: {model}")
            debug_steps.append("3. TIMEOUT: none (waiting indefinitely)")
            debug_steps.append(f"4. SEND_PROMPT_MODE: {'stdin' if send_prompt else 'argv'}")
            debug_steps.append(f"5. CLI_ARGS: {json.dumps(debug_args)}")
            debug_steps.append(f"6. PROMPT_LENGTH: {len(prompt)} chars")
            if send_prompt:
                debug_steps.append(f"7. STDIN_PREVIEW: {_truncate(prompt, 300)}")

            debug_raw = {
                "debug_command": resolved,
                "debug_args": debug_args,
                "debug_send_prompt": bool(send_prompt),
                "debug_stdin_prompt": prompt if send_prompt else None,
                "debug_steps": debug_steps,
            }

        def run_with_pty(selected_args: list[str], use_prompt: bool) -> tuple[int, str, str, int]:
            """Run using PTY for real-time output capture"""
            output_queue: queue.Queue = queue.Queue(maxsize=1000)  # 有界队列防止内存泄漏
            collected_output: list[str] = []

            def collect_output() -> None:
                while True:
                    try:
                        stream_type, data = output_queue.get(timeout=0.1)
                        if stream_type == "done":
                            break
                        collected_output.append(data)
                        stream_output_lines.append(f"[{stream_type.upper()}] {data}")
                    except queue.Empty:
                        continue

            collector_thread = threading.Thread(target=collect_output)
            collector_thread.daemon = True
            collector_thread.start()

            try:
                code, output, latency_ms = _run_cli_pty(
                    resolved,
                    selected_args,
                    str(config.get("working_dir") or ""),
                    config.get("env") or {},
                    prompt if use_prompt else None,
                    output_queue,
                )
            finally:
                output_queue.put(("done", ""))
                collector_thread.join(timeout=2)

            full_output = "".join(collected_output) if collected_output else output

            if full_output and "--json" in selected_args:
                full_output = _parse_codex_json_output(full_output)

            return code, full_output, "", latency_ms

        def run_without_pty(selected_args: list[str], use_prompt: bool) -> tuple[int, str, str, int]:
            """Standard subprocess execution"""
            code, stdout, stderr, latency_ms = _run_cli(
                resolved,
                selected_args,
                str(config.get("working_dir") or ""),
                config.get("env") or {},
                0,
                prompt if use_prompt else None,
            )

            output = stdout.strip() if stdout else ""

            if output and "--json" in selected_args:
                output = _parse_codex_json_output(output)

            return code, output, stderr or "", latency_ms

        try:
            use_pty = bool(config.get("debug_emit_args"))

            if use_pty:
                code, output, stderr_raw, latency_ms = run_with_pty(rendered_args, send_prompt)
            else:
                code, output, stderr_raw, latency_ms = run_without_pty(rendered_args, send_prompt)

            stdout_raw = output

            if use_pty and debug_raw is not None and stream_output_lines:
                debug_raw["debug_stream_output"] = stream_output_lines

            cli_error = _extract_cli_error_message(output)
            if code != 0 or cli_error:
                message = (stderr_raw or cli_error or stdout_raw or "Codex CLI invoke failed").strip()
                fallback_source = stderr_raw or stdout_raw or output or message
                fallback_effort = (
                    _pick_reasoning_effort_fallback(fallback_source) if _supports_reasoning_effort(model) else None
                )
                if fallback_effort:
                    retry_args = _set_codex_config_override(args, "model_reasoning_effort", f'"{fallback_effort}"')
                    rendered_retry_args, send_prompt_retry = _render_args(retry_args, prompt, model, output_path)
                    if config.get("debug_emit_args"):
                        try:
                            debug_args = [str(item) for item in rendered_retry_args]
                        except (RuntimeError, ValueError):
                            debug_args = []
                        debug_raw = {
                            "debug_args": debug_args,
                            "debug_send_prompt": bool(send_prompt_retry),
                            "debug_stdin_prompt": prompt if send_prompt_retry else None,
                        }
                    code, output, stderr_raw, latency_ms = run_without_pty(rendered_retry_args, send_prompt_retry)
                    stdout_raw = output
                    cli_error = _extract_cli_error_message(output)
                if code == 0 and not cli_error:
                    usage = estimate_usage(prompt, output)
                    return InvokeResult(ok=True, output=output, latency_ms=latency_ms, usage=usage, raw=debug_raw)
                message = (stderr_raw or cli_error or stdout_raw or "Codex CLI invoke failed").strip()
                if message and fallback_effort:
                    message = f"{message}\n(auto-fallback reasoning.effort={fallback_effort} failed)"

                usage = estimate_usage(prompt, output)
                return InvokeResult(
                    ok=False, output=output, latency_ms=latency_ms, usage=usage, error=message, raw=debug_raw
                )

            usage = estimate_usage(prompt, output)
            return InvokeResult(ok=True, output=output, latency_ms=latency_ms, usage=usage, raw=debug_raw)
        except subprocess.TimeoutExpired:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error="timeout", raw=debug_raw)
        except (RuntimeError, ValueError) as exc:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error=str(exc), raw=debug_raw)

    @classmethod
    def extract_thinking_support(cls, response: dict[str, Any]) -> ThinkingInfo:
        """Extract thinking information from Codex CLI response"""
        if not isinstance(response, dict) or "output" not in response:
            return ThinkingInfo(
                supports_thinking=False,
                confidence=0.0,
                format=None,
                thinking_text=None,
                extraction_method="codex_default",
            )

        output = response.get("output", "")
        config = response.get("config", {})
        thinking_config = config.get("thinking_extraction", {})

        if not thinking_config.get("enabled", True):
            return ThinkingInfo(
                supports_thinking=False, confidence=0.0, format=None, thinking_text=None, extraction_method="disabled"
            )

        patterns = thinking_config.get(
            "patterns", [r"<thinking>(.*?)</thinking>", r"```thinking(.*?)```", r"Reasoning:(.*?)(?:\n\n|\n[A-Z])"]
        )

        confidence_threshold = thinking_config.get("confidence_threshold", 0.7)

        for pattern in patterns:
            try:
                match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)
                if match:
                    thinking_text = match.group(1).strip()
                    confidence = cls._calculate_thinking_confidence(thinking_text)
                    format_type = "xml" if "<thinking>" in pattern else "markdown"

                    if confidence >= confidence_threshold:
                        return ThinkingInfo(
                            supports_thinking=True,
                            confidence=confidence,
                            format=format_type,
                            thinking_text=thinking_text,
                            extraction_method="codex_pattern",
                        )

                    return ThinkingInfo(
                        supports_thinking=True,
                        confidence=confidence,
                        format=format_type,
                        thinking_text=thinking_text,
                        extraction_method="codex_pattern_low_confidence",
                    )
            except re.error:
                continue

        reasoning_indicators = [
            "reasoning",
            "analysis",
            "thought",
            "considering",
            "because",
            "therefore",
            "first",
            "next",
            "finally",
            "step",
            "approach",
        ]

        output_lower = output.lower()
        if any(indicator in output_lower for indicator in reasoning_indicators):
            return ThinkingInfo(
                supports_thinking=True,
                confidence=0.4,
                format="text",
                thinking_text=None,
                extraction_method="codex_keyword",
            )

        return ThinkingInfo(
            supports_thinking=False, confidence=0.0, format=None, thinking_text=None, extraction_method="no_thinking"
        )

    @classmethod
    def get_working_directory_config(cls, config: dict[str, Any]) -> WorkingDirConfig:
        """Get working directory configuration"""
        codex_exec = config.get("codex_exec", {})
        target_dir = codex_exec.get("cd") or config.get("working_dir")

        return WorkingDirConfig(
            target_directory=target_dir, auto_create=True, cleanup_after=False, environment_vars=config.get("env", {})
        )

    @staticmethod
    def _calculate_thinking_confidence(thinking_text: str) -> float:
        """Calculate confidence score for thinking extraction"""
        if not thinking_text:
            return 0.0

        length_score = min(len(thinking_text) / 300, 1.0)
        structure_score = (
            0.3
            if any(word in thinking_text.lower() for word in ["because", "therefore", "however", "although"])
            else 0.0
        )
        detail_score = min(thinking_text.count(".") / 10, 0.4)

        return min(length_score + structure_score + detail_score, 1.0)

    @staticmethod
    def _render_args(args: list[str], prompt: str, model: str, output_path: str | None) -> tuple[list[str], bool]:
        """Render arguments with placeholder replacement (delegates to module function)"""
        return _render_args(args, prompt, model, output_path)
