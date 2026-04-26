from __future__ import annotations

import threading

from polaris.cells.roles.session.internal import conversation
from polaris.cells.roles.session.internal.role_session_service import RoleSessionService


def test_role_session_service_first_session_init_does_not_deadlock(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite:///{(tmp_path / 'conversations.db').as_posix()}"
    original_engine = conversation._engine
    original_session_local = conversation._SessionLocal
    test_engine = None

    monkeypatch.setattr(conversation, "_default_database_url", lambda: db_url)
    conversation._engine = None
    conversation._SessionLocal = None

    completed = threading.Event()
    result: dict[str, object] = {}
    failure: dict[str, BaseException] = {}

    def worker() -> None:
        try:
            with RoleSessionService() as service:
                created = service.create_session(role="pm", workspace=str(tmp_path))
                result["session"] = created.to_dict()
        except BaseException as exc:  # pragma: no cover - asserted below
            failure["error"] = exc
        finally:
            completed.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    try:
        assert completed.wait(2.0), "RoleSessionService.create_session deadlocked during first-time initialization"
        assert "error" not in failure, f"unexpected error: {failure['error']!r}"
        session = result["session"]
        assert isinstance(session, dict)
        assert session["role"] == "pm"
        assert session["workspace"] == str(tmp_path)
    finally:
        test_engine = conversation._engine
        if test_engine is not None and test_engine is not original_engine:
            test_engine.dispose()
        conversation._engine = original_engine
        conversation._SessionLocal = original_session_local
