"""Validated contracts for per-node distiller summaries.

These validators are host boundaries. They never execute a model or grant any
engineering effect; a summary only reaches the request through the host's fold
projection.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


def _tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4) if value else 0


class CandidateRefs:
    """Authorized references available to one bounded distiller input package."""

    def __init__(self, *, node_ids: set[str], slice_ids: set[str], evidence_refs: set[str]) -> None:
        self.node_ids = node_ids
        self.slice_ids = slice_ids
        self.evidence_refs = evidence_refs


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: object, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be nonempty text of at most {maximum} characters")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _refs(value: object, label: str, allowed: set[str], *, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} references")
    result: list[str] = []
    for item in value:
        reference = _text(item, label, maximum=160)
        if reference not in allowed:
            raise ValueError(f"{label} contains an unauthorized reference: {reference}")
        if reference in result:
            raise ValueError(f"{label} contains duplicate references")
        result.append(reference)
    return result


_STOCK_SUMMARY_MARKERS = frozenset(
    {
        "completed",
        "done",
        "ok",
        "success",
        "failed",
        "command failed",
        "passed",
        "error",
        "unknown",
        "skipped",
        "skip",
    }
)
_MIN_INFORMATIVE_SUMMARY_CHARS = 4


def summary_is_informative(text: object, original: object = "") -> bool:
    """Require a summary that explains the payload, never a status word or excerpt.

    Folding replaces the payload with this text in the next request, so a bare
    status (``completed``) or a verbatim fragment of the input destroys evidence
    without buying understanding. This is the single rule shared by the distiller
    boundary and the fold gate: the fold gate is what actually substitutes the
    text, so a second, looser copy would admit exactly what the boundary rejects.
    """

    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_INFORMATIVE_SUMMARY_CHARS:
        return False
    lowered = stripped.lower().rstrip(".")
    if lowered in _STOCK_SUMMARY_MARKERS:
        return False
    # "completed successfully" is still a status, not an explanation.
    if len(stripped.split()) <= 3 and any(lowered.startswith(marker) for marker in _STOCK_SUMMARY_MARKERS):
        return False
    if isinstance(original, str) and original.strip():
        normalized = re.sub(r"\s+", " ", stripped).lower()
        source = re.sub(r"\s+", " ", original).lower()
        if normalized in source:
            return False
    return True


# Node keys are plane-qualified. A read preview and a write body of the same path
# are different payloads: sharing one key lets whichever the distiller happened to
# summarise last silently stand in for the other at fold time. A command card is
# already keyed by its own content_ref, which is unique by construction.
def file_node_id(path: str) -> str:
    return f"file:{path}"


def write_node_id(path: str) -> str:
    return f"write:{path}"


def write_snapshot_id(content: str) -> str:
    return "writecall:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def summary_input_key(node: str, snapshot: str, content: str, ranges: object) -> str:
    """Bind a derived summary to the exact slice supplied to the distiller."""
    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return json.dumps([node, snapshot, source_hash, ranges], separators=(",", ":"), ensure_ascii=False)


def file_summary_source(card: Mapping[str, Any], preview: str) -> tuple[str, list[list[int]]] | None:
    """Locate the visible contiguous FileCard slice in its original snapshot."""

    coverage = card.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    bounds = card.get("content_bounds")
    bounds = bounds if isinstance(bounds, Mapping) else {}
    ranges = coverage.get("observed_line_ranges")
    if ranges is None:
        start, end = bounds.get("start_line"), bounds.get("end_line")
    elif isinstance(ranges, list) and len(ranges) == 1 and isinstance(ranges[0], list) and len(ranges[0]) == 2:
        start, end = ranges[0]
        if bounds.get("start_line", start) != start:
            return None
    else:
        return None

    if start is None:
        # A legacy preview without a slice origin can locate itself only if it
        # matches the original full source, including all bytes and newlines.
        if card.get("sha256") != hashlib.sha256(preview.encode("utf-8")).hexdigest():
            return None
        start = 1
    elif coverage.get("presentation_truncated", bounds.get("truncated", True)) is not False:
        # bounded_output appends this metadata after the final visible byte.
        preview = re.sub(r"\n\[output truncated: [1-9]\d* bytes omitted\]\Z", "", preview)

    if type(start) is not int or start < 1 or not preview:
        return None
    visible_end = start + len(preview.splitlines()) - 1
    if end is not None and (type(end) is not int or visible_end > end):
        return None
    return preview, [[start, visible_end]]


def _summary_source_anchor(summary: str, content: str) -> str:
    """Keep a concrete, source-local basis for the model's explanation."""

    quoted: list[str] = re.findall(r"`([^`\n]+)`", summary)
    if quoted:
        if any(
            len(part) > 160
            or not re.search(
                (r"(?<![A-Za-z0-9_$])" if re.match(r"[A-Za-z0-9_$]", part) else "")
                + re.escape(part)
                + (r"(?![A-Za-z0-9_$])" if re.search(r"[A-Za-z0-9_$]$", part) else ""),
                content,
            )
            for part in quoted
        ):
            raise ValueError("every quoted source excerpt must occur exactly in the supplied content")
        explanation = re.sub(r"`[^`\n]+`", "", summary).strip()
        if not summary_is_informative(explanation, content):
            raise ValueError("explain what the quoted source shows in addition to quoting it")
        anchor = max(quoted, key=len)
        if len(anchor.strip()) >= 3:
            return anchor

    # Earlier providers may name the observed symbol directly in prose.
    # Accept an exact identifier used as code or a literal value; sharing
    # ordinary prose words (e.g. 'module') cannot establish a source link.
    names: list[str] = re.findall(r"[A-Za-z_$][A-Za-z0-9_$.:-]{2,}", summary)
    for token in sorted(set(names), key=lambda s: (-len(s), s)):
        token = token.rstrip(".:")
        if len(token) < 3:
            continue
        pattern = rf"(?<![A-Za-z0-9_$]){re.escape(token)}(?![A-Za-z0-9_$])"
        if not re.search(pattern, content):
            continue
        if re.search(r"[a-z][A-Z]|[_.:$]|^[A-Z]{2}", token) or re.search(
            rf"(?:{pattern}\s*[(=:]|[\"']{re.escape(token)}[\"'])", content
        ):
            return token
    raise ValueError("explain one concrete source expression or diagnostic and quote it exactly in backticks")


def _plain_export_list_supported(prose: str, content: str) -> bool:
    """Allow older unquoted lists when each named subject has a directly visible declaration."""

    claim = re.search(r"(?:\bexports?\s+|导出\s*)([^,，;；(.（。!?]+)", prose, re.I)
    if claim is None:
        return False
    for subject in re.split(r"\s+and\s+|以及|与|、", claim.group(1), flags=re.I):
        names: list[str] = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", subject)
        if not any(
            re.search(
                rf"(?m)^[ \t]*(?:export|pub|public)[ \t]+(?:[\w$]+[ \t]+){{0,4}}{re.escape(name)}"
                rf"(?=[ \t]*(?:[({{<:;=]|$))",
                content,
            )
            for name in names
        ):
            return False
    return True


def _check_summary_visibility(summary: str, content: str, *, uncertain: bool, selected: str = "") -> None:
    """Require an observable basis for an explicit interface claim in the summary."""

    for sentence in re.split(r"(?<=[.!?])\s+|[。！？]", summary):
        quoted: list[str] = re.findall(r"`([^`\n]+)`", sentence)
        prose = re.sub(r"`[^`\n]+`", "", sentence)
        if not re.search(r"\b(?:exports?|exported|public (?:API|interface))\b|导出|公开接口", prose, re.I):
            continue
        if uncertain and re.search(
            r"\b(?:unknown|unverified|unconfirmed)\b|无法确认|无法判断|未证实|未知", prose, re.I
        ):
            continue
        # Directly quoted declarations accommodate named exports, re-exports
        # and aliases. A plain local binding does not establish visibility.
        witness = [
            part
            for part in [*quoted, selected]
            if re.match(r"\s*(?:export\b|public\b|pub\b|\(?provide\b|__all__\s*=|(?:module\.)?exports[.\[ =])", part)
        ]
        if not selected and not quoted and _plain_export_list_supported(prose, content):
            continue
        if not witness or any(not any(part in item for item in witness) for part in quoted):
            raise ValueError(
                "quote the source declaration or export list establishing visibility, or state it is unknown"
            )
        for name in re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", prose, re.ASCII):
            if re.search(r"[a-z][A-Z]|_", name) and name in content and not any(name in item for item in witness):
                raise ValueError("the source declaration must include the named public interface")


def _is_documentation_line(node: str, content: str, line_number: int) -> bool:
    """Recognize whole-line comment tokens in source, excluding directives and mixed code."""

    from pygments.lexers import get_lexer_for_filename
    from pygments.token import Comment
    from pygments.util import ClassNotFound

    try:
        lexer = get_lexer_for_filename(node.partition(":")[2])
    except ClassNotFound:
        return False
    lines = content.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: line_number - 1])
    end = start + len(lines[line_number - 1].rstrip("\r\n"))
    found = False
    for offset, kind, value in lexer.get_tokens_unprocessed(content):
        if offset >= end:
            break
        if offset + len(value) <= start or not value[max(0, start - offset) : end - offset].strip():
            continue
        if kind not in Comment or kind in Comment.Preproc or kind in Comment.PreprocFile or kind in Comment.Hashbang:
            return False
        found = True
    return found


def validate_node_summary(
    payload: object, request: Mapping[str, Any], *, selected_line: int | None = None
) -> dict[str, Any]:
    """Validate one NodeSummary response against its exact snapshot and coverage."""

    root = _object(payload, "node summary")
    required = {"node_id", "snapshot_id", "summary", "summary_kind", "coverage", "evidence_refs", "uncertain", "reason"}
    if set(root) != required:
        raise ValueError("node summary has missing or additional fields")
    for key in ("node_id", "snapshot_id"):
        if root[key] != request.get(key):
            raise ValueError(f"node summary {key} does not match the requested snapshot")
    summary = _text(root["summary"], "summary", maximum=1024)
    if not summary_is_informative(summary, request.get("content")):
        raise ValueError("summary must describe the payload, not a status word or a verbatim excerpt")
    content = request.get("content")
    anchor = None
    uncertain = _boolean(root["uncertain"], "uncertain")
    reason = _text(root["reason"], "reason", maximum=160)
    if request.get("node_kind") in ("file_card", "write_call", "command_card"):
        if not isinstance(content, str) or not content.strip():
            raise ValueError("distiller source content is missing")
        if selected_line is None:
            anchor = _summary_source_anchor(summary, content)
        else:
            lines = content.splitlines()
            if type(selected_line) is not int or not 1 <= selected_line <= len(lines):
                raise ValueError("source_line must be an integer index from the supplied source_lines")
            line = lines[selected_line - 1].strip()
            if len(line) < 3 or not any(char.isalnum() for char in line):
                raise ValueError("select a source_line containing a meaningful expression or diagnostic")
            # The model selects a line; the host copies the actual evidence.
            anchor = line[:120]
            for name in re.findall(
                r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|\b[A-Za-z][a-z]+[A-Z][A-Za-z0-9]*", summary, re.ASCII
            ):
                if name not in content and name not in str(request.get("node_id") or ""):
                    raise ValueError("a named symbol in the explanation is absent from the supplied source")
            for span in re.findall(r"`([^`\n]+)`", summary):
                if span not in content:
                    raise ValueError("a quoted literal is absent from the supplied source; use plain language")
        source_line = selected_line
        if source_line is None and anchor:
            matching = [index for index, line in enumerate(content.splitlines(), 1) if anchor in line]
            source_line = matching[0] if len(matching) == 1 else None
        if (
            source_line is not None
            and request.get("node_kind") in ("file_card", "write_call")
            and _is_documentation_line(str(request["node_id"]), content, source_line)
        ):
            # A write-call fold carries the summary text even when other provenance
            # fields aren't projected, so keep this qualification in the text itself.
            summary = f"Documented intent (implementation unverified) — {summary}"
            uncertain = True
            reason = "The source anchor is a comment. Executable behavior is unverified."
        _check_summary_visibility(
            summary,
            content,
            uncertain=uncertain,
            selected=anchor if selected_line is not None else "",
        )
        if selected_line is not None:
            summary = f"{summary} [source {selected_line}: {anchor}]"
    max_tokens = request.get("max_summary_tokens", 192)
    if type(max_tokens) is not int or max_tokens < 1 or _tokens(summary) > max_tokens:
        raise ValueError("summary exceeds max_summary_tokens")
    if root["summary_kind"] != "model_inference":
        raise ValueError("summary_kind must be model_inference")
    coverage = _object(root["coverage"], "coverage")
    if set(coverage) != {"observed_line_ranges", "summary_complete"}:
        raise ValueError("coverage has missing or additional fields")
    if coverage["observed_line_ranges"] != request.get("coverage", {}).get("observed_line_ranges"):
        raise ValueError("summary coverage differs from the requested source coverage")
    _boolean(coverage["summary_complete"], "summary_complete")
    allowed = set(request.get("allowed_evidence_refs") or []) | {str(request["snapshot_id"])}
    return {
        "schema_version": "node-summary.v1",
        **({"source_anchor": anchor} if anchor is not None else {}),
        **({"source_line": selected_line} if selected_line is not None else {}),
        "node_id": root["node_id"],
        "snapshot_id": root["snapshot_id"],
        "summary": summary,
        "summary_kind": "model_inference",
        "coverage": {
            "observed_line_ranges": coverage["observed_line_ranges"],
            "summary_complete": coverage["summary_complete"],
        },
        "evidence_refs": _refs(root["evidence_refs"], "summary.evidence_refs", allowed, maximum=12),
        "uncertain": uncertain,
        "reason": reason,
    }
