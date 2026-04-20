"""Six Questions Protocol - Prompt Templates."""

from __future__ import annotations

EXTRACT_ASSUMPTIONS_PROMPT = """## Task
Analyze the following conclusion and identify ALL assumptions being made.

## Conclusion
{conclusion}

## Context
{context}

Identify all assumptions (including hidden ones) in this conclusion.
For each assumption provide:
- text: What the assumption states
- confidence: How certain we are (0.0-1.0)
- conditions_for_failure: What would make this assumption wrong
- is_hidden: Whether this was explicitly stated or implicit

Respond in structured format."""


DEVILS_ADVOCATE_PROMPT = """## Task
Act as a devil's advocate. Find the strongest counterarguments against this conclusion.

## Conclusion
{conclusion}

## Assumptions Identified
{assumptions}

Find the most compelling counterarguments. Consider:
1. What evidence might contradict this?
2. What exceptions or edge cases exist?
3. What would make this conclusion wrong?

Respond with the strongest counterargument and assess its strength (0.0-1.0)."""


VERIFY_ASSUMPTIONS_PROMPT = """## Task
Verify the following assumptions against available evidence.

## Assumptions to Verify
{assumptions}

## Evidence
{evidence}

For each assumption, determine:
- Is this verified by evidence?
- Is this contradicted by evidence?
- Is this unverified (neither confirmed nor contradicted)?

Return verification status for each assumption."""
