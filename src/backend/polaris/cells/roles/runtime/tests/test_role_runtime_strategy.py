"""RoleRuntimeService strategy integration tests.

These tests live in the runtime cell (where RoleRuntimeService lives)
rather than in the kernelone/context cell to respect the import fence:
kernelone may NOT import from polaris/cells, but cells may import from kernelone.
"""

from __future__ import annotations

from typing import NoReturn

from polaris.kernelone.context import ResolvedStrategy


class TestRoleRuntimeServiceStrategy:
    """RoleRuntimeService strategy integration tests (no I/O)."""

    def test_resolve_strategy_profile_returns_resolved(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        resolved = svc.resolve_strategy_profile(domain="code", role="director")
        assert isinstance(resolved, ResolvedStrategy)
        assert resolved.profile.profile_id == "canonical_balanced"

    def test_create_strategy_run_increments_turn(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        ctx1 = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-test",
            budget=None,
            workspace="/repo",
        )
        ctx2 = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-test",
            budget=None,
            workspace="/repo",
        )
        ctx3 = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-other",
            budget=None,
            workspace="/repo",
        )
        # Same session: turn index increments
        assert ctx2.turn_index == ctx1.turn_index + 1
        # Different session: starts from 0
        assert ctx3.turn_index == 0

    def test_create_strategy_run_with_session_override(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        # Without session_override: gets canonical
        ctx = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id=None,
            budget=None,
            workspace="/repo",
        )
        assert ctx.profile_id == "canonical_balanced"

    def test_resolve_strategy_profile_prefers_domain_default_when_explicit(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        role_default = svc.resolve_strategy_profile(domain="document", role="director")
        explicit_domain = svc.resolve_strategy_profile(
            domain="document",
            role="director",
            prefer_domain_default=True,
        )
        assert role_default.profile.profile_id == "canonical_balanced"
        assert explicit_domain.profile.profile_id == "speed_first"

    def test_resolve_strategy_auto_overlay_prefers_domain_target(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        resolved = RoleRuntimeService.resolve_strategy(domain="writing", role="director")
        assert resolved.profile.profile_id == "director.writer"

    def test_resolve_strategy_general_domain_falls_back_to_code_overlay(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        resolved = RoleRuntimeService.resolve_strategy(domain="other", role="director")
        assert resolved.profile.profile_id == "director.execution"

    def test_build_session_request_propagates_context_domain(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="hello",
            context={"domain": "writing"},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "document"
        assert request.metadata["domain"] == "document"

    def test_build_task_request_propagates_metadata_domain(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleTaskCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleTaskCommandV1(
            role="director",
            task_id="task-1",
            workspace="/repo",
            objective="do work",
            metadata={"domain": "analysis"},
        )
        request = RoleRuntimeService._build_task_request(command)
        assert request.domain == "research"
        assert request.metadata["domain"] == "research"

    def test_build_session_request_injects_repo_intelligence_for_code_domain(
        self,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        class _FakeRepoMapResult:
            ranked_files = ["src/main.py"]
            ranked_symbols = ["main"]

            def to_text(self) -> str:
                return "【Ranked Files】\n  0.900 src/main.py"

        class _FakeFacade:
            def get_repo_map(self, **_kwargs):
                return _FakeRepoMapResult()

        monkeypatch.setattr(
            "polaris.kernelone.context.repo_intelligence.get_repo_intelligence",
            lambda **_kwargs: _FakeFacade(),
        )

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
            domain="code",
            context={"mentioned_idents": ["main"], "use_repo_intelligence": True},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.context_override is not None and "repo_intelligence" in request.context_override
        assert request.metadata.get("repo_intelligence_enabled") is True

    def test_build_session_request_skips_repo_intelligence_for_document_domain(
        self,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        def _unexpected(**_kwargs) -> NoReturn:
            raise AssertionError("repo intelligence should not be called for document domain")

        monkeypatch.setattr(
            "polaris.kernelone.context.repo_intelligence.get_repo_intelligence",
            _unexpected,
        )

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="写文档",
            domain="document",
            context={"mentioned_idents": ["main"], "use_repo_intelligence": True},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.context_override is None or "repo_intelligence" not in request.context_override
        assert request.metadata.get("repo_intelligence_enabled") is None

    def test_build_session_request_defaults_to_document_for_pm(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "document"
        assert request.metadata["domain"] == "document"

    def test_build_session_request_defaults_to_document_for_chief_engineer_alias(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="ChiefEnginner",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "document"
        assert request.metadata["domain"] == "document"

    def test_build_session_request_defaults_to_code_for_director(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "code"
        assert request.metadata["domain"] == "code"

    def test_build_session_request_explicit_domain_overrides_role_default(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
            context={"domain": "code"},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "code"
        assert request.metadata["domain"] == "code"
