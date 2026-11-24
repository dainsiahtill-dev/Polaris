"""Kimi Coding EpochCard summarizer using the configured Anthropic-compatible provider."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import validate_node_summary
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import _json_content


def _parse_lenient_json(text: str) -> dict[str, Any]:
    """Parse model JSON, tolerating trailing commas and a truncated tail."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError("Kimi Epoch response is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("Kimi Epoch response must be a JSON object")
    return payload


def _normalize_epoch_payload(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Tolerate missing optional fields without inventing semantic claims."""

    normalized = dict(payload)
    normalized["node_id"] = request["node_id"]
    normalized["snapshot_id"] = request["snapshot_id"]
    normalized["summary_kind"] = "model_inference"
    coverage = normalized.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    normalized["coverage"] = {
        "observed_line_ranges": request.get("coverage", {}).get("observed_line_ranges")
        or coverage.get("observed_line_ranges")
        or [[1, 1]],
        "summary_complete": bool(coverage.get("summary_complete", False)),
    }
    allowed = set(request.get("allowed_evidence_refs") or []) | {str(request["snapshot_id"])}
    raw_refs = normalized.get("evidence_refs")
    if isinstance(raw_refs, list):
        normalized["evidence_refs"] = list(dict.fromkeys(ref for ref in raw_refs if ref in allowed))[:12] or [
            str(request["snapshot_id"])
        ]
    else:
        normalized["evidence_refs"] = [str(request["snapshot_id"])]
    normalized["uncertain"] = bool(normalized.get("uncertain", True))
    if not isinstance(normalized.get("reason"), str) or not normalized["reason"].strip():
        normalized["reason"] = "Model omitted required fields; host treats the EpochCard as uncertain."
    return normalized


class KimiEpochPool:
    """Small pool facade for the configured Kimi Epoch summarizer."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def _post(self, messages: list[dict[str, str]]) -> str:
        system = (
            "You are the ContextOS Epoch devourer. Summarize one closed engineering epoch into useful long-term memory. "
            "Return only one JSON object with node_id,snapshot_id,summary,summary_kind,coverage,evidence_refs,uncertain,reason. "
            "summary_kind=model_inference. Preserve unresolved failures, UNKNOWN, key decisions, changed files, verification history "
            "and current dependencies. Do not claim acceptance or invent evidence. observed_line_ranges must exactly match the request. "
            "Keep summary within 1000 characters and reason within 160 characters; the summary replaces the epoch detail, so "
            "describe it rather than quoting it. "
            "Do NOT restate the user goal or the requirement text — the model already has them. "
            "Summarize what THIS epoch actually did: which files changed, which checks ran and their real verdicts, "
            "what is still unresolved, and what the next epoch must not repeat. "
            "实测旧摘要通篇复述目标（'本次epoch…处理冻结需求：用TypeScript实现…'），对模型零价值。"
        )
        body = {
            "model": self.model,
            "max_tokens": 1400,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        http_request = urllib.request.Request(
            self.base_url.rstrip("/") + "/v1/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            payload = json.load(response)
        content = payload.get("content") or []
        text = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        if not text:
            raise ValueError("Kimi Epoch summarizer returned no text")
        return text

    async def summarize_many(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for request in requests:
            async with self._lock:
                messages: list[dict[str, str]] = [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
                last_content = ""
                failure: str | None = None
                for _ in range(2):
                    try:
                        last_content = await asyncio.to_thread(self._post, messages)
                        payload = _parse_lenient_json(_json_content(last_content))
                        result = validate_node_summary(_normalize_epoch_payload(payload, request), request)
                        results.append({"endpoint_index": 0, "validation_result": "VALID", "result": result})
                        failure = None
                        break
                    except (ValueError, OSError, asyncio.TimeoutError) as error:
                        # One corrective retry: without it a single over-long
                        # summary permanently starves the epoch memory.
                        failure = f"{type(error).__name__}: {error}"
                        messages = [
                            *messages,
                            {"role": "assistant", "content": last_content},
                            {
                                "role": "user",
                                "content": (
                                    "The previous JSON failed host validation: "
                                    + failure
                                    + ". Return the corrected complete JSON object with every required key, and no other text."
                                ),
                            },
                        ]
                if failure is not None:
                    results.append(
                        {
                            "endpoint_index": 0,
                            "validation_result": "REJECTED",
                            "error": failure,
                        }
                    )
        return results


def kimi_epoch_pool_from_environment() -> KimiEpochPool | None:
    """Read the unified LLM config and select the Kimi highspeed Epoch model."""

    raw_path = os.environ.get("KERNELONE_LLM_CONFIG", "/home/dains/.polaris/config/llm/llm_config.json")
    path = Path(raw_path)
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        provider_id = os.environ.get("KERNELONE_CONTEXTOS_EPOCH_PROVIDER_ID", "anthropic_compat-1771249789301")
        role = config.get("roles", {}).get("devourer") or {}
        provider_id = str(role.get("provider_id") or provider_id)
        provider = config["providers"][provider_id]
        api_key = str(provider.get("api_key") or "")
        if not api_key:
            return None
        return KimiEpochPool(
            base_url=str(provider["base_url"]),
            api_key=api_key,
            model=str(
                role.get("model") or os.environ.get("KERNELONE_CONTEXTOS_EPOCH_MODEL", "kimi-for-coding-highspeed")
            ),
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None
