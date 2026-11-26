"""Remote OpenAI-compatible semantic advisor with strict contract validation."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import validate_node_summary


def _json_content(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("semantic advisor returned no JSON object")
    return text[start : end + 1]


@dataclass(frozen=True)
class RemoteAdvisorConfig:
    base_url: str
    model: str
    timeout_seconds: float = 120.0
    max_output_tokens: int = 800


def advisor_configs_from_workspace(workspace: Path) -> list[RemoteAdvisorConfig]:
    """Read the advisor pool from workspace-local config, falling back to env."""

    path = Path(workspace) / ".polaris" / "config.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            agents = (data.get("agents") or {}).get("distiller") or []
            if agents:
                return [
                    RemoteAdvisorConfig(base_url=str(item["base_url"]), model=str(item["model"])) for item in agents
                ]
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            pass
    raw = os.environ.get("KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG", "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
        return [RemoteAdvisorConfig(base_url=str(item["base_url"]), model=str(item["model"])) for item in items]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []


_DISTILLER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "source_line", "uncertain"],
    "properties": {
        "summary": {"type": "string"},
        "source_line": {"type": "integer"},
        "uncertain": {"type": "boolean"},
    },
}


_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "node_id",
        "snapshot_id",
        "summary",
        "summary_kind",
        "coverage",
        "evidence_refs",
        "uncertain",
        "reason",
    ],
    "properties": {
        "node_id": {"type": "string"},
        "snapshot_id": {"type": "string"},
        "summary": {"type": "string"},
        "summary_kind": {"type": "string", "const": "model_inference"},
        "coverage": {
            "type": "object",
            "additionalProperties": False,
            "required": ["observed_line_ranges", "summary_complete"],
            "properties": {
                "observed_line_ranges": {
                    "type": "array",
                    "maxItems": 1,
                    "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer"}},
                    "summary_complete": {"type": "boolean"},
                },
            },
            "evidence_refs": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
            "uncertain": {"type": "boolean"},
            "reason": {"type": "string", "maxLength": 160},
        },
    },
}


class RemoteSemanticAdvisor:
    """Small remote model client; no engineering tools or request mutation."""

    def __init__(self, config: RemoteAdvisorConfig) -> None:
        self.config = config
        self.last_usage: dict[str, Any] = {}
        self.last_timings: dict[str, Any] = {}
        self.last_normalization: list[str] = []

    def _post(self, messages: list[dict[str, str]], schema: dict[str, Any] | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.config.max_output_tokens,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "contextos_advice", "strict": True, "schema": schema},
            }
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if schema is None or error.code != 400:
                raise
            # Older compatible servers may not implement json_schema. Fall back
            # to the same bounded prompt, then apply the host validator anyway.
            return self._post(messages, None)
        self.last_usage = payload.get("usage") or {}
        self.last_timings = payload.get("timings") or {}
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("semantic advisor returned no choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("semantic advisor returned non-text content")
        return content

    async def summarize_node(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("node_kind")
        distilling = kind in ("file_card", "write_call", "command_card")
        schema = _SUMMARY_SCHEMA
        messages: list[dict[str, str]]
        if distilling:
            if kind == "command_card":
                focus = "Preserve the diagnostic, result or observed value that distinguishes the next debugging step."
            elif kind == "write_call":
                focus = (
                    "Describe the interface, branch or invariant expressed by this write body. Execution is unobserved."
                )
            else:
                focus = "Describe a local interface, condition or data relationship visible within this read slice."
            schema = _DISTILLER_SCHEMA
            source_lines = [
                [index, line]
                for index, line in enumerate(str(request.get("content") or "").splitlines(), 1)
                if line.strip()
            ]
            source_name = str(request["node_id"]).partition(":")[2] if kind != "command_card" else "command output"
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"{focus} Choose one numbered line showing the property you explain. "
                        "Return only summary (one short sentence in plain language), source_line (its integer index) "
                        "and uncertain (boolean). The host will copy the evidence. Do not copy the line or add quotes. "
                        "For implementation behavior, prefer a substantive executable condition or operation over a "
                        "comment when available. Preserve conditions. A comment states intent; unseen callers, successful execution and "
                        "public exposure need separate evidence. Mention exposure only when your chosen declaration "
                        "or export list shows it. Use uncertain=true when the slice cannot establish a consequence."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"node_kind": kind, "name": source_name, "source_lines": source_lines}, ensure_ascii=False
                    ),
                },
            ]
        else:
            example = {
                "node_id": request["node_id"],
                "snapshot_id": request["snapshot_id"],
                "summary": "<one concrete observation with a short exact excerpt in backticks and its meaning>",
                "summary_kind": "model_inference",
                "coverage": {
                    "observed_line_ranges": request["coverage"]["observed_line_ranges"],
                    "summary_complete": False,
                },
                "evidence_refs": [request["snapshot_id"], *request.get("allowed_evidence_refs", [])],
                "uncertain": True,
                "reason": "<which consequence or caller behavior this slice cannot establish>",
            }
            system = (
                "You are a read-only ContextOS node summarizer. Return only one JSON object with exactly these keys: "
                "node_id,snapshot_id,summary,summary_kind,coverage,evidence_refs,uncertain,reason. "
                "summary_kind must be model_inference. Do not claim completion or test success. "
                "Describe what the payload contains and why it matters; never answer with a bare status "
                "word (completed/ok/failed) or a verbatim excerpt of the payload, because the summary "
                "replaces the payload in the next request. Explain one useful distinction. "
                "Quote the short declaration, comparison or diagnostic that supports it exactly in backticks. "
                "Include visibility modifiers when claiming an interface is exported. Preserve input preconditions. "
                "Treat comments about unseen callers as documented intent, not observed behavior. "
                "Do not infer successful execution or architecture beyond this slice. "
                "Keep summary within 192 tokens and reason within 160 characters. "
                "Every key is required; evidence_refs and reason are never omitted. "
                "observed_line_ranges appears exactly once; never repeat keys or array items. Example: "
                + json.dumps(example, ensure_ascii=False)
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ]
        last_error: ValueError | None = None
        for _ in range(2):
            content = await asyncio.to_thread(self._post, messages, schema)
            try:
                payload = json.loads(_json_content(content))
                self.last_normalization = []
                if distilling and isinstance(payload, dict) and "source_line" in payload:
                    if (
                        set(payload) != {"summary", "source_line", "uncertain"}
                        or type(payload["source_line"]) is not int
                    ):
                        raise ValueError("use only summary, integer source_line and boolean uncertain")
                    host_bound = {
                        "node_id": request["node_id"],
                        "snapshot_id": request["snapshot_id"],
                        "summary": payload["summary"],
                        "summary_kind": "model_inference",
                        "coverage": {
                            "observed_line_ranges": request["coverage"]["observed_line_ranges"],
                            "summary_complete": False,
                        },
                        "evidence_refs": [request["snapshot_id"]],
                        "uncertain": payload["uncertain"],
                        "reason": "Based on the selected observed line; effects outside this slice are unverified.",
                    }
                    return validate_node_summary(host_bound, request, selected_line=payload["source_line"])
                if isinstance(payload, dict):
                    coverage_value = payload.get("coverage")
                    coverage = coverage_value if isinstance(coverage_value, dict) else {}
                    if "summary_complete" not in coverage:
                        coverage["summary_complete"] = False
                        self.last_normalization.append("coverage.summary_complete=host_default_false")
                        payload["coverage"] = coverage
                    allowed_refs = set(request.get("allowed_evidence_refs", [])) | {str(request["snapshot_id"])}
                    raw_refs = payload.get("evidence_refs")
                    if isinstance(raw_refs, list):
                        if any(not isinstance(ref, str) for ref in raw_refs):
                            raise ValueError("evidence_refs must contain only strings")
                        payload["evidence_refs"] = list(dict.fromkeys(ref for ref in raw_refs if ref in allowed_refs))[
                            :12
                        ]
                        if not payload["evidence_refs"]:
                            payload["evidence_refs"] = [request["snapshot_id"]]
                        self.last_normalization.append("evidence_refs=whitelist_dedup_cap12")
                    if "evidence_refs" not in payload:
                        payload["evidence_refs"] = [request["snapshot_id"], *request.get("allowed_evidence_refs", [])][
                            :12
                        ]
                        self.last_normalization.append("evidence_refs=host_whitelist")
                    refs = payload.get("evidence_refs")
                    if isinstance(refs, list):
                        payload["evidence_refs"] = list(dict.fromkeys(str(ref) for ref in refs))[:12]
                    if "uncertain" not in payload:
                        payload["uncertain"] = True
                        self.last_normalization.append("uncertain=host_conservative_true")
                    if "reason" not in payload:
                        payload["reason"] = "Model omitted required fields; host treats the summary as uncertain."
                        self.last_normalization.append("reason=host_uncertain_default")
                return validate_node_summary(payload, request)
            except ValueError as error:
                last_error = error
                messages.extend(
                    [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "The previous JSON failed host validation: "
                                + str(error)
                                + ". Return the corrected complete JSON object with every required key, and no other text."
                            ),
                        },
                    ]
                )
        raise ValueError(f"node summary failed validation after one corrective retry: {last_error}")
