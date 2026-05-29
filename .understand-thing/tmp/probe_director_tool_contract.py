from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


CONFIG_PATH = Path(r"C:\Users\dains\.polaris\config\llm\llm_config.json")
CANDIDATES = ("minimax-1771264734939", "openai_compat-1779538469833")
TOOL_CHOICE_VARIANTS: tuple[Any, ...] = (
    {"type": "function", "function": {"name": "append_to_file"}},
    "required",
    "auto",
)


def _join_url(base_url: str, api_path: str) -> str:
    return f"{base_url.rstrip('/')}/{api_path.lstrip('/')}"


def _iter_tool_markers(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if key_l in {"tool_calls", "function_call"}:
                found.append({"key": key_l, "value_type": type(value).__name__, "value": value})
            found.extend(_iter_tool_markers(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_iter_tool_markers(item))
    return found


def _tool_names(markers: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for marker in markers:
        value = marker.get("value")
        calls = value if isinstance(value, list) else [value]
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict) and function.get("name"):
                names.append(str(function.get("name")))
                continue
            if call.get("name"):
                names.append(str(call.get("name")))
    return names


def _redacted_error(response: requests.Response) -> str:
    text = response.text.strip()
    if len(text) > 600:
        return f"{text[:600]}...[truncated]"
    return text


def _payload(provider_type: str, model: str, tool_choice: Any) -> dict[str, Any]:
    tool = {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": "Append UTF-8 text to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    }
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Call the append_to_file tool with path 'audit.txt' and content 'probe'. "
                    "Do not answer in plain text."
                ),
            }
        ],
        "tools": [tool],
        "tool_choice": tool_choice,
        "parallel_tool_calls": False,
        "stream": False,
        "max_tokens": 80,
        "temperature": 0,
    }


def _candidate_url(provider: dict[str, Any]) -> str:
    provider_type = str(provider.get("type") or "").strip()
    base_url = str(provider.get("base_url") or "").strip()
    if provider_type == "minimax":
        return _join_url(base_url, "/text/chatcompletion_v2")
    api_path = str(provider.get("api_path") or "/v1/chat/completions").strip()
    return _join_url(base_url, api_path)


def _extract_output_preview(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()[:260]
        text = choices[0].get("text")
        if isinstance(text, str):
            return text.strip()[:260]
    return ""


def _probe_variant(provider_id: str, provider: dict[str, Any], tool_choice: Any) -> dict[str, Any]:
    url = _candidate_url(provider)
    headers = {
        "Authorization": f"Bearer {provider.get('api_key')}",
        "Content-Type": "application/json",
    }
    started = time.perf_counter()
    try:
        response = requests.post(
            url,
            headers=headers,
            json=_payload(str(provider.get("type") or ""), str(provider.get("model") or ""), tool_choice),
            timeout=45,
        )
    except Exception as exc:  # noqa: BLE001 - audit script reports external provider failures.
        return {
            "provider_id": provider_id,
            "provider_type": provider.get("type"),
            "model": provider.get("model"),
            "tool_choice_variant": str(tool_choice),
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:600],
        }
    latency_ms = int((time.perf_counter() - started) * 1000)
    result: dict[str, Any] = {
        "provider_id": provider_id,
        "provider_type": provider.get("type"),
        "model": provider.get("model"),
        "tool_choice_variant": str(tool_choice),
        "http_status": response.status_code,
        "latency_ms": latency_ms,
    }
    if response.status_code >= 400:
        result.update({"ok": False, "error": _redacted_error(response)})
        return result
    try:
        data = response.json()
    except ValueError:
        result.update({"ok": False, "error": "non_json_response"})
        return result
    markers = _iter_tool_markers(data)
    names = _tool_names(markers)
    result.update(
        {
            "ok": bool(names),
            "tool_marker_count": len(markers),
            "tool_names": names,
            "output_preview": _extract_output_preview(data),
            "top_level_keys": sorted(str(k) for k in data.keys()) if isinstance(data, dict) else [],
        }
    )
    return result


def main() -> None:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    results: list[dict[str, Any]] = []
    for provider_id in CANDIDATES:
        provider = providers.get(provider_id) if isinstance(providers, dict) else None
        if not isinstance(provider, dict):
            results.append({"provider_id": provider_id, "ok": False, "error": "provider_missing"})
            continue
        variants = TOOL_CHOICE_VARIANTS if provider.get("type") == "minimax" else TOOL_CHOICE_VARIANTS[:1]
        for tool_choice in variants:
            results.append(_probe_variant(provider_id, provider, tool_choice))
            continue
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
