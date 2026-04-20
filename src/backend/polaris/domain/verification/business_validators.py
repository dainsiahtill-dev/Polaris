"""Business validation rules for domain layer."""

from __future__ import annotations

import json
import re
from typing import Any


def _extract_json_object(text: str) -> dict[str, Any] | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None

    raw_candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if candidate.startswith("{") and candidate.endswith("}"):
        raw_candidates.append(candidate)

    for item in raw_candidates:
        try:
            payload = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def validate_pm_plan_json(text: str) -> tuple[bool, str]:
    """验证 PM 计划 JSON"""
    data = _extract_json_object(text)
    if data is None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"

    if not isinstance(data, dict):
        return False, "Root must be an object"

    required_keys = ["goal", "backlog", "timeline"]
    missing = [k for k in required_keys if k not in data]
    if missing:
        return False, f"Missing keys: {missing}"

    return True, "Valid"


def validate_director_safe_scope(text: str) -> tuple[bool, str]:
    """验证 Director 安全范围"""
    # Try to parse as JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            scope = data.get("scope") or data.get("in_scope") or []
            scope_text = json.dumps(scope).lower() if scope else text.lower()
        else:
            scope_text = text.lower()
    except json.JSONDecodeError:
        # Treat as plain text
        scope_text = text.lower()

    # Check for restricted paths/patterns - actual modification intent
    restricted = ["docs/", "scripts/"]
    for r in restricted:
        # Check for actual modification intent (not "never modify" or "reminder")
        patterns = [
            f"update {r}",
            f"modify {r}",
            f"write to {r}",
            f"change {r}",
            f"will update {r}",
            f"will modify {r}",
            f"plan: modify {r}",
        ]
        for p in patterns:
            if p in scope_text:
                # Make sure it's not in a "never" context
                start = scope_text.find(p)
                context = scope_text[max(0, start - 20) : start + len(p) + 5]
                if "never" not in context and "not" not in context:
                    return False, f"Detected restricted operation on {r}"

    return True, "Safe"


def validate_director_evidence(text: str) -> tuple[bool, str]:
    """验证 Director 证据"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False, "Invalid JSON"

    if not isinstance(data, dict):
        return False, "Root must be an object"

    evidence = data.get("evidence") or data.get("files") or []
    if not evidence:
        return True, "No evidence required"

    return True, "Evidence present"


def validate_no_hallucinated_paths(text: str, known_paths: list[str] | None = None) -> tuple[bool, str]:
    """验证没有幻觉路径"""
    path_pattern = r"[\"']?(?:[A-Za-z]:)?[/\\][^\s\"']+[\"']?"
    found_paths = re.findall(path_pattern, text)

    if not known_paths:
        return True, "No known paths to validate against"

    known_set = set(p.lower() for p in known_paths)
    hallucinated = []

    for path in found_paths:
        clean = path.strip("\"'").lower()
        if clean and clean not in known_set:
            hallucinated.append(path)

    if hallucinated:
        return False, f"Hallucinated paths: {hallucinated[:3]}"

    return True, "No hallucinated paths detected"


def validate_qa_json(text: str) -> tuple[bool, str]:
    """验证 QA JSON"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    if not isinstance(data, dict):
        return False, "Root must be an object"

    if "questions" not in data and "items" not in data:
        return False, "Missing 'questions' or 'items' key"

    return True, "Valid"


def validate_qa_passfail(result: dict[str, Any]) -> tuple[bool, str]:
    """验证 QA 通过/失败"""
    # Use explicit None checks to handle False values correctly
    passed = result.get("passed")
    if passed is None:
        passed = result.get("pass")
    if passed is None:
        passed = result.get("success")
    if passed is None:
        return False, "No pass/fail indicator found"

    return bool(passed), "Pass" if passed else "Fail"


def validate_docs_template(text: str) -> tuple[bool, str]:
    """验证文档模板"""
    required_fields = ["goal", "in_scope", "out_of_scope", "constraints"]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False, "Invalid JSON"

    if not isinstance(data, dict):
        return False, "Root must be an object"

    missing = [f for f in required_fields if f not in data]
    if missing:
        return False, f"Missing fields: {missing}"

    return True, "Valid"


__all__ = [
    "validate_director_evidence",
    "validate_director_safe_scope",
    "validate_docs_template",
    "validate_no_hallucinated_paths",
    "validate_pm_plan_json",
    "validate_qa_json",
    "validate_qa_passfail",
]
