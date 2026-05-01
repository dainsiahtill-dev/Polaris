"""HR role agent and LLM configuration store implementation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.service import (
    AgentMessage,
    MessageType,
    RoleAgent,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def _parse_iso_timestamp(value: Any) -> datetime:
    """Safely parse an ISO timestamp, falling back to now() on empty/invalid input."""
    raw = value
    if not raw or not isinstance(raw, str) or not raw.strip():
        return datetime.now()
    try:
        return datetime.fromisoformat(raw.strip())
    except (TypeError, ValueError):
        return datetime.now()


# Lazy import to avoid circular dependency at module level
def _get_polaris_home() -> str:
    from polaris.cells.storage.layout.public.service import polaris_home

    return polaris_home()


def _infer_workspace_from_storage_path(storage_path: str) -> str:
    target = Path(str(storage_path or "")).expanduser().resolve()
    lower = [part.lower() for part in target.parts]
    for marker in (".polaris", ".polaris", ".polaris-cache", ".polaris-cache", "runtime"):
        if marker not in lower:
            continue
        idx = lower.index(marker)
        if idx > 0:
            return str(Path(*target.parts[:idx]).resolve())
    return str(Path.cwd().resolve())


@dataclass
class LLMConfig:
    """LLM configuration for a role."""

    config_id: str
    role: str
    provider_id: str
    provider_type: str
    provider_kind: str
    model: str
    profile: str
    provider_cfg: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "role": self.role,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "provider_kind": self.provider_kind,
            "model": self.model,
            "profile": self.profile,
            "provider_cfg": dict(self.provider_cfg),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": bool(self.is_active),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LLMConfig:
        return cls(
            config_id=str(payload.get("config_id") or ""),
            role=str(payload.get("role") or ""),
            provider_id=str(payload.get("provider_id") or ""),
            provider_type=str(payload.get("provider_type") or ""),
            provider_kind=str(payload.get("provider_kind") or ""),
            model=str(payload.get("model") or ""),
            profile=str(payload.get("profile") or ""),
            provider_cfg=dict(payload.get("provider_cfg") or {}),
            created_at=_parse_iso_timestamp(payload.get("created_at")),
            updated_at=_parse_iso_timestamp(payload.get("updated_at")),
            is_active=bool(payload.get("is_active", True)),
        )


class LLMConfigStore:
    """Thread-safe config store backed by KernelFileSystem.

    Configs are stored at Global layer: ~/.polaris/config/llm/configs.json
    """

    def __init__(self, storage_path: str) -> None:
        self._storage_path = str(storage_path)
        self._workspace = _infer_workspace_from_storage_path(self._storage_path)
        self._fs = KernelFileSystem(self._workspace, get_default_adapter())
        self._lock = threading.RLock()
        self._configs_file_abs = str(Path(self._storage_path) / "configs.json")
        self._configs_file_logical = self._fs.to_logical_path(self._configs_file_abs)

    def _load_all_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self._fs.exists(self._configs_file_logical):
            return {}
        payload = self._fs.read_json(self._configs_file_logical)
        return payload if isinstance(payload, dict) else {}

    def _save_all_unlocked(self, payload: dict[str, dict[str, Any]]) -> None:
        self._fs.write_json(
            self._configs_file_logical,
            payload,
            indent=2,
            ensure_ascii=False,
        )

    def save(self, config: LLMConfig) -> None:
        with self._lock:
            rows = self._load_all_unlocked()
            rows[str(config.role)] = config.to_dict()
            self._save_all_unlocked(rows)

    def get(self, role: str) -> LLMConfig | None:
        token = str(role or "").strip()
        if not token:
            return None
        with self._lock:
            rows = self._load_all_unlocked()
        payload = rows.get(token)
        if not isinstance(payload, dict):
            return None
        try:
            return LLMConfig.from_dict(payload)
        except (RuntimeError, ValueError):
            return None

    def get_all(self) -> list[LLMConfig]:
        with self._lock:
            rows = self._load_all_unlocked()
        configs: list[LLMConfig] = []
        for payload in rows.values():
            if not isinstance(payload, dict):
                continue
            try:
                configs.append(LLMConfig.from_dict(payload))
            except (RuntimeError, ValueError):
                continue
        return sorted(configs, key=lambda item: item.role)

    def delete(self, role: str) -> bool:
        token = str(role or "").strip()
        if not token:
            return False
        with self._lock:
            rows = self._load_all_unlocked()
            existed = token in rows
            if existed:
                rows.pop(token, None)
                self._save_all_unlocked(rows)
            return existed


class HRAgent(RoleAgent):
    """HR role agent for role/provider config operations."""

    def __init__(self, workspace: str) -> None:
        super().__init__(workspace=workspace, agent_name="HR")
        # LLM config is global (per-user), stored under ~/.polaris/config/llm/
        config_dir = str(Path(_get_polaris_home()) / "config" / "llm")
        self._config_store = LLMConfigStore(config_dir)

    def _resolve_provider_kind(
        self,
        provider_id: str,
        provider_type: str,
        provider_cfg: dict[str, Any],
    ) -> str:
        token = str(provider_type or "").strip().lower()
        command = str(provider_cfg.get("command") or "").strip().lower()
        if token == "ollama":
            return "ollama"
        if token in {"codex_cli", "codex_sdk"}:
            return "codex"
        if token == "cli" and ("codex" in command or str(provider_id or "").strip() == "codex_cli"):
            return "codex"
        return "generic"

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
        now = datetime.now()
        cfg = dict(provider_cfg or {})
        item = LLMConfig(
            config_id=f"config_{role}_{now.strftime('%Y%m%d%H%M%S%f')}",
            role=str(role or "").strip(),
            provider_id=str(provider_id or "").strip(),
            provider_type=str(provider_type or "").strip().lower(),
            provider_kind=self._resolve_provider_kind(provider_id, provider_type, cfg),
            model=str(model or "").strip(),
            profile=str(profile or "").strip(),
            provider_cfg=cfg,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        self._config_store.save(item)
        return {"ok": True, "config": item.to_dict()}

    def _tool_get_llm_config(self, role: str) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": True, "has_config": False, "role": role}
        return {"ok": True, "has_config": True, "config": item.to_dict()}

    def _tool_list_all_configs(self) -> dict[str, Any]:
        rows = self._config_store.get_all()
        return {"ok": True, "count": len(rows), "configs": [item.to_dict() for item in rows]}

    def _tool_update_llm_config(
        self,
        role: str,
        provider_id: str | None = None,
        model: str | None = None,
        profile: str | None = None,
        provider_cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": False, "error": "config_not_found", "role": role}
        if provider_id is not None:
            item.provider_id = str(provider_id or "").strip()
        if model is not None:
            item.model = str(model or "").strip()
        if profile is not None:
            item.profile = str(profile or "").strip()
        if provider_cfg is not None:
            item.provider_cfg = dict(provider_cfg)
        item.provider_kind = self._resolve_provider_kind(item.provider_id, item.provider_type, item.provider_cfg)
        item.updated_at = datetime.now()
        self._config_store.save(item)
        return {"ok": True, "config": item.to_dict()}

    def _tool_deactivate_config(self, role: str) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": False, "error": "config_not_found", "role": role}
        item.is_active = False
        item.updated_at = datetime.now()
        self._config_store.save(item)
        return {"ok": True, "config": item.to_dict()}

    def _tool_activate_config(self, role: str) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": False, "error": "config_not_found", "role": role}
        item.is_active = True
        item.updated_at = datetime.now()
        self._config_store.save(item)
        return {"ok": True, "config": item.to_dict()}

    def _tool_delete_config(self, role: str) -> dict[str, Any]:
        return {"ok": True, "deleted": self._config_store.delete(role), "role": role}

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        payload = dict(message.payload or {})
        if message.type != MessageType.TASK:
            return None
        action = str(payload.get("action") or "").strip().lower()
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

    def get_config(self, role: str) -> LLMConfig | None:
        return self._config_store.get(role)

    def get_all_configs(self) -> list[LLMConfig]:
        return self._config_store.get_all()
