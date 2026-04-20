"""Semantic Document Chunking.

NLP-aware document splitting that respects semantic boundaries:
- Paragraph and section boundaries
- Code structure (class/function definitions)
- Markdown heading hierarchies
- High-signal term preservation

Replaces the fixed 80-line chunking in lancedb_code_search.py with
semantic boundary detection inspired by compaction.py's signal scoring.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
    SemanticChunk,
    SemanticChunkerPort,
)

# Reuse signal terms from compaction.py for consistency
_HIGH_SIGNAL_TERMS = (
    "error",
    "traceback",
    "exception",
    "failed",
    "failure",
    "fix",
    "bug",
    "refactor",
    "error",
    "exception",
    "failed",
    "failure",
    "fix",
    "bug",
    "class",
    "def",
    "function",
    "method",
    "interface",
    "abstract",
    "impl",
    "module",
    "import",
    "export",
    "async",
    "await",
    "error",
    "warning",
    "critical",
    "fatal",
    "assert",
    "config",
    "setting",
    "parameter",
    "argument",
    "return",
    "test",
    "spec",
    "benchmark",
    "benchmark",
)
_LOW_SIGNAL_PATTERNS = (
    r"^(hi|hello|hey|thanks|thank you|ok|好的|收到|bye|再见)\b",
    r"^(#+\s*)?comment[ers]?",
    r"^\s*#\s*(---|===|___)",  # Markdown dividers
)

# Code structure patterns
_CODE_STRUCTURE_PATTERNS = {
    "python": [
        r"^class\s+\w+",  # class definition
        r"^def\s+\w+\s*\(",  # function definition
        r"^async\s+def\s+\w+",  # async function
        r"^import\s+",  # import statement
        r"^from\s+\w+\s+import",  # from import
        r"^if\s+__name__\s*==",  # main guard
    ],
    "javascript": [
        r"^function\s+\w+",  # function declaration
        r"^const\s+\w+\s*=",  # const declaration
        r"^let\s+\w+\s*=",  # let declaration
        r"^class\s+\w+",  # class declaration
        r"^export\s+",  # export
        r"^import\s+",  # import
        r"=>\s*{",  # arrow function
    ],
    "typescript": [
        r"^function\s+\w+",
        r"^const\s+\w+\s*:",
        r"^let\s+\w+\s*:",
        r"^class\s+\w+",
        r"^interface\s+\w+",
        r"^type\s+\w+\s*=",
        r"^export\s+",
        r"^import\s+",
    ],
    "markdown": [
        r"^#\s+\S",  # H1 heading
        r"^##\s+\S",  # H2 heading
        r"^###\s+\S",  # H3 heading
        r"^\*\*.*\*\*$",  # bold line
        r"^\`\`\`",  # code fence
    ],
    "java": [
        r"^public\s+class\s+\w+",
        r"^private\s+class\s+\w+",
        r"^protected\s+class\s+\w+",
        r"^class\s+\w+",
        r"^public\s+\w+\s+\w+\s*\(",
        r"^private\s+\w+\s+\w+\s*\(",
        r"^protected\s+\w+\s+\w+\s*\(",
        r"^\s*void\s+\w+\s*\(",
        r"^\s*\w+\s+get\w+\s*\(",
        r"^\s*set\w+\s*\(",
        r"^import\s+",
        r"^package\s+",
    ],
    "c": [
        r"^#include\s*<",
        r"^#include\s*\"",
        r"^typedef\s+struct",
        r"^struct\s+\w+",
        r"^enum\s+\w+",
        r"^#define\s+",
        r"^static\s+\w+\s+\w+\s*\(",
        r"^\w+\s+\w+\s*\([^)]*\)\s*{",
    ],
    "cpp": [
        r"^#include\s*<",
        r"^#include\s*\"",
        r"^namespace\s+\w+",
        r"^class\s+\w+",
        r"^public:",
        r"^private:",
        r"^protected:",
        r"^template\s*<",
        r"^virtual\s+\w+",
        r"^std::",
        r"^using\s+namespace",
        r"^#define\s+",
    ],
    "csharp": [
        r"^using\s+System",
        r"^namespace\s+\w+",
        r"^public\s+class\s+\w+",
        r"^private\s+class\s+\w+",
        r"^protected\s+class\s+\w+",
        r"^internal\s+class\s+\w+",
        r"^public\s+async\s+Task",
        r"^public\s+\w+\s+\w+\s*\(",
        r"^private\s+\w+\s+\w+\s*\(",
    ],
    "go": [
        r"^package\s+\w+",
        r"^func\s+\w+\s*\(",
        r"^func\s+\(\w+\s+\*?\w+\)\s+\w+\s*\(",
        r"^type\s+\w+\s+struct",
        r"^type\s+\w+\s+interface",
        r"^import\s+\(",
        r'^import\s+"',
        r"^const\s+\w+",
        r"^var\s+\w+",
    ],
    "rust": [
        r"^fn\s+\w+\s*\(",
        r"^async\s+fn\s+\w+",
        r"^pub\s+fn\s+\w+",
        r"^impl\s+\w+",
        r"^impl\s+Trait\s+\w+",
        r"^struct\s+\w+",
        r"^enum\s+\w+",
        r"^use\s+\w+",
        r"^mod\s+\w+",
        r"^pub\s+struct\s+\w+",
        r"^pub\s+enum\s+\w+",
    ],
    "php": [
        r"^<?php",
        r"^namespace\s+\w+",
        r"^use\s+\w+",
        r"^class\s+\w+",
        r"^public\s+function\s+\w+",
        r"^private\s+function\s+\w+",
        r"^protected\s+function\s+\w+",
        r"^function\s+\w+\s*\(",
    ],
    "ruby": [
        r"^class\s+\w+",
        r"^module\s+\w+",
        r"^def\s+\w+",
        r"^def\s+self\.\w+",
        r"^attr_accessor\s+",
        r"^attr_reader\s+",
        r"^require\s+['\"]",
        r"^require_relative\s+",
    ],
    "sql": [
        r"^SELECT\s+",
        r"^FROM\s+",
        r"^WHERE\s+",
        r"^CREATE\s+TABLE",
        r"^CREATE\s+INDEX",
        r"^ALTER\s+TABLE",
        r"^DROP\s+TABLE",
        r"^INSERT\s+INTO",
        r"^UPDATE\s+\w+\s+SET",
        r"^DELETE\s+FROM",
        r"^JOIN\s+",
        r"^GROUP\s+BY",
        r"^ORDER\s+BY",
    ],
    "shell": [
        r"^#!/bin/bash",
        r"^#!/bin/sh",
        r"^#!/usr/bin/env\s+bash",
        r"^function\s+\w+",
        r"^\w+\s*\(\)\s*{",
        r"^export\s+\w+=",
        r"^if\s+\[\[",
        r"^if\s+\[",
        r"^case\s+\w+\s+in",
    ],
}

# Paragraph boundary: double newline or significant line break
_PARAGRAPH_BOUNDARY_RE = re.compile(r"\n\n+|\n{2,}")
# Code block fence
_CODE_FENCE_RE = re.compile(r"^```|```$", re.MULTILINE)
# Whitespace-only line
_BLANK_LINE_RE = re.compile(r"^\s*$")
# CJK character range (Chinese, Japanese Kanji, Korean Hangul)
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")
# CJK sentence-ending punctuation
_CJK_SENTENCE_END_RE = re.compile(r"[。！？；]+")
# CJK word separator (full-width punctuation that separates words)
_CJK_WORD_SEP_RE = re.compile(r"[,，、]+")


# ---------------------------------------------------------------------------
# CJK text segmentation helpers
# ---------------------------------------------------------------------------

try:
    import jieba

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


def _cjk_segment(text: str) -> list[str]:
    """Segment CJK text into word-like units.

    Uses jieba if available for Chinese segmentation; falls back to
    character-level segmentation for environments without jieba.

    Returns a list of word tokens (may include punctuation as separate tokens).
    """
    if not JIEBA_AVAILABLE or not _CJK_RE.search(text):
        # Character-level fallback: split on CJK word separators
        # but keep punctuation as separate tokens
        tokens: list[str] = []
        current: list[str] = []
        for char in text:
            if _CJK_RE.match(char):
                current.append(char)
            elif _CJK_WORD_SEP_RE.match(char):
                if current:
                    tokens.append("".join(current))
                    current = []
                tokens.append(char)
            else:
                if current:
                    tokens.append("".join(current))
                    current = []
                tokens.append(char)
        if current:
            tokens.append("".join(current))
        return [t for t in tokens if t.strip()]

    # jieba segmentation
    return list(jieba.cut(text, cut_all=False))


def _has_cjk(text: str) -> bool:
    """Return True if text contains any CJK characters."""
    return bool(_CJK_RE.search(text))


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ChunkBoundary:
    """A detected semantic boundary within text."""

    line_index: int  # 0-indexed line where boundary occurs
    score: float  # Boundary confidence 0.0-1.0
    reason: str  # Description of why this is a boundary


class SemanticChunker:
    """NLP-aware semantic chunker.

    Splits documents at semantic boundaries instead of fixed line counts.
    Uses signal scoring from compaction.py to weight chunk importance.

    Example::

        chunker = SemanticChunker()
        chunks = chunker.chunk(code_text, source_hint="python")
        for chunk in chunks:
            print(f"{chunk.chunk_id}: lines {chunk.line_start}-{chunk.line_end}")
    """

    # Target chunk size in characters (approximate, varies by boundary detection)
    CHUNK_TARGET_CHARS: int = 2000
    CHUNK_MIN_CHARS: int = 256
    # Minimum boundary score to trigger a chunk split
    BOUNDARY_THRESHOLD: float = 0.5

    def __init__(
        self,
        *,
        chunk_target_chars: int = 2000,
        chunk_min_chars: int = 256,
        boundary_threshold: float = 0.5,
    ) -> None:
        self._target_chars = chunk_target_chars
        self._min_chars = chunk_min_chars
        self._threshold = boundary_threshold

    def chunk(self, text: str, *, source_hint: str = "auto") -> list[SemanticChunk]:
        """Split text into semantically coherent chunks.

        Args:
            text: Raw document text
            source_hint: Source language hint ("python", "markdown", "javascript", "auto")

        Returns:
            List of SemanticChunk in reading order
        """
        if not text or not text.strip():
            return []

        lines = text.splitlines()
        if len(lines) <= 2:
            # Single chunk for very short text
            return [self._make_chunk(text, 1, len(lines), source_hint)]

        # Detect semantic boundaries
        boundaries = self._detect_boundaries(lines, source_hint)

        # Build chunks from boundaries
        chunks = self._build_chunks(lines, boundaries, source_hint)

        return chunks

    def _detect_boundaries(self, lines: list[str], source_hint: str) -> list[_ChunkBoundary]:
        """Detect semantic boundaries in the text.

        Uses multiple signals:
        1. Paragraph boundaries (\n\n)
        2. Code structure (class/def/function keywords)
        3. Markdown headings
        4. High-signal term concentration shifts
        """
        boundaries: list[_ChunkBoundary] = []
        structure_patterns = self._get_structure_patterns(source_hint)

        prev_line = ""
        in_code_block = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            score = 0.0
            reasons: list[str] = []

            # Track code block state
            if _CODE_FENCE_RE.match(stripped):
                in_code_block = not in_code_block

            # Skip blank lines for scoring (but they can be boundaries)
            if not stripped:
                if prev_line.strip():
                    # Blank line after content = potential paragraph boundary
                    score = 0.3
                    reasons.append("paragraph_break")
                prev_line = line
                continue

            # Check for paragraph boundary (double newline implied by blank line transition)
            if not prev_line.strip() and stripped:
                # Content following blank line = new paragraph
                score = max(score, 0.4)
                reasons.append("new_paragraph")

            # Check code structure patterns
            if structure_patterns:
                for pattern in structure_patterns:
                    if re.match(pattern, stripped):
                        score = max(score, 0.8)
                        reasons.append("code_structure")
                        break

            # Check markdown headings
            if source_hint in ("markdown", "auto") and re.match(r"^#{1,6}\s+\S", stripped):
                score = max(score, 0.85)
                reasons.append("markdown_heading")

            # High-signal term boost
            lowered = stripped.lower()
            signal_hits = sum(1 for term in _HIGH_SIGNAL_TERMS if term in lowered)
            if signal_hits >= 3:
                score = min(1.0, score + 0.2 * signal_hits)
                reasons.append(f"high_signal({signal_hits})")

            # CJK sentence boundary: treat CJK sentence-ending punctuation as strong boundary
            if _has_cjk(stripped):
                # Check for sentence-ending punctuation
                cjk_ends = _CJK_SENTENCE_END_RE.findall(stripped)
                if cjk_ends:
                    # Find position of first sentence-ending punctuation
                    match = _CJK_SENTENCE_END_RE.search(stripped)
                    if match:
                        # Only score as boundary if the sentence-ending mark is near end of line
                        # (not mid-sentence like "他说：" )
                        suffix = stripped[match.end() :].strip()
                        if not suffix or len(suffix) <= 2:
                            score = max(score, 0.8)
                            reasons.append("cjk_sentence_end")

            # Low-signal penalty
            for pattern in _LOW_SIGNAL_PATTERNS:
                if re.search(pattern, stripped, re.IGNORECASE):
                    score = max(0.0, score - 0.2)
                    break

            # Accumulate boundary if score exceeds threshold
            if score >= self._threshold:
                boundaries.append(
                    _ChunkBoundary(
                        line_index=i,
                        score=score,
                        reason="+".join(reasons) if reasons else "threshold",
                    )
                )

            prev_line = line

        return boundaries

    def _get_structure_patterns(self, source_hint: str) -> list[str]:
        """Get code structure patterns for the given source hint."""
        if source_hint == "auto":
            return []
        return _CODE_STRUCTURE_PATTERNS.get(source_hint, [])

    def _build_chunks(
        self,
        lines: list[str],
        boundaries: list[_ChunkBoundary],
        source_hint: str,
    ) -> list[SemanticChunk]:
        """Build chunks from detected boundaries, respecting target size."""
        if not boundaries:
            # No boundaries detected, return single chunk
            return [self._make_chunk("\n".join(lines), 1, len(lines), source_hint)]

        chunks: list[SemanticChunk] = []
        chunk_start = 0
        chunk_lines: list[str] = []

        for boundary in boundaries:
            # Lines from current chunk_start to boundary.line_index
            boundary_line_idx = boundary.line_index

            # Check if adding up to this boundary would exceed target size
            potential_end = boundary_line_idx
            potential_lines = lines[chunk_start:potential_end]
            potential_text = "\n".join(potential_lines)
            potential_len = len(potential_text)

            if potential_len >= self._target_chars and chunk_lines:
                # Emit current chunk
                chunk_text = "\n".join(chunk_lines)
                chunks.append(
                    self._make_chunk(
                        chunk_text,
                        chunk_start + 1,  # 1-indexed
                        chunk_start + len(chunk_lines),
                        source_hint,
                    )
                )
                chunk_start = boundary_line_idx
                chunk_lines = [lines[b_idx] for b_idx in range(chunk_start, min(boundary_line_idx + 1, len(lines)))]
            else:
                # Accumulate lines up to boundary
                for b_idx in range(chunk_start, min(boundary_line_idx + 1, len(lines))):
                    chunk_lines.append(lines[b_idx])

        # Don't forget the last chunk
        if chunk_lines or chunk_start < len(lines):
            remaining_lines = lines[chunk_start:]
            if remaining_lines:
                if not chunk_lines:
                    chunk_lines = remaining_lines
                chunk_text = "\n".join(chunk_lines)
                chunks.append(
                    self._make_chunk(
                        chunk_text,
                        chunk_start + 1,  # 1-indexed
                        len(lines),
                        source_hint,
                    )
                )

        # Post-process: merge very small chunks with previous
        chunks = self._merge_small_chunks(chunks)

        return chunks

    def _merge_small_chunks(self, chunks: list[SemanticChunk]) -> list[SemanticChunk]:
        """Merge very small chunks with their neighbors to avoid micro-chunks.

        Core rule: A chunk < min_chars should NOT exist alone. It must be merged
        with neighbors. Chunks >= min_chars are preserved as-is UNLESS they can
        merge with adjacent small chunks to form a better semantic unit.

        Algorithm:
        - Accumulate small chunks (len <= min_chars) into pending
        - When pending would exceed min_chars by a comfortable margin, emit it
        - When a large chunk (len > min_chars) arrives, flush any pending,
          but if pending is too small to emit alone, MERGE it with current
        """
        if len(chunks) <= 1:
            return chunks

        merged: list[SemanticChunk] = []
        pending_text: str | None = None
        pending_start: int = 0
        pending_end: int = 0
        pending_source: str = ""

        for chunk in chunks:
            if len(chunk.text) <= self._min_chars:
                # Small chunk: add to pending
                if pending_text is None:
                    pending_text = chunk.text
                    pending_start = chunk.line_start
                    pending_end = chunk.line_end
                    pending_source = chunk.source_hint
                else:
                    merged_text = pending_text + "\n" + chunk.text
                    pending_text = merged_text
                    pending_end = chunk.line_end
            # Large chunk (len > min_chars): flush pending, then emit large chunk
            elif pending_text is not None:
                # Check if pending is too small to emit alone
                if len(pending_text) <= self._min_chars:
                    # Merge pending with current large chunk
                    merged_text = pending_text + "\n" + chunk.text
                    merged.append(
                        self._make_chunk(
                            merged_text,
                            pending_start,
                            chunk.line_end,
                            chunk.source_hint,
                        )
                    )
                else:
                    # Pending is large enough, emit it separately
                    merged.append(
                        self._make_chunk(
                            pending_text,
                            pending_start,
                            pending_end,
                            pending_source,
                        )
                    )
                    merged.append(chunk)
                pending_text = None
            else:
                merged.append(chunk)

        # Handle remaining pending
        if pending_text is not None:
            merged.append(
                self._make_chunk(
                    pending_text,
                    pending_start,
                    pending_end,
                    pending_source,
                )
            )

        return merged

    def _make_chunk(
        self,
        text: str,
        line_start: int,
        line_end: int,
        source_hint: str,
    ) -> SemanticChunk:
        """Create a SemanticChunk with computed fields."""
        chunk_id = hashlib.sha256(text[:200].encode("utf-8")).hexdigest()[:16]

        # Compute semantic tags
        tags = self._compute_tags(text, source_hint)

        # Compute boundary score (rough heuristic: longer chunks = higher confidence)
        boundary_score = min(1.0, len(text) / self._target_chars)

        return SemanticChunk(
            chunk_id=chunk_id,
            text=text.strip(),
            line_start=line_start,
            line_end=line_end,
            boundary_score=boundary_score,
            semantic_tags=tuple(tags),
            source_hint=source_hint,
        )

    def _compute_tags(self, text: str, source_hint: str) -> list[str]:
        """Compute semantic tags for a chunk based on content analysis."""
        tags: list[str] = []
        lines = text.splitlines()
        first_line = lines[0].strip() if lines else ""

        # Source-agnostic tags
        lowered = text.lower()

        if "error" in lowered or "exception" in lowered or "failed" in lowered:
            tags.append("error_handling")
        if "test" in lowered or "spec" in lowered or "benchmark" in lowered:
            tags.append("test_code")
        if "config" in lowered or "setting" in lowered:
            tags.append("configuration")

        # High signal term count
        signal_hits = sum(1 for term in _HIGH_SIGNAL_TERMS if term in lowered)
        if signal_hits >= 5:
            tags.append("high_density")

        # Source-specific tags
        if source_hint == "python":
            if re.match(r"^class\s+\w+", first_line):
                tags.append("class_definition")
            elif re.match(r"^(async\s+)?def\s+\w+", first_line):
                tags.append("function_definition")
            if "import" in lowered:
                tags.append("import_statement")

        elif source_hint == "markdown":
            if re.match(r"^#\s+\S", first_line):
                tags.append("heading1")
            elif re.match(r"^##\s+\S", first_line):
                tags.append("heading2")
            if "```" in text:
                tags.append("code_block")
            if "|" in text and text.count("|") >= 3:
                tags.append("table")

        elif source_hint in ("javascript", "typescript"):
            if re.match(r"^function\s+\w+", first_line) or "=>" in first_line:
                tags.append("function_definition")
            if re.match(r"^class\s+\w+", first_line):
                tags.append("class_definition")
            if "export" in lowered:
                tags.append("export_statement")

        elif source_hint == "java":
            if re.match(r"^(public|private|protected)?\s*class\s+\w+", first_line):
                tags.append("class_definition")
            if re.match(r"^\s*(public|private|protected)?\s*\w+\s+\w+\s*\(", first_line):
                tags.append("method_definition")
            if re.match(r"^import\s+", first_line):
                tags.append("import_statement")
            if re.match(r"^package\s+", first_line):
                tags.append("package_declaration")

        elif source_hint in ("c", "cpp"):
            if re.match(r"^struct\s+\w+", first_line):
                tags.append("struct_definition")
            if re.match(r"^enum\s+\w+", first_line):
                tags.append("enum_definition")
            if re.match(r"^#include", first_line):
                tags.append("include_statement")
            if re.match(r"^typedef", first_line):
                tags.append("typedef_statement")
            if source_hint == "cpp":
                if re.match(r"^namespace\s+\w+", first_line):
                    tags.append("namespace_declaration")
                if re.match(r"^template\s*<", first_line):
                    tags.append("template_definition")

        elif source_hint == "go":
            if re.match(r"^package\s+\w+", first_line):
                tags.append("package_declaration")
            if re.match(r"^func\s+", first_line):
                tags.append("function_definition")
            if re.match(r"^type\s+\w+\s+struct", first_line):
                tags.append("struct_definition")
            if re.match(r"^type\s+\w+\s+interface", first_line):
                tags.append("interface_definition")
            if re.match(r"^import\s+", first_line):
                tags.append("import_statement")

        elif source_hint == "rust":
            if re.match(r"^fn\s+\w+", first_line):
                tags.append("function_definition")
            if re.match(r"^async\s+fn\s+\w+", first_line):
                tags.append("function_definition")
            if re.match(r"^pub\s+fn\s+\w+", first_line):
                tags.append("function_definition")
            if re.match(r"^struct\s+\w+", first_line):
                tags.append("struct_definition")
            if re.match(r"^enum\s+\w+", first_line):
                tags.append("enum_definition")
            if re.match(r"^impl\s+", first_line):
                tags.append("impl_block")
            if re.match(r"^use\s+\w+", first_line):
                tags.append("use_statement")

        elif source_hint == "php":
            if re.match(r"^namespace\s+\w+", first_line):
                tags.append("namespace_declaration")
            if re.match(r"^class\s+\w+", first_line):
                tags.append("class_definition")
            if re.match(r"^(public|private|protected)?\s*function\s+\w+", first_line):
                tags.append("method_definition")

        elif source_hint == "ruby":
            if re.match(r"^class\s+\w+", first_line):
                tags.append("class_definition")
            if re.match(r"^module\s+\w+", first_line):
                tags.append("module_definition")
            if re.match(r"^def\s+", first_line):
                tags.append("method_definition")
            if re.match(r"^require", first_line):
                tags.append("require_statement")

        elif source_hint == "sql":
            if re.match(r"^SELECT\s+", first_line, re.IGNORECASE):
                tags.append("select_query")
            if re.match(r"^CREATE\s+TABLE", first_line, re.IGNORECASE):
                tags.append("create_table")
            if re.match(r"^INSERT\s+INTO", first_line, re.IGNORECASE):
                tags.append("insert_statement")
            if re.match(r"^UPDATE\s+", first_line, re.IGNORECASE):
                tags.append("update_statement")
            if re.match(r"^DELETE\s+FROM", first_line, re.IGNORECASE):
                tags.append("delete_statement")

        elif source_hint == "shell":
            if re.match(r"^#!/", first_line):
                tags.append("shebang")
            if re.match(r"^function\s+\w+", first_line) or re.match(r"^\w+\s*\(\)", first_line):
                tags.append("function_definition")
            if re.match(r"^export\s+\w+=", first_line):
                tags.append("environment_variable")

        # CJK text detection (language-agnostic)
        if _has_cjk(text):
            tags.append("cjk_text")
            # Estimate which script based on character ranges
            if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
                tags.append("japanese")
            elif re.search(r"[\uac00-\ud7af]", text):
                tags.append("korean")
            elif re.search(r"[\u4e00-\u9fff]", text):
                tags.append("chinese")

        return tags


# Type annotation for protocol
SemanticChunker.__protocol__ = SemanticChunkerPort  # type: ignore[attr-defined]


__all__ = ["SemanticChunker"]
