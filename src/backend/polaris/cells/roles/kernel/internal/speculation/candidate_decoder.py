from __future__ import annotations

import json
import re
import time
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.models import (
    CandidateToolCall,
    FieldMutation,
)

# End tags that signal syntactic completeness
_END_TAGS = ("</tool_call>", "\n```")

# Critical fields that affect stability scoring
_CRITICAL_FIELDS = frozenset({"path", "query", "command", "content", "tool_name"})

# Regex to extract tool name from opening tag or JSON-like structure
_TOOL_NAME_RE = re.compile(
    r'"name"\s*:\s*"([^"]+)"|'
    r"<tool_call>\s*\n?\s*([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


class CandidateDecoder:
    """Incrementally parse streaming tool call arguments from LLM token deltas.

    The decoder maintains internal buffers and tracks a ``CandidateToolCall``
    through parse state transitions:
    ``incomplete -> syntactic_complete -> schema_valid -> semantically_stable``.
    """

    def __init__(
        self,
        candidate_id: str,
        stream_id: str,
        turn_id: str,
        *,
        schema: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the decoder.

        Args:
            candidate_id: Unique identifier for this candidate.
            stream_id: Identifier for the originating stream.
            turn_id: Identifier for the current turn.
            schema: Optional JSON Schema for argument validation.
        """
        self._candidate_id = candidate_id
        self._stream_id = stream_id
        self._turn_id = turn_id
        self._schema = schema
        self._buffer: list[str] = []
        self._candidate = CandidateToolCall(
            candidate_id=candidate_id,
            stream_id=stream_id,
            turn_id=turn_id,
            first_seen_at=time.monotonic(),
            updated_at=time.monotonic(),
        )
        self._args_buffer: list[str] = []
        self._in_args_section = False
        self._tool_name_extracted = False

    @property
    def candidate(self) -> CandidateToolCall:
        """Current candidate state (read-only reference; mutate via ``feed_delta``)."""
        return self._candidate

    def feed_delta(self, delta: str) -> CandidateToolCall | None:
        """Consume a token delta and update the candidate.

        Args:
            delta: A chunk of text from the LLM stream.

        Returns:
            The updated ``CandidateToolCall`` if a meaningful change occurred,
            otherwise ``None``.
        """
        if not delta:
            return None

        changed = False
        self._buffer.append(delta)
        combined = "".join(self._buffer)

        # 1. Detect end tags
        if not self._candidate.end_tag_seen:
            for tag in _END_TAGS:
                if tag in combined:
                    self._candidate.end_tag_seen = True
                    changed = True
                    break

        # 2. Extract tool name (once)
        if not self._tool_name_extracted:
            match = _TOOL_NAME_RE.search(combined)
            if match:
                tool_name = match.group(1) or match.group(2)
                if tool_name:
                    self._candidate.tool_name = tool_name
                    self._tool_name_extracted = True
                    changed = True

        # 3. Accumulate JSON arguments
        args_changed = self._update_args_buffer(delta, combined)
        if args_changed:
            changed = True

        # 4. Validate schema if syntactically complete
        if self._candidate.parse_state in {"incomplete", "syntactic_complete"}:
            self._try_schema_validation()

        # 5. Update timestamps
        if changed:
            self._candidate.updated_at = time.monotonic()
            return self._candidate

        return None

    def _update_args_buffer(self, delta: str, combined: str) -> bool:
        """Try to isolate the JSON argument payload from the accumulated text.

        Returns:
            True if the ``partial_args`` dict changed.
        """
        # Heuristic: look for first '{' after tool name or opening tag
        start_idx = combined.find("{")
        if start_idx == -1:
            return False

        # If we see an end tag, truncate to it for JSON extraction
        end_idx = len(combined)
        for tag in _END_TAGS:
            tag_pos = combined.find(tag, start_idx)
            if tag_pos != -1:
                end_idx = min(end_idx, tag_pos)

        json_slice = combined[start_idx:end_idx]

        # Try incremental parse
        try:
            parsed = json.loads(json_slice)
            if isinstance(parsed, dict):
                old_args = dict(self._candidate.partial_args)
                self._candidate.partial_args = parsed
                self._record_mutations(old_args, parsed)
                self._in_args_section = True
                return parsed != old_args
        except json.JSONDecodeError:
            # Try to extract a partial object for progressive enrichment
            partial = self._try_partial_parse(json_slice)
            if partial:
                old_args = dict(self._candidate.partial_args)
                self._candidate.partial_args = partial
                self._record_mutations(old_args, partial)
                self._in_args_section = True
                return partial != old_args

        return False

    def _try_partial_parse(self, text: str) -> dict[str, Any] | None:
        """Attempt to parse an incomplete JSON object into a partial dict.

        This handles cases where the stream cuts off mid-value by padding
        the text and parsing whatever is structurally valid.
        """
        # Strategy: progressively truncate at the last complete key-value pair
        # by adding closing braces and attempting json.loads
        candidates: list[str] = [text]
        # Find the last position where a value seems complete
        # Heuristic: look for commas or closing braces followed by optional whitespace
        for match in re.finditer(r'[}\]"]\s*(,|\s*$)', text):
            truncated = text[: match.end()]
            # Balance braces
            open_braces = truncated.count("{") - truncated.count("}")
            open_brackets = truncated.count("[") - truncated.count("]")
            padded = truncated + ("}" * open_braces) + ("]" * open_brackets)
            candidates.append(padded)

        # Try from longest to shortest
        for candidate in reversed(candidates):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
        return None

    def _record_mutations(
        self,
        old_args: dict[str, Any],
        new_args: dict[str, Any],
    ) -> None:
        """Record field-level mutations between two argument snapshots."""
        now = time.monotonic()
        all_keys = set(old_args.keys()) | set(new_args.keys())
        for key in all_keys:
            old_val = old_args.get(key)
            new_val = new_args.get(key)
            if old_val != new_val:
                mutation = FieldMutation(
                    field_path=key,
                    old_value=old_val,
                    new_value=new_val,
                    ts_monotonic=now,
                )
                self._candidate.mutation_history.append(mutation)
                self._candidate.last_mutation_at = now

    def _try_schema_validation(self) -> None:
        """Attempt JSON schema validation and advance parse state."""
        if not self._candidate.partial_args:
            return

        # Syntactic completeness: valid JSON dict + end tag seen or well-formed
        is_syntactically_complete = bool(self._candidate.end_tag_seen)
        if not is_syntactically_complete:
            # Also consider syntactically complete if we have a fully parseable
            # object with no open braces/brackets pending
            return

        self._candidate.parse_state = "syntactic_complete"

        # Schema validation
        if self._schema is not None:
            try:
                from jsonschema import Draft7Validator
                from jsonschema.exceptions import ValidationError as _JVError

                Draft7Validator(self._schema).validate(self._candidate.partial_args)
                self._candidate.schema_valid = True
                self._candidate.parse_state = "schema_valid"
            except (ValueError, TypeError, KeyError, _JVError):
                self._candidate.schema_valid = False
        else:
            # Without a schema, syntactic completeness implies schema_valid
            # for the purpose of state progression
            self._candidate.schema_valid = True
            self._candidate.parse_state = "schema_valid"

    def finalize(self) -> CandidateToolCall:
        """Force finalization of the candidate and return it.

        This is useful when the stream ends without an explicit end tag.
        """
        combined = "".join(self._buffer)
        if not self._candidate.end_tag_seen:
            for tag in _END_TAGS:
                if tag in combined:
                    self._candidate.end_tag_seen = True
                    break

        self._update_args_buffer("", combined)
        self._try_schema_validation()
        self._candidate.updated_at = time.monotonic()
        return self._candidate
