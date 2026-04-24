"""Evaluation Framework - Utilities"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.fs.text_ops import write_text_atomic


def utc_now() -> str:
    """获取 UTC 时间字符串"""
    return datetime.now(timezone.utc).isoformat()


def new_test_run_id() -> str:
    """生成新的测试运行 ID"""
    return str(uuid.uuid4())[:8]


def dedupe(items: list[str]) -> list[str]:
    """去重列表，保持顺序"""
    seen = set()
    result = []
    for item in items:
        key = str(item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(str(item).strip())
    return result


def truncate(text: str, limit: int) -> str:
    """截断文本"""
    if not text or len(text) <= limit:
        return text or ""
    return text[: max(0, limit - 3)] + "..."


def indent(text: str, spaces: int = 2) -> str:
    """缩进文本"""
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.splitlines())


def split_thinking_output(text: str) -> tuple[str, str]:
    """分离 thinking 和 answer"""
    thinking = ""
    answer = text or ""

    # Match <thinking> or <think> tags
    for tag in ["thinking", "think", "reasoning"]:
        pattern = rf"<{tag}[^>]*>(.*?)</{tag}>", re.DOTALL | re.IGNORECASE
        match = re.search(pattern[0], text or "", pattern[1])
        if match:
            thinking = match.group(1).strip()
            answer = re.sub(pattern[0], "", text or "", flags=pattern[1]).strip()
            break

    return thinking, answer


def looks_like_deflection(text: str) -> bool:
    """检测是否是推脱回答"""
    deflection_patterns = [
        "i cannot",
        "i can't",
        "i'm not able",
        "i am not able",
        "unable to",
        "cannot fulfill",
        "inappropriate",
        "against my",
        "as an ai",
        "as a language model",
    ]
    text_lower = str(text or "").lower()
    return any(p in text_lower for p in deflection_patterns)


def looks_like_structured_steps(text: str) -> bool:
    """检测是否包含结构化步骤"""
    candidate = str(text or "")
    if not candidate.strip():
        return False

    line_indicators = (
        r"^\s*\d+\.\s+",
        r"^\s*[-*]\s+",
    )
    for line in candidate.splitlines():
        for pattern in line_indicators:
            if re.search(pattern, line, re.IGNORECASE):
                return True

    paragraph_indicators = (
        r"\bstep\s+\d+\b",
        r"\bfirst\s*,",
        r"\bsecond\s*,",
        r"\bfinally\s*,",
    )
    return any(re.search(pattern, candidate, re.IGNORECASE) for pattern in paragraph_indicators)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def get_embedding_vector(text: str, model: str = "nomic-embed-text") -> list[float] | None:
    """Obtain a real embedding vector via the KernelOne embedding port.

    Delegates to the injected ``KernelEmbeddingPort`` so that HTTP transport
    decisions belong to the infrastructure layer, not to this Cell.

    Returns ``None`` if the port raises (e.g., service unavailable), so
    callers can handle the degraded path explicitly.  If the port is not
    injected at all (bootstrap not wired), a ``RuntimeError`` is raised with
    a clear message pointing to the injection call.

    ✅ MIGRATION COMPLETED (2026-04-09): The previous implementation called Ollama's
    ``/api/embeddings`` endpoint directly via ``urllib.request``.  That
    violated the dependency inversion principle — HTTP transport belongs to
    the infrastructure adapter, not this Cell.  This implementation uses
    ``KernelEmbeddingPort`` (polaris.kernelone.llm.embedding) instead.
    In test environments, inject a fake port via
    ``polaris.kernelone.llm.embedding.set_default_embedding_port()``.
    """
    import logging

    from polaris.kernelone.llm.embedding import get_default_embedding_port

    try:
        port = get_default_embedding_port()
    except RuntimeError:
        raise RuntimeError(
            "get_embedding_vector: KernelEmbeddingPort is not set. "
            "Ensure the bootstrap layer calls "
            "polaris.kernelone.llm.embedding.set_default_embedding_port() "
            "before invoking this function."
        )

    try:
        vector = port.get_embedding(
            str(text or ""),
            model=str(model or "nomic-embed-text") or None,
        )
    except (RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).warning("get_embedding_vector: embedding port raised: %s", exc)
        return None

    if isinstance(vector, list) and vector:
        return [float(v) for v in vector]
    return None


def semantic_criteria_hits(answer: str, criteria: list[str]) -> dict[str, float]:
    """计算语义匹配分数"""
    if not criteria:
        return {}

    answer_vec = get_embedding_vector(answer)
    if answer_vec is None:
        return dict.fromkeys(criteria, 0.0)

    results = {}
    for criterion in criteria:
        crit_vec = get_embedding_vector(criterion)
        if crit_vec:
            results[criterion] = cosine_similarity(answer_vec, crit_vec)
        else:
            results[criterion] = 0.0

    return results


def write_json_atomic(path: str, data: Any) -> None:
    """原子写入 JSON 文件（委托给 KernelOne 原子写入）

    write_text_atomic always writes UTF-8 internally; no encoding kwarg needed.
    """
    content = json.dumps(data, ensure_ascii=False, indent=2)
    write_text_atomic(path, content)


__all__ = [
    "cosine_similarity",
    "dedupe",
    "get_embedding_vector",
    "indent",
    "looks_like_deflection",
    "looks_like_structured_steps",
    "new_test_run_id",
    "semantic_criteria_hits",
    "split_thinking_output",
    "truncate",
    "utc_now",
    "write_json_atomic",
]
