"""Document rendering utilities for orchestration."""

import json
import logging
import os
import re
from typing import Any

from .directive_processing import (
    _contains_prompt_leakage,
)
from .helpers import (
    _role_llm_docs_enabled,
)

logger = logging.getLogger(__name__)


def _doc_generation_brief(rel_path: str) -> str:
    """Generate brief description for document."""
    token = str(rel_path or "").strip().replace("\\", "/").lower()
    if token.endswith("requirements.md"):
        return (
            "Write a rich product requirements document with domain narrative, functional slices, "
            "non-functional requirements, explicit constraints, and measurable acceptance criteria."
        )
    if token.endswith("plan.md"):
        return (
            "Write a delivery plan with phased milestones, verification matrix, risks, rollback plans, "
            "and ownership notes for PM/ChiefEngineer/Director/QA."
        )
    if token.endswith("adr.md"):
        return (
            "Write multiple architecture decision records with context, decision, alternatives, tradeoffs, "
            "and rollback triggers."
        )
    if token.endswith("interface_contract.md"):
        return (
            "Write interface contracts with clear boundary definitions, request/response structures, "
            "error semantics, and verification evidence mappings."
        )
    return "Write a concrete, reviewable engineering document with actionable detail and acceptance hooks."


def _build_architect_doc_prompt(
    *,
    rel_path: str,
    fields: dict[str, str],
    qa_commands: list[str],
    template_text: str,
) -> str:
    """Build architect document generation prompt."""
    context_payload = {
        "goal": str(fields.get("goal") or "").strip(),
        "in_scope": str(fields.get("in_scope") or "").strip(),
        "out_of_scope": str(fields.get("out_of_scope") or "").strip(),
        "constraints": str(fields.get("constraints") or "").strip(),
        "definition_of_done": str(fields.get("definition_of_done") or "").strip(),
        "backlog": str(fields.get("backlog") or "").strip(),
        "qa_commands": [str(item).strip() for item in qa_commands if str(item).strip()],
    }
    context_json = json.dumps(context_payload, ensure_ascii=False, indent=2)
    return (
        "你是 Polaris 的中书令（架构文档作者）。\n"
        "只输出 Markdown 文档正文，不要解释，不要代码块围栏，不要 JSON。\n"
        "要求：\n"
        "- 内容必须具体、可审查、可执行，避免空泛模板腔。\n"
        "- 必须给出可验证条目（命令/证据/验收条件）。\n"
        "- 不得复制角色设定或系统提示词原文。\n"
        f"- 文档目标：{_doc_generation_brief(rel_path)}\n"
        f"- 当前文档路径：{rel_path}\n\n"
        "项目上下文（JSON）：\n"
        f"{context_json}\n\n"
        "参考草稿（可重写，不要求保留结构）：\n"
        f"{template_text}\n"
    )


def _document_quality_ok(markdown_text: str) -> bool:
    """Check if generated document quality is acceptable."""
    text = str(markdown_text or "").strip()
    if len(text) < 320:
        return False
    if _contains_prompt_leakage(text):
        return False
    headings = len(re.findall(r"^\s{0,3}#{1,4}\s+\S+", text, flags=re.MULTILINE))
    bullets = len(re.findall(r"^\s*[-*]\s+\S+", text, flags=re.MULTILINE))
    table_rows = len(re.findall(r"^\s*\|.+\|\s*$", text, flags=re.MULTILINE))
    interface_mode = "interface contract" in text.lower()
    lowered = text.lower()
    if interface_mode:
        if headings < 4 or table_rows < 8 or bullets < 3:
            return False
    elif headings < 4 or bullets < 5:
        return False
    meaningful_lines = [
        re.sub(r"\s+", " ", line.strip().lower())
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("|")
    ]
    if meaningful_lines:
        unique_ratio = len(set(meaningful_lines)) / max(1, len(meaningful_lines))
        min_unique_ratio = 0.3 if interface_mode else 0.55
        if unique_ratio < min_unique_ratio:
            return False
    sentences = [
        re.sub(r"\s+", " ", item.strip().lower())
        for item in re.split(r"[。！？!?]\s*", text)
        if len(item.strip()) >= 16
    ]
    if sentences and len(set(sentences)) / max(1, len(sentences)) < 0.6:
        min_sentence_unique = 0.3 if interface_mode else 0.6
        if len(set(sentences)) / max(1, len(sentences)) < min_sentence_unique:
            return False
    placeholder_hits = sum(lowered.count(token) for token in ("tbd", "待补充", "placeholder", "lorem ipsum"))
    return not placeholder_hits > 2


def _normalize_doc_markdown(text: str) -> str:
    """Normalize document markdown text."""
    body = str(text or "").strip()
    if not body:
        return ""
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"^```(?:markdown|md)?\s*", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s*```$", "", body)
    return body.strip() + "\n"


def _render_llm_authored_docs(
    *,
    workspace_full: str,
    docs_map: dict[str, str],
    fields: dict[str, str],
    qa_commands: list[str],
    fallback_model: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Render LLM-authored documents."""
    if not _role_llm_docs_enabled():
        return docs_map, {"enabled": False, "attempted": 0, "accepted": 0}
    try:
        from polaris.cells.llm.provider_runtime.public import (
            invoke_role_runtime_provider,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to import invoke_role_runtime_provider for role LLM docs, skipping rendering: %s",
            exc,
        )
        return docs_map, {
            "enabled": True,
            "attempted": 0,
            "accepted": 0,
            "error": "runtime_invoke_unavailable",
        }

    rendered: dict[str, str] = {}
    attempted = 0
    accepted = 0
    failures: list[str] = []
    failure_reasons: list[dict[str, str]] = []
    timeout_raw = str(os.environ.get("KERNELONE_ARCHITECT_LLM_DOC_TIMEOUT", "60") or "60").strip()
    try:
        timeout_seconds = int(timeout_raw)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse KERNELONE_ARCHITECT_LLM_DOC_TIMEOUT %r, using default 60: %s",
            timeout_raw,
            exc,
        )
        timeout_seconds = 60
    timeout_seconds = max(timeout_seconds, 15)
    retries_raw = str(os.environ.get("KERNELONE_ARCHITECT_LLM_DOC_RETRIES", "2") or "2").strip()
    try:
        retries = int(retries_raw)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse KERNELONE_ARCHITECT_LLM_DOC_RETRIES %r, using default 2: %s",
            retries_raw,
            exc,
        )
        retries = 2
    retries = max(0, min(4, retries))
    for rel_path, template_text in docs_map.items():
        attempted += 1
        accepted_doc = False
        last_error = ""
        for attempt in range(1, retries + 2):
            prompt = _build_architect_doc_prompt(
                rel_path=rel_path,
                fields=fields,
                qa_commands=qa_commands,
                template_text=str(template_text or "") if attempt == 1 else "",
            )
            result = invoke_role_runtime_provider(
                role="architect",
                workspace=workspace_full,
                prompt=prompt,
                fallback_model=fallback_model,
                timeout=timeout_seconds,
                blocked_provider_types=None,
            )
            candidate = _normalize_doc_markdown(result.output)
            if result.ok and _document_quality_ok(candidate):
                rendered[rel_path] = candidate
                accepted += 1
                accepted_doc = True
                break
            last_error = str(getattr(result, "error", "") or "").strip()
        if not accepted_doc:
            rendered[rel_path] = str(template_text or "")
            failures.append(rel_path)
            failure_reasons.append(
                {
                    "doc": rel_path,
                    "error": last_error or "quality_gate_or_invoke_failed",
                }
            )
    return rendered, {
        "enabled": True,
        "attempted": attempted,
        "accepted": accepted,
        "retries": retries,
        "fallback_count": max(0, attempted - accepted),
        "fallback_docs": failures,
        "failure_reasons": failure_reasons,
    }


__all__ = [
    "_build_architect_doc_prompt",
    "_doc_generation_brief",
    "_document_quality_ok",
    "_normalize_doc_markdown",
    "_render_llm_authored_docs",
]
