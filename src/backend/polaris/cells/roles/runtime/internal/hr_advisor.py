"""RoleAgent hosting for the optional deterministic HR config advisor."""

from __future__ import annotations

from typing import Any, cast

from polaris.cells.llm.control_plane.public.service import LLMConfig, LLMConfigService

from .agent_runtime_base import AgentMessage, MessageType, RoleAgent


class HRAgent(RoleAgent):
    """Retain role lifecycle and messaging while LLM control plane owns config."""

    def __init__(self, workspace: str, *, config_dir: str | None = None) -> None:
        super().__init__(workspace=workspace, agent_name="HR")
        self._config_service = LLMConfigService(config_dir=config_dir)

    def setup_toolbox(self) -> None:
        tb = self.toolbox
        tb.register("set_llm_config", self._tool_set_llm_config)
        tb.register("get_llm_config", self._tool_get_llm_config)
        tb.register("list_all_configs", self._tool_list_all_configs)
        tb.register("update_llm_config", self._tool_update_llm_config)
        tb.register("deactivate_config", self._tool_deactivate_config)
        tb.register("activate_config", self._tool_activate_config)
        tb.register("delete_config", self._tool_delete_config)

    def _tool_set_llm_config(
        self,
        role: str,
        provider_id: str,
        provider_type: str,
        model: str,
        profile: str,
        provider_cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            (
                self._config_service.set_llm_config(
                    role=role,
                    provider_id=provider_id,
                    provider_type=provider_type,
                    model=model,
                    profile=profile,
                    provider_cfg=provider_cfg,
                )
            ),
        )

    def _tool_get_llm_config(self, role: str) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._config_service.get_llm_config(role)))

    def _tool_list_all_configs(self) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._config_service.list_all_configs()))

    def _tool_update_llm_config(
        self,
        role: str,
        provider_id: str | None = None,
        model: str | None = None,
        profile: str | None = None,
        provider_cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            (
                self._config_service.update_llm_config(
                    role=role, provider_id=provider_id, model=model, profile=profile, provider_cfg=provider_cfg
                )
            ),
        )

    def _tool_deactivate_config(self, role: str) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._config_service.deactivate_config(role)))

    def _tool_activate_config(self, role: str) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._config_service.activate_config(role)))

    def _tool_delete_config(self, role: str) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._config_service.delete_config(role)))

    def _resolve_provider_kind(self, provider_id: str, provider_type: str, provider_cfg: dict[str, Any]) -> str:
        return cast("str", (self._config_service.resolve_provider_kind(provider_id, provider_type, provider_cfg)))

    def get_config(self, role: str) -> LLMConfig | None:
        return self._config_service.get_config(role)

    def get_all_configs(self) -> list[LLMConfig]:
        return cast("list[Any]", (self._config_service.get_all_configs()))

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        payload = dict(message.payload or {})
        if message.type != MessageType.TASK:
            return None
        action = str(payload.get("action") or "").strip().lower()
        try:
            if action == "set_config":
                result = self._tool_set_llm_config(
                    role=str(payload.get("role") or "").strip(),
                    provider_id=str(payload.get("provider_id") or "").strip(),
                    provider_type=str(payload.get("provider_type") or "").strip(),
                    model=str(payload.get("model") or "").strip(),
                    profile=str(payload.get("profile") or "").strip(),
                    provider_cfg=payload.get("provider_cfg") if isinstance(payload.get("provider_cfg"), dict) else {},
                )
            elif action == "get_config":
                result = self._tool_get_llm_config(str(payload.get("role") or "").strip())
            elif action == "list_configs":
                result = self._tool_list_all_configs()
            else:
                result = {"ok": False, "error": "unsupported_action", "action": action}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": "internal_error", "detail": str(exc)}

        return AgentMessage.create(
            msg_type=MessageType.RESULT,
            sender=self.agent_name,
            receiver=message.sender,
            payload={"role": "hr", "action": action, "result": result},
            correlation_id=message.id,
        )

    def run_cycle(self) -> bool:
        message = self.message_queue.receive(block=False)
        if message is None:
            return False
        response = self.handle_message(message)
        if response is not None:
            self.message_queue.send(response)
        return True
