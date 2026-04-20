from __future__ import annotations

from .openai_sdk import OpenAISDK


class CodexSDK(OpenAISDK):
    """Codex SDK wrapper built on top of the OpenAI SDK client."""

    def supports_feature(self, feature: str) -> bool:
        return feature in {
            "thinking",
            "streaming",
            "file_operations",
            "function_calling",
            "json_mode",
        } or super().supports_feature(feature)
