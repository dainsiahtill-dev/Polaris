"""Public service exports for `llm.dialogue` cell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.llm.dialogue.internal.docs_dialogue import (
    build_dialogue_prompt,
    build_dialogue_state,
    finalize_dialogue_payload,
    generate_dialogue_fallback,
    generate_dialogue_turn,
    generate_dialogue_turn_streaming,
)
from polaris.cells.llm.dialogue.internal.docs_suggest import (
    build_docs_prompt,
    generate_docs_fields,
    generate_docs_fields_stream,
)
from polaris.cells.llm.dialogue.internal.role_dialogue import (
    ROLE_PROMPT_TEMPLATES,
    RoleOutputParser,
    RoleOutputQualityChecker,
    generate_role_response,
    generate_role_response_streaming,
    get_registered_roles,
    register_role_template,
    validate_and_parse_role_output,
)
from polaris.cells.llm.dialogue.public.contracts import (
    DialogueTurnResultV1,
    ILlmDialogueService,
    InvokeDocsDialogueCommandV1,
    InvokeRoleDialogueCommandV1,
    ValidateRoleOutputQueryV1,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class LlmDialogueService(ILlmDialogueService):
    """Contract-first dialogue facade."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def invoke_role_dialogue(
        self,
        command: InvokeRoleDialogueCommandV1,
    ) -> DialogueTurnResultV1:
        try:
            payload = await generate_role_response(
                workspace=command.workspace,
                settings=self._settings,
                role=command.role,
                message=command.message,
                context=dict(command.context),
            )
            content = str(payload.get("response") or payload.get("reply") or "")
            return DialogueTurnResultV1(
                ok=True,
                status="ok",
                workspace=command.workspace,
                role=command.role,
                content=content,
                metadata=dict(payload),
            )
        except (RuntimeError, ValueError) as exc:
            return DialogueTurnResultV1(
                ok=False,
                status="failed",
                workspace=command.workspace,
                role=command.role,
                content="",
                metadata={},
                error_code="role_dialogue_error",
                error_message=str(exc),
            )

    async def invoke_docs_dialogue(
        self,
        command: InvokeDocsDialogueCommandV1,
    ) -> DialogueTurnResultV1:
        try:
            state_mapping = dict(command.state)
            history = state_mapping.get("history")
            history_list = list(history) if isinstance(history, list) else []
            payload = await generate_dialogue_turn(
                workspace=command.workspace,
                settings=self._settings,
                fields=dict(command.fields),
                history=history_list,
                message=command.message,
            )
            result_payload = dict(payload or {})
            content = str(result_payload.get("reply") or "")
            return DialogueTurnResultV1(
                ok=True,
                status="ok",
                workspace=command.workspace,
                role="architect",
                content=content,
                metadata=result_payload,
            )
        except (RuntimeError, ValueError) as exc:
            return DialogueTurnResultV1(
                ok=False,
                status="failed",
                workspace=command.workspace,
                role="architect",
                content="",
                metadata={},
                error_code="docs_dialogue_error",
                error_message=str(exc),
            )

    def validate_role_output(self, query: ValidateRoleOutputQueryV1) -> Mapping[str, Any]:
        return dict(validate_and_parse_role_output(query.role, query.output))


__all__ = [
    "ROLE_PROMPT_TEMPLATES",
    "DialogueTurnResultV1",
    "ILlmDialogueService",
    "InvokeDocsDialogueCommandV1",
    "InvokeRoleDialogueCommandV1",
    "LlmDialogueService",
    "RoleOutputParser",
    "RoleOutputQualityChecker",
    "ValidateRoleOutputQueryV1",
    "build_dialogue_prompt",
    "build_dialogue_state",
    "build_docs_prompt",
    "finalize_dialogue_payload",
    "generate_dialogue_fallback",
    "generate_dialogue_turn",
    "generate_dialogue_turn_streaming",
    "generate_docs_fields",
    "generate_docs_fields_stream",
    "generate_role_response",
    "generate_role_response_streaming",
    "get_registered_roles",
    "register_role_template",
    "validate_and_parse_role_output",
]
