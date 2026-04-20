from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any

from polaris.kernelone.fs.encoding import build_utf8_env
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ThinkingInfo,
    ValidationResult,
    WorkingDirConfig,
)
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, estimate_usage
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds


def _timeout_seconds(config: dict[str, Any], default: int, key: str = "timeout") -> int:
    return normalize_timeout_seconds(config.get(key), default=default)


class GeminiCLIProvider(BaseProvider):
    """Gemini CLI provider with thinking extraction"""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Gemini CLI Provider",
            type="gemini_cli",
            description="Google Gemini command-line interface provider",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://ai.google.dev/cli",
            supported_features=[
                "thinking_extraction",
                "working_directory",
                "health_check",
                "streaming",
                "autonomous_file_operations",
                "api_key_auth",
            ],
            cost_class="METERED",
            provider_category="AGENT",
            autonomous_file_access=True,
            requires_file_interfaces=False,
            model_listing_method="TUI",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "command": "gemini",
            "args": ["chat", "--model", "{model}", "--prompt", "{prompt}"],
            "cli_mode": "headless",
            "env": {"GOOGLE_API_KEY": "", "GOOGLE_GENAI_USE_VERTEXAI": "false", "GOOGLE_GENAI_API_KEY": ""},
            "working_dir": "",
            "timeout": 60,
            "health_args": ["version"],
            "list_args": ["models", "list"],
            "thinking_extraction": {
                "enabled": True,
                "patterns": [
                    r"<thinking>(.*?)</thinking>",
                    r"```thinking(.*?)```",
                    r"Let me think(.*?)(?:\n\n|\n[A-Z])",
                    r"I need to consider(.*?)(?:\n\n|\n[A-Z])",
                ],
                "confidence_threshold": 0.6,
            },
            "streaming": {"enabled": False, "chunk_size": 1024},
        }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        errors = []
        warnings = []
        normalized = config.copy()

        # Validate command
        command = str(config.get("command", "gemini")).strip()
        if not command:
            command = "gemini"

        resolved = cls._resolve_command(command)
        if not resolved:
            errors.append(f"Gemini CLI command '{command}' not found in PATH")
            warnings.append("Please install Google Gemini CLI: https://ai.google.dev/cli")
        else:
            normalized["command"] = resolved

        # Validate API key
        env = config.get("env", {})
        api_key = env.get("GOOGLE_API_KEY") or env.get("GOOGLE_GENAI_API_KEY")
        if not api_key:
            errors.append("Google API key is required in GOOGLE_API_KEY or GOOGLE_GENAI_API_KEY environment variable")

        # Validate args
        args = config.get("args", ["chat", "--model", "{model}", "--prompt", "{prompt}"])
        if not isinstance(args, list):
            errors.append("Args must be a list")
            normalized["args"] = ["chat", "--model", "{model}", "--prompt", "{prompt}"]
        else:
            # Ensure required placeholders are present
            args_str = " ".join(args)
            if "{model}" not in args_str:
                warnings.append("Args should include {model} placeholder")
            if "{prompt}" not in args_str:
                warnings.append("Args should include {prompt} placeholder")

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

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, normalized_config=normalized)

    def __init__(self) -> None:
        pass

    def health(self, config: dict[str, Any]) -> HealthResult:
        command = str(config.get("command", "gemini")).strip()
        resolved = self._resolve_command(command)
        if not resolved:
            return HealthResult(ok=False, latency_ms=0, error="gemini CLI not found")

        health_args = config.get("health_args", ["version"])
        try:
            code, stdout, stderr, latency_ms = self._run_cli(
                resolved,
                list(map(str, health_args)),
                str(config.get("working_dir") or ""),
                config.get("env") or {},
                _timeout_seconds(config, 15),
                None,
            )
            if code != 0:
                message = (stderr or stdout or "health check failed").strip()
                return HealthResult(ok=False, latency_ms=latency_ms, error=message)
            return HealthResult(ok=True, latency_ms=latency_ms)
        except (RuntimeError, ValueError) as exc:
            return HealthResult(ok=False, latency_ms=0, error=str(exc))

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        command = str(config.get("command", "gemini")).strip()
        resolved = self._resolve_command(command)
        if not resolved:
            return ModelListResult(ok=False, supported=False, models=[], error="gemini CLI not found")

        list_args = config.get("list_args", ["models", "list"])
        try:
            code, stdout, stderr, _ = self._run_cli(
                resolved,
                list(map(str, list_args)),
                str(config.get("working_dir") or ""),
                config.get("env") or {},
                _timeout_seconds(config, 15),
                None,
            )
            if code != 0:
                message = (stderr or stdout or "model listing failed").strip()
                return ModelListResult(ok=False, supported=True, models=[], error=message)
            models = self._parse_model_output(stdout)
            return ModelListResult(ok=True, supported=True, models=models)
        except (RuntimeError, ValueError) as exc:
            return ModelListResult(ok=False, supported=True, models=[], error=str(exc))

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        command = str(config.get("command", "gemini")).strip()
        resolved = self._resolve_command(command)
        if not resolved:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error="gemini CLI not found")

        args = list(map(str, config.get("args", ["chat", "--model", "{model}", "--prompt", "{prompt}"])))
        rendered_args, send_prompt = self._render_args(args, prompt, model)

        timeout = _timeout_seconds(config, 60)
        try:
            code, stdout, stderr, latency_ms = self._run_cli(
                resolved,
                rendered_args,
                str(config.get("working_dir") or ""),
                config.get("env") or {},
                timeout,
                prompt if send_prompt else None,
            )
            output = stdout.strip() if stdout else ""

            # Clean up common Gemini CLI artifacts
            output = self._clean_output(output)

            usage = estimate_usage(prompt, output)
            if code != 0:
                message = (stderr or stdout or "gemini invoke failed").strip()
                return InvokeResult(ok=False, output=output, latency_ms=latency_ms, usage=usage, error=message)

            return InvokeResult(ok=True, output=output, latency_ms=latency_ms, usage=usage)
        except subprocess.TimeoutExpired:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=timeout * 1000, usage=usage, error="timeout")
        except (RuntimeError, ValueError) as exc:
            usage = estimate_usage(prompt, "")
            return InvokeResult(ok=False, output="", latency_ms=0, usage=usage, error=str(exc))

    @classmethod
    def extract_thinking_support(cls, response: dict[str, Any]) -> ThinkingInfo:
        """Extract thinking information from Gemini CLI response"""
        if not isinstance(response, dict) or "output" not in response:
            return ThinkingInfo(
                supports_thinking=False,
                confidence=0.0,
                format=None,
                thinking_text=None,
                extraction_method="gemini_default",
            )

        output = response.get("output", "")
        config = response.get("config", {})
        thinking_config = config.get("thinking_extraction", {})

        if not thinking_config.get("enabled", True):
            return ThinkingInfo(
                supports_thinking=False, confidence=0.0, format=None, thinking_text=None, extraction_method="disabled"
            )

        # Gemini-specific patterns
        patterns = thinking_config.get(
            "patterns",
            [
                r"<thinking>(.*?)</thinking>",
                r"```thinking(.*?)```",
                r"Let me think(.*?)(?:\n\n|\n[A-Z])",
                r"I need to consider(.*?)(?:\n\n|\n[A-Z])",
                r"Looking at this(.*?)(?:\n\n|\n[A-Z])",
            ],
        )

        confidence_threshold = thinking_config.get("confidence_threshold", 0.6)

        for pattern in patterns:
            try:
                match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)
                if match:
                    thinking_text = match.group(1).strip()
                    confidence = cls._calculate_thinking_confidence(thinking_text)

                    if confidence >= confidence_threshold:
                        return ThinkingInfo(
                            supports_thinking=True,
                            confidence=confidence,
                            format="xml" if "<thinking>" in pattern else "text",
                            thinking_text=thinking_text,
                            extraction_method="gemini_pattern",
                        )
            except re.error:
                continue

        # Check for Gemini-specific reasoning indicators
        reasoning_indicators = [
            "let me analyze",
            "i should consider",
            "looking at the context",
            "to approach this",
            "my reasoning",
            "step by step",
        ]

        output_lower = output.lower()
        if any(indicator in output_lower for indicator in reasoning_indicators):
            return ThinkingInfo(
                supports_thinking=True,
                confidence=0.4,
                format="text",
                thinking_text=None,
                extraction_method="gemini_keyword",
            )

        return ThinkingInfo(
            supports_thinking=False, confidence=0.0, format=None, thinking_text=None, extraction_method="no_thinking"
        )

    @classmethod
    def get_working_directory_config(cls, config: dict[str, Any]) -> WorkingDirConfig:
        """Get working directory configuration"""
        return WorkingDirConfig(
            target_directory=config.get("working_dir"),
            auto_create=True,
            cleanup_after=False,
            environment_vars=config.get("env", {}),
        )

    @staticmethod
    def _calculate_thinking_confidence(thinking_text: str) -> float:
        """Calculate confidence score for thinking extraction"""
        if not thinking_text:
            return 0.0

        # Gemini-specific confidence factors
        length_score = min(len(thinking_text) / 300, 1.0)  # Gemini tends to be concise
        reasoning_words = ["because", "therefore", "however", "although", "consider", "analyze", "evaluate"]
        reasoning_score = sum(0.1 for word in reasoning_words if word in thinking_text.lower())
        structure_score = 0.2 if any(punct in thinking_text for punct in [".", "!", "?"]) else 0.0

        return min(length_score + reasoning_score + structure_score, 1.0)

    @staticmethod
    def _clean_output(output: str) -> str:
        """Clean up Gemini CLI output artifacts"""
        # Remove common CLI artifacts
        artifacts = [
            r"^Generated by Gemini.*$\n?",
            r"^Model:.*$\n?",
            r"^Temperature:.*$\n?",
            r"^Time:.*$\n?",
            r"^\[.*\]$",
            r"^> $",
        ]

        cleaned = output
        for artifact in artifacts:
            cleaned = re.sub(artifact, "", cleaned, flags=re.MULTILINE)

        # Clean up extra whitespace
        cleaned = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned)
        cleaned = cleaned.strip()

        return cleaned

    # Helper methods
    @staticmethod
    def _resolve_command(command: str) -> str | None:
        if not command:
            return None
        if os.path.isabs(command) or os.path.exists(command):
            return command
        return shutil.which(command)

    @staticmethod
    def _run_cli(
        command: str,
        args: list[str],
        cwd: str,
        env: dict[str, str] | None,
        timeout: int,
        input_text: str | None,
    ) -> tuple[int, str, str, int]:
        cmd = [command, *args]
        start = time.time()
        result = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=cwd or None,
            env=build_utf8_env(env),
            timeout=timeout if timeout > 0 else None,
        )
        latency_ms = int((time.time() - start) * 1000)
        return result.returncode, result.stdout or "", result.stderr or "", latency_ms

    @staticmethod
    def _render_args(args: list[str], prompt: str, model: str) -> tuple[list[str], bool]:
        rendered: list[str] = []
        send_prompt = True
        for item in args:
            value = item.replace("{model}", model)
            if "{prompt}" in value:
                value = value.replace("{prompt}", prompt)
                send_prompt = False
            rendered.append(value)
        return rendered, send_prompt

    @staticmethod
    def _parse_model_output(output: str) -> list[ModelInfo]:
        text = (output or "").strip()
        if not text:
            return []
        models: list[ModelInfo] = []

        # Try JSON first
        if text.startswith("{") or text.startswith("["):
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    payload = payload.get("models") or payload.get("data") or payload.get("items") or []
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict):
                            model_id = str(item.get("id") or item.get("name") or "").strip()
                            if model_id:
                                models.append(ModelInfo(id=model_id, raw=item))
                        elif isinstance(item, str):
                            models.append(ModelInfo(id=item.strip()))
                return models
            except (RuntimeError, ValueError):
                models = []

        # Parse text output
        known_gemini_models = [
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-1.0-pro",
            "gemini-pro",
            "gemini-pro-vision",
        ]

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            # Check for known Gemini models
            for model_name in known_gemini_models:
                if model_name in line.lower():
                    models.append(ModelInfo(id=model_name, label=line))
                    break
            else:
                # Use first word as model ID
                model_id = line.split()[0].strip()
                if model_id:
                    models.append(ModelInfo(id=model_id, label=line))

        return models
