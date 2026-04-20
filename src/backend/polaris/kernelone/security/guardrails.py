"""Composable security guardrails chain."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.security.sanitization_hook import SanitizationHook

_JAILBREAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+all\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"bypass\s+safety", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
)

_HARMFUL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"how\s+to\s+build\s+(?:a\s+)?bomb", re.IGNORECASE),
    re.compile(r"write\s+malware", re.IGNORECASE),
    re.compile(r"steal\s+credentials", re.IGNORECASE),
)


@dataclass(frozen=True)
class GuardrailStageResult:
    name: str
    allowed: bool
    output: str
    latency_ms: float
    reason: str = ""


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    output: str
    blocked_reason: str = ""
    stage_results: tuple[GuardrailStageResult, ...] = ()

    def stage_latency_map(self) -> dict[str, float]:
        return {item.name: item.latency_ms for item in self.stage_results}


class InputGuard:
    """Block known jailbreak prompt patterns."""

    def process(self, text: str) -> GuardrailStageResult:
        started_ns = time.perf_counter_ns()
        for pattern in _JAILBREAK_PATTERNS:
            if pattern.search(text):
                return GuardrailStageResult(
                    name="input_guard",
                    allowed=False,
                    output=text,
                    latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
                    reason="jailbreak_pattern_detected",
                )
        return GuardrailStageResult(
            name="input_guard",
            allowed=True,
            output=text,
            latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
        )


class PIIMask:
    """Mask sensitive values from prompt text."""

    def __init__(self) -> None:
        self._hook = SanitizationHook()

    def process(self, text: str) -> GuardrailStageResult:
        started_ns = time.perf_counter_ns()
        sanitized = self._hook.sanitize({"text": text})
        masked = str(sanitized.get("text", text))
        return GuardrailStageResult(
            name="pii_mask",
            allowed=True,
            output=masked,
            latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
        )


class OutputVerifier:
    """Verify output schema/quality at a lightweight level."""

    def process(self, text: str) -> GuardrailStageResult:
        started_ns = time.perf_counter_ns()
        if not str(text).strip():
            return GuardrailStageResult(
                name="output_verifier",
                allowed=False,
                output=text,
                latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
                reason="empty_output",
            )
        return GuardrailStageResult(
            name="output_verifier",
            allowed=True,
            output=text,
            latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
        )


class HarmfulContentBlocker:
    """Block harmful output intents."""

    def process(self, text: str) -> GuardrailStageResult:
        started_ns = time.perf_counter_ns()
        for pattern in _HARMFUL_PATTERNS:
            if pattern.search(text):
                return GuardrailStageResult(
                    name="harmful_content_blocker",
                    allowed=False,
                    output=text,
                    latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
                    reason="harmful_intent_detected",
                )
        return GuardrailStageResult(
            name="harmful_content_blocker",
            allowed=True,
            output=text,
            latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
        )


@dataclass
class GuardrailsChain:
    """InputGuard -> PIIMask -> OutputVerifier -> HarmfulContentBlocker."""

    input_guard: InputGuard = field(default_factory=InputGuard)
    pii_mask: PIIMask = field(default_factory=PIIMask)
    output_verifier: OutputVerifier = field(default_factory=OutputVerifier)
    harmful_blocker: HarmfulContentBlocker = field(default_factory=HarmfulContentBlocker)

    def process(self, text: str) -> GuardrailResult:
        stage_results: list[GuardrailStageResult] = []

        first = self.input_guard.process(text)
        stage_results.append(first)
        if not first.allowed:
            return GuardrailResult(
                allowed=False,
                output=first.output,
                blocked_reason=first.reason,
                stage_results=tuple(stage_results),
            )

        masked = self.pii_mask.process(first.output)
        stage_results.append(masked)

        verified = self.output_verifier.process(masked.output)
        stage_results.append(verified)
        if not verified.allowed:
            return GuardrailResult(
                allowed=False,
                output=verified.output,
                blocked_reason=verified.reason,
                stage_results=tuple(stage_results),
            )

        harmful = self.harmful_blocker.process(verified.output)
        stage_results.append(harmful)
        if not harmful.allowed:
            return GuardrailResult(
                allowed=False,
                output=harmful.output,
                blocked_reason=harmful.reason,
                stage_results=tuple(stage_results),
            )

        return GuardrailResult(
            allowed=True,
            output=harmful.output,
            stage_results=tuple(stage_results),
        )

    def summarize_latencies(self, result: GuardrailResult) -> dict[str, Any]:
        stage_latencies = result.stage_latency_map()
        return {
            "stages": stage_latencies,
            "full_chain_ms": sum(stage_latencies.values()),
            "allowed": result.allowed,
            "blocked_reason": result.blocked_reason,
        }


__all__ = [
    "GuardrailResult",
    "GuardrailStageResult",
    "GuardrailsChain",
    "HarmfulContentBlocker",
    "InputGuard",
    "OutputVerifier",
    "PIIMask",
]
