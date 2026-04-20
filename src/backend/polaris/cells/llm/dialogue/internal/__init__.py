"""Internal module exports for `llm.dialogue`."""

from polaris.cells.llm.dialogue.internal.docs_dialogue import (
    generate_dialogue_turn,
    generate_dialogue_turn_streaming,
)
from polaris.cells.llm.dialogue.internal.docs_suggest import (
    generate_docs_fields,
    generate_docs_fields_stream,
)
from polaris.cells.llm.dialogue.internal.role_dialogue import (
    generate_role_response,
    generate_role_response_streaming,
)

__all__ = [
    "generate_dialogue_turn",
    "generate_dialogue_turn_streaming",
    "generate_docs_fields",
    "generate_docs_fields_stream",
    "generate_role_response",
    "generate_role_response_streaming",
]
