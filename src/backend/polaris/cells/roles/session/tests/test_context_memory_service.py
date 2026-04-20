from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.cells.roles.session.internal.context_memory_service import (
    RoleSessionContextMemoryService,
)
from polaris.cells.roles.session.internal.conversation import Base
from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
from polaris.cells.roles.session.public.contracts import (
    GetRoleSessionStateQueryV1,
    ReadRoleSessionArtifactQueryV1,
    SearchRoleSessionMemoryQueryV1,
)
from polaris.kernelone.context.context_os import CodeContextDomainAdapter, StateFirstContextOS
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def db_session() -> Generator[DbSession, None, None]:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def role_session_service(db_session: DbSession) -> RoleSessionService:
    return RoleSessionService(db=db_session)


@pytest.fixture
def memory_service(role_session_service: RoleSessionService) -> RoleSessionContextMemoryService:
    return RoleSessionContextMemoryService(session_service=role_session_service)


def _seed_context_os_snapshot(service: RoleSessionService) -> str:
    projection = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())._project_impl(
        messages=[
            {
                "role": "user",
                "content": "Fix polaris/kernelone/context/session_continuity.py and preserve session continuity runtime.",
                "sequence": 1,
            },
            {
                "role": "assistant",
                "content": "I will update the continuity runtime and keep the public facade stable.",
                "sequence": 2,
            },
            {
                "role": "tool",
                "content": "```python\nfrom polaris.kernelone.context.session_continuity import SessionContinuityEngine\n```",
                "sequence": 3,
            },
        ],
        recent_window_messages=2,
    )
    persisted_snapshot = projection.snapshot.to_dict()
    persisted_snapshot.pop("transcript_log", None)
    session = service.create_session(
        role="director",
        context_config={"state_first_context_os": persisted_snapshot},
    )
    return str(session.id)


def test_search_memory_reads_persisted_context_os(
    memory_service: RoleSessionContextMemoryService, role_session_service: RoleSessionService
) -> None:
    session_id = _seed_context_os_snapshot(role_session_service)

    result = memory_service.search_memory(
        SearchRoleSessionMemoryQueryV1(
            session_id=session_id,
            query="session_continuity.py",
            limit=4,
        )
    )

    assert result.ok is True
    payload = list(result.payload or [])
    assert payload
    assert any("session_continuity.py" in str(item.get("text") or "") for item in payload)
    assert isinstance(payload[0].get("score_breakdown"), dict)
    assert isinstance(payload[0].get("why"), list)


def test_read_artifact_returns_content_slice(
    memory_service: RoleSessionContextMemoryService, role_session_service: RoleSessionService
) -> None:
    session_id = _seed_context_os_snapshot(role_session_service)
    search = memory_service.search_memory(
        SearchRoleSessionMemoryQueryV1(
            session_id=session_id,
            query="SessionContinuityEngine",
            kind="artifact",
            limit=1,
        )
    )
    first = next(iter(search.payload or []))
    artifact_id = str(first["id"])

    result = memory_service.read_artifact(
        ReadRoleSessionArtifactQueryV1(
            session_id=session_id,
            artifact_id=artifact_id,
            start_line=1,
            end_line=1,
        )
    )

    assert result.ok is True
    payload = dict(result.payload or {})
    assert payload["artifact_id"] == artifact_id
    assert "SessionContinuityEngine" in str(payload.get("content") or "")


def test_get_state_reads_current_goal(
    memory_service: RoleSessionContextMemoryService, role_session_service: RoleSessionService
) -> None:
    session_id = _seed_context_os_snapshot(role_session_service)

    result = memory_service.get_state(
        GetRoleSessionStateQueryV1(
            session_id=session_id,
            path="task_state.current_goal",
        )
    )

    assert result.ok is True
    payload = dict(result.payload or {})
    assert "session continuity" in str(payload.get("value") or "").lower()


def test_get_state_can_rebuild_run_card(
    memory_service: RoleSessionContextMemoryService, role_session_service: RoleSessionService
) -> None:
    session_id = _seed_context_os_snapshot(role_session_service)

    result = memory_service.get_state(
        GetRoleSessionStateQueryV1(
            session_id=session_id,
            path="run_card",
        )
    )

    assert result.ok is True
    payload = dict(result.payload or {})
    assert payload.get("current_goal")
    assert isinstance(payload.get("active_entities"), list)
