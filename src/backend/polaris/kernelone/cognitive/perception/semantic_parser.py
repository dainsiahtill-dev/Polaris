"""Semantic Parser - Extracts surface intent from user messages."""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.perception.models import IntentNode


class SemanticParser:
    """
    Parses raw user messages into structured surface intents.
    Uses pattern-based extraction + confidence scoring.
    """

    # Intent patterns for Polaris domain
    INTENT_PATTERNS = {
        "create_file": [
            r"create\s+(?:a\s+)?(?:new\s+)?file",
            r"add\s+(?:a\s+)?(?:new\s+)?(?:file|module|class|endpoint|route|handler|service)",
            r"make\s+(?:a\s+)?(?:new\s+)?(?:file|class)",
            r"new\s+file",
            r"create\s+(?:a\s+)?(?:new\s+)?(?:api|rest|grpc)\s+endpoint",
            r"build\s+(?:a\s+)?(?:new\s+)?(?:file|module|feature)",
            r"create\s+(?:a\s+)?new\s+\w+\s+(?:file|module|test|feature)",
            r"add\s+(?:a\s+)?new\s+\w+\s+(?:file|module|test)",
        ],
        "modify_file": [
            r"update\s+(?:the\s+)?(?:file|code|module|authentication)",
            r"modify\s+(?:the\s+)?(?:file|code|function|module)",
            r"change\s+(?:the\s+)?(?:implementation|code|file)",
            r"edit\s+(?:the\s+)?(?:file|code)",
            r"fix\s+(?:the\s+)?(?:bug|issue|error)",
            r"refactor\s+(?:the\s+)?(?:code|function)",
            r"improve\s+(?:the\s+)?(?:code|file|module)",
            r"enhance\s+(?:the\s+)?(?:code|file|module|authentication)",
            r"update\s+the\s+(?:user\s+)?authentication\s+module",
            r"update\s+the\s+\w+\s+module",
        ],
        "read_file": [
            r"read\s+(?:the\s+)?(?:file|code|content)",
            r"show\s+(?:me\s+)?(?:the\s+)?(?:file|content|code)",
            r"what('s| is)\s+in\s+(?:the\s+)?(?:file|code)",
            r"list\s+(?:the\s+)?(?:files?|directory)",
            r"cat\s+(?:the\s+)?(?:file)",
        ],
        "delete_file": [
            r"delete\s+(?:the\s+)?(?:file|module|temporary|all\s+)?",
            r"remove\s+(?:the\s+)?(?:file|code|temporary)",
            r"clean\s+up\s+(?:the\s+)?(?:file|temporary|files)",
        ],
        "explain": [
            r"explain\s+(?:how|what|why)",
            r"tell\s+(?:me\s+)?(?:how|what|why)",
            r"describe\s+(?:how|what|why)",
            r"what\s+does\s+(?:the\s+)?(?:code|function)\s+do",
        ],
        "plan": [
            r"plan\s+(?:the\s+)?(?:project|task|work)",
            r"create\s+(?:a\s+)?plan",
            r"outline\s+(?:the\s+)?(?:approach|strategy)",
            r"roadmap",
        ],
        "review": [
            r"review\s+(?:the\s+)?(?:code|file|PR)",
            r"check\s+(?:the\s+)?(?:code|file)",
            r"audit\s+(?:the\s+)?(?:code|file)",
        ],
        "test": [
            r"run\s+(?:the\s+)?tests?",
            r"execute\s+(?:the\s+)?tests?",
            r"test\s+(?:the\s+)?(?:code|function)",
        ],
        "search": [
            r"search\s+(?:for\s+)?(?:code|files?|text)",
            r"find\s+(?:the\s+)?(?:file|code|function)",
            r"grep\s+(?:for\s+)?",
            r"ripgrep",
        ],
        "execute_command": [
            r"run\s+(?:the\s+)?command",
            r"execute\s+(?:the\s+)?(?:command|script)",
            r"bash\s+",
            r"shell\s+",
        ],
    }

    def parse(self, message: str, working_state: Any = None) -> tuple[IntentNode, float]:
        """
        Parse a user message into a surface intent node.
        Returns (IntentNode, confidence_score).
        """
        message_lower = message.lower().strip()

        best_intent_type = "unknown"
        best_confidence = 0.0

        for intent_type, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    confidence = self._calculate_confidence(message_lower, intent_type)
                    if confidence > best_confidence:
                        best_intent_type = intent_type
                        best_confidence = confidence

        node = IntentNode(
            node_id=f"intent_{abs(hash(message)) % (10**8):08d}",
            intent_type=best_intent_type,
            content=message,
            confidence=best_confidence,
            source_event_id="semantic_parser",
            uncertainty_factors=self._extract_uncertainty_factors(message_lower),
        )

        return node, best_confidence

    def _calculate_confidence(self, message: str, intent_type: str) -> float:
        """Calculate confidence that message matches intent_type."""
        base = 0.5
        for pattern in self.INTENT_PATTERNS[intent_type]:
            if re.search(pattern, message):
                base = max(base, 0.7)
                # Boost if pattern is long (more specific)
                if len(pattern) > 30:
                    base = min(base + 0.1, 0.95)
        return base

    def _extract_uncertainty_factors(self, message: str) -> tuple[str, ...]:
        """Extract what makes this intent uncertain."""
        factors = []

        uncertainty_markers = [
            "maybe",
            "perhaps",
            "possibly",
            "might",
            "could",
            "not sure",
            "unclear",
            "i think",
            "probably",
            "not certain",
            "might be",
            "could be",
        ]

        for marker in uncertainty_markers:
            if marker in message:
                factors.append(f"uncertainty_marker: {marker}")

        if "?" in message:
            factors.append("question_detected")

        if len(message.split()) < 5:
            factors.append("short_message_low_context")

        return tuple(factors)
