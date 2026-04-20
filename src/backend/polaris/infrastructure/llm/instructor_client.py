"""Instructor Client - Structured LLM output client.

Wraps LLM clients with Instructor for type-safe structured outputs.
Falls back to manual parsing if Instructor is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from polaris.kernelone.constants import DEFAULT_MAX_RETRIES
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Try to import instructor, fallback to manual mode if not available
try:
    from instructor import from_anthropic, from_openai

    INSTRUCTOR_AVAILABLE = True
except ImportError:
    INSTRUCTOR_AVAILABLE = False
    logger.warning("Instructor not installed, using fallback mode")


@dataclass
class StructuredOutputResult:
    """Result of structured output generation."""

    data: dict[str, Any]
    model_instance: BaseModel | None = None
    raw_content: str = ""
    retries_used: int = 0
    from_cache: bool = False


class StructuredLLMClient:
    """Structured output LLM client.

    Uses Instructor when available for reliable structured outputs.
    Falls back to manual JSON parsing for unsupported providers.
    """

    def __init__(self, base_client: Any, provider: str = "openai", enable_instructor: bool = True) -> None:
        """Initialize structured client.

        Args:
            base_client: Base LLM client (OpenAI, Anthropic, etc.)
            provider: Provider identifier
            enable_instructor: Whether to use Instructor if available
        """
        self.base_client = base_client
        self.provider = provider.lower()
        self._instructor_client: Any | None = None

        # Initialize Instructor if available and enabled
        if enable_instructor and INSTRUCTOR_AVAILABLE:
            self._init_instructor()

    def _init_instructor(self) -> None:
        """Initialize Instructor client."""
        try:
            if self.provider == "openai":
                self._instructor_client = from_openai(self.base_client)
            elif self.provider == "anthropic":
                self._instructor_client = from_anthropic(self.base_client)
            else:
                logger.debug(f"Instructor not supported for provider: {self.provider}")
        except (RuntimeError, ValueError) as e:
            logger.warning(f"Failed to initialize Instructor: {e}")
            self._instructor_client = None

    @property
    def is_structured_supported(self) -> bool:
        """Check if native structured output is supported."""
        return self._instructor_client is not None

    async def create_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> T:
        """Create structured output.

        Args:
            messages: Message list for LLM
            response_model: Pydantic model class for output
            model: Model identifier
            temperature: Temperature parameter
            max_tokens: Max tokens to generate
            max_retries: Max retries for validation failures

        Returns:
            Instance of response_model

        Raises:
            ValidationError: If output cannot be parsed after retries
        """
        # Try Instructor first if available
        if self._instructor_client:
            try:
                return await self._instructor_client.chat.completions.create(
                    model=model or "gpt-4",
                    messages=messages,
                    response_model=response_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                )
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Instructor failed: {e}, falling back to manual parsing")

        # Fallback to manual parsing with retries
        return await self._create_with_fallback(
            messages=messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )

    async def _create_with_fallback(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        model: str | None,
        temperature: float,
        max_tokens: int,
        max_retries: int,
    ) -> T:
        """Fallback implementation using manual parsing."""
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                # Add format instruction on retry
                current_messages = self._prepare_messages(messages, response_model, attempt)

                # Call LLM
                response = await self._call_llm(
                    messages=current_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                # Extract JSON
                data = self._extract_json(response)

                # Validate with Pydantic
                return response_model(**data)

            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_error = e
                logger.debug(f"Fallback parsing attempt {attempt + 1} failed: {e}")
                continue

        raise ValidationError.from_exception_data(
            title=response_model.__name__,
            line_errors=[
                {
                    "type": "value_error",
                    "input": None,
                    "loc": ("response",),
                }
            ],
        ) from last_error

    def _prepare_messages(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        attempt: int,
    ) -> list[dict[str, str]]:
        """Prepare messages with schema hint."""
        result = list(messages)

        if attempt > 0:
            # Add schema correction hint
            schema_json = response_model.model_json_schema()
            hint = f"""
Please ensure your response is valid JSON matching this schema:
```json
{json.dumps(schema_json, indent=2, ensure_ascii=False)[:1000]}
```

Previous attempt failed. Make sure to output ONLY the JSON object, wrapped in ```json``` code block.
"""
            result.append({"role": "user", "content": hint})
        else:
            # First attempt - add schema hint to system message
            schema_desc = self._generate_schema_description(response_model)
            result = self._inject_schema_hint(result, schema_desc)

        return result

    def _generate_schema_description(self, model_class: type[BaseModel]) -> str:
        """Generate human-readable schema description."""
        schema = model_class.model_json_schema()
        lines = ["Output must be valid JSON with this structure:"]

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        for name, prop in properties.items():
            is_required = name in required
            desc = prop.get("description", "")
            type_info = prop.get("type", "any")

            if "enum" in prop:
                type_info = f"literal[{', '.join(prop['enum'])}]"

            req_marker = "(required)" if is_required else "(optional)"
            lines.append(f"  - {name}: {type_info} {req_marker}")
            if desc:
                lines.append(f"    {desc}")

        return "\n".join(lines)

    def _inject_schema_hint(self, messages: list[dict[str, str]], schema_desc: str) -> list[dict[str, str]]:
        """Inject schema hint into system message."""
        result = []
        hint_added = False

        for msg in messages:
            if msg.get("role") == "system" and not hint_added:
                content = msg.get("content", "")
                content += f"\n\n[OUTPUT FORMAT]\n{schema_desc}\n\n"
                content += "Wrap your JSON output in ```json``` code block."
                result.append({"role": "system", "content": content})
                hint_added = True
            else:
                result.append(msg)

        if not hint_added:
            result.insert(
                0,
                {
                    "role": "system",
                    "content": f"[OUTPUT FORMAT]\n{schema_desc}\n\nWrap output in ```json``` code block.",
                },
            )

        return result

    async def _call_llm(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call underlying LLM.

        Wraps sync client calls in asyncio.to_thread to avoid blocking event loop.
        """
        # Handle different client APIs
        if self.provider == "openai":
            # Check if client is async
            if hasattr(self.base_client, "chat") and asyncio.iscoroutinefunction(
                getattr(self.base_client.chat.completions, "create", None) or self.base_client.chat.completions.create
            ):
                # AsyncOpenAI client
                response = await self.base_client.chat.completions.create(
                    model=model or "gpt-4",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            else:
                # Sync OpenAI client - run in thread pool
                def _call():
                    response = self.base_client.chat.completions.create(
                        model=model or "gpt-4",
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    return response.choices[0].message.content or ""

                return await asyncio.to_thread(_call)

        elif self.provider == "anthropic":
            # Check if client is async
            if asyncio.iscoroutinefunction(self.base_client.messages.create):
                # Async Anthropic client
                response = await self.base_client.messages.create(
                    model=model or "claude-3-sonnet",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_retries=1,
                )
                return response.content[0].text if response.content else ""
            else:
                # Sync Anthropic client - run in thread pool
                def _call():
                    response = self.base_client.messages.create(
                        model=model or "claude-3-sonnet",
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        max_retries=1,
                    )
                    return response.content[0].text if response.content else ""

                return await asyncio.to_thread(_call)

        else:
            # Generic fallback
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Extract JSON from text."""
        if not text or not text.strip():
            raise ValueError("Empty response")

        text = text.strip()

        # Try JSON code block
        patterns = [
            r"```(?:json)?\s*(\{.*?\})\s*```",  # ```json {...} ```
            r"```(?:json)?\s*(\[.*?\])\s*```",  # ```json [...] ```
            r"(\{[\s\S]*\})",  # Bare JSON object
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                try:
                    return json.loads(match.strip())
                except json.JSONDecodeError:
                    continue

        # Try parsing entire text as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        raise ValueError(f"No valid JSON found in response: {text[:200]}...")


# Convenience factory function
def create_structured_client(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    enable_instructor: bool = True,
    async_mode: bool = True,
) -> StructuredLLMClient:
    """Factory function to create structured client.

    Args:
        provider: LLM provider (openai, anthropic)
        api_key: API key (or use env var)
        base_url: Custom base URL (for proxies)
        enable_instructor: Enable Instructor if available
        async_mode: Whether to use async client (AsyncOpenAI vs OpenAI)

    Returns:
        Configured StructuredLLMClient
    """
    provider = provider.lower()

    base_client: Any
    if provider == "openai":
        if async_mode:
            from openai import AsyncOpenAI

            base_client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
            )
        else:
            from openai import OpenAI

            base_client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
    elif provider == "anthropic":
        if async_mode:
            from anthropic import AsyncAnthropic

            base_client = AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
            )
        else:
            from anthropic import Anthropic

            base_client = Anthropic(
                api_key=api_key,
                base_url=base_url,
            )
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    return StructuredLLMClient(
        base_client=base_client,
        provider=provider,
        enable_instructor=enable_instructor,
    )
