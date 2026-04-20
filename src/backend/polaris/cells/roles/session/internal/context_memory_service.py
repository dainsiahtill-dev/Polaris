"""Session-scoped Context OS memory facade for `roles.session`."""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
from polaris.cells.roles.session.public.contracts import (
    GetRoleSessionStateQueryV1,
    ReadRoleSessionArtifactQueryV1,
    ReadRoleSessionEpisodeQueryV1,
    RoleSessionContextQueryResultV1,
    SearchRoleSessionMemoryQueryV1,
)
from polaris.kernelone.context.context_os import ContextOSSnapshot, StateFirstContextOS


class RoleSessionContextMemoryService:
    """Read Context OS state from the `roles.session` source-of-truth."""

    def __init__(
        self,
        *,
        session_service: RoleSessionService | None = None,
    ) -> None:
        self._session_service = session_service
        self._owns_service = session_service is None
        self._context_os = StateFirstContextOS()

    @property
    def session_service(self) -> RoleSessionService:
        if self._session_service is None:
            self._session_service = RoleSessionService()
        return self._session_service

    def close(self) -> None:
        if self._owns_service and self._session_service is not None:
            self._session_service.close()
            self._session_service = None

    def __enter__(self) -> RoleSessionContextMemoryService:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _load_snapshot(self, session_id: str) -> ContextOSSnapshot | None:
        payload = self.session_service.get_context_os_snapshot(session_id)
        return ContextOSSnapshot.from_mapping(payload)

    def search_memory(
        self,
        query: SearchRoleSessionMemoryQueryV1,
    ) -> RoleSessionContextQueryResultV1:
        snapshot = self._load_snapshot(query.session_id)
        if snapshot is None:
            return RoleSessionContextQueryResultV1(
                ok=True,
                session_id=query.session_id,
                payload=[],
            )
        payload = self._context_os.search_memory(
            snapshot,
            query.query,
            kind=query.kind,
            entity=query.entity,
            limit=query.limit,
        )
        return RoleSessionContextQueryResultV1(ok=True, session_id=query.session_id, payload=payload)

    def read_artifact(
        self,
        query: ReadRoleSessionArtifactQueryV1,
    ) -> RoleSessionContextQueryResultV1:
        snapshot = self._load_snapshot(query.session_id)
        if snapshot is None:
            return RoleSessionContextQueryResultV1(
                ok=False,
                session_id=query.session_id,
                error_code="context_os_snapshot_missing",
                error_message="No persisted State-First Context OS snapshot is available for this session.",
            )
        span = None
        if query.start_line is not None or query.end_line is not None:
            start = query.start_line or 1
            end = query.end_line or start
            span = (start, end)
        payload = self._context_os.read_artifact(snapshot, query.artifact_id, span=span)
        if payload is None:
            return RoleSessionContextQueryResultV1(
                ok=False,
                session_id=query.session_id,
                error_code="artifact_not_found",
                error_message=f"Artifact not found: {query.artifact_id}",
            )
        return RoleSessionContextQueryResultV1(ok=True, session_id=query.session_id, payload=payload)

    def read_episode(
        self,
        query: ReadRoleSessionEpisodeQueryV1,
    ) -> RoleSessionContextQueryResultV1:
        snapshot = self._load_snapshot(query.session_id)
        if snapshot is None:
            return RoleSessionContextQueryResultV1(
                ok=False,
                session_id=query.session_id,
                error_code="context_os_snapshot_missing",
                error_message="No persisted State-First Context OS snapshot is available for this session.",
            )
        payload = self._context_os.read_episode(snapshot, query.episode_id)
        if payload is None:
            return RoleSessionContextQueryResultV1(
                ok=False,
                session_id=query.session_id,
                error_code="episode_not_found",
                error_message=f"Episode not found: {query.episode_id}",
            )
        return RoleSessionContextQueryResultV1(ok=True, session_id=query.session_id, payload=payload)

    def get_state(
        self,
        query: GetRoleSessionStateQueryV1,
    ) -> RoleSessionContextQueryResultV1:
        snapshot = self._load_snapshot(query.session_id)
        if snapshot is None:
            return RoleSessionContextQueryResultV1(
                ok=False,
                session_id=query.session_id,
                error_code="context_os_snapshot_missing",
                error_message="No persisted State-First Context OS snapshot is available for this session.",
            )
        payload = self._context_os.get_state(snapshot, query.path)
        if payload is None:
            return RoleSessionContextQueryResultV1(
                ok=False,
                session_id=query.session_id,
                error_code="state_path_not_found",
                error_message=f"State path not found: {query.path}",
            )
        return RoleSessionContextQueryResultV1(ok=True, session_id=query.session_id, payload=payload)

    # Plain adapter methods for KernelOne toolkit integration -----------------

    def search_memory_for_session(
        self,
        session_id: str,
        query: str,
        *,
        kind: str | None = None,
        entity: str | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        result = self.search_memory(
            SearchRoleSessionMemoryQueryV1(
                session_id=session_id,
                query=query,
                kind=kind,
                entity=entity,
                limit=limit,
            )
        )
        return list(result.payload or [])

    def read_artifact_for_session(
        self,
        session_id: str,
        artifact_id: str,
        *,
        span: tuple[int, int] | None = None,
    ) -> dict[str, Any] | None:
        result = self.read_artifact(
            ReadRoleSessionArtifactQueryV1(
                session_id=session_id,
                artifact_id=artifact_id,
                start_line=span[0] if span is not None else None,
                end_line=span[1] if span is not None else None,
            )
        )
        return dict(result.payload) if isinstance(result.payload, dict) else None

    def read_episode_for_session(
        self,
        session_id: str,
        episode_id: str,
    ) -> dict[str, Any] | None:
        result = self.read_episode(
            ReadRoleSessionEpisodeQueryV1(
                session_id=session_id,
                episode_id=episode_id,
            )
        )
        return dict(result.payload) if isinstance(result.payload, dict) else None

    def get_state_for_session(
        self,
        session_id: str,
        path: str,
    ) -> Any:
        result = self.get_state(
            GetRoleSessionStateQueryV1(
                session_id=session_id,
                path=path,
            )
        )
        return result.payload


__all__ = ["RoleSessionContextMemoryService"]
