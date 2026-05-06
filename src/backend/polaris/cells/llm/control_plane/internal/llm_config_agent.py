"""HR role agent and LLM configuration store implementation."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public import (
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


#: Sensitive keys that must be masked in API responses and logs.
_SENSITIVE_PROVIDER_KEYS: frozenset[str] = frozenset(
    {"api_key", "api_token", "access_token", "secret", "password", "auth_token"}
)

#: Allowed provider_cfg keys for generation params (data-plane).
_ALLOWED_PROVIDER_CFG_KEYS: frozenset[str] = frozenset(
    {
        "temperature",
        "top_p",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "timeout",
        "stop",
        "seed",
    }
)

#: Validation ranges for numeric provider_cfg values.
_PROVIDER_CFG_VALIDATION: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "presence_penalty": (-2.0, 2.0),
    "frequency_penalty": (-2.0, 2.0),
}

#: Maximum allowed max_tokens to prevent runaway billing/OOM.
_MAX_TOKENS_LIMIT: int = 32_768


def _mask_sensitive_values(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied provider_cfg with sensitive values masked."""
    result = copy.deepcopy(cfg)
    for key in result:
        if key.lower() in _SENSITIVE_PROVIDER_KEYS:
            result[key] = "***"
    return result


def _validate_provider_cfg(cfg: dict[str, Any]) -> None:
    """Validate provider_cfg values for known generation parameters.

    Only validates keys that are known generation parameters
    (temperature, max_tokens, etc.). Other keys (e.g. api_key)
    are allowed through without validation since they are
    provider-specific and will be masked in responses.

    Raises:
        ValueError: If any known parameter value is invalid.
    """
    for key, value in cfg.items():
        if key not in _ALLOWED_PROVIDER_CFG_KEYS:
            continue
        if key == "max_tokens":
            try:
                max_tokens = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"max_tokens must be an integer, got {value!r}") from exc
            if not (1 <= max_tokens <= _MAX_TOKENS_LIMIT):
                raise ValueError(f"max_tokens must be between 1 and {_MAX_TOKENS_LIMIT}, got {max_tokens}")
        if key in _PROVIDER_CFG_VALIDATION and value is not None:
            low, high = _PROVIDER_CFG_VALIDATION[key]
            try:
                num = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number, got {value!r}") from exc
            if not (low <= num <= high):
                raise ValueError(f"{key} must be between {low} and {high}, got {num}")


def _infer_workspace_from_storage_path(storage_path: str) -> str:
    target = Path(str(storage_path or "")).expanduser().resolve()
    path_str = str(target).lower()
    for marker in (".polaris", ".polaris-cache", "runtime"):
        idx = path_str.find(marker)
        if idx > 0:
            parent = str(target)[:idx]
            if parent:
                resolved = Path(parent).resolve()
                if str(resolved) != ".":
                    return str(resolved)
    return str(Path.cwd().resolve())


@dataclass(frozen=True, slots=True)
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

    def to_dict(self, *, mask_secrets: bool = False) -> dict[str, Any]:
        cfg = _mask_sensitive_values(self.provider_cfg) if mask_secrets else copy.deepcopy(self.provider_cfg)
        return {
            "config_id": self.config_id,
            "role": self.role,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "provider_kind": self.provider_kind,
            "model": self.model,
            "profile": self.profile,
            "provider_cfg": cfg,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": bool(self.is_active),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LLMConfig:
        raw_config_id = payload.get("config_id")
        raw_role = payload.get("role")
        raw_provider_id = payload.get("provider_id")
        raw_provider_type = payload.get("provider_type")
        raw_provider_kind = payload.get("provider_kind")
        raw_model = payload.get("model")
        raw_profile = payload.get("profile")
        raw_provider_cfg = payload.get("provider_cfg")

        return cls(
            config_id=str(raw_config_id) if raw_config_id else "",
            role=str(raw_role) if raw_role else "",
            provider_id=str(raw_provider_id) if raw_provider_id else "",
            provider_type=str(raw_provider_type) if raw_provider_type else "",
            provider_kind=str(raw_provider_kind) if raw_provider_kind else "",
            model=str(raw_model) if raw_model else "",
            profile=str(raw_profile) if raw_profile else "",
            provider_cfg=copy.deepcopy(raw_provider_cfg) if raw_provider_cfg else {},
            created_at=_parse_iso_timestamp(payload.get("created_at")),
            updated_at=_parse_iso_timestamp(payload.get("updated_at")),
            is_active=bool(payload.get("is_active", True)),
        )


class LLMConfigStore:
    """Thread-safe config store backed by KernelFileSystem with dict cache.

    Configs are stored at Global layer: ~/.polaris/config/llm/configs.json

    Performance optimizations:
    - In-memory dict cache avoids repeated JSON parsing
    - TTL-based cache invalidation (5 minutes default)
    - Dirty flag tracks pending writes
    - Optional preload on initialization
    """

    DEFAULT_CACHE_TTL: float = 300.0  # 5 minutes

    def __init__(
        self,
        storage_path: str,
        *,
        cache_ttl: float | None = None,
    ) -> None:
        self._storage_path = str(storage_path)
        self._workspace = _infer_workspace_from_storage_path(self._storage_path)
        self._fs = KernelFileSystem(self._workspace, get_default_adapter())
        self._lock = threading.RLock()
        self._configs_file_abs = str(Path(self._storage_path) / "configs.json")
        self._configs_file_logical = self._fs.to_logical_path(self._configs_file_abs)

        # Performance optimization: dict cache
        self._cache_ttl = cache_ttl if cache_ttl is not None else self.DEFAULT_CACHE_TTL
        self._cache: dict[str, dict[str, Any]] | None = None
        self._cache_timestamp: float = 0.0
        self._cache_dirty: bool = False

    def _is_cache_valid(self) -> bool:
        """Check if in-memory cache is still valid (not expired)."""
        if self._cache is None:
            return False
        return (time.monotonic() - self._cache_timestamp) < self._cache_ttl

    def _load_all_unlocked(self) -> dict[str, dict[str, Any]]:
        """Load all configs with dict cache optimization."""
        if self._cache is not None and not self._cache_dirty and self._is_cache_valid():
            return self._cache

        if not self._fs.exists(self._configs_file_logical):
            self._cache = {}
            self._cache_timestamp = time.monotonic()
            self._cache_dirty = False
            return {}

        payload = self._fs.read_json(self._configs_file_logical)
        result = payload if isinstance(payload, dict) else {}

        self._cache = result
        self._cache_timestamp = time.monotonic()
        self._cache_dirty = False
        return result

    def _save_all_unlocked(self, payload: dict[str, dict[str, Any]]) -> None:
        self._fs.write_json_atomic(
            self._configs_file_logical,
            payload,
            indent=2,
            ensure_ascii=False,
        )
        self._cache_dirty = False

    def preload(self) -> None:
        """Eagerly load all configs into cache. Call after construction for faster first access."""
        with self._lock:
            self._load_all_unlocked()

    def save(self, config: LLMConfig) -> None:
        token = (config.role or "").strip()
        with self._lock:
            rows = self._load_all_unlocked()
            rows[token] = config.to_dict()
            self._cache_dirty = True
            self._save_all_unlocked(rows)
            self._cache = rows
            self._cache_timestamp = time.monotonic()

    def get(self, role: str) -> LLMConfig | None:
        token = (role or "").strip()
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
        token = (role or "").strip()
        if not token:
            return False
        with self._lock:
            rows = self._load_all_unlocked()
            existed = token in rows
            if existed:
                rows.pop(token, None)
                self._cache_dirty = True
                self._save_all_unlocked(rows)
                self._cache = rows
                self._cache_timestamp = time.monotonic()
            return existed

    def invalidate_cache(self) -> None:
        """Manually invalidate the cache (e.g., after external modification)."""
        with self._lock:
            self._cache = None
            self._cache_dirty = False


class HRAgent(RoleAgent):
    """HR role agent for role/provider config operations."""

    def __init__(self, workspace: str) -> None:
        super().__init__(workspace=workspace, agent_name="HR")
        config_dir = str(Path(_get_polaris_home()) / "config" / "llm")
        self._config_store = LLMConfigStore(config_dir)
        self._config_store.preload()

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
        try:
            _validate_provider_cfg(cfg)
        except ValueError as exc:
            return {"ok": False, "error": "invalid_provider_cfg", "detail": str(exc)}
        item = LLMConfig(
            config_id=f"config_{role}_{uuid.uuid4().hex[:12]}",
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
        return {"ok": True, "config": item.to_dict(mask_secrets=True)}

    def _tool_get_llm_config(self, role: str) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": True, "has_config": False, "role": role}
        return {"ok": True, "has_config": True, "config": item.to_dict(mask_secrets=True)}

    def _tool_list_all_configs(self) -> dict[str, Any]:
        rows = self._config_store.get_all()
        return {"ok": True, "count": len(rows), "configs": [item.to_dict(mask_secrets=True) for item in rows]}

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
        kwargs: dict[str, Any] = {"updated_at": datetime.now()}
        if provider_id is not None:
            kwargs["provider_id"] = str(provider_id or "").strip()
        if model is not None:
            kwargs["model"] = str(model or "").strip()
        if profile is not None:
            kwargs["profile"] = str(profile or "").strip()
        if provider_cfg is not None:
            cfg = dict(provider_cfg)
            try:
                _validate_provider_cfg(cfg)
            except ValueError as exc:
                return {"ok": False, "error": "invalid_provider_cfg", "detail": str(exc)}
            kwargs["provider_cfg"] = cfg
        new_item = replace(item, **kwargs)
        new_item = replace(
            new_item,
            provider_kind=self._resolve_provider_kind(
                new_item.provider_id, new_item.provider_type, new_item.provider_cfg
            ),
        )
        self._config_store.save(new_item)
        return {"ok": True, "config": new_item.to_dict(mask_secrets=True)}

    def _tool_deactivate_config(self, role: str) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": False, "error": "config_not_found", "role": role}
        new_item = replace(item, is_active=False, updated_at=datetime.now())
        self._config_store.save(new_item)
        return {"ok": True, "config": new_item.to_dict(mask_secrets=True)}

    def _tool_activate_config(self, role: str) -> dict[str, Any]:
        item = self._config_store.get(role)
        if item is None:
            return {"ok": False, "error": "config_not_found", "role": role}
        new_item = replace(item, is_active=True, updated_at=datetime.now())
        self._config_store.save(new_item)
        return {"ok": True, "config": new_item.to_dict(mask_secrets=True)}

    def _tool_delete_config(self, role: str) -> dict[str, Any]:
        return {"ok": True, "deleted": self._config_store.delete(role), "role": role}

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

    def get_config(self, role: str) -> LLMConfig | None:
        return self._config_store.get(role)

    def get_all_configs(self) -> list[LLMConfig]:
        return self._config_store.get_all()
