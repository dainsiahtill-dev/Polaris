from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schema import MemoryItem, ReflectionNode

try:
    from polaris.kernelone.process.ollama_utils import OllamaResponse, invoke_ollama
    from polaris.kernelone.prompts.loader import get_template, render_template
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class OllamaMetadata:  # type: ignore[no-redef]
        """Fallback metadata for environments without Ollama integration."""

        done: bool = False
        done_reason: str | None = None
        prompt_eval_count: int = 0
        eval_count: int = 0
        truncated: bool = False
        finish_reason: str | None = None
        error: str = ""
        error_type: str = ""

    @dataclass
    class OllamaResponse:  # type: ignore[no-redef]
        """Fallback OllamaResponse for environments without Ollama integration."""

        output: str = ""
        metadata: OllamaMetadata | None = None

        def __post_init__(self) -> None:
            if self.metadata is None:
                self.metadata = OllamaMetadata(error="Ollama integration unavailable")

    def invoke_ollama(  # type: ignore[misc]
        prompt: str,
        model: str,
        workspace: str = "",
        show_output: bool = False,
        timeout: int = 0,
        usage_ctx: Any = None,
        events_path: str = "",
    ) -> OllamaResponse:
        """Fallback LLM invocation for environments without Ollama integration."""
        return OllamaResponse(output="", metadata=OllamaMetadata(error="Ollama integration unavailable"))  # type: ignore[arg-type]

    def get_template(_name: str, _profile: str | None = None) -> str:  # type: ignore[misc]
        raise FileNotFoundError("prompt template loader is unavailable")

    def render_template(template: str, context: dict[str, object]) -> str:  # type: ignore[misc]
        rendered = str(template or "")
        for key, value in context.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        return rendered


FALLBACK_REFLECTION_TEMPLATE = (
    "You are a reflection generator.\n"
    "Given memory snippets, return a JSON array. "
    "Each item must include: scope(list[str]), text(str), confidence(float 0..1), expiry_steps(int).\n"
    "Do not output markdown.\n\n"
    "memories:\n{{memories_text}}"
)

logger = logging.getLogger(__name__)
REFLECTION_MAX_ATTEMPTS = 3
REFLECTION_RETRY_BACKOFF_SECONDS = (0.0, 0.1, 0.2)


def parse_json_garbage(text: object) -> list[object]:
    """Robust JSON parser for LLM output."""
    import re

    text_value = str(text or "").strip()
    # Try to find JSON array
    match = re.search(r"\[.*\]", text_value, re.DOTALL)
    if match:
        text_value = match.group(0)
    try:
        payload = json.loads(text_value)
        return payload if isinstance(payload, list) else []
    except json.JSONDecodeError:
        return []


class ReflectionStore:
    def __init__(self, reflection_file: str) -> None:
        self.reflection_file = reflection_file
        self.reflections: list[ReflectionNode] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.reflection_file):
            return

        with open(self.reflection_file, encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    self.reflections.append(ReflectionNode(**data))
                except (json.JSONDecodeError, ValidationError) as exc:
                    logger.debug(
                        "Skipping malformed reflection record: path=%s line=%s error=%s",
                        self.reflection_file,
                        line_number,
                        exc,
                    )
                    continue

    def append(self, node: ReflectionNode) -> None:
        self.reflections.append(node)
        parent = Path(self.reflection_file).parent
        parent.mkdir(parents=True, exist_ok=True)
        with open(self.reflection_file, "a", encoding="utf-8") as handle:
            handle.write(node.model_dump_json() + "\n")

    def retrieve_active(self, current_step: int) -> list[ReflectionNode]:
        """Returns valid reflections that haven't expired."""
        active = []
        for ref in self.reflections:
            age = current_step - ref.created_step
            if age <= ref.expiry_steps:
                active.append(ref)
        return active

    def get_last_reflection_step(self) -> int:
        """Returns the step of the last created reflection."""
        if not self.reflections:
            return 0
        # Assuming append-only, last one is latest.
        # But to be safe, max of created_step
        return max(r.created_step for r in self.reflections)


class ReflectionScheduler:
    def should_reflect(self, current_step: int, last_reflection_step: int, recent_error_count: int) -> bool:
        """
        Reflect if:
        1. > 50 steps since last reflection
        2. > 3 errors recently
        """
        if current_step - last_reflection_step > 50:
            return True
        return recent_error_count >= 3


# Generator would interface with LLM, kept as placeholder for now
class ReflectionGenerator:
    def __init__(self, model: str, workspace_root: str) -> None:
        self.model = model
        self.workspace_root = workspace_root

    def generate(self, memories: list[MemoryItem], current_step: int) -> list[ReflectionNode]:
        if not memories:
            return []

        # Format memories for prompt
        mem_text = "\n".join([f"- [{m.kind.upper()}] {m.text}" for m in memories])

        template_str = FALLBACK_REFLECTION_TEMPLATE
        try:
            loaded = get_template("reflection_generator")
            if isinstance(loaded, str) and loaded.strip():
                template_str = loaded
        except (RuntimeError, ValueError) as exc:
            logger.debug("Using fallback reflection template due to loader error: %s", exc)

        prompt = render_template(template_str, {"memories_text": mem_text})

        data = []
        for attempt in range(REFLECTION_MAX_ATTEMPTS):
            output = invoke_ollama(
                prompt,
                self.model,
                self.workspace_root,
                show_output=False,
                timeout=120,
            )
            output_text = getattr(output, "output", output)
            output_metadata = getattr(output, "metadata", {})
            if isinstance(output_metadata, dict) and output_metadata.get("error"):
                logger.warning(
                    "Reflection generation attempt failed: attempt=%s/%s error=%s",
                    attempt + 1,
                    REFLECTION_MAX_ATTEMPTS,
                    output_metadata.get("error"),
                )
            data = parse_json_garbage(output_text)
            if isinstance(data, list) and data:
                break
            logger.debug(
                "Reflection generation returned no structured payload: attempt=%s/%s",
                attempt + 1,
                REFLECTION_MAX_ATTEMPTS,
            )
            if attempt + 1 < REFLECTION_MAX_ATTEMPTS:
                time.sleep(REFLECTION_RETRY_BACKOFF_SECONDS[min(attempt, len(REFLECTION_RETRY_BACKOFF_SECONDS) - 1)])
        if not data:
            logger.warning(
                "Reflection generation exhausted retries without valid structured output: attempts=%s",
                REFLECTION_MAX_ATTEMPTS,
            )

        reflections = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                raw_confidence = float(item.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, raw_confidence))
                expiry_steps = int(item.get("expiry_steps", 100))
                expiry_steps = max(1, expiry_steps)
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                raw_scope = item.get("scope", ["general"])
                if isinstance(raw_scope, list):
                    scope = [str(value).strip() for value in raw_scope if str(value).strip()]
                elif isinstance(raw_scope, str) and raw_scope.strip():
                    scope = [raw_scope.strip()]
                else:
                    scope = ["general"]
                reflections.append(
                    ReflectionNode(
                        created_step=current_step,
                        scope=scope,
                        text=text,
                        confidence=confidence,
                        expiry_steps=expiry_steps,
                        type="heuristic",
                        evidence_mem_ids=[m.id for m in memories],
                        importance=5,
                    )
                )
            except (ValidationError, ValueError, TypeError):
                continue

        return reflections
