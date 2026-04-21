"""Public service exports for `llm.control_plane` cell."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.control_plane.internal.llm_config_agent import HRAgent, LLMConfig, LLMConfigStore
from polaris.cells.llm.control_plane.internal.providers.ollama import list_ollama_models, ollama_stop
from polaris.cells.llm.control_plane.internal.tui_llm_client import (
    LLMMessage,
    TUILLMClient,
    get_role_system_prompt,
)
from polaris.cells.llm.control_plane.internal.vision_service import get_vision_service
from polaris.cells.llm.control_plane.public.contracts import (
    GetLlmConfigQueryV1,
    GetLlmRuntimeStatusQueryV1,
    ILLMControlPlane,
    InvokeLlmRoleCommandV1,
    LlmConfigResultV1,
    LlmInvocationResultV1,
    LLMRequest,
    LLMResponse,
    SaveLlmConfigCommandV1,
)
from polaris.kernelone.telemetry.metrics import MetricsRecorder, Timer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping


def _looks_like_error_message(content: str) -> bool:
    text = str(content or "").strip()
    return text.startswith("[错误]") or text.startswith("[调用错误]") or text.startswith("[LLM 错误]")


def _resolve_control_plane_storage_path(workspace: str) -> str:
    """Resolve LLM control plane storage to Global config layer.

    LLM role configs are user-global, stored under ~/.polaris/config/llm/.
    """
    from polaris.cells.storage.layout.public.service import polaris_home

    return str(Path(polaris_home()) / "config" / "llm")


def _normalize_provider_type(payload: Mapping[str, Any]) -> str:
    return str(payload.get("type") or payload.get("provider_type") or "openai_compat").strip().lower()


def _normalize_provider_kind(provider_type: str, payload: Mapping[str, Any]) -> str:
    token = str(payload.get("provider_kind") or "").strip().lower()
    if token:
        return token
    if provider_type in {"ollama", "codex_cli", "codex_sdk"}:
        return "codex" if provider_type.startswith("codex") else provider_type
    return "generic"


class LlmControlPlaneService(ILLMControlPlane):
    """Contract-first service for `llm.control_plane`."""

    def __init__(self, *, default_role: str = "pm", default_workspace: str = ".") -> None:
        self._default_role = str(default_role or "pm").strip() or "pm"
        self._default_workspace = str(default_workspace or ".").strip() or "."
        self._stores: dict[str, LLMConfigStore] = {}
        self._store_lock = Lock()

        # Initialize metrics recorder
        self.metrics = MetricsRecorder()
        self.metrics.define_counter(
            "llm_invocations_total", description="Total number of LLM invocations", labels=["role", "status"]
        )
        self.metrics.define_histogram(
            "llm_response_duration_seconds",
            description="LLM response duration in seconds",
            labels=["role"],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        )
        self.metrics.define_histogram(
            "llm_tokens_used",
            description="LLM tokens used per request",
            labels=["role", "token_type"],
            buckets=(10, 50, 100, 500, 1000, 5000, 10000, 50000),
        )

    def _get_store(self, workspace: str) -> LLMConfigStore:
        workspace_token = str(workspace or "").strip() or "."
        with self._store_lock:
            store = self._stores.get(workspace_token)
            if store is None:
                store = LLMConfigStore(_resolve_control_plane_storage_path(workspace_token))
                self._stores[workspace_token] = store
        return store

    @staticmethod
    def _build_role_message(command: InvokeLlmRoleCommandV1) -> str:
        base = str(command.message or "").strip()
        if not command.context:
            return base
        context_json = json.dumps(dict(command.context), ensure_ascii=False, sort_keys=True)
        return f"{base}\n\n[context]\n{context_json}"

    def save_config(self, command: SaveLlmConfigCommandV1) -> LlmConfigResultV1:
        payload = dict(command.config)
        provider_type = _normalize_provider_type(payload)
        provider_kind = _normalize_provider_kind(provider_type, payload)
        profile = str(payload.get("profile") or command.role).strip() or command.role
        now = datetime.now(timezone.utc)
        config = LLMConfig(
            config_id=str(payload.get("config_id") or f"cfg_{command.role}_{int(now.timestamp())}"),
            role=command.role,
            provider_id=command.provider_id,
            provider_type=provider_type,
            provider_kind=provider_kind,
            model=command.model,
            profile=profile,
            provider_cfg=payload,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        self._get_store(command.workspace).save(config)
        return LlmConfigResultV1(
            workspace=command.workspace,
            role=command.role,
            provider_id=command.provider_id,
            model=command.model,
            ready=True,
            metadata={
                "provider_type": provider_type,
                "provider_kind": provider_kind,
                "profile": profile,
            },
        )

    def get_config(self, query: GetLlmConfigQueryV1) -> LlmConfigResultV1:
        config = self._get_store(query.workspace).get(query.role)
        if config is None:
            return LlmConfigResultV1(
                workspace=query.workspace,
                role=query.role,
                provider_id="unconfigured",
                model="unconfigured",
                ready=False,
                metadata={},
            )
        return LlmConfigResultV1(
            workspace=query.workspace,
            role=config.role,
            provider_id=config.provider_id,
            model=config.model,
            ready=bool(config.is_active),
            metadata={
                "provider_type": config.provider_type,
                "provider_kind": config.provider_kind,
                "profile": config.profile,
                "provider_cfg": dict(config.provider_cfg),
            },
        )

    def get_runtime_status(self, query: GetLlmRuntimeStatusQueryV1) -> Mapping[str, Any]:
        configs = self._get_store(query.workspace).get_all()
        role_map = {item.role: item for item in configs}
        payload: dict[str, Any] = {
            "workspace": query.workspace,
            "configured_roles": sorted(role_map.keys()),
            "configured_count": len(role_map),
            "ready": bool(role_map),
        }
        if query.role:
            role_cfg = role_map.get(query.role)
            payload["role"] = query.role
            payload["role_configured"] = role_cfg is not None
            if role_cfg is not None:
                payload["provider_id"] = role_cfg.provider_id
                payload["model"] = role_cfg.model
                payload["provider_type"] = role_cfg.provider_type
        return payload

    async def invoke_role(self, command: InvokeLlmRoleCommandV1) -> LlmInvocationResultV1:
        with Timer("llm_invoke_timer") as timer:
            client = TUILLMClient(
                role=command.role,
                workspace=command.workspace,
                system_prompt=get_role_system_prompt(command.role),
            )
            message = self._build_role_message(command)
            chunks: list[str] = []
            if command.stream:
                content = await client.chat_stream(
                    [LLMMessage(role="user", content=message)],
                    on_token=chunks.append,
                )
            else:
                content = await client.chat([LLMMessage(role="user", content=message)])
            ok = not _looks_like_error_message(content)

            # Record metrics
            duration_seconds = timer.elapsed_seconds
            self.metrics.get_histogram("llm_response_duration_seconds", {"role": command.role}).observe(
                duration_seconds
            )
            status = "success" if ok else "error"
            self.metrics.get_counter("llm_invocations_total", {"role": command.role, "status": status}).inc()

            return LlmInvocationResultV1(
                ok=ok,
                workspace=command.workspace,
                role=command.role,
                content=content,
                metadata={
                    "configured": client.is_configured(),
                    "stream": bool(command.stream),
                    "chunk_count": len(chunks),
                    "context_keys": sorted(str(key) for key in command.context),
                },
            )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        with Timer("llm_generate_timer") as timer:
            client = TUILLMClient(
                role=self._default_role,
                workspace=self._default_workspace,
                system_prompt=str(request.system_prompt or "").strip(),
            )
            content = await client.chat([LLMMessage(role="user", content=request.prompt)])

            # Record metrics
            duration_seconds = timer.elapsed_seconds
            self.metrics.get_histogram("llm_response_duration_seconds", {"role": self._default_role}).observe(
                duration_seconds
            )
            self.metrics.get_counter("llm_invocations_total", {"role": self._default_role, "status": "success"}).inc()

            return LLMResponse(content=content)

    async def stream(
        self,
        request: LLMRequest,
        *,
        timeout: float = 0.05,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from the LLM provider.

        Parameters
        ----------
        request:
            The LLM request to process.
        timeout:
            Maximum seconds to wait per queue drain iteration.  Defaults to 0.05 s.
            Smaller values reduce latency at the cost of higher CPU; larger values
            reduce CPU at the cost of higher latency.

        Completeness contract
        ---------------------
        The loop exits only after the provider has emitted StreamEventType.COMPLETE
        (signalled by ``producer.done()``) AND the internal queue is fully drained.
        This guarantees that no tokens are silently dropped even when the provider
        produces a burst of text followed by the COMPLETE event.
        """
        client = TUILLMClient(
            role=self._default_role,
            workspace=self._default_workspace,
            system_prompt=str(request.system_prompt or "").strip(),
        )
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        # Mutable flag shared between the producer callback and the consumer loop.
        # Set to True once the provider signals completion.
        saw_complete: list[bool] = [False]

        def _on_token(token: str) -> None:
            if token:
                queue.put_nowait(str(token))
            # StreamEventType.COMPLETE is represented by the empty sentinel.
            if token == "":
                saw_complete[0] = True

        producer = asyncio.create_task(
            client.chat_stream([LLMMessage(role="user", content=request.prompt)], on_token=_on_token)
        )
        yielded = False
        while True:
            # Exit only when the provider has finished AND we have drained
            # all pending tokens AND we have observed the COMPLETE signal.
            if producer.done() and queue.empty() and saw_complete[0]:
                break
            try:
                token = await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError:
                # If the producer is done but we haven't observed COMPLETE yet,
                # the queue may be momentarily empty — keep waiting up to timeout
                # to give the COMPLETE signal time to arrive.
                if producer.done() and saw_complete[0]:
                    break
                continue
            yielded = True
            if token:
                yield token
        tail = await producer
        if not yielded and tail:
            yield str(tail)


_DEFAULT_LLM_CONTROL_PLANE = LlmControlPlaneService()


def save_llm_config(command: SaveLlmConfigCommandV1) -> LlmConfigResultV1:
    return _DEFAULT_LLM_CONTROL_PLANE.save_config(command)


def get_llm_config(query: GetLlmConfigQueryV1) -> LlmConfigResultV1:
    return _DEFAULT_LLM_CONTROL_PLANE.get_config(query)


def get_llm_runtime_status(query: GetLlmRuntimeStatusQueryV1) -> Mapping[str, Any]:
    return _DEFAULT_LLM_CONTROL_PLANE.get_runtime_status(query)


async def invoke_llm_role(command: InvokeLlmRoleCommandV1) -> LlmInvocationResultV1:
    return await _DEFAULT_LLM_CONTROL_PLANE.invoke_role(command)


def load_llm_config_port(
    workspace: str,
    cache_root: str,
) -> dict[str, Any]:
    """Public port for loading LLM config.

    This is the official entry point for other Cells (e.g. llm.provider_config)
    to load LLM configuration without importing from the KernelOne implementation
    directly.

    Args:
        workspace: The workspace root path.
        cache_root: The cache root path.

    Returns:
        The LLM configuration dictionary.
    """
    from polaris.kernelone.llm import config_store as _impl

    return _impl.load_llm_config(workspace, cache_root)


__all__ = [
    "HRAgent",
    "ILLMControlPlane",
    "LLMConfig",
    "LLMConfigStore",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "LlmControlPlaneService",
    "TUILLMClient",
    "get_llm_config",
    "get_llm_runtime_status",
    "get_role_system_prompt",
    "get_vision_service",
    "invoke_llm_role",
    "list_ollama_models",
    "load_llm_config_port",
    "ollama_stop",
    "save_llm_config",
]
