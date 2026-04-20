"""Ollama provider adapter for native `/api/chat` and OpenAI-compat responses."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.llm.provider_adapters.base import (
    AssistantMessage,
    ConversationStateLike,
    DecodedProviderOutput,
    ProviderAdapter,
    ReasoningSummary,
    serialize_input_payload,
    serialize_transcript_for_prompt,
)
from polaris.kernelone.llm.provider_adapters.factory import provider_adapter
from polaris.kernelone.llm.provider_adapters.openai_responses_adapter import (
    OpenAIResponsesAdapter,
    _build_messages_from_transcript,
)

_OPENAI_ADAPTER = OpenAIResponsesAdapter()


def _extract_ollama_tool_calls(message: Any) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []

    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_calls):
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function")
        if not isinstance(function_payload, dict):
            continue
        tool_name = str(function_payload.get("name") or item.get("name") or "").strip()
        if not tool_name:
            continue
        arguments, arguments_text, arguments_complete = serialize_input_payload(function_payload.get("arguments"))
        normalized.append(
            {
                "tool": tool_name,
                "arguments": arguments,
                "arguments_text": arguments_text,
                "arguments_complete": arguments_complete,
                "call_id": str(item.get("id") or function_payload.get("id") or ""),
                "index": item.get("index", index),
            }
        )
    return normalized


@provider_adapter("ollama")
class OllamaChatAdapter(ProviderAdapter):
    """Decode native Ollama chat responses while delegating compat mode to OpenAI."""

    @property
    def provider_name(self) -> str:
        return "ollama"

    def build_request(
        self,
        state: ConversationStateLike,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        messages = _build_messages_from_transcript(state)
        if state.system_prompt:
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = (
                    str(state.system_prompt or "") + "\n" + str(messages[0].get("content") or "")
                ).strip()
            else:
                messages.insert(0, {"role": "system", "content": str(state.system_prompt or "")})

        return {
            "prompt": serialize_transcript_for_prompt(state),
            "config": {
                "messages": messages,
                "system_prompt": state.system_prompt,
                "stream": stream,
            },
        }

    def decode_response(
        self,
        raw_response: Any,
    ) -> DecodedProviderOutput:
        if isinstance(raw_response, dict) and isinstance(raw_response.get("choices"), list):
            return _OPENAI_ADAPTER.decode_response(raw_response)
        return self._decode_ollama_payload(raw_response)

    def decode_stream_event(
        self,
        raw_event: Any,
    ) -> DecodedProviderOutput | None:
        if not isinstance(raw_event, dict):
            return None
        if isinstance(raw_event.get("choices"), list):
            return _OPENAI_ADAPTER.decode_stream_event(raw_event)
        if bool(raw_event.get("done", False)):
            decoded = self._decode_ollama_payload(raw_event)
            if decoded.tool_calls:
                return decoded
            if isinstance(raw_event.get("message"), dict):
                # Native chat terminal frames usually replay the full message.
                # Keep them as metadata-only to avoid duplicated visible output.
                return None
            if decoded.transcript_items:
                return decoded
            # Native Ollama terminal frames frequently echo the full assistant
            # message snapshot. Treat them as terminal metadata rather than a
            # fresh delta to avoid duplicating the visible answer.
            return None
        return self._decode_ollama_payload(raw_event)

    def build_tool_result_payload(
        self,
        tool_result: Any,
    ) -> Any:
        call_id = str(getattr(tool_result, "call_id", "") or "")
        tool_name = str(getattr(tool_result, "tool_name", "") or "")
        content = str(getattr(tool_result, "content", "") or "")
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "content": content,
        }

    def extract_usage(
        self,
        raw_response: Any,
    ) -> dict[str, Any]:
        if not isinstance(raw_response, dict):
            return {}
        usage = raw_response.get("usage")
        if isinstance(usage, dict):
            return dict(usage)
        prompt_tokens = int(raw_response.get("prompt_eval_count") or 0)
        completion_tokens = int(raw_response.get("eval_count") or 0)
        total_tokens = int(raw_response.get("total_tokens") or (prompt_tokens + completion_tokens))
        if total_tokens <= 0:
            return {}
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _decode_ollama_payload(
        self,
        raw_payload: Any,
    ) -> DecodedProviderOutput:
        if not isinstance(raw_payload, dict):
            return DecodedProviderOutput(raw=raw_payload)

        message = raw_payload.get("message")
        transcript_items: list[Any] = []
        if isinstance(message, dict):
            thinking = str(message.get("thinking") or "").strip()
            if thinking:
                transcript_items.append(ReasoningSummary(content=thinking))

            content = str(message.get("content") or "")
            if content:
                transcript_items.append(AssistantMessage(content=content))

            return DecodedProviderOutput(
                transcript_items=transcript_items,
                tool_calls=_extract_ollama_tool_calls(message),
                usage=self.extract_usage(raw_payload),
                raw=raw_payload,
            )

        # Native `/api/generate` emits top-level `response` deltas.
        thinking = str(raw_payload.get("thinking") or "").strip()
        if thinking:
            transcript_items.append(ReasoningSummary(content=thinking))
        response_content = str(raw_payload.get("response") or raw_payload.get("content") or "")
        if response_content:
            transcript_items.append(AssistantMessage(content=response_content))

        return DecodedProviderOutput(
            transcript_items=transcript_items,
            tool_calls=[],
            usage=self.extract_usage(raw_payload),
            raw=raw_payload,
        )
