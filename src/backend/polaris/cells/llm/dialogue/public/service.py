"""Public service exports for `llm.dialogue` cell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from polaris.cells.llm.dialogue.internal.docs_dialogue import (
    build_dialogue_prompt,
    build_dialogue_state,
    finalize_dialogue_payload,
    generate_dialogue_fallback,
    generate_dialogue_turn,
    generate_dialogue_turn_streaming,
)
from polaris.cells.llm.dialogue.internal.docs_suggest import (
    build_default_docs_fields,
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


def _string(value: Any) -> str:
    return str(value or "").strip()


def _create_role_runtime_service() -> Any:
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    return RoleRuntimeService()


class LlmDialogueService(ILlmDialogueService):
    """Contract-first dialogue facade."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def invoke_role_dialogue(
        self,
        command: InvokeRoleDialogueCommandV1,
    ) -> DialogueTurnResultV1:
        try:
            from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

            context_payload = dict(command.context)
            metadata_payload = dict(command.metadata)
            metadata_payload.update(
                {
                    "source": "llm.dialogue.public.service",
                    "role_runtime_required": True,
                    "cognitive_runtime_required": True,
                    "context_os_expected": True,
                    "runtime_fallback_used": False,
                    "fallback_policy": "fail_closed",
                    "llm_dialogue_public_compat": True,
                }
            )
            run_id = _string(context_payload.get("run_id") or metadata_payload.get("run_id"))
            task_id = _string(context_payload.get("task_id") or metadata_payload.get("task_id"))
            session_id = _string(
                context_payload.get("session_id")
                or context_payload.get("runtime_session_id")
                or metadata_payload.get("session_id")
            )
            if not session_id:
                session_id = f"{command.role}-dialogue-{uuid4().hex}"

            result = await _create_role_runtime_service().execute_role_session(
                ExecuteRoleSessionCommandV1(
                    role=command.role,
                    session_id=session_id,
                    workspace=command.workspace,
                    user_message=command.message,
                    run_id=run_id or None,
                    task_id=task_id or None,
                    domain=_string(context_payload.get("domain") or metadata_payload.get("domain")) or "general",
                    history=(),
                    context=context_payload,
                    metadata=metadata_payload,
                    stream=False,
                    host_kind="llm_dialogue_public_service",
                )
            )
            result_metadata = dict(getattr(result, "metadata", {}) or {})
            result_metadata.update(
                {
                    "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                    "role_runtime_session_id": session_id,
                    "runtime_fallback_used": False,
                    "fallback_policy": "fail_closed",
                    "llm_dialogue_public_compat": True,
                    "usage": dict(getattr(result, "usage", {}) or {}),
                    "tool_calls": list(getattr(result, "tool_calls", ()) or ()),
                    "artifacts": list(getattr(result, "artifacts", ()) or ()),
                }
            )
            content = str(getattr(result, "output", "") or "")
            error_message = _string(getattr(result, "error_message", ""))
            error_code = _string(getattr(result, "error_code", ""))
            ok = bool(getattr(result, "ok", False)) and not bool(error_message or error_code)
            return DialogueTurnResultV1(
                ok=ok,
                status="ok" if ok else "failed",
                workspace=command.workspace,
                role=command.role,
                content=content,
                metadata=result_metadata,
                error_code=None if ok else (error_code or "role_runtime_error"),
                error_message=None if ok else (error_message or "role runtime failed"),
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
    "build_default_docs_fields",
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
